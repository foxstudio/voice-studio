from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.errors import AppException
from app.schemas.voice_studio import (
    BatchGenerateRequest,
    BatchSegmentInput,
    BatchSegmentResult,
    BatchTask,
    GenerateRequest,
    TaskStatus,
    now_iso,
)
from app.services import batch_segment_attempt_store, cosyvoice_constraints, database as db, emotion_reference, engine_policy, engine_registry, engine_request_builder, execution_plan, reference_audio_integrity, settings_store, voice_store
from app.services.paths import (
    PROJECT_ROOT,
    expand_path,
    project_subprocess_env,
)

_queue: asyncio.Queue[str] | None = None
_worker_task: asyncio.Task[None] | None = None
_worker_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()
_cancelled_batches: set[str] = set()
_queued_batch_ids: set[str] = set()
_cancelled_lock = threading.Lock()
_TERMINAL_STATUSES = {TaskStatus.success, TaskStatus.failed, TaskStatus.cancelled}


def _resolve_execution_plan(engine_id: str, requested_device: str | None = None) -> execution_plan.ExecutionPlan | None:
    if engine_policy.resolve_engine_id(engine_id) not in execution_plan.DEVICE_CONTROLLED_ENGINES:
        return None
    requested = requested_device or settings_store.get().device
    try:
        return execution_plan.resolve(engine_id, requested)
    except execution_plan.ExecutionPlanError as exc:
        raise AppException(409, "DEVICE_UNAVAILABLE", exc.message) from exc


def _execution_plan_for_batch(batch: BatchTask) -> execution_plan.ExecutionPlan | None:
    persisted = batch.parameters.get("_execution_plan")
    requested = None
    if isinstance(persisted, dict):
        requested = execution_plan.ExecutionPlan.from_dict(persisted).requested_device
    return _resolve_execution_plan(batch.engine_id, requested)


@dataclass(frozen=True)
class _BatchRunPlan:
    payload_json: str
    env: dict[str, str]


def _save(batch: BatchTask) -> BatchTask:
    db.upsert("batches", batch.batch_task_id, batch.model_dump())
    return batch


def get_batch(batch_task_id: str) -> BatchTask | None:
    data = db.get_one("batches", "batch_task_id", batch_task_id)
    return BatchTask(**data) if data else None


def list_batches() -> list[BatchTask]:
    return [BatchTask(**d) for d in db.list_all("batches", "created_at")]


def list_project_batches(project_id: str) -> list[BatchTask]:
    with db.conn() as connection:
        rows = connection.execute(
            """
            SELECT data
            FROM batches
            WHERE json_extract(data, '$.parameters.project_id') = ?
            ORDER BY created_at DESC
            """,
            (project_id,),
        ).fetchall()
    return [
        BatchTask(**json.loads(row["data"]))
        for row in rows
    ]


def delete_project_batches(project_id: str) -> int:
    batches = list_project_batches(project_id)
    if any(batch.status not in _TERMINAL_STATUSES for batch in batches):
        raise AppException(409, "VIDEO_LOCALIZATION_DELETE_BLOCKED", "当前仍有批量配音任务，请先取消或等待完成后再删除项目")
    for batch in batches:
        db.delete_one("batches", "batch_task_id", batch.batch_task_id)
        with _cancelled_lock:
            _cancelled_batches.discard(batch.batch_task_id)
    return len(batches)


def _is_cancelled(batch_task_id: str) -> bool:
    with _cancelled_lock:
        return batch_task_id in _cancelled_batches


def _enqueue_batch_id(batch_task_id: str) -> None:
    if _queue is None or batch_task_id in _queued_batch_ids:
        return
    _queue.put_nowait(batch_task_id)
    _queued_batch_ids.add(batch_task_id)


