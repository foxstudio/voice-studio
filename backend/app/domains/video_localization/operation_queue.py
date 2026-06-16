from __future__ import annotations

import asyncio
import queue
import threading
from pathlib import Path
from typing import Literal

from app.domains.video_localization import service
from app.errors import AppException
from app.schemas.voice_studio import VideoLocalizationDraft, VideoLocalizationOperation, now_iso
from app.services import project_store

OperationKind = Literal["source_audio", "stems", "english_asr"]
OperationStatus = Literal["queued", "running", "success", "failed", "cancelled"]

_queue: queue.Queue[str | None] | None = None
_worker_thread: threading.Thread | None = None
_lock = threading.Lock()
_queued_operation_ids: set[str] = set()

_ACTIVE_STATUSES = {"queued", "running"}
_KIND_LABELS: dict[OperationKind, str] = {
    "source_audio": "抽取源音轨",
    "stems": "分离人声与背景声",
    "english_asr": "英文 ASR 转字幕",
}


def start_worker() -> None:
    global _queue, _worker_thread
    with _lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _queue = queue.Queue()
        _worker_thread = threading.Thread(target=_worker, args=(_queue,), daemon=True, name="video-localization-operation-worker")
        _worker_thread.start()
    _recover_active_operations()


async def shutdown() -> None:
    global _queue, _worker_thread
    with _lock:
        q = _queue
        thread = _worker_thread
        _queue = None
        _worker_thread = None
        _queued_operation_ids.clear()
    if q:
        q.put(None)
    if thread and thread.is_alive():
        await asyncio.to_thread(thread.join, 2)


def list_operations(project_id: str) -> list[VideoLocalizationOperation] | None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return None
    return sorted(draft.operations, key=lambda item: item.created_at, reverse=True)


def get_operation(project_id: str, operation_id: str) -> VideoLocalizationOperation | None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return None
    return next((operation for operation in draft.operations if operation.operation_id == operation_id), None)


def submit(project_id: str, kind: OperationKind, parameters: dict | None = None) -> VideoLocalizationOperation | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = service.get_video_localization(project_id) or VideoLocalizationDraft()
    _validate_prerequisites(kind, draft)

    active = _active_operation_for_kind(draft, kind)
    if active:
        _enqueue(active.operation_id)
        return active

    operation = VideoLocalizationOperation(
        project_id=project_id,
        kind=kind,
        label=_KIND_LABELS[kind],
        parameters=parameters or {},
    )
    draft = _with_operation(draft, operation)
    draft = _with_kind_status(draft, kind, "queued")
    service.save_video_localization(project_id, draft)
    _enqueue(operation.operation_id)
    return operation


def _worker(task_queue: queue.Queue[str | None]) -> None:
    while True:
        operation_id = task_queue.get()
        if operation_id is None:
            task_queue.task_done()
            return
        with _lock:
            _queued_operation_ids.discard(operation_id)
        try:
            _process(operation_id)
        finally:
            task_queue.task_done()


def _process(operation_id: str) -> None:
    project_id, operation = _find_operation(operation_id)
    if not project_id or not operation:
        return
    if operation.status == "cancelled":
        return

    _mark_operation(project_id, operation_id, status="running", progress=0.2, started_at=now_iso())
    try:
        if operation.kind == "source_audio":
            updated = service.extract_source_audio(project_id)
            summary = _source_audio_summary(updated)
        elif operation.kind == "stems":
            updated = service.separate_source_audio(project_id)
            summary = _stems_summary(updated)
        elif operation.kind == "english_asr":
            engine_id = str(operation.parameters.get("engine_id") or "faster-whisper-turbo")
            updated = service.transcribe_english_source_audio(project_id, engine_id=engine_id)
            summary = _english_asr_summary(updated)
        else:
            raise AppException(400, "VIDEO_LOCALIZATION_OPERATION_UNSUPPORTED", f"Unsupported operation: {operation.kind}")
        if updated is None:
            raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
        _mark_operation(project_id, operation_id, status="success", progress=1.0, completed_at=now_iso(), result_summary=summary)
    except AppException as exc:
        _mark_kind_failed(project_id, operation.kind, exc.code, exc.message)
        _mark_operation(project_id, operation_id, status="failed", progress=1.0, completed_at=now_iso(), error_code=exc.code, error_message=exc.message)
    except Exception as exc:
        _mark_kind_failed(project_id, operation.kind, "VIDEO_LOCALIZATION_OPERATION_FAILED", str(exc))
        _mark_operation(
            project_id,
            operation_id,
            status="failed",
            progress=1.0,
            completed_at=now_iso(),
            error_code="VIDEO_LOCALIZATION_OPERATION_FAILED",
            error_message=str(exc),
        )


def _enqueue(operation_id: str) -> None:
    start_worker()
    with _lock:
        if operation_id in _queued_operation_ids:
            return
        _queued_operation_ids.add(operation_id)
        q = _queue
    if q is not None:
        q.put(operation_id)


