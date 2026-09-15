"""Authoritative detail reader for managed entity normalization."""

from __future__ import annotations

from sqlite3 import Connection
from typing import Any

from app.domains.video_localization import (
    asr_entity_normalization_operation_projection as projection,
    asr_entity_normalization_result_reader,
    managed_local_detail,
    operation_state,
    workflow_contracts,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.schemas.video_localization_asr_entity_normalization_step import (
    ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrEntityNormalizationDetailParametersV1,
)
from app.services import (
    video_localization_operation_detail_core_store as detail_store,
)
from app.services import (
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)


_ACTIVE_STATUSES = frozenset({"queued", "running"})
_TERMINAL_STATUSES = frozenset(
    {"success", "failed", "cancelled"}
)


def assemble_entity_normalization_detail(
    connection: Connection,
    ledger,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> VideoLocalizationOperation:
    project_id = str(ledger["project_id"])
    operation_id = str(ledger["operation_id"])
    if (
        str(ledger["kind"]) != "english_asr"
        or str(ledger["workflow_version"])
        != ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
    ):
        raise OperationDetailRepairRequired(
            "ledger_identity_invalid"
        )
    status = str(ledger["status"])
    if status not in _ACTIVE_STATUSES | _TERMINAL_STATUSES:
        raise OperationDetailRepairRequired(
            "ledger_status_invalid"
        )
    parameters = _read_parameters(connection, ledger)
    try:
        steps = tuple(
            step_store.list_step_attempts_from_connection(
                connection,
                project_id,
                operation_id,
            )
        )
    except (
        step_store.StepSchemaError,
        TypeError,
        ValueError,
    ) as exc:
        raise OperationDetailRepairRequired(
            "step_state_invalid"
        ) from exc
    authority = (
        asr_entity_normalization_result_reader
        .read_success_from_connection(
            connection,
            project_id,
            operation_id,
            file_backend=file_backend,
        )
    )
    attempts = connection.execute(
        """
        SELECT attempt_number, status, started_at, completed_at, error_code
        FROM video_localization_operation_attempts
        WHERE project_id = ? AND operation_id = ?
        ORDER BY attempt_number, attempt_id
        """,
        (project_id, operation_id),
    ).fetchall()
    started_at = (
        str(attempts[0]["started_at"])
        if attempts and attempts[0]["started_at"] is not None
        else None
    )
    completed_at = (
        str(ledger["completed_at"])
        if ledger["completed_at"] is not None
        else None
    )
    error_code = _error_code(status, steps, attempts)
    return VideoLocalizationOperation(
        operation_id=operation_id,
        project_id=project_id,
        kind="english_asr",
        status=status,
        label=(
            workflow_contracts
            .ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_DEFINITION
            .label
        ),
        progress=_progress(status, steps),
        error_code=error_code,
        error_message=_error_message(status, error_code),
        cancel_requested=bool(ledger["cancel_requested"]),
        result_summary=_result_summary(
            status,
            authority.result if authority else None,
            steps,
            project_id=project_id,
            started_at=started_at,
            completed_at=completed_at,
            error_code=error_code,
        ),
        parameters=parameters,
        created_at=str(ledger["created_at"]),
        started_at=started_at,
        completed_at=completed_at,
    )


def _read_parameters(connection: Connection, ledger) -> dict[str, Any]:
    project_id = str(ledger["project_id"])
    operation_id = str(ledger["operation_id"])
    try:
        record = detail_store.get_detail_core_from_connection(
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
    if (
        record is None
        or record.core.kind != "english_asr"
        or record.core.workflow_version
        != ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
        or not isinstance(
            record.core.parameters,
            AsrEntityNormalizationDetailParametersV1,
        )
    ):
        raise OperationDetailRepairRequired(
            "detail_core_mismatch"
        )
    durable_parameters = (
        record.core.parameters.model_dump(
            mode="json",
            exclude={"parameters_schema_version"},
        )
    )
    for key in (
        "profile_id",
        "profile_configuration_fingerprint",
    ):
        if durable_parameters.get(key) is None:
            durable_parameters.pop(key, None)
    parameters = {
        **durable_parameters,
        "execution_mode": "stop_after",
        "stop_after_step": "normalize_entities",
    }
    public = {
        **parameters,
        "scope": operation_state.operation_scope(
            "english_asr",
            parameters,
        ),
    }
    if (
        ledger_store.parameters_fingerprint(public)
        != str(ledger["parameters_fingerprint"])
    ):
        raise OperationDetailRepairRequired(
            "detail_parameters_mismatch"
        )
    return public


def _result_summary(
    status: str,
    result,
    steps,
    *,
    project_id: str,
    started_at: str | None,
    completed_at: str | None,
    error_code: str | None,
) -> dict[str, Any]:
    if status == "success":
        if result is None:
            raise OperationDetailRepairRequired(
                "successful_workflow_step_missing"
            )
        summary = projection.success_summary(
            result,
            project_id=project_id,
            step_result=projection.step_result(
                result,
                project_id=project_id,
            ),
        )
        managed_local_detail.apply_workflow_duration(
            summary,
            steps,
            started_at=started_at,
            completed_at=completed_at,
        )
        return summary
    summary = (
        projection.initial_summary()
        if status == "queued"
        else projection.running_summary()
    )
    summary["stage"] = {
        "failed": "名称与术语统一失败",
        "cancelled": "名称与术语统一已取消",
    }.get(status, summary["stage"])
    latest = managed_local_detail.latest_step(
        steps,
        lambda step: step.step_id
        == "finalize_entity_normalization",
    )
    step_result = summary["task_step_results"][
        "normalize_entities"
    ]
    if latest is not None:
        step_result["status"] = (
            managed_local_detail.normalized_step_status(
                latest.status
            )
        )
    elif status == "failed":
        step_result["status"] = "failed"
    elif status == "cancelled":
        step_result["status"] = "cancelled"
    managed_local_detail.apply_workflow_duration(
        summary,
        steps,
        started_at=started_at,
        completed_at=completed_at,
    )
    if status == "failed":
        summary["error_detail"] = {
            "status": "failed",
            "summary": _error_message(status, error_code),
            "metrics": (
                [{"label": "错误代码", "value": error_code}]
                if error_code
                else []
            ),
            "sections": [],
            "notes": [
                "未知的付费模型结果不会自动重发；请先确认服务商侧结果。"
            ],
        }
    return summary


def _progress(status: str, steps) -> float:
    if status in _TERMINAL_STATUSES:
        return 1.0
    if status == "queued":
        return 0.0
    if any(
        step.status == "success"
        and step.step_id
        == "prepare_entity_normalization_input"
        for step in steps
    ):
        return 0.6
    return 0.42


def _error_code(status: str, steps, attempts) -> str | None:
    if status != "failed":
        return None
    for step in reversed(steps):
        if step.error_code:
            return step.error_code
    for attempt in reversed(attempts):
        if attempt["error_code"]:
            return str(attempt["error_code"])
    return "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_FAILED"


def _error_message(
    status: str,
    error_code: str | None,
) -> str | None:
    if status != "failed":
        return None
    if error_code and "RESULT_UNKNOWN" in error_code:
        return "名称与术语统一请求的结果暂时无法确认，系统没有自动重复请求。"
    return "名称与术语统一未完成，请检查模型配置后重试。"


__all__ = ["assemble_entity_normalization_detail"]
