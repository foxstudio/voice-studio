from __future__ import annotations

import asyncio
import queue
import threading
from pathlib import Path

from fastapi import UploadFile

from app.models.schemas import TaskStatus, TranscriptionRecord, TranscriptionTask, now_iso
from app.services import asr_service, audio_tools, database as db

_queue: queue.Queue[str | None] | None = None
_worker_thread: threading.Thread | None = None
_lock = threading.Lock()


def _save_task(data: dict) -> TranscriptionTask:
    db.upsert("asr_tasks", data["task_id"], data)
    return TranscriptionTask(**data)


def get_task(task_id: str) -> TranscriptionTask | None:
    data = db.get_one("asr_tasks", "task_id", task_id)
    return TranscriptionTask(**data) if data else None


def list_tasks() -> list[TranscriptionTask]:
    return [TranscriptionTask(**item) for item in db.list_all("asr_tasks", "created_at")]


def start_worker() -> None:
    global _queue, _worker_thread
    with _lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _queue = queue.Queue()
        _worker_thread = threading.Thread(target=_worker, args=(_queue,), daemon=True, name="asr-task-worker")
        _worker_thread.start()


async def shutdown() -> None:
    global _queue, _worker_thread
    with _lock:
        q = _queue
        thread = _worker_thread
        _queue = None
        _worker_thread = None
    if q:
        q.put(None)
    if thread and thread.is_alive():
        await asyncio.to_thread(thread.join, 2)


async def submit(file: UploadFile, language: str = "auto", engine_id: str = "mimo-v2.5-asr") -> TranscriptionTask:
    suffix = Path(file.filename or "transcribe.wav").suffix.lower() or ".wav"
    asr_service.validate_request(engine_id, language, suffix)

    start_worker()
    task = TranscriptionTask(engine_id=engine_id, filename=file.filename or f"upload{suffix}", language=language)
    upload_path = asr_service.upload_path_for(engine_id, task.task_id, suffix)
    content = await file.read()
    upload_path.write_bytes(content)
    task.has_source_audio = True

    task_data = {
        **task.model_dump(),
        "size_bytes": len(content),
        "upload_path": str(upload_path),
    }
    _save_task(task_data)
    if _queue is None:
        start_worker()
    assert _queue is not None
    _queue.put(task.task_id)
    return TranscriptionTask(**task_data)


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
    data = db.get_one("asr_tasks", "task_id", task_id)
    if not data:
        return
    task = TranscriptionTask(**data)
    upload_path = data.get("upload_path")
    if not upload_path:
        return

    task.status = TaskStatus.running
    task.started_at = now_iso()
    _save_task({**data, **task.model_dump(), "upload_path": upload_path})
    try:
        result = asr_service.transcribe(engine_id=task.engine_id, audio_path=upload_path, language=task.language)
        duration_ms = None
        try:
            duration_ms = audio_tools.probe_audio(Path(upload_path)).get("duration_ms")
        except Exception:
            duration_ms = None
        record = TranscriptionRecord(
            engine_id=task.engine_id,
            filename=task.filename,
            language=task.language,
            text=result["text"],
            segments=asr_service.normalize_segments(result.get("segments")),
            has_source_audio=True,
            duration_ms=duration_ms,
            size_bytes=task.size_bytes,
            usage_seconds=result.get("usage_seconds"),
            provider_response_id=result.get("provider_response_id"),
        )
        for key, value in asr_service.timestamp_metadata_for(record.engine_id, record.segments).items():
            setattr(record, key, value)
        db.upsert(
            "transcriptions",
            record.transcription_id,
            {**record.model_dump(), "source_audio_path": str(upload_path)},
            "created_at",
        )
        task.status = TaskStatus.success
        task.text = record.text
        task.segments = record.segments
        task.has_source_audio = True
        task.timestamp_mode = record.timestamp_mode
        task.timestamp_source_engine_id = record.timestamp_source_engine_id
        task.transcription_id = record.transcription_id
        task.duration_ms = record.duration_ms
        task.usage_seconds = record.usage_seconds
        task.provider_response_id = record.provider_response_id
    except Exception as exc:
        task.status = TaskStatus.failed
        task.error_message = str(exc)
        Path(upload_path).unlink(missing_ok=True)
    task.completed_at = now_iso()
    _save_task({**data, **task.model_dump(), "upload_path": str(upload_path)})
