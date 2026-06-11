from __future__ import annotations

import asyncio
import queue
import shutil
import threading
from pathlib import Path

from fastapi import UploadFile

from app.errors import AppException
from app.schemas.voice_studio import TaskStatus, TranscriptionRecord, TranscriptionTask, now_iso
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


def _task_is_active(status: TaskStatus | str) -> bool:
    return status in [
        TaskStatus.pending,
        TaskStatus.queued,
        TaskStatus.running,
        TaskStatus.retrying,
        TaskStatus.postprocessing,
    ]


def _source_audio_path(data: dict) -> str | None:
    upload_path = data.get("upload_path")
    if upload_path and Path(upload_path).exists():
        return str(upload_path)
    transcription_id = data.get("transcription_id")
    if transcription_id:
        record = db.get_one("transcriptions", "transcription_id", transcription_id)
        source_audio_path = record.get("source_audio_path") if record else None
        if source_audio_path and Path(source_audio_path).exists():
            return str(source_audio_path)
    return None


def _transcription_uses_upload(transcription_id: str | None, upload_path: str | None) -> bool:
    if not transcription_id or not upload_path:
        return False
    record = db.get_one("transcriptions", "transcription_id", transcription_id)
    return bool(record and record.get("source_audio_path") == upload_path)


def cancel_task(task_id: str) -> dict[str, str]:
    data = db.get_one("asr_tasks", "task_id", task_id)
    if not data:
        return {"task_id": task_id, "status": "not_found"}
    task = TranscriptionTask(**data)
    if task.status in [TaskStatus.success, TaskStatus.failed, TaskStatus.cancelled]:
        return {"task_id": task_id, "status": task.status.value}

    was_running = data.get("status") in [TaskStatus.running, TaskStatus.retrying, TaskStatus.postprocessing, "running", "retrying", "postprocessing"]
    task.status = TaskStatus.cancelled
    if was_running:
        task.error_message = "已请求取消；当前识别完成本轮后会丢弃结果。"
    if data.get("status") in [TaskStatus.pending, TaskStatus.queued, "pending", "queued"]:
        task.completed_at = now_iso()
        task.error_message = "已取消"
    _save_task({**data, **task.model_dump(), "cancel_requested": True})
    return {"task_id": task_id, "status": task.status.value}


def delete_task(task_id: str) -> dict[str, str]:
    data = db.get_one("asr_tasks", "task_id", task_id)
    if not data:
        return {"task_id": task_id, "status": "not_found"}
    task = TranscriptionTask(**data)
    if _task_is_active(task.status) or (task.status == TaskStatus.cancelled and not task.completed_at):
        return {"task_id": task_id, "status": "active_task"}
    upload_path = data.get("upload_path")
    if upload_path and not _transcription_uses_upload(task.transcription_id, upload_path):
        Path(upload_path).unlink(missing_ok=True)
    db.delete_one("asr_tasks", "task_id", task_id)
    return {"task_id": task_id, "status": "deleted"}


def retry_task(task_id: str) -> TranscriptionTask:
    data = db.get_one("asr_tasks", "task_id", task_id)
    if not data:
        raise AppException(404, "ASR_TASK_NOT_FOUND", "ASR task not found")
    task = TranscriptionTask(**data)
    if _task_is_active(task.status) or (task.status == TaskStatus.cancelled and not task.completed_at):
        raise AppException(409, "ASR_TASK_ACTIVE", "ASR task is still active")

    source_audio_path = _source_audio_path(data)
    if not source_audio_path:
        raise AppException(400, "ASR_SOURCE_AUDIO_MISSING", "Source audio is no longer available for retry")

    start_worker()
    source = Path(source_audio_path)
    suffix = source.suffix.lower() or ".wav"
    retry = TranscriptionTask(engine_id=task.engine_id, filename=task.filename, language=task.language, status=TaskStatus.queued)
    retry.has_source_audio = True
    retry_upload_path = asr_service.upload_path_for(task.engine_id, retry.task_id, suffix)
    retry_upload_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, retry_upload_path)

    task_data = {
        **retry.model_dump(),
        "size_bytes": retry_upload_path.stat().st_size,
        "upload_path": str(retry_upload_path),
    }
    _save_task(task_data)
    if _queue is None:
        start_worker()
    assert _queue is not None
    _queue.put(retry.task_id)
    return TranscriptionTask(**task_data)


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
    if data.get("status") in [TaskStatus.cancelled, "cancelled"]:
        if not task.completed_at:
            task.completed_at = now_iso()
            _save_task({**data, **task.model_dump()})
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
        latest = db.get_one("asr_tasks", "task_id", task_id) or {}
        if latest.get("cancel_requested") or latest.get("status") in [TaskStatus.cancelled, "cancelled"]:
            task.status = TaskStatus.cancelled
            task.error_message = latest.get("error_message") or "已取消，结果未保留。"
            task.completed_at = now_iso()
            Path(upload_path).unlink(missing_ok=True)
            _save_task({**data, **task.model_dump(), "upload_path": str(upload_path), "cancel_requested": True})
            return
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
        latest = db.get_one("asr_tasks", "task_id", task_id) or {}
        if latest.get("cancel_requested") or latest.get("status") in [TaskStatus.cancelled, "cancelled"]:
            task.status = TaskStatus.cancelled
            task.error_message = latest.get("error_message") or "已取消"
        else:
            task.status = TaskStatus.failed
            task.error_message = str(exc)
    task.completed_at = now_iso()
    latest = db.get_one("asr_tasks", "task_id", task_id) or {}
    _save_task({**data, **task.model_dump(), "upload_path": str(upload_path), "cancel_requested": latest.get("cancel_requested", False)})
