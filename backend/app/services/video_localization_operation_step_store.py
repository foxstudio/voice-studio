from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import Connection
from typing import Literal

from app.services import database
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    require_active_execution_fence,
)


StepCostClass = Literal[
    "local_free",
    "external_free",
    "external_paid",
]
StepStatus = Literal[
    "prepared",
    "submitted",
    "result_unknown",
    "success",
    "failed",
    "cancelled",
]
TerminalStepStatus = Literal["success", "failed", "cancelled"]
StepPrepareOutcome = Literal["created", "reused"]

STEP_ATTEMPT_SCHEMA_VERSION = "operation-step-attempt-v1"
_COST_CLASSES = frozenset(
    {"local_free", "external_free", "external_paid"}
)
_TERMINAL_STATUSES = frozenset({"success", "failed", "cancelled"})


class StepIdentityConflict(RuntimeError):
    """An idempotent step identity was reused with different metadata."""


class StepTransitionConflict(RuntimeError):
    """A requested step mutation does not follow the durable state machine."""


class ProviderIdempotencyConflict(RuntimeError):
    """A Provider idempotency key already belongs to another step attempt."""


class StepSchemaError(RuntimeError):
    """A persisted step row uses an unsupported storage contract."""


@dataclass(frozen=True)
class OperationStepAttempt:
    step_attempt_id: str
    project_id: str
    operation_id: str
    operation_attempt_id: str
    step_id: str
    step_schema_version: str
    step_attempt_number: int
    fencing_token: int
    workflow_version: str
    input_fingerprint: str
    cost_class: StepCostClass
    provider_name: str | None
    provider_idempotency_key: str | None
    provider_request_id: str | None
    status: StepStatus
    status_revision: int
    prepared_at: str
    submitted_at: str | None = None
    result_unknown_at: str | None = None
    completed_at: str | None = None
    output_fingerprint: str | None = None
    error_code: str | None = None


class UnresolvedProviderResult(RuntimeError):
    """A previous paid submission with the same input is unresolved."""

    def __init__(self, step: OperationStepAttempt):
        super().__init__(
            "a previous paid Provider result is unresolved"
        )
        self.step = step


@dataclass(frozen=True)
class StepPrepareDecision:
    outcome: StepPrepareOutcome
    step: OperationStepAttempt


