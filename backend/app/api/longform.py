from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.errors import AppException
from app.schemas.voice_studio import LongformGenerateRequest, LongformTask
from app.services import longform_queue

router = APIRouter()


@router.get("", response_model=list[LongformTask])
async def list_longform_tasks():
    return longform_queue.list_tasks()


@router.post("/generate", response_model=LongformTask)
async def generate_longform(req: LongformGenerateRequest):
    return await longform_queue.submit(req)


@router.get("/{longform_task_id}", response_model=LongformTask)
async def get_longform_task(longform_task_id: str):
    task = longform_queue.get_task(longform_task_id)
    if not task:
        raise AppException(404, "LONGFORM_TASK_NOT_FOUND", "Longform task not found")
    return task


@router.post("/{longform_task_id}/retry-failed", response_model=LongformTask)
async def retry_failed_segments(longform_task_id: str):
    return await longform_queue.retry_failed(longform_task_id)


@router.post("/{longform_task_id}/cancel")
async def cancel_longform_task(longform_task_id: str):
    return longform_queue.cancel_longform(longform_task_id)


@router.post("/{longform_task_id}/segments/{segment_index}/cancel")
async def cancel_longform_segment(longform_task_id: str, segment_index: int):
    return longform_queue.cancel_longform_segment(longform_task_id, segment_index)


@router.delete("/{longform_task_id}")
async def dismiss_longform_task(longform_task_id: str):
    return longform_queue.dismiss_longform(longform_task_id)


@router.get("/{longform_task_id}/download")
async def download_longform_export(longform_task_id: str):
    task = longform_queue.get_task(longform_task_id)
    if not task:
        raise AppException(404, "LONGFORM_TASK_NOT_FOUND", "Longform task not found")
    if not task.export_path:
        raise AppException(404, "LONGFORM_EXPORT_NOT_FOUND", "Longform task has no merged export")
    return FileResponse(task.export_path)
