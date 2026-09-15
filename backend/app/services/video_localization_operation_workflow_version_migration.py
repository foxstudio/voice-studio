from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any, Literal

from app.services import database
from app.services import (
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (
    video_localization_operation_store as operation_store,
)


LEGACY_NULL_WORKFLOW_VERSION = "None"
DEFAULT_WORKFLOW_VERSION = "operation-v1"
WorkflowVersionMigrationStatus = Literal[
    "planned",
    "migrated",
    "rejected",
]
_KNOWN_KINDS = frozenset(
    {
        "source_audio",
        "stems",
        "english_asr",
        "speaker_diarization",
        "localization_draft",
        "reference_clips",
        "semantic_tts_grouping",
    }
)


@dataclass(frozen=True)
class WorkflowVersionMigrationResult:
    project_id: str
    operation_id: str
    status: WorkflowVersionMigrationStatus
    issue_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowVersionMigrationReport:
    apply: bool
    scanned_operation_count: int
    planned_operation_count: int
    migrated_operation_count: int
    rejected_operation_count: int
    truncated: bool
    next_project_id: str | None
    next_operation_id: str | None
    results: tuple[WorkflowVersionMigrationResult, ...]

    @property
    def healthy(self) -> bool:
        return not self.truncated and not self.rejected_operation_count


def migrate_legacy_null_workflow_versions(
    *,
    apply: bool = False,
    limit: int = 1_000,
    after_project_id: str | None = None,
    after_operation_id: str | None = None,
) -> WorkflowVersionMigrationReport:
    """Plan or apply one bounded keyset batch of legacy null sentinels."""

    if limit < 1 or limit > 10_000:
        raise ValueError(
            "workflow version migration limit must be between 1 and 10000"
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
        results = tuple(
            _migrate_candidate(
                connection,
                row,
                apply=apply,
            )
            for row in checked
        )
    next_cursor = (
        (
            results[-1].project_id,
            results[-1].operation_id,
        )
        if len(rows) > limit and results
        else (None, None)
    )
    return WorkflowVersionMigrationReport(
        apply=apply,
        scanned_operation_count=len(results),
        planned_operation_count=sum(
            result.status == "planned" for result in results
        ),
        migrated_operation_count=sum(
            result.status == "migrated" for result in results
        ),
        rejected_operation_count=sum(
            result.status == "rejected" for result in results
        ),
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
            LEGACY_NULL_WORKFLOW_VERSION,
            limit,
        )
    else:
        where = (
            "AND (project_id > ? OR "
            "(project_id = ? AND operation_id > ?))"
        )
        parameters = (
            LEGACY_NULL_WORKFLOW_VERSION,
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
) -> WorkflowVersionMigrationResult:
    project_id = str(ledger["project_id"])
    operation_id = str(ledger["operation_id"])
    project_exists, payload = (
        operation_store.read_project_mirror_operation_from_connection(
            connection,
            project_id,
            operation_id,
        )
    )
    issues: list[str] = []
    if not project_exists:
        issues.append("project_missing")
    elif payload is None:
        issues.append("mirror_operation_missing")
    elif not isinstance(payload, dict):
        issues.append("mirror_operation_invalid")
    else:
        issues.extend(_ledger_mirror_issues(ledger, payload))
    if connection.execute(
        """
        SELECT 1
        FROM video_localization_operation_detail_cores
        WHERE project_id = ?
          AND operation_id = ?
        LIMIT 1
        """,
        (project_id, operation_id),
    ).fetchone():
        issues.append("detail_core_conflict")
    step_versions = {
        str(row["workflow_version"])
        for row in connection.execute(
            """
            SELECT DISTINCT workflow_version
            FROM video_localization_operation_step_attempts
            WHERE project_id = ?
              AND operation_id = ?
            """,
            (project_id, operation_id),
        ).fetchall()
    }
    if step_versions - {LEGACY_NULL_WORKFLOW_VERSION}:
        issues.append("step_workflow_mismatch")
    if issues:
        return WorkflowVersionMigrationResult(
            project_id=project_id,
            operation_id=operation_id,
            status="rejected",
            issue_codes=tuple(dict.fromkeys(issues)),
        )
    if not apply:
        return WorkflowVersionMigrationResult(
            project_id=project_id,
            operation_id=operation_id,
            status="planned",
        )
    cursor = connection.execute(
        """
        UPDATE video_localization_operations
        SET workflow_version = ?
        WHERE project_id = ?
          AND operation_id = ?
          AND workflow_version = ?
        """,
        (
            DEFAULT_WORKFLOW_VERSION,
            project_id,
            operation_id,
            LEGACY_NULL_WORKFLOW_VERSION,
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(
            "workflow version migration lost its ledger candidate"
        )
    connection.execute(
        """
        UPDATE video_localization_operation_step_attempts
        SET workflow_version = ?
        WHERE project_id = ?
          AND operation_id = ?
          AND workflow_version = ?
        """,
        (
            DEFAULT_WORKFLOW_VERSION,
            project_id,
            operation_id,
            LEGACY_NULL_WORKFLOW_VERSION,
        ),
    )
    return WorkflowVersionMigrationResult(
        project_id=project_id,
        operation_id=operation_id,
        status="migrated",
    )


def _ledger_mirror_issues(
    ledger,
    operation: dict[str, Any],
) -> tuple[str, ...]:
    issues: list[str] = []
    required_text = {
        "project_id": operation.get("project_id"),
        "operation_id": operation.get("operation_id"),
        "kind": operation.get("kind"),
        "status": operation.get("status"),
        "created_at": operation.get("created_at"),
    }
    parameters = operation.get("parameters")
    result_summary = operation.get("result_summary")
    completed_at = operation.get("completed_at")
    if (
        any(
            not isinstance(value, str) or not value.strip()
            for value in required_text.values()
        )
        or not isinstance(parameters, dict)
        or not isinstance(result_summary, dict)
        or not isinstance(
            operation.get("cancel_requested", False),
            bool,
        )
        or (
            completed_at is not None
            and not isinstance(completed_at, str)
        )
    ):
        return ("mirror_operation_invalid",)
    if (
        str(required_text["project_id"]) != str(ledger["project_id"])
        or str(required_text["operation_id"])
        != str(ledger["operation_id"])
        or str(required_text["kind"]) != str(ledger["kind"])
        or str(required_text["created_at"]) != str(ledger["created_at"])
    ):
        issues.append("mirror_identity_mismatch")
    if str(required_text["kind"]) not in _KNOWN_KINDS:
        issues.append("unsupported_kind")
    try:
        mirror_workflow_version = (
            ledger_store.workflow_version_from_operation(operation)
        )
    except ValueError:
        issues.append("mirror_workflow_invalid")
    else:
        if mirror_workflow_version != DEFAULT_WORKFLOW_VERSION:
            issues.append("mirror_workflow_mismatch")
    if (
        str(required_text["status"]) != str(ledger["status"])
        or bool(operation.get("cancel_requested"))
        != bool(ledger["cancel_requested"])
        or completed_at
        != (
            str(ledger["completed_at"])
            if ledger["completed_at"] is not None
            else None
        )
    ):
        issues.append("mirror_lifecycle_mismatch")
    if (
        isinstance(parameters, dict)
        and ledger_store.parameters_fingerprint(parameters)
        != str(ledger["parameters_fingerprint"])
    ):
        issues.append("mirror_parameters_mismatch")
    return tuple(issues)


def _normalized_cursor(
    project_id: str | None,
    operation_id: str | None,
) -> tuple[str, str] | None:
    normalized_project = str(project_id or "").strip()
    normalized_operation = str(operation_id or "").strip()
    if bool(normalized_project) != bool(normalized_operation):
        raise ValueError(
            "workflow migration cursor requires project and operation IDs"
        )
    if not normalized_project:
        return None
    if len(normalized_project) > 128 or len(normalized_operation) > 128:
        raise ValueError("workflow migration cursor is too long")
    return normalized_project, normalized_operation


__all__ = [
    "DEFAULT_WORKFLOW_VERSION",
    "LEGACY_NULL_WORKFLOW_VERSION",
    "WorkflowVersionMigrationReport",
    "WorkflowVersionMigrationResult",
    "migrate_legacy_null_workflow_versions",
]
