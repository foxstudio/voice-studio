from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from app.models.exceptions import AppException
from typing import Any

from fastapi import WebSocket

from app.engines.registry import build_default_registry
from app.engines.seed_audio.assets import SeedAudioAssetResolver
from app.engines.seed_audio.client import urllib_json_transport
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
from app.services import asr_service, audio_tools, cosyvoice_constraints, custom_reference_store, database as db, emotion_reference, engine_policy, engine_registry, engine_request_builder, history_store, project_store, settings_store, text_verifier, voice_store

_queue: asyncio.Queue[str] | None = None
_worker_task: asyncio.Task[None] | None = None
_worker_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()
_cancelled: set[str] = set()
_queued_task_ids: set[str] = set()
_clients: list[WebSocket] = []
_clients_lock = threading.Lock()
_adapter_registry = build_default_registry()
_seed_audio_transport = urllib_json_transport
_seed_audio_asset_resolver = SeedAudioAssetResolver()
_seed_audio_allow_test_host = False
_shutting_down = False

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


def _is_doubao_tts(engine_id: str) -> bool:
    return engine_policy.is_doubao_tts(engine_id)


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
        return _refresh_seed_audio_verification(task)
    if task.status not in [TaskStatus.running, TaskStatus.postprocessing, TaskStatus.retrying]:
        return _refresh_seed_audio_verification(task)
    stale_after = _timeout_seconds_for(task.engine_id) + 180
    if _elapsed_since(task.started_at) <= stale_after:
        return _refresh_seed_audio_verification(task)
    task.status = TaskStatus.failed
    task.completed_at = now_iso()
    task.error_message = "任务超过模型常规超时窗口，已自动标记为失败。可复用参数重新生成。"
    _cancelled.discard(task.task_id)
    return _refresh_seed_audio_verification(_save(task))


def list_tasks() -> list[GenerationTask]:
    return [_reconcile_stale_task(row) for row in db.list_all("tasks", "created_at", limit=-1)]


