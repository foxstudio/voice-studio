"""Public summaries for the managed research-evidence breakpoint."""

from __future__ import annotations

from typing import Any

from app.domains.video_localization import (
    llm_observability,
    research_evidence,
    workflow_contracts,
)


_CATEGORY_LABELS = {
    "proper_noun": "专有名称",
    "background": "背景资料",
    "culture": "文化语境",
    "persona": "人物身份",
}
_STOP_LABELS = {
    "no_candidates": "没有待查问题",
    "evidence_sufficient": "现有资料已够下一步判断",
    "no_progress": "继续搜索没有新增有效方向",
    "max_rounds_reached": "已达到最多搜索轮次",
    "query_budget_exhausted": "已达到最多查询次数",
    "evaluator_unavailable": "模型未能判断是否继续",
    "search_unavailable": "搜索服务不可用",
}
_OUTCOME_LABELS = {
    "supported": "找到相关资料",
    "unresolved_no_evidence": "暂未找到有效资料",
    "failed": "查询失败",
}


def initial_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "准备查询资料"
    summary["task_step_results"] = {
        "research": {
            **_task_definition(),
            "status": "todo",
            "summary": "等待锁定全文理解和可选画面证据。",
            "metrics": [],
            "sections": [],
            "notes": [],
        }
    }
    return summary


def running_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "正在查询资料"
    summary["task_step_results"] = {
        "research": {
            **_task_definition(),
            "status": "running",
            "summary": "正在围绕全文理解提出的疑点查询并判断证据是否足够。",
            "metrics": [],
            "sections": [],
            "notes": [
                "本步骤不会决定规范名称，也不会修改听写文字。"
            ],
        }
    }
    return summary


def success_summary(
    result: research_evidence.AsrResearchEvidenceResult,
    *,
    step_result: dict[str, Any],
) -> dict[str, Any]:
    duration_ms = result.stage_timing.duration_ms
    summary = _base_summary()
    summary.update(
        {
            "stage": "资料查询已完成（开发单步）",
            "task_duration_ms": duration_ms,
            "candidate_count": len(result.input.candidates),
            "query_count": len(result.query_runs),
            "evidence_count": len(result.evidence),
            "unresolved_count": len(
                result.unresolved_candidate_ids
            ),
            "language": result.input.language,
            "source_track_id": result.input.source_track_id,
            "llm_profile_id": result.profile_id,
            "llm_model_id": result.model_id,
            "task_stage_timings": {
                "research": {"duration_ms": duration_ms}
            },
            "task_step_results": {"research": step_result},
            "quality_summary": (
                result.quality_summary.model_dump(mode="json")
            ),
        }
    )
    return summary