def retry_batch(
    batch_task_id: str,
    *,
    confirm_cloud_replay: bool = False,
) -> BatchTask:
    batch = get_batch(batch_task_id)
    if not batch:
        raise ValueError("Batch task not found")
    if batch.status not in _TERMINAL_STATUSES:
        raise ValueError("Batch task is still active")
    if (
        batch.provider_state_uncertain
        and not confirm_cloud_replay
    ):
        raise AppException(
            409,
            "CLOUD_REPLAY_CONFIRM_REQUIRED",
            "原云端批量请求可能仍已产生费用；"
            "确认云端状态后才能重新生成",
        )
    for segment in batch.segments:
        if segment.status != TaskStatus.success:
            segment.status = TaskStatus.queued
            segment.error_message = None
            segment.output_path = None
            segment.duration_ms = None
            segment.provider_state_uncertain = False
    batch.status = TaskStatus.queued
    batch.progress = 0.0
    batch.error_message = None
    batch.provider_state_uncertain = False
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
        _queued_batch_ids.clear()
        _worker_loop = loop
        _worker_task = loop.create_task(_worker(_queue))
        for batch_task_id in _recover_incomplete_batches():
            _enqueue_batch_id(batch_task_id)


async def shutdown() -> None:
    global _queue, _worker_loop, _worker_task
    task = _worker_task
    _queue = None
    _worker_loop = None
    _worker_task = None
    _queued_batch_ids.clear()
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
    emotion_reference.validate_batch_request(req)
    _validate_direct_references(req)
    _validate_cosyvoice_zero_shot_references(req)
    resolved_execution_plan = _resolve_execution_plan(req.engine_id)
    start_worker()
    parameters = req.model_dump()
    if resolved_execution_plan is not None:
        parameters["_execution_plan"] = resolved_execution_plan.to_dict()
    batch = BatchTask(
        project_name=req.project_name,
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        output_dir=req.output_dir,
        output_format=req.output_format,
        status=TaskStatus.queued,
        segments=_result_segments(req),
        parameters=parameters,
    )
    _save(batch)
    _enqueue_batch_id(batch.batch_task_id)
    return batch


def _recover_incomplete_batches() -> list[str]:
    batch_task_ids: list[str] = []
    for row in db.list_all(
        "batches",
        "created_at",
        False,
        limit=-1,
    ):
        batch = BatchTask(**row)
        if batch.status in _TERMINAL_STATUSES:
            continue
        if (
            engine_policy.requires_manual_replay_after_start(
                batch.engine_id
            )
            and (
                batch.provider_state_uncertain
                or batch.status
                in {
                    TaskStatus.running,
                    TaskStatus.postprocessing,
                    TaskStatus.retrying,
                }
            )
        ):
            legacy_batch_uncertainty = (
                batch.provider_state_uncertain
                and not any(
                    segment.provider_attempt_id
                    for segment in batch.segments
                )
            )
            batch.status = TaskStatus.failed
            batch.progress = min(batch.progress, 0.99)
            batch.completed_at = now_iso()
            batch.provider_state_uncertain = True
            batch.error_message = engine_policy.CLOUD_RESULT_UNKNOWN_MESSAGE
            for segment in batch.segments:
                if segment.status == TaskStatus.success:
                    continue
                may_have_reached_provider = bool(
                    segment.status == TaskStatus.running
                    or segment.provider_state_uncertain
                    or segment.provider_attempt_id
                    or legacy_batch_uncertainty
                )
                segment.status = TaskStatus.failed
                if may_have_reached_provider:
                    segment.provider_state_uncertain = True
                    segment.error_message = engine_policy.CLOUD_RESULT_UNKNOWN_MESSAGE
                else:
                    segment.error_message = "服务重启前尚未向云端发送，可安全重试"
            _save(batch)
            continue
        if batch.status != TaskStatus.queued:
            batch.status = TaskStatus.queued
            batch.progress = 0.0
            batch.started_at = None
            batch.completed_at = None
            batch.error_message = "服务重启后已重新排队。"
            for segment in batch.segments:
                if segment.status == TaskStatus.success:
                    continue
                segment.status = TaskStatus.queued
                segment.error_message = None
            _save(batch)
        batch_task_ids.append(batch.batch_task_id)
    return batch_task_ids


def _resolve_reference(req: BatchGenerateRequest) -> str | None:
    ref = req.reference_audio_path or req.parameters.get("reference_audio_path")
    values = req.model_dump()
    values["reference_audio_path"] = ref
    for key in (
        "custom_reference_source_audio_path",
        "custom_reference_source_duration_ms",
        "custom_reference_trim_start_ms",
        "custom_reference_trim_end_ms",
    ):
        if key in req.parameters:
            values[key] = req.parameters[key]
    try:
        return reference_audio_integrity.resolve_primary_reference(values)
    except reference_audio_integrity.ReferenceAudioIntegrityError as exc:
        raise ValueError(str(exc)) from exc


