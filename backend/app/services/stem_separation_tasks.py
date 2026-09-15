from __future__ import annotations

import asyncio
import json
import queue
import shutil
import threading
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.errors import AppException
from app.schemas.stem_separation_task import StemSeparationTask
from app.schemas.voice_studio import TaskStatus, now_iso
from app.services import audio_tools, settings_store, stem_separation_engine


SUPPORTED_SUFFIXES = frozenset({".wav", ".flac"})
_queue: queue.Queue[str | None] | None = None
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()
_store_lock = threading.RLock()
_RUNTIME_ID = uuid4().hex


def _root() -> Path:
    path = settings_store.cache_dir() / "stem-separation-tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _task_dir(task_id: str) -> Path:
    return _root() / task_id


def _task_file(task_id: str) -> Path:
    return _task_dir(task_id) / "task.json"


def _source_path(task_id: str, suffix: str) -> Path:
    return _task_dir(task_id) / f"source{suffix}"


def artifact_path(task_id: str, kind: str) -> Path:
    if kind not in {"vocals", "background"}:
        raise AppException(404, "STEM_ARTIFACT_NOT_FOUND", "Stem artifact not found")
    return _task_dir(task_id) / f"{kind}.wav"


def _write_task(data: dict) -> StemSeparationTask:
    task = StemSeparationTask(**data)
    task_dir = _task_dir(task.task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    destination = _task_file(task.task_id)
    temporary = destination.with_suffix(".json.part")
    with _store_lock:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)
    return task


def _read_task_data(task_id: str) -> dict | None:
    path = _task_file(task_id)
    if not path.is_file():
        return None
    with _store_lock:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


def get_task(task_id: str) -> StemSeparationTask | None:
    data = _read_task_data(task_id)
    if not data:
        return None
    data = _recover_orphaned(data)
    return StemSeparationTask(**data)


def _is_active(status: TaskStatus | str) -> bool:
    return status in {
        TaskStatus.pending,
        TaskStatus.queued,
        TaskStatus.running,
        TaskStatus.retrying,
        "pending",
        "queued",
        "running",
        "retrying",
    }


def _recover_orphaned(data: dict) -> dict:
    if _is_active(data.get("status", "")) and data.get("runtime_id") != _RUNTIME_ID:
        task = StemSeparationTask(**data)
        task.status = TaskStatus.failed
        task.error_message = "Voice Studio restarted before the stem task completed"
        task.completed_at = now_iso()
        data = {**data, **task.model_dump(), "runtime_id": _RUNTIME_ID}
        _write_task(data)
    return data


async def submit(file: UploadFile) -> StemSeparationTask:
    suffix = Path(file.filename or "source.wav").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise AppException(
            400,
            "STEM_SOURCE_FORMAT_UNSUPPORTED",
            "Stem separation accepts WAV or FLAC audio",
        )
    start_worker()
    task = StemSeparationTask(
        filename=file.filename or f"source{suffix}",
        engine_id=stem_separation_engine.ENGINE_ID,
    )
    source = _source_path(task.task_id, suffix)
    source.parent.mkdir(parents=True, exist_ok=True)
    size_bytes = 0
    try:
        with source.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                destination.write(chunk)
                size_bytes += len(chunk)
        if size_bytes <= 0:
            raise AppException(400, "STEM_SOURCE_EMPTY", "Source audio is empty")
        probe = audio_tools.probe_audio(source)
    except Exception:
        shutil.rmtree(source.parent, ignore_errors=True)
        raise
    data = {
        **task.model_dump(),
        "size_bytes": size_bytes,
        "duration_ms": probe.get("duration_ms"),
        "sample_rate": probe.get("sample_rate"),
        "channels": probe.get("channels"),
        "source_path": str(source),
        "cancel_requested": False,
        "runtime_id": _RUNTIME_ID,
    }
    saved = _write_task(data)
    assert _queue is not None
    _queue.put(task.task_id)
    return saved


def start_worker() -> None:
    global _queue, _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _queue = queue.Queue()
        _worker_thread = threading.Thread(
            target=_worker,
            args=(_queue,),
            daemon=True,
            name="stem-separation-task-worker",
        )
        _worker_thread.start()


async def shutdown() -> None:
    global _queue, _worker_thread
    with _worker_lock:
        task_queue = _queue
        thread = _worker_thread
        _queue = None
        _worker_thread = None
    if task_queue:
        task_queue.put(None)
    if thread and thread.is_alive():
        await asyncio.to_thread(thread.join, 2)


def cancel_task(task_id: str) -> StemSeparationTask | None:
    data = _read_task_data(task_id)
    if not data:
        return None
    data = _recover_orphaned(data)
    task = StemSeparationTask(**data)
    if not _is_active(task.status):
        return task
    was_running = task.status == TaskStatus.running
    task.status = TaskStatus.cancelled
    task.error_message = (
        "Cancellation requested; current separation will finish and discard its result."
        if was_running
        else "Cancelled"
    )
    if not was_running:
        task.completed_at = now_iso()
    return _write_task({**data, **task.model_dump(), "cancel_requested": True})


def delete_task(task_id: str) -> bool:
    data = _read_task_data(task_id)
    if not data:
        return False
    data = _recover_orphaned(data)
    task = StemSeparationTask(**data)
    if _is_active(task.status) or (
        task.status == TaskStatus.cancelled and not task.completed_at
    ):
        raise AppException(409, "STEM_TASK_ACTIVE", "Stem task is still active")
    shutil.rmtree(_task_dir(task_id), ignore_errors=True)
    return True


def _worker(task_queue: queue.Queue[str | None]) -> None:
    while True:
        task_id = task_queue.get()
        if task_id is None:
            task_queue.task_done()
            return
        try:
            _process(task_id)
        finally:
            task_queue.task_done()


def _process(task_id: str) -> None:
    data = _read_task_data(task_id)
    if not data:
        return
    task = StemSeparationTask(**data)
    if task.status == TaskStatus.cancelled:
        if not task.completed_at:
            task.completed_at = now_iso()
            _write_task({**data, **task.model_dump()})
        return
    source = Path(data.get("source_path", ""))
    task.status = TaskStatus.running
    task.started_at = now_iso()
    _write_task({**data, **task.model_dump()})
    vocals = artifact_path(task_id, "vocals")
    background = artifact_path(task_id, "background")
    try:
        settings = settings_store.get()
        stem_separation_engine.separate(
            source,
            vocals,
            background,
            overlap=settings.stem_separation_overlap,
            chunk_duration_seconds=(
                settings.stem_separation_chunk_duration_seconds
            ),
        )
        latest = _read_task_data(task_id) or {}
        if latest.get("cancel_requested"):
            vocals.unlink(missing_ok=True)
            background.unlink(missing_ok=True)
            task.status = TaskStatus.cancelled
            task.error_message = latest.get("error_message") or "Cancelled"
        else:
            task.status = TaskStatus.success
            task.vocals_ready = vocals.is_file() and vocals.stat().st_size > 0
            task.background_ready = (
                background.is_file() and background.stat().st_size > 0
            )
            if not task.vocals_ready or not task.background_ready:
                raise RuntimeError("Stem separation did not create both output files")
    except Exception as exc:
        latest = _read_task_data(task_id) or {}
        if latest.get("cancel_requested"):
            task.status = TaskStatus.cancelled
            task.error_message = latest.get("error_message") or "Cancelled"
        else:
            task.status = TaskStatus.failed
            task.error_message = str(exc)
    task.completed_at = now_iso()
    latest = _read_task_data(task_id) or data
    _write_task({**latest, **task.model_dump()})