def _recover_active_operations() -> None:
    for project in project_store.list_projects():
        raw = project.parameters.get(service.VIDEO_LOCALIZATION_KEY) or {}
        draft = VideoLocalizationDraft(**raw)
        changed = False
        for operation in draft.operations:
            if operation.status in _ACTIVE_STATUSES:
                operation.status = "queued"
                operation.started_at = None
                operation.error_message = None
                changed = True
        if changed:
            service.save_video_localization(project.project_id, draft)
        for operation in draft.operations:
            if operation.status == "queued":
                _enqueue(operation.operation_id)


def _validate_prerequisites(kind: OperationKind, draft: VideoLocalizationDraft) -> None:
    if kind == "source_audio":
        if not draft.source_media.video_path:
            raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_MISSING", "Import a source video before extracting audio")
        if not Path(draft.source_media.video_path).exists():
            raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_NOT_FOUND", "Source video file is missing")
        return

    if kind in {"stems", "english_asr"}:
        audio_path_value = draft.source_media.audio_path or draft.stems.original_audio_path
        if not audio_path_value:
            raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING", "Extract source audio before running this operation")
        if not Path(audio_path_value).exists():
            raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND", "Source audio file is missing")
        return


def _active_operation_for_kind(draft: VideoLocalizationDraft, kind: OperationKind) -> VideoLocalizationOperation | None:
    return next((operation for operation in reversed(draft.operations) if operation.kind == kind and operation.status in _ACTIVE_STATUSES), None)


def _find_operation(operation_id: str) -> tuple[str | None, VideoLocalizationOperation | None]:
    for project in project_store.list_projects():
        raw = project.parameters.get(service.VIDEO_LOCALIZATION_KEY) or {}
        draft = VideoLocalizationDraft(**raw)
        for operation in draft.operations:
            if operation.operation_id == operation_id:
                return project.project_id, operation
    return None, None


def _mark_operation(project_id: str, operation_id: str, **updates) -> None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return
    next_operations = []
    for operation in draft.operations:
        if operation.operation_id == operation_id:
            next_operations.append(operation.model_copy(update=updates))
        else:
            next_operations.append(operation)
    service.save_video_localization(project_id, draft.model_copy(update={"operations": next_operations}))


def _mark_kind_failed(project_id: str, kind: OperationKind, code: str, message: str) -> None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return
    service.save_video_localization(project_id, _with_kind_status(draft, kind, "failed", error_code=code, error_message=message))


def _with_operation(draft: VideoLocalizationDraft, operation: VideoLocalizationOperation) -> VideoLocalizationDraft:
    return draft.model_copy(update={"operations": [*draft.operations, operation]})


def _with_kind_status(
    draft: VideoLocalizationDraft,
    kind: OperationKind,
    status: OperationStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
) -> VideoLocalizationDraft:
    metadata = dict(draft.source_media.metadata)
    stems = draft.stems
    if kind == "source_audio":
        metadata["audio_extract_status"] = status
        if error_code:
            metadata["audio_extract_error_code"] = error_code
        if error_message:
            metadata["audio_extract_error"] = error_message
    elif kind == "english_asr":
        metadata["english_asr_status"] = status
        if error_code:
            metadata["english_asr_error_code"] = error_code
        if error_message:
            metadata["english_asr_error"] = error_message
    elif kind == "stems":
        stems = draft.stems.model_copy(
            update={
                "separation_status": "running" if status in {"queued", "running"} else status,
                "quality_flags": sorted(set([*draft.stems.quality_flags, error_code] if error_code else draft.stems.quality_flags)),
            }
        )
    source_media = draft.source_media.model_copy(update={"metadata": metadata})
    return draft.model_copy(update={"source_media": source_media, "stems": stems})


def _source_audio_summary(draft: VideoLocalizationDraft | None) -> dict:
    if not draft:
        return {}
    return {
        "audio_path": draft.source_media.audio_path,
        "duration_ms": draft.source_media.duration_ms,
        "sample_rate": draft.source_media.metadata.get("audio_sample_rate"),
        "channels": draft.source_media.metadata.get("audio_channels"),
    }


def _stems_summary(draft: VideoLocalizationDraft | None) -> dict:
    if not draft:
        return {}
    return {
        "vocals_clean_path": draft.stems.vocals_clean_path,
        "background_path": draft.stems.background_path,
        "separation_engine_id": draft.stems.separation_engine_id,
    }


def _english_asr_summary(draft: VideoLocalizationDraft | None) -> dict:
    if not draft:
        return {}
    return {
        "engine_id": draft.source_media.metadata.get("english_asr_engine_id"),
        "segment_count": draft.source_media.metadata.get("english_asr_segment_count"),
        "cue_count": len(draft.cues),
    }
