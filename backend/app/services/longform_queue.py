from __future__ import annotations

import asyncio
import json
import logging
import time
import threading
from contextlib import suppress
from pathlib import Path

from app.errors import AppException
from app.schemas.voice_studio import (
    ExportRecord,
    ExportRequest,
    GenerateRequest,
    LongformGenerateRequest,
    LongformSegmentTask,
    LongformTask,
    PlannedTextSegment,
    TTSVerificationResponse,
    TaskStatus,
    TranscriptionRecord,
    now_iso,
)
from app.services import custom_reference_store, database as db, emotion_reference, engine_policy, export_store, reference_audio_integrity, task_queue, text_planner, text_verifier, video_localization_tts_handoff

_queue: asyncio.Queue[str] | None = None
_worker_task: asyncio.Task[None] | None = None
_worker_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()
_queued_task_ids: set[str] = set()
_cancelled_longform: set[str] = set()
_cancelled_segments: dict[str, set[int]] = {}
_cancelled_lock = threading.Lock()

_TERMINAL_STATUSES = {TaskStatus.success, TaskStatus.failed, TaskStatus.cancelled}


def _save(task: LongformTask) -> LongformTask:
    if video_localization_tts_handoff.persist_terminal_longform_task(task):
        return task
    db.upsert(
        "longform_tasks",
        task.longform_task_id,
        task.model_dump(),
    )
    return task


def get_task(longform_task_id: str) -> LongformTask | None:
    data = db.get_one("longform_tasks", "longform_task_id", longform_task_id)
    return _ensure_result_records(LongformTask(**data)) if data else None


def list_tasks(*, include_completed: bool = True, limit: int = 100) -> list[LongformTask]:
    with db.conn() as connection:
        if include_completed:
            rows = connection.execute(
                "SELECT data FROM longform_tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT data FROM longform_tasks WHERE status != ? ORDER BY created_at DESC LIMIT ?",
                (TaskStatus.success.value, limit),
            ).fetchall()
    return [_ensure_result_records(LongformTask(**json.loads(row["data"]))) for row in rows]


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
        for task in list_tasks():
            if task.status not in _TERMINAL_STATUSES:
                task.status = TaskStatus.queued
                task.progress = min(task.progress, 0.05)
                task.error_message = "服务重启后已重新排队。"
                _save(task)
                _enqueue_task_id(task.longform_task_id)


async def shutdown() -> None:
    global _queue, _worker_loop, _worker_task
    task = _worker_task
    _queue = None
    _worker_loop = None
    _worker_task = None
    _queued_task_ids.clear()
    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def submit(req: LongformGenerateRequest) -> LongformTask:
    # Video-localization handoffs are revalidated once at parent submission. Child
    # segments intentionally drop the timeline binding and the merged export is
    # adopted once after every segment has finished.
    generate_request = req.generate_request
    recovery = generate_request.video_localization_recovery
    if recovery is not None:
        if generate_request.source != "video_localization" or not generate_request.bind_to_video_localization or generate_request.engine_id != "omnivoice":
            raise AppException(409, "DUBBING_RECOVERY_HANDOFF_REQUIRED", "恢复任务必须使用当前项目的受管配音入口。")
        if [part.text for part in (req.segments or [])] != recovery.phrases or not req.merge_enabled or req.silence_ms != 0 or not req.verify_enabled or not req.stop_merge_on_verification_failed or req.max_retries > 2:
            raise AppException(409, "DUBBING_RECOVERY_SEGMENTS_CHANGED", "分段或合并参数与冻结恢复方案不一致。")
        existing = get_task(recovery.recovery_id)
        if existing is not None:
            saved = LongformGenerateRequest.model_validate(existing.parameters)
            if saved.generate_request.project_id != generate_request.project_id or saved.generate_request.video_localization_recovery != recovery:
                raise AppException(409, "DUBBING_RECOVERY_ID_CONFLICT", "恢复标识已用于另一份方案。")
            return existing
    if (
        generate_request.source == "video_localization"
        and generate_request.bind_to_video_localization
    ):
        generate_request = (
            video_localization_tts_handoff.finalize_submission(
                generate_request
            )
        )
        req = req.model_copy(update={"generate_request": generate_request})
    try:
        emotion_reference.resolve_generate_request(req.generate_request)
    except emotion_reference.EmotionReferenceError as exc:
        raise AppException(400, exc.code, exc.message) from exc
    generate_request = req.generate_request
    if generate_request.engine_id in reference_audio_integrity.DIRECT_REFERENCE_ENGINE_IDS and (
        generate_request.reference_audio_path or generate_request.voice_id
    ):
        try:
            reference_audio_integrity.resolve_primary_reference(generate_request)
        except reference_audio_integrity.ReferenceAudioIntegrityError as exc:
            raise AppException(400, exc.code, exc.message) from exc
    start_worker()
    planned = _segments_from_request(req)
    task = LongformTask(
        **({"longform_task_id": recovery.recovery_id} if recovery else {}),
        engine_id=req.generate_request.engine_id,
        voice_id=req.generate_request.voice_id,
        input_text=req.generate_request.text,
        status=TaskStatus.queued,
        progress=0.0,
        segments=[
            LongformSegmentTask(
                index=segment.index,
                text=segment.text,
                char_count=segment.char_count,
            )
            for segment in planned
        ],
        verify_enabled=req.verify_enabled,
        merge_enabled=req.merge_enabled,
        max_retries=req.max_retries,
        stop_merge_on_verification_failed=req.stop_merge_on_verification_failed,
        asr_engine_id=req.asr_engine_id,
        parameters=req.model_dump(),
    )
    if (
        generate_request.source == "video_localization"
        and generate_request.bind_to_video_localization
        and generate_request.project_id
        and generate_request.segment_id
    ):
        (
            video_localization_tts_handoff.persist_and_register_longform_task(
                task,
                generate_request,
            )
        )
    else:
        _save(task)
    _enqueue_task_id(task.longform_task_id)
    return task


