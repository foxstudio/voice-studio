from __future__ import annotations

import json
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Literal, cast

from app.schemas.video_localization_tts_handoff import (
    TTS_HANDOFF_EVENT_ADAPTER,
    TTS_HANDOFF_EVENT_SCHEMA_VERSION,
    TtsHandoffClaim,
    TtsHandoffEventV1,
)
from app.services import database


TtsHandoffEventStatus = Literal[
    "pending",
    "applied",
    "abandoned",
]
TtsHandoffClaimOutcome = Literal[
    "acquired",
    "active_lease",
    "not_pending",
]


class TtsHandoffClaimLost(RuntimeError):
    """The delivery runner no longer owns this outbox event."""


@dataclass(frozen=True)
class StoredTtsHandoffEvent:
    event_id: str
    payload: TtsHandoffEventV1
    status: TtsHandoffEventStatus
    attempt_count: int
    last_error: str | None


@dataclass(frozen=True)
class TtsHandoffClaimDecision:
    outcome: TtsHandoffClaimOutcome
    event: StoredTtsHandoffEvent
    claim: TtsHandoffClaim | None

    @property
    def acquired(self) -> bool:
        return self.outcome == "acquired"


def registration_event_id(
    *,
    source_kind: str,
    source_id: str,
) -> str:
    return f"task-registration:{source_kind}:{source_id}"


def result_placement_event_id(task_id: str) -> str:
    return f"result-placement:{task_id}"


def workflow_terminal_event_id(
    *,
    source_id: str,
    workflow_id: str,
) -> str:
    return f"workflow-terminal:{source_id}:{workflow_id}"


def enqueue(
    connection: Connection,
    *,
    event_id: str,
    payload: TtsHandoffEventV1,
    created_at: str,
) -> None:
    """Insert or supersede one aggregate-keyed handoff event."""

    payload_json = payload.model_dump_json()
    connection.execute(
        """
        INSERT INTO video_localization_tts_handoff_outbox (
            event_id,
            project_id,
            event_kind,
            payload_version,
            payload_json,
            status,
            created_at,
            attempt_count,
            last_attempt_at,
            last_error,
            applied_at,
            abandoned_at,
            claim_owner,
            fencing_token,
            claimed_at_ms,
            lease_expires_at_ms
        )
        VALUES (
            ?, ?, ?, ?, ?, 'pending', ?, 0, NULL, NULL, NULL, NULL,
            NULL, 0, NULL, NULL
        )
        ON CONFLICT(event_id) DO UPDATE SET
            project_id = excluded.project_id,
            event_kind = excluded.event_kind,
            payload_version = excluded.payload_version,
            payload_json = excluded.payload_json,
            status = CASE
                WHEN
                    video_localization_tts_handoff_outbox.payload_json
                        = excluded.payload_json
                THEN video_localization_tts_handoff_outbox.status
                ELSE 'pending'
            END,
            created_at = CASE
                WHEN
                    video_localization_tts_handoff_outbox.payload_json
                        = excluded.payload_json
                THEN video_localization_tts_handoff_outbox.created_at
                ELSE excluded.created_at
            END,
            attempt_count = CASE
                WHEN
                    video_localization_tts_handoff_outbox.payload_json
                        = excluded.payload_json
                THEN video_localization_tts_handoff_outbox.attempt_count
                ELSE 0
            END,
            last_attempt_at = CASE
                WHEN
                    video_localization_tts_handoff_outbox.payload_json
                        = excluded.payload_json
                THEN video_localization_tts_handoff_outbox.last_attempt_at
                ELSE NULL
            END,
            last_error = CASE
                WHEN
                    video_localization_tts_handoff_outbox.payload_json
                        = excluded.payload_json
                THEN video_localization_tts_handoff_outbox.last_error
                ELSE NULL
            END,
            applied_at = CASE
                WHEN
                    video_localization_tts_handoff_outbox.payload_json
                        = excluded.payload_json
                THEN video_localization_tts_handoff_outbox.applied_at
                ELSE NULL
            END,
            abandoned_at = CASE
                WHEN
                    video_localization_tts_handoff_outbox.payload_json
                        = excluded.payload_json
                THEN video_localization_tts_handoff_outbox.abandoned_at
                ELSE NULL
            END,
            claim_owner = CASE
                WHEN
                    video_localization_tts_handoff_outbox.payload_json
                        = excluded.payload_json
                THEN video_localization_tts_handoff_outbox.claim_owner
                ELSE NULL
            END,
            fencing_token = CASE
                WHEN
                    video_localization_tts_handoff_outbox.payload_json
                        = excluded.payload_json
                THEN video_localization_tts_handoff_outbox.fencing_token
                ELSE video_localization_tts_handoff_outbox.fencing_token + 1
            END,
            claimed_at_ms = CASE
                WHEN
                    video_localization_tts_handoff_outbox.payload_json
                        = excluded.payload_json
                THEN video_localization_tts_handoff_outbox.claimed_at_ms
                ELSE NULL
            END,
            lease_expires_at_ms = CASE
                WHEN
                    video_localization_tts_handoff_outbox.payload_json
                        = excluded.payload_json
                THEN video_localization_tts_handoff_outbox.lease_expires_at_ms
                ELSE NULL
            END
        """,
        (
            event_id,
            payload.project_id,
            payload.event_kind,
            TTS_HANDOFF_EVENT_SCHEMA_VERSION,
            payload_json,
            created_at,
        ),
    )


