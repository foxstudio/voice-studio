from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from app.models.exceptions import AppException
from typing import Any

from fastapi import WebSocket

from app.schemas.voice_studio import (
    ExportRecord,
    GenerateRequest,
    GenerationTask,
    HistoryItem,
    LongformSegmentTask,
    LongformTask,
    Project,
    ScriptSegment,
    SegmentStatus,
    TaskStatus,
    TranscriptionRecord,
    TTSVerificationResponse,
    now_iso,
)
from app.services import asr_service, audio_tools, database as db, engine_policy, engine_registry, engine_request_builder, history_store, project_store, settings_store, text_verifier, voice_store

_queue: asyncio.Queue[str] | None = None
_worker_task: asyncio.Task[None] | None = None
_worker_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()
_cancelled: set[str] = set()
_queued_task_ids: set[str] = set()
_clients: list[WebSocket] = []
_clients_lock = threading.Lock()

_TERMINAL_STATUSES = {TaskStatus.success, TaskStatus.failed, TaskStatus.cancelled}
_TERMINAL_STATUS_VALUES = {s.value for s in _TERMINAL_STATUSES}
_RECOVERABLE_STATUSES = {
    TaskStatus.pending,
    TaskStatus.queued,
    TaskStatus.running,
    TaskStatus.postprocessing,
    TaskStatus.retrying,
}


def _save(task: GenerationTask) -> GenerationTask:
    db.upsert("tasks", task.task_id, task.model_dump())
    return task


def _timeout_seconds_for(engine_id: str) -> int:
    return engine_policy.timeout_seconds_for(engine_id)


def _task_is_active(status: TaskStatus | str) -> bool:
    return status in [TaskStatus.pending, TaskStatus.queued, TaskStatus.running, TaskStatus.postprocessing, TaskStatus.retrying]


def _is_mimo_tts(engine_id: str) -> bool:
    return engine_policy.is_mimo_tts(engine_id)