def step_result(
    result: research_evidence.AsrResearchEvidenceResult,
) -> dict[str, Any]:
    candidate_by_id = {
        item.candidate_id: item
        for item in result.input.candidates
    }
    query_items = [
        {
            "title": item.effective_query,
            "text": item.failure_reason or _OUTCOME_LABELS[item.outcome],
            "meta": f"第 {item.round_index} 轮",
            "tone": (
                "positive"
                if item.outcome == "supported"
                else "warning"
            ),
            "facts": [
                {
                    "label": "查询结果",
                    "value": _OUTCOME_LABELS[item.outcome],
                },
                {"label": "找到资料", "value": str(item.source_count)},
                *(
                    [
                        {
                            "label": "画面线索",
                            "value": str(
                                len(item.visual_hint_ids_used)
                            ),
                        }
                    ]
                    if item.visual_hint_ids_used
                    else []
                ),
            ],
            "links": [],
        }
        for item in result.query_runs
    ]
    evidence_items = [
        {
            "title": item.title,
            "text": item.snippet or "来源没有提供摘要。",
            "meta": _CATEGORY_LABELS.get(
                candidate_by_id[item.candidate_id].category,
                "资料",
            ),
            "tone": "positive",
            "facts": [
                {
                    "label": "对应疑点",
                    "value": (
                        candidate_by_id[item.candidate_id].reason
                        or candidate_by_id[item.candidate_id].query
                    ),
                },
                {
                    "label": "命中关注词",
                    "value": (
                        "、".join(item.matched_target_terms)
                        or "由相关性规则筛选"
                    ),
                },
            ],
            "links": [
                {
                    "title": item.title,
                    "url": item.url,
                    "meta": item.provider,
                    "text": item.snippet,
                }
            ],
        }
        for item in result.evidence
    ]
    assessment_items = [
        {
            "title": (
                f"第 {round_item.round_index} 轮 · "
                f"{candidate_by_id[item.candidate_id].query}"
            ),
            "text": item.reason,
            "tone": (
                "positive"
                if item.decision == "sufficient"
                else "warning"
            ),
            "facts": [
                {
                    "label": "模型判断",
                    "value": {
                        "sufficient": "资料已够下一步使用",
                        "search_more": "需要继续搜索",
                        "unresolved": "暂时无法确认",
                    }[item.decision],
                },
                *(
                    [
                        {
                            "label": "下一轮查询",
                            "value": item.followup_query,
                        }
                    ]
                    if item.followup_query
                    else []
                ),
            ],
            "links": [],
        }
        for round_item in result.rounds
        for item in round_item.assessments
    ]
    unresolved_items = [
        {
            "title": candidate_by_id[candidate_id].query,
            "text": (
                candidate_by_id[candidate_id].reason
                or "现有资料不足，保留给下一步或人工复核。"
            ),
            "tone": "warning",
            "facts": [
                {
                    "label": "关注词",
                    "value": (
                        "、".join(
                            candidate_by_id[candidate_id].target_terms
                        )
                        or "未指定"
                    ),
                }
            ],
            "links": [],
        }
        for candidate_id in result.unresolved_candidate_ids
    ]
    llm_items, llm_metrics = llm_observability.project_llm_calls(
        result.llm_calls
    )
    status = (
        "success"
        if result.status in {"completed", "not_needed"}
        else "warning"
        if result.status == "partial"
        else "failed"
    )
    return {
        **_task_definition(),
        "status": status,
        "summary": (
            f"共查询 {len(result.query_runs)} 次，经过 "
            f"{len(result.rounds)} 轮判断，找到 "
            f"{len(result.evidence)} 条可用资料；仍有 "
            f"{len(result.unresolved_candidate_ids)} 个疑点待确认。"
        ),
        "metrics": [
            {"label": "待查疑点", "value": str(len(result.input.candidates))},
            {"label": "查询轮次", "value": str(len(result.rounds))},
            {"label": "实际查询", "value": str(len(result.query_runs))},
            {"label": "可用资料", "value": str(len(result.evidence))},
            {
                "label": "仍待确认",
                "value": str(len(result.unresolved_candidate_ids)),
            },
        ],
        "sections": [
            *(
                [{"title": "是否继续搜索", "items": assessment_items}]
                if assessment_items
                else []
            ),
            *(
                [{"title": "查询记录", "items": query_items}]
                if query_items
                else []
            ),
            *(
                [{"title": "可用资料", "items": evidence_items}]
                if evidence_items
                else []
            ),
            *(
                [{"title": "仍待确认", "items": unresolved_items}]
                if unresolved_items
                else []
            ),
        ],
        "notes": [
            "本步骤只收集资料，不决定规范名称。",
            "本步骤没有修改任何听写片段。",
            f"停止原因：{_STOP_LABELS[result.stop_reason]}。",
        ],
        "summary_section_limit": 4,
        "debug": {
            "description": "用于核对上游来源、查询策略、模型和停止规则。",
            "metrics": [
                {"label": "输入契约", "value": result.input.contract_version},
                {
                    "label": "上游契约",
                    "value": result.input.upstream_contract_version,
                },
                {"label": "输出契约", "value": result.contract_version},
                {"label": "上游任务", "value": result.input.upstream_operation_id},
                {"label": "最多轮次", "value": str(result.input.policy.max_rounds)},
                {
                    "label": "查询上限",
                    "value": str(result.input.policy.max_total_queries),
                },
                {"label": "停止原因", "value": _STOP_LABELS[result.stop_reason]},
                {"label": "模型配置", "value": result.profile_id or "未配置"},
                {"label": "实际模型", "value": result.model_id or "未配置"},
                *llm_metrics,
            ],
            "sections": (
                [{"title": "模型调用明细", "items": llm_items}]
                if llm_items
                else []
            ),
            "notes": [
                "这里只保存用量统计，不保存模型的完整隐藏思考。",
                *result.warnings,
            ],
        },
    }


def _base_summary() -> dict[str, Any]:
    definition = (
        workflow_contracts
        .asr_research_evidence_development_workflow_summary()
    )
    return {
        "stage_id": "research",
        "execution_scope": "partial",
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def _task_definition() -> dict[str, Any]:
    task = (
        workflow_contracts
        .ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_DEFINITION
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
