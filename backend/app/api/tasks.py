"""任务队列 API"""

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.models.schemas import GenerationTask
from app.services import task_queue

router = APIRouter()


@router.get("", response_model=list[GenerationTask])
async def list_tasks():
    return task_queue.list_tasks()


@router.get("/{task_id}", response_model=GenerationTask)
async def get_task(task_id: str):
    task = task_queue.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return task


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str):
    task_queue.cancel_task(task_id)
    return {"status": "cancelled"}


@router.post("/{task_id}/retry")
async def retry_task(task_id: str):
    new_id = await task_queue.retry_task(task_id)
    return {"status": "queued", "task_id": new_id}


@router.websocket("/ws")
async def tasks_websocket(ws: WebSocket):
    await ws.accept()
    task_queue.add_ws_client(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        task_queue.remove_ws_client(ws)