async def retry_failed(
    longform_task_id: str,
    *,
    confirm_cloud_replay: bool = False,
) -> LongformTask:
    start_worker()
    task = get_task(longform_task_id)
    if not task:
        raise AppException(404, "LONGFORM_TASK_NOT_FOUND", "Longform task not found")
    if task.status not in _TERMINAL_STATUSES:
        raise AppException(409, "LONGFORM_TASK_ACTIVE", "Longform task is still active")
    uncertain_cloud_result = False
    for segment in task.segments:
        if segment.status != TaskStatus.failed:
            continue
        if not segment.task_id:
            uncertain_cloud_result = (
                uncertain_cloud_result
                or segment.error_message
                == engine_policy.CLOUD_RESULT_UNKNOWN_MESSAGE
            )
            continue
        generated = task_queue.get_task(segment.task_id)
        if not generated:
            uncertain_cloud_result = (
                uncertain_cloud_result
                or segment.error_message
                == engine_policy.CLOUD_RESULT_UNKNOWN_MESSAGE
            )
            continue
        if (
            engine_policy.requires_manual_replay_after_start(
                generated.engine_id
            )
            and generated.status not in _TERMINAL_STATUSES
        ):
            raise AppException(
                409,
                "LONGFORM_CLOUD_SEGMENT_ACTIVE",
                "原云端分段仍在执行，不能重复提交",
            )
        uncertain_cloud_result = (
            uncertain_cloud_result
            or generated.provider_state_uncertain
        )
    if uncertain_cloud_result and not confirm_cloud_replay:
        raise AppException(
            409,
            "CLOUD_REPLAY_CONFIRM_REQUIRED",
            "原云端请求可能仍已产生费用；确认云端状态后才能重新生成",
        )
    for segment in task.segments:
        if segment.status == TaskStatus.failed:
            segment.status = TaskStatus.queued
            segment.error_message = None
    task.status = TaskStatus.queued
    task.completed_at = None
    task.error_message = None
    _save(task)
    _enqueue_task_id(task.longform_task_id)
    return task


def cancel_longform(longform_task_id: str) -> dict:
    """取消整个长文本任务：所有剩余段落取消，不合并"""
    task = get_task(longform_task_id)
    if not task:
        raise AppException(404, "LONGFORM_TASK_NOT_FOUND", "Longform task not found")
    if task.status in _TERMINAL_STATUSES:
        raise AppException(409, "LONGFORM_TASK_TERMINAL", "Longform task is already in a terminal state")
    with _cancelled_lock:
        _cancelled_longform.add(longform_task_id)
    for seg in task.segments:
        if seg.status not in _TERMINAL_STATUSES:
            seg.status = TaskStatus.cancelled
            seg.error_message = "已取消"
        if seg.task_id and seg.status != TaskStatus.success:
            task_queue.cancel_task(seg.task_id)
    task.status = TaskStatus.cancelled
    task.completed_at = now_iso()
    _save(task)
    _notify_clients()
    return {"longform_task_id": longform_task_id, "status": "cancelled"}


