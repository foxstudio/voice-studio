"""Authoritative detail reader for managed document understanding."""

from __future__ import annotations

from sqlite3 import Connection
from typing import Any

from app.domains.video_localization import (
    asr_document_understanding_operation_projection,
    asr_document_understanding_result_reader,
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
from app.schemas.video_localization_asr_document_understanding_step import (
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrDocumentUnderstandingDetailParametersV1,
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


def assemble_document_understanding_detail(
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
        != ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION
    ):
        raise OperationDetailRepairRequired(
            "ledger_identity_invalid"
        )
    status = str(ledger["status"])
    if status not in _ACTIVE_STATUSES | _TERMINAL_STATUSES:
        raise OperationDetailRepairRequired(
            "ledger_status_invalid"
        )
    parameters = _read_parameters(
        connection,
        ledger,
    )
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
    result = (
        asr_document_understanding_result_reader
        .read_result_from_connection(
            connection,
            project_id,
            operation_id,
            file_backend=file_backend,
        )
    )
    attempts = connection.execute(
        """
        SELECT
            attempt_number,
            status,
            started_at,
            completed_at,
            error_code
        FROM video_localization_operation_attempts
        WHERE project_id = ?
          AND operation_id = ?
        ORDER BY attempt_number, attempt_id
        """,
        (project_id, operation_id),
    ).fetchall()
    started_at = (
        str(attempts[0]["started_at"])
        if attempts
        and attempts[0]["started_at"] is not None
        else None
    )
    completed_at = (
        str(ledger["completed_at"])
        if ledger["completed_at"] is not None
        else None
    )
    error_code = _error_code(
        status,
        steps,
        attempts,
    )
    return VideoLocalizationOperation(
        operation_id=operation_id,
        project_id=project_id,
        kind="english_asr",
        status=status,
        label=(
            workflow_contracts
            .ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_DEFINITION
            .label
        ),
        progress=_progress(status, steps),
        error_code=error_code,
        error_message=_error_message(
            status,
            error_code,
        ),
        cancel_requested=bool(
            ledger["cancel_requested"]
        ),
        result_summary=_result_summary(
            status,
            result,
            steps,
            started_at=started_at,
            completed_at=completed_at,
            error_code=error_code,
        ),
        parameters=parameters,
        created_at=str(ledger["created_at"]),
        started_at=started_at,
        completed_at=completed_at,
    )


def _read_parameters(
    connection: Connection,
    ledger,
) -> dict[str, Any]:
    project_id = str(ledger["project_id"])
    operation_id = str(ledger["operation_id"])
    try:
        detail_record = (
            detail_store.get_detail_core_from_connection(
                connection,
                project_id,
                operation_id,
            )
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
        core.kind != "english_asr"
        or core.workflow_version
        != ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION
        or not isinstance(
            core.parameters,
            AsrDocumentUnderstandingDetailParametersV1,
        )
    ):
        raise OperationDetailRepairRequired(
            "detail_core_mismatch"
        )
    parameters = {
        **core.parameters.model_dump(
            mode="json",
            exclude={"parameters_schema_version"},
        ),
        "execution_mode": "stop_after",
        "stop_after_step": "understand_document",
    }
    public_parameters = {
        **parameters,
        "scope": operation_state.operation_scope(
            "english_asr",
            parameters,
        ),
    }
    if (
        ledger_store.parameters_fingerprint(
            public_parameters
        )
        != str(ledger["parameters_fingerprint"])
    ):
        raise OperationDetailRepairRequired(
            "detail_parameters_mismatch"
        )
    return public_parameters


def _result_summary(
    status: str,
    result,
    steps,
    *,
    started_at: str | None,
    completed_at: str | None,
    error_code: str | None,
) -> dict[str, Any]:
    if status == "success":
        if result is None:
            raise OperationDetailRepairRequired(
                "successful_workflow_step_missing"
            )
        summary = (
            asr_document_understanding_operation_projection
            .success_summary(result)
        )
        managed_local_detail.apply_workflow_duration(
            summary,
            steps,
            started_at=started_at,
            completed_at=completed_at,
        )
        return summary
    if status == "queued":
        summary = (
            asr_document_understanding_operation_projection
            .initial_summary()
        )
    else:
        summary = (
            asr_document_understanding_operation_projection
            .running_summary()
        )
        summary["stage"] = {
            "failed": "全文理解失败",
            "cancelled": "全文理解已取消",
        }.get(status, summary["stage"])
    latest = managed_local_detail.latest_step(
        steps,
        lambda step: step.step_id
        == "finalize_document_understanding",
    )
    step = summary["task_step_results"][
        "understand_document"
    ]
    if latest is not None:
        step["status"] = (
            managed_local_detail.normalized_step_status(
                latest.status
            )
        )
    elif status == "failed":
        step["status"] = "failed"
    elif status == "cancelled":
        step["status"] = "cancelled"
    managed_local_detail.apply_workflow_duration(
        summary,
        steps,
        started_at=started_at,
        completed_at=completed_at,
    )
    if status == "failed":
        summary["error_detail"] = {
            "status": "failed",
            "summary": _error_message(
                status,
                error_code,
            ),
            "metrics": (
                [
                    {
                        "label": "错误代码",
                        "value": error_code,
                    }
                ]
                if error_code
                else []
            ),
            "sections": [],
            "notes": [
                (
                    "如果付费调用结果未知，系统不会自动重发；"
                    "请先确认服务商侧结果。"
                )
            ],
        }
    return summary


def _progress(status: str, steps) -> float:
    if status in _TERMINAL_STATUSES:
        return 1.0
    if status == "queued":
        return 0.0
    if any(
        step.step_id
        == "finalize_document_understanding"
        and step.status == "success"
        for step in steps
    ):
        return 0.9
    if any(
        step.step_id.startswith("document_call_")
        and step.status == "success"
        for step in steps
    ):
        return 0.6
    return 0.15


def _error_code(status: str, steps, attempts) -> str | None:
    if status != "failed":
        return None
    failed = managed_local_detail.latest_step(
        steps,
        lambda step: step.status
        in {"failed", "result_unknown"},
    )
    if failed is not None and failed.error_code:
        return failed.error_code
    for attempt in reversed(attempts):
        value = str(
            attempt["error_code"] or ""
        ).strip()
        if value:
            return value
    return "VIDEO_LOCALIZATION_OPERATION_FAILED"


def _error_message(
    status: str,
    error_code: str | None,
) -> str | None:
    if status != "failed":
        return None
    if error_code and "RESULT_UNKNOWN" in error_code:
        return (
            "模型调用结果未知。为避免重复计费，"
            "系统没有自动重发。"
        )
    return "全文理解未完成，请查看错误详情后重试。"


__all__ = [
    "assemble_document_understanding_detail",
]
