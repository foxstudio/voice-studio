from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from app.models.schemas import (
    BatchGenerateRequest,
    BatchSegmentInput,
    BatchSegmentResult,
    BatchTask,
    GenerateRequest,
    TaskStatus,
    now_iso,
)
from app.services import database as db, engine_registry, settings_store, voice_store
from app.services.paths import PROJECT_ROOT, expand_path

_queue: asyncio.Queue[str] = asyncio.Queue()
_started = False
_lock = threading.Lock()


def _save(batch: BatchTask) -> BatchTask:
    db.upsert("batches", batch.batch_task_id, batch.model_dump())
    return batch


def get_batch(batch_task_id: str) -> BatchTask | None:
    data = db.get_one("batches", "batch_task_id", batch_task_id)
    return BatchTask(**data) if data else None


def list_batches() -> list[BatchTask]:
    return [BatchTask(**d) for d in db.list_all("batches", "created_at")]


def start_worker() -> None:
    global _started
    with _lock:
        if _started:
            return
        asyncio.get_event_loop().create_task(_worker())
        _started = True


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
    await _queue.put(batch.batch_task_id)
    return batch


def _resolve_reference(req: BatchGenerateRequest) -> str | None:
    ref = req.reference_audio_path or req.parameters.get("reference_audio_path")
    if isinstance(ref, str) and Path(ref).exists():
        return ref
    return voice_store.reference_path(req.voice_id)


def _common_kwargs(req: BatchGenerateRequest) -> dict[str, Any]:
    if req.engine_id in ["mimo-v2.5-tts", "mimo-v2.5-tts-preset", "mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voiceclone"]:
        settings = settings_store.get()
        api_key = settings_store.mimo_api_key()
        if not settings.cloud_enabled:
            raise ValueError("MIMO_CLOUD_DISABLED")
        if not api_key:
            raise ValueError("MIMO_API_KEY_MISSING")
        model = "mimo-v2.5-tts" if req.engine_id in ["mimo-v2.5-tts", "mimo-v2.5-tts-preset"] else req.engine_id
        return {
            "base_url": settings.mimo_base_url,
            "api_key": api_key,
            "model": model,
            "mimo_voice": req.parameters.get("mimo_voice") or settings.mimo_default_voice,
            "voice_design_prompt": req.parameters.get("voice_design_prompt"),
            "reference_audio_path": _resolve_reference(req),
            "temperature": req.parameters.get("temperature", 0.6),
            "top_p": req.parameters.get("top_p", 0.95),
        }

    ref = _resolve_reference(req)
    voice = voice_store.get_voice(req.voice_id) if req.voice_id else None
    ref_text = req.ref_text or req.parameters.get("ref_text") or (voice.reference_text if voice else None)
    if req.engine_id == "indextts-v2" and not ref:
        raise ValueError("REFERENCE_AUDIO_REQUIRED")
    base = GenerateRequest(text="placeholder", engine_id=req.engine_id, voice_id=req.voice_id, language=req.language)
    values = base.model_dump()
    values.update(req.parameters)
    common = {
        "reference_audio": ref,
        "ref_text": ref_text,
        "language": req.language,
        "model_dir": str(settings_store.model_path(req.engine_id)),
    }
    for key in [
        "temperature",
        "top_p",
        "top_k",
        "repetition_penalty",
        "max_text_tokens_per_segment",
        "interval_silence",
        "segment_overlap_ms",
        "speed",
        "seed",
        "max_mel_tokens",
        "diffusion_steps",
        "cfg_rate",
        "emotion",
        "emo_alpha",
        "emotion_text",
    ]:
        common[key] = values.get(key)
    return common


def _runner_segments(req: BatchGenerateRequest, batch: BatchTask, output_dir: Path) -> list[dict[str, Any]]:
    runner_segments = []
    for index, segment in enumerate(req.segments):
        result = batch.segments[index]
        output_path = output_dir / (result.audio or f"{result.segment_id}.{req.output_format}")
        params = {
            "speed": segment.speed,
            "emotion": segment.emotion,
            "emotion_text": segment.emotion_text,
            "style_instruction": segment.style_instruction,
            "voice_design_prompt": segment.voice_design_prompt,
            "mimo_voice": segment.mimo_voice,
            "language": segment.language,
            "reference_audio": segment.reference_audio_path,
            "ref_text": segment.ref_text,
        }
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
    if proc.returncode != 0:
        try:
            error = json.loads(stdout.splitlines()[-1] if stdout else "{}")
        except Exception:
            error = {}
        raise RuntimeError(error.get("error") or proc.stderr[-1200:] or "Batch inference subprocess failed")
    if not stdout:
        raise RuntimeError("Batch inference subprocess returned no output")
    return json.loads(stdout.splitlines()[-1])


async def _worker() -> None:
    while True:
        batch_id = await _queue.get()
        batch = get_batch(batch_id)
        if not batch:
            continue
        await _process(batch)


async def _process(batch: BatchTask) -> None:
    batch.status = TaskStatus.running
    batch.started_at = now_iso()
    batch.progress = 0.05
    _save(batch)
    try:
        req = BatchGenerateRequest(**batch.parameters)
        result = await asyncio.to_thread(run_batch, req, batch)
        by_id = {item["segment_id"]: item for item in result.get("results", [])}
        success = 0
        for segment in batch.segments:
            data = by_id.get(segment.segment_id, {})
            if data.get("status") == "success":
                segment.status = TaskStatus.success
                segment.output_path = data.get("output_path")
                segment.duration_ms = data.get("duration_ms")
                success += 1
            else:
                segment.status = TaskStatus.failed
                segment.error_message = data.get("error_message") or "批处理段落生成失败"
        batch.progress = 1.0
        batch.status = TaskStatus.success if success == len(batch.segments) else TaskStatus.failed
        batch.error_message = None if batch.status == TaskStatus.success else "部分或全部段落生成失败"
    except Exception as exc:
        batch.status = TaskStatus.failed
        batch.error_message = str(exc)
        for segment in batch.segments:
            if segment.status not in [TaskStatus.success, TaskStatus.failed]:
                segment.status = TaskStatus.failed
                segment.error_message = str(exc)
    batch.completed_at = now_iso()
    _save(batch)
