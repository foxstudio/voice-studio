"""Path-free public projections for managed stem separation."""

from __future__ import annotations

from app.domains.video_localization import workflow_contracts
from app.schemas.video_localization_stem_separation_step import (
    STEM_SEPARATION_STEP_ID,
    StemSeparationStepOutputV1,
)


def workflow_summary_fields() -> dict:
    definition = (
        workflow_contracts.stem_separation_workflow_summary()
    )
    return {
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def initial_summary() -> dict:
    task = _task_definition()
    return {
        "stage": "准备分离人声与背景声",
        "stage_id": STEM_SEPARATION_STEP_ID,
        "task_step_results": {
            STEM_SEPARATION_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": "todo",
            }
        },
        **workflow_summary_fields(),
    }


def success_summary(
    output: StemSeparationStepOutputV1,
) -> dict:
    task = _task_definition()
    metrics = media_metrics(output)
    public_output = output.model_dump(
        mode="json",
        exclude={
            "schema_version",
            "source_audio_sha256",
            "vocals_clean_sha256",
            "background_sha256",
        },
    )
    return {
        "stage": "人声与背景声已分离并保存",
        "stage_id": STEM_SEPARATION_STEP_ID,
        **public_output,
        **workflow_summary_fields(),
        "task_step_results": {
            STEM_SEPARATION_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": "success",
                "purpose": task.description,
                "summary": "已生成并保存可供后续流程使用的人声和背景声。",
                "metrics": metrics,
            }
        },
        "task_final_result": {
            "status": "success",
            "summary": "人声与背景声已分离并保存。",
            "metrics": metrics,
            "sections": [],
            "notes": list(output.quality_flags),
        },
    }


def media_metrics(
    output: StemSeparationStepOutputV1,
) -> list[dict[str, str]]:
    return [
        {"label": "分离音轨", "value": "2"},
        {
            "label": "分离引擎",
            "value": output.separation_engine_id,
        },
        {
            "label": "质量提醒",
            "value": str(len(output.quality_flags)),
        },
    ]


def _task_definition():
    return (
        workflow_contracts
        .STEM_SEPARATION_WORKFLOW_DEFINITION
        .stages[0]
        .atomic_tasks[0]
    )


__all__ = [
    "initial_summary",
    "media_metrics",
    "success_summary",
    "workflow_summary_fields",
]
