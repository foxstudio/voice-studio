from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Iterable

from fastapi import WebSocket

from app.models.schemas import (
    GenerateRequest,
    GenerationTask,
    HistoryItem,
    Project,
    ScriptSegment,
    SegmentStatus,
    TaskStatus,
    now_iso,
)
from app.services import audio_tools, database as db, engine_registry, history_store, project_store, settings_store, voice_store

_queue: asyncio.Queue[str] = asyncio.Queue()
_started = False
_lock = threading.Lock()
_cancelled: set[str] = set()
_clients: list[WebSocket] = []


def _save(task: GenerationTask) -> GenerationTask:
    db.upsert("tasks", task.task_id, task.model_dump())
    return task


def list_tasks() -> list[GenerationTask]:
    return [GenerationTask(**d) for d in db.list_all("tasks", "created_at")]


def get_task(task_id: str) -> GenerationTask | None:
    data = db.get_one("tasks", "task_id", task_id)
    return GenerationTask(**data) if data else None


async def _broadcast(task: GenerationTask) -> None:
    dead = []
    payload = task.model_dump_json()
    for ws in _clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _clients:
            _clients.remove(ws)


def add_ws_client(ws: WebSocket) -> None:
    _clients.append(ws)


def remove_ws_client(ws: WebSocket) -> None:
    if ws in _clients:
        _clients.remove(ws)


def start_worker() -> None:
    global _started
    with _lock:
        if _started:
            return
        asyncio.get_event_loop().create_task(_worker())
        _started = True


async def submit(req: GenerateRequest, task_type: str = "single", project_id: str | None = None, segment_id: str | None = None) -> str:
    start_worker()
    task = GenerationTask(
        task_type=task_type,
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        project_id=project_id,
        segment_id=segment_id,
        input_text=req.text,
        status=TaskStatus.queued,
        parameters=req.model_dump(),
    )
    _save(task)
    await _queue.put(task.task_id)
    await _broadcast(task)
    return task.task_id


async def submit_project(project: Project) -> list[str]:
    task_ids = []
    for seg in project.segments:
        if not seg.text.strip() or seg.locked:
            continue
        role = next((r for r in project.roles if r.role_id == seg.role_id), None)
        req = GenerateRequest(
            text=seg.text,
            engine_id=seg.engine_id or (role.default_engine_id if role else None) or project.default_engine_id or "indextts-v2",
            voice_id=seg.voice_id or (role.default_voice_id if role else None),
            language=seg.language or (role.default_language if role else "zh"),
            emotion=seg.emotion or (role.default_emotion if role else None),
            speed=seg.speed or (role.default_speed if role else 1.0),
        )
        seg.status = SegmentStatus.queued
        task_ids.append(await submit(req, task_type="segment", project_id=project.project_id, segment_id=seg.segment_id))
    project_store.save_project(project)
    return task_ids


