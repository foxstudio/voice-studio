"""Public summaries for the managed visual-evidence breakpoint."""

from __future__ import annotations

from typing import Any

from app.domains.video_localization import (
    llm_observability,
    visual_evidence,
    workflow_contracts,
)
from app.domains.video_localization.timeline_timecode import (
    format_timeline_position,
)


def initial_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "准备读取画面证据"
    summary["task_step_results"] = {
        "visual_evidence": {
            **_task_definition(),
            "status": "todo",
            "summary": "等待锁定上游全文理解结果和源视频。",
            "metrics": [],
            "sections": [],
            "notes": [],
        }
    }
    return summary


def running_summary() -> dict[str, Any]:
    summary = _base_summary()
    summary["stage"] = "正在读取画面证据"
    summary["task_step_results"] = {
        "visual_evidence": {
            **_task_definition(),
            "status": "running",
            "summary": "正在按问题提取少量截图并读取直接可见信息。",
            "metrics": [],
            "sections": [],
            "notes": [
                "不会根据长相识别人，也不会决定规范名称或修改文字。"
            ],
        }
    }
    return summary


def success_summary(
    result: visual_evidence.AsrVisualEvidenceResult,
    *,
    project_id: str,
    operation_id: str,
    step_result: dict[str, Any],
) -> dict[str, Any]:
    del project_id, operation_id
    duration_ms = result.stage_timing.duration_ms
    summary = _base_summary()
    summary.update(
        {
            "stage": "画面取证已完成（开发单步）",
            "task_duration_ms": duration_ms,
            "question_count": len(result.input.questions),
            "frame_count": len(result.frames),
            "observation_count": len(result.observations),
            "llm_profile_id": result.profile_id,
            "llm_model_id": result.model_id,
            "task_stage_timings": {
                "visual_evidence": {
                    "duration_ms": duration_ms
                }
            },
            "task_step_results": {
                "visual_evidence": step_result
            },
            "quality_summary": (
                result.quality_summary.model_dump(mode="json")
            ),
        }
    )
    return summary


def step_result(
    result: visual_evidence.AsrVisualEvidenceResult,
    *,
    project_id: str,
    operation_id: str,
) -> dict[str, Any]:
    frames_by_id = {
        item.frame_id: item for item in result.frames
    }
    observation_items = []
    for raw_item in visual_evidence.reader_observation_items(
        result
    ):
        item = dict(raw_item)
        frame_ids = item.pop("_frame_ids", [])
        item.pop("_frames", None)
        item.pop("_frame_rate", None)
        links = [
            {
                "title": (
                    "查看 "
                    f"{format_timeline_position(frame.timestamp_ms, frame_rate=result.input.video_frame_rate)}"
                    "截图"
                ),
                "url": (
                    f"/api/projects/{project_id}/"
                    "video-localization/operations/"
                    f"{operation_id}/"
                    "development-visual-evidence-frames/"
                    f"{frame.frame_id}"
                ),
                "meta": format_timeline_position(
                    frame.timestamp_ms,
                    frame_rate=result.input.video_frame_rate,
                ),
                "text": f"第 {frame.round_index} 轮截图",
            }
            for frame_id in frame_ids
            if (
                frame := frames_by_id.get(frame_id)
            ) is not None
        ]
        observation_items.append(
            {**item, "links": links}
        )
    llm_call_items, llm_call_metrics = (
        llm_observability.project_llm_calls(
            result.llm_calls,
            calls_complete=True,
        )
    )
    return {
        **_task_definition(),
        "status": (
            "success"
            if result.status in {"completed", "not_needed"}
            else "warning"
        ),
        "summary": (
            f"检查 {len(result.input.questions)} 个画面问题，"
            f"提取 {len(result.frames)} 张截图，"
            f"得到 {result.quality_summary.answered_question_count} "
            "项可用观察。"
        ),
        "metrics": [
            {
                "label": "画面问题",
                "value": str(len(result.input.questions)),
            },
            {
                "label": "截图",
                "value": str(len(result.frames)),
            },
            {
                "label": "可用观察",
                "value": str(
                    result.quality_summary
                    .answered_question_count
                ),
            },
            {
                "label": "二次补看",
                "value": str(
                    result.quality_summary
                    .second_round_question_count
                ),
            },
            {
                "label": "模型请求",
                "value": str(
                    result.quality_summary.model_call_count
                ),
            },
        ],
        "sections": (
            [
                {
                    "title": "画面观察",
                    "items": observation_items,
                }
            ]
            if observation_items
            else []
        ),
        "notes": [
            "画面信息先作为查询线索；后续步骤仍需独立判断。",
            "本步骤没有根据长相识别人。",
            "本步骤没有决定规范名称，也没有修改听写或字幕。",
            *result.warnings,
        ],
        "debug": {
            "description": (
                "用于核对输入来源、模型、截图时间和任务边界。"
            ),
            "metrics": [
                {
                    "label": "输入契约",
                    "value": result.input.contract_version,
                },
                {
                    "label": "上游契约",
                    "value": (
                        result.input.upstream_contract_version
                    ),
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
                    "label": "视频指纹",
                    "value": result.input.video_sha256[:12],
                },
                {
                    "label": "模型配置",
                    "value": result.profile_id or "未使用",
                },
                {
                    "label": "实际模型",
                    "value": result.model_id or "未使用",
                },
                {
                    "label": "停止原因",
                    "value": result.stop_reason,
                },
                *llm_call_metrics,
            ],
            "sections": (
                [
                    {
                        "title": "模型调用明细",
                        "items": llm_call_items,
                    }
                ]
                if llm_call_items
                else []
            ),
            "notes": [],
        },
    }


def _base_summary() -> dict[str, Any]:
    definition = (
        workflow_contracts
        .asr_visual_evidence_development_workflow_summary()
    )
    return {
        "stage_id": "visual_evidence",
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
        .ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_DEFINITION
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