def _segment_reference(segment: BatchSegmentInput, engine_id: str) -> str | None:
    values = segment.model_dump()
    values["engine_id"] = segment.engine_id or engine_id
    for key in (
        "custom_reference_source_audio_path",
        "custom_reference_source_duration_ms",
        "custom_reference_trim_start_ms",
        "custom_reference_trim_end_ms",
    ):
        if key in segment.parameters:
            values[key] = segment.parameters[key]
    try:
        return reference_audio_integrity.resolve_primary_reference(values)
    except reference_audio_integrity.ReferenceAudioIntegrityError as exc:
        raise ValueError(str(exc)) from exc


def _segment_ref_text(segment: BatchSegmentInput) -> str | None:
    if segment.ref_text is not None:
        return segment.ref_text
    voice = voice_store.get_voice(segment.voice_id) if segment.voice_id else None
    return voice.reference_text if voice else None


def _all_segments_have_reference(req: BatchGenerateRequest) -> bool:
    return bool(req.segments) and all(_segment_reference(segment, req.engine_id) for segment in req.segments)


def _all_segments_have_reference_text(req: BatchGenerateRequest) -> bool:
    return bool(req.segments) and all((_segment_ref_text(segment) or "").strip() for segment in req.segments)


def _validate_cosyvoice_zero_shot_references(req: BatchGenerateRequest) -> None:
    """Validate the actual common/segment reference paths before queueing."""

    if req.engine_id != "cosyvoice-zero-shot":
        return
    common_ref = _resolve_reference(req)
    effective_references = [
        _segment_reference(segment, req.engine_id) or common_ref
        for segment in req.segments
    ] or [common_ref]
    if not effective_references or any(reference is None for reference in effective_references):
        raise ValueError("REFERENCE_AUDIO_REQUIRED")
    for reference in set(effective_references):
        cosyvoice_constraints.validate_zero_shot_reference_audio(reference)


def _validate_direct_references(req: BatchGenerateRequest) -> None:
    if req.engine_id not in reference_audio_integrity.DIRECT_REFERENCE_ENGINE_IDS:
        return
    if req.reference_audio_path or req.voice_id or req.parameters.get("reference_audio_path"):
        _resolve_reference(req)
    for segment in req.segments:
        if segment.reference_audio_path or segment.voice_id:
            _segment_reference(segment, req.engine_id)


