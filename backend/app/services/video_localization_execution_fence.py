from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Iterator


@dataclass(frozen=True)
class ExecutionFence:
    """Opaque capability required for one operation worker to commit state."""

    attempt_id: str
    project_id: str
    operation_id: str
    runner_id: str
    fencing_token: int

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.attempt_id,
                self.project_id,
                self.operation_id,
                self.runner_id,
            )
        ):
            raise ValueError("execution fence identifiers must not be empty")
        if self.fencing_token < 1:
            raise ValueError("execution fencing_token must be positive")


class ExecutionFenceLost(RuntimeError):
    """The worker no longer owns a live lease and must discard its result."""


class ExecutionOperationCancelled(RuntimeError):
    """The authoritative ledger operation was cancelled before commit."""


_CURRENT_EXECUTION_FENCE: ContextVar[ExecutionFence | None] = ContextVar(
    "video_localization_execution_fence",
    default=None,
)


@contextmanager
def execution_fence_scope(
    execution_fence: ExecutionFence,
) -> Iterator[None]:
    """Propagate one explicit worker capability through nested Project writes."""
    token = _CURRENT_EXECUTION_FENCE.set(execution_fence)
    try:
        yield
    finally:
        _CURRENT_EXECUTION_FENCE.reset(token)


def resolve_execution_fence(
    explicit: ExecutionFence | None,
) -> ExecutionFence | None:
    contextual = _CURRENT_EXECUTION_FENCE.get()
    if (
        explicit is not None
        and contextual is not None
        and explicit != contextual
    ):
        raise ValueError(
            "explicit execution fence conflicts with worker scope"
        )
    return explicit or contextual


def require_active_execution_fence(
    connection: Connection,
    execution_fence: ExecutionFence,
    *,
    target_project_id: str,
    observed_at_ms: int,
) -> None:
    """Validate one worker capability inside the caller's transaction."""

    if execution_fence.project_id != target_project_id:
        raise ExecutionFenceLost(
            "operation execution fence belongs to another project"
        )
    row = connection.execute(
        """
        SELECT 1
        FROM video_localization_operation_attempts
        WHERE attempt_id = ?
          AND project_id = ?
          AND operation_id = ?
          AND runner_id = ?
          AND fencing_token = ?
          AND status = 'running'
          AND lease_expires_at_ms > ?
        """,
        (
            execution_fence.attempt_id,
            execution_fence.project_id,
            execution_fence.operation_id,
            execution_fence.runner_id,
            execution_fence.fencing_token,
            observed_at_ms,
        ),
    ).fetchone()
    if row is None:
        raise ExecutionFenceLost(
            "operation execution fence is missing, expired, or superseded"
        )
    operation = connection.execute(
        """
        SELECT status, cancel_requested
        FROM video_localization_operations
        WHERE project_id = ?
          AND operation_id = ?
        """,
        (
            target_project_id,
            execution_fence.operation_id,
        ),
    ).fetchone()
    if operation is None:
        raise ExecutionFenceLost(
            "authoritative operation ledger entry is missing"
        )
    if (
        bool(operation["cancel_requested"])
        or str(operation["status"] or "")
        not in {"queued", "running"}
    ):
        raise ExecutionOperationCancelled(
            "authoritative operation ledger entry is no longer active"
        )


__all__ = [
    "ExecutionFence",
    "ExecutionFenceLost",
    "ExecutionOperationCancelled",
    "execution_fence_scope",
    "require_active_execution_fence",
    "resolve_execution_fence",
]