def _mimo_idempotency_marker(req: GenerateRequest) -> str:
    if req.idempotency_marker:
        return req.idempotency_marker
    payload = {
        "engine_id": req.engine_id,
        "text": req.text,
        "voice_id": req.voice_id,
        "reference_audio_path": req.reference_audio_path,
        "mimo_voice": req.mimo_voice,
        "instruction": req.style_instruction or req.emotion_text or req.emotion,
        "voice_design_prompt": req.voice_design_prompt or req.style_instruction or req.emotion_text,
        "optimize_text_preview": req.optimize_text_preview,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "output_format": req.output_format,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"mimo:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def _parameters_with_idempotency_marker(req: GenerateRequest) -> dict:
    parameters = req.model_dump()
    if _is_mimo_tts(req.engine_id):
        parameters["idempotency_marker"] = _mimo_idempotency_marker(req)
    return parameters


def _elapsed_since(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, (datetime.now() - datetime.fromisoformat(value)).total_seconds())
    except ValueError:
        return 0.0


def _coerce_task_status(value: Any) -> TaskStatus | str | None:
    if isinstance(value, TaskStatus):
        return value
    if isinstance(value, str):
        if value in _TERMINAL_STATUS_VALUES:
            return TaskStatus(value)
        return value
    return None


def _task_status_is_terminal(value: TaskStatus | str | None) -> bool:
    coerced = _coerce_task_status(value)
    return coerced in _TERMINAL_STATUSES


def _task_is_protected_by_state(task: GenerationTask, *, row: dict[str, Any] | None = None) -> bool:
    data = row if row is not None else db.get_one("tasks", "task_id", task.task_id)
    if task.task_id in _cancelled:
        return True
    if not data:
        return _task_status_is_terminal(task.status)
    if data.get("cancel_requested"):
        return True
    return _task_status_is_terminal(_coerce_task_status(data.get("status")))


def _sync_task_status_from_db(task: GenerationTask, row: dict[str, Any] | None = None) -> GenerationTask:
    data = row if row is not None else db.get_one("tasks", "task_id", task.task_id)
    if not data:
        if task.task_id in _cancelled:
            task.status = TaskStatus.cancelled
            task.error_message = task.error_message or "已取消"
        return task
    status = _coerce_task_status(data.get("status"))
    if data.get("cancel_requested") and status not in _TERMINAL_STATUSES:
        task.status = TaskStatus.cancelled
        task.error_message = task.error_message or data.get("error_message") or "已取消"
        task.completed_at = task.completed_at or data.get("completed_at")
        return task
    if status in _TERMINAL_STATUSES:
        task.status = status
        task.completed_at = task.completed_at or data.get("completed_at")
        if not task.error_message and data.get("error_message"):
            task.error_message = data.get("error_message")
    elif task.task_id in _cancelled:
        task.status = TaskStatus.cancelled
        task.error_message = task.error_message or data.get("error_message") or "已取消"
    return task


def _reconcile_stale_task(task_data: dict) -> GenerationTask:
    task = GenerationTask(**task_data)
    if task_data.get("cancel_requested"):
        return task
    if task.status not in [TaskStatus.running, TaskStatus.postprocessing, TaskStatus.retrying]:
        return task
    stale_after = _timeout_seconds_for(task.engine_id) + 180
    if _elapsed_since(task.started_at) <= stale_after:
        return task
    task.status = TaskStatus.failed
    task.completed_at = now_iso()
    task.error_message = "任务超过模型常规超时窗口，已自动标记为失败。可复用参数重新生成。"
    _cancelled.discard(task.task_id)
    return _save(task)


def list_tasks() -> list[GenerationTask]:
    return [_reconcile_stale_task(row) for row in db.list_all("tasks", "created_at", limit=-1)]


def get_task(task_id: str) -> GenerationTask | None:
    data = db.get_one("tasks", "task_id", task_id)
    return _reconcile_stale_task(data) if data else None


def _verification_language(task: GenerationTask) -> str:
    value = task.parameters.get("language")
    return value if value in {"auto", "zh", "en"} else "zh"


def _save_history_verification(result_id: str | None, report: TTSVerificationResponse | None, error: str | None = None) -> None:
    if not result_id:
        return
    item = history_store.get(result_id)
    if not item:
        return
    item.verification = report
    item.verification_error = error
    history_store.add(item)


def attach_verification(task_id: str, report: TTSVerificationResponse | None, error: str | None = None) -> GenerationTask | None:
    task = get_task(task_id)
    if not task:
        return None
    task.verification = report
    task.verification_error = error
    _save_history_verification(task.result_id, report, error)
    return _save(task)


def attach_verification_to_result(result_id: str, report: TTSVerificationResponse | None, error: str | None = None) -> list[GenerationTask]:
    updated: list[GenerationTask] = []
    _save_history_verification(result_id, report, error)
    for row in db.list_all("tasks", "created_at", False, limit=-1):
        task = GenerationTask(**row)
        if task.result_id != result_id:
            continue
        task.verification = report
        task.verification_error = error
        updated.append(_save(task))
    return updated


def _verify_task_output(task: GenerationTask, *, asr_engine_id: str = "qwen3-asr-mlx") -> TTSVerificationResponse:
    import tempfile

    if not task.result_id:
        raise ValueError("任务没有可校对的生成结果")
    item = history_store.get(task.result_id)
    if not item:
        raise ValueError("生成结果不存在")
    audio_path = history_store.audio_path(task.result_id)
    if not audio_path:
        raise ValueError("结果音频不存在")
    language = _verification_language(task)
    suffix = audio_path.suffix.lower() or ".wav"
    # FLAC 等非 WAV/MP3 格式 → 临时转为 WAV 再送 ASR
    asr_path = audio_path
    tmp_path: str | None = None
    if suffix not in asr_service.SUPPORTED_SUFFIXES:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()
        audio_tools.convert_file(audio_path, tmp_path, "wav")
        asr_path = Path(tmp_path)
        suffix = ".wav"
    try:
        asr_service.validate_request(asr_engine_id, language, suffix)
        result = asr_service.transcribe(engine_id=asr_engine_id, audio_path=str(asr_path), language=language)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
    record = TranscriptionRecord(
        engine_id=asr_engine_id,
        filename=audio_path.name,
        language=language if language in {"auto", "zh", "en"} else "zh",
        text=result["text"],
        segments=asr_service.normalize_segments(result.get("segments")),
        size_bytes=audio_path.stat().st_size if audio_path.exists() else 0,
        usage_seconds=result.get("usage_seconds"),
        provider_response_id=result.get("provider_response_id"),
    )
    for key, value in asr_service.timestamp_metadata_for(record.engine_id, record.segments).items():
        setattr(record, key, value)
    record.has_source_audio = False
    db.upsert("transcriptions", record.transcription_id, record.model_dump(), "created_at")
    return text_verifier.verify_transcript(
        expected_text=task.input_text,
        transcript_text=record.text,
        result_id=task.result_id,
        transcription_id=record.transcription_id,
        asr_engine_id=asr_engine_id,
    )


async def _auto_verify_task(task_id: str) -> None:
    task = get_task(task_id)
    if not task or not task.result_id or task.verification:
        return
    try:
        report = await asyncio.to_thread(_verify_task_output, task)
        updated = attach_verification(task_id, report)
    except Exception as exc:
        updated = attach_verification(task_id, None, f"自动校对失败：{exc}")
    if updated:
        await _broadcast(updated)


def schedule_auto_verification(task_id: str) -> None:
    loop = _worker_loop
    if not loop or loop.is_closed():
        return
    loop.create_task(_auto_verify_task(task_id))


def find_longform_export_task(longform_task_id: str, export_id: str | None) -> GenerationTask | None:
    for row in db.list_all("tasks", "created_at", False, limit=-1):
        task = GenerationTask(**row)
        if task.task_type == "export" and task.longform_task_id == longform_task_id:
            if not export_id or task.longform_export_id == export_id:
                return task
    return None


def update_longform_segment_metadata(task_id: str, *, longform_task_id: str, segment_index: int, segment_count: int) -> GenerationTask | None:
    task = get_task(task_id)
    if not task:
        return None
    changed = (
        task.longform_task_id != longform_task_id
        or task.longform_segment_index != segment_index
        or task.longform_segment_count != segment_count
    )
    if not changed:
        return task
    task.longform_task_id = longform_task_id
    task.longform_segment_index = segment_index
    task.longform_segment_count = segment_count
    return _save(task)


async def _broadcast(task: GenerationTask) -> None:
    dead = []
    payload = task.model_dump_json()
    with _clients_lock:
        clients = list(_clients)
    for ws in clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    if dead:
        with _clients_lock:
            for ws in dead:
                if ws in _clients:
                    _clients.remove(ws)


def _broadcast_from_thread(task: GenerationTask) -> None:
    loop = _worker_loop
    if not loop or loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(_broadcast(task), loop)
    except Exception:
        return


def add_ws_client(ws: WebSocket) -> None:
    with _clients_lock:
        _clients.append(ws)


def remove_ws_client(ws: WebSocket) -> None:
    with _clients_lock:
        if ws in _clients:
            _clients.remove(ws)


def start_worker() -> None:
    global _queue, _worker_loop, _worker_task
    loop = asyncio.get_running_loop()
    with _lock:
        if _worker_task and not _worker_task.done() and _worker_loop is loop:
            return
        if _worker_task and not _worker_task.done():
            _worker_task.cancel()
        _queue = asyncio.Queue()
        _worker_loop = loop
        _worker_task = loop.create_task(_worker(_queue))
        for task_id in _recover_incomplete_tasks():
            _enqueue_task_id(task_id)


async def shutdown() -> None:
    global _queue, _worker_loop, _worker_task
    task = _worker_task
    _queue = None
    _worker_loop = None
    _worker_task = None
    _cancelled.clear()
    _queued_task_ids.clear()
    with _clients_lock:
        _clients.clear()
    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def submit(
    req: GenerateRequest,
    task_type: str = "single",
    project_id: str | None = None,
    segment_id: str | None = None,
    *,
    longform_task_id: str | None = None,
    longform_segment_index: int | None = None,
    longform_segment_count: int | None = None,
) -> str:
    start_worker()
    task = GenerationTask(
        task_type=task_type,
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        project_id=project_id,
        segment_id=segment_id,
        longform_task_id=longform_task_id,
        longform_segment_index=longform_segment_index,
        longform_segment_count=longform_segment_count,
        input_text=req.text,
        status=TaskStatus.queued,
        parameters=_parameters_with_idempotency_marker(req),
    )
    _save(task)
    _enqueue_task_id(task.task_id)
    await _broadcast(task)
    return task.task_id


def add_completed_longform_export(
    longform_task: LongformTask,
    export_record: ExportRecord,
    *,
    duration_ms: int | None = None,
    generation_time_ms: int | None = None,
) -> GenerationTask:
    parameters = dict(longform_task.parameters)
    parameters.update(
        {
            "longform_task_id": longform_task.longform_task_id,
            "longform_segment_count": len(longform_task.segments),
            "longform_export_id": export_record.export_id,
            "source_result_ids": longform_task.result_ids,
        }
    )
    task = GenerationTask(
        task_type="export",
        engine_id=longform_task.engine_id,
        voice_id=longform_task.voice_id,
        longform_task_id=longform_task.longform_task_id,
        longform_segment_count=len(longform_task.segments),
        longform_export_id=export_record.export_id,
        input_text=longform_task.input_text,
        status=TaskStatus.success,
        progress=1.0,
        result_audio_id=export_record.export_id,
        result_duration_ms=duration_ms,
        generation_time_ms=generation_time_ms,
        parameters=parameters,
        started_at=longform_task.started_at,
        completed_at=now_iso(),
    )
    voice = voice_store.get_voice(longform_task.voice_id) if longform_task.voice_id else None
    hist = history_store.add(
        HistoryItem(
            task_id=task.task_id,
            engine_id=longform_task.engine_id,
            voice_id=longform_task.voice_id,
            voice_name=voice.name if voice else None,
            longform_task_id=longform_task.longform_task_id,
            longform_segment_count=len(longform_task.segments),
            longform_export_id=export_record.export_id,
            input_text=longform_task.input_text,
            output_audio_id=export_record.export_id,
            output_path=export_record.path,
            duration_ms=duration_ms,
            generation_time_ms=generation_time_ms,
            parameter_snapshot=parameters,
        )
    )
    task.result_id = hist.result_id
    saved = _save(task)
    schedule_auto_verification(saved.task_id)
    return saved


def add_completed_longform_segment(
    longform_task: LongformTask,
    segment: LongformSegmentTask,
) -> GenerationTask | None:
    if not segment.result_id:
        return None
    hist = history_store.get(segment.result_id)
    if not hist:
        return None
    parameters = dict(hist.parameter_snapshot or {})
    task = GenerationTask(
        task_id=segment.task_id or hist.task_id,
        task_type="segment",
        engine_id=longform_task.engine_id,
        voice_id=longform_task.voice_id,
        longform_task_id=longform_task.longform_task_id,
        longform_segment_index=segment.index,
        longform_segment_count=len(longform_task.segments),
        input_text=segment.text,
        status=TaskStatus.success,
        progress=1.0,
        result_audio_id=hist.output_audio_id,
        result_id=hist.result_id,
        result_duration_ms=segment.duration_ms or hist.duration_ms,
        generation_time_ms=hist.generation_time_ms,
        verification=hist.verification or segment.verification,
        verification_error=hist.verification_error,
        parameters=parameters,
        completed_at=hist.created_at,
    )
    hist.longform_task_id = longform_task.longform_task_id
    hist.longform_segment_index = segment.index
    hist.longform_segment_count = len(longform_task.segments)
    if segment.verification:
        hist.verification = segment.verification
        hist.verification_error = None
    history_store.add(hist)
    return _save(task)


async def submit_project(project: Project) -> list[str]:
    task_ids = []
    for seg in project.segments:
        if not seg.text.strip() or seg.locked:
            continue
        role = next((r for r in project.roles if r.role_id == seg.role_id), None)
        req = _request_from_segment(project, seg, role)
        seg.status = SegmentStatus.queued
        task_ids.append(await submit(req, task_type="segment", project_id=project.project_id, segment_id=seg.segment_id))
    project_store.save_project(project)
    return task_ids


def _request_from_segment(project: Project, seg: ScriptSegment, role) -> GenerateRequest:
    values = GenerateRequest(
        text=seg.text,
        engine_id=seg.engine_id or (role.default_engine_id if role else None) or project.default_engine_id or "indextts-v2",
        voice_id=seg.voice_id or (role.default_voice_id if role else None),
        language=seg.language or (role.default_language if role else "zh"),
        emotion=seg.emotion or (role.default_emotion if role else None),
        speed=seg.speed or (role.default_speed if role else 1.0),
    ).model_dump()
    merged_params = {
        **project.parameters,
        **(role.default_parameters if role else {}),
        **seg.parameters,
    }
    for key, value in merged_params.items():
        if value is not None:
            values[key] = value
    values.update(
        {
            "text": seg.text,
            "engine_id": seg.engine_id or values.get("engine_id") or project.default_engine_id or "indextts-v2",
            "voice_id": seg.voice_id or values.get("voice_id"),
            "language": seg.language or values.get("language") or "zh",
            "emotion": seg.emotion or values.get("emotion"),
            "speed": seg.speed or values.get("speed") or 1.0,
        }
    )
    return GenerateRequest(**values)


def cancel_task(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        return {"task_id": task_id, "status": "not_found"}
    _cancelled.add(task_id)
    if _task_is_active(task.status):
        task.status = TaskStatus.cancelled
        task.completed_at = now_iso()
        task.error_message = "已取消"
        _save_data = task.model_dump()
        _save_data["cancel_requested"] = True
        db.upsert("tasks", task.task_id, _save_data)
    return {"task_id": task_id, "status": task.status.value}


def delete_task(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        return {"task_id": task_id, "status": "not_found"}
    if _task_is_active(task.status):
        return {"task_id": task_id, "status": "active_task"}
    if task.result_id:
        history_store.delete(task.result_id)
    db.delete_one("tasks", "task_id", task_id)
    _cancelled.discard(task_id)
    return {"task_id": task_id, "status": "deleted"}


async def retry_task(task_id: str) -> str:
    old = get_task(task_id)
    if not old:
        raise ValueError("Task not found")
    return await submit(
        GenerateRequest(**old.parameters),
        old.task_type,
        old.project_id,
        old.segment_id,
        longform_task_id=old.longform_task_id,
        longform_segment_index=old.longform_segment_index,
        longform_segment_count=old.longform_segment_count,
    )


async def _worker(queue: asyncio.Queue[str]) -> None:
    while True:
        task_id = await queue.get()
        _queued_task_ids.discard(task_id)
        task = get_task(task_id)
        if not task:
            continue
        if _task_is_protected_by_state(task):
            continue
        if task.status in [TaskStatus.cancelled, "cancelled"]:
            continue
        await _process(task)


def _enqueue_task_id(task_id: str) -> None:
    if _queue is None or task_id in _queued_task_ids:
        return
    data = db.get_one("tasks", "task_id", task_id)
    if data and (_task_status_is_terminal(data.get("status")) or data.get("cancel_requested")):
        return
    _queue.put_nowait(task_id)
    _queued_task_ids.add(task_id)


def _recover_incomplete_tasks() -> list[str]:
    task_ids: list[str] = []
    for row in db.list_all("tasks", "created_at", False, limit=-1):
        task = _reconcile_stale_task(row)
        if row.get("cancel_requested"):
            continue
        if task.status in _TERMINAL_STATUSES or task.status not in _RECOVERABLE_STATUSES:
            continue
        if _is_mimo_tts(task.engine_id) and task.status in {TaskStatus.running, TaskStatus.postprocessing, TaskStatus.retrying}:
            marker = task.parameters.get("provider_request_id") or task.parameters.get("idempotency_marker")
            if not marker:
                task.status = TaskStatus.failed
                task.progress = min(task.progress, 0.99)
                task.completed_at = now_iso()
                task.error_message = "云端任务缺少幂等标记，服务重启后未自动重放。请确认云端状态后重新提交。"
                _save(task)
                continue
        if task.status != TaskStatus.queued or task.started_at or task.progress:
            previous_status = task.status
            task.status = TaskStatus.queued
            task.progress = 0.0
            task.started_at = None
            task.completed_at = None
            if previous_status in {TaskStatus.running, TaskStatus.postprocessing, TaskStatus.retrying}:
                task.error_message = "服务重启后已重新排队。"
            _save(task)
        task_ids.append(task.task_id)
    return task_ids


def _resolve_reference(req: GenerateRequest) -> str | None:
    if req.reference_audio_path and Path(req.reference_audio_path).exists():
        return req.reference_audio_path
    return voice_store.reference_path(req.voice_id)


def _kwargs(req: GenerateRequest, output_path: str) -> dict:
    ref = _resolve_reference(req)
    voice = voice_store.get_voice(req.voice_id) if req.voice_id else None
    ref_text = req.ref_text or (voice.reference_text if voice else None)
    if req.engine_id == "omnivoice" and ref and ref_text is None:
        # Avoid OmniVoice's on-the-fly Whisper auto-transcription in isolated jobs.
        # Missing transcripts should not turn a short TTS request into a 10-minute ASR timeout.
        ref_text = ""
    if req.engine_id == "indextts-v2" and not ref:
        raise AppException(400, "REFERENCE_AUDIO_REQUIRED", "IndexTTS v2 需要参考音频")
    if req.engine_id in {"f5-tts", "cosyvoice-zero-shot"}:
        if not ref:
            raise AppException(400, "REFERENCE_AUDIO_REQUIRED", "该引擎需要参考音频")
        if not (ref_text or "").strip():
            raise AppException(400, "REFERENCE_TEXT_REQUIRED", "该引擎需要参考台词")
    if engine_request_builder.is_mimo_tts_request(req.engine_id):
        return engine_request_builder.build_mimo_tts_single_kwargs(
            req,
            output_path,
            reference_audio_path=ref,
            idempotency_marker=_mimo_idempotency_marker(req),
        )
    model_dir = str(settings_store.model_path(req.engine_id))
    if req.engine_id in {"emotivoice", "cosyvoice-sft"}:
        return engine_request_builder.build_preset_voice_single_kwargs(req, output_path)
    if req.engine_id == "f5-tts":
        return engine_request_builder.build_f5_tts_single_kwargs(
            req,
            output_path,
            reference_audio=ref,
            ref_text=ref_text,
        )
    if req.engine_id == "cosyvoice-zero-shot":
        return engine_request_builder.build_cosyvoice_zero_shot_single_kwargs(
            req,
            output_path,
            reference_audio=ref,
            ref_text=ref_text,
        )
    if req.engine_id == "indextts-v2":
        return engine_request_builder.build_indextts_v2_single_kwargs(
            req,
            output_path,
            reference_audio=ref,
            model_dir=model_dir,
        )
    if req.engine_id == "omnivoice":
        return engine_request_builder.build_omnivoice_single_kwargs(
            req,
            output_path,
            reference_audio=ref,
            ref_text=ref_text,
            model_dir=model_dir,
        )
    raise ValueError(f"Unsupported engine: {req.engine_id}")


def _postprocess_audio(task: GenerationTask, req: GenerateRequest, result: dict, audio_id: str) -> Path:
    """音频后处理：格式转换。纯函数，不碰状态。"""
    final_path = Path(result["output_path"])
    if req.output_format != "wav":
        converted = settings_store.output_dir() / f"{audio_id}.{req.output_format}"
        final_path = audio_tools.copy_or_convert(final_path, converted, req.output_format)
    if not final_path.exists() or final_path.stat().st_size <= 0:
        raise RuntimeError(f"生成完成但结果音频不存在：{final_path}")
    return final_path


def _save_history(task: GenerationTask, req: GenerateRequest, final_path: Path, audio_id: str, result: dict) -> HistoryItem:
    """写入历史记录。纯函数，不碰 task 状态。"""
    voice = voice_store.get_voice(req.voice_id) if req.voice_id else None
    hist = history_store.add(HistoryItem(
        task_id=task.task_id,
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        voice_name=voice.name if voice else None,
        project_id=task.project_id,
        segment_id=task.segment_id,
        longform_task_id=task.longform_task_id,
        longform_segment_index=task.longform_segment_index,
        longform_segment_count=task.longform_segment_count,
        input_text=req.text,
        output_audio_id=audio_id,
        output_path=str(final_path),
        duration_ms=result.get("duration_ms"),
        generation_time_ms=result.get("generation_time_ms"),
        parameter_snapshot=task.parameters,
    ))
    return hist


def _update_project_segment(task: GenerationTask, audio_id: str | None, hist_result_id: str | None, status: SegmentStatus, error: str | None = None) -> None:
    """更新项目段落状态。纯函数，只做 IO。"""
    if task.project_id and task.segment_id:
        project_store.update_segment_result(task.project_id, task.segment_id, audio_id, hist_result_id, status, error)


async def _update_status(task: GenerationTask, **kwargs) -> None:
    """唯一状态写入口：写 DB + 广播。不做任何业务逻辑。"""
    for key, value in kwargs.items():
        if value is not None and hasattr(task, key):
            setattr(task, key, value)
    _save(task)
    await _broadcast(task)


def _update_status_sync(task: GenerationTask, **kwargs) -> None:
    """同步版状态写入（用于 progress_tick 等线程内调用）。只写 DB，不广播。"""
    for key, value in kwargs.items():
        if value is not None and hasattr(task, key):
            setattr(task, key, value)
    _save(task)
    _broadcast_from_thread(task)


def decide_task_state(task: GenerationTask, *, engine_result: dict | None = None, engine_error: Exception | None = None, cancelled: bool = False) -> tuple[TaskStatus | None, str | None]:
    """统一状态决策：把各种结果翻译成状态。只返回决策，不写状态。
    
    返回 (status, error_message)。status 为 None 表示不改状态（交给 DB 同步）。
    """
    if cancelled:
        return TaskStatus.cancelled, "cancelled by user"
    if engine_error:
        if _task_is_protected_by_state(task):
            return None, None
        return TaskStatus.failed, str(engine_error)
    if engine_result:
        if _task_is_protected_by_state(task):
            return None, None
        return TaskStatus.postprocessing, None
    return None, None


_RAMP_SECONDS = {
    "omnivoice": 300.0,
    "indextts-v2": 180.0,
    "emotivoice": 180.0,
    "f5-tts": 240.0,
    "cosyvoice-sft": 420.0,
    "cosyvoice-zero-shot": 420.0,
    "mimo-v2.5-tts-preset": 120.0,
    "mimo-v2.5-tts-voicedesign": 120.0,
    "mimo-v2.5-tts-voiceclone": 120.0,
}


async def _execute_engine(task: GenerationTask, engine_id: str, kwargs: dict, wav_path: Path) -> tuple[dict, dict]:
    """纯引擎调用。返回 (result, progress_state)。不碰任务状态。"""
    progress_state = {"last_sent_at": 0.0, "last_value": 0.24}
    ramp_seconds = _RAMP_SECONDS.get(engine_id, 180.0)

    def progress_tick(elapsed_seconds: float) -> None:
        next_value = min(0.92, 0.24 + min(1.0, elapsed_seconds / ramp_seconds) * 0.66)
        now = time.monotonic()
        if next_value <= progress_state["last_value"] + 0.01 and now - progress_state["last_sent_at"] < 2.0:
            return
        if _task_is_protected_by_state(task):
            return
        progress_state["last_value"] = next_value
        progress_state["last_sent_at"] = now
        _update_status_sync(task, progress=next_value)

    timeout_seconds = _timeout_seconds_for(engine_id)
    result = await asyncio.to_thread(
        engine_registry.run_isolated,
        engine_id,
        kwargs,
        timeout_seconds,
        lambda: task.task_id in _cancelled,
        progress_tick,
    )
    return result, progress_state


async def _process(task: GenerationTask) -> None:
    """任务执行流水线：规范化 → 引擎执行 → 后处理 → 资产登记 → 收口。"""
    if _task_is_protected_by_state(task):
        _sync_task_status_from_db(task)
        return

    # Stage 1: 任务规范化
    await _update_status(task, status=TaskStatus.running, started_at=now_iso(), progress=0.12)
    try:
        req = GenerateRequest(**task.parameters)
        engine_registry.ensure_loaded(req.engine_id)
        await _update_status(task, progress=0.24)
        settings_store.ensure_directories()
        audio_id = task.task_id
        wav_path = settings_store.output_dir() / f"{audio_id}.wav"

        # Stage 2: 引擎执行
        result, progress_state = await _execute_engine(task, req.engine_id, _kwargs(req, str(wav_path)), wav_path)
        new_status, error_msg = decide_task_state(task, engine_result=result, cancelled=task.task_id in _cancelled)
        if new_status is None:
            _sync_task_status_from_db(task)
            return
        if new_status == TaskStatus.cancelled:
            await _update_status(task, status=new_status, error_message=error_msg)
            return

        # Stage 3: 音频后处理
        await _update_status(task, status=TaskStatus.postprocessing, progress=0.96)
        final_path = _postprocess_audio(task, req, result, audio_id)

        # Stage 4: 资产登记 + 历史
        await _update_status(task, status=TaskStatus.success, progress=1.0,
                       result_audio_id=audio_id,
                       result_duration_ms=result.get("duration_ms"),
                       generation_time_ms=result.get("generation_time_ms"))
        hist = _save_history(task, req, final_path, audio_id, result)
        task.result_id = hist.result_id
        _update_project_segment(task, audio_id, hist.result_id, SegmentStatus.completed)

    except Exception as exc:
        cancelled = task.task_id in _cancelled or str(exc) == "Generation cancelled"
        new_status, error_msg = decide_task_state(task, engine_error=exc, cancelled=cancelled)
        if new_status is None:
            _sync_task_status_from_db(task)
            return
        await _update_status(task, status=new_status, error_message=error_msg)
        if new_status == TaskStatus.failed:
            _update_project_segment(task, None, None, SegmentStatus.failed, error_msg)

    # 收口：记录完成时间
    await _update_status(task, completed_at=now_iso())
    if task.status == TaskStatus.success and task.result_id and not (task.longform_task_id and task.task_type == "segment"):
        schedule_auto_verification(task.task_id)