def claim_pending(
    event_id: str,
    *,
    runner_id: str,
    observed_at_ms: int,
    lease_duration_ms: int,
) -> TtsHandoffClaimDecision | None:
    normalized_runner_id = runner_id.strip()
    if not normalized_runner_id:
        raise ValueError("TTS handoff runner_id must not be empty")
    if observed_at_ms < 0:
        raise ValueError("TTS handoff observed_at_ms must not be negative")
    if lease_duration_ms < 1:
        raise ValueError("TTS handoff lease_duration_ms must be positive")
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT *
            FROM video_localization_tts_handoff_outbox
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        event = _event_from_row(row)
        if event.status != "pending":
            return TtsHandoffClaimDecision(
                outcome="not_pending",
                event=event,
                claim=None,
            )
        current_owner = str(row["claim_owner"] or "")
        current_expiry = int(row["lease_expires_at_ms"] or 0)
        current_token = int(row["fencing_token"] or 0)
        if current_owner and current_expiry > observed_at_ms:
            return TtsHandoffClaimDecision(
                outcome="active_lease",
                event=event,
                claim=TtsHandoffClaim(
                    event_id=event_id,
                    project_id=event.payload.project_id,
                    runner_id=current_owner,
                    fencing_token=current_token,
                    lease_expires_at_ms=current_expiry,
                ),
            )
        next_token = current_token + 1
        lease_expires_at_ms = observed_at_ms + lease_duration_ms
        cursor = connection.execute(
            """
            UPDATE video_localization_tts_handoff_outbox
            SET
                claim_owner = ?,
                fencing_token = ?,
                claimed_at_ms = ?,
                lease_expires_at_ms = ?
            WHERE event_id = ?
              AND status = 'pending'
              AND fencing_token = ?
              AND (
                    claim_owner IS NULL
                    OR lease_expires_at_ms IS NULL
                    OR lease_expires_at_ms <= ?
              )
            """,
            (
                normalized_runner_id,
                next_token,
                observed_at_ms,
                lease_expires_at_ms,
                event_id,
                current_token,
                observed_at_ms,
            ),
        )
        if cursor.rowcount != 1:
            raise TtsHandoffClaimLost(
                "TTS handoff event changed while acquiring its lease"
            )
    return TtsHandoffClaimDecision(
        outcome="acquired",
        event=event,
        claim=TtsHandoffClaim(
            event_id=event_id,
            project_id=event.payload.project_id,
            runner_id=normalized_runner_id,
            fencing_token=next_token,
            lease_expires_at_ms=lease_expires_at_ms,
        ),
    )


def heartbeat_claim(
    claim: TtsHandoffClaim,
    *,
    observed_at_ms: int,
    lease_duration_ms: int,
) -> bool:
    """Extend one live delivery lease without reviving an expired claim."""

    if observed_at_ms < 0:
        raise ValueError("TTS handoff observed_at_ms must not be negative")
    if lease_duration_ms < 1:
        raise ValueError("TTS handoff lease_duration_ms must be positive")
    next_expiry = observed_at_ms + lease_duration_ms
    with database.conn() as connection:
        cursor = connection.execute(
            """
            UPDATE video_localization_tts_handoff_outbox
            SET lease_expires_at_ms = MAX(
                lease_expires_at_ms,
                ?
            )
            WHERE event_id = ?
              AND project_id = ?
              AND status = 'pending'
              AND claim_owner = ?
              AND fencing_token = ?
              AND lease_expires_at_ms > ?
            """,
            (
                next_expiry,
                claim.event_id,
                claim.project_id,
                claim.runner_id,
                claim.fencing_token,
                observed_at_ms,
            ),
        )
    return cursor.rowcount == 1