def prepare_step(
    execution_fence: ExecutionFence,
    *,
    step_id: str,
    workflow_version: str,
    input_fingerprint: str,
    cost_class: StepCostClass,
    observed_at: datetime,
    provider_name: str | None = None,
    provider_idempotency_key: str | None = None,
) -> StepPrepareDecision:
    normalized = _validate_prepare_input(
        step_id=step_id,
        workflow_version=workflow_version,
        input_fingerprint=input_fingerprint,
        cost_class=cost_class,
        provider_name=provider_name,
        provider_idempotency_key=provider_idempotency_key,
    )
    observed_at_ms = _epoch_milliseconds(observed_at)
    prepared_at = _utc_iso(observed_at)
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_active_execution_fence(
            connection,
            execution_fence,
            target_project_id=execution_fence.project_id,
            observed_at_ms=observed_at_ms,
        )
        _require_workflow_version(
            connection,
            execution_fence,
            normalized["workflow_version"],
        )
        existing = _read_idempotent_step(
            connection,
            execution_fence,
            step_id=normalized["step_id"],
            input_fingerprint=normalized["input_fingerprint"],
        )
        if existing is not None:
            _require_same_prepare_metadata(existing, normalized)
            return StepPrepareDecision(outcome="reused", step=existing)
        reusable_success = _read_reusable_success_step(
            connection,
            execution_fence,
            step_id=normalized["step_id"],
            workflow_version=normalized["workflow_version"],
            input_fingerprint=normalized["input_fingerprint"],
        )
        if reusable_success is not None:
            _require_reusable_success_metadata(
                reusable_success,
                normalized,
            )
            return StepPrepareDecision(
                outcome="reused",
                step=reusable_success,
            )
        _require_no_unresolved_paid_input(
            connection,
            execution_fence,
            normalized,
        )
        _require_provider_key_available(
            connection,
            provider_name=normalized["provider_name"],
            provider_idempotency_key=(
                normalized["provider_idempotency_key"]
            ),
        )
        counter = connection.execute(
            """
            SELECT
                COALESCE(MAX(step_attempt_number), 0) AS attempt_number
            FROM video_localization_operation_step_attempts
            WHERE project_id = ?
              AND operation_id = ?
              AND step_id = ?
            """,
            (
                execution_fence.project_id,
                execution_fence.operation_id,
                normalized["step_id"],
            ),
        ).fetchone()
        step_attempt_number = int(counter["attempt_number"]) + 1
        step_attempt_id = uuid.uuid4().hex
        try:
            connection.execute(
                """
                INSERT INTO
                    video_localization_operation_step_attempts (
                        step_attempt_id,
                        project_id,
                        operation_id,
                        operation_attempt_id,
                        step_id,
                        step_schema_version,
                        step_attempt_number,
                        fencing_token,
                        workflow_version,
                        input_fingerprint,
                        cost_class,
                        provider_name,
                        provider_idempotency_key,
                        status,
                        prepared_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'prepared', ?)
                """,
                (
                    step_attempt_id,
                    execution_fence.project_id,
                    execution_fence.operation_id,
                    execution_fence.attempt_id,
                    normalized["step_id"],
                    STEP_ATTEMPT_SCHEMA_VERSION,
                    step_attempt_number,
                    execution_fence.fencing_token,
                    normalized["workflow_version"],
                    normalized["input_fingerprint"],
                    normalized["cost_class"],
                    normalized["provider_name"],
                    normalized["provider_idempotency_key"],
                    prepared_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if (
                normalized["provider_name"] is not None
                and normalized["provider_idempotency_key"] is not None
            ):
                raise ProviderIdempotencyConflict(
                    "provider idempotency key already belongs to "
                    "another step attempt"
                ) from exc
            raise
        created = _read_step(
            connection,
            step_attempt_id,
        )
        assert created is not None
    return StepPrepareDecision(outcome="created", step=created)


def mark_step_submitted(
    step_attempt_id: str,
    *,
    execution_fence: ExecutionFence,
    observed_at: datetime,
) -> OperationStepAttempt:
    return _transition_step(
        step_attempt_id,
        execution_fence=execution_fence,
        observed_at=observed_at,
        target_status="submitted",
    )


def record_provider_request_id(
    step_attempt_id: str,
    *,
    execution_fence: ExecutionFence,
    provider_request_id: str,
    observed_at: datetime,
) -> OperationStepAttempt:
    request_id = _required_text(
        provider_request_id,
        "provider request ID",
    )
    observed_at_ms = _epoch_milliseconds(observed_at)
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = require_step_execution_fence(
            connection,
            step_attempt_id,
            execution_fence=execution_fence,
            observed_at_ms=observed_at_ms,
        )
        if current.cost_class == "local_free":
            raise StepTransitionConflict(
                "local step does not have a Provider request ID"
            )
        if current.status not in {"submitted", "result_unknown"}:
            raise StepTransitionConflict(
                "Provider request ID requires a submitted step"
            )
        if current.provider_request_id is not None:
            if current.provider_request_id != request_id:
                raise StepTransitionConflict(
                    "Provider request ID is immutable once recorded"
                )
            return current
        connection.execute(
            """
            UPDATE video_localization_operation_step_attempts
            SET
                provider_request_id = ?,
                status_revision = status_revision + 1
            WHERE step_attempt_id = ?
              AND provider_request_id IS NULL
            """,
            (request_id, step_attempt_id),
        )
        updated = _read_step(connection, step_attempt_id)
        assert updated is not None
    return updated


def recover_interrupted_provider_steps(
    execution_fence: ExecutionFence,
    *,
    observed_at: datetime,
) -> tuple[OperationStepAttempt, ...]:
    """Convert older submitted Provider calls into durable unknown results.

    The current live fence proves that older worker leases for the same
    operation no longer own writes. Prepared steps are untouched because no
    network submission was durably recorded.
    """

    observed_at_ms = _epoch_milliseconds(observed_at)
    observed_at_text = _utc_iso(observed_at)
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_active_execution_fence(
            connection,
            execution_fence,
            target_project_id=execution_fence.project_id,
            observed_at_ms=observed_at_ms,
        )
        rows = connection.execute(
            """
            SELECT *
            FROM video_localization_operation_step_attempts
            WHERE project_id = ?
              AND operation_id = ?
              AND operation_attempt_id != ?
              AND fencing_token < ?
              AND cost_class IN ('external_free', 'external_paid')
              AND status = 'submitted'
            ORDER BY step_attempt_number, step_attempt_id
            """,
            (
                execution_fence.project_id,
                execution_fence.operation_id,
                execution_fence.attempt_id,
                execution_fence.fencing_token,
            ),
        ).fetchall()
        recovered: list[OperationStepAttempt] = []
        for row in rows:
            current = _step_from_row(row)
            updated_row = connection.execute(
                """
                UPDATE video_localization_operation_step_attempts
                SET
                    status = 'result_unknown',
                    status_revision = status_revision + 1,
                    result_unknown_at = ?,
                    error_code = 'PROVIDER_EXECUTION_INTERRUPTED'
                WHERE step_attempt_id = ?
                  AND status = 'submitted'
                  AND status_revision = ?
                """,
                (
                    observed_at_text,
                    current.step_attempt_id,
                    current.status_revision,
                ),
            )
            if updated_row.rowcount != 1:
                raise StepTransitionConflict(
                    "interrupted Provider step changed during recovery"
                )
            updated = _read_step(
                connection,
                current.step_attempt_id,
            )
            assert updated is not None
            recovered.append(updated)
    return tuple(recovered)


def mark_step_result_unknown(
    step_attempt_id: str,
    *,
    execution_fence: ExecutionFence,
    observed_at: datetime,
    error_code: str,
) -> OperationStepAttempt:
    return _transition_step(
        step_attempt_id,
        execution_fence=execution_fence,
        observed_at=observed_at,
        target_status="result_unknown",
        error_code=_required_text(error_code, "error code"),
    )


def finish_step(
    step_attempt_id: str,
    *,
    execution_fence: ExecutionFence,
    status: TerminalStepStatus,
    observed_at: datetime,
    output_fingerprint: str | None = None,
    error_code: str | None = None,
) -> OperationStepAttempt:
    normalized_output, normalized_error = (
        _validate_terminal_result(
            status,
            output_fingerprint=output_fingerprint,
            error_code=error_code,
        )
    )
    return _transition_step(
        step_attempt_id,
        execution_fence=execution_fence,
        observed_at=observed_at,
        target_status=status,
        output_fingerprint=normalized_output,
        error_code=normalized_error,
    )


def get_step_attempt(
    step_attempt_id: str,
) -> OperationStepAttempt | None:
    with database.conn() as connection:
        return get_step_attempt_from_connection(
            connection,
            step_attempt_id,
        )


def get_step_attempt_from_connection(
    connection: Connection,
    step_attempt_id: str,
) -> OperationStepAttempt | None:
    return _read_step(
        connection,
        _required_text(step_attempt_id, "step attempt ID"),
    )


def list_step_attempts(
    project_id: str,
    operation_id: str,
) -> list[OperationStepAttempt]:
    with database.conn() as connection:
        return list_step_attempts_from_connection(
            connection,
            project_id,
            operation_id,
        )


def list_step_attempts_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
) -> list[OperationStepAttempt]:
    rows = connection.execute(
        """
        SELECT *
        FROM video_localization_operation_step_attempts
        WHERE project_id = ?
          AND operation_id = ?
        ORDER BY
            step_id,
            step_attempt_number,
            step_attempt_id
        """,
        (project_id, operation_id),
    ).fetchall()
    return [_step_from_row(row) for row in rows]


def delete_project(
    connection: Connection,
    project_id: str,
) -> None:
    connection.execute(
        """
        DELETE FROM video_localization_operation_step_attempts
        WHERE project_id = ?
        """,
        (project_id,),
    )


def _transition_step(
    step_attempt_id: str,
    *,
    execution_fence: ExecutionFence,
    observed_at: datetime,
    target_status: StepStatus,
    output_fingerprint: str | None = None,
    error_code: str | None = None,
) -> OperationStepAttempt:
    observed_at_ms = _epoch_milliseconds(observed_at)
    observed_at_text = _utc_iso(observed_at)
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = require_step_execution_fence(
            connection,
            step_attempt_id,
            execution_fence=execution_fence,
            observed_at_ms=observed_at_ms,
        )
        if current.status == target_status:
            if (
                current.output_fingerprint == output_fingerprint
                and current.error_code == error_code
            ):
                return current
            raise StepTransitionConflict(
                "step terminal fields conflict with the persisted state"
            )
        if current.status == "result_unknown":
            raise StepTransitionConflict(
                "result_unknown requires explicit Provider or human "
                "resolution"
            )
        if current.status in _TERMINAL_STATUSES:
            raise StepTransitionConflict(
                "terminal step cannot transition to another state"
            )
        if target_status == "submitted":
            if current.status != "prepared":
                raise StepTransitionConflict(
                    "only prepared step can become submitted"
                )
            if current.cost_class == "local_free":
                raise StepTransitionConflict(
                    "local step must not enter submitted"
                )
        elif target_status == "result_unknown":
            if current.status != "submitted":
                raise StepTransitionConflict(
                    "only submitted step can become result_unknown"
                )
        elif target_status in _TERMINAL_STATUSES:
            if (
                current.cost_class != "local_free"
                and current.status != "submitted"
            ):
                raise StepTransitionConflict(
                    "external step must be submitted before completion"
                )
            if (
                current.cost_class == "local_free"
                and current.status != "prepared"
            ):
                raise StepTransitionConflict(
                    "local step must complete from prepared"
                )
        else:  # pragma: no cover - typed callers and tests guard this
            raise ValueError("unsupported step transition")
        submitted_at = (
            observed_at_text
            if target_status == "submitted"
            else current.submitted_at
        )
        result_unknown_at = (
            observed_at_text
            if target_status == "result_unknown"
            else current.result_unknown_at
        )
        completed_at = (
            observed_at_text
            if target_status in _TERMINAL_STATUSES
            else current.completed_at
        )
        connection.execute(
            """
            UPDATE video_localization_operation_step_attempts
            SET
                status = ?,
                status_revision = status_revision + 1,
                submitted_at = ?,
                result_unknown_at = ?,
                completed_at = ?,
                output_fingerprint = ?,
                error_code = ?
            WHERE step_attempt_id = ?
              AND status_revision = ?
            """,
            (
                target_status,
                submitted_at,
                result_unknown_at,
                completed_at,
                output_fingerprint,
                error_code,
                step_attempt_id,
                current.status_revision,
            ),
        )
        updated = _read_step(connection, step_attempt_id)
        assert updated is not None
    return updated


def require_step_execution_fence(
    connection: Connection,
    step_attempt_id: str,
    *,
    execution_fence: ExecutionFence,
    observed_at_ms: int,
) -> OperationStepAttempt:
    """Validate that one step belongs to the caller's live worker fence."""

    current = _read_step(connection, step_attempt_id)
    if current is None:
        raise StepIdentityConflict("step attempt does not exist")
    if (
        current.project_id != execution_fence.project_id
        or current.operation_id != execution_fence.operation_id
        or current.operation_attempt_id != execution_fence.attempt_id
        or current.fencing_token != execution_fence.fencing_token
    ):
        raise StepIdentityConflict(
            "step attempt belongs to another execution fence"
        )
    require_active_execution_fence(
        connection,
        execution_fence,
        target_project_id=current.project_id,
        observed_at_ms=observed_at_ms,
    )
    return current


def _require_workflow_version(
    connection: Connection,
    execution_fence: ExecutionFence,
    workflow_version: str,
) -> None:
    row = connection.execute(
        """
        SELECT workflow_version
        FROM video_localization_operations
        WHERE project_id = ?
          AND operation_id = ?
        """,
        (
            execution_fence.project_id,
            execution_fence.operation_id,
        ),
    ).fetchone()
    if row is None:
        raise StepIdentityConflict(
            "authoritative operation ledger entry is missing"
        )
    if str(row["workflow_version"]) != workflow_version:
        raise StepIdentityConflict(
            "step workflow version differs from operation ledger"
        )


def _read_idempotent_step(
    connection: Connection,
    execution_fence: ExecutionFence,
    *,
    step_id: str,
    input_fingerprint: str,
) -> OperationStepAttempt | None:
    row = connection.execute(
        """
        SELECT *
        FROM video_localization_operation_step_attempts
        WHERE project_id = ?
          AND operation_id = ?
          AND operation_attempt_id = ?
          AND step_id = ?
          AND input_fingerprint = ?
        """,
        (
            execution_fence.project_id,
            execution_fence.operation_id,
            execution_fence.attempt_id,
            step_id,
            input_fingerprint,
        ),
    ).fetchone()
    return _step_from_row(row) if row is not None else None


def _require_same_prepare_metadata(
    current: OperationStepAttempt,
    normalized: dict[str, str | None],
) -> None:
    if (
        current.workflow_version != normalized["workflow_version"]
        or current.cost_class != normalized["cost_class"]
        or current.provider_name != normalized["provider_name"]
        or current.provider_idempotency_key
        != normalized["provider_idempotency_key"]
    ):
        raise StepIdentityConflict(
            "idempotent step identity has different prepare metadata"
        )


def _read_reusable_success_step(
    connection: Connection,
    execution_fence: ExecutionFence,
    *,
    step_id: str,
    workflow_version: str,
    input_fingerprint: str,
) -> OperationStepAttempt | None:
    row = connection.execute(
        """
        SELECT *
        FROM video_localization_operation_step_attempts
        WHERE project_id = ?
          AND operation_id = ?
          AND operation_attempt_id != ?
          AND step_id = ?
          AND workflow_version = ?
          AND input_fingerprint = ?
          AND status = 'success'
        ORDER BY step_attempt_number DESC
        LIMIT 1
        """,
        (
            execution_fence.project_id,
            execution_fence.operation_id,
            execution_fence.attempt_id,
            step_id,
            workflow_version,
            input_fingerprint,
        ),
    ).fetchone()
    return _step_from_row(row) if row is not None else None


def _require_reusable_success_metadata(
    current: OperationStepAttempt,
    normalized: dict[str, str | None],
) -> None:
    if (
        current.workflow_version != normalized["workflow_version"]
        or current.cost_class != normalized["cost_class"]
        or current.provider_name != normalized["provider_name"]
    ):
        raise StepIdentityConflict(
            "reusable successful step has different Provider metadata"
        )


def _require_no_unresolved_paid_input(
    connection: Connection,
    execution_fence: ExecutionFence,
    normalized: dict[str, str | None],
) -> None:
    if normalized["cost_class"] != "external_paid":
        return
    row = connection.execute(
        """
        SELECT *
        FROM video_localization_operation_step_attempts
        WHERE project_id = ?
          AND operation_id = ?
          AND step_id = ?
          AND workflow_version = ?
          AND input_fingerprint = ?
          AND cost_class = 'external_paid'
          AND operation_attempt_id != ?
          AND status IN ('submitted', 'result_unknown')
        ORDER BY step_attempt_number DESC
        LIMIT 1
        """,
        (
            execution_fence.project_id,
            execution_fence.operation_id,
            normalized["step_id"],
            normalized["workflow_version"],
            normalized["input_fingerprint"],
            execution_fence.attempt_id,
        ),
    ).fetchone()
    if row is not None:
        raise UnresolvedProviderResult(_step_from_row(row))


def _require_provider_key_available(
    connection: Connection,
    *,
    provider_name: str | None,
    provider_idempotency_key: str | None,
) -> None:
    if provider_name is None or provider_idempotency_key is None:
        return
    row = connection.execute(
        """
        SELECT step_attempt_id
        FROM video_localization_operation_step_attempts
        WHERE provider_name = ?
          AND provider_idempotency_key = ?
        """,
        (provider_name, provider_idempotency_key),
    ).fetchone()
    if row is not None:
        raise ProviderIdempotencyConflict(
            "provider idempotency key already belongs to another "
            "step attempt"
        )


def _read_step(
    connection: Connection,
    step_attempt_id: str,
) -> OperationStepAttempt | None:
    row = connection.execute(
        """
        SELECT *
        FROM video_localization_operation_step_attempts
        WHERE step_attempt_id = ?
        """,
        (step_attempt_id,),
    ).fetchone()
    return _step_from_row(row) if row is not None else None


def _step_from_row(row) -> OperationStepAttempt:
    step_schema_version = str(row["step_schema_version"])
    if step_schema_version != STEP_ATTEMPT_SCHEMA_VERSION:
        raise StepSchemaError(
            "persisted step attempt schema version is unsupported"
        )
    return OperationStepAttempt(
        step_attempt_id=str(row["step_attempt_id"]),
        project_id=str(row["project_id"]),
        operation_id=str(row["operation_id"]),
        operation_attempt_id=str(row["operation_attempt_id"]),
        step_id=str(row["step_id"]),
        step_schema_version=step_schema_version,
        step_attempt_number=int(row["step_attempt_number"]),
        fencing_token=int(row["fencing_token"]),
        workflow_version=str(row["workflow_version"]),
        input_fingerprint=str(row["input_fingerprint"]),
        cost_class=str(row["cost_class"]),
        provider_name=_nullable_text(row["provider_name"]),
        provider_idempotency_key=_nullable_text(
            row["provider_idempotency_key"]
        ),
        provider_request_id=_nullable_text(
            row["provider_request_id"]
        ),
        status=str(row["status"]),
        status_revision=int(row["status_revision"]),
        prepared_at=str(row["prepared_at"]),
        submitted_at=_nullable_text(row["submitted_at"]),
        result_unknown_at=_nullable_text(
            row["result_unknown_at"]
        ),
        completed_at=_nullable_text(row["completed_at"]),
        output_fingerprint=_nullable_text(
            row["output_fingerprint"]
        ),
        error_code=_nullable_text(row["error_code"]),
    )


def _validate_prepare_input(
    *,
    step_id: str,
    workflow_version: str,
    input_fingerprint: str,
    cost_class: str,
    provider_name: str | None,
    provider_idempotency_key: str | None,
) -> dict[str, str | None]:
    normalized_cost = _required_text(cost_class, "cost class")
    if normalized_cost not in _COST_CLASSES:
        raise ValueError("step cost class is invalid")
    normalized_provider = _optional_text(
        provider_name,
        "provider name",
    )
    normalized_key = _optional_text(
        provider_idempotency_key,
        "provider idempotency key",
    )
    if normalized_cost == "local_free":
        if normalized_provider is not None or normalized_key is not None:
            raise ValueError(
                "local step must not define provider identity"
            )
    elif normalized_provider is None or normalized_key is None:
        raise ValueError(
            "external step requires provider name and idempotency key"
        )
    return {
        "step_id": _required_text(step_id, "step ID"),
        "workflow_version": _required_text(
            workflow_version,
            "workflow version",
        ),
        "input_fingerprint": _required_text(
            input_fingerprint,
            "input fingerprint",
        ),
        "cost_class": normalized_cost,
        "provider_name": normalized_provider,
        "provider_idempotency_key": normalized_key,
    }


def _validate_terminal_result(
    status: str,
    *,
    output_fingerprint: str | None,
    error_code: str | None,
) -> tuple[str | None, str | None]:
    if status not in _TERMINAL_STATUSES:
        raise ValueError("step terminal status is invalid")
    normalized_output = _optional_text(
        output_fingerprint,
        "output fingerprint",
    )
    normalized_error = _optional_text(error_code, "error code")
    if status == "success":
        if normalized_output is None:
            raise ValueError(
                "successful step requires an output fingerprint"
            )
        if normalized_error is not None:
            raise ValueError(
                "successful step must not have an error code"
            )
    elif status == "failed":
        if normalized_error is None:
            raise ValueError("failed step requires an error code")
        if normalized_output is not None:
            raise ValueError(
                "failed step must not have an output fingerprint"
            )
    return normalized_output, normalized_error


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) > 512:
        raise ValueError(f"{label} is too long")
    return normalized


def _optional_text(
    value: str | None,
    label: str,
) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)


def _nullable_text(value) -> str | None:
    return str(value) if value is not None else None


def _epoch_milliseconds(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("step timestamp must include timezone")
    return int(value.timestamp() * 1_000)


def _utc_iso(value: datetime) -> str:
    _epoch_milliseconds(value)
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


__all__ = [
    "OperationStepAttempt",
    "ProviderIdempotencyConflict",
    "STEP_ATTEMPT_SCHEMA_VERSION",
    "StepIdentityConflict",
    "StepPrepareDecision",
    "StepSchemaError",
    "StepTransitionConflict",
    "UnresolvedProviderResult",
    "finish_step",
    "get_step_attempt",
    "get_step_attempt_from_connection",
    "list_step_attempts",
    "list_step_attempts_from_connection",
    "mark_step_result_unknown",
    "mark_step_submitted",
    "prepare_step",
    "record_provider_request_id",
    "recover_interrupted_provider_steps",
    "require_step_execution_fence",
]