def cancel_longform_segment(longform_task_id: str, segment_index: int) -> dict:
    """取消单个分段：跳过它，继续其余段落"""
    task = get_task(longform_task_id)
    if not task:
        raise AppException(404, "LONGFORM_TASK_NOT_FOUND", "Longform task not found")
    if task.status in _TERMINAL_STATUSES:
        raise AppException(409, "LONGFORM_TASK_TERMINAL", "Longform task is already in a terminal state")
    if segment_index < 0 or segment_index >= len(task.segments):
        raise AppException(400, "INVALID_SEGMENT_INDEX", f"Segment index {segment_index} out of range")
    seg = task.segments[segment_index]
    if seg.status in _TERMINAL_STATUSES:
        raise AppException(409, "SEGMENT_TERMINAL", "Segment is already in a terminal state")
    with _cancelled_lock:
        if longform_task_id not in _cancelled_segments:
            _cancelled_segments[longform_task_id] = set()
        _cancelled_segments[longform_task_id].add(segment_index)
    seg.status = TaskStatus.cancelled
    seg.error_message = "已取消"
    if seg.task_id:
        task_queue.cancel_task(seg.task_id)
    _save(task)
    _notify_clients()
    return {"longform_task_id": longform_task_id, "segment_index": segment_index, "status": "cancelled"}


def dismiss_longform(longform_task_id: str) -> dict:
    """关闭已终止的长文本任务：清理非终态分段，从数据库删除记录"""
    task = get_task(longform_task_id)
    if not task:
        raise AppException(404, "LONGFORM_TASK_NOT_FOUND", "Longform task not found")
    if task.status not in _TERMINAL_STATUSES:
        raise AppException(409, "LONGFORM_TASK_NOT_TERMINAL", "Only terminal tasks can be dismissed")
    for seg in task.segments:
        if seg.status not in _TERMINAL_STATUSES:
            seg.status = TaskStatus.cancelled
            seg.error_message = "用户关闭"
    managed_paths = custom_reference_store.managed_paths_in(task.model_dump())
    db.delete_one("longform_tasks", "longform_task_id", longform_task_id)
    for path in managed_paths:
        custom_reference_store.delete_if_unreferenced(path)
    _notify_clients()
    return {"longform_task_id": longform_task_id, "status": "dismissed"}


def _notify_clients() -> None:
    """Longform state changed — frontend polls via Api.longformTasks()."""


def _segments_from_request(req: LongformGenerateRequest) -> list[PlannedTextSegment]:
    if req.segments:
        return req.segments
    plan = text_planner.plan_text(
        text=req.generate_request.text,
        engine_id=req.generate_request.engine_id,
        planner_mode="rules",
        target_format=req.generate_request.output_format,
    )
    return plan.segments or [
        PlannedTextSegment(index=1, text=req.generate_request.text, char_count=len(req.generate_request.text.strip()), segment_reason="direct_text")
    ]


def _ensure_result_records(task: LongformTask) -> LongformTask:
    if task.segments:
        segment_count = len(task.segments)
        for segment in task.segments:
            if segment.task_id:
                updated = task_queue.update_longform_segment_metadata(
                    segment.task_id,
                    longform_task_id=task.longform_task_id,
                    segment_index=segment.index,
                    segment_count=segment_count,
                )
                if not updated and segment.result_id:
                    task_queue.add_completed_longform_segment(task, segment)
            elif segment.result_id:
                restored = task_queue.add_completed_longform_segment(task, segment)
                if restored:
                    segment.task_id = restored.task_id
                    _save(task)
    if task.status != TaskStatus.success or not task.export_id or not task.export_path:
        return task
    if task_queue.find_longform_export_task(task.longform_task_id, task.export_id):
        return task
    export_path = Path(task.export_path)
    if not export_path.exists():
        return task
    try:
        req = LongformGenerateRequest(**task.parameters)
        silence_ms = req.silence_ms
    except Exception:
        silence_ms = 300
    task_queue.add_completed_longform_export(
        task,
        ExportRecord(
            export_id=task.export_id,
            path=task.export_path,
            format=export_path.suffix.lstrip(".") or "wav",
            source_count=len(task.result_ids) or len(task.segments),
        ),
        duration_ms=_merged_duration_ms(task, silence_ms),
        generation_time_ms=_segments_generation_time_ms(task),
    )
    return task


