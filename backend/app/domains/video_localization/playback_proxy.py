from __future__ import annotations

import hashlib
import json
import math
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domains.video_localization import media_assets, media_work_coordinator


PLAYBACK_PROXY_PROFILE = "segmented-h264-fmp4-v1"
SEGMENT_MS = 4_000
STARTUP_BUFFER_MS = 12_000
_SEGMENT_MAX_ATTEMPTS = 2
_JOBS: dict[str, "PlaybackProxyJob"] = {}
_JOBS_LOCK = threading.Lock()
_VIDEO_METADATA_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_VIDEO_METADATA_CACHE_LOCK = threading.Lock()


@dataclass
class PlaybackProxyJob:
    project_id: str
    source_path: Path
    signature: str
    root: Path
    duration_ms: int
    generation: str
    requested_start_ms: int
    requested_end_ms: int
    fill_background: bool
    priority_segments: list[int] = field(default_factory=list)
    queued_segments: set[int] = field(default_factory=set)
    ready_segments: set[int] = field(default_factory=set)
    failed_segments: dict[int, str] = field(default_factory=dict)
    background_cursor: int = 0
    active_segment: int | None = None
    updated_at: str = field(default_factory=lambda: _now_iso())
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: threading.Thread | None = None


def request_playback(
    project_id: str,
    source_path: Path,
    *,
    source_playable: bool,
    start_ms: int = 0,
    end_ms: int | None = None,
    fill_background: bool = True,
) -> dict[str, Any]:
    """Resolve direct playback or enqueue the requested proxy range."""

    metadata = _video_metadata(source_path)
    duration_ms = max(0, int(metadata.get("duration_ms") or 0))
    if source_playable:
        return _source_status(source_path, duration_ms)

    bounded_start = _bounded_start(start_ms, duration_ms)
    bounded_end = _bounded_end(
        end_ms if end_ms is not None else bounded_start + STARTUP_BUFFER_MS,
        bounded_start,
        duration_ms,
    )
    signature = _source_signature(source_path)
    root = _cache_root(project_id, source_path)
    total_segments = _total_segments(duration_ms)
    requested_segments = _segment_indexes(
        bounded_start,
        bounded_end,
        duration_ms,
    )
    ready_segments = _ready_segment_indexes(root, total_segments)
    with _JOBS_LOCK:
        current = _JOBS.get(project_id)
        if (
            current
            and current.thread
            and current.thread.is_alive()
            and current.signature == signature
        ):
            with current.lock:
                current.requested_start_ms = bounded_start
                current.requested_end_ms = bounded_end
                current.fill_background = current.fill_background or fill_background
                _prioritize_segments(current, requested_segments)
                current.updated_at = _now_iso()
        elif total_segments > 0 and len(ready_segments) == total_segments:
            if current:
                current.cancel.set()
                _JOBS.pop(project_id, None)
            persisted = _read_json(root / "metadata.json") or {}
            _write_json(
                root / "metadata.json",
                {
                    "contract_version": "video-playback-proxy-cache-v1",
                    "profile": PLAYBACK_PROXY_PROFILE,
                    "generation": str(persisted.get("generation") or signature),
                    "source": source_path.name,
                    "duration_ms": duration_ms,
                    "segment_ms": SEGMENT_MS,
                    "requested_start_ms": bounded_start,
                    "requested_end_ms": bounded_end,
                    "updated_at": _now_iso(),
                },
            )
        else:
            if current:
                current.cancel.set()
            job = PlaybackProxyJob(
                project_id=project_id,
                source_path=source_path,
                signature=signature,
                root=root,
                duration_ms=duration_ms,
                generation=uuid.uuid4().hex[:16],
                requested_start_ms=bounded_start,
                requested_end_ms=bounded_end,
                fill_background=fill_background,
                ready_segments=ready_segments,
            )
            _prioritize_segments(job, requested_segments)
            job.thread = threading.Thread(
                target=_run_job,
                args=(job,),
                daemon=True,
                name=f"playback-proxy-{project_id[:8]}",
            )
            _JOBS[project_id] = job
            _write_job_metadata(job)
            job.thread.start()
    return playback_status(project_id, source_path)


