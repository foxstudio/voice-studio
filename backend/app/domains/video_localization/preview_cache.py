from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domains.video_localization import media_assets, media_work_coordinator
from app.errors import AppException
from app.services import settings_store


CACHE_PROFILE_VERSION = "timeline-sprites-v2"
CHUNK_MS = 10_000
SPRITE_COLUMNS = 5
_JOBS: dict[str, "PreviewCacheJob"] = {}
_JOBS_LOCK = threading.Lock()
_FFMPEG_POLL_SECONDS = 0.05
_FFMPEG_TIMEOUT_SECONDS = 120.0
_FFMPEG_TERMINATE_GRACE_SECONDS = 2.0
_REFRESH_CANCEL_WAIT_SECONDS = 5.0
_CHUNK_MAX_ATTEMPTS = 2
_CONFIG_CACHE: dict[tuple[str, int, int, str], dict[str, Any]] = {}
_CONFIG_CACHE_LOCK = threading.Lock()
_DELETE_ON_JOB_EXIT: set[str] = set()
_ROOT_METADATA_UNSET = object()
_PRUNE_LOCK = threading.Lock()


@dataclass
class PreviewCacheJob:
    project_id: str
    source_path: Path
    signature: str
    root: Path
    duration_ms: int
    frame_interval_ms: int
    frame_width: int
    jpeg_quality: int
    generation: str
    full: bool = False
    priority_chunks: list[int] = field(default_factory=list)
    scan_cursor: int = 0
    phase: str = "queued"
    rendering_chunk: int | None = None
    error: str | None = None
    started_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: threading.Thread | None = None


def cache_status(project_id: str, source_path: Path) -> dict[str, Any]:
    config = _cache_config(source_path)
    root = _cache_root(project_id, source_path, config)
    duration_ms = config["duration_ms"]
    chunk_count = max(1, math.ceil(duration_ms / CHUNK_MS)) if duration_ms else 0
    with _JOBS_LOCK:
        job = _JOBS.get(project_id)
    active_job = job if job and job.signature == _source_signature(source_path, config) and job.thread and job.thread.is_alive() else None
    rendering_chunk = active_job.rendering_chunk if active_job and active_job.phase == "rendering" else None
    persisted_error = _read_json(root / "error.json") or {}
    failed_chunk = persisted_error.get("chunk_index")
    ranges, cached_bytes, ready_count = _cached_ranges(root, duration_ms, chunk_count)
    ranges = [dict(item) for item in ranges]
    if rendering_chunk is not None and 0 <= rendering_chunk < len(ranges) and ranges[rendering_chunk]["status"] != "ready":
        ranges[rendering_chunk]["status"] = "rendering"
    if isinstance(failed_chunk, int) and 0 <= failed_chunk < len(ranges) and ranges[failed_chunk]["status"] == "empty":
        ranges[failed_chunk]["status"] = "failed"
    if active_job:
        state = "building"
    elif chunk_count and ready_count == chunk_count:
        state = "ready"
    elif persisted_error:
        state = "failed"
    elif ready_count:
        state = "partial"
    else:
        state = "empty"
    phase = active_job.phase if active_job else ("failed" if state == "failed" else "idle")
    return {
        "contract_version": "video-preview-cache-status-v1",
        "state": state,
        "phase": phase,
        "active_chunk": rendering_chunk,
        "started_at": active_job.started_at if active_job else None,
        "updated_at": active_job.updated_at if active_job else persisted_error.get("occurred_at"),
        "retryable": state == "failed",
        "profile": CACHE_PROFILE_VERSION,
        "revision": active_job.generation if active_job else _root_generation(root),
        "mode": config["mode"],
        "duration_ms": duration_ms,
        "chunk_ms": CHUNK_MS,
        "frame_interval_ms": config["frame_interval_ms"],
        "frame_width": config["frame_width"],
        "frame_height": config["frame_height"],
        "sprite_columns": SPRITE_COLUMNS,
        "sprite_rows": math.ceil((CHUNK_MS / config["frame_interval_ms"]) / SPRITE_COLUMNS),
        "ready_chunks": ready_count,
        "total_chunks": chunk_count,
        "progress": ready_count / chunk_count if chunk_count else 0.0,
        "cached_bytes": cached_bytes,
        "capacity_bytes": _effective_capacity_bytes(root),
        "ranges": ranges,
        "error": active_job.error if active_job and active_job.error else str(persisted_error.get("message") or "") or None,
    }


