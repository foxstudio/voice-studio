"""Public summaries for managed section-review operations."""

from __future__ import annotations

from typing import Any

from app.domains.video_localization import (
    llm_observability,
    section_review,
    workflow_contracts,
)
from app.domains.video_localization.timeline_timecode import (
    format_timeline_range,
)


def initial_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "准备第 1 轮分段复查"
    summary["task_step_results"] = {
        "section_review_r1": {
            **_task_definition(),
            "status": "todo",
            "summary": "等待锁定名称统一和全文理解结果。",
            "metrics": [],
            "sections": [],
            "notes": [],
        }
    }
    return summary


def running_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "正在进行第 1 轮分段复查"
    summary["task_step_results"] = {
        "section_review_r1": {
            **_task_definition(),
            "status": "running",
            "summary": "正在结合全文理解、前后文和查证资料检查各个区块。",
            "metrics": [],
            "sections": [],
            "notes": [
                "这一步只提出疑点，不修改字幕文字或时间码。",
                "服务恢复时会复用已经提交的区块结果。",
            ],
        }
    }
    return summary


def success_summary(
    result: section_review.AsrSectionReviewResult,
    *,
    step_result: dict[str, Any],
) -> dict[str, Any]:
    duration_ms = result.duration_ms
    failed_runs = [
        item for item in result.section_runs
        if item.status == "failed"
    ]
    summary = _base_summary()
    summary.update(
        {
            "stage": "第 1 轮分段复查已完成（开发单步）",
            "task_duration_ms": duration_ms,
            "checked_section_count": (
                result.quality_summary.checked_section_count
            ),
            "failed_section_count": len(failed_runs),
            "issue_count": len(result.issues),
            "source_track_id": result.input.source_track_id,
            "llm_profile_id": result.profile_id,
            "llm_model_id": result.model_id,
            "task_stage_timings": {
                "section_review_r1": {
                    "duration_ms": duration_ms
                }
            },
            "task_step_results": {
                "section_review_r1": step_result
            },
            "quality_summary": (
                result.quality_summary.model_dump(mode="json")
            ),
        }
    )
    return summary


def step_result(
    result: section_review.AsrSectionReviewResult,
) -> dict[str, Any]:
    failed_runs = [
        item for item in result.section_runs
        if item.status == "failed"
    ]
    llm_items, llm_metrics = (
        llm_observability.project_llm_calls(
            result.llm_calls
        )
    )
    segment_by_id = {
        item.segment_id: item for item in result.input.segments
    }
    issue_items = []
    for item in result.issues[:16]:
        targets = [
            segment_by_id[segment_id]
            for segment_id in item.target_segment_ids
            if segment_id in segment_by_id
        ]
        segment = (
            targets[0]
            if targets
            else segment_by_id.get(item.segment_id)
        )
        time_label = (
            format_timeline_range(
                segment.start_ms,
                targets[-1].end_ms if targets else segment.end_ms,
            )
            if segment is not None
            else "时间位置未知"
        )
        issue_items.append(
            {
                "title": (
                    f"{'、'.join(item.target_segment_ids) or item.segment_id}"
                    f" · {time_label}"
                ),
                "text": item.reason,
                "meta": (
                    f"原文：{item.current_excerpt}"
                    + (
                        f" → 建议：{item.proposed_replacement}"
                        if item.proposed_replacement
                        else " · 建议人工听一下"
                    )
                ),
                "tone": (
                    "warning"
                    if item.needs_confirmation
                    else "neutral"
                ),
                "facts": [
                    {
                        "label": "可信度",
                        "value": f"{item.confidence:.0%}",
                    },
                    {
                        "label": "查证资料",
                        "value": str(len(item.evidence_source_ids)),
                    },
                    {
                        "label": "发现方式",
                        "value": _origin_label(item.origin),
                    },
                ],
                "links": [],
            }
        )
    section_items = [
        {
            "title": item.section_id,
            "text": (
                f"发现 {item.issue_count} 个可能问题。"
                if item.status == "completed"
                else item.error_message or "这个区块没有完成。"
            ),
            "meta": f"{item.duration_ms / 1000:.1f} 秒",
            "tone": (
                "positive"
                if item.status == "completed"
                else "warning"
            ),
            "facts": [
                {
                    "label": "状态",
                    "value": (
                        "已检查"
                        if item.status == "completed"
                        else "未完成"
                    ),
                },
                {
                    "label": "模型请求",
                    "value": str(len(item.llm_call_ids)),
                },
            ],
            "links": [],
        }
        for item in result.section_runs
    ]
    return {
        **_task_definition(),
        "status": (
            "warning"
            if result.status in {"partial", "failed"}
            else "success"
        ),
        "summary": (
            f"已检查 {result.quality_summary.checked_section_count}/"
            f"{result.quality_summary.section_count} 个区块，"
            f"找出 {len(result.issues)} 个可能问题。"
        ),
        "metrics": [
            {
                "label": "已检查区块",
                "value": (
                    f"{result.quality_summary.checked_section_count}/"
                    f"{result.quality_summary.section_count}"
                ),
            },
            {
                "label": "可能问题",
                "value": str(len(result.issues)),
            },
            {
                "label": "未完成区块",
                "value": str(len(failed_runs)),
            },
            {
                "label": "模型请求",
                "value": str(len(result.llm_calls)),
            },
        ],
        "sections": [
            *(
                [{"title": "可能的听写问题", "items": issue_items}]
                if issue_items
                else []
            ),
            {"title": "区块检查", "items": section_items},
        ],
        "notes": [
            "这一步没有修改字幕文字或时间码。",
            "下一步会把这些疑点放回全文，决定接受、拒绝或保留原文。",
            *[item.message for item in result.warnings],
        ],
        "debug": {
            "description": "用于确认输入来源、覆盖范围、模型调用和失败区块。",
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
                    "label": "名称统一任务",
                    "value": result.input.upstream_operation_id,
                },
                {
                    "label": "全文理解任务",
                    "value": result.input.understanding_operation_id,
                },
                {
                    "label": "来源音轨",
                    "value": result.input.source_track_id,
                },
                {
                    "label": "音频指纹",
                    "value": result.input.source_audio_sha256[:12],
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


def _origin_label(value: str) -> str:
    return {
        "deterministic_boundary_rule": "本地跨段检查",
        "deterministic_text_rule": "本地句内检查",
    }.get(value, "模型分段复查")


def _base_summary() -> dict[str, Any]:
    definition = (
        workflow_contracts
        .asr_section_review_development_workflow_summary()
    )
    return {
        "stage_id": "section_review_r1",
        "execution_scope": "partial",
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def _task_definition() -> dict[str, Any]:
    task = (
        workflow_contracts
        .ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_DEFINITION
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
