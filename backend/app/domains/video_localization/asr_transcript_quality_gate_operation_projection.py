"""Public summaries for managed transcript-quality-gate operations."""

from __future__ import annotations

from typing import Any

from app.domains.video_localization import (
    asr_pipeline,
    transcript_quality_gate,
    workflow_contracts,
)
from app.domains.video_localization.timeline_timecode import (
    format_timeline_range,
)


def initial_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "准备进入校时前检查"
    summary["task_step_results"] = {
        "transcript_quality_gate": {
            **_task_definition(),
            "status": "todo",
            "summary": "等待锁定已完成的全文复核结果。",
            "metrics": [],
            "sections": [],
            "notes": [],
        }
    }
    return summary


def running_summary(segments) -> dict[str, Any]:
    summary = _base_summary()
    summary.update(
        {
            "stage": "正在进行进入校时前检查",
            "preview_phase": "text_review",
            "preview_cues": (
                asr_pipeline.transcript_segments_to_preview_cues(
                    segments
                )
            ),
            "task_step_results": {
                "transcript_quality_gate": {
                    **_task_definition(),
                    "status": "running",
                    "summary": (
                        "正在检查全文复核终态和需要保留的复听提示。"
                    ),
                    "metrics": [],
                    "sections": [],
                    "notes": [
                        "本步骤只做本地规则判断，不调用模型。",
                        "本步骤不修改字幕，也不会执行逐词时间对齐。",
                    ],
                }
            },
        }
    )
    return summary


def success_summary(
    result: transcript_quality_gate.AsrTranscriptQualityGateResult,
) -> dict[str, Any]:
    summary = _base_summary()
    hard_blockers = transcript_quality_gate.hard_alignment_blockers(
        result
    )
    summary.update(
        {
            "stage": "进入校时前检查已完成（开发单步）",
            "task_duration_ms": result.duration_ms,
            "decision": result.decision,
            "can_start_alignment": result.can_start_alignment,
            "review_target_count": len(result.review_targets),
            "blocker_count": len(hard_blockers),
            "warning_count": len(result.warnings),
            "source_track_id": result.input.source_track_id,
            "preview_phase": "text_review",
            "preview_cues": (
                asr_pipeline.transcript_segments_to_preview_cues(
                    result.input.segments
                )
            ),
            "task_stage_timings": {
                "transcript_quality_gate": {
                    "duration_ms": result.duration_ms
                }
            },
            "task_step_results": {
                "transcript_quality_gate": step_result(result)
            },
            "quality_summary": (
                result.quality_summary.model_dump(mode="json")
            ),
        }
    )
    return summary


