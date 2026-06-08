from __future__ import annotations

from fastapi import APIRouter

from app.models.schemas import GeneratePlanRequest, GeneratePlanResponse, GenerateRequest, GenerateResponse
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
    task_id = await task_queue.submit(req)
    return GenerateResponse(task_id=task_id)
