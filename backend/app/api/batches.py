from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from app.errors import AppException
from app.schemas.voice_studio import BatchTask
from app.services import batch_queue

router = APIRouter()


@router.get("", response_model=list[BatchTask])
async def list_batches():
    return batch_queue.list_batches()


@router.post("/generate", response_model=BatchTask)
async def generate_batch(payload: Any = Body(...)):
    try:
        return await batch_queue.submit(payload)
    except ValueError as exc:
        code, separator, message = str(exc).partition(": ")
        if code.startswith("COSYVOICE_"):
            raise AppException(400, code, message if separator else "CosyVoice Zero-Shot 参考音频不符合官方要求") from exc
        if code.startswith("EMOTION_REFERENCE_"):
            raise AppException(400, code, message if separator else "独立情绪参考参数无效") from exc
        raise AppException(400, "BATCH_PAYLOAD_INVALID", str(exc)) from exc


@router.get("/{batch_task_id}", response_model=BatchTask)
async def get_batch(batch_task_id: str):
    batch = batch_queue.get_batch(batch_task_id)
    if not batch:
        raise AppException(404, "BATCH_NOT_FOUND", "Batch task not found")
    return batch