def request_cache(
    project_id: str,
    source_path: Path,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    full: bool = False,
) -> dict[str, Any]:
    config = _cache_config(source_path)
    signature = _source_signature(source_path, config)
    root = _cache_root(project_id, source_path, config)
    duration_ms = config["duration_ms"]
    requested_chunks = _chunk_indexes(start_ms, end_ms, duration_ms)
    existing_status = cache_status(project_id, source_path)
    if _cache_status_satisfies_request(existing_status, requested_chunks, full=full):
        return existing_status
    with _JOBS_LOCK:
        current = _JOBS.get(project_id)
        if current and current.thread and current.thread.is_alive() and current.signature == signature:
            with current.lock:
                current.full = current.full or full
                for chunk_index in requested_chunks:
                    if chunk_index not in current.priority_chunks:
                        current.priority_chunks.append(chunk_index)
        else:
            if current:
                current.cancel.set()
                if _JOBS.get(project_id) is current:
                    _JOBS.pop(project_id, None)
            job = PreviewCacheJob(
                project_id=project_id,
                source_path=source_path,
                signature=signature,
                root=root,
                duration_ms=duration_ms,
                frame_interval_ms=config["frame_interval_ms"],
                frame_width=config["frame_width"],
                jpeg_quality=config["jpeg_quality"],
                generation=_root_generation(root),
                full=full,
                priority_chunks=requested_chunks,
            )
            job.thread = threading.Thread(target=_run_job, args=(job,), daemon=True, name=f"preview-cache-{project_id[:8]}")
            _JOBS[project_id] = job
            job.thread.start()
    return cache_status(project_id, source_path)


def refresh_cache(project_id: str, source_path: Path) -> dict[str, Any]:
    with _JOBS_LOCK:
        current = _JOBS.pop(project_id, None)
        if current:
            current.cancel.set()
    if current and current.thread and current.thread.is_alive() and current.thread is not threading.current_thread():
        current.thread.join(timeout=_REFRESH_CANCEL_WAIT_SECONDS)
        if current.thread.is_alive():
            with _JOBS_LOCK:
                if project_id not in _JOBS:
                    _JOBS[project_id] = current
            raise AppException(
                409,
                "VIDEO_PREVIEW_CACHE_CANCEL_TIMEOUT",
                "The previous preview cache renderer did not stop in time. Retry refresh after it exits.",
            )
    _remove_project_cache_root(project_id)
    return request_cache(project_id, source_path, full=True, start_ms=0, end_ms=CHUNK_MS)


def delete_project_cache(project_id: str) -> None:
    """Cancel project rendering and remove every persisted preview-cache generation."""
    with _JOBS_LOCK:
        current = _JOBS.pop(project_id, None)
        if current:
            current.cancel.set()
            if current.thread and current.thread.is_alive():
                _DELETE_ON_JOB_EXIT.add(project_id)
    _remove_project_cache_root(project_id)


def cancel_all() -> None:
    with _JOBS_LOCK:
        jobs = list(_JOBS.values())
        _JOBS.clear()
    for job in jobs:
        job.cancel.set()


def cached_sprite(project_id: str, source_path: Path, chunk_index: int) -> Path | None:
    config = _cache_config(source_path)
    root = _cache_root(project_id, source_path, config)
    metadata = _chunk_metadata(root, max(0, chunk_index))
    if not metadata:
        return None
    sprite_path = root / f"chunk-{max(0, chunk_index):05d}" / "sprite.jpg"
    if not sprite_path.is_file():
        return None
    try:
        os.utime(root, None)
    except OSError:
        pass
    return sprite_path