def _enqueue_task_id(longform_task_id: str) -> None:
    if _queue is None or longform_task_id in _queued_task_ids:
        return
    _queue.put_nowait(longform_task_id)
    _queued_task_ids.add(longform_task_id)


def _restore_cancelled_segment_state(task: LongformTask) -> None:
    db_task = get_task(task.longform_task_id)
    if not db_task:
        return
    for idx, segment in enumerate(task.segments):
        if idx >= len(db_task.segments):
            break
        if segment.status == TaskStatus.success:
            continue
        task.segments[idx] = db_task.segments[idx]


def _is_longform_cancelled(longform_task_id: str) -> bool:
    with _cancelled_lock:
        return longform_task_id in _cancelled_longform


async def _worker(queue: asyncio.Queue[str]) -> None:
    while True:
        longform_task_id = await queue.get()
        _queued_task_ids.discard(longform_task_id)
        task = get_task(longform_task_id)
        if not task or task.status == TaskStatus.cancelled:
            continue
        await _process(task)


async def _process(task: LongformTask) -> None:
    task.status = TaskStatus.running
    task.started_at = task.started_at or now_iso()
    task.progress = max(task.progress, 0.02)
    _save(task)
    try:
        req = LongformGenerateRequest(**task.parameters)
        total = max(1, len(task.segments))
        for index, segment in enumerate(task.segments):
            # 检查整个任务是否被取消
            longform_cancelled = _is_longform_cancelled(task.longform_task_id)
            if longform_cancelled:
                _restore_cancelled_segment_state(task)
                task.status = TaskStatus.cancelled
                task.completed_at = now_iso()
                _save(task)
                _mark_bound_workflow_terminal(task)
                return

            # 跳过已成功的段落
            if segment.status == TaskStatus.success and segment.result_id:
                task.progress = (index + 1) / total * 0.9
                _save(task)
                continue

            # 检查当前段落是否被单独取消
            with _cancelled_lock:
                segment_cancelled = index in _cancelled_segments.get(task.longform_task_id, set())
            if segment_cancelled:
                segment.status = TaskStatus.cancelled
                segment.error_message = "已取消"
                task.progress = (index + 1) / total * 0.9
                _save(task)
                continue
            ok = await _process_segment(task, segment, req)
            if _is_longform_cancelled(task.longform_task_id):
                _restore_cancelled_segment_state(task)
                task.status = TaskStatus.cancelled
                task.completed_at = now_iso()
                task.progress = (index + 1) / total * 0.9
                _save(task)
                _mark_bound_workflow_terminal(task)
                return
            task.progress = (index + 1) / total * 0.9
            _save(task)
        success_segments = [segment for segment in task.segments if segment.status == TaskStatus.success and segment.result_id]
        failed_segments = [segment for segment in task.segments if segment.status == TaskStatus.failed]
        task.result_ids = [segment.result_id for segment in success_segments if segment.result_id]

        # 整个任务被取消 → 不合并，直接标记 cancelled
        longform_cancelled = _is_longform_cancelled(task.longform_task_id)
        if longform_cancelled:
            _restore_cancelled_segment_state(task)
            task.status = TaskStatus.cancelled
            task.error_message = "任务已取消"
        elif req.generate_request.video_localization_recovery and len(success_segments) != len(task.segments):
            task.status = TaskStatus.failed
            task.error_message = "恢复短语尚未全部成功，未合并不完整音频。"
        elif task.merge_enabled and task.result_ids and (not failed_segments or not task.stop_merge_on_verification_failed):
            record = export_store.create_export(
                ExportRequest(
                    result_ids=task.result_ids,
                    format=req.generate_request.output_format,
                    silence_ms=req.silence_ms,
                    normalize=req.normalize,
                )
            )
            task.export_id = record.export_id
            task.export_path = record.path
            task_queue.add_completed_longform_export(
                task,
                record,
                duration_ms=_merged_duration_ms(task, req.silence_ms),
                generation_time_ms=_segments_generation_time_ms(task),
            )
            task.progress = 1.0
            if failed_segments:
                task.status = TaskStatus.failed
                task.error_message = f"{len(failed_segments)} 个段落生成或校对失败，已合并 {len(success_segments)}/{len(task.segments)} 个成功段"
            else:
                task.status = TaskStatus.success
        else:
            task.progress = 1.0
            if failed_segments:
                task.status = TaskStatus.failed
                task.error_message = f"{len(failed_segments)} 个段落生成或校对失败，已完成 {len(success_segments)}/{len(task.segments)} 段"
            else:
                task.status = TaskStatus.success
    except Exception as exc:
        task.status = TaskStatus.failed
        task.error_message = str(exc)
    task.completed_at = now_iso()
    _save(task)
    _mark_bound_workflow_terminal(task)
    if task.status == TaskStatus.success and req.generate_request.video_localization_recovery:
        merged = task_queue.find_longform_export_task(task.longform_task_id, task.export_id)
        if merged is not None:
            from app.services import video_localization_dubbing_executor
            try:
                await video_localization_dubbing_executor.handle_completed_task(merged)
            except Exception:
                # Resume retries placement without regenerating completed segments.
                logging.getLogger(__name__).exception("Recovery placement failed for %s", task.longform_task_id)


