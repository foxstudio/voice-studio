"""Public summaries for managed entity normalization."""

from __future__ import annotations

from typing import Any

from app.domains.video_localization import (
    entity_normalization,
    llm_observability,
    workflow_contracts,
)


def initial_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "准备统一名称与术语"
    summary["task_step_results"] = {
        "normalize_entities": {
            **_task_definition(),
            "status": "todo",
            "summary": "等待锁定资料查询结果和当前项目词表。",
            "metrics": [],
            "sections": [],
            "notes": [],
        }
    }
    return summary


def running_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "正在统一名称与术语"
    summary["task_step_results"] = {
        "normalize_entities": {
            **_task_definition(),
            "status": "running",
            "summary": "正在应用项目词表，并判断有证据支持的规范名称。",
            "metrics": [],
            "sections": [],
            "notes": [
                "只有证据充分且不改变数字或版本含义时才会修改文字。"
            ],
        }
    }
    return summary


def success_summary(
    result: entity_normalization.AsrEntityNormalizationResult,
    *,
    project_id: str,
    step_result: dict[str, Any],
) -> dict[str, Any]:
    duration_ms = result.duration_ms
    summary = _base_summary()
    summary.update(
        {
            "stage": "统一名称与术语已完成（开发单步）",
            "task_duration_ms": duration_ms,
            "resolution_count": len(result.resolutions),
            "change_count": len(result.changes),
            "warning_count": len(result.warnings),
            "segment_count": len(result.updated_segments),
            "source_track_id": result.input.source_track_id,
            "llm_profile_id": result.profile_id,
            "llm_model_id": result.model_id,
            "task_stage_timings": {
                "normalize_entities": {
                    "duration_ms": duration_ms
                }
            },
            "task_step_results": {
                "normalize_entities": step_result
            },
        }
    )
    return summary


def step_result(
    result: entity_normalization.AsrEntityNormalizationResult,
    *,
    project_id: str,
) -> dict[str, Any]:
    llm_items, llm_metrics = (
        llm_observability.project_llm_calls(
            result.llm_calls
        )
    )
    resolution_items = [
        {
            "title": item.canonical_name,
            "text": (
                "原文写法：" + "、".join(item.variants)
                if item.variants
                else "没有需要替换的原文写法"
            ),
            "meta": f"可信度 {item.confidence:.0%}",
            "tone": "positive",
            "facts": [
                {
                    "label": "身份或作用",
                    "value": _reader_facing_role(item.role),
                },
                {
                    "label": "证据",
                    "value": str(len(item.evidence_source_ids)),
                },
            ],
            "links": _visual_evidence_links(
                result,
                item.evidence_source_ids,
                project_id=project_id,
            ),
        }
        for item in result.resolutions[:12]
    ]
    change_items = [
        {
            "title": item.segment_id,
            "text": f"{item.before} → {item.after}",
            "meta": item.reason,
            "tone": "positive",
            "facts": [],
            "links": _visual_evidence_links(
                result,
                item.evidence_source_ids,
                project_id=project_id,
            ),
        }
        for item in result.changes[:12]
    ]
    return {
        **_task_definition(),
        "status": (
            "warning"
            if result.status in {"partial", "failed"}
            else "success"
        ),
        "summary": (
            f"确认 {len(result.resolutions)} 个规范名称，"
            f"修改 {len(result.changes)} 处文字，"
            f"{len(result.warnings)} 处保留原文待确认。"
        ),
        "metrics": [
            {
                "label": "规范名称",
                "value": str(len(result.resolutions)),
            },
            {
                "label": "文字修改",
                "value": str(len(result.changes)),
            },
            {
                "label": "待确认",
                "value": str(len(result.warnings)),
            },
        ],
        "sections": [
            *(
                [{"title": "规范名称", "items": resolution_items}]
                if resolution_items
                else []
            ),
            *(
                [{"title": "文字修改", "items": change_items}]
                if change_items
                else []
            ),
        ],
        "notes": [item.message for item in result.warnings],
        "debug": {
            "description": "用于确认上下游契约、来源音轨和模型配置。",
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
                    "label": "上游任务",
                    "value": result.input.upstream_operation_id,
                },
                {
                    "label": "来源音轨",
                    "value": result.input.source_track_id,
                },
                {
                    "label": "音频指纹",
                    "value": result.input.source_audio_sha256[:12],
                },
                {
                    "label": "模型配置",
                    "value": result.profile_id or "未使用",
                },
                *llm_metrics,
            ],
            "sections": (
                [{"title": "模型调用明细", "items": llm_items}]
                if llm_items
                else []
            ),
            "notes": [
                "这里只保存模型、Token、费用和结束原因；"
                "不保存完整提问、回答正文或隐藏思考。"
            ],
        },
    }


def _visual_evidence_links(
    result: entity_normalization.AsrEntityNormalizationResult,
    evidence_source_ids: list[str],
    *,
    project_id: str,
) -> list[dict]:
    operation_id = (
        result.input.visual_evidence_operation_id or ""
    ).strip()
    if not operation_id:
        return []
    cited = set(evidence_source_ids)
    output: list[dict] = []
    seen: set[str] = set()
    for claim in result.input.visual_name_evidence:
        if claim.evidence_id not in cited:
            continue
        for frame_id in claim.frame_ids:
            if frame_id in seen:
                continue
            seen.add(frame_id)
            output.append(
                {
                    "title": "查看姓名画面证据",
                    "url": (
                        f"/api/projects/{project_id}/"
                        "video-localization/operations/"
                        f"{operation_id}/"
                        "development-visual-evidence-frames/"
                        f"{frame_id}"
                    ),
                    "meta": f"可信度 {claim.confidence:.0%}",
                    "text": (
                        f"{claim.transcript_variant} → "
                        f"{claim.visible_name}"
                    ),
                }
            )
    return output


def _reader_facing_role(value: str) -> str:
    normalized = " ".join(str(value or "").split())
    return normalized[:300] or "查证资料支持的专有名称"


def _base_summary() -> dict[str, Any]:
    definition = (
        workflow_contracts
        .asr_entity_normalization_development_workflow_summary()
    )
    return {
        "stage_id": "normalize_entities",
        "execution_scope": "partial",
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def _task_definition() -> dict[str, Any]:
    task = (
        workflow_contracts
        .ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_DEFINITION
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