def _run_job(job: PreviewCacheJob) -> None:
    try:
        _update_job_state(job, phase="queued")
        job.root.mkdir(parents=True, exist_ok=True)
        _remove_orphan_parts(job.root)
        (job.root / "error.json").unlink(missing_ok=True)
        _write_json(
            job.root / "metadata.json",
            {
                "profile": CACHE_PROFILE_VERSION,
                "generation": job.generation,
                "duration_ms": job.duration_ms,
                "chunk_ms": CHUNK_MS,
                "frame_interval_ms": job.frame_interval_ms,
                "frame_width": job.frame_width,
                "source": job.source_path.name,
            },
        )
        _remove_stale_project_caches(job.root)
        while not job.cancel.is_set():
            chunk_index = _next_chunk(job)
            if chunk_index is None:
                break
            if _chunk_metadata(job.root, chunk_index):
                continue
            _update_job_state(job, phase="rendering", rendering_chunk=chunk_index)
            for attempt in range(_CHUNK_MAX_ATTEMPTS):
                try:
                    _build_chunk(job, job.source_path, chunk_index)
                    break
                except Exception:
                    if job.cancel.is_set() or attempt + 1 >= _CHUNK_MAX_ATTEMPTS:
                        raise
            _update_job_state(job, phase="queued", rendering_chunk=None)
        if not job.cancel.is_set():
            _prune_cache(job.root)
    except Exception as exc:
        if job.cancel.is_set():
            return
        _update_job_state(job, phase="failed", error=str(exc)[:500])
        try:
            _write_json(
                job.root / "error.json",
                {
                    "message": job.error,
                    "chunk_index": job.rendering_chunk,
                    "occurred_at": job.updated_at,
                },
            )
        except OSError:
            pass
    finally:
        if job.cancel.is_set() and job.phase != "failed":
            _update_job_state(job, phase="cancelled", rendering_chunk=None)
        else:
            _update_job_state(job, rendering_chunk=None)
        remove_after_exit = False
        with _JOBS_LOCK:
            if _JOBS.get(job.project_id) is job:
                _JOBS.pop(job.project_id, None)
            if job.project_id in _DELETE_ON_JOB_EXIT:
                _DELETE_ON_JOB_EXIT.discard(job.project_id)
                remove_after_exit = True
        if remove_after_exit:
            _remove_project_cache_root(job.project_id)


_UNCHANGED = object()


def _update_job_state(
    job: PreviewCacheJob,
    *,
    phase: str | None = None,
    rendering_chunk: int | None | object = _UNCHANGED,
    error: str | None | object = _UNCHANGED,
) -> None:
    with job.lock:
        if phase is not None:
            job.phase = phase
        if rendering_chunk is not _UNCHANGED:
            job.rendering_chunk = rendering_chunk  # type: ignore[assignment]
        if error is not _UNCHANGED:
            job.error = error  # type: ignore[assignment]
        job.updated_at = _now_iso()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_chunk(job: PreviewCacheJob) -> int | None:
    chunk_count = max(1, math.ceil(job.duration_ms / CHUNK_MS)) if job.duration_ms else 0
    with job.lock:
        while job.priority_chunks:
            candidate = job.priority_chunks.pop(0)
            if 0 <= candidate < chunk_count:
                return candidate
        if not job.full:
            return None
    while job.scan_cursor < chunk_count:
        chunk_index = job.scan_cursor
        job.scan_cursor += 1
        if not _chunk_metadata(job.root, chunk_index):
            return chunk_index
    return None