def task_summary() -> dict[str, int]:
    _reconcile_active_tasks()
    active_values = tuple(status.value for status in _RECOVERABLE_STATUSES)
    processing_values = tuple(
        status.value for status in _RECOVERABLE_STATUSES if status not in {TaskStatus.pending, TaskStatus.queued}
    )
    waiting_values = (TaskStatus.pending.value, TaskStatus.queued.value)
    placeholders = ", ".join("?" for _ in active_values)
    processing_placeholders = ", ".join("?" for _ in processing_values)
    waiting_placeholders = ", ".join("?" for _ in waiting_values)
    with db.conn() as connection:
        row = connection.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ({placeholders}) THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status IN ({processing_placeholders}) THEN 1 ELSE 0 END) AS processing,
                SUM(CASE WHEN status IN ({waiting_placeholders}) THEN 1 ELSE 0 END) AS waiting,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN status IN (?, ?) THEN 1 ELSE 0 END) AS failed
            FROM tasks
            """,
            (
                *active_values,
                *processing_values,
                *waiting_values,
                TaskStatus.success.value,
                TaskStatus.failed.value,
                TaskStatus.cancelled.value,
            ),
        ).fetchone()
    return {
        "all": int(row["total"] or 0),
        "active": int(row["active"] or 0),
        "processing": int(row["processing"] or 0),
        "waiting": int(row["waiting"] or 0),
        "success": int(row["success"] or 0),
        "failed": int(row["failed"] or 0),
    }


def list_tasks_page(
    *,
    offset: int = 0,
    limit: int = 12,
    status_filter: str = "all",
    engine_ids: list[str] | None = None,
    voice_ids: list[str] | None = None,
    query: str = "",
    created_after: str | None = None,
    sort_by: str = "latest",
) -> tuple[list[GenerationTask], int]:
    _reconcile_active_tasks()
    where: list[str] = []
    params: list[Any] = []

    if status_filter == "active":
        values = [status.value for status in _RECOVERABLE_STATUSES]
        where.append(f"status IN ({', '.join('?' for _ in values)})")
        params.extend(values)
    elif status_filter == "success":
        where.append("status = ?")
        params.append(TaskStatus.success.value)
    elif status_filter == "failed":
        where.append("status IN (?, ?)")
        params.extend([TaskStatus.failed.value, TaskStatus.cancelled.value])

    if engine_ids:
        where.append(f"json_extract(data, '$.engine_id') IN ({', '.join('?' for _ in engine_ids)})")
        params.extend(engine_ids)
    if created_after:
        where.append("created_at >= ?")
        params.append(created_after)

    normalized_query = query.strip().lower()
    if normalized_query:
        like = f"%{normalized_query}%"
        query_parts = [
            "lower(json_extract(data, '$.input_text')) LIKE ?",
            "lower(json_extract(data, '$.engine_id')) LIKE ?",
            "lower(status) LIKE ?",
        ]
        query_params: list[Any] = [like, like, like]
        if voice_ids:
            query_parts.append(f"json_extract(data, '$.voice_id') IN ({', '.join('?' for _ in voice_ids)})")
            query_params.extend(voice_ids)
        where.append(f"({' OR '.join(query_parts)})")
        params.extend(query_params)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    active_values = [status.value for status in _RECOVERABLE_STATUSES]
    waiting_values = [TaskStatus.pending.value, TaskStatus.queued.value]
    processing_values = [status.value for status in _RECOVERABLE_STATUSES if status not in {TaskStatus.pending, TaskStatus.queued}]
    rank_sql = (
        f"CASE WHEN status IN ({', '.join('?' for _ in processing_values)}) THEN 0 "
        f"WHEN status IN ({', '.join('?' for _ in waiting_values)}) THEN 1 ELSE 2 END"
    )
    order_params: list[Any] = [*processing_values, *waiting_values]
    if sort_by == "oldest":
        order_sql = f"{rank_sql}, created_at ASC, task_id ASC"
    elif sort_by == "duration_desc":
        order_sql = f"{rank_sql}, COALESCE(CAST(json_extract(data, '$.result_duration_ms') AS INTEGER), 0) DESC, created_at DESC"
    else:
        order_sql = f"{rank_sql}, created_at DESC, task_id DESC"

    with db.conn() as connection:
        count_row = connection.execute(f"SELECT COUNT(*) AS total FROM tasks {where_sql}", params).fetchone()
        rows = connection.execute(
            f"SELECT data FROM tasks {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            (*params, *order_params, limit, offset),
        ).fetchall()
    return ([_reconcile_stale_task(json.loads(row["data"])) for row in rows], int(count_row["total"] or 0))


def task_download_sequences(tasks: list[GenerationTask]) -> dict[str, int]:
    targets = [task for task in tasks if task.result_id]
    if not targets:
        return {}
    sequences: dict[str, int] = {}
    timestamp_sql = "COALESCE(json_extract(data, '$.completed_at'), created_at)"
    with db.conn() as connection:
        for task in targets:
            timestamp = task.completed_at or task.created_at
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS sequence
                FROM tasks
                WHERE json_extract(data, '$.result_id') IS NOT NULL
                  AND date({timestamp_sql}, 'localtime') = date(?, 'localtime')
                  AND (
                    {timestamp_sql} < ?
                    OR ({timestamp_sql} = ? AND task_id <= ?)
                  )
                """,
                (timestamp, timestamp, timestamp, task.task_id),
            ).fetchone()
            sequences[task.task_id] = max(1, int(row["sequence"] or 0))
    return sequences


def _reconcile_active_tasks() -> None:
    values = [status.value for status in _RECOVERABLE_STATUSES]
    with db.conn() as connection:
        rows = connection.execute(
            f"SELECT data FROM tasks WHERE status IN ({', '.join('?' for _ in values)})",
            values,
        ).fetchall()
    for row in rows:
        _reconcile_stale_task(json.loads(row["data"]))


