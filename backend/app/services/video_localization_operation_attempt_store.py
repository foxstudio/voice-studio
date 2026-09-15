from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.services import database
from app.services.video_localization_execution_fence import ExecutionFence


AttemptStatus = Literal[
    "running",
    "success",
    "failed",
    "cancelled",
    "incomplete",
    "interrupted",
]
TerminalAttemptStatus = Literal[
    "success",
    "failed",
    "cancelled",
    "incomplete",
    "interrupted",
]
ClaimOutcome = Literal["acquired", "active_lease"]


@dataclass(frozen=True)
class OperationAttempt:
    attempt_id: str
    project_id: str
    operation_id: str
    attempt_number: int
    runner_id: str
    status: AttemptStatus
    started_at: str
    heartbeat_at: str
    completed_at: str | None = None
    error_code: str | None = None
    fencing_token: int = 0
    heartbeat_at_ms: int | None = None
    lease_expires_at_ms: int | None = None

    @property
    def is_durable_claim(self) -> bool:
        return (
            self.fencing_token > 0
            and self.heartbeat_at_ms is not None
            and self.lease_expires_at_ms is not None
        )

    @property
    def execution_fence(self) -> ExecutionFence | None:
        if not self.is_durable_claim:
            return None
        return ExecutionFence(
            attempt_id=self.attempt_id,
            project_id=self.project_id,
            operation_id=self.operation_id,
            runner_id=self.runner_id,
            fencing_token=self.fencing_token,
        )


@dataclass(frozen=True)
class ClaimDecision:
    outcome: ClaimOutcome
    attempt: OperationAttempt

    @property
    def acquired(self) -> bool:
        return self.outcome == "acquired"


@dataclass(frozen=True)
class LatestAttemptInventory:
    total_attempt_count: int
    operation_count: int
    attempts: tuple[OperationAttempt, ...]
    truncated: bool


