"""Public summaries for managed ASR whole-recheck operations."""

from __future__ import annotations

from typing import Any

from app.domains.video_localization import (
    asr_pipeline,
    llm_observability,
    whole_recheck,
    workflow_contracts,
)
from app.domains.video_localization.timeline_timecode import (
    format_timeline_range,
)


def initial_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "准备本地收尾检查"
    summary["task_step_results"] = {
        "whole_recheck_r1": {
            **_task_definition(),
            "status": "todo",
            "summary": "等待锁定修改汇总与全文理解结果。",
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
            "stage": "正在进行本地收尾检查",
            "preview_phase": "text_review",
            "preview_cues": (
                asr_pipeline.transcript_segments_to_preview_cues(
                    segments
                )
            ),
            "task_step_results": {
                "whole_recheck_r1": {
                    **_task_definition(),
                    "status": "running",
                    "summary": (
                        "正在按本地规则检查修改结果和剩余问题。"
                    ),
                    "metrics": [],
                    "sections": [],
                    "notes": [
                        "本步骤不会修改字幕文字、片段 ID 或时间码。",
                        "本步骤不调用模型。",
                    ],
                }
            },
        }
    )
    return summary


def success_summary(
    result: whole_recheck.AsrWholeRecheckResult,
) -> dict[str, Any]:
    summary = _base_summary()
    summary.update(
        {
            "stage": "本地收尾检查已完成（开发单步）",
            "task_duration_ms": result.duration_ms,
            "next_action": result.next_action,
            "next_section_count": len(result.next_sections),
            "unresolved_item_count": len(result.unresolved_items),
            "source_track_id": result.input.source_track_id,
            "llm_profile_id": result.profile_id,
            "llm_model_id": result.model_id,
            "preview_phase": "text_review",
            "preview_cues": (
                asr_pipeline.transcript_segments_to_preview_cues(
                    result.input.segments
                )
            ),
            "task_stage_timings": {
                "whole_recheck_r1": {
                    "duration_ms": result.duration_ms
                }
            },
            "task_step_results": {
                "whole_recheck_r1": step_result(result)
            },
            "quality_summary": (
                result.quality_summary.model_dump(mode="json")
            ),
        }
    )
    return summary


def step_result(
    result: whole_recheck.AsrWholeRecheckResult,
) -> dict[str, Any]:
    llm_items, llm_metrics = llm_observability.project_llm_calls(
        result.llm_calls,
        calls_complete=True,
    )
    action_labels = {
        "finish": "可以结束",
        "review_next_round": "进入下一轮",
        "manual_review": "自动结束复查并建议复听",
    }
    next_sections = [
        {
            "title": (
                f"{item.role} · "
                + format_timeline_range(
                    result.input.segments[
                        item.start_ordinal - 1
                    ].start_ms,
                    result.input.segments[
                        item.end_ordinal - 1
                    ].end_ms,
                )
            ),
            "text": "；".join(item.focus),
            "meta": (
                f"片段 {item.start_segment_id} 至 "
                f"{item.end_segment_id}"
            ),
            "tone": "neutral",
            "facts": [],
            "links": [],
        }
        for item in result.next_sections
    ]
    unresolved = whole_recheck.reader_unresolved_items(result)[:16]
    return {
        **_task_definition(),
        "status": (
            "success"
            if result.next_action == "finish"
            else "warning"
        ),
        "summary": result.summary,
        "metrics": [
            {
                "label": "复核结论",
                "value": action_labels[result.next_action],
            },
            {
                "label": "下一轮分段",
                "value": str(len(result.next_sections)),
            },
            {
                "label": "仍需确认",
                "value": str(len(result.unresolved_items)),
            },
            {"label": "模型请求", "value": str(len(result.llm_calls))},
        ],
        "sections": [
            *(
                [{"title": "下一轮检查计划", "items": next_sections}]
                if next_sections
                else []
            ),
            *(
                [{"title": "仍需确认", "items": unresolved}]
                if unresolved
                else []
            ),
        ],
        "notes": [
            "本步骤只读，没有修改字幕文字、片段 ID 或时间码。",
            *result.warnings,
        ],
        "debug": {
            "description": "用于核对输入来源、只读契约、模型调用和用量。",
            "metrics": [
                {
                    "label": "修改汇总任务",
                    "value": result.input.upstream_operation_id,
                },
                {
                    "label": "全文理解任务",
                    "value": result.input.understanding_operation_id,
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
                    "label": "时间码不变",
                    "value": (
                        "是"
                        if result.quality_summary.source_timing_unchanged
                        else "否"
                    ),
                },
                {"label": "模型配置", "value": result.profile_id},
                {
                    "label": "实际模型",
                    "value": result.model_id or "未返回",
                },
                *llm_metrics,
            ],
            "sections": (
                [{"title": "模型调用明细", "items": llm_items}]
                if llm_items
                else []
            ),
            "notes": [
                "这里只记录模型、Token、费用和结束原因；"
                "不保存完整提问、回答正文或隐藏思考。"
            ],
        },
    }


def _base_summary() -> dict[str, Any]:
    definition = (
        workflow_contracts
        .asr_whole_recheck_development_workflow_summary()
    )
    return {
        "stage_id": "whole_recheck_r1",
        "execution_scope": "partial",
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def _task_definition() -> dict[str, Any]:
    task = (
        workflow_contracts
        .ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_DEFINITION
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
