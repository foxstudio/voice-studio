"""任务队列 - 内存队列 + WebSocket 推送 + SQLite"""

import asyncio
import uuid
from datetime import datetime

from fastapi import WebSocket

from app.models.schemas import GenerateRequest, GenerationTask, HistoryItem, TaskStatus
from app.services import database as db, history_store

_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_running = False
_ws_clients: list[WebSocket] = []


def add_ws_client(ws: WebSocket):
    _ws_clients.append(ws)


def remove_ws_client(ws: WebSocket):
    try:
        _ws_clients.remove(ws)
    except ValueError:
        pass


async def _broadcast(task: GenerationTask):
    data = task.model_dump_json()
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


def list_tasks() -> list[GenerationTask]:
    return [GenerationTask(**d) for d in db.db_list_tasks()]


def get_task(task_id: str) -> GenerationTask | None:
    for d in db.db_list_tasks():
        if d.get("task_id") == task_id:
            return GenerationTask(**d)
    return None


async def _worker():
    global _worker_running
    _worker_running = True
    while True:
        task_id = await _queue.get()
        task_data = None
        for d in db.db_list_tasks():
            if d.get("task_id") == task_id:
                task_data = d
                break
        if not task_data:
            continue
        task = GenerationTask(**task_data)
        if task.status == TaskStatus.cancelled:
            continue

        task.status = TaskStatus.running
        task.started_at = datetime.now().isoformat()
        db.db_save_task(task.model_dump())
        await _broadcast(task)

        try:
            from app.services.tts_engine import synthesize
            result = await synthesize(task)
            task.status = TaskStatus.success
            task.result_audio_id = result["audio_id"]
            task.result_duration_ms = result.get("duration_ms")
            task.generation_time_ms = result.get("generation_time_ms")
        except Exception as e:
            task.status = TaskStatus.failed
            task.error_message = str(e)

        task.completed_at = datetime.now().isoformat()
        db.db_save_task(task.model_dump())
        await _broadcast(task)

        if task.status == TaskStatus.success:
            voice_name = None
            if task.voice_id:
                from app.services.voice_store import get_voice
                v = get_voice(task.voice_id)
                if v:
                    voice_name = v.name
            history_store.add(HistoryItem(
                result_id=uuid.uuid4().hex[:12],
                task_id=task.task_id,
                engine_id=task.engine_id,
                engine_version=task.engine_version,
                voice_id=task.voice_id,
                voice_name=voice_name,
                input_text=task.input_text,
                output_audio_id=task.result_audio_id,
                duration_ms=task.result_duration_ms,
                generation_time_ms=task.generation_time_ms,
                parameter_snapshot=task.parameters,
            ))


async def submit(req: GenerateRequest) -> str:
    if not _worker_running:
        asyncio.create_task(_worker())
    task_id = uuid.uuid4().hex[:12]

    # 构建完整参数快照
    params = {
        "engine_version": req.engine_version.value,
        "reference_audio_path": req.reference_audio_path,
        "emotion_mode": req.emotion_mode.value,
        "emotion_values": req.emotion_values,
        "emotion_text": req.emotion_text,
        "emo_alpha": req.emo_alpha,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "top_k": req.top_k,
        "repetition_penalty": req.repetition_penalty,
        "speed": req.speed,
        "seed": req.seed,
        "max_mel_tokens": req.max_mel_tokens,
        "max_text_tokens_per_segment": req.max_text_tokens_per_segment,
        "interval_silence": req.interval_silence,
        "segment_overlap_ms": req.segment_overlap_ms,
        "diffusion_steps": req.diffusion_steps,
        "cfg_rate": req.cfg_rate,
    }

    task = GenerationTask(
        task_id=task_id,
        engine_id=req.engine_id,
        engine_version=req.engine_version.value,
        voice_id=req.voice_id,
        input_text=req.text,
        parameters=params,
    )
    task.status = TaskStatus.queued
    db.db_save_task(task.model_dump())
    await _queue.put(task_id)
    await _broadcast(task)
    return task_id


def cancel_task(task_id: str) -> None:
    task = get_task(task_id)
    if task:
        task.status = TaskStatus.cancelled
        db.db_save_task(task.model_dump())


async def retry_task(task_id: str) -> str:
    old = get_task(task_id)
    if not old:
        return task_id
    new_req = GenerateRequest(
        text=old.input_text,
        engine_id=old.engine_id,
        voice_id=old.voice_id,
    )
    return await submit(new_req)