def get_task(task_id: str) -> GenerationTask | None:
    data = db.get_one("tasks", "task_id", task_id)
    return _reconcile_stale_task(data) if data else None


def _verification_language(task: GenerationTask) -> str:
    value = task.parameters.get("language")
    return value if value in {"auto", "zh", "en"} else "zh"


def verification_expected_text_for_task(task: GenerationTask) -> str:
    parameters = task.parameters.get("engine_parameters")
    filter_parenthetical_content = isinstance(parameters, dict) and bool(
        parameters.get("max_length_to_filter_parenthesis")
    )
    return text_verifier.verification_expected_text(
        task.input_text,
        engine_id=task.engine_id,
        filter_parenthetical_content=filter_parenthetical_content,
    )


def verification_expected_text_for_result(result_id: str, *, input_text: str, engine_id: str) -> str:
    for row in db.list_all("tasks", "created_at", False, limit=-1):
        task = GenerationTask(**row)
        if task.result_id == result_id:
            return verification_expected_text_for_task(task)
    return text_verifier.verification_expected_text(input_text, engine_id=engine_id)


def _save_history_verification(result_id: str | None, report: TTSVerificationResponse | None, error: str | None = None) -> None:
    if not result_id:
        return
    item = history_store.get(result_id)
    if not item:
        return
    item.verification = report
    item.verification_error = error
    history_store.add(item)


def _refresh_seed_audio_verification(task: GenerationTask) -> GenerationTask:
    """Repair legacy Seed Audio coverage using the stored ASR text, without another ASR call."""
    report = task.verification
    if task.engine_id != text_verifier.SEED_AUDIO_ENGINE_ID or report is None:
        return task
    expected = verification_expected_text_for_task(task)
    if report.expected_text == expected:
        return task
    if expected:
        refreshed = text_verifier.verify_transcript(
            expected_text=expected,
            transcript_text=report.transcript_text,
            result_id=task.result_id,
            transcription_id=report.transcription_id,
            asr_engine_id=report.asr_engine_id,
        )
    else:
        refreshed = text_verifier.skipped_non_speech_report(
            original_prompt=task.input_text,
            result_id=task.result_id,
            transcription_id=report.transcription_id,
            asr_engine_id=report.asr_engine_id,
        )
    task.verification = refreshed
    task.verification_error = None
    _save_history_verification(task.result_id, refreshed)
    return _save(task)


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
    expected_text = verification_expected_text_for_task(task)
    if task.engine_id == text_verifier.SEED_AUDIO_ENGINE_ID and not expected_text:
        return text_verifier.skipped_non_speech_report(
            original_prompt=task.input_text,
            result_id=task.result_id,
            asr_engine_id=asr_engine_id,
        )
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
        expected_text=expected_text,
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
    global _queue, _worker_loop, _worker_task, _shutting_down
    _shutting_down = False
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
    global _queue, _worker_loop, _worker_task, _shutting_down
    _shutting_down = True
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
    try:
        emotion_reference.resolve_generate_request(req)
    except emotion_reference.EmotionReferenceError as exc:
        raise AppException(400, exc.code, exc.message) from exc
    if engine_policy.is_single_generation_only(req.engine_id) and task_type != "single":
        raise AppException(400, "SINGLE_GENERATION_ONLY", "Seed Audio 1.0 暂只支持单次生成")
    if req.engine_id == "cosyvoice-zero-shot":
        reference_audio = _resolve_reference(req)
        if not reference_audio:
            raise AppException(400, "REFERENCE_AUDIO_REQUIRED", "该引擎需要参考音频")
        voice = voice_store.get_voice(req.voice_id) if req.voice_id else None
        reference_text = req.ref_text or (voice.reference_text if voice else None)
        if not (reference_text or "").strip():
            raise AppException(400, "REFERENCE_TEXT_REQUIRED", "该引擎需要参考台词")
        try:
            cosyvoice_constraints.validate_zero_shot_reference_audio(reference_audio)
        except ValueError as exc:
            code, _, message = str(exc).partition(": ")
            raise AppException(400, code, message or "CosyVoice Zero-Shot 参考音频不符合官方要求") from exc
    start_worker()
    request_parameters = _parameters_with_idempotency_marker(req)
    task = GenerationTask(
        task_type=task_type,
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        project_id=project_id or req.project_id,
        segment_id=segment_id or req.localized_subtitle_id or req.cue_id or req.segment_id,
        localized_subtitle_id=req.localized_subtitle_id,
        cue_id=req.cue_id,
        bind_to_video_localization=req.bind_to_video_localization,
        longform_task_id=longform_task_id,
        longform_segment_index=longform_segment_index,
        longform_segment_count=longform_segment_count,
        input_text=req.text,
        status=TaskStatus.queued,
        parameters=request_parameters,
    )
    task.generation_id = task.task_id
    task.parameters["generation_id"] = task.generation_id
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
    managed_paths = custom_reference_store.managed_paths_in(task.parameters)
    if task.result_id:
        history = history_store.get(task.result_id)
        if history:
            managed_paths.update(custom_reference_store.managed_paths_in(history.parameter_snapshot))
        history_store.delete(task.result_id)
    db.delete_one("tasks", "task_id", task_id)
    for path in managed_paths:
        custom_reference_store.delete_if_unreferenced(path)
    _cancelled.discard(task_id)
    return {"task_id": task_id, "status": "deleted"}


