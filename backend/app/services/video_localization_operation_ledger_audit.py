from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services import database
from app.services import video_localization_operation_ledger_store
from app.services import video_localization_operation_store


LedgerReconciliationCategory = Literal[
    "matched",
    "project_missing",
    "mirror_operation_missing",
    "identity_mismatch",
    "state_mismatch",
    "parameters_mismatch",
    "ledger_operation_missing",
    "active_kind_conflict",
]
_MATCHED_CATEGORY = "matched"
_OPERATIONS_PATH = "$.parameters.video_localization.operations"


@dataclass(frozen=True)
class LedgerReconciliation:
    project_id: str
    operation_id: str
    ledger_status: str | None
    mirror_status: str | None
    category: LedgerReconciliationCategory

    @property
    def matched(self) -> bool:
        return self.category == _MATCHED_CATEGORY


@dataclass(frozen=True)
class LedgerReconciliationReport:
    total_ledger_count: int
    total_mirror_count: int
    checked_ledger_count: int
    checked_missing_ledger_count: int
    pending_outbox_count: int
    truncated: bool
    category_counts: dict[str, int]
    issues: tuple[LedgerReconciliation, ...]

    @property
    def healthy(self) -> bool:
        return (
            not self.truncated
            and self.pending_outbox_count == 0
            and not self.issues
        )


def reconcile_operation_ledger(
    *,
    limit: int = 1_000,
) -> LedgerReconciliationReport:
    """Compare payload-free ledger state with the Project compatibility mirror."""
    if limit < 1:
        raise ValueError("ledger audit limit must be positive")
    inventory = (
        video_localization_operation_ledger_store
        .ledger_inventory(limit=limit)
    )
    reconciliations = [
        _reconcile_entry(entry)
        for entry in inventory.entries
    ]
    reconciliations.extend(
        _active_kind_conflicts(inventory.entries)
    )
    (
        total_mirror_count,
        missing_ledger,
        missing_truncated,
    ) = _missing_ledger_mirrors(limit=limit)
    reconciliations.extend(missing_ledger)
    category_counts: dict[str, int] = {}
    for reconciliation in reconciliations:
        category_counts[reconciliation.category] = (
            category_counts.get(reconciliation.category, 0) + 1
        )
    return LedgerReconciliationReport(
        total_ledger_count=inventory.total_count,
        total_mirror_count=total_mirror_count,
        checked_ledger_count=len(inventory.entries),
        checked_missing_ledger_count=len(missing_ledger),
        pending_outbox_count=(
            video_localization_operation_ledger_store
            .pending_outbox_count()
        ),
        truncated=inventory.truncated or missing_truncated,
        category_counts=category_counts,
        issues=tuple(
            item
            for item in reconciliations
            if not item.matched
        ),
    )


def _reconcile_entry(
    entry: (
        video_localization_operation_ledger_store.OperationLedgerEntry
    ),
) -> LedgerReconciliation:
    project_exists, operation = (
        video_localization_operation_store
        .read_project_mirror_operation(
            entry.project_id,
            entry.operation_id,
        )
    )
    mirror_status = (
        str(operation.get("status") or "")
        if operation is not None
        else None
    )
    if not project_exists:
        category: LedgerReconciliationCategory = "project_missing"
    elif operation is None:
        category = "mirror_operation_missing"
    elif (
        str(operation.get("project_id") or "") != entry.project_id
        or str(operation.get("kind") or "") != entry.kind
        or str(operation.get("created_at") or "") != entry.created_at
    ):
        category = "identity_mismatch"
    elif (
        mirror_status != entry.status
        or bool(operation.get("cancel_requested"))
        != entry.cancel_requested
        or (
            str(operation.get("completed_at"))
            if operation.get("completed_at") is not None
            else None
        )
        != entry.completed_at
        or _mirror_workflow_version(operation)
        != entry.workflow_version
    ):
        category = "state_mismatch"
    elif not isinstance(operation.get("parameters"), dict) or (
        video_localization_operation_ledger_store
        .parameters_fingerprint(operation["parameters"])
        != entry.parameters_fingerprint
    ):
        category = "parameters_mismatch"
    else:
        category = "matched"
    return LedgerReconciliation(
        project_id=entry.project_id,
        operation_id=entry.operation_id,
        ledger_status=entry.status,
        mirror_status=mirror_status,
        category=category,
    )


def _mirror_workflow_version(operation: dict) -> str | None:
    try:
        return (
            video_localization_operation_ledger_store
            .workflow_version_from_operation(operation)
        )
    except ValueError:
        return None


def _missing_ledger_mirrors(
    *,
    limit: int,
) -> tuple[int, list[LedgerReconciliation], bool]:
    with database.conn() as connection:
        total_mirror_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM projects
                JOIN json_each(projects.data, ?) AS operation
                """,
                (_OPERATIONS_PATH,),
            ).fetchone()[0]
        )
        missing_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM projects
                JOIN json_each(projects.data, ?) AS operation
            LEFT JOIN video_localization_operations AS ledger
              ON ledger.project_id = projects.project_id
             AND ledger.operation_id = json_extract(
                      operation.value,
                      '$.operation_id'
                  )
                WHERE ledger.operation_id IS NULL
                """,
                (_OPERATIONS_PATH,),
            ).fetchone()[0]
        )
        rows = connection.execute(
            """
            SELECT
                projects.project_id,
                json_extract(
                    operation.value,
                    '$.operation_id'
                ) AS operation_id,
                json_extract(
                    operation.value,
                    '$.status'
                ) AS status
            FROM projects
            JOIN json_each(projects.data, ?) AS operation
            LEFT JOIN video_localization_operations AS ledger
              ON ledger.project_id = projects.project_id
             AND ledger.operation_id = json_extract(
                  operation.value,
                  '$.operation_id'
              )
            WHERE ledger.operation_id IS NULL
            ORDER BY projects.project_id, operation_id
            LIMIT ?
            """,
            (_OPERATIONS_PATH, limit),
        ).fetchall()
    return (
        total_mirror_count,
        [
            LedgerReconciliation(
                project_id=str(row["project_id"]),
                operation_id=str(row["operation_id"]),
                ledger_status=None,
                mirror_status=str(row["status"] or ""),
                category="ledger_operation_missing",
            )
            for row in rows
        ],
        missing_count > len(rows),
    )


def _active_kind_conflicts(
    entries: tuple[
        video_localization_operation_ledger_store.OperationLedgerEntry,
        ...,
    ],
) -> list[LedgerReconciliation]:
    active_by_kind: dict[
        tuple[str, str],
        list[
            video_localization_operation_ledger_store
            .OperationLedgerEntry
        ],
    ] = {}
    for entry in entries:
        if entry.status not in {"queued", "running"}:
            continue
        active_by_kind.setdefault(
            (entry.project_id, entry.kind),
            [],
        ).append(entry)
    return [
        LedgerReconciliation(
            project_id=entry.project_id,
            operation_id=entry.operation_id,
            ledger_status=entry.status,
            mirror_status=entry.status,
            category="active_kind_conflict",
        )
        for group in active_by_kind.values()
        if len(group) > 1
        for entry in group
    ]


__all__ = [
    "LedgerReconciliation",
    "LedgerReconciliationCategory",
    "LedgerReconciliationReport",
    "reconcile_operation_ledger",
]
