"""语音生成 API"""

from fastapi import APIRouter

from app.models.schemas import GenerateRequest, GenerateResponse
from app.services import task_queue

router = APIRouter()


@router.post("", response_model=GenerateResponse)
async def generate_speech(req: GenerateRequest):
    task_id = await task_queue.submit(req)
    return GenerateResponse(task_id=task_id, status="queued")