async def retry_task(task_id: str, *, confirm_cloud_replay: bool = False) -> str:
    old = get_task(task_id)
    if not old:
        raise ValueError("Task not found")
    if engine_policy.is_single_generation_only(old.engine_id):
        if _task_is_active(old.status):
            raise AppException(409, "TASK_ACTIVE", "Seed Audio 云端任务仍在执行，不能重复提交")
        if (old.status == TaskStatus.cancelled or old.provider_state_uncertain) and not confirm_cloud_replay:
            raise AppException(
                409,
                "CLOUD_REPLAY_CONFIRM_REQUIRED",
                "原云端请求可能仍已产生费用；确认云端状态后才能重新生成",
            )
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
        if engine_policy.requires_manual_replay_after_start(task.engine_id) and task.status in {
            TaskStatus.running,
            TaskStatus.postprocessing,
            TaskStatus.retrying,
        }:
            task.status = TaskStatus.failed
            task.progress = min(task.progress, 0.99)
            task.completed_at = now_iso()
            task.provider_state_uncertain = True
            task.error_message = "云端音频生成状态不明确，服务重启后不会自动重放，以免重复计费。请核对云端状态后重新提交。"
            _save(task)
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
    if req.reference_audio_path:
        if Path(req.reference_audio_path).exists():
            return req.reference_audio_path
        raise AppException(400, "REFERENCE_AUDIO_NOT_FOUND", "指定的参考音频不存在")
    return voice_store.reference_path(req.voice_id)


