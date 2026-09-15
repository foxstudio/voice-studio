"""Path-free public projections for automatic reference candidates."""

from __future__ import annotations

from app.domains.video_localization import workflow_contracts
from app.schemas.video_localization_reference_candidates_step import (
    REFERENCE_CANDIDATES_STEP_ID,
    ReferenceCandidatesStepOutputV1,
)


def workflow_summary_fields() -> dict:
    definition = (
        workflow_contracts.reference_candidates_workflow_summary()
    )
    return {
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def initial_summary() -> dict:
    task = _task_definition()
    return {
        "stage": "准备生成参考音候选",
        "stage_id": REFERENCE_CANDIDATES_STEP_ID,
        "task_step_results": {
            REFERENCE_CANDIDATES_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": "todo",
            }
        },
        **workflow_summary_fields(),
    }


def success_summary(
    output: ReferenceCandidatesStepOutputV1,
) -> dict:
    task = _task_definition()
    metrics = media_metrics(output)
    return {
        "stage": "参考音候选已生成并保存",
        "stage_id": REFERENCE_CANDIDATES_STEP_ID,
        "reference_clip_count": output.candidate_clip_count,
        "generated_clip_count": output.generated_clip_count,
        "linked_cue_count": output.linked_cue_count,
        "media_status": output.media_status,
        **workflow_summary_fields(),
        "task_step_results": {
            REFERENCE_CANDIDATES_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": "success",
                "purpose": task.description,
                "summary": (
                    f"已准备 {output.candidate_clip_count} 个"
                    "可人工复核的参考音候选。"
                ),
                "metrics": metrics,
            }
        },
        "task_final_result": {
            "status": "success",
            "summary": (
                f"已生成并关联 {output.candidate_clip_count} 个"
                "参考音候选。"
            ),
            "metrics": metrics,
            "sections": [],
            "notes": [],
        },
    }


def media_metrics(
    output: ReferenceCandidatesStepOutputV1,
) -> list[dict[str, str]]:
    return [
        {
            "label": "候选参考音",
            "value": str(output.candidate_clip_count),
        },
        {
            "label": "本次新生成",
            "value": str(output.generated_clip_count),
        },
        {
            "label": "已关联字幕",
            "value": str(output.linked_cue_count),
        },
    ]


def _task_definition():
    return (
        workflow_contracts
        .REFERENCE_CANDIDATES_WORKFLOW_DEFINITION
        .stages[0]
        .atomic_tasks[0]
    )


__all__ = [
    "initial_summary",
    "media_metrics",
    "success_summary",
    "workflow_summary_fields",
]
