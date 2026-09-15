from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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
from app.services import asr_service, audio_tools, cosyvoice_constraints, custom_reference_store, database as db, emotion_reference, engine_policy, engine_registry, engine_request_builder, execution_plan, generation_task_scheduler, history_store, project_store, reference_audio_integrity, settings_store, text_verifier, video_localization_tts_handoff, video_localization_tts_workflow_store, voice_store

_queue: (
    asyncio.Queue[str]
    | generation_task_scheduler.ProjectFairGenerationQueue
    | None
) = None
_worker_task: asyncio.Task[None] | None = None
_verification_recovery_task: asyncio.Task[None] | None = None
_verification_lock: asyncio.Lock | None = None
_worker_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()
_task_write_lock = threading.RLock()
_cancelled: set[str] = set()
_queued_task_ids: set[str] = set()
_verification_task_ids: set[str] = set()
_clients: list[WebSocket] = []
_clients_lock = threading.Lock()
_adapter_registry = build_default_registry()
_seed_audio_transport = urllib_json_transport
_seed_audio_asset_resolver = SeedAudioAssetResolver()
_seed_audio_allow_test_host = False
_shutting_down = False
logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {TaskStatus.success, TaskStatus.failed, TaskStatus.cancelled}
_TERMINAL_STATUS_VALUES = {s.value for s in _TERMINAL_STATUSES}
_RECOVERABLE_STATUSES = {
    TaskStatus.pending,
    TaskStatus.queued,
    TaskStatus.running,
    TaskStatus.postprocessing,
    TaskStatus.retrying,
}
CLOUD_RESULT_UNKNOWN_MESSAGE = engine_policy.CLOUD_RESULT_UNKNOWN_MESSAGE


def _resolve_execution_plan(engine_id: str, requested_device: str | None = None) -> execution_plan.ExecutionPlan | None:
    if engine_policy.resolve_engine_id(engine_id) not in execution_plan.DEVICE_CONTROLLED_ENGINES:
        return None
    requested = requested_device or settings_store.get().device
    try:
        return execution_plan.resolve(engine_id, requested)
    except execution_plan.ExecutionPlanError as exc:
        raise AppException(409, "DEVICE_UNAVAILABLE", exc.message) from exc


def _execution_plan_for_task(task: GenerationTask) -> execution_plan.ExecutionPlan | None:
    persisted = task.parameters.get("_execution_plan")
    requested = None
    if isinstance(persisted, dict):
        requested = execution_plan.ExecutionPlan.from_dict(persisted).requested_device
    return _resolve_execution_plan(task.engine_id, requested)


def _upsert_task_payload(
    task: GenerationTask,
    payload: dict[str, Any],
) -> None:
    def sync_tts_runtime(connection) -> None:
        video_localization_tts_workflow_store.sync_generation_task_from_connection(
            connection,
            task_id=task.task_id,
            status=task.status.value,
            progress=float(task.progress or 0.0),
            error_message=task.error_message,
            started_at=task.started_at,
            completed_at=task.completed_at,
            result_id=task.result_id,
            projected_at=now_iso(),
        )

    db.upsert(
        "tasks",
        task.task_id,
        payload,
        "updated_at",
        sync_tts_runtime,
    )


def _save(task: GenerationTask) -> GenerationTask:
    with _task_write_lock:
        # Scheduling changes belong to the task store, not a worker's older
        # in-memory request. Status/progress writes must not undo a promotion.
        current = db.get_one("tasks", "task_id", task.task_id)
        if current and "resource_priority" in current.get("parameters", {}):
            task.parameters["resource_priority"] = current["parameters"]["resource_priority"]
        _upsert_task_payload(task, task.model_dump())
    return task


