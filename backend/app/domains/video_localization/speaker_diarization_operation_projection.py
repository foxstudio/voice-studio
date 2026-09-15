"""Path-free projections for standalone speaker diarization."""

from __future__ import annotations

from app.domains.video_localization import workflow_contracts
from app.schemas.video_localization_speaker_diarization_step import (
    SPEAKER_DIARIZATION_STEP_ID,
    SpeakerDiarizationResultV1,
    SpeakerDiarizationStepOutputV1,
)


def workflow_summary_fields() -> dict:
    definition = (
        workflow_contracts
        .speaker_diarization_workflow_summary()
    )
    return {
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def initial_summary() -> dict:
    task = _task_definition()
    return {
        "stage": "准备区分说话人",
        "stage_id": SPEAKER_DIARIZATION_STEP_ID,
        "execution_scope": "partial",
        "task_step_results": {
            SPEAKER_DIARIZATION_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": "todo",
            }
        },
        **workflow_summary_fields(),
    }


def running_summary() -> dict:
    task = _task_definition()
    return {
        "stage": "正在区分说话人",
        "stage_id": SPEAKER_DIARIZATION_STEP_ID,
        "execution_scope": "partial",
        "task_step_results": {
            SPEAKER_DIARIZATION_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": "running",
                "purpose": task.description,
                "summary": "正在分析声音特征并校验匿名说话人分组。",
            }
        },
        **workflow_summary_fields(),
    }


def success_summary(
    output: SpeakerDiarizationStepOutputV1,
) -> dict:
    result = output.result
    task = _task_definition()
    metrics = result_metrics(result)
    return {
        "stage": "说话人区分已完成（开发单步）",
        "stage_id": SPEAKER_DIARIZATION_STEP_ID,
        "execution_scope": "partial",
        "speaker_count": len(result.clusters),
        "diarization_status": result.status,
        "count_guidance": result.count_guidance.model_dump(
            mode="json"
        ),
        "quality_summary": result.quality_summary.model_dump(
            mode="json"
        ),
        "task_duration_ms": result.stage_timing.duration_ms,
        "sample": result_sample(result),
        **workflow_summary_fields(),
        "task_step_results": {
            SPEAKER_DIARIZATION_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": (
                    "success"
                    if result.quality_summary.status == "passed"
                    else "warning"
                ),
                "purpose": task.description,
                "summary": (
                    f"已识别 {len(result.clusters)} 个匿名说话人，"
                    f"生成 {len(result.segments)} 个声音片段。"
                ),
                "metrics": metrics,
                "notes": list(result.quality_flags),
            }
        },
        "task_final_result": {
            "status": (
                "success"
                if result.quality_summary.status == "passed"
                else "warning"
            ),
            "summary": (
                f"已完成匿名说话人区分，共识别 "
                f"{len(result.clusters)} 个声音角色。"
            ),
            "metrics": metrics,
            "sections": [],
            "notes": list(result.quality_flags),
        },
    }


def result_metrics(
    result: SpeakerDiarizationResultV1,
) -> list[dict[str, str]]:
    return [
        {
            "label": "匿名说话人",
            "value": str(len(result.clusters)),
        },
        {
            "label": "声音片段",
            "value": str(len(result.segments)),
        },
        {
            "label": "重叠讲话",
            "value": str(
                result.quality_summary.overlap_segment_count
            ),
        },
    ]


def result_sample(
    result: SpeakerDiarizationResultV1,
) -> dict:
    return {
        "clusters": [
            {
                "cluster_id": cluster.cluster_id,
                "start_ms": cluster.start_ms,
                "end_ms": cluster.end_ms,
                "duration_ms": cluster.duration_ms,
                "segment_count": cluster.segment_count,
                "merge_status": cluster.merge_status,
            }
            for cluster in result.clusters
        ],
        "segments": [
            {
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "speaker_cluster_id": (
                    segment.speaker_cluster_id
                ),
                "confidence": segment.confidence,
                "has_speaker_overlap": (
                    segment.has_speaker_overlap
                ),
            }
            for segment in _bounded_segments(
                result.segments,
                limit=8,
            )
        ],
    }


def _bounded_segments(segments: tuple, *, limit: int) -> tuple:
    if len(segments) <= limit:
        return segments
    return tuple(
        segments[
            min(
                len(segments) - 1,
                index * len(segments) // limit,
            )
        ]
        for index in range(limit)
    )


def _task_definition():
    return (
        workflow_contracts
        .SPEAKER_DIARIZATION_WORKFLOW_DEFINITION
        .stages[0]
        .atomic_tasks[0]
    )


__all__ = [
    "initial_summary",
    "result_metrics",
    "result_sample",
    "running_summary",
    "success_summary",
    "workflow_summary_fields",
]
