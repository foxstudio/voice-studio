from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from app.errors import AppException
from app.schemas.voice_studio import GeneratePlanRequest, GeneratePlanResponse, GenerateRequest, GenerateResponse
from app.services import (
    task_queue,
    text_planner,
    video_localization_tts_handoff,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/plan", response_model=GeneratePlanResponse)
async def generate_plan(req: GeneratePlanRequest):
    return text_planner.plan_text(
        text=req.text,
        engine_id=req.engine_id,
        planner_mode=req.planner_mode,
        target_format=req.target_format,
    )


@router.post(
    "",
    response_model=GenerateResponse,
    response_model_exclude_none=True,
)
async def generate(req: GenerateRequest):
    if text_planner.requires_longform_generation(
        text=req.text,
        engine_id=req.engine_id,
        target_format=req.output_format,
    ):
        raise AppException(
            409,
            "LONGFORM_REQUIRED",
            "CosyVoice 单条台词过长，容易只生成前半段。请使用长文本分段生成并开启校对。",
            {"engine_id": req.engine_id},
        )
    try:
        req = await asyncio.to_thread(
            video_localization_tts_handoff.finalize_submission,
            req,
        )
    except Exception as exc:
        if req.video_localization_workflow_id:
            try:
                await asyncio.to_thread(
                    video_localization_tts_handoff.mark_workflow_terminal,
                    req,
                    status="failed",
                    error_message=(
                        exc.message
                        if isinstance(exc, AppException)
                        else "配音任务提交失败，请重试"
                    ),
                    source_id=req.video_localization_workflow_id,
                )
            except Exception:
                logger.exception(
                    "Failed to terminalize TTS workflow after submission validation failure",
                    extra={
                        "project_id": req.project_id,
                        "workflow_id": req.video_localization_workflow_id,
                    },
                )
        raise
    try:
        task_id = await task_queue.submit(
            req,
            project_id=req.project_id,
            segment_id=req.segment_id,
        )
    except Exception:
        if req.video_localization_workflow_id:
            try:
                video_localization_tts_handoff.mark_workflow_terminal(
                    req,
                    status="failed",
                    error_message="配音任务未能进入生成队列，请重试",
                    source_id=req.video_localization_workflow_id,
                )
            except Exception:
                logger.exception(
                    "Failed to terminalize TTS workflow after queue submission failure",
                    extra={
                        "project_id": req.project_id,
                        "workflow_id": req.video_localization_workflow_id,
                    },
                )
        raise
    return GenerateResponse(
        task_id=task_id,
        video_localization_workflow_id=req.video_localization_workflow_id,
    )
