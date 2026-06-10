from __future__ import annotations

import asyncio
import time
import threading
from contextlib import suppress
from pathlib import Path

from app.models.exceptions import AppException
from app.models.schemas import (
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
from app.services import asr_service, database as db, export_store, history_store, task_queue, text_planner, text_verifier

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
    db.upsert("longform_tasks", task.longform_task_id, task.model_dump())
    return task


def get_task(longform_task_id: str) -> LongformTask | None:
    data = db.get_one("longform_tasks", "longform_task_id", longform_task_id)
    return _ensure_result_records(LongformTask(**data)) if data else None


def list_tasks() -> list[LongformTask]:
    return [_ensure_result_records(LongformTask(**item)) for item in db.list_all("longform_tasks", "created_at")]


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
    start_worker()
    planned = _segments_from_request(req)
    task = LongformTask(
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
    _save(task)
    _enqueue_task_id(task.longform_task_id)
    return task


async def retry_failed(longform_task_id: str) -> LongformTask:
    start_worker()
    task = get_task(longform_task_id)
    if not task:
        raise AppException(404, "LONGFORM_TASK_NOT_FOUND", "Longform task not found")
    if task.status not in _TERMINAL_STATUSES:
        raise AppException(409, "LONGFORM_TASK_ACTIVE", "Longform task is still active")
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
    db.delete_one("longform_tasks", "longform_task_id", longform_task_id)
    _notify_clients()
    return {"longform_task_id": longform_task_id, "status": "dismissed"}


def _notify_clients() -> None:
    """推送 WebSocket 通知"""
    try:
        task_queue._notify_clients()
    except Exception:
        pass


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
            with _cancelled_lock:
                longform_cancelled = task.longform_task_id in _cancelled_longform
            if longform_cancelled:
                task.status = TaskStatus.cancelled
                task.completed_at = now_iso()
                _save(task)
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
            task.progress = (index + 1) / total * 0.9
            _save(task)
            if not ok and task.stop_merge_on_verification_failed:
                break
        success_segments = [segment for segment in task.segments if segment.status == TaskStatus.success and segment.result_id]
        failed_segments = [segment for segment in task.segments if segment.status == TaskStatus.failed]
        task.result_ids = [segment.result_id for segment in success_segments if segment.result_id]

        # 整个任务被取消 → 不合并，直接标记 cancelled
        with _cancelled_lock:
            longform_cancelled = task.longform_task_id in _cancelled_longform
        if longform_cancelled:
            task.status = TaskStatus.cancelled
            task.error_message = "任务已取消"
        elif failed_segments:
            task.status = TaskStatus.failed
            task.error_message = f"{len(failed_segments)} 个段落生成或校对失败"
        elif task.merge_enabled and len(task.result_ids) > 1:
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
            task.status = TaskStatus.success
        else:
            task.progress = 1.0
            task.status = TaskStatus.success
    except Exception as exc:
        task.status = TaskStatus.failed
        task.error_message = str(exc)
    task.completed_at = now_iso()
    _save(task)


async def _process_segment(task: LongformTask, segment: LongformSegmentTask, req: LongformGenerateRequest) -> bool:
    last_error = ""
    for _ in range(task.max_retries + 1):
        segment.attempts += 1
        segment.status = TaskStatus.queued
        segment.error_message = None
        _save(task)
        segment_request = _segment_request(req.generate_request, segment.text)
        segment.task_id = await task_queue.submit(
            segment_request,
            task_type="segment",
            longform_task_id=task.longform_task_id,
            longform_segment_index=segment.index,
            longform_segment_count=len(task.segments),
        )
        segment.status = TaskStatus.running
        _save(task)
        try:
            generated = await _wait_for_generation(segment.task_id)
        except TimeoutError as e:
            last_error = str(e)
            segment.status = TaskStatus.failed
            segment.error_message = last_error
            _save(task)
            continue
        if not generated or generated.status != TaskStatus.success or not generated.result_id:
            last_error = generated.error_message if generated else "生成任务不存在"
            segment.status = TaskStatus.failed
            segment.error_message = last_error or "段落生成失败"
            _save(task)
            continue
        segment.result_id = generated.result_id
        segment.duration_ms = generated.result_duration_ms
        if task.verify_enabled:
            report = await _verify_segment(segment, task.asr_engine_id, segment_request.language)
            segment.verification = report
            if segment.task_id:
                task_queue.attach_verification(segment.task_id, report)
            if report.status == "failed":
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
    return GenerateRequest(**values)


async def _wait_for_generation(task_id: str, timeout: float = 300.0):
    start = time.monotonic()
    for _ in range(1800):
        task = task_queue.get_task(task_id)
        if task and task.status in _TERMINAL_STATUSES:
            return task
        if time.monotonic() - start > timeout:
            raise TimeoutError(f"任务 {task_id} 生成超时（{timeout:.0f}s）")
        await asyncio.sleep(1)
    raise TimeoutError(f"任务 {task_id} 生成超时（超过最大轮询次数）")


async def _verify_segment(segment: LongformSegmentTask, asr_engine_id: str, language: str) -> TTSVerificationResponse:
    if not segment.result_id:
        return text_verifier.verify_transcript(expected_text=segment.text, transcript_text="", result_id=None, asr_engine_id=asr_engine_id)
    audio_path = history_store.audio_path(segment.result_id)
    if not audio_path:
        return _skipped_verification(segment, asr_engine_id, "结果音频不存在，跳过校对。")
    try:
        suffix = Path(audio_path).suffix.lower() or ".wav"
        asr_service.validate_request(asr_engine_id, language if language in {"auto", "zh", "en"} else "zh", suffix)
        result = asr_service.transcribe(engine_id=asr_engine_id, audio_path=str(audio_path), language=language if language in {"auto", "zh", "en"} else "zh")
        record = TranscriptionRecord(
            engine_id=asr_engine_id,
            filename=Path(audio_path).name,
            language=language if language in {"auto", "zh", "en"} else "zh",
            text=result["text"],
            segments=asr_service.normalize_segments(result.get("segments")),
            size_bytes=Path(audio_path).stat().st_size if Path(audio_path).exists() else 0,
            usage_seconds=result.get("usage_seconds"),
            provider_response_id=result.get("provider_response_id"),
        )
        for key, value in asr_service.timestamp_metadata_for(record.engine_id, record.segments).items():
            setattr(record, key, value)
        db.upsert("transcriptions", record.transcription_id, record.model_dump(), "created_at")
        return text_verifier.verify_transcript(
            expected_text=segment.text,
            transcript_text=record.text,
            result_id=segment.result_id,
            transcription_id=record.transcription_id,
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