def step_result(
    result: transcript_quality_gate.AsrTranscriptQualityGateResult,
) -> dict[str, Any]:
    decision_labels = {
        "ready_for_alignment": "可以进入校时",
        "manual_review_required": "建议人工复听",
        "failed": "上游结果未就绪",
    }
    segment_locations = {
        item.segment_id: format_timeline_range(
            item.start_ms,
            item.end_ms,
        )
        for item in result.input.segments
    }

    def issue_location(
        segment_id: str | None,
        issue_id: str | None,
    ) -> str:
        if segment_id:
            time_range = segment_locations.get(segment_id)
            return (
                f"{time_range} · {segment_id}"
                if time_range
                else segment_id
            )
        return issue_id or ""

    review_items = [
        {
            "title": item.title,
            "text": item.detail,
            "meta": item.location,
            "tone": "warning",
            "facts": (
                [
                    {
                        "label": "当前听写原文",
                        "value": item.excerpt,
                    }
                ]
                if item.excerpt
                else []
            ),
            "links": [],
        }
        for item in result.review_targets
    ]
    hard_blockers = transcript_quality_gate.hard_alignment_blockers(
        result
    )
    blocker_items = [
        {
            "title": "阻断原因",
            "text": item.message,
            "meta": issue_location(item.segment_id, item.issue_id),
            "tone": "warning",
            "facts": [],
            "links": [],
        }
        for item in hard_blockers
    ]
    review_keys = {
        (item.issue_id, item.segment_id)
        for item in result.review_targets
    }
    warning_items = [
        {
            "title": "质量提醒",
            "text": item.message,
            "meta": issue_location(item.segment_id, item.issue_id),
            "tone": "neutral",
            "facts": [],
            "links": [],
        }
        for item in result.warnings
        if not (
            item.code == "review_recommended"
            and (item.issue_id, item.segment_id) in review_keys
        )
    ]
    review_recommended = bool(
        result.review_targets
        or result.warnings
        or result.decision == "manual_review_required"
    )
    status = (
        "failed"
        if result.decision == "failed"
        else "warning"
        if review_recommended
        else "success"
    )
    reader_summary = (
        (
            f"已自动继续校时，建议复听 {len(result.review_targets)} 处。"
            if result.review_targets
            else (
                "已自动继续校时，当前最高概率文字已保留，"
                "并记录了质量提醒。"
            )
            if review_recommended
            else "文字已经稳定，可以开始生成逐词时间。"
        )
        if result.decision != "failed"
        else "检查未通过，请先重试或修复上游技术或结构问题。"
    )
    return {
        **_task_definition(),
        "status": status,
        "summary": reader_summary,
        "metrics": [
            {
                "label": "检查结论",
                "value": decision_labels[result.decision],
            },
            {
                "label": "建议复听",
                "value": str(len(result.review_targets)),
            },
            {
                "label": "阻断项",
                "value": str(len(hard_blockers)),
            },
            {
                "label": "质量提醒",
                "value": str(len(result.warnings)),
            },
            {"label": "模型调用", "value": "0"},
        ],
        "sections": [
            *(
                [{"title": "建议复听", "items": review_items}]
                if review_items
                else []
            ),
            *(
                [{"title": "阻断原因", "items": blocker_items}]
                if blocker_items
                else []
            ),
            *(
                [{"title": "质量提醒", "items": warning_items}]
                if warning_items
                else []
            ),
        ],
        "review_targets": [
            {
                "title": item.title,
                "location": item.location,
                "detail": item.detail,
                "excerpt": item.excerpt,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
            }
            for item in result.review_targets
        ],
        "notes": [
            "本步骤只做本地规则判断，没有调用语言模型。",
            "字幕文字、片段 ID 和时间码均保持不变。",
            "正式流程会自动继续逐词时间对齐；本次开发单步按断点要求停在检查结果。",
        ],
        "debug": {
            "description": "用于核对上游来源、输入输出契约和只读检查。",
            "metrics": [
                {
                    "label": "输入契约",
                    "value": result.input.contract_version,
                },
                {
                    "label": "上游契约",
                    "value": result.input.upstream_contract_version,
                },
                {
                    "label": "输出契约",
                    "value": result.contract_version,
                },
                {
                    "label": "全文复核任务",
                    "value": result.input.upstream_operation_id,
                },
                {
                    "label": "字幕文字不变",
                    "value": (
                        "是"
                        if result.quality_summary.source_text_unchanged
                        else "否"
                    ),
                },
                {
                    "label": "片段 ID 不变",
                    "value": (
                        "是"
                        if result.quality_summary.segment_ids_unchanged
                        else "否"
                    ),
                },
                {
                    "label": "时间码不变",
                    "value": (
                        "是"
                        if result.quality_summary.source_timing_unchanged
                        else "否"
                    ),
                },
                {"label": "模型调用", "value": "0"},
            ],
            "sections": [],
            "notes": [],
        },
    }


def _base_summary() -> dict[str, Any]:
    definition = (
        workflow_contracts
        .asr_transcript_quality_gate_development_workflow_summary()
    )
    return {
        "stage_id": "transcript_quality_gate",
        "execution_scope": "partial",
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def _task_definition() -> dict[str, Any]:
    task = (
        workflow_contracts
        .ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_DEFINITION
        .stages[0]
        .atomic_tasks[0]
    )
    return {
        "label": task.label,
        "order": task.order,
        "purpose": task.description,
    }


__all__ = [
    "initial_summary",
    "running_summary",
    "step_result",
    "success_summary",
]
