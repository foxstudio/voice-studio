from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection
import uuid


@dataclass(frozen=True)
class BatchSegmentAttempt:
    attempt_id: str
    attempt_number: int
    provider_request_id: str | None


def prepare_from_connection(
    connection: Connection,
    *,
    batch_task_id: str,
    segment_id: str,
    engine_id: str,
    request_fingerprint: str,
    provider_request_id: str | None,
    prepared_at: str,
) -> BatchSegmentAttempt:
    row = connection.execute(
        """
        SELECT COALESCE(MAX(attempt_number), 0) AS attempt_number
        FROM batch_segment_attempts
        WHERE batch_task_id = ? AND segment_id = ?
        """,
        (batch_task_id, segment_id),
    ).fetchone()
    attempt_number = int(row["attempt_number"] or 0) + 1
    attempt_id = uuid.uuid4().hex
    connection.execute(
        """
        INSERT INTO batch_segment_attempts (
            attempt_id, batch_task_id, segment_id, attempt_number,
            engine_id, request_fingerprint, provider_request_id,
            status, prepared_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', ?)
        """,
        (
            attempt_id,
            batch_task_id,
            segment_id,
            attempt_number,
            engine_id,
            request_fingerprint,
            provider_request_id,
            prepared_at,
        ),
    )
    return BatchSegmentAttempt(
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        provider_request_id=provider_request_id,
    )


def finish_from_connection(
    connection: Connection,
    *,
    attempt_id: str,
    status: str,
    completed_at: str,
    provider_log_id: str | None = None,
    error_message: str | None = None,
) -> None:
    if status not in {"success", "failed", "uncertain"}:
        raise ValueError("invalid batch segment attempt status")
    cursor = connection.execute(
        """
        UPDATE batch_segment_attempts
        SET status = ?, completed_at = ?, provider_log_id = ?,
            error_message = ?
        WHERE attempt_id = ? AND status = 'prepared'
        """,
        (
            status,
            completed_at,
            provider_log_id,
            error_message,
            attempt_id,
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("batch segment attempt is not prepared")


__all__ = [
    "BatchSegmentAttempt",
    "finish_from_connection",
    "prepare_from_connection",
]
