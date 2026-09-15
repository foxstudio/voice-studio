from __future__ import annotations

import asyncio
import logging
import queue
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from app.domains.video_localization import media_assets
from app.domains.video_localization import preview_cache
from app.domains.video_localization import project_snapshot_projection
from app.services import database
from app.services import custom_reference_store
from app.services import history_store
from app.services import video_localization_project_cleanup_store as store
from app.services import video_localization_project_snapshot_store
from app.services import waveform_cache


logger = logging.getLogger(__name__)
_WORKER_LOCK = threading.Lock()
_WORKER_QUEUE: queue.Queue[_CleanupWorkItem | None] | None = None
_WORKER_THREAD: threading.Thread | None = None
_WORKER_STOP: threading.Event | None = None
_SCHEDULED_JOBS: set[tuple[tuple[str, int], str]] = set()


@dataclass(frozen=True)
class _CleanupWorkItem:
    job_id: str
    database_identity: tuple[str, int]


def new_job(
    *,
    project_id: str,
    action: Literal["reset", "delete"],
    directory_name: str,
    cleanup_payload: dict[str, Any] | None = None,
) -> store.ProjectCleanupJob:
    return store.ProjectCleanupJob(
        job_id=uuid.uuid4().hex,
        project_id=project_id,
        action=action,
        directory_name=directory_name,
        cleanup_payload=cleanup_payload or {},
        package_staged=False,
    )


def flush(job_id: str) -> bool:
    """Best-effort, idempotent cleanup after the SQLite commit point."""

    job = store.load(job_id)
    if job is None:
        return False
    attempted_at = datetime.now().isoformat(timespec="microseconds")
    try:
        if not job.package_staged:
            with project_snapshot_projection.project_lock(
                job.project_id
            ):
                media_assets.stage_project_video_localization_dir(
                    directory_name=job.directory_name,
                    cleanup_id=job.job_id,
                )
                store.mark_package_staged(job.job_id)
            job = store.load(job.job_id) or job
        if job.action == "reset":
            project_snapshot_projection.flush(job.project_id)
            if video_localization_project_snapshot_store.load(
                job.project_id
            ) is not None:
                raise OSError(
                    "reset snapshot projection is still pending"
                )
        _cleanup_secondary_artifacts(job)
        with project_snapshot_projection.project_lock(job.project_id):
            media_assets.purge_staged_project_video_localization_dir(
                job.job_id
            )
        return store.complete(job.job_id)
    except Exception as exc:
        store.record_failure(
            job.job_id,
            attempted_at=attempted_at,
            error=f"{type(exc).__name__}: {exc}",
        )
        logger.warning(
            "Project %s cleanup remains pending for %s: %s",
            job.action,
            job.project_id,
            exc,
        )
        return False


def replay_pending(*, limit: int = 100) -> int:
    completed = 0
    for job_id in store.pending_job_ids(limit=limit):
        if flush(job_id):
            completed += 1
    return completed


def start_worker(*, replay_limit: int = 0) -> None:
    """Start one process-local cleanup worker without blocking startup."""

    global _WORKER_QUEUE, _WORKER_STOP, _WORKER_THREAD
    with _WORKER_LOCK:
        if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
            worker_running = True
        else:
            work_queue: queue.Queue[_CleanupWorkItem | None] = (
                queue.Queue()
            )
            stop_event = threading.Event()
            worker = threading.Thread(
                target=_worker_loop,
                args=(work_queue, stop_event),
                daemon=True,
                name="video-localization-project-cleanup",
            )
            try:
                worker.start()
            except Exception:
                logger.exception(
                    "Could not start project cleanup worker"
                )
                return
            _WORKER_QUEUE = work_queue
            _WORKER_STOP = stop_event
            _WORKER_THREAD = worker
            worker_running = True
    if worker_running and replay_limit > 0:
        for job_id in store.pending_job_ids(limit=replay_limit):
            schedule(job_id)


def schedule(job_id: str) -> bool:
    """Queue one durable cleanup job for serial background execution."""

    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return False
    start_worker()
    database_identity = database.runtime_identity()
    schedule_key = (database_identity, normalized_job_id)
    with _WORKER_LOCK:
        work_queue = _WORKER_QUEUE
        worker = _WORKER_THREAD
        if (
            work_queue is None
            or worker is None
            or not worker.is_alive()
            or schedule_key in _SCHEDULED_JOBS
        ):
            return False
        _SCHEDULED_JOBS.add(schedule_key)
        work_queue.put(
            _CleanupWorkItem(
                job_id=normalized_job_id,
                database_identity=database_identity,
            )
        )
    return True


async def shutdown() -> None:
    """Stop accepting work; unfinished jobs remain durable for replay."""

    with _WORKER_LOCK:
        work_queue = _WORKER_QUEUE
        stop_event = _WORKER_STOP
        worker = _WORKER_THREAD
        if stop_event is not None:
            stop_event.set()
        if work_queue is not None:
            work_queue.put(None)
    if (
        worker is not None
        and worker.is_alive()
        and worker is not threading.current_thread()
    ):
        await asyncio.to_thread(worker.join, 2.0)


def _worker_loop(
    work_queue: queue.Queue[_CleanupWorkItem | None],
    stop_event: threading.Event,
) -> None:
    while True:
        item = work_queue.get()
        try:
            if item is None:
                return
            schedule_key = (
                item.database_identity,
                item.job_id,
            )
            if (
                stop_event.is_set()
                or item.database_identity
                != database.runtime_identity()
            ):
                continue
            flush(item.job_id)
        except Exception:
            logger.exception(
                "Unexpected project cleanup worker failure"
            )
        finally:
            if item is not None:
                with _WORKER_LOCK:
                    _SCHEDULED_JOBS.discard(schedule_key)
            work_queue.task_done()


def flush_project(project_id: str) -> bool:
    for job in store.pending_for_project(project_id):
        flush(job.job_id)
    return not store.pending_for_project(project_id)


def has_pending_reset(project_id: str) -> bool:
    return any(
        job.action == "reset"
        for job in store.pending_for_project(project_id)
    )


def has_pending_delete(project_id: str) -> bool:
    return store.has_pending_delete(project_id)


def _cleanup_secondary_artifacts(job: store.ProjectCleanupJob) -> None:
    from app.domains.video_localization import playback_proxy

    playback_proxy.delete_project_cache(job.project_id)
    preview_cache.delete_project_cache(job.project_id)
    waveform_cache.delete_result_cache_prefix(
        f"video-localization-{job.project_id}-"
    )
    media_assets.invalidate_project_directory_name(job.project_id)
    media_assets.invalidate_project_media_paths(job.project_id)
    media_assets.invalidate_project_timeline_audio_paths(job.project_id)
    payload = job.cleanup_payload
    for result_id in _string_list(payload.get("result_ids")):
        waveform_cache.delete_result_cache(result_id)
    surviving_outputs = {
        Path(item.output_path).resolve()
        for item in history_store.list_history(limit=-1)
        if item.output_path
    }
    for value in _string_list(payload.get("output_paths")):
        path = Path(value)
        if (
            path.exists()
            and not path.is_symlink()
            and path.resolve() not in surviving_outputs
        ):
            path.unlink()
    for value in _string_list(
        payload.get("managed_reference_paths")
    ):
        custom_reference_store.delete_if_unreferenced(Path(value))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


__all__ = [
    "flush",
    "flush_project",
    "has_pending_delete",
    "has_pending_reset",
    "new_job",
    "replay_pending",
    "schedule",
    "shutdown",
    "start_worker",
]