def list_pending(*, limit: int) -> list[StoredTtsHandoffEvent]:
    bounded_limit = max(1, min(int(limit), 1_000))
    with database.conn() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM video_localization_tts_handoff_outbox
            WHERE status = 'pending'
            ORDER BY created_at ASC, event_id ASC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
    return [_event_from_row(row) for row in rows]


def get(event_id: str) -> StoredTtsHandoffEvent | None:
    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM video_localization_tts_handoff_outbox
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
    return _event_from_row(row) if row is not None else None


def mark_applied(
    claim: TtsHandoffClaim,
    *,
    attempted_at: str,
    observed_at_ms: int,
) -> bool:
    with database.conn() as connection:
        cursor = connection.execute(
            """
            UPDATE video_localization_tts_handoff_outbox
            SET
                status = 'applied',
                attempt_count = attempt_count + 1,
                last_attempt_at = ?,
                last_error = NULL,
                applied_at = ?,
                abandoned_at = NULL,
                claim_owner = NULL,
                claimed_at_ms = NULL,
                lease_expires_at_ms = NULL
            WHERE event_id = ?
              AND status = 'pending'
              AND project_id = ?
              AND claim_owner = ?
              AND fencing_token = ?
              AND lease_expires_at_ms > ?
            """,
            (
                attempted_at,
                attempted_at,
                claim.event_id,
                claim.project_id,
                claim.runner_id,
                claim.fencing_token,
                observed_at_ms,
            ),
        )
        return cursor.rowcount > 0


def record_failure(
    claim: TtsHandoffClaim,
    *,
    attempted_at: str,
    observed_at_ms: int,
    error: str,
) -> bool:
    with database.conn() as connection:
        cursor = connection.execute(
            """
            UPDATE video_localization_tts_handoff_outbox
            SET
                attempt_count = attempt_count + 1,
                last_attempt_at = ?,
                last_error = ?,
                claim_owner = NULL,
                claimed_at_ms = NULL,
                lease_expires_at_ms = NULL
            WHERE event_id = ?
              AND status = 'pending'
              AND project_id = ?
              AND claim_owner = ?
              AND fencing_token = ?
              AND lease_expires_at_ms > ?
            """,
            (
                attempted_at,
                error[:2_000],
                claim.event_id,
                claim.project_id,
                claim.runner_id,
                claim.fencing_token,
                observed_at_ms,
            ),
        )
        return cursor.rowcount > 0


def require_active_claim(
    connection: Connection,
    claim: TtsHandoffClaim,
    *,
    target_project_id: str,
    observed_at_ms: int,
) -> None:
    if claim.project_id != target_project_id:
        raise TtsHandoffClaimLost(
            "TTS handoff claim belongs to another project"
        )
    row = connection.execute(
        """
        SELECT 1
        FROM video_localization_tts_handoff_outbox
        WHERE event_id = ?
          AND project_id = ?
          AND status = 'pending'
          AND claim_owner = ?
          AND fencing_token = ?
          AND lease_expires_at_ms > ?
        """,
        (
            claim.event_id,
            claim.project_id,
            claim.runner_id,
            claim.fencing_token,
            observed_at_ms,
        ),
    ).fetchone()
    if row is None:
        raise TtsHandoffClaimLost(
            "TTS handoff claim is missing, expired, or superseded"
        )


def mark_abandoned(
    event_id: str,
    *,
    attempted_at: str,
    reason: str,
) -> bool:
    with database.conn() as connection:
        return mark_abandoned_from_connection(
            connection,
            event_id,
            attempted_at=attempted_at,
            reason=reason,
        )


def abandon_claim(
    claim: TtsHandoffClaim,
    *,
    attempted_at: str,
    observed_at_ms: int,
    reason: str,
) -> bool:
    with database.conn() as connection:
        return abandon_claim_from_connection(
            connection,
            claim,
            attempted_at=attempted_at,
            observed_at_ms=observed_at_ms,
            reason=reason,
        )


