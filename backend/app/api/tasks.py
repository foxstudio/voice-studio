from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.errors import AppException
from app.schemas.voice_studio import GenerationTask
from app.services import task_queue

router = APIRouter()


class TaskSummaryResponse(BaseModel):
    all: int
    active: int
    processing: int
    waiting: int
    success: int
    failed: int


class TaskPageResponse(BaseModel):
    items: list[GenerationTask]
    total: int
    offset: int
    limit: int
    summary: TaskSummaryResponse
    download_sequences: dict[str, int]


class TaskRetryRequest(BaseModel):
    confirm_cloud_replay: bool = False


@router.get("", response_model=list[GenerationTask])
async def list_tasks():
    return task_queue.list_tasks()


@router.get("/page", response_model=TaskPageResponse)
async def list_tasks_page(
    offset: int = Query(0, ge=0),
    limit: int = Query(12, ge=1, le=100),
    status: str = Query("all", pattern="^(all|active|success|failed)$"),
    engine_ids: str = "",
    voice_ids: str = "",
    q: str = Query("", max_length=200),
    created_after: str | None = None,
    sort: str = Query("latest", pattern="^(latest|oldest|duration_desc)$"),
):
    items, total = task_queue.list_tasks_page(
        offset=offset,
        limit=limit,
        status_filter=status,
        engine_ids=[value for value in engine_ids.split(",") if value] or None,
        voice_ids=[value for value in voice_ids.split(",") if value] or None,
        query=q,
        created_after=created_after,
        sort_by=sort,
    )
    return TaskPageResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
        summary=TaskSummaryResponse(**task_queue.task_summary()),
        download_sequences=task_queue.task_download_sequences(items),
    )


@router.get("/summary", response_model=TaskSummaryResponse)
async def get_task_summary():
    return TaskSummaryResponse(**task_queue.task_summary())


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
async def retry_task(task_id: str, data: TaskRetryRequest | None = None):
    return {
        "task_id": await task_queue.retry_task(
            task_id,
            confirm_cloud_replay=bool(data and data.confirm_cloud_replay),
        ),
        "status": "queued",
    }


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    result = task_queue.delete_task(task_id)
    if result["status"] == "not_found":
        raise AppException(404, "TASK_NOT_FOUND", "Task not found")
    if result["status"] == "active_task":
        raise AppException(409, "TASK_ACTIVE", "Task is still active")
    return result


@router.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    task_queue.add_ws_client(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        task_queue.remove_ws_client(ws)