def _build_chunk(job: PreviewCacheJob, proxy_path: Path, chunk_index: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AppException(500, "VIDEO_PREVIEW_CACHE_FFMPEG_MISSING", "ffmpeg is required to build preview cache")
    start_ms = chunk_index * CHUNK_MS
    duration_ms = min(CHUNK_MS, max(0, job.duration_ms - start_ms))
    if duration_ms <= 0:
        return
    temporary = job.root / f".chunk-{chunk_index:05d}-{uuid.uuid4().hex[:8]}.part"
    destination = job.root / f"chunk-{chunk_index:05d}"
    temporary.mkdir(parents=True, exist_ok=False)
    config = _cache_config(job.source_path)
    frame_count = max(1, math.ceil(duration_ms / job.frame_interval_ms))
    rows = max(1, math.ceil(frame_count / SPRITE_COLUMNS))
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-threads",
        "2",
        "-filter_threads",
        "1",
        "-ss",
        f"{start_ms / 1000:.3f}",
        "-i",
        str(proxy_path),
        "-t",
        f"{duration_ms / 1000:.3f}",
        "-an",
        "-vf",
        f"fps=1000/{job.frame_interval_ms},scale={job.frame_width}:{config['frame_height']},tile={SPRITE_COLUMNS}x{rows}",
        "-q:v",
        str(job.jpeg_quality),
        "-frames:v",
        "1",
        "-threads:v",
        "2",
        str(temporary / "sprite.jpg"),
    ]
    try:
        with media_work_coordinator.MEDIA_WORK_COORDINATOR.slot(
            media_work_coordinator.MediaWorkPriority.PREVIEW,
            job.cancel,
            poll_seconds=_FFMPEG_POLL_SECONDS,
        ) as acquired_slot:
            if not acquired_slot or job.cancel.is_set():
                return
            if job.cancel.is_set():
                return
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + _FFMPEG_TIMEOUT_SECONDS
            timed_out = False
            while process.poll() is None:
                if job.cancel.wait(_FFMPEG_POLL_SECONDS):
                    _terminate_process(process)
                    _communicate_process(process)
                    return
                if time.monotonic() >= deadline:
                    timed_out = True
                    _terminate_process(process)
                    break
            stdout, stderr = _communicate_process(process)
            if timed_out:
                raise RuntimeError(f"preview frame generation timed out after {_FFMPEG_TIMEOUT_SECONDS:g} seconds")
        sprite = temporary / "sprite.jpg"
        if process.returncode != 0 or not sprite.is_file():
            detail = (stderr or stdout or "preview frame generation failed")[-1000:]
            raise RuntimeError(detail)
        size_bytes = sprite.stat().st_size
        _write_json(
            temporary / "chunk.json",
            {
                "start_ms": start_ms,
                "end_ms": start_ms + duration_ms,
                "frame_count": frame_count,
                "size_bytes": size_bytes,
                "columns": SPRITE_COLUMNS,
                "rows": rows,
            },
        )
        if job.cancel.is_set():
            return
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        temporary.replace(destination)
        try:
            os.utime(job.root, None)
        except OSError:
            pass
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _cache_config(source_path: Path) -> dict[str, Any]:
    settings = settings_store.get()
    mode = settings.video_preview_cache_mode
    stat = source_path.stat()
    cache_key = (str(source_path), stat.st_size, stat.st_mtime_ns, mode)
    with _CONFIG_CACHE_LOCK:
        cached = _CONFIG_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    metadata = media_assets.probe_video(source_path)
    duration_ms = int(metadata.get("duration_ms") or 0)
    try:
        free_bytes = shutil.disk_usage(settings_store.cache_dir()).free
    except OSError:
        free_bytes = 0
    if mode == "quality":
        interval_ms, width, jpeg_quality = 250, 640, 4
    elif mode == "compact":
        interval_ms, width, jpeg_quality = 1000, 360, 7
    elif duration_ms > 30 * 60_000 or (free_bytes and free_bytes < 5 * 1024**3):
        interval_ms, width, jpeg_quality = 1000, 360, 7
    else:
        interval_ms, width, jpeg_quality = 500, 480, 5
    source_width = max(1, int(metadata.get("width") or 16))
    source_height = max(1, int(metadata.get("height") or 9))
    frame_height = max(2, round((width * source_height / source_width) / 2) * 2)
    config = {
        "mode": mode,
        "duration_ms": duration_ms,
        "frame_interval_ms": interval_ms,
        "frame_width": width,
        "frame_height": frame_height,
        "jpeg_quality": jpeg_quality,
    }
    with _CONFIG_CACHE_LOCK:
        _CONFIG_CACHE.clear()
        _CONFIG_CACHE[cache_key] = dict(config)
    return config