def _kwargs(req: GenerateRequest, output_path: str) -> dict:
    try:
        emotion_reference.validate_generate_request(req)
    except emotion_reference.EmotionReferenceError as exc:
        raise AppException(400, exc.code, exc.message) from exc
    voice = voice_store.get_voice(req.voice_id) if req.voice_id else None
    if engine_request_builder.is_doubao_tts_request(req.engine_id):
        return engine_request_builder.build_doubao_tts_single_kwargs(req, output_path, voice=voice)

    ref = _resolve_reference(req)
    ref_text = req.ref_text or (voice.reference_text if voice else None)
    if req.engine_id == "omnivoice" and ref and ref_text is None:
        # Avoid OmniVoice's on-the-fly Whisper auto-transcription in isolated jobs.
        # Missing transcripts should not turn a short TTS request into a 10-minute ASR timeout.
        ref_text = ""
    if req.engine_id in {"indextts-v2", "confucius4-mlx-int8"} and not ref:
        message = "Confucius4-TTS 需要参考音频" if req.engine_id == "confucius4-mlx-int8" else "IndexTTS v2 需要参考音频"
        raise AppException(400, "REFERENCE_AUDIO_REQUIRED", message)
    if req.engine_id == "indextts-v2" and req.emotion_mode.value == "emotion_text":
        raise AppException(400, "INDEXTTS_EMOTION_TEXT_UNSUPPORTED", "IndexTTS 当前只支持选择内置情绪或跟随参考音色，不支持自由文字情绪指令")
    if req.engine_id in {"f5-tts", "cosyvoice-zero-shot"}:
        if not ref:
            raise AppException(400, "REFERENCE_AUDIO_REQUIRED", "该引擎需要参考音频")
        if not (ref_text or "").strip():
            raise AppException(400, "REFERENCE_TEXT_REQUIRED", "该引擎需要参考台词")
    if req.engine_id == "cosyvoice-zero-shot":
        try:
            cosyvoice_constraints.validate_zero_shot_reference_audio(ref)
        except ValueError as exc:
            code, _, message = str(exc).partition(": ")
            raise AppException(400, code, message or "CosyVoice Zero-Shot 参考音频不符合官方要求") from exc
    if req.engine_id == "qwen3-tts-mlx-0.6b" and ref and not (ref_text or "").strip():
        raise AppException(400, "REFERENCE_TEXT_REQUIRED", "Qwen3 参考音色需要参考音频对应的准确台词")
    if engine_request_builder.is_mimo_tts_request(req.engine_id):
        return engine_request_builder.build_mimo_tts_single_kwargs(
            req,
            output_path,
            reference_audio_path=ref,
            idempotency_marker=_mimo_idempotency_marker(req),
        )
    model_dir = str(settings_store.model_path(req.engine_id))
    if req.engine_id == "emotivoice":
        return engine_request_builder.build_emotivoice_single_kwargs(req, output_path)
    if req.engine_id == "cosyvoice-sft":
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
    if req.engine_id == "confucius4-mlx-int8":
        return engine_request_builder.build_confucius4_mlx_single_kwargs(
            req,
            output_path,
            reference_audio=ref,
            model_dir=model_dir,
        )
    if req.engine_id == "qwen3-tts-mlx-0.6b":
        return engine_request_builder.build_qwen3_tts_single_kwargs(
            req,
            output_path,
            reference_audio=ref,
            ref_text=ref_text,
        )
    if req.engine_id == "indextts-v2":
        try:
            emotion_reference_audio = emotion_reference.resolve_generate_request(req)
        except emotion_reference.EmotionReferenceError as exc:
            raise AppException(400, exc.code, exc.message) from exc
        return engine_request_builder.build_indextts_v2_single_kwargs(
            req,
            output_path,
            reference_audio=ref,
            emotion_reference_audio=emotion_reference_audio,
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
    if (
        _adapter_registry.get(req.engine_id) is None
        and req.output_format != "wav"
        and final_path.suffix.lower() != f".{req.output_format}"
    ):
        converted = settings_store.output_dir() / f"{audio_id}.{req.output_format}"
        final_path = audio_tools.copy_or_convert(final_path, converted, req.output_format)
    if not final_path.exists() or final_path.stat().st_size <= 0:
        raise RuntimeError(f"生成完成但结果音频不存在：{final_path}")
    return final_path


def _direct_cloud_output_format(req: GenerateRequest) -> str:
    """Return a provider-native format only when that exact engine supports it."""
    if req.engine_id in {"doubao-tts-preset", "doubao-tts-voiceclone"} and req.output_format in {"wav", "mp3", "pcm", "ogg_opus"}:
        return req.output_format
    return "wav"


def _save_history(task: GenerationTask, req: GenerateRequest, final_path: Path, audio_id: str, result: dict) -> HistoryItem:
    """写入历史记录。纯函数，不碰 task 状态。"""
    voice = voice_store.get_voice(req.voice_id) if req.voice_id else None
    hist = history_store.add(HistoryItem(
        task_id=task.task_id,
        generation_id=task.generation_id or task.task_id,
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        voice_name=voice.name if voice else None,
        project_id=task.project_id,
        segment_id=task.segment_id,
        localized_subtitle_id=task.localized_subtitle_id,
        cue_id=task.cue_id,
        bind_to_video_localization=task.bind_to_video_localization,
        longform_task_id=task.longform_task_id,
        longform_segment_index=task.longform_segment_index,
        longform_segment_count=task.longform_segment_count,
        input_text=req.text,
        output_audio_id=audio_id,
        output_path=str(final_path),
        duration_ms=result.get("duration_ms"),
        generation_time_ms=result.get("generation_time_ms"),
        provider_request_id=result.get("provider_request_id"),
        provider_log_id=result.get("provider_log_id"),
        original_duration_ms=result.get("original_duration_ms"),
        subtitle=result.get("subtitle"),
        response_source=result.get("response_source"),
        parameter_snapshot=task.parameters,
    ))
    return hist


def _update_project_segment(task: GenerationTask, audio_id: str | None, hist_result_id: str | None, status: SegmentStatus, error: str | None = None) -> None:
    """更新项目段落状态。纯函数，只做 IO。"""
    if task.project_id and task.segment_id:
        project_store.update_segment_result(task.project_id, task.segment_id, audio_id, hist_result_id, status, error)


def _sync_video_localization_tts_result(task: GenerationTask, hist: HistoryItem) -> None:
    generation_id = str(task.generation_id or task.parameters.get("generation_id") or "")
    target_id = task.localized_subtitle_id or task.cue_id
    if (
        task.parameters.get("source") != "video_localization"
        or not task.bind_to_video_localization
        or not task.project_id
        or not target_id
        or not generation_id
        or generation_id != task.task_id
        or hist.generation_id != generation_id
        or hist.project_id != task.project_id
        or hist.localized_subtitle_id != task.localized_subtitle_id
        or hist.cue_id != task.cue_id
        or not hist.bind_to_video_localization
    ):
        return
    if not hist.output_path or not hist.result_id:
        return
    from app.domains.video_localization import service as video_localization_service

    video_localization_service.sync_single_tts_result(
        task.project_id,
        target_id,
        result_id=hist.result_id,
        output_path=hist.output_path,
        duration_ms=hist.duration_ms,
        task_id=task.task_id,
        generation_id=generation_id,
    )


def _try_sync_video_localization_tts_result(task: GenerationTask, hist: HistoryItem) -> None:
    try:
        _sync_video_localization_tts_result(task, hist)
    except Exception as exc:
        task.logs.append(f"视频本土化 cue 回填失败：{exc}")


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
    "doubao-tts-preset": 120.0,
}


