from __future__ import annotations

import asyncio
import queue
import threading

from app.domains.video_localization import operation_state
from app.domains.video_localization import service
from app.errors import AppException
from app.schemas.voice_studio import VideoLocalizationDraft, VideoLocalizationOperation, now_iso
from app.services import project_store

OperationKind = operation_state.OperationKind
OperationStatus = operation_state.OperationStatus

_queue: queue.Queue[str | None] | None = None
_worker_thread: threading.Thread | None = None
_lock = threading.Lock()
_queued_operation_ids: set[str] = set()

_ACTIVE_STATUSES = operation_state.ACTIVE_STATUSES
_TERMINAL_STATUSES = operation_state.TERMINAL_STATUSES
_KIND_LABELS = operation_state.KIND_LABELS


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


def cancel(project_id: str, operation_id: str) -> VideoLocalizationOperation | None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return None
    operation = _operation_from_draft(draft, operation_id)
    if not operation:
        raise AppException(404, "VIDEO_LOCALIZATION_OPERATION_NOT_FOUND", "Operation not found")
    if operation.status not in _ACTIVE_STATUSES:
        return operation

    completed_at = now_iso() if operation.status == "queued" else None
    updates = {
        "status": "cancelled",
        "cancel_requested": True,
        "completed_at": completed_at,
        "error_message": "已取消" if operation.status == "queued" else "已请求取消；当前处理结束后会丢弃任务结果。",
    }
    _mark_operation(project_id, operation_id, kind=operation.kind, **updates)
    updated = get_operation(project_id, operation_id)
    return updated or operation.model_copy(update=updates)


def retry(project_id: str, operation_id: str) -> VideoLocalizationOperation | None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return None
    operation = _operation_from_draft(draft, operation_id)
    if not operation:
        raise AppException(404, "VIDEO_LOCALIZATION_OPERATION_NOT_FOUND", "Operation not found")
    if operation.status in _ACTIVE_STATUSES:
        raise AppException(409, "VIDEO_LOCALIZATION_OPERATION_ACTIVE", "Operation is still active")
    service.save_video_localization(project_id, operation_state.with_kind_status(draft, operation.kind, "queued"))
    return submit(project_id, operation.kind, operation.parameters)


def submit(project_id: str, kind: OperationKind, parameters: dict | None = None) -> VideoLocalizationOperation | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = service.get_video_localization(project_id) or VideoLocalizationDraft()
    operation_state.validate_prerequisites(kind, draft)

    active = operation_state.active_operation_for_kind(draft, kind)
    if active:
        _enqueue(active.operation_id)
        return active

    operation = VideoLocalizationOperation(
        project_id=project_id,
        kind=kind,
        label=_KIND_LABELS[kind],
        parameters=parameters or {},
    )
    draft = operation_state.with_operation(draft, operation)
    draft = operation_state.with_kind_status(draft, kind, "queued")
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
    if operation.status == "cancelled" or operation.cancel_requested:
        _mark_operation(project_id, operation_id, kind=operation.kind, status="cancelled", completed_at=operation.completed_at or now_iso())
        return

    _mark_operation(project_id, operation_id, kind=operation.kind, status="running", progress=0.2, started_at=now_iso())
    try:
        if operation.kind == "source_audio":
            updated = service.extract_source_audio(project_id)
            summary = operation_state.source_audio_summary(updated)
        elif operation.kind == "stems":
            updated = service.separate_source_audio(project_id)
            summary = operation_state.stems_summary(updated)
        elif operation.kind == "english_asr":
            engine_id = str(operation.parameters.get("engine_id") or "faster-whisper-turbo")
            updated = service.transcribe_english_source_audio(project_id, engine_id=engine_id)
            summary = operation_state.english_asr_summary(updated)
        elif operation.kind == "reference_clips":
            updated = service.create_reference_clips_from_cues(project_id)
            summary = operation_state.reference_clips_summary(updated)
        else:
            raise AppException(400, "VIDEO_LOCALIZATION_OPERATION_UNSUPPORTED", f"Unsupported operation: {operation.kind}")
        if updated is None:
            raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
        latest = get_operation(project_id, operation_id)
        if operation_state.operation_was_cancelled(latest):
            _mark_operation(project_id, operation_id, kind=operation.kind, status="cancelled", progress=1.0, completed_at=now_iso(), error_message="已取消，任务结果未作为成功状态保留。")
            return
        _mark_operation(project_id, operation_id, kind=operation.kind, status="success", progress=1.0, completed_at=now_iso(), result_summary=summary)
    except AppException as exc:
        latest = get_operation(project_id, operation_id)
        if operation_state.operation_was_cancelled(latest):
            _mark_operation(project_id, operation_id, kind=operation.kind, status="cancelled", progress=1.0, completed_at=now_iso(), error_message="已取消，失败结果未保留。")
            return
        _mark_kind_failed(project_id, operation.kind, exc.code, exc.message)
        _mark_operation(project_id, operation_id, kind=operation.kind, status="failed", progress=1.0, completed_at=now_iso(), error_code=exc.code, error_message=exc.message)
    except Exception as exc:
        latest = get_operation(project_id, operation_id)
        if operation_state.operation_was_cancelled(latest):
            _mark_operation(project_id, operation_id, kind=operation.kind, status="cancelled", progress=1.0, completed_at=now_iso(), error_message="已取消，失败结果未保留。")
            return
        _mark_kind_failed(project_id, operation.kind, "VIDEO_LOCALIZATION_OPERATION_FAILED", str(exc))
        _mark_operation(
            project_id,
            operation_id,
            kind=operation.kind,
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
            if operation.cancel_requested:
                operation.status = "cancelled"
                operation.completed_at = operation.completed_at or now_iso()
                changed = True
            elif operation.status in _ACTIVE_STATUSES:
                operation.status = "queued"
                operation.started_at = None
                operation.error_message = None
                changed = True
        if changed:
            service.save_video_localization(project.project_id, draft)
        for operation in draft.operations:
            if operation.status == "queued":
                _enqueue(operation.operation_id)


def _find_operation(operation_id: str) -> tuple[str | None, VideoLocalizationOperation | None]:
    for project in project_store.list_projects():
        raw = project.parameters.get(service.VIDEO_LOCALIZATION_KEY) or {}
        draft = VideoLocalizationDraft(**raw)
        for operation in draft.operations:
            if operation.operation_id == operation_id:
                return project.project_id, operation
    return None, None


def _operation_from_draft(draft: VideoLocalizationDraft, operation_id: str) -> VideoLocalizationOperation | None:
    return operation_state.operation_from_draft(draft, operation_id)


def _mark_operation(project_id: str, operation_id: str, *, kind: OperationKind | None = None, **updates) -> None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return
    next_operations = []
    for operation in draft.operations:
        if operation.operation_id == operation_id:
            next_operations.append(operation.model_copy(update=updates))
        else:
            next_operations.append(operation)
    next_draft = draft.model_copy(update={"operations": next_operations})
    status = updates.get("status")
    if kind and status in _TERMINAL_STATUSES.union(_ACTIVE_STATUSES):
        next_draft = operation_state.with_kind_status(next_draft, kind, status)
    service.save_video_localization(project_id, next_draft)


def _mark_kind_failed(project_id: str, kind: OperationKind, code: str, message: str) -> None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return
    service.save_video_localization(project_id, operation_state.with_kind_status(draft, kind, "failed", error_code=code, error_message=message))
