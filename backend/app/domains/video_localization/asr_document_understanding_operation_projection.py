"""Public summaries for the managed document-understanding breakpoint."""

from __future__ import annotations

from typing import Any

from app.domains.video_localization import (
    document_understanding_contracts,
    visual_evidence,
    workflow_contracts,
)


_RESEARCH_CATEGORY_LABELS = {
    "proper_noun": "专有名称",
    "background": "背景资料",
    "culture": "文化语境",
    "persona": "人物身份",
}

_CONTENT_KIND_LABELS = {
    "film_or_drama": "电影、剧情或动画作品",
    "interview": "访谈",
    "tutorial": "教程",
    "presentation": "演讲或演示",
    "conversation": "对话",
    "other": "其他内容",
    "unknown": "尚未确定",
}


def initial_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "准备理解全文并规划复查"
    summary["task_step_results"] = {
        "understand_document": {
            **_task_definition(),
            "status": "todo",
            "summary": "等待锁定上游初始分析结果。",
            "metrics": [],
            "sections": [],
            "notes": [],
        }
    }
    return summary


def running_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "正在理解全文并规划复查"
    summary["task_step_results"] = {
        "understand_document": {
            **_task_definition(),
            "status": "running",
            "summary": "正在读取已锁定的上游结果并理解全文。",
            "metrics": [],
            "sections": [],
            "notes": [
                "后续名称核对、局部复查和逐词对齐不会在本次运行中启动。"
            ],
        }
    }
    return summary


def success_summary(
    result: (
        document_understanding_contracts
        .AsrDocumentUnderstandingResult
    ),
) -> dict[str, Any]:
    duration_ms = result.stage_timing.duration_ms
    speaker_count = len(
        {
            segment.speaker_cluster_id
            for segment in result.input.segments
            if segment.speaker_cluster_id
        }
    )
    summary = _base_summary()
    summary.update(
        {
            "stage": "理解全文并规划复查已完成（开发单步）",
            "task_duration_ms": duration_ms,
            "segment_count": len(result.input.segments),
            "speaker_count": speaker_count,
            "language": result.input.language,
            "source_track_id": result.input.source_track_id,
            "llm_profile_id": result.profile_id,
            "llm_model_id": result.model_id,
            "task_stage_timings": {
                "understand_document": {
                    "duration_ms": duration_ms
                }
            },
            "task_step_results": {
                "understand_document": step_result(result)
            },
            "quality_summary": (
                result.quality_summary.model_dump(mode="json")
            ),
        }
    )
    return summary