def playback_status(project_id: str, source_path: Path) -> dict[str, Any]:
    metadata = _video_metadata(source_path)
    duration_ms = max(0, int(metadata.get("duration_ms") or 0))
    total_segments = _total_segments(duration_ms)
    signature = _source_signature(source_path)
    root = _cache_root(project_id, source_path)
    persisted = _read_json(root / "metadata.json") or {}
    with _JOBS_LOCK:
        candidate = _JOBS.get(project_id)
    job = (
        candidate
        if candidate
        and candidate.signature == signature
        and candidate.thread
        and candidate.thread.is_alive()
        else None
    )
    if job:
        with job.lock:
            ready_segments = set(job.ready_segments)
            failed_segments = dict(job.failed_segments)
            requested_start_ms = job.requested_start_ms
            requested_end_ms = job.requested_end_ms
            active_segment = job.active_segment
            updated_at = job.updated_at
    else:
        ready_segments = _ready_segment_indexes(root, total_segments)
        failed_segments = _persisted_errors(root)
        requested_start_ms = _strict_int(persisted.get("requested_start_ms"), 0)
        requested_end_ms = _strict_int(
            persisted.get("requested_end_ms"),
            min(duration_ms, requested_start_ms + STARTUP_BUFFER_MS),
        )
        active_segment = None
        updated_at = str(persisted.get("updated_at") or "") or None

    target_segment = min(
        max(0, requested_start_ms // SEGMENT_MS),
        max(0, total_segments - 1),
    )
    playable = total_segments > 0 and target_segment in ready_segments
    if total_segments and len(ready_segments) == total_segments:
        state = "ready"
    elif ready_segments:
        state = "partial"
    elif job:
        state = "building"
    elif failed_segments:
        state = "failed"
    else:
        state = "idle"
    return {
        "contract_version": "video-playback-proxy-status-v2",
        "state": state,
        "mode": "segmented",
        "variant": "segments",
        "playable": playable,
        "profile": PLAYBACK_PROXY_PROFILE,
        # Segment URLs are immutable for this source/profile signature. Worker
        # restarts may create a new internal generation without changing bytes.
        "revision": signature,
        "duration_ms": duration_ms,
        "segment_ms": SEGMENT_MS,
        "requested_range": {
            "start_ms": requested_start_ms,
            "end_ms": requested_end_ms,
        },
        "ready_ranges": _coalesced_ranges(ready_segments, duration_ms),
        "active_segment": active_segment,
        "ready_segments": len(ready_segments),
        "total_segments": total_segments,
        "progress": len(ready_segments) / total_segments if total_segments else 0.0,
        "updated_at": updated_at,
        "retryable": bool(failed_segments),
        "error": next(iter(failed_segments.values()), None),
    }


def segment_file(
    project_id: str,
    source_path: Path,
    segment_index: int,
    *,
    revision: str,
) -> Path | None:
    if revision != _source_signature(source_path):
        return None
    duration_ms = int(_video_metadata(source_path).get("duration_ms") or 0)
    if segment_index < 0 or segment_index >= _total_segments(duration_ms):
        return None
    candidate = _segment_path(_cache_root(project_id, source_path), segment_index)
    return candidate if candidate.is_file() and candidate.stat().st_size > 0 else None


def delete_project_cache(project_id: str) -> None:
    with _JOBS_LOCK:
        job = _JOBS.pop(project_id, None)
    if job:
        job.cancel.set()
    root = media_assets.project_video_localization_dir(project_id) / "preview" / "playback-v2"
    if root.is_symlink():
        root.unlink(missing_ok=True)
    elif root.exists():
        shutil.rmtree(root, ignore_errors=True)


def cancel_all() -> None:
    with _JOBS_LOCK:
        jobs = list(_JOBS.values())
        _JOBS.clear()
    for job in jobs:
        job.cancel.set()


def _run_job(job: PlaybackProxyJob) -> None:
    try:
        while not job.cancel.is_set():
            work_item = _next_segment(job)
            if work_item is None:
                break
            segment_index, priority = work_item
            if segment_index in job.ready_segments:
                continue
            with job.lock:
                job.active_segment = segment_index
                job.updated_at = _now_iso()
            destination = _segment_path(job.root, segment_index)
            start_ms = segment_index * SEGMENT_MS
            duration_ms = min(SEGMENT_MS, max(0, job.duration_ms - start_ms))
            last_error: Exception | None = None
            for _attempt in range(_SEGMENT_MAX_ATTEMPTS):
                try:
                    with media_work_coordinator.MEDIA_WORK_COORDINATOR.slot(
                        priority,
                        job.cancel,
                    ) as acquired:
                        if not acquired:
                            return
                        media_assets.build_playback_proxy_segment(
                            job.source_path,
                            destination,
                            start_ms=start_ms,
                            duration_ms=duration_ms,
                            cancel_event=job.cancel,
                        )
                    last_error = None
                    break
                except InterruptedError:
                    return
                except Exception as exc:  # isolated to this rebuildable segment
                    last_error = exc
            with job.lock:
                if last_error is None:
                    job.ready_segments.add(segment_index)
                    job.failed_segments.pop(segment_index, None)
                    _error_path(job.root, segment_index).unlink(missing_ok=True)
                else:
                    message = str(last_error)[:500]
                    job.failed_segments[segment_index] = message
                    _write_json(_error_path(job.root, segment_index), {"error": message})
                job.active_segment = None
                job.updated_at = _now_iso()
                _write_job_metadata(job)
    finally:
        with job.lock:
            job.active_segment = None
            job.updated_at = _now_iso()
            if not job.cancel.is_set():
                _write_job_metadata(job)
        with _JOBS_LOCK:
            if _JOBS.get(job.project_id) is job:
                _JOBS.pop(job.project_id, None)


def _next_segment(
    job: PlaybackProxyJob,
) -> tuple[int, media_work_coordinator.MediaWorkPriority] | None:
    total_segments = _total_segments(job.duration_ms)
    with job.lock:
        while job.priority_segments:
            candidate = job.priority_segments.pop(0)
            job.queued_segments.discard(candidate)
            if 0 <= candidate < total_segments and candidate not in job.ready_segments:
                return (
                    candidate,
                    media_work_coordinator.MediaWorkPriority.INTERACTIVE,
                )
        if not job.fill_background:
            return None
        while job.background_cursor < total_segments:
            candidate = job.background_cursor
            job.background_cursor += 1
            if candidate not in job.ready_segments:
                return (
                    candidate,
                    media_work_coordinator.MediaWorkPriority.BACKGROUND,
                )
    return None


def _enqueue_priority(job: PlaybackProxyJob, segment_index: int) -> None:
    if segment_index in job.ready_segments or segment_index in job.queued_segments:
        return
    job.priority_segments.append(segment_index)
    job.queued_segments.add(segment_index)
    job.failed_segments.pop(segment_index, None)
    _error_path(job.root, segment_index).unlink(missing_ok=True)


def _prioritize_segments(job: PlaybackProxyJob, segment_indexes: list[int]) -> None:
    """Put a new browser target ahead of stale lookahead/background work."""

    pending: list[int] = []
    for segment_index in segment_indexes:
        if segment_index in job.ready_segments:
            continue
        if segment_index in job.priority_segments:
            job.priority_segments.remove(segment_index)
        job.queued_segments.add(segment_index)
        job.failed_segments.pop(segment_index, None)
        _error_path(job.root, segment_index).unlink(missing_ok=True)
        pending.append(segment_index)
    job.priority_segments[0:0] = pending


def _source_status(source_path: Path, duration_ms: int) -> dict[str, Any]:
    revision = _source_signature(source_path)
    return {
        "contract_version": "video-playback-proxy-status-v2",
        "state": "ready",
        "mode": "source",
        "variant": "source",
        "playable": True,
        "profile": "source",
        "revision": revision,
        "duration_ms": duration_ms,
        "segment_ms": SEGMENT_MS,
        "requested_range": {"start_ms": 0, "end_ms": duration_ms},
        "ready_ranges": [{"start_ms": 0, "end_ms": duration_ms}],
        "active_segment": None,
        "ready_segments": _total_segments(duration_ms),
        "total_segments": _total_segments(duration_ms),
        "progress": 1.0,
        "updated_at": None,
        "retryable": False,
        "error": None,
    }


def _cache_root(project_id: str, source_path: Path) -> Path:
    return (
        media_assets.project_video_localization_dir(project_id)
        / "preview"
        / "playback-v2"
        / _source_signature(source_path)
    )


def _source_signature(source_path: Path) -> str:
    stat = source_path.stat()
    payload = (
        f"{source_path.name}:{stat.st_size}:{stat.st_mtime_ns}:"
        f"{PLAYBACK_PROXY_PROFILE}:{SEGMENT_MS}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def _video_metadata(source_path: Path) -> dict[str, Any]:
    stat = source_path.stat()
    key = (str(source_path), stat.st_size, stat.st_mtime_ns)
    with _VIDEO_METADATA_CACHE_LOCK:
        cached = _VIDEO_METADATA_CACHE.get(key)
    if cached is not None:
        return dict(cached)
    metadata = media_assets.probe_video(source_path)
    with _VIDEO_METADATA_CACHE_LOCK:
        _VIDEO_METADATA_CACHE.clear()
        _VIDEO_METADATA_CACHE[key] = dict(metadata)
    return metadata


def _segment_path(root: Path, segment_index: int) -> Path:
    return root / f"segment-{segment_index:05d}.mp4"


def _error_path(root: Path, segment_index: int) -> Path:
    return root / f"segment-{segment_index:05d}.error.json"


def _ready_segment_indexes(root: Path, total_segments: int) -> set[int]:
    ready: set[int] = set()
    if not root.is_dir() or root.is_symlink():
        return ready
    for candidate in root.glob("segment-*.mp4"):
        try:
            index = int(candidate.stem.removeprefix("segment-"))
            if 0 <= index < total_segments and candidate.stat().st_size > 0:
                ready.add(index)
        except (OSError, ValueError):
            continue
    return ready


def _persisted_errors(root: Path) -> dict[int, str]:
    errors: dict[int, str] = {}
    if not root.is_dir() or root.is_symlink():
        return errors
    for candidate in root.glob("segment-*.error.json"):
        try:
            index = int(candidate.name.split(".", 1)[0].removeprefix("segment-"))
        except ValueError:
            continue
        payload = _read_json(candidate) or {}
        message = str(payload.get("error") or "")
        if message:
            errors[index] = message
    return errors


def _write_job_metadata(job: PlaybackProxyJob) -> None:
    _write_json(
        job.root / "metadata.json",
        {
            "contract_version": "video-playback-proxy-cache-v1",
            "profile": PLAYBACK_PROXY_PROFILE,
            "generation": job.generation,
            "source": job.source_path.name,
            "duration_ms": job.duration_ms,
            "segment_ms": SEGMENT_MS,
            "requested_start_ms": job.requested_start_ms,
            "requested_end_ms": job.requested_end_ms,
            "updated_at": job.updated_at,
        },
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _segment_indexes(start_ms: int, end_ms: int, duration_ms: int) -> list[int]:
    if duration_ms <= 0:
        return []
    first = min(max(0, start_ms // SEGMENT_MS), _total_segments(duration_ms) - 1)
    last = min(
        max(first, max(start_ms, end_ms - 1) // SEGMENT_MS),
        _total_segments(duration_ms) - 1,
    )
    return list(range(first, last + 1))


def _coalesced_ranges(ready_segments: set[int], duration_ms: int) -> list[dict[str, int]]:
    ranges: list[dict[str, int]] = []
    for segment_index in sorted(ready_segments):
        start_ms = segment_index * SEGMENT_MS
        end_ms = min(duration_ms, start_ms + SEGMENT_MS)
        if ranges and ranges[-1]["end_ms"] == start_ms:
            ranges[-1]["end_ms"] = end_ms
        else:
            ranges.append({"start_ms": start_ms, "end_ms": end_ms})
    return ranges


def _total_segments(duration_ms: int) -> int:
    return math.ceil(duration_ms / SEGMENT_MS) if duration_ms > 0 else 0


def _bounded_start(start_ms: int, duration_ms: int) -> int:
    if duration_ms <= 0:
        return 0
    return min(max(0, int(start_ms)), max(0, duration_ms - 1))


def _bounded_end(end_ms: int, start_ms: int, duration_ms: int) -> int:
    if duration_ms <= 0:
        return 0
    return min(duration_ms, max(start_ms + 1, int(end_ms)))


def _strict_int(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
