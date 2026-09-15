"""Managed detail assembly for source-audio operations."""

from __future__ import annotations

from sqlite3 import Connection
from typing import Any

from app.domains.video_localization import (
    managed_local_detail,
    managed_local_workflow_specs,
    source_audio_operation_projection,
    workflow_contracts,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_source_audio_step import (
    SOURCE_AUDIO_STEP_ID,
)


def assemble_source_audio_detail(
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
            .SOURCE_AUDIO_STEP_SPEC
        ),
        file_backend=file_backend,
    )
    return VideoLocalizationOperation(
        operation_id=state.operation_id,
        project_id=state.project_id,
        kind="source_audio",
        status=state.status,
        label=(
            workflow_contracts
            .SOURCE_AUDIO_WORKFLOW_DEFINITION
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
    state: (
        managed_local_detail.ManagedLocalDetailState[
            Any
        ]
    ),
) -> dict[str, Any]:
    if state.status == "success" and state.result is not None:
        summary = source_audio_operation_projection.success_summary(
            state.result.output
        )
        if state.task_duration_ms is not None:
            summary["task_duration_ms"] = (
                state.task_duration_ms
            )
        return summary
    workflow = workflow_contracts.source_audio_workflow_summary()
    task = (
        workflow_contracts
        .SOURCE_AUDIO_WORKFLOW_DEFINITION
        .stages[0]
        .atomic_tasks[0]
    )
    public_step_status = state.public_step_status
    summary: dict[str, Any] = {
        "stage": {
            "success": "原始音轨已提取并保存",
            "failed": "提取原始音轨失败",
            "cancelled": "提取原始音轨已取消",
            "queued": "准备提取原始音轨",
        }.get(state.status, "正在提取原始音轨"),
        "stage_id": SOURCE_AUDIO_STEP_ID,
        "workflow_schema_version": workflow["schema_version"],
        "workflow_id": workflow["workflow_id"],
        "task_stage_groups": workflow["stages"],
        "task_step_results": {
            SOURCE_AUDIO_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": public_step_status,
                "purpose": task.description,
                "summary": {
                    "success": "已提取并保存可供后续流程使用的原始音轨。",
                    "failed": "原始音轨未能完整提取并保存。",
                    "cancelled": "任务已取消，未继续提取原始音轨。",
                    "todo": "等待开始提取原始音轨。",
                    "running": "正在提取并校验原始音轨。",
                }[public_step_status],
            }
        },
    }
    if state.result is not None:
        output = state.result.output
        summary.update(
            output.model_dump(
                mode="json",
                exclude={"schema_version"},
            )
        )
        summary["task_step_results"][SOURCE_AUDIO_STEP_ID][
            "metrics"
        ] = source_audio_operation_projection.media_metrics(output)
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
            "notes": ["请确认源视频仍然可用后重新执行。"],
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
        "VIDEO_LOCALIZATION_SOURCE_CHANGED": (
            "提取期间源视频发生了变化，请重新执行。"
        ),
        "VIDEO_LOCALIZATION_SOURCE_AUDIO_RESULT_WRITE_FAILED": (
            "原音轨结果未能完整保存，本次任务没有标记成功。"
        ),
    }.get(
        error_code or "",
        "原始音轨未能完整提取并保存，请检查源视频后重试。",
    )
__all__ = ["assemble_source_audio_detail"]