async def _execute_engine(task: GenerationTask, req: GenerateRequest, wav_path: Path) -> tuple[dict, dict]:
    """纯引擎调用。返回 (result, progress_state)。不碰任务状态。"""
    progress_state = {"last_sent_at": 0.0, "last_value": 0.24}
    engine_id = req.engine_id
    adapter = _adapter_registry.get(engine_id)
    if adapter is not None:
        settings = settings_store.get()
        prepared_request, asset_summaries = await asyncio.to_thread(
            adapter.resolve_generate_request,
            req,
            asset_resolver=_seed_audio_asset_resolver,
            upload_confirmation_required=settings.doubao_upload_confirm,
        )
        if _task_is_protected_by_state(task):
            raise RuntimeError("Generation cancelled")
        request_id = task.provider_request_id or str(uuid.uuid4())
        await _update_status(
            task,
            provider_request_id=request_id,
            provider_state_uncertain=True,
        )
        result = await asyncio.to_thread(
            adapter.execute,
            req,
            output_dir=settings_store.output_dir(),
            output_name=task.task_id,
            api_key=settings_store.doubao_api_key(),
            base_url=settings.doubao_base_url,
            timeout=_timeout_seconds_for(engine_id),
            transport=_seed_audio_transport,
            prepared_request=prepared_request,
            asset_summaries=asset_summaries,
            request_id=request_id,
            allow_test_host=_seed_audio_allow_test_host,
            cancel_check=lambda: _shutting_down or task.task_id in _cancelled,
        )
        return result, progress_state

    kwargs = _kwargs(req, str(wav_path))
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

    generated_path: Path | None = None
    saved_history: HistoryItem | None = None
    success_persisted = False

    # Stage 1: 任务规范化
    await _update_status(task, status=TaskStatus.running, started_at=now_iso(), progress=0.12)
    try:
        req = GenerateRequest(**task.parameters)
        if _adapter_registry.get(req.engine_id) is None:
            engine_registry.ensure_loaded(req.engine_id)
        await _update_status(task, progress=0.24)
        settings_store.ensure_directories()
        audio_id = task.task_id
        # 豆包 TTS 2.0 原生支持这四种格式；不要为了通用导出层而把
        # PCM/OGG Opus 悄悄转成 WAV，用户选择的格式必须如实交给官方。
        direct_cloud_format = _direct_cloud_output_format(req)
        wav_path = settings_store.output_dir() / f"{audio_id}.{direct_cloud_format}"

        # Stage 2: 引擎执行
        result, progress_state = await _execute_engine(task, req, wav_path)
        generated_path = Path(result["output_path"])
        asset_summaries = result.get("asset_summaries")
        if isinstance(asset_summaries, list):
            task.parameters["asset_summaries"] = asset_summaries
        new_status, error_msg = decide_task_state(task, engine_result=result, cancelled=task.task_id in _cancelled)
        if new_status is None:
            _sync_task_status_from_db(task)
            return
        if new_status == TaskStatus.cancelled:
            _cleanup_seed_orphan(task, generated_path)
            await _update_status(task, status=new_status, error_message=error_msg)
            return

        # Stage 3: 音频后处理
        await _update_status(task, status=TaskStatus.postprocessing, progress=0.96)
        final_path = _postprocess_audio(task, req, result, audio_id)

        # Stage 4: 先写历史，再提交 success 终态，避免成功任务没有结果记录。
        saved_history = _save_history(task, req, final_path, audio_id, result)
        await _update_status(
            task,
            status=TaskStatus.success,
            progress=1.0,
            result_audio_id=audio_id,
            result_id=saved_history.result_id,
            result_duration_ms=result.get("duration_ms"),
            generation_time_ms=result.get("generation_time_ms"),
            provider_request_id=result.get("provider_request_id"),
            provider_log_id=result.get("provider_log_id"),
            provider_state_uncertain=False,
            original_duration_ms=result.get("original_duration_ms"),
            subtitle=result.get("subtitle"),
            response_source=result.get("response_source"),
        )
        success_persisted = True
        _update_project_segment(task, audio_id, saved_history.result_id, SegmentStatus.completed)
        _try_sync_video_localization_tts_result(task, saved_history)

    except Exception as exc:
        if saved_history is not None and not success_persisted:
            if generated_path is not None:
                _cleanup_seed_orphan(task, generated_path)
            history_store.delete(saved_history.result_id)
            generated_path = None
        elif generated_path is not None:
            _cleanup_seed_orphan(task, generated_path)
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


def _cleanup_seed_orphan(task: GenerationTask, path: Path) -> None:
    if not engine_policy.is_single_generation_only(task.engine_id):
        return
    try:
        resolved = path.expanduser().resolve(strict=False)
        output_root = settings_store.output_dir().expanduser().resolve(strict=False)
        if resolved.parent == output_root and resolved.stem == task.task_id:
            resolved.unlink(missing_ok=True)
    except OSError:
        return