def update_pending_production_priorities(
    project_id: str, *, plan_revision: int, group_priorities: dict[str, str]
) -> list[str]:
    """Update only queued managed tasks from one production plan, in place.

    Running inference is never interrupted. The production plan owns durable
    continuation policy; this updates its existing queue entries without replay.
    """
    priorities = {
        group_id: generation_task_scheduler.parse_priority(priority).name.lower()
        for group_id, priority in group_priorities.items()
    }
    updated: list[str] = []
    with _task_write_lock:
        with db.conn() as connection:
            rows = connection.execute(
                "SELECT data FROM tasks WHERE json_extract(data, '$.project_id') = ? "
                "AND status IN ('pending', 'queued', 'retrying')",
                (project_id,),
            ).fetchall()
        for row in rows:
            task = GenerationTask(**json.loads(row["data"]))
            parameters = task.parameters
            group_id = parameters.get("video_localization_dubbing_group_id")
            if (
                task.status not in {TaskStatus.pending, TaskStatus.queued, TaskStatus.retrying}
                or parameters.get("video_localization_execution_scope") not in {"single_group", "all_remaining"}
                or parameters.get("video_localization_dubbing_plan_revision") != plan_revision
                or group_id not in priorities
            ):
                continue
            if parameters.get("resource_priority", "normal") == priorities[group_id]:
                continue
            # Patch only scheduling metadata. A cancellation/terminal commit
            # between the read and this write must never be resurrected.
            with db.conn() as connection:
                result = connection.execute(
                    "UPDATE tasks SET data = json_set(data, '$.parameters.resource_priority', ?) "
                    "WHERE task_id = ? AND status IN ('pending', 'queued', 'retrying') "
                    "AND COALESCE(json_extract(data, '$.cancel_requested'), 0) = 0",
                    (priorities[group_id], task.task_id),
                )
                if result.rowcount:
                    updated.append(task.task_id)
    return updated


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
    if engine_policy.requires_manual_replay_after_start(task.engine_id):
        task.provider_state_uncertain = True
        task.error_message = CLOUD_RESULT_UNKNOWN_MESSAGE
    else:
        task.error_message = "任务超过模型常规超时窗口，已自动标记为失败。可复用参数重新生成。"
    _cancelled.discard(task.task_id)
    return _refresh_seed_audio_verification(_save(task))


def list_tasks() -> list[GenerationTask]:
    return [_reconcile_stale_task(row) for row in db.list_all("tasks", "created_at", limit=-1)]


def list_project_tasks(project_id: str) -> list[GenerationTask]:
    with db.conn() as connection:
        rows = connection.execute(
            """
            SELECT data
            FROM tasks
            WHERE json_extract(data, '$.project_id') = ?
            ORDER BY created_at DESC
            """,
            (project_id,),
        ).fetchall()
    return [
        _reconcile_stale_task(json.loads(row["data"]))
        for row in rows
    ]