def abandon_claim_from_connection(
    connection: Connection,
    claim: TtsHandoffClaim,
    *,
    attempted_at: str,
    observed_at_ms: int,
    reason: str,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE video_localization_tts_handoff_outbox
        SET
            status = 'abandoned',
            attempt_count = attempt_count + 1,
            last_attempt_at = ?,
            last_error = ?,
            applied_at = NULL,
            abandoned_at = ?,
            claim_owner = NULL,
            claimed_at_ms = NULL,
            lease_expires_at_ms = NULL
        WHERE event_id = ?
          AND project_id = ?
          AND status = 'pending'
          AND claim_owner = ?
          AND fencing_token = ?
          AND lease_expires_at_ms > ?
        """,
        (
            attempted_at,
            reason[:2_000],
            attempted_at,
            claim.event_id,
            claim.project_id,
            claim.runner_id,
            claim.fencing_token,
            observed_at_ms,
        ),
    )
    return cursor.rowcount > 0


def mark_abandoned_from_connection(
    connection: Connection,
    event_id: str,
    *,
    attempted_at: str,
    reason: str,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE video_localization_tts_handoff_outbox
        SET
            status = 'abandoned',
            attempt_count = attempt_count + 1,
            last_attempt_at = ?,
            last_error = ?,
            applied_at = NULL,
            abandoned_at = ?,
            claim_owner = NULL,
            claimed_at_ms = NULL,
            lease_expires_at_ms = NULL
        WHERE event_id = ?
          AND status = 'pending'
        """,
        (
            attempted_at,
            reason[:2_000],
            attempted_at,
            event_id,
        ),
    )
    return cursor.rowcount > 0


def abandon_project(
    connection: Connection,
    project_id: str,
    *,
    abandoned_at: str,
    reason: str,
) -> None:
    connection.execute(
        """
        UPDATE video_localization_tts_handoff_outbox
        SET
            status = 'abandoned',
            last_attempt_at = ?,
            last_error = ?,
            applied_at = NULL,
            abandoned_at = ?,
            claim_owner = NULL,
            claimed_at_ms = NULL,
            lease_expires_at_ms = NULL
        WHERE project_id = ?
          AND status = 'pending'
        """,
        (
            abandoned_at,
            reason[:2_000],
            abandoned_at,
            project_id,
        ),
    )


def delete_project(
    connection: Connection,
    project_id: str,
) -> None:
    connection.execute(
        """
        DELETE FROM video_localization_tts_handoff_outbox
        WHERE project_id = ?
        """,
        (project_id,),
    )


def pending_count() -> int:
    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM video_localization_tts_handoff_outbox
            WHERE status = 'pending'
            """
        ).fetchone()
    return int(row["count"]) if row is not None else 0


def _event_from_row(row) -> StoredTtsHandoffEvent:
    payload_version = str(row["payload_version"])
    if payload_version != TTS_HANDOFF_EVENT_SCHEMA_VERSION:
        raise ValueError("unsupported video-localization TTS handoff event version")
    payload = TTS_HANDOFF_EVENT_ADAPTER.validate_python(json.loads(str(row["payload_json"])))
    if payload.project_id != str(row["project_id"]) or payload.event_kind != str(row["event_kind"]):
        raise ValueError("video-localization TTS handoff event envelope mismatch")
    status = str(row["status"])
    if status not in {"pending", "applied", "abandoned"}:
        raise ValueError("video-localization TTS handoff event status is invalid")
    return StoredTtsHandoffEvent(
        event_id=str(row["event_id"]),
        payload=payload,
        status=cast(TtsHandoffEventStatus, status),
        attempt_count=int(row["attempt_count"]),
        last_error=(str(row["last_error"]) if row["last_error"] is not None else None),
    )


__all__ = [
    "StoredTtsHandoffEvent",
    "TtsHandoffClaimDecision",
    "TtsHandoffClaimLost",
    "TtsHandoffClaimOutcome",
    "TtsHandoffEventStatus",
    "abandon_claim",
    "abandon_claim_from_connection",
    "abandon_project",
    "claim_pending",
    "delete_project",
    "enqueue",
    "get",
    "heartbeat_claim",
    "list_pending",
    "mark_abandoned",
    "mark_abandoned_from_connection",
    "mark_applied",
    "pending_count",
    "record_failure",
    "require_active_claim",
    "registration_event_id",
    "result_placement_event_id",
    "workflow_terminal_event_id",
]
