from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.errors import AppException
from app.schemas.voice_studio import LongformGenerateRequest, LongformTask
from app.services import longform_queue

router = APIRouter()


class LongformRetryRequest(BaseModel):
    confirm_cloud_replay: bool = False


@router.get("", response_model=list[LongformTask])
def list_longform_tasks(
    include_completed: bool = True,
    limit: int = Query(100, ge=1, le=100),
):
    return longform_queue.list_tasks(include_completed=include_completed, limit=limit)


@router.post("/generate", response_model=LongformTask)
async def generate_longform(req: LongformGenerateRequest):
    if req.generate_request.source == "video_localization" and req.generate_request.bind_to_video_localization:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_LONGFORM_BINDING_UNSUPPORTED",
            "关联视频本土化的配音暂不支持长文本分段，请缩短字幕或取消关联后生成",
        )
    return await longform_queue.submit(req)


@router.get("/{longform_task_id}", response_model=LongformTask)
def get_longform_task(longform_task_id: str):
    task = longform_queue.get_task(longform_task_id)
    if not task:
        raise AppException(404, "LONGFORM_TASK_NOT_FOUND", "Longform task not found")
    return task


@router.post("/{longform_task_id}/retry-failed", response_model=LongformTask)
async def retry_failed_segments(
    longform_task_id: str,
    data: LongformRetryRequest | None = None,
):
    return await longform_queue.retry_failed(
        longform_task_id,
        confirm_cloud_replay=bool(
            data and data.confirm_cloud_replay
        ),
    )


@router.post("/{longform_task_id}/cancel")
def cancel_longform_task(longform_task_id: str):
    return longform_queue.cancel_longform(longform_task_id)


@router.post("/{longform_task_id}/segments/{segment_index}/cancel")
def cancel_longform_segment(longform_task_id: str, segment_index: int):
    return longform_queue.cancel_longform_segment(longform_task_id, segment_index)


@router.delete("/{longform_task_id}")
def dismiss_longform_task(longform_task_id: str):
    return longform_queue.dismiss_longform(longform_task_id)


@router.get("/{longform_task_id}/download")
def download_longform_export(longform_task_id: str):
    task = longform_queue.get_task(longform_task_id)
    if not task:
        raise AppException(404, "LONGFORM_TASK_NOT_FOUND", "Longform task not found")
    if not task.export_path:
        raise AppException(404, "LONGFORM_EXPORT_NOT_FOUND", "Longform task has no merged export")
    return FileResponse(task.export_path)
