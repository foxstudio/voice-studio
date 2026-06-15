from __future__ import annotations

from fastapi import APIRouter

from app.schemas.voice_studio import GeneratePlanRequest, GeneratePlanResponse, GenerateRequest, GenerateResponse
from app.services import task_queue, text_planner

router = APIRouter()


@router.post("/plan", response_model=GeneratePlanResponse)
async def generate_plan(req: GeneratePlanRequest):
    return text_planner.plan_text(
        text=req.text,
        engine_id=req.engine_id,
        planner_mode=req.planner_mode,
        target_format=req.target_format,
    )


@router.post("", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    task_id = await task_queue.submit(req, project_id=req.project_id, segment_id=req.segment_id)
    return GenerateResponse(task_id=task_id)