def begin_attempt(
    project_id: str,
    operation_id: str,
    *,
    runner_id: str,
    started_at: str,
) -> OperationAttempt:
    """Append a shadow execution attempt without changing operation authority."""
    attempt_id = uuid.uuid4().hex
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT COALESCE(MAX(attempt_number), 0) AS attempt_number
            FROM video_localization_operation_attempts
            WHERE project_id = ?
              AND operation_id = ?
            """,
            (project_id, operation_id),
        ).fetchone()
        attempt_number = int(row["attempt_number"]) + 1
        connection.execute(
            """
            INSERT INTO video_localization_operation_attempts (
                attempt_id,
                project_id,
                operation_id,
                attempt_number,
                runner_id,
                status,
                started_at,
                heartbeat_at
            ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
            """,
            (
                attempt_id,
                project_id,
                operation_id,
                attempt_number,
                runner_id,
                started_at,
                started_at,
            ),
        )
    return OperationAttempt(
        attempt_id=attempt_id,
        project_id=project_id,
        operation_id=operation_id,
        attempt_number=attempt_number,
        runner_id=runner_id,
        status="running",
        started_at=started_at,
        heartbeat_at=started_at,
    )


def claim_attempt(
    project_id: str,
    operation_id: str,
    *,
    runner_id: str,
    observed_at: datetime,
    lease_duration: timedelta,
) -> ClaimDecision:
    """Acquire one project-scoped durable execution lease.

    A DB failure is intentionally propagated because the product worker must
    never execute when claim ownership is uncertain.
    """
    observed_at_ms = _epoch_milliseconds(observed_at)
    lease_duration_ms = _duration_milliseconds(lease_duration)
    lease_expires_at_ms = observed_at_ms + lease_duration_ms
    observed_at_text = _utc_iso(observed_at)
    attempt_id = uuid.uuid4().hex
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        active_lease = connection.execute(
            """
            SELECT
                attempt_id,
                project_id,
                operation_id,
                attempt_number,
                fencing_token,
                runner_id,
                status,
                started_at,
                heartbeat_at,
                heartbeat_at_ms,
                lease_expires_at_ms,
                completed_at,
                error_code
            FROM video_localization_operation_attempts
            WHERE project_id = ?
              AND operation_id = ?
              AND status = 'running'
              AND lease_expires_at_ms > ?
            ORDER BY fencing_token DESC
            LIMIT 1
            """,
            (project_id, operation_id, observed_at_ms),
        ).fetchone()
        if active_lease is not None:
            return ClaimDecision(
                outcome="active_lease",
                attempt=_attempt_from_row(active_lease),
            )
        counters = connection.execute(
            """
            SELECT
                COALESCE(MAX(attempt_number), 0) AS attempt_number,
                COALESCE(MAX(fencing_token), 0) AS fencing_token
            FROM video_localization_operation_attempts
            WHERE project_id = ?
              AND operation_id = ?
            """,
            (project_id, operation_id),
        ).fetchone()
        attempt_number = int(counters["attempt_number"]) + 1
        fencing_token = int(counters["fencing_token"]) + 1
        connection.execute(
            """
            INSERT INTO video_localization_operation_attempts (
                attempt_id,
                project_id,
                operation_id,
                attempt_number,
                fencing_token,
                runner_id,
                status,
                started_at,
                heartbeat_at,
                heartbeat_at_ms,
                lease_expires_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)
            """,
            (
                attempt_id,
                project_id,
                operation_id,
                attempt_number,
                fencing_token,
                runner_id,
                observed_at_text,
                observed_at_text,
                observed_at_ms,
                lease_expires_at_ms,
            ),
        )
    return ClaimDecision(
        outcome="acquired",
        attempt=OperationAttempt(
            attempt_id=attempt_id,
            project_id=project_id,
            operation_id=operation_id,
            attempt_number=attempt_number,
            fencing_token=fencing_token,
            runner_id=runner_id,
            status="running",
            started_at=observed_at_text,
            heartbeat_at=observed_at_text,
            heartbeat_at_ms=observed_at_ms,
            lease_expires_at_ms=lease_expires_at_ms,
        ),
    )


def heartbeat_attempt(
    attempt_id: str,
    *,
    runner_id: str,
    heartbeat_at: str,
) -> bool:
    with database.conn() as connection:
        cursor = connection.execute(
            """
            UPDATE video_localization_operation_attempts
            SET heartbeat_at = ?
            WHERE attempt_id = ?
              AND runner_id = ?
              AND status = 'running'
            """,
            (heartbeat_at, attempt_id, runner_id),
        )
    return cursor.rowcount == 1


def heartbeat_claim(
    attempt_id: str,
    project_id: str,
    operation_id: str,
    *,
    runner_id: str,
    fencing_token: int,
    observed_at: datetime,
    lease_duration: timedelta,
) -> bool:
    observed_at_ms = _epoch_milliseconds(observed_at)
    lease_duration_ms = _duration_milliseconds(lease_duration)
    observed_at_text = _utc_iso(observed_at)
    with database.conn() as connection:
        cursor = connection.execute(
            """
            UPDATE video_localization_operation_attempts
            SET
                heartbeat_at = ?,
                heartbeat_at_ms = ?,
                lease_expires_at_ms = ?
            WHERE attempt_id = ?
              AND project_id = ?
              AND operation_id = ?
              AND runner_id = ?
              AND fencing_token = ?
              AND status = 'running'
              AND heartbeat_at_ms <= ?
              AND lease_expires_at_ms > ?
            """,
            (
                observed_at_text,
                observed_at_ms,
                observed_at_ms + lease_duration_ms,
                attempt_id,
                project_id,
                operation_id,
                runner_id,
                fencing_token,
                observed_at_ms,
                observed_at_ms,
            ),
        )
    return cursor.rowcount == 1


def owns_active_claim(
    attempt_id: str,
    project_id: str,
    operation_id: str,
    *,
    runner_id: str,
    fencing_token: int,
    observed_at: datetime,
) -> bool:
    observed_at_ms = _epoch_milliseconds(observed_at)
    with database.conn() as connection:
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
                attempt_id,
                project_id,
                operation_id,
                runner_id,
                fencing_token,
                observed_at_ms,
            ),
        ).fetchone()
    return row is not None


def active_claim(
    project_id: str,
    operation_id: str,
    *,
    observed_at: datetime,
) -> OperationAttempt | None:
    observed_at_ms = _epoch_milliseconds(observed_at)
    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT
                attempt_id,
                project_id,
                operation_id,
                attempt_number,
                fencing_token,
                runner_id,
                status,
                started_at,
                heartbeat_at,
                heartbeat_at_ms,
                lease_expires_at_ms,
                completed_at,
                error_code
            FROM video_localization_operation_attempts
            WHERE project_id = ?
              AND operation_id = ?
              AND status = 'running'
              AND lease_expires_at_ms > ?
            ORDER BY fencing_token DESC
            LIMIT 1
            """,
            (project_id, operation_id, observed_at_ms),
        ).fetchone()
    return _attempt_from_row(row) if row is not None else None


def finish_attempt(
    attempt_id: str,
    *,
    runner_id: str,
    status: TerminalAttemptStatus,
    completed_at: str,
    error_code: str | None = None,
) -> bool:
    with database.conn() as connection:
        cursor = connection.execute(
            """
            UPDATE video_localization_operation_attempts
            SET
                status = ?,
                heartbeat_at = ?,
                completed_at = ?,
                error_code = ?
            WHERE attempt_id = ?
              AND runner_id = ?
              AND status = 'running'
            """,
            (
                status,
                completed_at,
                completed_at,
                error_code,
                attempt_id,
                runner_id,
            ),
        )
    return cursor.rowcount == 1