def _common_kwargs(req: BatchGenerateRequest, *, device: str | None = None) -> dict[str, Any]:
    if engine_request_builder.is_mimo_tts_request(req.engine_id):
        return engine_request_builder.build_mimo_tts_batch_common_kwargs(
            req,
            reference_audio_path=_resolve_reference(req),
        )
    if engine_request_builder.is_doubao_tts_request(req.engine_id):
        voice = voice_store.get_voice(req.voice_id) if req.voice_id else None
        return engine_request_builder.build_doubao_tts_batch_common_kwargs(req, voice=voice)

    ref = _resolve_reference(req)
    voice = voice_store.get_voice(req.voice_id) if req.voice_id else None
    ref_text = req.ref_text or req.parameters.get("ref_text") or (voice.reference_text if voice else None)
    if req.engine_id == "omnivoice" and ref and ref_text is None:
        # Avoid OmniVoice's on-the-fly Whisper auto-transcription in batch jobs.
        ref_text = ""
    has_segment_refs = _all_segments_have_reference(req)
    has_segment_ref_texts = _all_segments_have_reference_text(req)
    if req.engine_id in {"indextts-v2", "confucius4-mlx-int8"} and not (ref or has_segment_refs):
        raise ValueError("REFERENCE_AUDIO_REQUIRED")
    if req.engine_id == "indextts-v2" and req.parameters.get("emotion_mode") == "emotion_text":
        raise ValueError("INDEXTTS_EMOTION_TEXT_UNSUPPORTED")
    if req.engine_id in {"f5-tts", "cosyvoice-zero-shot"}:
        if not (ref or has_segment_refs):
            raise ValueError("REFERENCE_AUDIO_REQUIRED")
        if not ((ref_text or "").strip() or has_segment_ref_texts):
            raise ValueError("REFERENCE_TEXT_REQUIRED")
    _validate_cosyvoice_zero_shot_references(req)
    if req.engine_id == "qwen3-tts-mlx-0.6b" and (ref or has_segment_refs) and not ((ref_text or "").strip() or has_segment_ref_texts):
        raise ValueError("REFERENCE_TEXT_REQUIRED")
    base = GenerateRequest(text="placeholder", engine_id=req.engine_id, voice_id=req.voice_id, language=req.language)
    values = base.model_dump()
    values.update(req.parameters)
    # Batch jobs start from the generic request model too.  Keep OmniVoice's
    # actual default aligned with its page and official runtime, while leaving
    # an explicit batch `diffusion_steps` untouched.
    if req.engine_id == "omnivoice" and "diffusion_steps" not in req.parameters:
        values["diffusion_steps"] = 32
    if req.engine_id == "emotivoice":
        return engine_request_builder.build_emotivoice_batch_common_kwargs(values)
    if req.engine_id == "cosyvoice-sft":
        return engine_request_builder.build_preset_voice_batch_common_kwargs(values)
    if req.engine_id == "f5-tts":
        return engine_request_builder.build_f5_tts_batch_common_kwargs(
            values,
            reference_audio=ref,
            ref_text=ref_text,
            device=device,
        )
    if req.engine_id == "cosyvoice-zero-shot":
        return engine_request_builder.build_cosyvoice_zero_shot_batch_common_kwargs(
            values,
            reference_audio=ref,
            ref_text=ref_text,
        )
    if req.engine_id == "indextts-v2":
        emotion_reference_audio = emotion_reference.resolve_batch_common(
            engine_id=req.engine_id,
            parameters=req.parameters,
        )
        return engine_request_builder.build_indextts_v2_batch_common_kwargs(
            values,
            parameters=req.parameters,
            reference_audio=ref,
            emotion_reference_audio=emotion_reference_audio,
            language=req.language,
            model_dir=str(settings_store.model_path(req.engine_id)),
            device=device,
        )
    if req.engine_id == "confucius4-mlx-int8":
        return engine_request_builder.build_confucius4_mlx_batch_common_kwargs(
            values,
            reference_audio=ref,
            language=req.language,
            model_dir=str(settings_store.model_path(req.engine_id)),
        )
    if req.engine_id == "qwen3-tts-mlx-0.6b":
        return engine_request_builder.build_qwen3_tts_batch_common_kwargs(
            values,
            parameters=req.parameters,
            reference_audio=ref,
            ref_text=ref_text,
            language=req.language,
        )
    if req.engine_id == "omnivoice":
        return engine_request_builder.build_omnivoice_batch_common_kwargs(
            values,
            reference_audio=ref,
            ref_text=ref_text,
            language=req.language,
            model_dir=str(settings_store.model_path(req.engine_id)),
            device=device,
        )
    raise ValueError(f"Unsupported engine: {req.engine_id}")