def get_task_by_video_localization_workflow_id(
    workflow_id: str,
) -> GenerationTask | None:
    """Resolve a task when an old workflow missed its registration write."""

    if not workflow_id:
        return None
    with db.conn() as connection:
        row = connection.execute(
            """
            SELECT data
            FROM tasks
            WHERE json_extract(
                data,
                '$.parameters.video_localization_workflow_id'
            ) = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (workflow_id,),
        ).fetchone()
    return _reconcile_stale_task(json.loads(row["data"])) if row else None


def delete_project_tasks(project_id: str) -> int:
    tasks = list_project_tasks(project_id)
    if any(_task_is_active(task.status) for task in tasks):
        raise AppException(409, "VIDEO_LOCALIZATION_DELETE_BLOCKED", "当前仍有配音生成任务，请先取消或等待完成后再删除项目")
    for task in tasks:
        delete_task(task.task_id)
    return len(tasks)


def task_summary(*, reconcile_active: bool = True) -> dict[str, int]:
    if reconcile_active:
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
    if voice_ids and not query.strip():
        where.append(
            f"json_extract(data, '$.voice_id') IN ({', '.join('?' for _ in voice_ids)})"
        )
        params.extend(voice_ids)

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
            query_parts.append(
                f"json_extract(data, '$.voice_id') IN ({', '.join('?' for _ in voice_ids)})"
            )
            query_params.extend(voice_ids)
        where.append(f"({' OR '.join(query_parts)})")
        params.extend(query_params)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
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
    timestamp_sql = "COALESCE(json_extract(data, '$.completed_at'), created_at)"
    placeholders = ", ".join("?" for _ in targets)
    with db.conn() as connection:
        rows = connection.execute(
            f"""
            WITH ranked AS (
                SELECT
                    task_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY date({timestamp_sql}, 'localtime')
                        ORDER BY {timestamp_sql}, task_id
                    ) AS sequence
                FROM tasks
                WHERE json_extract(data, '$.result_id') IS NOT NULL
            )
            SELECT task_id, sequence
            FROM ranked
            WHERE task_id IN ({placeholders})
            """,
            [task.task_id for task in targets],
        ).fetchall()
    return {
        str(row["task_id"]): max(1, int(row["sequence"] or 0))
        for row in rows
    }


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


def get_tasks_by_ids(
    task_ids: set[str] | list[str] | tuple[str, ...],
) -> dict[str, GenerationTask]:
    unique_ids = sorted({str(task_id) for task_id in task_ids if task_id})
    if not unique_ids:
        return {}
    placeholders = ", ".join("?" for _ in unique_ids)
    with db.conn() as connection:
        rows = connection.execute(
            f"SELECT data FROM tasks WHERE task_id IN ({placeholders})",
            unique_ids,
        ).fetchall()
    tasks = [
        _reconcile_stale_task(json.loads(row["data"]))
        for row in rows
    ]
    return {task.task_id: task for task in tasks}


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


def verify_result_output(
    *,
    result_id: str,
    expected_text: str,
    engine_id: str,
    language: str = "zh",
    asr_engine_id: str = "qwen3-asr-mlx",
) -> TTSVerificationResponse:
    import tempfile

    item = history_store.get(result_id)
    if not item:
        raise ValueError("生成结果不存在")
    audio_path = history_store.audio_path(result_id)
    if not audio_path:
        raise ValueError("结果音频不存在")
    if engine_id == text_verifier.SEED_AUDIO_ENGINE_ID and not expected_text:
        return text_verifier.skipped_non_speech_report(
            original_prompt=item.input_text,
            result_id=result_id,
            asr_engine_id=asr_engine_id,
        )
    normalized_language = language if language in {"auto", "zh", "en"} else "zh"
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
        asr_service.validate_request(asr_engine_id, normalized_language, suffix)
        result = asr_service.transcribe(engine_id=asr_engine_id, audio_path=str(asr_path), language=normalized_language)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
    record = TranscriptionRecord(
        engine_id=asr_engine_id,
        filename=audio_path.name,
        language=normalized_language,
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
        result_id=result_id,
        transcription_id=record.transcription_id,
        asr_engine_id=asr_engine_id,
    )


def _verify_task_output(task: GenerationTask, *, asr_engine_id: str = "qwen3-asr-mlx") -> TTSVerificationResponse:
    if not task.result_id:
        raise ValueError("任务没有可校对的生成结果")
    return verify_result_output(
        result_id=task.result_id,
        expected_text=verification_expected_text_for_task(task),
        engine_id=task.engine_id,
        language=_verification_language(task),
        asr_engine_id=asr_engine_id,
    )


async def _auto_verify_task(task_id: str) -> None:
    task = get_task(task_id)
    if not task or not task.result_id or task.verification:
        return
    if task.parameters.get("source") == "video_localization" and task.bind_to_video_localization:
        # Video-localization owns its placement through the durable handoff
        # outbox.  Generic TTS verification must neither delay nor replay that
        # projection, otherwise one result has two independent write paths.
        return
    report: TTSVerificationResponse | None = None
    verification_error: str | None = None
    try:
        report = await asyncio.to_thread(_verify_task_output, task)
    except Exception as exc:
        verification_error = f"自动校对失败：{exc}"

    task.verification = report
    task.verification_error = verification_error
    _save_history_verification(task.result_id, report, verification_error)
    updated = _save(task)
    await _broadcast(updated)


def _missing_auto_verification_task_ids(limit: int = 20) -> list[str]:
    candidates: list[GenerationTask] = []
    for row in db.list_all("tasks", "created_at", False, limit=-1):
        task = GenerationTask(**row)
        if (
            task.status != TaskStatus.success
            or not task.result_id
            or task.verification is not None
            or task.verification_error
            or task.task_type in {"segment", "export"}
            or (
                task.parameters.get("source") == "video_localization"
                and task.bind_to_video_localization
            )
        ):
            continue
        candidates.append(task)
    candidates.sort(key=lambda task: (task.completed_at or task.created_at, task.task_id), reverse=True)
    return [task.task_id for task in candidates[: max(0, limit)]]


async def _run_auto_verification(task_id: str) -> None:
    try:
        if _verification_lock is None:
            await _auto_verify_task(task_id)
        else:
            async with _verification_lock:
                await _auto_verify_task(task_id)
    finally:
        _verification_task_ids.discard(task_id)


async def _recover_missing_auto_verifications() -> None:
    for task_id in _missing_auto_verification_task_ids():
        if _shutting_down:
            return
        if task_id in _verification_task_ids:
            continue
        _verification_task_ids.add(task_id)
        await _run_auto_verification(task_id)


def schedule_auto_verification(task_id: str) -> None:
    loop = _worker_loop
    if not loop or loop.is_closed() or task_id in _verification_task_ids:
        return
    _verification_task_ids.add(task_id)
    loop.create_task(_run_auto_verification(task_id))


def find_longform_segment_task(longform_task_id: str, index: int) -> GenerationTask | None:
    """Recover a child persisted before its parent saved the returned task ID."""
    matches = [task for row in db.list_all("tasks", "created_at", False, limit=-1)
               if (task := GenerationTask(**row)).longform_task_id == longform_task_id
               and task.task_type == "segment" and task.longform_segment_index == index]
    return max(matches, key=lambda task: task.created_at) if matches else None


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
    global _queue, _worker_loop, _worker_task, _verification_recovery_task, _verification_lock, _shutting_down
    _shutting_down = False
    loop = asyncio.get_running_loop()
    with _lock:
        if _worker_task and not _worker_task.done() and _worker_loop is loop:
            return
        if _worker_task and not _worker_task.done():
            _worker_task.cancel()
        _queue = generation_task_scheduler.ProjectFairGenerationQueue(
            _queued_task_descriptor,
            on_drop=lambda task_id: _queued_task_ids.discard(task_id),
        )
        _worker_loop = loop
        _verification_lock = asyncio.Lock()
        _worker_task = loop.create_task(_worker(_queue))
        for task_id in _recover_incomplete_tasks():
            _enqueue_task_id(task_id)
        if not _verification_recovery_task or _verification_recovery_task.done():
            _verification_recovery_task = loop.create_task(_recover_missing_auto_verifications())


def _queued_task_descriptor(
    task_id: str,
) -> generation_task_scheduler.GenerationTaskQueueDescriptor | None:
    task = get_task(task_id)
    if task is None or _task_status_is_terminal(task.status):
        return None
    try:
        priority = video_localization_tts_handoff.resolve_generation_priority(task)
        return generation_task_scheduler.descriptor(
            task_id,
            project_id=task.project_id,
            priority=priority or task.parameters.get("resource_priority", "normal"),
        )
    except AppException as exc:
        _fail_queue_admission(task, exc)
        return None
    except Exception:
        # A project-specific projection bug must not kill the sole TTS worker
        # or silently admit this task at normal priority. Preserve a terminal
        # task/outbox record using the same failure path as explicit errors.
        logger.exception("Generation queue priority projection failed")
        _fail_queue_admission(task, AppException(
            500, "GENERATION_QUEUE_ADMISSION_FAILED",
            "读取排队策略失败，未启动生成；请修复后重试该任务。",
        ))
        return None


def _fail_queue_admission(task: GenerationTask, error: AppException) -> None:
    """Fail only this task on an explicit project error; never start continuation.

    Unknown projection failures are also terminal, never normal priority.
    The existing terminal outbox retains a damaged project's workflow
    update until project repair makes projection possible again.
    """
    message = f"{error.code}: {error.message}"
    status, failure = decide_task_state(task, engine_error=RuntimeError(message))
    if status is None:
        return
    task.logs.append("排队时读取项目失败，未启动生成；请先修复项目，再重试该任务。")
    _update_status_sync(task, status=status, error_message=failure, completed_at=now_iso())
    request = GenerateRequest(
        text=task.input_text,
        engine_id=task.engine_id,
        project_id=task.project_id,
        segment_id=task.segment_id,
        source=task.parameters.get("source"),
        bind_to_video_localization=task.bind_to_video_localization,
        video_localization_workflow_id=task.parameters.get("video_localization_workflow_id"),
        generation_id=task.generation_id,
    )
    video_localization_tts_handoff.mark_workflow_terminal(
        request, status=status.value, error_message=failure, source_id=task.task_id,
    )


async def shutdown() -> None:
    global _queue, _worker_loop, _worker_task, _verification_recovery_task, _verification_lock, _shutting_down
    _shutting_down = True
    task = _worker_task
    _queue = None
    _worker_loop = None
    _worker_task = None
    _verification_lock = None
    verification_recovery_task = _verification_recovery_task
    _verification_recovery_task = None
    _cancelled.clear()
    _queued_task_ids.clear()
    _verification_task_ids.clear()
    with _clients_lock:
        _clients.clear()
    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    if verification_recovery_task and not verification_recovery_task.done():
        verification_recovery_task.cancel()
        with suppress(asyncio.CancelledError):
            await verification_recovery_task


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
    if req.engine_id in reference_audio_integrity.DIRECT_REFERENCE_ENGINE_IDS and (req.reference_audio_path or req.voice_id):
        _resolve_reference(req)
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
    resolved_execution_plan = _resolve_execution_plan(req.engine_id)
    start_worker()
    request_parameters = _parameters_with_idempotency_marker(req)
    if resolved_execution_plan is not None:
        request_parameters["_execution_plan"] = resolved_execution_plan.to_dict()
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
    if (
        req.source == "video_localization"
        and req.bind_to_video_localization
        and task.project_id
        and task.segment_id
    ):
        (
            video_localization_tts_handoff.persist_and_register_generation_task(
                task,
                workflow_id=req.video_localization_workflow_id,
            )
        )
    else:
        _save(task)
    _enqueue_task_id(task.task_id)
    task = get_task(task.task_id) or task
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
    raw_generate_request = parameters.get("generate_request")
    try:
        generate_request = GenerateRequest(**raw_generate_request) if isinstance(raw_generate_request, dict) else None
    except Exception:
        generate_request = None
    if generate_request is not None:
        parameters.update(generate_request.model_dump(mode="json"))
    parameters.update(
        {
            "longform_task_id": longform_task.longform_task_id,
            "longform_segment_count": len(longform_task.segments),
            "longform_export_id": export_record.export_id,
            "source_result_ids": longform_task.result_ids,
            "generation_id": None,
            **(
                {
                    "source": generate_request.source,
                    "timeline_clip_id": generate_request.timeline_clip_id,
                    "video_localization_workflow_id": generate_request.video_localization_workflow_id,
                    "engine_parameters": generate_request.engine_parameters,
                    "language": generate_request.language,
                }
                if generate_request
                else {}
            ),
        }
    )
    task = GenerationTask(
        task_type="export",
        engine_id=longform_task.engine_id,
        voice_id=longform_task.voice_id,
        project_id=generate_request.project_id if generate_request else None,
        segment_id=generate_request.segment_id if generate_request else None,
        localized_subtitle_id=generate_request.localized_subtitle_id if generate_request else None,
        cue_id=generate_request.cue_id if generate_request else None,
        bind_to_video_localization=bool(generate_request and generate_request.bind_to_video_localization),
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
    task.generation_id = task.task_id
    task.parameters["generation_id"] = task.generation_id
    if task.bind_to_video_localization and task.project_id and task.segment_id:
        (
            video_localization_tts_handoff.persist_and_register_generation_task(
                task,
                workflow_id=(
                    generate_request.video_localization_workflow_id
                    if generate_request
                    else None
                ),
            )
        )
    voice = voice_store.get_voice(longform_task.voice_id) if longform_task.voice_id else None
    hist = video_localization_tts_handoff.persist_generated_history(
        task,
        HistoryItem(
            task_id=task.task_id,
            generation_id=task.generation_id,
            engine_id=longform_task.engine_id,
            voice_id=longform_task.voice_id,
            voice_name=voice.name if voice else None,
            project_id=task.project_id,
            segment_id=task.segment_id,
            localized_subtitle_id=task.localized_subtitle_id,
            cue_id=task.cue_id,
            bind_to_video_localization=task.bind_to_video_localization,
            longform_task_id=longform_task.longform_task_id,
            longform_segment_count=len(longform_task.segments),
            longform_export_id=export_record.export_id,
            input_text=longform_task.input_text,
            output_audio_id=export_record.export_id,
            output_path=export_record.path,
            duration_ms=duration_ms,
            generation_time_ms=generation_time_ms,
            parameter_snapshot=parameters,
        ),
    )
    task.result_id = hist.result_id
    if task.bind_to_video_localization and task.project_id and task.segment_id:
        if not (
            video_localization_tts_handoff
            .place_generated_result_with_retry(task, hist)
        ):
            task.error_message = "长文本音频已合并，但写回视频时间线失败。请从配音记录重新采用。"
    saved = _save(task)
    if not (saved.parameters.get("source") == "video_localization" and saved.bind_to_video_localization):
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
    await asyncio.to_thread(project_store.save_project, project)
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
        _upsert_task_payload(task, _save_data)
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


def mark_artifacts_removed_from_connection(
    connection,
    *,
    task_ids: set[str] | None = None,
    result_ids: set[str],
    removed_at: str,
) -> int:
    if not result_ids:
        return 0
    placeholders = ", ".join("?" for _ in result_ids)
    rows = connection.execute(
        f"""
        SELECT data
        FROM tasks
        WHERE json_extract(data, '$.result_id') IN ({placeholders})
        """,
        sorted(result_ids),
    ).fetchall()
    updated = 0
    for row in rows:
        task = GenerationTask(**json.loads(row["data"]))
        if task_ids is not None and task.task_id not in task_ids:
            continue
        if not task or _task_is_active(task.status):
            continue
        if task.result_id and task.result_id not in result_ids:
            continue
        task.result_id = None
        task.result_audio_id = None
        task.result_duration_ms = None
        task.artifacts_removed_at = removed_at
        if not task.logs or task.logs[-1] != "未使用的生成素材已清理":
            task.logs.append("未使用的生成素材已清理")
        db.upsert_from_connection(
            connection,
            "tasks",
            task.task_id,
            task.model_dump(),
        )
        updated += 1
    return updated


def mark_artifacts_removed(
    task_ids: set[str],
    result_ids: set[str],
    *,
    removed_at: str,
) -> int:
    with db.conn() as connection:
        return mark_artifacts_removed_from_connection(
            connection,
            task_ids=task_ids,
            result_ids=result_ids,
            removed_at=removed_at,
        )


async def retry_task(task_id: str, *, confirm_cloud_replay: bool = False) -> str:
    old = get_task(task_id)
    if not old:
        raise ValueError("Task not found")
    if engine_policy.requires_manual_replay_after_start(old.engine_id):
        if _task_is_active(old.status):
            raise AppException(409, "TASK_ACTIVE", "云端任务仍在执行，不能重复提交")
        if (
            old.status == TaskStatus.cancelled
            or old.provider_state_uncertain
        ) and not confirm_cloud_replay:
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
    if not data or _task_status_is_terminal(data.get("status")) or data.get("cancel_requested"):
        return
    accepted = _queue.put_nowait(task_id)
    if accepted is not False:
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
            task.error_message = CLOUD_RESULT_UNKNOWN_MESSAGE
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
    try:
        return reference_audio_integrity.resolve_primary_reference(req)
    except reference_audio_integrity.ReferenceAudioIntegrityError as exc:
        raise AppException(400, exc.code, exc.message) from exc


def _kwargs(req: GenerateRequest, output_path: str, *, device: str | None = None) -> dict:
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
            device=device,
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
            device=device,
        )
    if req.engine_id == "omnivoice":
        return engine_request_builder.build_omnivoice_single_kwargs(
            req,
            output_path,
            reference_audio=ref,
            ref_text=ref_text,
            model_dir=model_dir,
            device=device,
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
    return video_localization_tts_handoff.persist_generated_history(
        task,
        HistoryItem(
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
        ),
    )


def _update_project_segment(task: GenerationTask, audio_id: str | None, hist_result_id: str | None, status: SegmentStatus, error: str | None = None) -> None:
    """更新项目段落状态。纯函数，只做 IO。"""
    if (
        task.parameters.get("source") == "video_localization"
        and task.bind_to_video_localization
    ):
        return
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

    resolved_execution_plan = _execution_plan_for_task(task)
    kwargs = _kwargs(req, str(wav_path))
    if resolved_execution_plan and resolved_execution_plan.device:
        kwargs["device"] = resolved_execution_plan.device
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
        try:
            _update_status_sync(task, progress=next_value)
        except Exception as exc:
            # Progress is observability, not generation correctness. A brief
            # SQLite write conflict must not abort or orphan the inference
            # subprocess that is already producing paid/local work.
            logger.warning(
                "TTS progress persistence failed for task %s: %s",
                task.task_id,
                exc,
            )

    timeout_seconds = _timeout_seconds_for(engine_id)
    if engine_policy.requires_manual_replay_after_start(engine_id):
        provider_request_id = task.provider_request_id
        if _is_doubao_tts(engine_id):
            provider_request_id = provider_request_id or str(uuid.uuid4())
            kwargs["request_id"] = provider_request_id
        await _update_status(
            task,
            provider_request_id=provider_request_id,
            provider_state_uncertain=True,
        )
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
        timeline_sync_succeeded = await asyncio.to_thread(
            video_localization_tts_handoff.place_generated_result_with_retry,
            task,
            saved_history,
        )
        # Recovery notices describe an earlier queue state, not the successful
        # result. Clear them only on the success path; failed/cancelled paths
        # still persist their real terminal error below.
        task.error_message = None if timeline_sync_succeeded else (
            "音频已生成并保存在配音记录中，但写回视频本土化时间线失败。请在任务详情中查看回填日志后重试采用该记录。"
        )
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
    if task.status == TaskStatus.success:
        try:
            from app.services import video_localization_dubbing_executor
            if _uses_pipelined_closeout(task):
                video_localization_dubbing_executor.schedule_task_closeout(task)
            else:
                await video_localization_dubbing_executor.handle_completed_task(task)
        except Exception as exc:
            logger.warning(
                "Video-localization generation success close-out failed for %s: %s",
                task.task_id,
                exc,
            )
            task.logs.append(
                "音频已生成并保留；自动气口收尾暂未完成，可从当前语义组继续。"
            )
            _save(task)
    if task.status == TaskStatus.failed:
        try:
            from app.services import video_localization_dubbing_executor
            if _uses_pipelined_closeout(task):
                video_localization_dubbing_executor.schedule_task_closeout(task)
            else:
                await video_localization_dubbing_executor.handle_failed_task(task)
        except Exception as exc:
            logger.warning(
                "Video-localization generation failure close-out failed for %s: %s",
                task.task_id,
                exc,
            )
            task.logs.append(
                "生成失败已保留；后续组暂未继续，可从下一组恢复。"
            )
            _save(task)
    is_video_localization = (
        task.parameters.get("source") == "video_localization"
        and task.bind_to_video_localization
    )
    if (
        task.status == TaskStatus.success
        and task.result_id
        and not is_video_localization
        and not (task.longform_task_id and task.task_type == "segment")
    ):
        schedule_auto_verification(task.task_id)


def _uses_pipelined_closeout(task: GenerationTask) -> bool:
    return (
        task.parameters.get("video_localization_execution_scope")
        == "all_remaining"
        and int(
            task.parameters.get("video_localization_max_in_flight_groups")
            or 1
        )
        > 1
    )


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