def finish_claim(
    attempt_id: str,
    project_id: str,
    operation_id: str,
    *,
    runner_id: str,
    fencing_token: int,
    status: TerminalAttemptStatus,
    observed_at: datetime,
    error_code: str | None = None,
) -> bool:
    observed_at_ms = _epoch_milliseconds(observed_at)
    observed_at_text = _utc_iso(observed_at)
    with database.conn() as connection:
        cursor = connection.execute(
            """
            UPDATE video_localization_operation_attempts
            SET
                status = ?,
                heartbeat_at = ?,
                heartbeat_at_ms = ?,
                completed_at = ?,
                error_code = ?
            WHERE attempt_id = ?
              AND project_id = ?
              AND operation_id = ?
              AND runner_id = ?
              AND fencing_token = ?
              AND status = 'running'
              AND lease_expires_at_ms > ?
            """,
            (
                status,
                observed_at_text,
                observed_at_ms,
                observed_at_text,
                error_code,
                attempt_id,
                project_id,
                operation_id,
                runner_id,
                fencing_token,
                observed_at_ms,
            ),
        )
    return cursor.rowcount == 1


def list_attempts(
    project_id: str,
    operation_id: str,
) -> list[OperationAttempt]:
    with database.conn() as connection:
        rows = connection.execute(
            """
            SELECT
                attempt_id,
                project_id,
                operation_id,
                attempt_number,
                fencing_token,
                runner_id,
                status,
                started_at,
                heartbeat_at,
                heartbeat_at_ms,
                lease_expires_at_ms,
                completed_at,
                error_code
            FROM video_localization_operation_attempts
            WHERE project_id = ?
              AND operation_id = ?
            ORDER BY attempt_number
            """,
            (project_id, operation_id),
        ).fetchall()
    return [_attempt_from_row(row) for row in rows]


def latest_attempt_inventory(
    *,
    limit: int = 1_000,
) -> LatestAttemptInventory:
    if limit < 1 or limit > 10_000:
        raise ValueError("limit must be between 1 and 10000")
    with database.conn() as connection:
        counts = connection.execute(
            """
            SELECT
                COUNT(*) AS total_attempt_count,
                (
                    SELECT COUNT(*)
                    FROM (
                        SELECT project_id, operation_id
                        FROM video_localization_operation_attempts
                        GROUP BY project_id, operation_id
                    )
                ) AS operation_count
            FROM video_localization_operation_attempts
            """
        ).fetchone()
        rows = connection.execute(
            """
            SELECT
                attempt.attempt_id,
                attempt.project_id,
                attempt.operation_id,
                attempt.attempt_number,
                attempt.fencing_token,
                attempt.runner_id,
                attempt.status,
                attempt.started_at,
                attempt.heartbeat_at,
                attempt.heartbeat_at_ms,
                attempt.lease_expires_at_ms,
                attempt.completed_at,
                attempt.error_code
            FROM video_localization_operation_attempts AS attempt
            JOIN (
                SELECT
                    project_id,
                    operation_id,
                    MAX(attempt_number) AS attempt_number
                FROM video_localization_operation_attempts
                GROUP BY project_id, operation_id
            ) AS latest
              ON latest.project_id = attempt.project_id
             AND latest.operation_id = attempt.operation_id
             AND latest.attempt_number = attempt.attempt_number
            ORDER BY
                attempt.heartbeat_at DESC,
                attempt.project_id,
                attempt.operation_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    operation_count = int(counts["operation_count"])
    return LatestAttemptInventory(
        total_attempt_count=int(counts["total_attempt_count"]),
        operation_count=operation_count,
        attempts=tuple(_attempt_from_row(row) for row in rows),
        truncated=operation_count > len(rows),
    )


def _attempt_from_row(row) -> OperationAttempt:
    return OperationAttempt(
        attempt_id=str(row["attempt_id"]),
        project_id=str(row["project_id"]),
        operation_id=str(row["operation_id"]),
        attempt_number=int(row["attempt_number"]),
        fencing_token=int(row["fencing_token"]),
        runner_id=str(row["runner_id"]),
        status=row["status"],
        started_at=str(row["started_at"]),
        heartbeat_at=str(row["heartbeat_at"]),
        heartbeat_at_ms=(
            int(row["heartbeat_at_ms"])
            if row["heartbeat_at_ms"] is not None
            else None
        ),
        lease_expires_at_ms=(
            int(row["lease_expires_at_ms"])
            if row["lease_expires_at_ms"] is not None
            else None
        ),
        completed_at=(
            str(row["completed_at"])
            if row["completed_at"] is not None
            else None
        ),
        error_code=(
            str(row["error_code"])
            if row["error_code"] is not None
            else None
        ),
    )


def _epoch_milliseconds(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return int(value.timestamp() * 1_000)


def _duration_milliseconds(value: timedelta) -> int:
    milliseconds = int(value.total_seconds() * 1_000)
    if milliseconds < 1:
        raise ValueError("lease_duration must be at least 1 millisecond")
    return milliseconds


def _utc_iso(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


__all__ = [
    "AttemptStatus",
    "ClaimDecision",
    "ClaimOutcome",
    "ExecutionFence",
    "LatestAttemptInventory",
    "OperationAttempt",
    "TerminalAttemptStatus",
    "begin_attempt",
    "active_claim",
    "claim_attempt",
    "finish_claim",
    "finish_attempt",
    "heartbeat_claim",
    "heartbeat_attempt",
    "latest_attempt_inventory",
    "list_attempts",
    "owns_active_claim",
]
