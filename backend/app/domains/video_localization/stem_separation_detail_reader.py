"""Managed detail assembly for stem-separation operations."""

from __future__ import annotations

from sqlite3 import Connection
from typing import Any

from app.domains.video_localization import (
    managed_local_detail,
    managed_local_workflow_specs,
    stem_separation_operation_projection,
    workflow_contracts,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_stem_separation_step import (
    STEM_SEPARATION_STEP_ID,
)


_PRIVATE_OUTPUT_FIELDS = {
    "schema_version",
    "source_audio_sha256",
    "vocals_clean_sha256",
    "background_sha256",
}


def assemble_stem_separation_detail(
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
            .STEM_SEPARATION_STEP_SPEC
        ),
        file_backend=file_backend,
    )
    return VideoLocalizationOperation(
        operation_id=state.operation_id,
        project_id=state.project_id,
        kind="stems",
        status=state.status,
        label=(
            workflow_contracts
            .STEM_SEPARATION_WORKFLOW_DEFINITION
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
            stem_separation_operation_projection
            .success_summary(state.result.output)
        )
        if state.task_duration_ms is not None:
            summary["task_duration_ms"] = state.task_duration_ms
        return summary
    workflow = (
        workflow_contracts.stem_separation_workflow_summary()
    )
    task = (
        workflow_contracts
        .STEM_SEPARATION_WORKFLOW_DEFINITION
        .stages[0]
        .atomic_tasks[0]
    )
    public_step_status = state.public_step_status
    summary: dict[str, Any] = {
        "stage": {
            "success": "人声与背景声已分离并保存",
            "failed": "分离人声与背景声失败",
            "cancelled": "分离任务已取消",
            "queued": "准备分离人声与背景声",
        }.get(state.status, "正在分离人声与背景声"),
        "stage_id": STEM_SEPARATION_STEP_ID,
        "workflow_schema_version": workflow["schema_version"],
        "workflow_id": workflow["workflow_id"],
        "task_stage_groups": workflow["stages"],
        "task_step_results": {
            STEM_SEPARATION_STEP_ID: {
                "label": task.label,
                "order": task.order,
                "status": public_step_status,
                "purpose": task.description,
                "summary": {
                    "success": "已生成并保存可用的人声和背景声。",
                    "failed": "双轨分离结果未能完整生成并保存。",
                    "cancelled": "任务已取消，未继续生成分离音轨。",
                    "todo": "等待开始分离人声与背景声。",
                    "running": "正在生成并校验人声与背景声。",
                }[public_step_status],
            }
        },
    }
    if state.result is not None:
        output = state.result.output
        summary.update(
            output.model_dump(
                mode="json",
                exclude=_PRIVATE_OUTPUT_FIELDS,
            )
        )
        summary["task_step_results"][
            STEM_SEPARATION_STEP_ID
        ]["metrics"] = (
            stem_separation_operation_projection.media_metrics(
                output
            )
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
            "notes": ["请确认源音轨仍然可用后重新执行。"],
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
        "VIDEO_LOCALIZATION_SOURCE_AUDIO_CHANGED": (
            "分离期间源音轨发生了变化，请重新执行。"
        ),
        (
            "VIDEO_LOCALIZATION_STEM_SEPARATION_"
            "RESULT_WRITE_FAILED"
        ): "分轨结果未能完整保存，本次任务没有标记成功。",
    }.get(
        error_code or "",
        "人声与背景声未能完整分离并保存，请检查源音轨后重试。",
    )


__all__ = ["assemble_stem_separation_detail"]
