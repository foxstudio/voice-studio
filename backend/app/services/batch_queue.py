from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.schemas.voice_studio import (
    BatchGenerateRequest,
    BatchSegmentInput,
    BatchSegmentResult,
    BatchTask,
    GenerateRequest,
    TaskStatus,
    now_iso,
)
from app.services import database as db, engine_registry, engine_request_builder, settings_store, voice_store
from app.services.paths import PROJECT_ROOT, expand_path

_queue: asyncio.Queue[str] | None = None
_worker_task: asyncio.Task[None] | None = None
_worker_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()
_cancelled_batches: set[str] = set()
_cancelled_lock = threading.Lock()
_TERMINAL_STATUSES = {TaskStatus.success, TaskStatus.failed, TaskStatus.cancelled}


def _save(batch: BatchTask) -> BatchTask:
    db.upsert("batches", batch.batch_task_id, batch.model_dump())
    return batch


def get_batch(batch_task_id: str) -> BatchTask | None:
    data = db.get_one("batches", "batch_task_id", batch_task_id)
    return BatchTask(**data) if data else None


def list_batches() -> list[BatchTask]:
    return [BatchTask(**d) for d in db.list_all("batches", "created_at")]


def _is_cancelled(batch_task_id: str) -> bool:
    with _cancelled_lock:
        return batch_task_id in _cancelled_batches


def _enqueue_batch_id(batch_task_id: str) -> None:
    if _queue is not None:
        _queue.put_nowait(batch_task_id)


def retry_batch(batch_task_id: str) -> BatchTask:
    batch = get_batch(batch_task_id)
    if not batch:
        raise ValueError("Batch task not found")
    if batch.status not in _TERMINAL_STATUSES:
        raise ValueError("Batch task is still active")
    for segment in batch.segments:
        if segment.status != TaskStatus.success:
            segment.status = TaskStatus.queued
            segment.error_message = None
            segment.output_path = None
            segment.duration_ms = None
    batch.status = TaskStatus.queued
    batch.progress = 0.0
    batch.error_message = None
    batch.started_at = None
    batch.completed_at = None
    with _cancelled_lock:
        _cancelled_batches.discard(batch.batch_task_id)
    _save(batch)
    _enqueue_batch_id(batch.batch_task_id)
    return batch


def cancel_batch(batch_task_id: str) -> dict:
    batch = get_batch(batch_task_id)
    if not batch:
        return {"batch_task_id": batch_task_id, "status": "not_found"}
    if batch.status in _TERMINAL_STATUSES:
        return {"batch_task_id": batch_task_id, "status": batch.status.value}
    with _cancelled_lock:
        _cancelled_batches.add(batch_task_id)
    for segment in batch.segments:
        if segment.status != TaskStatus.success:
            segment.status = TaskStatus.cancelled
            segment.error_message = "已取消"
    batch.status = TaskStatus.cancelled
    batch.completed_at = now_iso()
    _save(batch)
    return {"batch_task_id": batch_task_id, "status": "cancelled"}


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


async def shutdown() -> None:
    global _queue, _worker_loop, _worker_task
    task = _worker_task
    _queue = None
    _worker_loop = None
    _worker_task = None
    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def normalize_payload(payload: Any) -> BatchGenerateRequest:
    if isinstance(payload, list):
        return BatchGenerateRequest(segments=[BatchSegmentInput(**item) for item in payload])
    if isinstance(payload, dict):
        if "segments" in payload:
            return BatchGenerateRequest(**payload)
        if all(key in payload for key in ["chapter", "step", "text"]):
            return BatchGenerateRequest(segments=[BatchSegmentInput(**payload)])
    raise ValueError("BATCH_PAYLOAD_INVALID")


def _segment_id(segment: BatchSegmentInput, index: int) -> str:
    if segment.segment_id:
        return segment.segment_id
    if segment.chapter and segment.step is not None:
        return f"{segment.chapter}-{segment.step}"
    return f"segment-{index + 1:04d}"


def _safe_relative_audio(segment: BatchSegmentInput, output_format: str) -> str:
    if segment.audio:
        candidate = Path(segment.audio)
        parts = [part for part in candidate.parts if part not in ["", "."]]
        if candidate.is_absolute() or ".." in parts:
            candidate = Path(candidate.name)
        if not candidate.suffix:
            candidate = candidate.with_suffix(f".{output_format}")
        return str(candidate)
    chapter = segment.chapter or "chapter"
    step = segment.step or 1
    return f"{chapter}/{step}.{output_format}"


def _result_segments(req: BatchGenerateRequest) -> list[BatchSegmentResult]:
    results = []
    for index, segment in enumerate(req.segments):
        audio = _safe_relative_audio(segment, req.output_format)
        results.append(
            BatchSegmentResult(
                segment_id=_segment_id(segment, index),
                chapter=segment.chapter,
                step=segment.step,
                text=segment.text,
                audio=audio,
                status=TaskStatus.queued,
            )
        )
    return results


