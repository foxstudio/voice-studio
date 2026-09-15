from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import Connection
from typing import Literal

from app.services import database
from app.services import video_localization_operation_step_store


ADJUDICATION_SCHEMA_VERSION = "operation-step-adjudication-v1"
AdjudicationDecision = Literal["success", "failed"]
AdjudicationSource = Literal["provider_query", "human_review"]
AdjudicationOutcome = Literal["created", "reused"]

_DECISIONS = frozenset({"success", "failed"})
_SOURCES = frozenset({"provider_query", "human_review"})
_REASON_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
_ERROR_CODE_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_.:-]{1,127}$"
)
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AdjudicationConflict(RuntimeError):
    """A decision cannot be applied to the current durable step."""


class AdjudicationSchemaError(RuntimeError):
    """A persisted adjudication uses an unsupported contract."""


@dataclass(frozen=True)
class StepResultAdjudication:
    adjudication_id: str
    adjudication_schema_version: str
    step_attempt_id: str
    project_id: str
    operation_id: str
    decision: AdjudicationDecision
    source: AdjudicationSource
    reason_code: str
    expected_status_revision: int
    resulting_status_revision: int
    provider_request_id: str | None
    output_fingerprint: str | None
    error_code: str | None
    decided_at: str


@dataclass(frozen=True)
class StepAdjudicationDecision:
    outcome: AdjudicationOutcome
    adjudication: StepResultAdjudication
    step: (
        video_localization_operation_step_store.OperationStepAttempt
    )


def adjudicate_result_unknown(
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    expected_status_revision: int,
    decision: AdjudicationDecision,
    source: AdjudicationSource,
    reason_code: str,
    observed_at: datetime,
    output_fingerprint: str | None = None,
    error_code: str | None = None,
) -> StepAdjudicationDecision:
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        return adjudicate_result_unknown_from_connection(
            connection,
            project_id,
            operation_id,
            step_attempt_id,
            expected_status_revision=expected_status_revision,
            decision=decision,
            source=source,
            reason_code=reason_code,
            observed_at=observed_at,
            output_fingerprint=output_fingerprint,
            error_code=error_code,
        )