def _mark_bound_workflow_terminal(task: LongformTask) -> None:
    if task.status not in {TaskStatus.failed, TaskStatus.cancelled}:
        return
    try:
        req = LongformGenerateRequest(**task.parameters).generate_request
    except Exception:
        return
    if not (
        req.source == "video_localization"
        and req.bind_to_video_localization
        and req.project_id
        and req.video_localization_workflow_id
    ):
        return
    video_localization_tts_handoff.mark_workflow_terminal(
        req,
        status=task.status.value,
        error_message=task.error_message,
        source_id=task.longform_task_id,
    )


async def _process_segment(task: LongformTask, segment: LongformSegmentTask, req: LongformGenerateRequest) -> bool:
    last_error = ""
    recovery = req.generate_request.video_localization_recovery
    remaining = max(0, task.max_retries + 1 - segment.attempts) if recovery else task.max_retries + 1
    existing = task_queue.get_task(segment.task_id) if segment.task_id else None
    if recovery and existing is None:
        existing = task_queue.find_longform_segment_task(task.longform_task_id, segment.index)
        if existing is not None:
            segment.task_id = existing.task_id
            segment.attempts = max(1, segment.attempts)
            _save(task)
    reusable = existing is not None and existing.status in {TaskStatus.pending, TaskStatus.queued, TaskStatus.running, TaskStatus.postprocessing, TaskStatus.retrying, TaskStatus.success}
    for _ in range(max(remaining, 1 if reusable else 0)):
        segment_request = _segment_request(req.generate_request, segment.text)
        if not reusable:
            if recovery and segment.attempts >= task.max_retries + 1:
                break
            segment.attempts += 1
            segment.status = TaskStatus.queued
            segment.error_message = None
            _save(task)
            segment.task_id = await task_queue.submit(
                segment_request,
                task_type="segment",
                longform_task_id=task.longform_task_id,
                longform_segment_index=segment.index,
                longform_segment_count=len(task.segments),
            )
        reusable = False
        segment.status = TaskStatus.running
        _save(task)
        try:
            generated = await _wait_for_generation(
                segment.task_id,
                runtime_timeout=float(engine_policy.timeout_seconds_for(segment_request.engine_id) + 60),
            )
        except TimeoutError as e:
            last_error = str(e)
            cloud_result_unknown = (
                engine_policy.requires_manual_replay_after_start(
                    segment_request.engine_id
                )
            )
            if cloud_result_unknown:
                last_error = engine_policy.CLOUD_RESULT_UNKNOWN_MESSAGE
            segment.status = TaskStatus.failed
            segment.error_message = last_error
            _save(task)
            # The child may still be running. A timeout is not permission
            # to launch another generation; resume this task ID explicitly.
            return False
        if recovery and generated and generated.status == TaskStatus.cancelled:
            segment.status = TaskStatus.cancelled
            segment.error_message = "已取消，不自动重新生成。"
            _save(task)
            return False
        if not generated or generated.status != TaskStatus.success or not generated.result_id:
            last_error = generated.error_message if generated else "生成任务不存在"
            if generated and generated.provider_state_uncertain:
                last_error = engine_policy.CLOUD_RESULT_UNKNOWN_MESSAGE
            segment.status = TaskStatus.failed
            segment.error_message = last_error or "段落生成失败"
            _save(task)
            if generated and generated.provider_state_uncertain:
                return False
            continue
        segment.result_id = generated.result_id
        segment.duration_ms = generated.result_duration_ms
        if task.verify_enabled:
            report = await _verify_segment(segment, task.asr_engine_id, segment_request.language, segment_request.engine_id)
            segment.verification = report
            if segment.task_id:
                task_queue.attach_verification(segment.task_id, report)
            if recovery and report.status == "skipped":
                segment.status = TaskStatus.failed
                segment.error_message = "短语识别检查尚未完成；已保留音频，继续时只补检查。"
                _save(task)
                return False
            verification_blocks_segment = recovery is not None or not (
                req.generate_request.source == "video_localization"
                and req.generate_request.bind_to_video_localization
            )
            if report.status == "failed" and verification_blocks_segment:
                last_error = "校对失败：检测到缺句或漏段"
                segment.status = TaskStatus.failed
                segment.error_message = last_error
                _save(task)
                continue
        segment.status = TaskStatus.success
        segment.error_message = None
        _save(task)
        return True
    segment.status = TaskStatus.failed
    segment.error_message = segment.error_message or last_error or "段落生成失败"
    _save(task)
    return False