async def submit(payload: Any) -> BatchTask:
    global _queue
    req = normalize_payload(payload)
    start_worker()
    batch = BatchTask(
        project_name=req.project_name,
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        output_dir=req.output_dir,
        output_format=req.output_format,
        status=TaskStatus.queued,
        segments=_result_segments(req),
        parameters=req.model_dump(),
    )
    _save(batch)
    if _queue is None:
        with _lock:
            if _queue is None:
                _queue = asyncio.Queue()
    await _queue.put(batch.batch_task_id)
    return batch


def _resolve_reference(req: BatchGenerateRequest) -> str | None:
    ref = req.reference_audio_path or req.parameters.get("reference_audio_path")
    if isinstance(ref, str) and ref:
        if Path(ref).exists():
            return ref
        raise ValueError("REFERENCE_AUDIO_NOT_FOUND")
    return voice_store.reference_path(req.voice_id)


def _segment_reference(segment: BatchSegmentInput) -> str | None:
    if segment.reference_audio_path:
        if Path(segment.reference_audio_path).exists():
            return segment.reference_audio_path
        raise ValueError("REFERENCE_AUDIO_NOT_FOUND")
    return voice_store.reference_path(segment.voice_id)


def _segment_ref_text(segment: BatchSegmentInput) -> str | None:
    if segment.ref_text is not None:
        return segment.ref_text
    voice = voice_store.get_voice(segment.voice_id) if segment.voice_id else None
    return voice.reference_text if voice else None


def _all_segments_have_reference(req: BatchGenerateRequest) -> bool:
    return bool(req.segments) and all(_segment_reference(segment) for segment in req.segments)


def _all_segments_have_reference_text(req: BatchGenerateRequest) -> bool:
    return bool(req.segments) and all((_segment_ref_text(segment) or "").strip() for segment in req.segments)


def _common_kwargs(req: BatchGenerateRequest) -> dict[str, Any]:
    if engine_request_builder.is_mimo_tts_request(req.engine_id):
        return engine_request_builder.build_mimo_tts_batch_common_kwargs(
            req,
            reference_audio_path=_resolve_reference(req),
        )

    ref = _resolve_reference(req)
    voice = voice_store.get_voice(req.voice_id) if req.voice_id else None
    ref_text = req.ref_text or req.parameters.get("ref_text") or (voice.reference_text if voice else None)
    if req.engine_id == "omnivoice" and ref and ref_text is None:
        # Avoid OmniVoice's on-the-fly Whisper auto-transcription in batch jobs.
        ref_text = ""
    has_segment_refs = _all_segments_have_reference(req)
    has_segment_ref_texts = _all_segments_have_reference_text(req)
    if req.engine_id == "indextts-v2" and not (ref or has_segment_refs):
        raise ValueError("REFERENCE_AUDIO_REQUIRED")
    if req.engine_id in {"f5-tts", "cosyvoice-zero-shot"}:
        if not (ref or has_segment_refs):
            raise ValueError("REFERENCE_AUDIO_REQUIRED")
        if not ((ref_text or "").strip() or has_segment_ref_texts):
            raise ValueError("REFERENCE_TEXT_REQUIRED")
    base = GenerateRequest(text="placeholder", engine_id=req.engine_id, voice_id=req.voice_id, language=req.language)
    values = base.model_dump()
    values.update(req.parameters)
    if req.engine_id in {"emotivoice", "cosyvoice-sft"}:
        return engine_request_builder.build_preset_voice_batch_common_kwargs(values)
    if req.engine_id == "f5-tts":
        return engine_request_builder.build_f5_tts_batch_common_kwargs(
            values,
            reference_audio=ref,
            ref_text=ref_text,
        )
    if req.engine_id == "cosyvoice-zero-shot":
        return engine_request_builder.build_cosyvoice_zero_shot_batch_common_kwargs(
            values,
            reference_audio=ref,
            ref_text=ref_text,
        )
    if req.engine_id == "indextts-v2":
        return engine_request_builder.build_indextts_v2_batch_common_kwargs(
            values,
            parameters=req.parameters,
            reference_audio=ref,
            language=req.language,
            model_dir=str(settings_store.model_path(req.engine_id)),
        )
    if req.engine_id == "omnivoice":
        return engine_request_builder.build_omnivoice_batch_common_kwargs(
            values,
            reference_audio=ref,
            ref_text=ref_text,
            language=req.language,
            model_dir=str(settings_store.model_path(req.engine_id)),
        )
    raise ValueError(f"Unsupported engine: {req.engine_id}")