def _source_signature(source_path: Path, config: dict[str, Any]) -> str:
    stat = source_path.stat()
    payload = (
        f"{source_path.name}:{stat.st_size}:{stat.st_mtime_ns}:{CACHE_PROFILE_VERSION}:"
        f"{config['frame_interval_ms']}:{config['frame_width']}:{config['jpeg_quality']}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def _project_cache_root(project_id: str) -> Path:
    safe_project_id = "".join(character for character in project_id if character.isalnum() or character in "_-") or "project"
    return settings_store.cache_dir() / "video-preview" / safe_project_id


def _remove_project_cache_root(project_id: str) -> None:
    project_root = _project_cache_root(project_id)
    if project_root.is_symlink():
        project_root.unlink(missing_ok=True)
    elif project_root.exists():
        shutil.rmtree(project_root, ignore_errors=True)


def _cache_root(project_id: str, source_path: Path, config: dict[str, Any]) -> Path:
    return _project_cache_root(project_id) / _source_signature(source_path, config)


def _chunk_indexes(start_ms: int | None, end_ms: int | None, duration_ms: int) -> list[int]:
    if start_ms is None:
        return []
    bounded_start = max(0, min(int(start_ms), max(0, duration_ms - 1)))
    bounded_end = max(bounded_start + 1, min(int(end_ms if end_ms is not None else bounded_start + CHUNK_MS), duration_ms))
    return list(range(bounded_start // CHUNK_MS, max(bounded_start // CHUNK_MS + 1, math.ceil(bounded_end / CHUNK_MS))))


def _cache_status_satisfies_request(
    status: dict[str, Any],
    requested_chunks: list[int],
    *,
    full: bool,
) -> bool:
    ranges = status.get("ranges")
    if not isinstance(ranges, list):
        return False
    required_chunks = list(range(len(ranges))) if full else requested_chunks
    if not required_chunks:
        return False
    return all(
        0 <= chunk_index < len(ranges)
        and isinstance(ranges[chunk_index], dict)
        and ranges[chunk_index].get("status") == "ready"
        for chunk_index in required_chunks
    )


def _chunk_metadata(
    root: Path,
    chunk_index: int,
    *,
    root_metadata: dict[str, Any] | None | object = _ROOT_METADATA_UNSET,
) -> dict[str, Any] | None:
    if chunk_index < 0:
        return None
    if root_metadata is _ROOT_METADATA_UNSET:
        root_metadata = _read_json(root / "metadata.json")
    if not isinstance(root_metadata, dict) or root_metadata.get("profile") != CACHE_PROFILE_VERSION:
        return None
    duration_ms = _strict_positive_int(root_metadata.get("duration_ms"))
    chunk_ms = _strict_positive_int(root_metadata.get("chunk_ms"))
    frame_interval_ms = _strict_positive_int(root_metadata.get("frame_interval_ms"))
    if duration_ms is None or chunk_ms != CHUNK_MS or frame_interval_ms is None:
        return None

    chunk_root = root / f"chunk-{chunk_index:05d}"
    path = chunk_root / "chunk.json"
    sprite = chunk_root / "sprite.jpg"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not sprite.is_file():
            return None
        sprite_size = sprite.stat().st_size
    except (OSError, json.JSONDecodeError):
        return None

    start_ms = _strict_nonnegative_int(payload.get("start_ms"))
    end_ms = _strict_positive_int(payload.get("end_ms"))
    frame_count = _strict_positive_int(payload.get("frame_count"))
    size_bytes = _strict_positive_int(payload.get("size_bytes"))
    columns = _strict_positive_int(payload.get("columns"))
    rows = _strict_positive_int(payload.get("rows"))
    expected_start = chunk_index * CHUNK_MS
    expected_end = min(duration_ms, expected_start + CHUNK_MS)
    if expected_start >= duration_ms or expected_end <= expected_start:
        return None
    expected_frame_count = max(1, math.ceil((expected_end - expected_start) / frame_interval_ms))
    expected_rows = max(1, math.ceil(expected_frame_count / SPRITE_COLUMNS))
    if (
        start_ms != expected_start
        or end_ms != expected_end
        or frame_count != expected_frame_count
        or columns != SPRITE_COLUMNS
        or rows != expected_rows
        or size_bytes != sprite_size
        or sprite_size <= 0
    ):
        return None
    return payload


def _cached_ranges(root: Path, duration_ms: int, chunk_count: int) -> tuple[list[dict[str, Any]], int, int]:
    ranges: list[dict[str, Any]] = []
    cached_bytes = 0
    ready_count = 0
    root_metadata = _read_json(root / "metadata.json")
    for chunk_index in range(chunk_count):
        chunk = _chunk_metadata(root, chunk_index, root_metadata=root_metadata)
        if chunk:
            ready_count += 1
            cached_bytes += int(chunk.get("size_bytes") or 0)
        ranges.append(
            {
                "start_ms": chunk_index * CHUNK_MS,
                "end_ms": min(duration_ms, (chunk_index + 1) * CHUNK_MS),
                "status": "ready" if chunk else "empty",
                "frame_count": int(chunk.get("frame_count") or 0) if chunk else 0,
                "sprite_rows": int(chunk.get("rows") or 0) if chunk else 0,
            }
        )
    return ranges, cached_bytes, ready_count


def _strict_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _strict_positive_int(value: Any) -> int | None:
    parsed = _strict_nonnegative_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=_FFMPEG_TERMINATE_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=_FFMPEG_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _communicate_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        stdout, stderr = process.communicate(timeout=_FFMPEG_TERMINATE_GRACE_SECONDS)
        return stdout or "", stderr or ""
    except subprocess.TimeoutExpired:
        _terminate_process(process)
        return "", ""


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _root_generation(root: Path) -> str:
    metadata_path = root / "metadata.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        generation = str(payload.get("generation") or "").strip()
        if generation:
            return generation
    except (OSError, json.JSONDecodeError):
        pass
    return uuid.uuid4().hex[:16]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _effective_capacity_bytes(root: Path) -> int:
    configured = int(settings_store.get().video_preview_cache_max_gb * 1024**3)
    try:
        free = shutil.disk_usage(root.parent if root.parent.exists() else settings_store.cache_dir()).free
    except OSError:
        return configured
    adaptive = max(256 * 1024**2, int(free * 0.08))
    return min(configured, adaptive)


def _remove_stale_project_caches(current_root: Path) -> None:
    parent = current_root.parent
    if not parent.exists() or parent.is_symlink():
        return
    for candidate in parent.iterdir():
        if candidate == current_root or not candidate.is_dir() or candidate.is_symlink():
            continue
        shutil.rmtree(candidate, ignore_errors=True)


def _remove_orphan_parts(root: Path) -> None:
    if not root.exists() or not root.is_dir() or root.is_symlink():
        return
    try:
        candidates = list(root.iterdir())
    except OSError:
        return
    for candidate in candidates:
        if not candidate.name.startswith(".") or ".part" not in candidate.name or candidate.is_symlink():
            continue
        if candidate.is_dir():
            shutil.rmtree(candidate, ignore_errors=True)
        else:
            candidate.unlink(missing_ok=True)


def _prune_cache(current_root: Path) -> None:
    with _PRUNE_LOCK:
        cache_root = settings_store.cache_dir() / "video-preview"
        if not cache_root.exists() or cache_root.is_symlink():
            return
        with _JOBS_LOCK:
            protected_roots = {
                job.root
                for job in _JOBS.values()
                if job.thread and job.thread.is_alive()
            }
        protected_roots.add(current_root)
        entries: list[tuple[float, int, Path]] = []
        total = 0
        for project_dir in cache_root.iterdir():
            if not project_dir.is_dir() or project_dir.is_symlink():
                continue
            for signature_dir in project_dir.iterdir():
                if not signature_dir.is_dir() or signature_dir.is_symlink():
                    continue
                size = sum(path.stat().st_size for path in signature_dir.rglob("*") if path.is_file())
                try:
                    modified = signature_dir.stat().st_mtime
                except OSError:
                    modified = time.time()
                entries.append((modified, size, signature_dir))
                total += size
        capacity = _effective_capacity_bytes(current_root)
        for _, size, path in sorted(entries, key=lambda item: item[0]):
            if total <= capacity:
                break
            if path in protected_roots:
                continue
            shutil.rmtree(path, ignore_errors=True)
            total -= size