def _merged_duration_ms(task: LongformTask, silence_ms: int) -> int | None:
    durations = [segment.duration_ms for segment in task.segments if segment.status == TaskStatus.success and segment.duration_ms]
    if not durations:
        return None
    return sum(durations) + max(0, len(durations) - 1) * silence_ms


def _segments_generation_time_ms(task: LongformTask) -> int | None:
    total = 0
    found = False
    for segment in task.segments:
        if not segment.task_id:
            continue
        generated = task_queue.get_task(segment.task_id)
        if generated and generated.generation_time_ms:
            total += generated.generation_time_ms
            found = True
    return total if found else None


def _segment_request(base: GenerateRequest, text: str) -> GenerateRequest:
    values = base.model_dump()
    values["text"] = text
    values.update(
        {
            "project_id": None,
            "video_localization_recovery": None,
            "video_localization_execution_scope": None,
            "segment_id": None,
            "localized_subtitle_id": None,
            "cue_id": None,
            "timeline_clip_id": None,
            "generation_id": None,
            "bind_to_video_localization": False,
            "video_localization_workflow_id": None,
            "video_localization_start_ms": None,
            "video_localization_end_ms": None,
            "video_localization_source_cue_ids": [],
        }
    )
    return GenerateRequest(**values)


async def _wait_for_generation(task_id: str, runtime_timeout: float):
    running_since: float | None = None
    while True:
        task = task_queue.get_task(task_id)
        if task and task.status in _TERMINAL_STATUSES:
            return task
        if task and task.status not in {TaskStatus.pending, TaskStatus.queued}:
            running_since = running_since or time.monotonic()
        if running_since is not None and time.monotonic() - running_since > runtime_timeout:
            raise TimeoutError(f"任务 {task_id} 运行超时（{runtime_timeout:.0f}s，不含排队时间）")
        await asyncio.sleep(1)


async def _verify_segment(segment: LongformSegmentTask, asr_engine_id: str, language: str, engine_id: str) -> TTSVerificationResponse:
    if not segment.result_id:
        return text_verifier.verify_transcript(expected_text=segment.text, transcript_text="", result_id=None, asr_engine_id=asr_engine_id)
    try:
        return await asyncio.to_thread(
            task_queue.verify_result_output,
            result_id=segment.result_id,
            expected_text=segment.text,
            engine_id=engine_id,
            language=language,
            asr_engine_id=asr_engine_id,
        )
    except Exception as exc:
        return _skipped_verification(segment, asr_engine_id, f"ASR 校对不可用：{exc}")


def _skipped_verification(segment: LongformSegmentTask, asr_engine_id: str, message: str) -> TTSVerificationResponse:
    return TTSVerificationResponse(
        status="skipped",
        coverage=0.0,
        similarity=0.0,
        expected_text=segment.text,
        transcript_text="",
        normalized_expected=text_verifier.normalize_text(segment.text),
        normalized_transcript="",
        warnings=[message],
        suggestions=["可稍后配置 ASR 引擎后重新校对，或人工复听确认。"],
        result_id=segment.result_id,
        asr_engine_id=asr_engine_id,
    )
