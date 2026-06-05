"""任务队列 - 内存队列 + WebSocket 推送"""

import asyncio
import uuid
from datetime import datetime

from fastapi import WebSocket

from app.models.schemas import GenerateRequest, GenerationTask, TaskStatus

_tasks: dict[str, GenerationTask] = {}
_ws_clients: list[WebSocket] = []
_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_running = False


def add_ws_client(ws: WebSocket):
    _ws_clients.append(ws)


def remove_ws_client(ws: WebSocket):
    _ws_clients.discard(ws) if hasattr(_ws_clients, "discard") else None
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


async def _worker():
    global _worker_running
    _worker_running = True
    while True:
        task_id = await _queue.get()
        task = _tasks.get(task_id)
        if not task or task.status == TaskStatus.cancelled:
            continue
        task.status = TaskStatus.running
        task.started_at = datetime.now().isoformat()
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
        await _broadcast(task)


async def submit(req: GenerateRequest) -> str:
    if not _worker_running:
        asyncio.create_task(_worker())
    task_id = uuid.uuid4().hex[:12]
    task = GenerationTask(
        task_id=task_id,
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        input_text=req.text,
    )
    _tasks[task_id] = task
    task.status = TaskStatus.queued
    await _queue.put(task_id)
    await _broadcast(task)
    return task_id


def list_tasks() -> list[GenerationTask]:
    return list(_tasks.values())


def get_task(task_id: str) -> GenerationTask:
    return _tasks.get(task_id)


def cancel_task(task_id: str) -> None:
    task = _tasks.get(task_id)
    if task:
        task.status = TaskStatus.cancelled


async def retry_task(task_id: str) -> str:
    old = _tasks.get(task_id)
    if not old:
        return task_id
    new_req = GenerateRequest(
        text=old.input_text,
        engine_id=old.engine_id,
        voice_id=old.voice_id,
    )
    return await submit(new_req)