def _runner_segments(req: BatchGenerateRequest, batch: BatchTask, output_dir: Path) -> list[dict[str, Any]]:
    runner_segments = []
    for index, segment in enumerate(req.segments):
        result = batch.segments[index]
        output_path = output_dir / (result.audio or f"{result.segment_id}.{req.output_format}")
        params = dict(segment.parameters)
        explicit_params = {
            "speed": segment.speed,
            "emotion": segment.emotion,
            "emotion_text": segment.emotion_text,
            "style_instruction": segment.style_instruction,
            "voice_design_prompt": segment.voice_design_prompt,
            "mimo_voice": segment.mimo_voice,
            "language": segment.language,
            "reference_audio": _segment_reference(segment),
            "ref_text": _segment_ref_text(segment),
            "speaker_id": segment.parameters.get("speaker_id"),
            "prompt": segment.parameters.get("prompt"),
            "nfe_step": segment.parameters.get("nfe_step"),
            "cfg_strength": segment.parameters.get("cfg_strength"),
            "target_rms": segment.parameters.get("target_rms"),
            "cross_fade_duration": segment.parameters.get("cross_fade_duration"),
            "remove_silence": segment.parameters.get("remove_silence"),
        }
        params.update({key: value for key, value in explicit_params.items() if value is not None})
        runner_segments.append(
            {
                "segment_id": result.segment_id,
                "text": segment.text,
                "output_path": str(output_path),
                "parameters": params,
            }
        )
    return runner_segments


def run_batch(req: BatchGenerateRequest, batch: BatchTask) -> dict[str, Any]:
    engine_registry.ensure_loaded(req.engine_id)
    output_dir = expand_path(req.output_dir) if req.output_dir else settings_store.output_dir() / "batches" / batch.batch_task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "engine_id": req.engine_id,
        "common": _common_kwargs(req),
        "segments": _runner_segments(req, batch, output_dir),
    }
    env = {"PYTHONPATH": f"{PROJECT_ROOT / 'backend'}:{PROJECT_ROOT}", **os.environ}
    proc = subprocess.run(
        [sys.executable, "-m", "app.services.batch_inference_runner"],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=1800,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    stdout = (proc.stdout or "").strip()
    parsed_stdout: dict[str, Any] | None = None
    if stdout:
        try:
            parsed_stdout = json.loads(stdout.splitlines()[-1])
        except Exception:
            parsed_stdout = None
    if proc.returncode != 0:
        if parsed_stdout and isinstance(parsed_stdout.get("results"), list):
            parsed_stdout["_runner_error"] = proc.stderr[-1200:] or "Batch inference subprocess failed"
            return parsed_stdout
        error = parsed_stdout if parsed_stdout else {}
        raise RuntimeError(error.get("error") or proc.stderr[-1200:] or "Batch inference subprocess failed")
    if not stdout:
        raise RuntimeError("Batch inference subprocess returned no output")
    if parsed_stdout is None:
        return json.loads(stdout.splitlines()[-1])
    return parsed_stdout


async def _worker(queue: asyncio.Queue[str]) -> None:
    while True:
        batch_id = await queue.get()
        batch = get_batch(batch_id)
        if not batch or batch.status == TaskStatus.cancelled or _is_cancelled(batch_id):
            continue
        await _process(batch)


async def _process(batch: BatchTask) -> None:
    batch.status = TaskStatus.running
    batch.started_at = now_iso()
    batch.progress = 0.05
    _save(batch)
    success_count = 0
    try:
        req = BatchGenerateRequest(**batch.parameters)
        result = await asyncio.to_thread(run_batch, req, batch)
        if _is_cancelled(batch.batch_task_id):
            latest = get_batch(batch.batch_task_id)
            if latest:
                batch.segments = latest.segments
            batch.status = TaskStatus.cancelled
            batch.completed_at = now_iso()
            _save(batch)
            return
        by_id = {item["segment_id"]: item for item in result.get("results", [])}
        runner_error = result.get("_runner_error")
        for segment in batch.segments:
            if segment.status == TaskStatus.success and segment.output_path:
                success_count += 1
                continue
            data = by_id.get(segment.segment_id, {})
            if data.get("status") == "success":
                segment.status = TaskStatus.success
                segment.output_path = data.get("output_path")
                segment.duration_ms = data.get("duration_ms")
                success_count += 1
            else:
                segment.status = TaskStatus.failed
                segment.error_message = data.get("error_message") or runner_error or "批处理段落生成失败"
        batch.progress = 1.0
        failed_count = len(batch.segments) - success_count
        batch.status = TaskStatus.success if failed_count == 0 or (req.partial_success and success_count > 0) else TaskStatus.failed
        if batch.status == TaskStatus.success:
            batch.error_message = None
        elif runner_error:
            batch.error_message = f"批处理段落处理异常: 成功 {success_count} 个，失败 {failed_count} 个。{runner_error}"
        else:
            batch.error_message = f"批处理段落生成失败: 成功 {success_count} 个，失败 {failed_count} 个。"
    except Exception as exc:
        if _is_cancelled(batch.batch_task_id):
            latest = get_batch(batch.batch_task_id)
            if latest:
                batch.segments = latest.segments
            batch.status = TaskStatus.cancelled
            batch.completed_at = now_iso()
            _save(batch)
            return
        batch.status = TaskStatus.failed
        for segment in batch.segments:
            if segment.status not in [TaskStatus.success, TaskStatus.failed]:
                segment.status = TaskStatus.failed
                segment.error_message = str(exc)
        success_count = sum(1 for segment in batch.segments if segment.status == TaskStatus.success)
        failed_count = len(batch.segments) - success_count
        batch.error_message = f"批处理段落处理异常: 成功 {success_count} 个，失败 {failed_count} 个。{exc}"
    batch.completed_at = now_iso()
    _save(batch)