def adjudicate_result_unknown_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    expected_status_revision: int,
    decision: AdjudicationDecision,
    source: AdjudicationSource,
    reason_code: str,
    observed_at: datetime,
    output_fingerprint: str | None = None,
    error_code: str | None = None,
) -> StepAdjudicationDecision:
    """Adjudicate inside an existing caller-owned write transaction."""

    if not connection.in_transaction:
        raise RuntimeError(
            "adjudication requires a caller-owned transaction"
        )
    normalized = _validate_command(
        project_id=project_id,
        operation_id=operation_id,
        step_attempt_id=step_attempt_id,
        expected_status_revision=expected_status_revision,
        decision=decision,
        source=source,
        reason_code=reason_code,
        observed_at=observed_at,
        output_fingerprint=output_fingerprint,
        error_code=error_code,
    )
    existing = _read_adjudication(
        connection,
        normalized["step_attempt_id"],
    )
    if existing is not None:
        if not _matches_command(existing, normalized):
            raise AdjudicationConflict(
                "step already has a different adjudication"
            )
        step = (
            video_localization_operation_step_store
            .get_step_attempt_from_connection(
                connection,
                normalized["step_attempt_id"],
            )
        )
        if step is None:
            raise AdjudicationConflict(
                "adjudicated step attempt no longer exists"
            )
        _require_adjudicated_step_matches(
            existing,
            step,
        )
        return StepAdjudicationDecision(
            outcome="reused",
            adjudication=existing,
            step=step,
        )

    current = (
        video_localization_operation_step_store
        .get_step_attempt_from_connection(
            connection,
            normalized["step_attempt_id"],
        )
    )
    if current is None:
        raise AdjudicationConflict(
            "step attempt does not exist"
        )
    if (
        current.project_id != normalized["project_id"]
        or current.operation_id
        != normalized["operation_id"]
    ):
        raise AdjudicationConflict(
            "step adjudication identity does not match"
        )
    operation_exists = connection.execute(
        """
        SELECT 1
        FROM video_localization_operations
        WHERE project_id = ?
          AND operation_id = ?
        """,
        (
            normalized["project_id"],
            normalized["operation_id"],
        ),
    ).fetchone()
    if operation_exists is None:
        raise AdjudicationConflict(
            "operation ledger identity does not exist"
        )
    if (
        normalized["source"] == "provider_query"
        and current.provider_request_id is None
    ):
        raise AdjudicationConflict(
            "Provider query adjudication requires a request ID"
        )
    previous, updated = _transition_unknown_step(
        connection,
        current,
        normalized,
    )
    provider_request_id = previous.provider_request_id
    adjudication_id = uuid.uuid4().hex
    connection.execute(
        """
        INSERT INTO
            video_localization_operation_step_adjudications (
                adjudication_id,
                adjudication_schema_version,
                step_attempt_id,
                project_id,
                operation_id,
                decision,
                source,
                reason_code,
                expected_status_revision,
                resulting_status_revision,
                provider_request_id,
                output_fingerprint,
                error_code,
                decided_at
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            adjudication_id,
            ADJUDICATION_SCHEMA_VERSION,
            normalized["step_attempt_id"],
            normalized["project_id"],
            normalized["operation_id"],
            normalized["decision"],
            normalized["source"],
            normalized["reason_code"],
            normalized["expected_status_revision"],
            updated.status_revision,
            provider_request_id,
            normalized["output_fingerprint"],
            normalized["error_code"],
            normalized["decided_at"],
        ),
    )
    adjudication = _read_adjudication(
        connection,
        normalized["step_attempt_id"],
    )
    assert adjudication is not None
    return StepAdjudicationDecision(
        outcome="created",
        adjudication=adjudication,
        step=updated,
    )


def get_adjudication(
    step_attempt_id: str,
) -> StepResultAdjudication | None:
    with database.conn() as connection:
        return _read_adjudication(
            connection,
            _required_text(
                step_attempt_id,
                "step attempt ID",
            ),
        )


def validate_adjudication_command(
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    expected_status_revision: int,
    decision: AdjudicationDecision,
    source: AdjudicationSource,
    reason_code: str,
    observed_at: datetime,
    output_fingerprint: str | None = None,
    error_code: str | None = None,
) -> None:
    """Validate a command before another service performs side effects."""

    _validate_command(
        project_id=project_id,
        operation_id=operation_id,
        step_attempt_id=step_attempt_id,
        expected_status_revision=expected_status_revision,
        decision=decision,
        source=source,
        reason_code=reason_code,
        observed_at=observed_at,
        output_fingerprint=output_fingerprint,
        error_code=error_code,
    )


def list_operation_adjudications(
    project_id: str,
    operation_id: str,
) -> list[StepResultAdjudication]:
    with database.conn() as connection:
        return list_operation_adjudications_from_connection(
            connection,
            project_id,
            operation_id,
        )


def list_operation_adjudications_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
) -> list[StepResultAdjudication]:
    rows = connection.execute(
        """
        SELECT *
        FROM video_localization_operation_step_adjudications
        WHERE project_id = ?
          AND operation_id = ?
        ORDER BY decided_at, adjudication_id
        """,
        (
            _required_text(project_id, "project ID"),
            _required_text(operation_id, "operation ID"),
        ),
    ).fetchall()
    return [_adjudication_from_row(row) for row in rows]


def delete_project(
    connection: Connection,
    project_id: str,
) -> None:
    connection.execute(
        """
        DELETE FROM video_localization_operation_step_adjudications
        WHERE project_id = ?
        """,
        (_required_text(project_id, "project ID"),),
    )


def _read_adjudication(
    connection: Connection,
    step_attempt_id: str,
) -> StepResultAdjudication | None:
    row = connection.execute(
        """
        SELECT *
        FROM video_localization_operation_step_adjudications
        WHERE step_attempt_id = ?
        """,
        (step_attempt_id,),
    ).fetchone()
    return (
        _adjudication_from_row(row)
        if row is not None
        else None
    )


def _transition_unknown_step(
    connection: Connection,
    current: (
        video_localization_operation_step_store.OperationStepAttempt
    ),
    command: dict[str, str | int | None],
) -> tuple[
    video_localization_operation_step_store.OperationStepAttempt,
    video_localization_operation_step_store.OperationStepAttempt,
]:
    if current.status != "result_unknown":
        raise AdjudicationConflict(
            "only result_unknown step can be adjudicated"
        )
    expected_revision = int(
        command["expected_status_revision"]
    )
    if current.status_revision != expected_revision:
        raise AdjudicationConflict(
            "step adjudication status revision is stale"
        )
    cursor = connection.execute(
        """
        UPDATE video_localization_operation_step_attempts
        SET
            status = ?,
            status_revision = status_revision + 1,
            completed_at = ?,
            output_fingerprint = ?,
            error_code = ?
        WHERE step_attempt_id = ?
          AND status = 'result_unknown'
          AND status_revision = ?
        """,
        (
            command["decision"],
            command["decided_at"],
            command["output_fingerprint"],
            command["error_code"],
            command["step_attempt_id"],
            expected_revision,
        ),
    )
    if cursor.rowcount != 1:
        raise AdjudicationConflict(
            "step adjudication lost its status revision"
        )
    updated = (
        video_localization_operation_step_store
        .get_step_attempt_from_connection(
            connection,
            str(command["step_attempt_id"]),
        )
    )
    assert updated is not None
    return current, updated


def _adjudication_from_row(row) -> StepResultAdjudication:
    schema_version = str(
        row["adjudication_schema_version"]
    )
    if schema_version != ADJUDICATION_SCHEMA_VERSION:
        raise AdjudicationSchemaError(
            "persisted adjudication schema version is unsupported"
        )
    decision = str(row["decision"])
    source = str(row["source"])
    reason_code = str(row["reason_code"])
    expected_revision = int(row["expected_status_revision"])
    resulting_revision = int(row["resulting_status_revision"])
    provider_request_id = _nullable_text(
        row["provider_request_id"]
    )
    output_fingerprint = _nullable_text(
        row["output_fingerprint"]
    )
    error_code = _nullable_text(row["error_code"])
    decided_at = str(row["decided_at"])
    try:
        _validate_command(
            project_id=str(row["project_id"]),
            operation_id=str(row["operation_id"]),
            step_attempt_id=str(row["step_attempt_id"]),
            expected_status_revision=expected_revision,
            decision=decision,
            source=source,
            reason_code=reason_code,
            observed_at=_parse_timestamp(decided_at),
            output_fingerprint=output_fingerprint,
            error_code=error_code,
        )
    except (TypeError, ValueError) as exc:
        raise AdjudicationSchemaError(
            "persisted adjudication fields are invalid"
        ) from exc
    if resulting_revision != expected_revision + 1:
        raise AdjudicationSchemaError(
            "persisted adjudication revision is invalid"
        )
    if source == "provider_query" and provider_request_id is None:
        raise AdjudicationSchemaError(
            "Provider query adjudication lacks request ID"
        )
    return StepResultAdjudication(
        adjudication_id=_required_text(
            str(row["adjudication_id"]),
            "adjudication ID",
        ),
        adjudication_schema_version=schema_version,
        step_attempt_id=str(row["step_attempt_id"]),
        project_id=str(row["project_id"]),
        operation_id=str(row["operation_id"]),
        decision=decision,
        source=source,
        reason_code=reason_code,
        expected_status_revision=expected_revision,
        resulting_status_revision=resulting_revision,
        provider_request_id=provider_request_id,
        output_fingerprint=output_fingerprint,
        error_code=error_code,
        decided_at=decided_at,
    )


def _validate_command(
    *,
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    expected_status_revision: int,
    decision: str,
    source: str,
    reason_code: str,
    observed_at: datetime,
    output_fingerprint: str | None,
    error_code: str | None,
) -> dict[str, str | int | None]:
    if (
        not isinstance(expected_status_revision, int)
        or isinstance(expected_status_revision, bool)
        or expected_status_revision <= 0
    ):
        raise ValueError(
            "expected status revision must be a positive integer"
        )
    normalized_decision = _required_text(
        decision,
        "adjudication decision",
    )
    if normalized_decision not in _DECISIONS:
        raise ValueError("adjudication decision is invalid")
    normalized_source = _required_text(
        source,
        "adjudication source",
    )
    if normalized_source not in _SOURCES:
        raise ValueError("adjudication source is invalid")
    normalized_reason = _required_text(
        reason_code,
        "reason code",
    )
    if _REASON_CODE_PATTERN.fullmatch(normalized_reason) is None:
        raise ValueError(
            "reason code must be a stable uppercase code"
        )
    normalized_output = _optional_text(
        output_fingerprint,
        "output fingerprint",
    )
    normalized_error = _optional_text(error_code, "error code")
    if normalized_decision == "success":
        if (
            normalized_output is None
            or _FINGERPRINT_PATTERN.fullmatch(
                normalized_output
            )
            is None
        ):
            raise ValueError(
                "successful adjudication requires a SHA-256 "
                "output fingerprint"
            )
        if normalized_error is not None:
            raise ValueError(
                "successful adjudication must not have an error code"
            )
    else:
        if normalized_output is not None:
            raise ValueError(
                "failed adjudication must not have an output "
                "fingerprint"
            )
        if (
            normalized_error is None
            or _ERROR_CODE_PATTERN.fullmatch(normalized_error)
            is None
        ):
            raise ValueError(
                "failed adjudication requires a stable error code"
            )
    return {
        "project_id": _required_text(project_id, "project ID"),
        "operation_id": _required_text(
            operation_id,
            "operation ID",
        ),
        "step_attempt_id": _required_text(
            step_attempt_id,
            "step attempt ID",
        ),
        "expected_status_revision": expected_status_revision,
        "decision": normalized_decision,
        "source": normalized_source,
        "reason_code": normalized_reason,
        "output_fingerprint": normalized_output,
        "error_code": normalized_error,
        "decided_at": _utc_iso(observed_at),
    }


def _matches_command(
    current: StepResultAdjudication,
    command: dict[str, str | int | None],
) -> bool:
    return (
        current.project_id == command["project_id"]
        and current.operation_id == command["operation_id"]
        and current.step_attempt_id
        == command["step_attempt_id"]
        and current.expected_status_revision
        == command["expected_status_revision"]
        and current.decision == command["decision"]
        and current.source == command["source"]
        and current.reason_code == command["reason_code"]
        and current.output_fingerprint
        == command["output_fingerprint"]
        and current.error_code == command["error_code"]
    )


def _require_adjudicated_step_matches(
    adjudication: StepResultAdjudication,
    step: video_localization_operation_step_store.OperationStepAttempt,
) -> None:
    if (
        step.project_id != adjudication.project_id
        or step.operation_id != adjudication.operation_id
        or step.step_attempt_id != adjudication.step_attempt_id
        or step.status != adjudication.decision
        or step.status_revision
        != adjudication.resulting_status_revision
        or step.output_fingerprint
        != adjudication.output_fingerprint
        or step.error_code != adjudication.error_code
    ):
        raise AdjudicationSchemaError(
            "adjudication and terminal step state are inconsistent"
        )


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


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "adjudication timestamp must include timezone"
        )
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _parse_timestamp(value: str) -> datetime:
    normalized = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp lacks timezone")
    return parsed


__all__ = [
    "ADJUDICATION_SCHEMA_VERSION",
    "AdjudicationConflict",
    "AdjudicationSchemaError",
    "StepAdjudicationDecision",
    "StepResultAdjudication",
    "adjudicate_result_unknown",
    "adjudicate_result_unknown_from_connection",
    "delete_project",
    "get_adjudication",
    "list_operation_adjudications",
    "list_operation_adjudications_from_connection",
    "validate_adjudication_command",
]
