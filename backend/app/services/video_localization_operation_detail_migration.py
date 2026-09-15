from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import Connection
from typing import Any, Callable, Literal

from app.schemas.video_localization_operation_detail import (
    SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
    OperationDetailCoreV1,
    SemanticTtsGroupingDetailParametersV1,
)
from app.services import database
from app.services import (
    video_localization_operation_detail_core_store as detail_store,
)
from app.services import (
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (
    video_localization_operation_store as operation_store,
)


DetailMigrationStatus = Literal[
    "planned",
    "migrated",
    "unchanged",
    "rejected",
]


@dataclass(frozen=True)
class _LegacySemanticOperation:
    """Minimal typed view of the temporary Project compatibility mirror."""

    project_id: str
    operation_id: str
    kind: str
    status: str
    cancel_requested: bool
    parameters: dict[str, Any]
    result_summary: dict[str, Any]
    created_at: str
    completed_at: str | None


@dataclass(frozen=True)
class OperationDetailMigrationResult:
    project_id: str
    operation_id: str
    status: DetailMigrationStatus
    issue_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperationDetailMigrationReport:
    apply: bool
    scanned_operation_count: int
    planned_operation_count: int
    migrated_operation_count: int
    unchanged_operation_count: int
    rejected_operation_count: int
    truncated: bool
    next_project_id: str | None
    next_operation_id: str | None
    results: tuple[OperationDetailMigrationResult, ...]

    @property
    def healthy(self) -> bool:
        return not self.truncated and not self.rejected_operation_count


def migrate_operation_details(
    *,
    apply: bool = False,
    limit: int = 1_000,
    after_project_id: str | None = None,
    after_operation_id: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> OperationDetailMigrationReport:
    """Plan or apply one bounded keyset batch of semantic-v2 cores."""

    if limit < 1 or limit > 10_000:
        raise ValueError(
            "detail migration limit must be between 1 and 10000"
        )
    cursor = _normalized_cursor(
        after_project_id,
        after_operation_id,
    )
    context: AbstractContextManager[Connection] = (
        database.conn() if apply else database.read_conn()
    )
    with context as connection:
        if apply:
            connection.execute("BEGIN IMMEDIATE")
        rows = _candidate_rows(
            connection,
            cursor=cursor,
            limit=limit + 1,
        )
        checked = rows[:limit]
        observed_at = _utc_iso(
            (clock or _utc_now)()
        )
        results = tuple(
            _migrate_candidate(
                connection,
                row,
                apply=apply,
                observed_at=observed_at,
            )
            for row in checked
        )
    counts = {
        status: sum(
            result.status == status for result in results
        )
        for status in (
            "planned",
            "migrated",
            "unchanged",
            "rejected",
        )
    }
    next_cursor = (
        (
            results[-1].project_id,
            results[-1].operation_id,
        )
        if len(rows) > limit and results
        else (None, None)
    )
    return OperationDetailMigrationReport(
        apply=apply,
        scanned_operation_count=len(results),
        planned_operation_count=counts["planned"],
        migrated_operation_count=counts["migrated"],
        unchanged_operation_count=counts["unchanged"],
        rejected_operation_count=counts["rejected"],
        truncated=len(rows) > limit,
        next_project_id=next_cursor[0],
        next_operation_id=next_cursor[1],
        results=results,
    )


def _candidate_rows(
    connection: Connection,
    *,
    cursor: tuple[str, str] | None,
    limit: int,
):
    if cursor is None:
        where = ""
        parameters: tuple[object, ...] = (
            SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
            limit,
        )
    else:
        where = (
            "AND (project_id > ? OR "
            "(project_id = ? AND operation_id > ?))"
        )
        parameters = (
            SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
            cursor[0],
            cursor[0],
            cursor[1],
            limit,
        )
    return connection.execute(
        f"""
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
        WHERE workflow_version = ?
          {where}
        ORDER BY project_id, operation_id
        LIMIT ?
        """,
        parameters,
    ).fetchall()


def _migrate_candidate(
    connection: Connection,
    ledger,
    *,
    apply: bool,
    observed_at: str,
) -> OperationDetailMigrationResult:
    project_id = str(ledger["project_id"])
    operation_id = str(ledger["operation_id"])
    try:
        existing = detail_store.get_detail_core_from_connection(
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
    ):
        return _rejected(
            project_id,
            operation_id,
            "detail_core_invalid",
        )
    if existing is not None:
        return OperationDetailMigrationResult(
            project_id=project_id,
            operation_id=operation_id,
            status="unchanged",
        )
    project_exists, payload = (
        operation_store
        .read_project_mirror_operation_from_connection(
            connection,
            project_id,
            operation_id,
        )
    )
    if not project_exists:
        return _rejected(
            project_id,
            operation_id,
            "project_missing",
        )
    if payload is None:
        return _rejected(
            project_id,
            operation_id,
            "mirror_operation_missing",
        )
    operation = _parse_legacy_operation(payload)
    if operation is None:
        return _rejected(
            project_id,
            operation_id,
            "mirror_operation_invalid",
        )
    issues = _ledger_mirror_issues(ledger, operation)
    if issues:
        return OperationDetailMigrationResult(
            project_id=project_id,
            operation_id=operation_id,
            status="rejected",
            issue_codes=issues,
        )
    if operation.kind != "semantic_tts_grouping":
        return _rejected(
            project_id,
            operation_id,
            "unsupported_workflow",
        )
    try:
        core = _detail_core_from_legacy_operation(operation)
    except (KeyError, TypeError, ValueError):
        return _rejected(
            project_id,
            operation_id,
            "mirror_operation_invalid",
        )
    if not apply:
        return OperationDetailMigrationResult(
            project_id=project_id,
            operation_id=operation_id,
            status="planned",
        )
    try:
        outcome = detail_store.put_detail_core_from_connection(
            connection,
            core,
            written_at=observed_at,
        )
    except (
        detail_store.OperationDetailCoreConflict,
        detail_store.OperationDetailCoreIdentityConflict,
        detail_store.OperationDetailCoreIntegrityError,
        detail_store.OperationDetailCoreSchemaError,
        TypeError,
        ValueError,
    ):
        return _rejected(
            project_id,
            operation_id,
            "detail_core_write_failed",
        )
    return OperationDetailMigrationResult(
        project_id=project_id,
        operation_id=operation_id,
        status=(
            "migrated" if outcome == "created" else "unchanged"
        ),
    )


def _ledger_mirror_issues(
    ledger,
    operation: _LegacySemanticOperation,
) -> tuple[str, ...]:
    issues: list[str] = []
    if (
        operation.project_id != str(ledger["project_id"])
        or operation.operation_id != str(ledger["operation_id"])
        or operation.kind != str(ledger["kind"])
        or operation.created_at != str(ledger["created_at"])
    ):
        issues.append("mirror_identity_mismatch")
    workflow_version = str(
        operation.result_summary.get(
            "workflow_schema_version"
        )
        or "operation-v1"
    )
    if (
        operation.status != str(ledger["status"])
        or operation.cancel_requested
        != bool(ledger["cancel_requested"])
        or operation.completed_at
        != (
            str(ledger["completed_at"])
            if ledger["completed_at"] is not None
            else None
        )
        or workflow_version != str(ledger["workflow_version"])
    ):
        issues.append("mirror_lifecycle_mismatch")
    if (
        ledger_store.parameters_fingerprint(
            operation.parameters
        )
        != str(ledger["parameters_fingerprint"])
    ):
        issues.append("mirror_parameters_mismatch")
    return tuple(issues)


def _parse_legacy_operation(
    payload: object,
) -> _LegacySemanticOperation | None:
    if not isinstance(payload, dict):
        return None
    parameters = payload.get("parameters")
    result_summary = payload.get("result_summary")
    cancel_requested = payload.get("cancel_requested", False)
    completed_at = payload.get("completed_at")
    values = {
        "project_id": payload.get("project_id"),
        "operation_id": payload.get("operation_id"),
        "kind": payload.get("kind"),
        "status": payload.get("status"),
        "created_at": payload.get("created_at"),
    }
    if (
        any(
            not isinstance(value, str) or not value.strip()
            for value in values.values()
        )
        or not isinstance(cancel_requested, bool)
        or not isinstance(parameters, dict)
        or not all(
            isinstance(key, str) for key in parameters
        )
        or not isinstance(result_summary, dict)
        or not all(
            isinstance(key, str) for key in result_summary
        )
        or (
            completed_at is not None
            and not isinstance(completed_at, str)
        )
    ):
        return None
    return _LegacySemanticOperation(
        project_id=str(values["project_id"]).strip(),
        operation_id=str(values["operation_id"]).strip(),
        kind=str(values["kind"]).strip(),
        status=str(values["status"]).strip(),
        cancel_requested=cancel_requested,
        parameters=dict(parameters),
        result_summary=dict(result_summary),
        created_at=str(values["created_at"]),
        completed_at=completed_at,
    )


def _detail_core_from_legacy_operation(
    operation: _LegacySemanticOperation,
) -> OperationDetailCoreV1:
    parameters = operation.parameters
    return OperationDetailCoreV1(
        project_id=operation.project_id,
        operation_id=operation.operation_id,
        kind="semantic_tts_grouping",
        workflow_version=SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
        parameters=SemanticTtsGroupingDetailParametersV1(
            profile_id=parameters["profile_id"],
            profile_configuration_fingerprint=parameters[
                "profile_configuration_fingerprint"
            ],
            target_chars=parameters["target_chars"],
            max_chars=parameters["max_chars"],
        ),
    )


def _rejected(
    project_id: str,
    operation_id: str,
    *issues: str,
) -> OperationDetailMigrationResult:
    return OperationDetailMigrationResult(
        project_id=project_id,
        operation_id=operation_id,
        status="rejected",
        issue_codes=tuple(issues),
    )


def _normalized_cursor(
    project_id: str | None,
    operation_id: str | None,
) -> tuple[str, str] | None:
    normalized_project = str(project_id or "").strip()
    normalized_operation = str(operation_id or "").strip()
    if bool(normalized_project) != bool(normalized_operation):
        raise ValueError(
            "detail migration cursor requires project and operation IDs"
        )
    if not normalized_project:
        return None
    if len(normalized_project) > 128 or len(normalized_operation) > 128:
        raise ValueError("detail migration cursor is too long")
    return normalized_project, normalized_operation


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


__all__ = [
    "DetailMigrationStatus",
    "OperationDetailMigrationReport",
    "OperationDetailMigrationResult",
    "migrate_operation_details",
]