def _runner_segments(req: BatchGenerateRequest, batch: BatchTask, output_dir: Path) -> list[dict[str, Any]]:
    runner_segments = []
    common_emotion_reference = emotion_reference.resolve_batch_common(
        engine_id=req.engine_id,
        parameters=req.parameters,
    )
    for index, segment in enumerate(req.segments):
        result = batch.segments[index]
        if (
            result.status == TaskStatus.success
            and result.output_path
        ):
            continue
        output_path = output_dir / (result.audio or f"{result.segment_id}.{req.output_format}")
        params = dict(segment.parameters)
        if req.engine_id == "qwen3-tts-mlx-0.6b":
            # The Qwen3 CustomVoice route accepts ``style_instruction`` as
            # ``instruct``.  The single-request builder already removes it
            # for Base reference cloning and VoiceDesign; keep the segment
            # value here so CustomVoice batches do not silently lose it.
            # These two legacy generic fields still have no Qwen3 control
            # path and must not appear as deceptively "used" settings.
            for key in ("cfg_scale", "ddpm_steps"):
                params.pop(key, None)
        explicit_params = {
            "speed": segment.speed,
            "emotion": segment.emotion,
            "emotion_text": segment.emotion_text,
            "style_instruction": segment.style_instruction,
            "voice_design_prompt": segment.voice_design_prompt,
            "mimo_voice": segment.mimo_voice,
            "language": segment.language,
            "reference_audio": _segment_reference(segment, req.engine_id),
            "ref_text": _segment_ref_text(segment),
            "speaker_id": segment.parameters.get("speaker_id"),
            "prompt": segment.parameters.get("prompt"),
            "nfe_step": segment.parameters.get("nfe_step"),
            "cfg_strength": segment.parameters.get("cfg_strength"),
            "target_rms": segment.parameters.get("target_rms"),
            "cross_fade_duration": segment.parameters.get("cross_fade_duration"),
            "remove_silence": segment.parameters.get("remove_silence"),
            "top_p": segment.parameters.get("top_p"),
            "top_k": segment.parameters.get("top_k"),
            "repetition_penalty": segment.parameters.get("repetition_penalty"),
            "max_tokens": segment.parameters.get("max_tokens"),
            "cfg_scale": None if req.engine_id == "qwen3-tts-mlx-0.6b" else segment.parameters.get("cfg_scale"),
            "ddpm_steps": None if req.engine_id == "qwen3-tts-mlx-0.6b" else segment.parameters.get("ddpm_steps"),
        }
        params.update({key: value for key, value in explicit_params.items() if value is not None})
        segment_emotion_reference = emotion_reference.resolve_batch_segment(
            engine_id=req.engine_id,
            common_parameters=req.parameters,
            common_audio_path=common_emotion_reference,
            segment_parameters=segment.parameters,
        )
        if segment_emotion_reference.overrides_common:
            params["emotion_reference_audio"] = segment_emotion_reference.audio_path
            if segment_emotion_reference.clears_emotion:
                params["emotion"] = None
        runner_segments.append(
            {
                "segment_id": result.segment_id,
                "text": segment.text,
                "output_path": str(output_path),
                "parameters": params,
            }
        )
    return runner_segments


def _prepare_batch_run(
    req: BatchGenerateRequest,
    batch: BatchTask,
) -> _BatchRunPlan:
    _validate_cosyvoice_zero_shot_references(req)
    engine_registry.ensure_loaded(req.engine_id)
    resolved_execution_plan = _execution_plan_for_batch(batch)
    output_dir = expand_path(req.output_dir) if req.output_dir else settings_store.output_dir() / "batches" / batch.batch_task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    common = _common_kwargs(req)
    if resolved_execution_plan and resolved_execution_plan.device:
        common["device"] = resolved_execution_plan.device
    payload = {
        "engine_id": req.engine_id,
        "common": common,
        "segments": _runner_segments(req, batch, output_dir),
    }
    return _BatchRunPlan(
        payload_json=json.dumps(payload, ensure_ascii=False),
        env=project_subprocess_env(),
    )


def _execute_batch_run(plan: _BatchRunPlan) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "app.services.batch_inference_runner"],
        input=plan.payload_json,
        text=True,
        capture_output=True,
        timeout=1800,
        cwd=str(PROJECT_ROOT),
        env=plan.env,
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


def run_batch(req: BatchGenerateRequest, batch: BatchTask) -> dict[str, Any]:
    return _execute_batch_run(_prepare_batch_run(req, batch))


def _cloud_provider_request_id(engine_id: str) -> str | None:
    if engine_request_builder.is_doubao_tts_request(engine_id):
        # Volcengine requires a fresh unique reqid for every actual request.
        return str(uuid.uuid4())
    return None


