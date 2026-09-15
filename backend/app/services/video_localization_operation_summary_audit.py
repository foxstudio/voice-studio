from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection
from typing import Literal, Sequence

from app.schemas.video_localization_operation_summary import (
    OperationSummaryCoreV1,
    SUMMARY_CORE_SCHEMA_VERSION,
    operation_summary_core_fingerprint,
    operation_summary_core_json,
)
from app.schemas.video_localization_operation_summary_migration import (
    ExpectedOperationSummary,
)
from app.schemas.public_payload import contains_internal_locator
from app.services import video_localization_operation_summary_store


SummaryReconciliationCategory = Literal[
    "project_missing",
    "legacy_project_invalid",
    "legacy_draft_invalid",
    "duplicate_operation_identity",
    "active_kind_conflict",
    "projection_state_missing",
    "projection_status_missing",
    "projection_repair_required",
    "projection_state_mismatch",
    "ledger_operation_missing",
    "summary_operation_missing",
    "legacy_operation_missing",
    "identity_mismatch",
    "state_mismatch",
    "ledger_revision_mismatch",
    "summary_schema_mismatch",
    "summary_core_invalid",
    "summary_core_identity_mismatch",
    "summary_core_fingerprint_mismatch",
    "summary_core_noncanonical",
    "summary_core_mismatch",
    "public_locator_exposed",
    "ordering_mismatch",
]

LegacyProjectError = Literal[
    "legacy_project_invalid",
    "legacy_draft_invalid",
]

_ACTIVE_STATUSES = {"queued", "running"}
_VALID_PROJECTION_STATUSES = {
    "missing",
    "shadow",
    "verified",
    "authoritative",
    "repair_required",
}


@dataclass(frozen=True)
class SummaryReconciliationIssue:
    project_id: str
    operation_id: str | None
    category: SummaryReconciliationCategory


@dataclass(frozen=True)
class ProjectSummaryReconciliation:
    project_id: str
    legacy_count: int
    ledger_count: int
    summary_count: int
    matched_count: int
    issues: tuple[SummaryReconciliationIssue, ...]


