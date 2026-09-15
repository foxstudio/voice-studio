from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services import video_localization_operation_attempt_store
from app.services import video_localization_operation_store


ReconciliationCategory = Literal[
    "matched_active",
    "matched_terminal",
    "attempt_running_after_terminal",
    "attempt_terminal_while_authority_active",
    "terminal_status_mismatch",
    "attempt_incomplete",
    "project_missing",
    "operation_missing",
    "authority_status_invalid",
]

_ACTIVE_STATUSES = {"queued", "running"}
_TERMINAL_STATUSES = {"success", "failed", "cancelled"}
_MATCHED_CATEGORIES = {"matched_active", "matched_terminal"}


@dataclass(frozen=True)
class AttemptReconciliation:
    attempt_id: str
    project_id: str
    operation_id: str
    attempt_number: int
    attempt_status: str
    authority_status: str | None
    category: ReconciliationCategory

    @property
    def matched(self) -> bool:
        return self.category in _MATCHED_CATEGORIES


@dataclass(frozen=True)
class AttemptReconciliationReport:
    total_attempt_count: int
    operation_count: int
    checked_operation_count: int
    truncated: bool
    category_counts: dict[str, int]
    issues: tuple[AttemptReconciliation, ...]

    @property
    def healthy(self) -> bool:
        return not self.truncated and not self.issues


def reconcile_latest_attempts(
    *,
    limit: int = 1_000,
) -> AttemptReconciliationReport:
    """Compare latest execution attempts with Project JSON without mutation."""
    inventory = (
        video_localization_operation_attempt_store.latest_attempt_inventory(
            limit=limit
        )
    )
    reconciliations = tuple(
        _reconcile_attempt(attempt)
        for attempt in inventory.attempts
    )
    category_counts: dict[str, int] = {}
    for reconciliation in reconciliations:
        category_counts[reconciliation.category] = (
            category_counts.get(reconciliation.category, 0) + 1
        )
    return AttemptReconciliationReport(
        total_attempt_count=inventory.total_attempt_count,
        operation_count=inventory.operation_count,
        checked_operation_count=len(reconciliations),
        truncated=inventory.truncated,
        category_counts=category_counts,
        issues=tuple(
            item for item in reconciliations if not item.matched
        ),
    )


def _reconcile_attempt(
    attempt: video_localization_operation_attempt_store.OperationAttempt,
) -> AttemptReconciliation:
    project_exists, operation = (
        video_localization_operation_store
        .read_project_mirror_operation(
            attempt.project_id,
            attempt.operation_id,
        )
    )
    authority_status = (
        str(operation.get("status") or "")
        if operation is not None
        else None
    )
    if not project_exists:
        category: ReconciliationCategory = "project_missing"
    elif operation is None:
        category = "operation_missing"
    elif authority_status not in _ACTIVE_STATUSES.union(
        _TERMINAL_STATUSES
    ):
        category = "authority_status_invalid"
    elif attempt.status in {"incomplete", "interrupted"}:
        category = "attempt_incomplete"
    elif attempt.status == "running":
        category = (
            "matched_active"
            if authority_status in _ACTIVE_STATUSES
            else "attempt_running_after_terminal"
        )
    elif attempt.status in _TERMINAL_STATUSES:
        if authority_status in _ACTIVE_STATUSES:
            category = "attempt_terminal_while_authority_active"
        elif attempt.status == authority_status:
            category = "matched_terminal"
        else:
            category = "terminal_status_mismatch"
    else:
        category = "attempt_incomplete"
    return AttemptReconciliation(
        attempt_id=attempt.attempt_id,
        project_id=attempt.project_id,
        operation_id=attempt.operation_id,
        attempt_number=attempt.attempt_number,
        attempt_status=attempt.status,
        authority_status=authority_status,
        category=category,
    )


__all__ = [
    "AttemptReconciliation",
    "AttemptReconciliationReport",
    "ReconciliationCategory",
    "reconcile_latest_attempts",
]
