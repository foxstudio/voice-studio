"""Managed detail assembly for automatic reference candidates."""

from __future__ import annotations

from sqlite3 import Connection
from typing import Any

from app.domains.video_localization import (
    managed_local_detail,
    managed_local_workflow_specs,
    reference_candidates_operation_projection,
    workflow_contracts,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_reference_candidates_step import (
    REFERENCE_CANDIDATES_STEP_ID,
)


def assemble_reference_candidates_detail(
    connection: Connection,
    ledger,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> VideoLocalizationOperation:
    state = managed_local_detail.read_zero_input_detail_state(
        connection,
        ledger,
        spec=(
            managed_local_workflow_specs
            .REFERENCE_CANDIDATES_STEP_SPEC
        ),
        file_backend=file_backend,
    )
    return VideoLocalizationOperation(
        operation_id=state.operation_id,
        project_id=state.project_id,
        kind="reference_clips",
        status=state.status,
        label=(
            workflow_contracts
            .REFERENCE_CANDIDATES_WORKFLOW_DEFINITION
            .label
        ),
        progress=state.progress,
        error_code=state.error_code,
        error_message=_error_message(
            state.status,
            state.error_code,
        ),
        cancel_requested=state.cancel_requested,
        result_summary=_result_summary(state),
        parameters=state.parameters,
        created_at=state.created_at,
        started_at=state.started_at,
        completed_at=state.completed_at,
    )


def _result_summary(
    state: managed_local_detail.ManagedLocalDetailState[
        Any
    ],
) -> dict[str, Any]:
    if state.status == "success" and state.result is not None:
        summary = (
            reference_candidates_operation_projection
            .success_summary(state.result.output)
        )
        if state.task_duration_ms is not None:
            summary["task_duration_ms"] = state.task_duration_ms
        return summary
    workflow = (
        workflow_contracts
        .reference_candidates_workflow_summary()
    )
    task = (
        workflow_contracts
        .REFERENCE_CANDIDATES_WORKFLOW_DEFINITION
        .stages[0]
        .atomic_tasks[0]
    )
    public_step_status = state.public_step_status
    summary: dict[str, Any] = {
        "stage": {
            "success": "参考音候选已生成并保存",
            "failed": "生成参考音候选失败",
            "cancelled": "参考音候选任务已取消",
            "queued": "准备生成参考音候选",
        }.get(state.status, "正在生成参考音候选"),
        "stage_id": REFERENCE_CANDIDATES_STEP_ID,
        "workflow_schema_version": workflow["schema_version"],
        "workflow_id": workflow["workflow_id"],
        "task_stage_groups": workflow["stages"],
        "task_step_results": {
            REFERENCE_CANDIDATES_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": public_step_status,
                "purpose": task.description,
                "summary": {
                    "success": "已生成并关联可人工复核的参考音候选。",
                    "failed": "参考音候选未能完整生成并保存。",
                    "cancelled": "任务已取消，未继续生成参考音候选。",
                    "todo": "等待开始生成参考音候选。",
                    "running": "正在裁切并校验参考音候选。",
                }[public_step_status],
            }
        },
    }
    if state.result is not None:
        output = state.result.output
        summary.update(
            {
                "reference_clip_count": (
                    output.candidate_clip_count
                ),
                "generated_clip_count": (
                    output.generated_clip_count
                ),
                "linked_cue_count": output.linked_cue_count,
                "media_status": output.media_status,
            }
        )
        summary["task_step_results"][
            REFERENCE_CANDIDATES_STEP_ID
        ]["metrics"] = (
            reference_candidates_operation_projection
            .media_metrics(output)
        )
    if state.task_duration_ms is not None:
        summary["task_duration_ms"] = state.task_duration_ms
    if state.status == "failed":
        summary["error_detail"] = {
            "status": "failed",
            "summary": _error_message(
                state.status,
                state.error_code,
            ),
            "metrics": (
                [
                    {
                        "label": "错误代码",
                        "value": state.error_code,
                    }
                ]
                if state.error_code
                else []
            ),
            "sections": [],
            "notes": ["请确认干净人声和候选字幕仍然可用后重试。"],
        }
    return summary


def _error_message(
    status: str,
    error_code: str | None,
) -> str | None:
    if status == "cancelled":
        return "任务已取消。"
    if status != "failed":
        return None
    return {
        "VIDEO_LOCALIZATION_REFERENCE_INPUT_CHANGED": (
            "生成期间干净人声、cue、说话人或参考音发生了变化，请重新执行。"
        ),
        (
            "VIDEO_LOCALIZATION_REFERENCE_CANDIDATES_"
            "RESULT_WRITE_FAILED"
        ): "参考音候选结果未能完整保存，本次任务没有标记成功。",
    }.get(
        error_code or "",
        "参考音候选未能完整生成并保存，请检查干净人声后重试。",
    )


__all__ = ["assemble_reference_candidates_detail"]
