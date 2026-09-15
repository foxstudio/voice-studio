from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import media_assets, preview_cache  # noqa: E402
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import database, settings_store  # noqa: E402


def _configure(tmp_path: Path) -> None:
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    preview_cache.cancel_all()
    preview_cache._CONFIG_CACHE.clear()


class _CompletedPopen:
    def __init__(self):
        self.returncode = 0

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        return "", ""


class _BlockingPopen:
    def __init__(self, *, ignore_terminate: bool = False):
        self.returncode: int | None = None
        self.ignore_terminate = ignore_terminate
        self.terminated = threading.Event()
        self.killed = threading.Event()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated.set()
        if not self.ignore_terminate:
            self.returncode = -15

    def kill(self):
        self.killed.set()
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise preview_cache.subprocess.TimeoutExpired("ffmpeg", timeout)
        return self.returncode

    def communicate(self, timeout=None):
        return "", ""


def _write_valid_chunk(
    root: Path,
    *,
    chunk_index: int = 0,
    duration_ms: int = 25_000,
    frame_interval_ms: int = 500,
    sprite_bytes: bytes = b"jpeg-sprite",
    size_bytes: int | None = None,
) -> Path:
    preview_cache._write_json(
        root / "metadata.json",
        {
            "profile": preview_cache.CACHE_PROFILE_VERSION,
            "generation": "test-generation",
            "duration_ms": duration_ms,
            "chunk_ms": preview_cache.CHUNK_MS,
            "frame_interval_ms": frame_interval_ms,
            "frame_width": 480,
            "source": "source.mp4",
        },
    )
    start_ms = chunk_index * preview_cache.CHUNK_MS
    end_ms = min(duration_ms, start_ms + preview_cache.CHUNK_MS)
    frame_count = max(1, (end_ms - start_ms + frame_interval_ms - 1) // frame_interval_ms)
    rows = max(1, (frame_count + preview_cache.SPRITE_COLUMNS - 1) // preview_cache.SPRITE_COLUMNS)
    chunk_root = root / f"chunk-{chunk_index:05d}"
    sprite = chunk_root / "sprite.jpg"
    sprite.parent.mkdir(parents=True, exist_ok=True)
    sprite.write_bytes(sprite_bytes)
    preview_cache._write_json(
        chunk_root / "chunk.json",
        {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "frame_count": frame_count,
            "size_bytes": len(sprite_bytes) if size_bytes is None else size_bytes,
            "columns": preview_cache.SPRITE_COLUMNS,
            "rows": rows,
        },
    )
    return sprite


def test_preview_cache_builds_only_requested_chunk_and_serves_one_sprite(tmp_path: Path, monkeypatch):
    _configure(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"preview-source")
    monkeypatch.setattr(
        media_assets,
        "probe_video",
        lambda _path: {"codec_name": "h264", "width": 640, "height": 360, "duration_ms": 25_000},
    )
    monkeypatch.setattr(preview_cache.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    commands: list[list[str]] = []
    release_render = threading.Event()

    def fake_popen(command, **_kwargs):
        assert release_render.wait(timeout=5)
        commands.append(command)
        Path(command[-1]).write_bytes(b"jpeg-sprite")
        return _CompletedPopen()

    monkeypatch.setattr(preview_cache.subprocess, "Popen", fake_popen)
    try:
        initial = preview_cache.request_cache("project-1", source, start_ms=12_000, end_ms=13_000)
        assert initial["state"] == "building"
    finally:
        release_render.set()

    deadline = time.monotonic() + 2
    status = initial
    while time.monotonic() < deadline:
        status = preview_cache.cache_status("project-1", source)
        if status["state"] != "building":
            break
        time.sleep(0.01)

    assert status["state"] == "partial"
    assert [item["status"] for item in status["ranges"]] == ["empty", "ready", "empty"]
    assert status["ranges"][1]["frame_count"] == 20
    sprite = preview_cache.cached_sprite("project-1", source, 1)
    assert sprite is not None
    assert sprite.read_bytes() == b"jpeg-sprite"
    assert len(list(sprite.parent.glob("*.jpg"))) == 1
    assert len(commands) == 1
    assert commands[0].index("-threads:v") > commands[0].index("-i")
    assert commands[0][commands[0].index("-threads:v") + 1] == "2"


def test_preview_cache_reuses_complete_persisted_generation_without_starting_a_job(
    tmp_path: Path,
    monkeypatch,
):
    _configure(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"preview-source")
    monkeypatch.setattr(
        media_assets,
        "probe_video",
        lambda _path: {"codec_name": "h264", "width": 640, "height": 360, "duration_ms": 25_000},
    )
    config = preview_cache._cache_config(source)
    root = preview_cache._cache_root("project-return", source, config)
    for chunk_index in range(3):
        _write_valid_chunk(root, chunk_index=chunk_index, duration_ms=25_000)
    status = preview_cache.request_cache(
        "project-return",
        source,
        start_ms=0,
        end_ms=20_000,
        full=True,
    )

    assert status["state"] == "ready"
    assert status["ready_chunks"] == status["total_chunks"] == 3
    assert "project-return" not in preview_cache._JOBS


def test_preview_cache_ranges_clamp_to_video_duration():
    assert preview_cache._chunk_indexes(0, 10_000, 25_000) == [0]
    assert preview_cache._chunk_indexes(9_999, 20_001, 25_000) == [0, 1, 2]
    assert preview_cache._chunk_indexes(99_000, None, 25_000) == [2]


def test_chunk_metadata_rejects_missing_sprite(tmp_path: Path):
    root = tmp_path / "preview"
    sprite = _write_valid_chunk(root)
    sprite.unlink()

    assert preview_cache._chunk_metadata(root, 0) is None


def test_chunk_metadata_rejects_zero_byte_sprite(tmp_path: Path):
    root = tmp_path / "preview"
    _write_valid_chunk(root, sprite_bytes=b"")

    assert preview_cache._chunk_metadata(root, 0) is None


def test_chunk_metadata_rejects_sprite_size_mismatch(tmp_path: Path):
    root = tmp_path / "preview"
    _write_valid_chunk(root, size_bytes=999)

    assert preview_cache._chunk_metadata(root, 0) is None


def test_cached_ranges_immediately_returns_to_empty_after_nested_sprite_deletion(tmp_path: Path, monkeypatch):
    _configure(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"preview-source")
    monkeypatch.setattr(
        media_assets,
        "probe_video",
        lambda _path: {"codec_name": "h264", "width": 640, "height": 360, "duration_ms": 25_000},
    )
    config = preview_cache._cache_config(source)
    root = preview_cache._cache_root("project-delete", source, config)
    sprite = _write_valid_chunk(root)

    ready = preview_cache.cache_status("project-delete", source)
    assert ready["ready_chunks"] == 1
    assert ready["ranges"][0]["status"] == "ready"

    sprite.unlink()
    empty = preview_cache.cache_status("project-delete", source)
    assert empty["ready_chunks"] == 0
    assert empty["cached_bytes"] == 0
    assert empty["ranges"][0]["status"] == "empty"


def test_cached_ranges_reads_root_metadata_once_per_status_projection(tmp_path: Path, monkeypatch):
    root = tmp_path / "preview"
    for chunk_index in range(3):
        _write_valid_chunk(root, chunk_index=chunk_index, duration_ms=25_000)
    original_read_json = preview_cache._read_json
    metadata_reads = 0

    def counted_read_json(path):
        nonlocal metadata_reads
        if path.name == "metadata.json":
            metadata_reads += 1
        return original_read_json(path)

    monkeypatch.setattr(preview_cache, "_read_json", counted_read_json)

    ranges, _cached_bytes, ready_count = preview_cache._cached_ranges(root, 25_000, 3)

    assert ready_count == 3
    assert all(item["status"] == "ready" for item in ranges)
    assert metadata_reads == 1


def test_full_job_chunk_cursor_does_not_rescan_completed_prefix(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    job = preview_cache.PreviewCacheJob(
        project_id="project-long",
        source_path=source,
        signature="signature",
        root=tmp_path / "cache",
        duration_ms=1_000_000,
        frame_interval_ms=1000,
        frame_width=360,
        jpeg_quality=7,
        generation="generation",
        full=True,
    )
    inspected: list[int] = []
    monkeypatch.setattr(
        preview_cache,
        "_chunk_metadata",
        lambda _root, chunk_index, **_kwargs: inspected.append(chunk_index) or None,
    )

    indexes = [preview_cache._next_chunk(job) for _ in range(101)]

    assert indexes[:100] == list(range(100))
    assert indexes[100] is None
    assert inspected == list(range(100))


def test_preview_job_records_only_thumbnail_cache_metadata(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    job = preview_cache.PreviewCacheJob(
        project_id="project-friendly",
        source_path=source,
        signature="signature",
        root=tmp_path / "cache",
        duration_ms=10_000,
        frame_interval_ms=1_000,
        frame_width=360,
        jpeg_quality=7,
        generation="generation",
    )
    monkeypatch.setattr(preview_cache, "_next_chunk", lambda _job: None)
    monkeypatch.setattr(preview_cache, "_prune_cache", lambda _root: None)

    preview_cache._run_job(job)

    assert job.phase == "queued"
    assert "playback_variant" not in preview_cache._read_json(
        job.root / "metadata.json"
    )


def test_request_cache_reuses_ready_sprites_without_playback_state(
    tmp_path: Path,
    monkeypatch,
):
    _configure(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(
        preview_cache.media_assets,
        "probe_video",
        lambda _path: {
            "codec_name": "h264",
            "width": 640,
            "height": 360,
            "duration_ms": 10_000,
        },
    )
    config = preview_cache._cache_config(source)
    root = preview_cache._cache_root("project-missing-decision", source, config)
    _write_valid_chunk(root, duration_ms=10_000)
    monkeypatch.setattr(preview_cache, "_next_chunk", lambda _job: None)
    monkeypatch.setattr(preview_cache, "_prune_cache", lambda _root: None)

    status = preview_cache.request_cache("project-missing-decision", source)

    assert status["state"] == "ready"
    assert "project-missing-decision" not in preview_cache._JOBS


def test_preview_job_removes_crash_leftover_partial_directories(tmp_path: Path):
    root = tmp_path / "preview"
    orphan = root / ".chunk-00003-crashed.part"
    orphan.mkdir(parents=True)
    (orphan / "sprite.jpg").write_bytes(b"partial")
    unrelated = root / "keep"
    unrelated.mkdir(parents=True)

    preview_cache._remove_orphan_parts(root)

    assert not orphan.exists()
    assert unrelated.exists()


def test_audio_proxy_lock_registry_releases_unused_keys(tmp_path: Path):
    with media_assets._audio_preview_lock(tmp_path / "preview" / "audio.m4a"):
        assert media_assets._AUDIO_PREVIEW_LOCKS.active_key_count == 1

    assert media_assets._AUDIO_PREVIEW_LOCKS.active_key_count == 0


def test_capacity_pruning_never_deletes_an_active_job_root(tmp_path: Path, monkeypatch):
    _configure(tmp_path)
    cache_root = settings_store.cache_dir() / "video-preview"
    current_root = cache_root / "current-project" / "current-signature"
    active_root = cache_root / "active-project" / "active-signature"
    stale_root = cache_root / "stale-project" / "stale-signature"
    for root in (current_root, active_root, stale_root):
        root.mkdir(parents=True)
        (root / "data.bin").write_bytes(b"cache")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    active_job = preview_cache.PreviewCacheJob(
        project_id="active-project",
        source_path=source,
        signature="active-signature",
        root=active_root,
        duration_ms=10_000,
        frame_interval_ms=500,
        frame_width=480,
        jpeg_quality=5,
        generation="generation",
    )
    release = threading.Event()
    active_job.thread = threading.Thread(target=lambda: release.wait(timeout=1))
    with preview_cache._JOBS_LOCK:
        preview_cache._JOBS[active_job.project_id] = active_job
    active_job.thread.start()
    monkeypatch.setattr(preview_cache, "_effective_capacity_bytes", lambda _root: 0)
    try:
        preview_cache._prune_cache(current_root)
    finally:
        release.set()
        active_job.thread.join(timeout=1)
        with preview_cache._JOBS_LOCK:
            preview_cache._JOBS.pop(active_job.project_id, None)

    assert current_root.exists()
    assert active_root.exists()
    assert not stale_root.exists()


def test_persisted_renderer_error_exposes_failed_retryable_status(tmp_path: Path, monkeypatch):
    _configure(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"preview-source")
    monkeypatch.setattr(
        media_assets,
        "probe_video",
        lambda _path: {"codec_name": "h264", "width": 640, "height": 360, "duration_ms": 25_000},
    )
    config = preview_cache._cache_config(source)
    root = preview_cache._cache_root("project-failed", source, config)
    preview_cache._write_json(
        root / "error.json",
        {
            "message": "temporary renderer failure",
            "chunk_index": 1,
            "occurred_at": "2026-07-26T10:00:00Z",
        },
    )

    status = preview_cache.cache_status("project-failed", source)

    assert status["state"] == "failed"
    assert status["phase"] == "failed"
    assert status["active_chunk"] is None
    assert status["retryable"] is True
    assert status["error"] == "temporary renderer failure"
    assert status["updated_at"] == "2026-07-26T10:00:00Z"
    assert status["ranges"][1]["status"] == "failed"
    assert "cache_path" not in status


def test_queued_preview_job_does_not_mark_a_chunk_as_rendering(tmp_path: Path, monkeypatch):
    _configure(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"preview-source")
    monkeypatch.setattr(
        media_assets,
        "probe_video",
        lambda _path: {"codec_name": "h264", "width": 640, "height": 360, "duration_ms": 25_000},
    )
    config = preview_cache._cache_config(source)
    job = preview_cache.PreviewCacheJob(
        project_id="project-queued",
        source_path=source,
        signature=preview_cache._source_signature(source, config),
        root=preview_cache._cache_root("project-queued", source, config),
        duration_ms=25_000,
        frame_interval_ms=500,
        frame_width=480,
        jpeg_quality=5,
        generation="generation",
        full=True,
        phase="queued",
    )
    release = threading.Event()
    job.thread = threading.Thread(target=lambda: release.wait(timeout=1))
    with preview_cache._JOBS_LOCK:
        preview_cache._JOBS[job.project_id] = job
    job.thread.start()
    try:
        status = preview_cache.cache_status(job.project_id, source)
    finally:
        release.set()
        job.thread.join(timeout=1)
        with preview_cache._JOBS_LOCK:
            preview_cache._JOBS.pop(job.project_id, None)

    assert status["state"] == "building"
    assert status["phase"] == "queued"
    assert status["active_chunk"] is None
    assert all(item["status"] == "empty" for item in status["ranges"])


def test_full_cache_retries_one_transient_chunk_failure(tmp_path: Path, monkeypatch):
    _configure(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"preview-source")
    root = tmp_path / "preview"
    job = preview_cache.PreviewCacheJob(
        project_id="project-retry",
        source_path=source,
        signature="signature",
        root=root,
        duration_ms=10_000,
        frame_interval_ms=500,
        frame_width=480,
        jpeg_quality=5,
        generation="generation",
        full=True,
    )
    attempts: list[int] = []
    monkeypatch.setattr(preview_cache, "_prune_cache", lambda _root: None)

    def fake_build(_job, _proxy_path, chunk_index):
        attempts.append(chunk_index)
        if len(attempts) == 1:
            raise RuntimeError("transient failure")
        _write_valid_chunk(root, chunk_index=chunk_index, duration_ms=10_000)

    monkeypatch.setattr(preview_cache, "_build_chunk", fake_build)

    preview_cache._run_job(job)

    assert attempts == [0, 0]
    assert job.error is None
    assert preview_cache._chunk_metadata(root, 0) is not None
    assert not (root / "error.json").exists()


def test_build_chunk_cancellation_terminates_ffmpeg(tmp_path: Path, monkeypatch):
    _configure(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"preview-source")
    job = preview_cache.PreviewCacheJob(
        project_id="project-cancel",
        source_path=source,
        signature="signature",
        root=tmp_path / "preview",
        duration_ms=10_000,
        frame_interval_ms=500,
        frame_width=480,
        jpeg_quality=5,
        generation="generation",
    )
    job.root.mkdir(parents=True)
    process = _BlockingPopen()
    started = threading.Event()
    errors: list[Exception] = []
    monkeypatch.setattr(preview_cache.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(preview_cache, "_cache_config", lambda _path: {"frame_height": 270})

    def fake_popen(*_args, **_kwargs):
        started.set()
        return process

    monkeypatch.setattr(preview_cache.subprocess, "Popen", fake_popen)

    def build():
        try:
            preview_cache._build_chunk(job, source, 0)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=build)
    thread.start()
    assert started.wait(timeout=1)
    job.cancel.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert not errors
    assert process.terminated.is_set()
    assert not process.killed.is_set()
    assert not (job.root / "chunk-00000").exists()


def test_build_chunk_timeout_kills_unresponsive_ffmpeg(tmp_path: Path, monkeypatch):
    _configure(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"preview-source")
    job = preview_cache.PreviewCacheJob(
        project_id="project-timeout",
        source_path=source,
        signature="signature",
        root=tmp_path / "preview",
        duration_ms=10_000,
        frame_interval_ms=500,
        frame_width=480,
        jpeg_quality=5,
        generation="generation",
    )
    job.root.mkdir(parents=True)
    process = _BlockingPopen(ignore_terminate=True)
    monkeypatch.setattr(preview_cache.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(preview_cache, "_cache_config", lambda _path: {"frame_height": 270})
    monkeypatch.setattr(preview_cache, "_FFMPEG_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(preview_cache.subprocess, "Popen", lambda *_args, **_kwargs: process)

    try:
        preview_cache._build_chunk(job, source, 0)
    except RuntimeError as exc:
        assert "timed out" in str(exc)
    else:  # pragma: no cover - the timeout is the behavior under test
        raise AssertionError("preview ffmpeg timeout did not raise")

    assert process.terminated.is_set()
    assert process.killed.is_set()
    assert not (job.root / "chunk-00000").exists()


def test_playback_segment_process_can_be_cancelled(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.mov"
    source.write_bytes(b"preview-source")
    cancel = threading.Event()
    process = _BlockingPopen()
    started = threading.Event()
    errors: list[Exception] = []
    monkeypatch.setattr(media_assets.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def fake_popen(*_args, **_kwargs):
        started.set()
        return process

    monkeypatch.setattr(media_assets.subprocess, "Popen", fake_popen)

    destination = tmp_path / "preview" / "segment-00000.mp4"

    def build_proxy():
        try:
            media_assets.build_playback_proxy_segment(
                source,
                destination,
                start_ms=0,
                duration_ms=4_000,
                cancel_event=cancel,
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=build_proxy)
    thread.start()
    assert started.wait(timeout=1)
    cancel.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], InterruptedError)
    assert process.terminated.is_set()
    assert not list(destination.parent.glob("*.part.mp4"))


def test_refresh_cache_only_requeues_preview_chunks(tmp_path: Path, monkeypatch):
    _configure(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"preview-source")
    project_root = preview_cache._project_cache_root("project-refresh")
    project_root.mkdir(parents=True)
    (project_root / "stale-sprite.jpg").write_bytes(b"stale")
    observed: dict[str, object] = {}
    old_job_exited = threading.Event()
    old_job = preview_cache.PreviewCacheJob(
        project_id="project-refresh",
        source_path=source,
        signature="old-signature",
        root=project_root / "old-signature",
        duration_ms=10_000,
        frame_interval_ms=500,
        frame_width=480,
        jpeg_quality=5,
        generation="old-generation",
    )

    def old_worker():
        assert old_job.cancel.wait(timeout=1)
        time.sleep(0.05)
        old_job_exited.set()

    old_job.thread = threading.Thread(target=old_worker)
    with preview_cache._JOBS_LOCK:
        preview_cache._JOBS[old_job.project_id] = old_job
    old_job.thread.start()

    def fake_request(project_id, source_path, **kwargs):
        assert old_job_exited.is_set()
        assert old_job.thread is not None and not old_job.thread.is_alive()
        assert not project_root.exists()
        observed.update(project_id=project_id, source_path=source_path, kwargs=kwargs)
        return {"state": "building"}

    monkeypatch.setattr(preview_cache, "request_cache", fake_request)

    assert preview_cache.refresh_cache("project-refresh", source) == {"state": "building"}
    assert not project_root.exists()
    assert observed == {
        "project_id": "project-refresh",
        "source_path": source,
        "kwargs": {"full": True, "start_ms": 0, "end_ms": preview_cache.CHUNK_MS},
    }


def test_refresh_cache_times_out_instead_of_blocking_the_service(tmp_path: Path, monkeypatch):
    _configure(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"preview-source")
    project_id = "project-refresh-timeout"
    release = threading.Event()
    old_job = preview_cache.PreviewCacheJob(
        project_id=project_id,
        source_path=source,
        signature="old-signature",
        root=preview_cache._project_cache_root(project_id) / "old-signature",
        duration_ms=10_000,
        frame_interval_ms=500,
        frame_width=480,
        jpeg_quality=5,
        generation="old-generation",
    )
    old_job.thread = threading.Thread(target=lambda: release.wait(timeout=1))
    with preview_cache._JOBS_LOCK:
        preview_cache._JOBS[project_id] = old_job
    old_job.thread.start()
    monkeypatch.setattr(preview_cache, "_REFRESH_CANCEL_WAIT_SECONDS", 0.0)

    try:
        preview_cache.refresh_cache(project_id, source)
    except Exception as exc:
        assert getattr(exc, "code", "") == "VIDEO_PREVIEW_CACHE_CANCEL_TIMEOUT"
    else:  # pragma: no cover - a stuck renderer must never make refresh continue
        raise AssertionError("refresh unexpectedly continued while the previous renderer was alive")
    finally:
        release.set()
        old_job.thread.join(timeout=1)

    assert preview_cache._JOBS.get(project_id) is old_job
