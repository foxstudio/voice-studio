from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models.exceptions import AppException
from app.models.schemas import GenerationTask
from app.services import task_queue

router = APIRouter()


@router.get("", response_model=list[GenerationTask])
async def list_tasks():
    return task_queue.list_tasks()


@router.get("/{task_id}", response_model=GenerationTask)
async def get_task(task_id: str):
    task = task_queue.get_task(task_id)
    if not task:
        raise AppException(404, "TASK_NOT_FOUND", "Task not found")
    return task


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str):
    return task_queue.cancel_task(task_id)


@router.post("/{task_id}/retry")
async def retry_task(task_id: str):
    return {"task_id": await task_queue.retry_task(task_id), "status": "queued"}


@router.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    task_queue.add_ws_client(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        task_queue.remove_ws_client(ws)