def step_result(
    result: (
        document_understanding_contracts
        .AsrDocumentUnderstandingResult
    ),
) -> dict[str, Any]:
    brief = result.brief
    entity_items = [
        {
            "title": item.name,
            "text": item.role or "待下一步结合上下文判断。",
            "tone": (
                "warning"
                if item.needs_research
                else "neutral"
            ),
            "facts": [
                {
                    "label": "是否建议查证",
                    "value": (
                        "是" if item.needs_research else "否"
                    ),
                }
            ],
            "links": [],
        }
        for item in brief.entity_candidates
    ]
    research_items = [
        {
            "title": item.query,
            "text": item.reason or "交给下一步决定是否查询。",
            "tone": "warning",
            "facts": [
                {
                    "label": "类型",
                    "value": _RESEARCH_CATEGORY_LABELS.get(
                        item.category,
                        "其他",
                    ),
                },
                {
                    "label": "关注词",
                    "value": (
                        "、".join(item.target_terms)
                        or "未指定"
                    ),
                },
            ],
            "links": [],
        }
        for item in brief.research_candidates
    ]
    review_items = [
        {
            "title": f"{item.section_id} · {item.role}",
            "text": "；".join(item.focus),
            "meta": (
                f"第 {item.start_ordinal}–{item.end_ordinal} 段"
                f" · {item.start_segment_id} → "
                f"{item.end_segment_id}"
            ),
            "facts": [],
            "links": [],
        }
        for item in brief.review_sections
    ]
    visual_items = [
        {
            "title": item.question,
            "text": item.reason,
            "meta": (
                f"{visual_evidence.visual_question_kind_label(item.kind)}"
                f" · 第 {item.start_ordinal}–"
                f"{item.end_ordinal} 段"
            ),
            "tone": "warning",
            "facts": [
                {
                    "label": "取帧方式",
                    "value": (
                        "向后查看"
                        if item.frame_strategy == "look_ahead"
                        else "当前片段附近"
                    ),
                }
            ],
            "links": [],
        }
        for item in brief.visual_questions
    ]
    return {
        **_task_definition(),
        "status": (
            "success"
            if result.quality_summary.status == "passed"
            else "warning"
        ),
        "summary": (
            f"已通读 {len(result.input.segments)} 个讲话片段，"
            f"规划 {len(brief.review_sections)} 个连续复查区块，"
            f"列出 {len(brief.research_candidates)} 个待查问题。"
        ),
        "metrics": [
            {
                "label": "讲话片段",
                "value": str(len(result.input.segments)),
            },
            {
                "label": "匿名说话人",
                "value": str(
                    len(
                        {
                            item.speaker_cluster_id
                            for item in result.input.segments
                            if item.speaker_cluster_id
                        }
                    )
                ),
            },
            {
                "label": "复查区块",
                "value": str(len(brief.review_sections)),
            },
            {
                "label": "待查问题",
                "value": str(
                    len(brief.research_candidates)
                ),
            },
            {
                "label": "模型请求",
                "value": str(result.llm_call_count),
            },
        ],
        "sections": [
            {
                "title": "全文概览",
                "open_by_default": True,
                "items": [
                    {
                        "title": "内容概述",
                        "text": brief.summary,
                        "facts": [
                            {
                                "label": "讲话方式",
                                "value": brief.speaker_style,
                            },
                            {
                                "label": "内容类型",
                                "value": _CONTENT_KIND_LABELS.get(
                                    brief.content_kind,
                                    "尚未确定",
                                ),
                            },
                        ],
                        "links": [],
                    },
                    *[
                        {
                            "title": f"内容推进 {index}",
                            "text": value,
                            "facts": [],
                            "links": [],
                        }
                        for index, value in enumerate(
                            brief.content_logic,
                            start=1,
                        )
                    ],
                ],
            },
            *(
                [
                    {
                        "title": "语言与原片字幕",
                        "items": [
                            {
                                "title": f"语言观察 {index}",
                                "text": value,
                                "facts": [],
                                "links": [],
                            }
                            for index, value in enumerate(
                                brief.language_notes,
                                start=1,
                            )
                        ],
                    }
                ]
                if brief.language_notes
                else []
            ),
            *(
                [
                    {
                        "title": "名称与实体候选",
                        "items": entity_items,
                    }
                ]
                if entity_items
                else []
            ),
            *(
                [
                    {
                        "title": "建议下一步查证",
                        "items": research_items,
                    }
                ]
                if research_items
                else []
            ),
            *(
                [
                    {
                        "title": "建议查看画面",
                        "items": visual_items,
                    }
                ]
                if visual_items
                else []
            ),
            {
                "title": "建议复查范围",
                "items": review_items,
            },
        ],
        "notes": [
            "本步骤没有执行联网查询。",
            "本步骤没有修改任何听写片段。",
            "后续名称核对、局部复查、逐词对齐和字幕整理均未执行。",
        ],
        "summary_section_limit": 4,
        "debug": {
            "description": (
                "用于确认输入来源、模型、契约和全文理解策略。"
            ),
            "metrics": [
                {
                    "label": "输入契约",
                    "value": result.input.contract_version,
                },
                {
                    "label": "上游契约",
                    "value": (
                        result.input
                        .upstream_contract_version
                    ),
                },
                {
                    "label": "输出契约",
                    "value": result.contract_version,
                },
                {
                    "label": "上游任务",
                    "value": (
                        result.input.upstream_operation_id
                    ),
                },
                {
                    "label": "来源音轨",
                    "value": result.input.source_track_id,
                },
                {
                    "label": "音频指纹",
                    "value": (
                        result.input.source_audio_sha256[:12]
                    ),
                },
                {
                    "label": "模型配置",
                    "value": result.profile_id,
                },
                {
                    "label": "实际模型",
                    "value": result.model_id,
                },
                {
                    "label": "处理策略",
                    "value": (
                        "全文一次处理"
                        if result.execution_strategy
                        == "full_document"
                        else "长文窗口处理"
                    ),
                },
                {
                    "label": "窗口数量",
                    "value": str(result.window_count),
                },
                {
                    "label": "重试次数",
                    "value": str(result.retry_count),
                },
            ],
            "sections": [],
            "notes": list(result.warnings),
        },
    }


def _base_summary() -> dict[str, Any]:
    definition = (
        workflow_contracts
        .asr_document_understanding_development_workflow_summary()
    )
    return {
        "stage_id": "understand_document",
        "execution_scope": "partial",
        "workflow_schema_version": (
            definition["schema_version"]
        ),
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def _task_definition() -> dict[str, Any]:
    task = (
        workflow_contracts
        .ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_DEFINITION
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
