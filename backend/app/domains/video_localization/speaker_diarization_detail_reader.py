"""Managed detail and result reads for standalone speaker diarization."""

from __future__ import annotations

from sqlite3 import Connection
from typing import Any

from app.domains.video_localization import (
    managed_local_detail,
    managed_local_workflow_specs,
    speaker_diarization_operation_projection,
    workflow_contracts,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    SpeakerDiarizationDetailParametersV1,
)
from app.schemas.video_localization_speaker_diarization_step import (
    SPEAKER_DIARIZATION_STEP_ID,
    SpeakerDiarizationResultV1,
)
from app.services import database
from app.services import (
    video_localization_operation_detail_core_store as detail_store,
)


def assemble_speaker_diarization_detail(
    connection: Connection,
    ledger,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> VideoLocalizationOperation:
    state = _read_state(
        connection,
        ledger,
        file_backend=file_backend,
    )
    return VideoLocalizationOperation(
        operation_id=state.operation_id,
        project_id=state.project_id,
        kind="speaker_diarization",
        status=state.status,
        label=(
            workflow_contracts
            .SPEAKER_DIARIZATION_WORKFLOW_DEFINITION
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


def read_speaker_diarization_result(
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> SpeakerDiarizationResultV1 | None:
    with database.read_conn() as connection:
        ledger = connection.execute(
            """
            SELECT
                project_id,
                operation_id,
                kind,
                status,
                cancel_requested,
                parameters_fingerprint,
                workflow_version,
                created_at,
                completed_at
            FROM video_localization_operations
            WHERE project_id = ?
              AND operation_id = ?
            """,
            (project_id, operation_id),
        ).fetchone()
        if ledger is None:
            return None
        state = _read_state(
            connection,
            ledger,
            file_backend=file_backend,
        )
        return (
            state.result.output.result
            if state.result is not None
            else None
        )


def _read_state(
    connection: Connection,
    ledger,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> (
    managed_local_detail.ManagedLocalDetailState[Any]
):
    project_id = str(ledger["project_id"])
    operation_id = str(ledger["operation_id"])
    try:
        detail_record = detail_store.get_detail_core_from_connection(
            connection,
            project_id,
            operation_id,
        )
    except (
        detail_store.OperationDetailCoreIdentityConflict,
        detail_store.OperationDetailCoreIntegrityError,
        detail_store.OperationDetailCoreSchemaError,
        TypeError,
        ValueError,
    ) as exc:
        raise OperationDetailRepairRequired(
            "detail_core_invalid"
        ) from exc
    if detail_record is None:
        raise OperationDetailRepairRequired(
            "detail_core_missing"
        )
    core = detail_record.core
    if (
        core.kind != "speaker_diarization"
        or core.workflow_version
        != (
            managed_local_workflow_specs
            .SPEAKER_DIARIZATION_STEP_SPEC
            .workflow_version
        )
        or not isinstance(
            core.parameters,
            SpeakerDiarizationDetailParametersV1,
        )
    ):
        raise OperationDetailRepairRequired(
            "detail_core_mismatch"
        )
    parameters = core.parameters.model_dump(
        mode="json",
        exclude={
            "parameters_schema_version",
        },
        exclude_none=True,
    )
    return managed_local_detail.read_detail_state(
        connection,
        ledger,
        spec=(
            managed_local_workflow_specs
            .SPEAKER_DIARIZATION_STEP_SPEC
        ),
        parameters=parameters,
        file_backend=file_backend,
    )


def _result_summary(
    state: managed_local_detail.ManagedLocalDetailState[Any],
) -> dict[str, Any]:
    if state.status == "success" and state.result is not None:
        summary = (
            speaker_diarization_operation_projection
            .success_summary(state.result.output)
        )
        if state.task_duration_ms is not None:
            summary["task_duration_ms"] = (
                state.task_duration_ms
            )
        return summary
    summary = (
        speaker_diarization_operation_projection
        .initial_summary()
    )
    public_step_status = state.public_step_status
    summary["stage"] = {
        "failed": "说话人区分失败",
        "cancelled": "说话人区分任务已取消",
        "queued": "准备区分说话人",
    }.get(state.status, "正在区分说话人")
    step = summary["task_step_results"][
        SPEAKER_DIARIZATION_STEP_ID
    ]
    step.update(
        {
            "status": public_step_status,
            "summary": {
                "success": "已完成匿名说话人区分。",
                "failed": "说话人区分结果未能完整生成并保存。",
                "cancelled": "任务已取消，未继续区分说话人。",
                "todo": "等待开始区分说话人。",
                "running": "正在分析声音特征并校验分组。",
            }[public_step_status],
        }
    )
    if state.result is not None:
        result = state.result.output.result
        summary.update(
            {
                "speaker_count": len(result.clusters),
                "diarization_status": result.status,
                "count_guidance": (
                    result.count_guidance.model_dump(
                        mode="json"
                    )
                ),
                "quality_summary": (
                    result.quality_summary.model_dump(
                        mode="json"
                    )
                ),
                "sample": (
                    speaker_diarization_operation_projection
                    .result_sample(result)
                ),
            }
        )
        step["metrics"] = (
            speaker_diarization_operation_projection
            .result_metrics(result)
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
            "notes": ["请确认输入音轨仍然可用后重试。"],
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
        (
            "VIDEO_LOCALIZATION_SPEAKER_DIARIZATION_"
            "INPUT_CHANGED"
        ): "输入音轨或参数发生了变化，请重新执行。",
        (
            "VIDEO_LOCALIZATION_SPEAKER_DIARIZATION_"
            "RESULT_WRITE_FAILED"
        ): "说话人区分结果未能完整保存，本次任务没有标记成功。",
    }.get(
        error_code or "",
        "说话人区分未能完整生成并保存，请检查输入音轨后重试。",
    )


__all__ = [
    "assemble_speaker_diarization_detail",
    "read_speaker_diarization_result",
]