def cancel_task(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        return {"task_id": task_id, "status": "not_found"}
    _cancelled.add(task_id)
    if task.status in [TaskStatus.pending, TaskStatus.queued]:
        task.status = TaskStatus.cancelled
        task.completed_at = now_iso()
        _save(task)
    return {"task_id": task_id, "status": task.status}


async def retry_task(task_id: str) -> str:
    old = get_task(task_id)
    if not old:
        raise ValueError("Task not found")
    return await submit(GenerateRequest(**old.parameters), old.task_type, old.project_id, old.segment_id)


async def _worker() -> None:
    while True:
        task_id = await _queue.get()
        task = get_task(task_id)
        if not task or task.status == TaskStatus.cancelled:
            continue
        await _process(task)


def _resolve_reference(req: GenerateRequest) -> str | None:
    if req.reference_audio_path and Path(req.reference_audio_path).exists():
        return req.reference_audio_path
    return voice_store.reference_path(req.voice_id)


def _emotion(req: GenerateRequest):
    if req.emotion:
        return req.emotion
    if req.emotion_mode == "emotion_vector":
        return req.emotion_values
    if req.emotion_mode == "emotion_text":
        return req.emotion_text
    return None


def _kwargs(req: GenerateRequest, output_path: str) -> dict:
    ref = _resolve_reference(req)
    voice = voice_store.get_voice(req.voice_id) if req.voice_id else None
    ref_text = req.ref_text or (voice.reference_text if voice else None)
    if req.engine_id == "indextts-v2" and not ref:
        raise ValueError("REFERENCE_AUDIO_REQUIRED")
    if req.engine_id in ["mimo-v2.5-tts", "mimo-v2.5-tts-preset", "mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voiceclone"]:
        settings = settings_store.get()
        api_key = settings_store.mimo_api_key()
        if not settings.cloud_enabled:
            raise ValueError("MIMO_CLOUD_DISABLED")
        if not api_key:
            raise ValueError("MIMO_API_KEY_MISSING")
        model = "mimo-v2.5-tts" if req.engine_id in ["mimo-v2.5-tts", "mimo-v2.5-tts-preset"] else req.engine_id
        return {
            "text": req.text,
            "output_path": output_path,
            "base_url": settings.mimo_base_url,
            "api_key": api_key,
            "model": model,
            "voice": req.mimo_voice or settings.mimo_default_voice,
            "instruction": req.style_instruction or req.emotion_text or req.emotion,
            "voice_design_prompt": req.voice_design_prompt or req.style_instruction or req.emotion_text,
            "reference_audio_path": ref,
            "temperature": req.temperature,
            "top_p": req.top_p,
        }
    model_dir = str(settings_store.model_path(req.engine_id))
    common = {
        "text": req.text,
        "reference_audio": ref,
        "output_path": output_path,
        "model_dir": model_dir,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "top_k": req.top_k,
        "repetition_penalty": req.repetition_penalty,
        "max_text_tokens_per_segment": req.max_text_tokens_per_segment,
        "interval_silence": req.interval_silence,
        "segment_overlap_ms": req.segment_overlap_ms,
        "speed": req.speed,
        "seed": req.seed,
    }
    if req.engine_id == "indextts-v2":
        common.update({
            "max_mel_tokens": req.max_mel_tokens or 1500,
            "diffusion_steps": req.diffusion_steps,
            "cfg_rate": req.cfg_rate,
            "emotion": _emotion(req),
            "emo_alpha": req.emo_alpha,
        })
    else:
        common.update({
            "language": req.language,
            "ref_text": ref_text,
            "emotion": req.emotion,
            "emotion_text": req.emotion_text,
        })
    return common


async def _process(task: GenerationTask) -> None:
    task.status = TaskStatus.running
    task.started_at = now_iso()
    task.progress = 0.2
    _save(task)
    await _broadcast(task)
    try:
        req = GenerateRequest(**task.parameters)
        engine_registry.ensure_loaded(req.engine_id)
        settings_store.ensure_directories()
        audio_id = task.task_id
        wav_path = settings_store.output_dir() / f"{audio_id}.wav"
        result = await asyncio.to_thread(engine_registry.run_isolated, req.engine_id, _kwargs(req, str(wav_path)))
        if task.task_id in _cancelled:
            task.status = TaskStatus.cancelled
            task.error_message = "cancelled by user"
        else:
            final_path = Path(result["output_path"])
            if req.output_format != "wav":
                converted = settings_store.output_dir() / f"{audio_id}.{req.output_format}"
                audio_tools.copy_or_convert(final_path, converted, req.output_format)
                final_path = converted
            task.status = TaskStatus.success
            task.progress = 1.0
            task.result_audio_id = audio_id
            task.result_duration_ms = result.get("duration_ms")
            task.generation_time_ms = result.get("generation_time_ms")
            hist = history_store.add(HistoryItem(
                task_id=task.task_id,
                engine_id=req.engine_id,
                voice_id=req.voice_id,
                voice_name=voice_store.get_voice(req.voice_id).name if req.voice_id and voice_store.get_voice(req.voice_id) else None,
                project_id=task.project_id,
                segment_id=task.segment_id,
                input_text=req.text,
                output_audio_id=audio_id,
                output_path=str(final_path),
                duration_ms=task.result_duration_ms,
                generation_time_ms=task.generation_time_ms,
                parameter_snapshot=task.parameters,
            ))
            task.result_id = hist.result_id
            if task.project_id and task.segment_id:
                project_store.update_segment_result(task.project_id, task.segment_id, audio_id, hist.result_id, SegmentStatus.completed)
    except Exception as exc:
        task.status = TaskStatus.failed
        task.error_message = str(exc)
        if task.project_id and task.segment_id:
            project_store.update_segment_result(task.project_id, task.segment_id, None, None, SegmentStatus.failed, str(exc))
    task.completed_at = now_iso()
    _save(task)
    await _broadcast(task)
