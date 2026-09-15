"""Path-free public projections for the source-audio workflow."""

from __future__ import annotations

from app.domains.video_localization import workflow_contracts
from app.schemas.video_localization_source_audio_step import (
    SOURCE_AUDIO_STEP_ID,
    SourceAudioStepOutputV1,
)


def workflow_summary_fields() -> dict:
    definition = workflow_contracts.source_audio_workflow_summary()
    return {
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def initial_summary() -> dict:
    task = _task_definition()
    return {
        "stage": "准备提取原始音轨",
        "stage_id": SOURCE_AUDIO_STEP_ID,
        "task_step_results": {
            SOURCE_AUDIO_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": "todo",
            }
        },
        **workflow_summary_fields(),
    }


def success_summary(
    output: SourceAudioStepOutputV1,
) -> dict:
    task = _task_definition()
    metrics = media_metrics(output)
    return {
        "stage": "原始音轨已提取并保存",
        "stage_id": SOURCE_AUDIO_STEP_ID,
        **output.model_dump(
            mode="json",
            exclude={"schema_version"},
        ),
        **workflow_summary_fields(),
        "task_step_results": {
            SOURCE_AUDIO_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": "success",
                "purpose": task.description,
                "summary": "已提取并保存可供后续流程使用的原始音轨。",
                "metrics": metrics,
            }
        },
        "task_final_result": {
            "status": "success",
            "summary": "原始音轨已提取并保存。",
            "metrics": metrics,
            "sections": [],
            "notes": [],
        },
    }


def media_metrics(
    output: SourceAudioStepOutputV1,
) -> list[dict[str, str]]:
    return [
        {
            "label": "时长",
            "value": f"{output.duration_ms / 1000:g} 秒",
        },
        {
            "label": "采样率",
            "value": f"{output.sample_rate / 1000:g} kHz",
        },
        {"label": "声道", "value": str(output.channels)},
    ]


def _task_definition():
    return (
        workflow_contracts
        .SOURCE_AUDIO_WORKFLOW_DEFINITION
        .stages[0]
        .atomic_tasks[0]
    )


__all__ = [
    "initial_summary",
    "media_metrics",
    "success_summary",
    "workflow_summary_fields",
]
