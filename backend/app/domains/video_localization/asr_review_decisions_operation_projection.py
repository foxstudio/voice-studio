"""Public summaries for managed ASR review-decisions operations."""

from __future__ import annotations

from typing import Any

from app.domains.video_localization import (
    asr_pipeline,
    llm_observability,
    review_decisions,
    workflow_contracts,
)


def initial_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "准备汇总第 1 轮修改"
    summary["task_step_results"] = {
        "review_decisions_r1": {
            **_task_definition(),
            "status": "todo",
            "summary": "等待锁定已完成的分段复查结果。",
            "metrics": [],
            "sections": [],
            "notes": [],
        }
    }
    return summary


def running_summary(
    segments,
) -> dict[str, Any]:
    summary = _base_summary()
    summary.update(
        {
            "stage": "正在汇总第 1 轮修改",
            "preview_phase": "text_review",
            "preview_cues": (
                asr_pipeline.transcript_segments_to_preview_cues(
                    segments
                )
            ),
            "task_step_results": {
                "review_decisions_r1": {
                    **_task_definition(),
                    "status": "running",
                    "summary": (
                        "正在结合完整上下文、查证资料和已锁定修改逐条判断。"
                    ),
                    "metrics": [],
                    "sections": [],
                    "notes": [
                        "运行预览只用于当前页面，不写入项目持久数据。",
                        "已经确认的模型请求会被复用，不会在恢复时重复发送。",
                    ],
                }
            },
        }
    )
    return summary


def success_summary(
    result: review_decisions.AsrReviewDecisionsResult,
) -> dict[str, Any]:
    summary = _base_summary()
    summary.update(
        {
            "stage": "汇总第 1 轮修改已完成（开发单步）",
            "task_duration_ms": result.duration_ms,
            "decision_count": len(result.decisions),
            "applied_change_count": len(result.changes),
            "unresolved_issue_count": (
                result.quality_summary.unresolved_issue_count
            ),
            "source_track_id": result.input.source_track_id,
            "llm_profile_id": result.profile_id,
            "llm_model_id": result.model_id,
            "preview_phase": "text_review",
            "preview_cues": (
                asr_pipeline.transcript_segments_to_preview_cues(
                    result.updated_segments
                )
            ),
            "task_stage_timings": {
                "review_decisions_r1": {
                    "duration_ms": result.duration_ms
                }
            },
            "task_step_results": {
                "review_decisions_r1": step_result(result)
            },
            "quality_summary": (
                result.quality_summary.model_dump(mode="json")
            ),
        }
    )
    return summary


def step_result(
    result: review_decisions.AsrReviewDecisionsResult,
) -> dict[str, Any]:
    llm_items, llm_metrics = llm_observability.project_llm_calls(
        result.llm_calls,
        calls_complete=True,
    )
    rejected_count = sum(
        item.outcome == "rejected" for item in result.decisions
    )
    unresolved_count = result.quality_summary.unresolved_issue_count
    change_items = [
        {
            "title": "已修正一处听写",
            "text": item.reason,
            "before": item.before,
            "after": item.after,
            "before_label": "修改前",
            "after_label": "修改后",
            "tone": "positive",
            "facts": [
                {
                    "label": "把握度",
                    "value": f"{item.confidence:.0%}",
                }
            ],
            "links": [],
        }
        for item in result.changes[:12]
    ]
    return {
        **_task_definition(),
        "status": (
            "warning"
            if result.status == "partial"
            else "success"
        ),
        "summary": (
            f"已判断 {len(result.decisions)} 条疑点，"
            f"采纳 {len(result.changes)} 处，"
            f"保留原文 {rejected_count} 处，"
            f"另有 {unresolved_count} 处需要复核。"
        ),
        "metrics": [
            {"label": "已判断", "value": str(len(result.decisions))},
            {"label": "实际修改", "value": str(len(result.changes))},
            {"label": "保留原文", "value": str(rejected_count)},
            {"label": "需要复核", "value": str(unresolved_count)},
            {"label": "模型请求", "value": str(len(result.llm_calls))},
        ],
        "sections": (
            [{"title": "实际修改", "items": change_items}]
            if change_items
            else []
        ),
        "notes": [
            "只处理了上一任务明确提出的疑点，没有增加新修改。",
            "片段 ID 和时间码保持不变。",
            *[item.message for item in result.warnings],
        ],
        "debug": {
            "description": "用于核对输入来源、安全规则、模型调用和用量。",
            "metrics": [
                {
                    "label": "分段复查任务",
                    "value": result.input.upstream_operation_id,
                },
                {"label": "模型配置", "value": result.profile_id},
                {
                    "label": "实际模型",
                    "value": result.model_id or "未调用",
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
        .asr_review_decisions_development_workflow_summary()
    )
    return {
        "stage_id": "review_decisions_r1",
        "execution_scope": "partial",
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def _task_definition() -> dict[str, Any]:
    task = (
        workflow_contracts
        .ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_DEFINITION
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