def _execute_cloud_batch_segments(
    batch: BatchTask,
    plan: _BatchRunPlan,
) -> dict[str, Any]:
    """Fence and checkpoint each non-idempotent provider call separately."""

    payload = json.loads(plan.payload_json)
    results: list[dict[str, Any]] = []
    for segment_payload in payload.get("segments", []):
        if _is_cancelled(batch.batch_task_id):
            break
        segment_id = str(segment_payload["segment_id"])
        segment = next(
            item for item in batch.segments if item.segment_id == segment_id
        )
        provider_request_id = _cloud_provider_request_id(batch.engine_id)
        segment_payload = {
            **segment_payload,
            "parameters": {
                **dict(segment_payload.get("parameters") or {}),
                "provider_request_id": provider_request_id,
            },
        }
        request_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "engine_id": payload["engine_id"],
                    "common": payload.get("common", {}),
                    "segment": segment_payload,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        prepared_at = now_iso()
        with db.conn() as connection:
            attempt = batch_segment_attempt_store.prepare_from_connection(
                connection,
                batch_task_id=batch.batch_task_id,
                segment_id=segment_id,
                engine_id=batch.engine_id,
                request_fingerprint=request_fingerprint,
                provider_request_id=provider_request_id,
                prepared_at=prepared_at,
            )
            segment.provider_attempt_id = attempt.attempt_id
            segment.provider_request_id = provider_request_id
            segment.provider_log_id = None
            segment.provider_state_uncertain = True
            segment.status = TaskStatus.running
            segment.error_message = None
            batch.provider_state_uncertain = True
            db.upsert_from_connection(
                connection,
                "batches",
                batch.batch_task_id,
                batch.model_dump(),
            )

        single_payload = {
            **payload,
            "segments": [segment_payload],
        }
        try:
            response = _execute_batch_run(
                _BatchRunPlan(
                    payload_json=json.dumps(single_payload, ensure_ascii=False),
                    env=plan.env,
                )
            )
            data = next(
                (
                    item
                    for item in response.get("results", [])
                    if item.get("segment_id") == segment_id
                ),
                {},
            )
            if not data:
                data = {
                    "segment_id": segment_id,
                    "status": "failed",
                    "error_message": response.get("_runner_error")
                    or "云端批处理未返回片段结果",
                    "provider_request_id": provider_request_id,
                    "provider_state_uncertain": True,
                }
        except Exception as exc:
            data = {
                "segment_id": segment_id,
                "status": "failed",
                "error_message": str(exc),
                "provider_request_id": provider_request_id,
                "provider_state_uncertain": True,
            }

        succeeded = data.get("status") == "success"
        uncertain = bool(data.get("provider_state_uncertain")) and not succeeded
        completed_at = now_iso()
        segment.status = TaskStatus.success if succeeded else TaskStatus.failed
        segment.output_path = data.get("output_path") if succeeded else None
        segment.duration_ms = data.get("duration_ms") if succeeded else None
        segment.error_message = None if succeeded else str(
            data.get("error_message") or "批处理段落生成失败"
        )
        segment.provider_request_id = (
            data.get("provider_request_id") or provider_request_id
        )
        segment.provider_log_id = data.get("provider_log_id")
        segment.provider_state_uncertain = uncertain
        batch.provider_state_uncertain = any(
            item.provider_state_uncertain for item in batch.segments
        )
        with db.conn() as connection:
            batch_segment_attempt_store.finish_from_connection(
                connection,
                attempt_id=attempt.attempt_id,
                status=(
                    "success"
                    if succeeded
                    else "uncertain"
                    if uncertain
                    else "failed"
                ),
                completed_at=completed_at,
                provider_log_id=segment.provider_log_id,
                error_message=segment.error_message,
            )
            db.upsert_from_connection(
                connection,
                "batches",
                batch.batch_task_id,
                batch.model_dump(),
            )
        results.append(data)
    return {"results": results}


async def _worker(queue: asyncio.Queue[str]) -> None:
    while True:
        batch_id = await queue.get()
        _queued_batch_ids.discard(batch_id)
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
        cloud_batch = (
            engine_policy.requires_manual_replay_after_start(
                req.engine_id
            )
        )
        if cloud_batch:
            run_plan = await asyncio.to_thread(
                _prepare_batch_run,
                req,
                batch,
            )
            result = await asyncio.to_thread(
                _execute_cloud_batch_segments,
                batch,
                run_plan,
            )
        else:
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
        if cloud_batch:
            batch.provider_state_uncertain = any(
                segment.provider_state_uncertain
                for segment in batch.segments
            )
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