def reconcile_project_snapshot(
    connection: Connection,
    project_id: str,
    *,
    project_exists: bool,
    expected_operations: Sequence[ExpectedOperationSummary] | None,
    legacy_error: LegacyProjectError | None = None,
) -> ProjectSummaryReconciliation:
    """Compare legacy, ledger, and summary rows in one caller-owned snapshot."""

    ledger_rows = connection.execute(
        """
        SELECT
            operation_id,
            project_id,
            kind,
            status,
            cancel_requested,
            state_revision,
            created_at,
            completed_at
        FROM video_localization_operations
        WHERE project_id = ?
        ORDER BY created_at DESC, operation_id DESC
        """,
        (project_id,),
    ).fetchall()
    summary_rows = connection.execute(
        """
        SELECT
            project_id,
            operation_id,
            summary_schema_version,
            ledger_state_revision,
            core_revision,
            content_fingerprint,
            core_json
        FROM video_localization_operation_summaries
        WHERE project_id = ?
        ORDER BY operation_id
        """,
        (project_id,),
    ).fetchall()
    issues: list[SummaryReconciliationIssue] = []

    def add(
        category: SummaryReconciliationCategory,
        operation_id: str | None = None,
    ) -> None:
        issue = SummaryReconciliationIssue(
            project_id=project_id,
            operation_id=operation_id,
            category=category,
        )
        if issue not in issues:
            issues.append(issue)

    if not project_exists:
        orphan_ids = sorted(
            {
                str(row["operation_id"])
                for row in [*ledger_rows, *summary_rows]
            }
        )
        if orphan_ids:
            for operation_id in orphan_ids:
                add("project_missing", operation_id)
        else:
            add("project_missing")
        return ProjectSummaryReconciliation(
            project_id=project_id,
            legacy_count=0,
            ledger_count=len(ledger_rows),
            summary_count=len(summary_rows),
            matched_count=0,
            issues=tuple(issues),
        )

    if legacy_error is not None:
        add(legacy_error)
        return ProjectSummaryReconciliation(
            project_id=project_id,
            legacy_count=0,
            ledger_count=len(ledger_rows),
            summary_count=len(summary_rows),
            matched_count=0,
            issues=tuple(issues),
        )

    if expected_operations is None:
        for operation_id in sorted(
            {
                str(row["operation_id"])
                for row in [*ledger_rows, *summary_rows]
            }
        ):
            add("legacy_operation_missing", operation_id)
        return ProjectSummaryReconciliation(
            project_id=project_id,
            legacy_count=0,
            ledger_count=len(ledger_rows),
            summary_count=len(summary_rows),
            matched_count=0,
            issues=tuple(issues),
        )

    expected_by_id: dict[str, ExpectedOperationSummary] = {}
    duplicate_ids: set[str] = set()
    for operation in expected_operations:
        if operation.operation_id in expected_by_id:
            duplicate_ids.add(operation.operation_id)
        expected_by_id[operation.operation_id] = operation
    for operation_id in sorted(duplicate_ids):
        add("duplicate_operation_identity", operation_id)

    active_by_kind: dict[str, list[str]] = {}
    for operation in expected_operations:
        if operation.status in _ACTIVE_STATUSES:
            active_by_kind.setdefault(
                operation.kind,
                [],
            ).append(operation.operation_id)
    for operation_ids in active_by_kind.values():
        if len(operation_ids) > 1:
            for operation_id in sorted(operation_ids):
                add("active_kind_conflict", operation_id)

    ledger_by_id = {
        str(row["operation_id"]): row for row in ledger_rows
    }
    summary_by_id = {
        str(row["operation_id"]): row for row in summary_rows
    }
    expected_ids = set(expected_by_id)
    ledger_ids = set(ledger_by_id)
    summary_ids = set(summary_by_id)
    for operation_id in sorted(expected_ids - ledger_ids):
        add("ledger_operation_missing", operation_id)
    for operation_id in sorted(expected_ids - summary_ids):
        add("summary_operation_missing", operation_id)
    for operation_id in sorted(
        (ledger_ids | summary_ids) - expected_ids
    ):
        add("legacy_operation_missing", operation_id)

    expected_order = [
        operation.operation_id
        for operation in sorted(
            expected_operations,
            key=lambda item: (
                item.created_at,
                item.operation_id,
            ),
            reverse=True,
        )
    ]
    ledger_order = [
        str(row["operation_id"])
        for row in ledger_rows
        if str(row["operation_id"]) in expected_ids
    ]
    if (
        not duplicate_ids
        and set(expected_order) == set(ledger_order)
        and expected_order != ledger_order
    ):
        for operation_id, expected_id in zip(
            ledger_order,
            expected_order,
            strict=True,
        ):
            if operation_id != expected_id:
                add("ordering_mismatch", operation_id)

    state = connection.execute(
        """
        SELECT
            projection_revision,
            summary_schema_version,
            summary_status,
            summary_row_count,
            summary_fingerprint,
            history_revision,
            history_fingerprint
        FROM video_localization_operation_projection_state
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    try:
        integrity = (
            video_localization_operation_summary_store
            .inspect_project_projection(connection, project_id)
        )
    except (TypeError, ValueError):
        integrity = None
        add("projection_state_mismatch")
    if state is None:
        add("projection_state_missing")
    else:
        status = str(state["summary_status"])
        projection_revision = _safe_int(
            state["projection_revision"]
        )
        history_revision = _safe_int(state["history_revision"])
        if (
            projection_revision is None
            or projection_revision < 0
            or history_revision is None
            or history_revision < 0
        ):
            add("projection_state_mismatch")
        if status not in _VALID_PROJECTION_STATUSES:
            add("projection_state_mismatch")
        elif status == "missing":
            add("projection_status_missing")
        elif status == "repair_required":
            add("projection_repair_required")
        if (
            status != "missing"
            and integrity is not None
            and (
                str(state["summary_schema_version"] or "")
                != SUMMARY_CORE_SCHEMA_VERSION
                or _safe_int(state["summary_row_count"])
                != integrity.summary_row_count
                or str(state["summary_fingerprint"] or "")
                != integrity.summary_fingerprint
                or str(state["history_fingerprint"] or "")
                != integrity.history_fingerprint
                or integrity.ledger_row_count
                != integrity.summary_row_count
                or integrity.joined_row_count
                != integrity.ledger_row_count
            )
        ):
            add("projection_state_mismatch")

    matched_count = 0
    for operation_id in sorted(
        expected_ids & ledger_ids & summary_ids
    ):
        expected = expected_by_id[operation_id]
        ledger = ledger_by_id[operation_id]
        summary = summary_by_id[operation_id]
        if (
            expected.core.project_id != project_id
            or str(ledger["project_id"]) != project_id
            or str(ledger["kind"]) != expected.kind
            or str(ledger["created_at"]) != expected.created_at
        ):
            add("identity_mismatch", operation_id)
        if (
            str(ledger["status"]) != expected.status
            or bool(ledger["cancel_requested"])
            != expected.cancel_requested
            or _optional_text(ledger["completed_at"])
            != expected.completed_at
        ):
            add("state_mismatch", operation_id)
        if (
            str(summary["summary_schema_version"])
            != SUMMARY_CORE_SCHEMA_VERSION
        ):
            add("summary_schema_mismatch", operation_id)
        try:
            core = OperationSummaryCoreV1.model_validate_json(
                str(summary["core_json"])
            )
            canonical_json = operation_summary_core_json(core)
            fingerprint = operation_summary_core_fingerprint(core)
        except Exception:
            add("summary_core_invalid", operation_id)
            continue
        if (
            core.project_id != project_id
            or core.operation_id != operation_id
        ):
            add("summary_core_identity_mismatch", operation_id)
        if canonical_json != str(summary["core_json"]):
            add("summary_core_noncanonical", operation_id)
        if fingerprint != str(summary["content_fingerprint"]):
            add("summary_core_fingerprint_mismatch", operation_id)
        try:
            expected_fingerprint = (
                operation_summary_core_fingerprint(expected.core)
            )
        except (TypeError, ValueError):
            expected_fingerprint = None
        if expected_fingerprint != fingerprint:
            add("summary_core_mismatch", operation_id)
        if (
            _safe_int(summary["ledger_state_revision"]) is None
            or _safe_int(summary["core_revision"]) is None
            or _safe_int(summary["core_revision"]) < 1
        ):
            add("summary_core_invalid", operation_id)
        if (
            _safe_int(summary["ledger_state_revision"])
            != _safe_int(ledger["state_revision"])
        ):
            add("ledger_revision_mismatch", operation_id)
        payload = core.model_dump(mode="json")
        if contains_internal_locator(payload):
            add("public_locator_exposed", operation_id)
        if not any(
            issue.operation_id == operation_id
            for issue in issues
        ):
            matched_count += 1

    return ProjectSummaryReconciliation(
        project_id=project_id,
        legacy_count=len(expected_operations),
        ledger_count=len(ledger_rows),
        summary_count=len(summary_rows),
        matched_count=matched_count,
        issues=tuple(issues),
    )


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "ExpectedOperationSummary",
    "LegacyProjectError",
    "ProjectSummaryReconciliation",
    "SummaryReconciliationCategory",
    "SummaryReconciliationIssue",
    "reconcile_project_snapshot",
]
