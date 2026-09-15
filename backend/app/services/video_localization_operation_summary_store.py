from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Iterable, Literal, cast

from app.schemas.video_localization_operation_summary import (
    OperationSummaryCoreV1,
    SUMMARY_CORE_SCHEMA_VERSION,
    operation_summary_core_fingerprint,
    operation_summary_core_json,
)


SummaryProjectionStatus = Literal[
    "missing",
    "shadow",
    "verified",
    "authoritative",
    "repair_required",
]
_PRESERVED_STATUSES = {
    "verified",
    "authoritative",
    "repair_required",
}
_VALID_STATUSES = {
    "missing",
    "shadow",
    *_PRESERVED_STATUSES,
}
_TERMINAL_STATUSES = {"success", "failed", "cancelled"}
MAX_ACTIVE_OPERATION_COUNT = 32
MAX_HISTORY_PAGE_SIZE = 100
_HISTORY_CURSOR_VERSION = 1
_MAX_HISTORY_CURSOR_LENGTH = 1024


class OperationSummaryProjectionConflict(RuntimeError):
    """The summary projection does not cover the authoritative ledger."""


class OperationSummaryProjectionSourceChanged(RuntimeError):
    """The project changed reader source while a feed was being built."""


class OperationSummaryHistoryCursorInvalid(ValueError):
    """The terminal-history cursor is malformed or unsupported."""


class OperationSummaryHistoryCursorStale(RuntimeError):
    """The terminal-history snapshot changed after the cursor was issued."""


@dataclass(frozen=True)
class OperationSummaryProjectionState:
    project_id: str
    projection_revision: int
    summary_schema_version: str | None
    summary_status: SummaryProjectionStatus
    summary_row_count: int
    summary_fingerprint: str | None
    history_revision: int
    history_fingerprint: str | None
    last_verified_at: str | None


@dataclass(frozen=True)
class OperationSummaryAuthority:
    summary_schema_version: str
    closed_at: str


@dataclass(frozen=True)
class OperationSummaryProjectionSync:
    changed: bool
    history_changed: bool
    row_count: int
    summary_fingerprint: str
    history_revision: int
    history_fingerprint: str


@dataclass(frozen=True)
class OperationSummaryProjectionIntegrity:
    ledger_row_count: int
    summary_row_count: int
    joined_row_count: int
    summary_fingerprint: str
    history_fingerprint: str


@dataclass(frozen=True)
class OperationSummaryFeedDescriptor:
    project_id: str
    projection_revision: int
    summary_status: SummaryProjectionStatus
    history_revision: int = 0
    authority_closed: bool = False

    @property
    def repository_ready(self) -> bool:
        return (
            self.summary_status in {"verified", "authoritative"}
            or (
                self.authority_closed
                and self.summary_status == "missing"
            )
        )


@dataclass(frozen=True)
class OperationSummaryProjectionRecord:
    core: OperationSummaryCoreV1
    kind: str
    status: str
    cancel_requested: bool
    created_at: str
    completed_at: str | None


@dataclass(frozen=True)
class OperationSummaryProjectionRead:
    descriptor: OperationSummaryFeedDescriptor
    records: tuple[OperationSummaryProjectionRecord, ...]


@dataclass(frozen=True)
class OperationSummaryProjectionPage:
    descriptor: OperationSummaryFeedDescriptor
    active_records: tuple[OperationSummaryProjectionRecord, ...]
    history_records: tuple[OperationSummaryProjectionRecord, ...]
    history_total: int
    next_cursor: str | None


@dataclass(frozen=True)
class OperationSummaryHistoryCursor:
    history_revision: int
    created_at: str
    operation_id: str


def validate_project_projection(
    connection: Connection,
    project_id: str,
) -> None:
    """Reject a corrupt existing shadow before applying a new write."""

    state = _read_state(connection, project_id)
    summary_row_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM video_localization_operation_summaries
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()[0]
    )
    if state is None:
        if summary_row_count:
            raise OperationSummaryProjectionConflict(
                "summary rows exist without projection state"
            )
        return
    _validate_state_before_sync(
        state,
        existing_row_count=summary_row_count,
    )
    if state.summary_status == "missing":
        return
    projected_rows = connection.execute(
        """
        SELECT
            ledger.operation_id,
            ledger.kind,
            ledger.status,
            ledger.cancel_requested,
            ledger.state_revision,
            ledger.created_at,
            ledger.completed_at,
            summary.summary_schema_version,
            summary.ledger_state_revision
                AS summary_ledger_state_revision,
            summary.core_revision,
            summary.content_fingerprint
        FROM video_localization_operations AS ledger
        LEFT JOIN video_localization_operation_summaries AS summary
          ON summary.project_id = ledger.project_id
         AND summary.operation_id = ledger.operation_id
        WHERE ledger.project_id = ?
        ORDER BY ledger.operation_id
        """,
        (project_id,),
    ).fetchall()
    if (
        len(projected_rows) != summary_row_count
        or any(
            row["core_revision"] is None
            for row in projected_rows
        )
    ):
        raise OperationSummaryProjectionConflict(
            "summary projection coverage is inconsistent"
        )
    for row in projected_rows:
        if (
            str(row["summary_schema_version"])
            != SUMMARY_CORE_SCHEMA_VERSION
        ):
            raise OperationSummaryProjectionConflict(
                "stored summary core schema is not supported"
            )
        if int(row["summary_ledger_state_revision"]) != int(
            row["state_revision"]
        ):
            raise OperationSummaryProjectionConflict(
                "summary ledger revision is inconsistent"
            )
    summary_fingerprint = _projection_fingerprint(
        projected_rows
    )
    history_fingerprint = _projection_fingerprint(
        row
        for row in projected_rows
        if str(row["status"]) in _TERMINAL_STATUSES
    )
    if (
        state.summary_fingerprint != summary_fingerprint
        or state.history_fingerprint != history_fingerprint
    ):
        raise OperationSummaryProjectionConflict(
            "summary projection fingerprint is inconsistent"
        )


def promote_project_projection(
    connection: Connection,
    project_id: str,
    *,
    verified_at: str,
) -> bool:
    """Promote one transactionally rechecked shadow to verified."""

    state = _read_state(connection, project_id)
    if state is None:
        raise OperationSummaryProjectionConflict(
            "operation projection state is missing"
        )
    if state.summary_status == "authoritative":
        validate_project_projection(connection, project_id)
        return False
    if state.summary_status not in {"shadow", "verified"}:
        raise OperationSummaryProjectionConflict(
            "operation summary projection is not promotable"
        )
    validate_project_projection(connection, project_id)
    if state.summary_status == "verified":
        return False
    connection.execute(
        """
        UPDATE video_localization_operation_projection_state
        SET summary_status = 'verified', last_verified_at = ?
        WHERE project_id = ?
          AND summary_status = 'shadow'
        """,
        (verified_at, project_id),
    )
    return True


def close_project_projection_authority(
    connection: Connection,
    project_id: str,
    *,
    closed_at: str,
) -> bool:
    """Promote one transactionally verified projection to authoritative."""

    state = _read_state(connection, project_id)
    if state is None:
        raise OperationSummaryProjectionConflict(
            "operation projection state is missing"
        )
    if state.summary_status not in {"verified", "authoritative"}:
        raise OperationSummaryProjectionConflict(
            "operation summary projection is not verified"
        )
    validate_project_projection(connection, project_id)
    if state.summary_status == "authoritative":
        return False
    connection.execute(
        """
        UPDATE video_localization_operation_projection_state
        SET summary_status = 'authoritative', last_verified_at = ?
        WHERE project_id = ?
          AND summary_status = 'verified'
        """,
        (closed_at, project_id),
    )
    return True


def mark_summary_authority_closed(
    connection: Connection,
    *,
    closed_at: str,
) -> bool:
    """Persist the global release boundary after every project is verified."""

    current = _read_summary_authority(connection)
    if current is not None:
        if (
            current.summary_schema_version
            != SUMMARY_CORE_SCHEMA_VERSION
        ):
            raise OperationSummaryProjectionConflict(
                "operation summary authority schema is incompatible"
            )
        return False
    connection.execute(
        """
        INSERT INTO video_localization_operation_summary_authority (
            authority_key,
            summary_schema_version,
            closed_at
        )
        VALUES ('operation-summary', ?, ?)
        """,
        (SUMMARY_CORE_SCHEMA_VERSION, closed_at),
    )
    return True


def read_summary_authority(
    connection: Connection | None = None,
) -> OperationSummaryAuthority | None:
    if connection is not None:
        return _read_summary_authority(connection)

    from app.services import database

    with database.conn() as connection:
        return _read_summary_authority(connection)


def backfill_project_summaries(
    connection: Connection,
    project_id: str,
    cores: Iterable[OperationSummaryCoreV1],
    *,
    projected_at: str,
) -> OperationSummaryProjectionSync:
    """Explicitly seed or repair a legacy-authoritative projection.

    Runtime shadow writes never replace a corrupt projection. The bounded
    migration command is the explicit repair boundary while Project remains
    authoritative: a previously marked project is rebuilt from typed legacy
    operations, then returned to shadow observation.
    """

    state = _read_state(connection, project_id)
    if state is None:
        connection.execute(
            """
            INSERT INTO video_localization_operation_projection_state (
                project_id,
                projection_revision
            )
            VALUES (?, 0)
            """,
            (project_id,),
        )
        state = _read_state(connection, project_id)
    if state is None:
        raise OperationSummaryProjectionConflict(
            "operation projection state could not be initialized"
        )
    if state.summary_status == "repair_required":
        connection.execute(
            """
            DELETE FROM video_localization_operation_summaries
            WHERE project_id = ?
            """,
            (project_id,),
        )
        connection.execute(
            """
            UPDATE video_localization_operation_projection_state
            SET
                summary_schema_version = NULL,
                summary_status = 'missing',
                summary_row_count = 0,
                summary_fingerprint = NULL,
                history_fingerprint = NULL,
                last_verified_at = NULL
            WHERE project_id = ?
            """,
            (project_id,),
        )
    elif state.summary_status != "missing":
        validate_project_projection(connection, project_id)
    sync = sync_project_summaries(
        connection,
        project_id,
        cores,
        projected_at=projected_at,
    )
    authority = _read_summary_authority(connection)
    if authority is None:
        connection.execute(
            """
            UPDATE video_localization_operation_projection_state
            SET summary_status = 'shadow', last_verified_at = NULL
            WHERE project_id = ?
              AND summary_status IN (
                  'missing',
                  'repair_required',
                  'shadow'
              )
            """,
            (project_id,),
        )
    else:
        connection.execute(
            """
            UPDATE video_localization_operation_projection_state
            SET
                summary_status = 'authoritative',
                last_verified_at = ?
            WHERE project_id = ?
              AND summary_status != 'repair_required'
            """,
            (authority.closed_at, project_id),
        )
    return sync


def mark_project_repair_required(
    connection: Connection,
    project_id: str,
) -> None:
    """Record a migration failure without touching Project business time."""

    connection.execute(
        """
        INSERT INTO video_localization_operation_projection_state (
            project_id,
            projection_revision,
            summary_status
        )
        VALUES (?, 0, 'repair_required')
        ON CONFLICT(project_id) DO UPDATE SET
            summary_status = 'repair_required',
            last_verified_at = NULL
        """,
        (project_id,),
    )


def inspect_project_projection(
    connection: Connection,
    project_id: str,
) -> OperationSummaryProjectionIntegrity:
    """Compute projection integrity values inside the caller's snapshot."""

    ledger_row_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM video_localization_operations
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()[0]
    )
    summary_row_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM video_localization_operation_summaries
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()[0]
    )
    projected_rows = connection.execute(
        """
        SELECT
            ledger.operation_id,
            ledger.kind,
            ledger.status,
            ledger.cancel_requested,
            ledger.state_revision,
            ledger.created_at,
            ledger.completed_at,
            summary.core_revision,
            summary.content_fingerprint
        FROM video_localization_operations AS ledger
        JOIN video_localization_operation_summaries AS summary
          ON summary.project_id = ledger.project_id
         AND summary.operation_id = ledger.operation_id
        WHERE ledger.project_id = ?
        ORDER BY ledger.operation_id
        """,
        (project_id,),
    ).fetchall()
    return OperationSummaryProjectionIntegrity(
        ledger_row_count=ledger_row_count,
        summary_row_count=summary_row_count,
        joined_row_count=len(projected_rows),
        summary_fingerprint=_projection_fingerprint(
            projected_rows
        ),
        history_fingerprint=_projection_fingerprint(
            row
            for row in projected_rows
            if str(row["status"]) in _TERMINAL_STATUSES
        ),
    )


def sync_project_summaries(
    connection: Connection,
    project_id: str,
    cores: Iterable[OperationSummaryCoreV1],
    *,
    projected_at: str,
) -> OperationSummaryProjectionSync:
    core_by_id = _validated_cores(project_id, cores)
    ledger_rows = connection.execute(
        """
        SELECT
            operation_id,
            kind,
            status,
            cancel_requested,
            state_revision,
            created_at,
            completed_at
        FROM video_localization_operations
        WHERE project_id = ?
        ORDER BY operation_id
        """,
        (project_id,),
    ).fetchall()
    ledger_ids = {
        str(row["operation_id"]) for row in ledger_rows
    }
    if ledger_ids != set(core_by_id):
        raise OperationSummaryProjectionConflict(
            "summary cores must exactly cover the operation ledger"
        )
    existing_rows = connection.execute(
        """
        SELECT
            operation_id,
            summary_schema_version,
            ledger_state_revision,
            core_revision,
            content_fingerprint,
            core_json
        FROM video_localization_operation_summaries
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchall()
    existing_by_id = {
        str(row["operation_id"]): row
        for row in existing_rows
    }
    state = _read_state(connection, project_id)
    if state is None:
        raise OperationSummaryProjectionConflict(
            "operation projection state is missing"
        )
    _validate_state_before_sync(
        state,
        existing_row_count=len(existing_rows),
    )
    ledger_by_id = {
        str(row["operation_id"]): row for row in ledger_rows
    }
    for operation_id, core in core_by_id.items():
        ledger = ledger_by_id[operation_id]
        ledger_state_revision = int(
            ledger["state_revision"]
        )
        fingerprint = operation_summary_core_fingerprint(core)
        current = existing_by_id.get(operation_id)
        if current is not None:
            _validate_existing_summary_row(
                project_id,
                operation_id,
                current,
            )
        core_changed = (
            current is None
            or str(current["content_fingerprint"])
            != fingerprint
        )
        core_revision = (
            1
            if current is None
            else int(current["core_revision"])
            + int(core_changed)
        )
        if (
            current is not None
            and not core_changed
            and int(current["ledger_state_revision"])
            == ledger_state_revision
        ):
            continue
        connection.execute(
            """
            INSERT INTO video_localization_operation_summaries (
                project_id,
                operation_id,
                summary_schema_version,
                ledger_state_revision,
                core_revision,
                content_fingerprint,
                core_json,
                projected_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, operation_id) DO UPDATE SET
                summary_schema_version =
                    excluded.summary_schema_version,
                ledger_state_revision =
                    excluded.ledger_state_revision,
                core_revision = excluded.core_revision,
                content_fingerprint =
                    excluded.content_fingerprint,
                core_json = excluded.core_json,
                projected_at = excluded.projected_at
            """,
            (
                project_id,
                operation_id,
                SUMMARY_CORE_SCHEMA_VERSION,
                ledger_state_revision,
                core_revision,
                fingerprint,
                operation_summary_core_json(core),
                projected_at,
            ),
        )
    stale_ids = set(existing_by_id) - ledger_ids
    if stale_ids:
        connection.executemany(
            """
            DELETE FROM video_localization_operation_summaries
            WHERE project_id = ?
              AND operation_id = ?
            """,
            [
                (project_id, operation_id)
                for operation_id in sorted(stale_ids)
            ],
        )
    projected_rows = connection.execute(
        """
        SELECT
            ledger.operation_id,
            ledger.kind,
            ledger.status,
            ledger.cancel_requested,
            ledger.state_revision,
            ledger.created_at,
            ledger.completed_at,
            summary.core_revision,
            summary.content_fingerprint
        FROM video_localization_operations AS ledger
        JOIN video_localization_operation_summaries AS summary
          ON summary.project_id = ledger.project_id
         AND summary.operation_id = ledger.operation_id
        WHERE ledger.project_id = ?
        ORDER BY ledger.operation_id
        """,
        (project_id,),
    ).fetchall()
    if len(projected_rows) != len(ledger_rows):
        raise OperationSummaryProjectionConflict(
            "summary projection join is incomplete"
        )
    summary_fingerprint = _projection_fingerprint(
        projected_rows
    )
    history_fingerprint = _projection_fingerprint(
        row
        for row in projected_rows
        if str(row["status"]) in _TERMINAL_STATUSES
    )
    previous_summary_fingerprint = (
        state.summary_fingerprint
    )
    previous_history_fingerprint = (
        state.history_fingerprint
    )
    history_revision = state.history_revision
    history_changed = (
        previous_history_fingerprint is not None
        and previous_history_fingerprint != history_fingerprint
    )
    if history_changed:
        history_revision += 1
    authority = _read_summary_authority(connection)
    if state.summary_status == "repair_required":
        summary_status = "repair_required"
    elif authority is not None:
        summary_status = "authoritative"
    elif state.summary_status in _PRESERVED_STATUSES:
        summary_status = state.summary_status
    else:
        summary_status = "shadow"
    connection.execute(
        """
        UPDATE video_localization_operation_projection_state
        SET
            summary_schema_version = ?,
            summary_status = ?,
            summary_row_count = ?,
            summary_fingerprint = ?,
            history_revision = ?,
            history_fingerprint = ?
        WHERE project_id = ?
        """,
        (
            SUMMARY_CORE_SCHEMA_VERSION,
            summary_status,
            len(projected_rows),
            summary_fingerprint,
            history_revision,
            history_fingerprint,
            project_id,
        ),
    )
    return OperationSummaryProjectionSync(
        changed=(
            previous_summary_fingerprint != summary_fingerprint
        ),
        history_changed=history_changed,
        row_count=len(projected_rows),
        summary_fingerprint=summary_fingerprint,
        history_revision=history_revision,
        history_fingerprint=history_fingerprint,
    )


def encode_history_cursor(
    history_revision: int,
    created_at: str,
    operation_id: str,
) -> str:
    cursor = OperationSummaryHistoryCursor(
        history_revision=history_revision,
        created_at=created_at,
        operation_id=operation_id,
    )
    _validate_history_cursor(cursor)
    body = {
        "v": _HISTORY_CURSOR_VERSION,
        "r": cursor.history_revision,
        "t": cursor.created_at,
        "o": cursor.operation_id,
    }
    checksum = hashlib.sha256(
        _canonical_cursor_json(body)
    ).hexdigest()
    payload = {**body, "c": checksum}
    return base64.urlsafe_b64encode(
        _canonical_cursor_json(payload)
    ).decode("ascii").rstrip("=")


def decode_history_cursor(
    value: str,
) -> OperationSummaryHistoryCursor:
    encoded = value.strip()
    if not encoded or len(encoded) > _MAX_HISTORY_CURSOR_LENGTH:
        raise OperationSummaryHistoryCursorInvalid(
            "operation history cursor is invalid"
        )
    try:
        padding = "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(
            encoded + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded)
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ) as exc:
        raise OperationSummaryHistoryCursorInvalid(
            "operation history cursor is invalid"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "v",
        "r",
        "t",
        "o",
        "c",
    }:
        raise OperationSummaryHistoryCursorInvalid(
            "operation history cursor is invalid"
        )
    body = {
        "v": payload["v"],
        "r": payload["r"],
        "t": payload["t"],
        "o": payload["o"],
    }
    checksum = payload["c"]
    if (
        payload["v"] != _HISTORY_CURSOR_VERSION
        or not isinstance(checksum, str)
        or not hmac.compare_digest(
            checksum,
            hashlib.sha256(
                _canonical_cursor_json(body)
            ).hexdigest(),
        )
    ):
        raise OperationSummaryHistoryCursorInvalid(
            "operation history cursor is invalid"
        )
    try:
        cursor = OperationSummaryHistoryCursor(
            history_revision=int(payload["r"]),
            created_at=str(payload["t"]),
            operation_id=str(payload["o"]),
        )
    except (TypeError, ValueError) as exc:
        raise OperationSummaryHistoryCursorInvalid(
            "operation history cursor is invalid"
        ) from exc
    _validate_history_cursor(cursor)
    return cursor


def read_projection_state(
    project_id: str,
) -> OperationSummaryProjectionState | None:
    from app.services import database

    with database.conn() as connection:
        return _read_state(connection, project_id)


def read_feed_descriptor(
    project_id: str,
) -> OperationSummaryFeedDescriptor | None:
    """Read the source-selection fields without touching Project JSON."""

    from app.services import database

    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT
                projects.project_id,
                COALESCE(state.projection_revision, 0)
                    AS projection_revision,
                COALESCE(state.history_revision, 0)
                    AS history_revision,
                COALESCE(state.summary_status, 'missing')
                    AS summary_status,
                (
                    SELECT summary_schema_version
                    FROM video_localization_operation_summary_authority
                    WHERE authority_key = 'operation-summary'
                ) AS authority_schema_version,
                (
                    SELECT closed_at
                    FROM video_localization_operation_summary_authority
                    WHERE authority_key = 'operation-summary'
                ) AS authority_closed_at
            FROM projects
            LEFT JOIN video_localization_operation_projection_state
                AS state
              ON state.project_id = projects.project_id
            WHERE projects.project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        descriptor = _descriptor_from_row(row)
        if (
            descriptor.authority_closed
            and descriptor.summary_status == "missing"
        ):
            _require_empty_authoritative_projection(
                connection,
                project_id,
                descriptor,
                _read_state(connection, project_id),
            )
        return descriptor


def read_verified_project_summaries(
    project_id: str,
) -> OperationSummaryProjectionRead | None:
    """Read and validate a repository-owned v1 feed snapshot.

    The query set never selects ``projects.data``. Projects that are not
    verified remain on the compatibility reader; repair-required or corrupt
    verified projections fail explicitly.
    """

    from app.services import database

    with database.conn() as connection:
        connection.execute("BEGIN")
        descriptor_row = connection.execute(
            """
            SELECT
                projects.project_id,
                COALESCE(state.projection_revision, 0)
                    AS projection_revision,
                COALESCE(state.history_revision, 0)
                    AS history_revision,
                COALESCE(state.summary_status, 'missing')
                    AS summary_status,
                (
                    SELECT summary_schema_version
                    FROM video_localization_operation_summary_authority
                    WHERE authority_key = 'operation-summary'
                ) AS authority_schema_version,
                (
                    SELECT closed_at
                    FROM video_localization_operation_summary_authority
                    WHERE authority_key = 'operation-summary'
                ) AS authority_closed_at
            FROM projects
            LEFT JOIN video_localization_operation_projection_state
                AS state
              ON state.project_id = projects.project_id
            WHERE projects.project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if descriptor_row is None:
            return None
        descriptor = _descriptor_from_row(descriptor_row)
        if not descriptor.repository_ready:
            raise OperationSummaryProjectionSourceChanged(
                "operation summary projection reader source changed"
            )
        state = _read_state(connection, project_id)
        if descriptor.summary_status == "missing":
            _require_empty_authoritative_projection(
                connection,
                project_id,
                descriptor,
                state,
            )
            return OperationSummaryProjectionRead(
                descriptor=descriptor,
                records=(),
            )
        if state is None:
            raise OperationSummaryProjectionConflict(
                "operation projection state is missing"
            )
        integrity = inspect_project_projection(
            connection,
            project_id,
        )
        rows = connection.execute(
            """
            SELECT
                ledger.operation_id,
                ledger.project_id,
                ledger.kind,
                ledger.status,
                ledger.cancel_requested,
                ledger.state_revision,
                ledger.created_at,
                ledger.completed_at,
                summary.summary_schema_version,
                summary.ledger_state_revision,
                summary.core_revision,
                summary.content_fingerprint,
                summary.core_json
            FROM video_localization_operations AS ledger
            JOIN video_localization_operation_summaries AS summary
              ON summary.project_id = ledger.project_id
             AND summary.operation_id = ledger.operation_id
            WHERE ledger.project_id = ?
            ORDER BY ledger.created_at DESC, ledger.operation_id DESC
            """,
            (project_id,),
        ).fetchall()
        _require_read_integrity(
            project_id,
            state,
            integrity,
            rows,
        )
        records = tuple(
            _projection_record(project_id, row)
            for row in rows
        )
    return OperationSummaryProjectionRead(
        descriptor=descriptor,
        records=records,
    )


def read_verified_project_summary_page(
    project_id: str,
    *,
    history_limit: int,
    cursor: OperationSummaryHistoryCursor | None = None,
) -> OperationSummaryProjectionPage | None:
    """Read one bounded v2 head or terminal-history page."""

    if history_limit < 1 or history_limit > MAX_HISTORY_PAGE_SIZE:
        raise ValueError("operation history page size is invalid")

    from app.services import database

    with database.conn() as connection:
        connection.execute("BEGIN")
        descriptor_row = connection.execute(
            """
            SELECT
                projects.project_id,
                COALESCE(state.projection_revision, 0)
                    AS projection_revision,
                COALESCE(state.history_revision, 0)
                    AS history_revision,
                COALESCE(state.summary_status, 'missing')
                    AS summary_status,
                (
                    SELECT summary_schema_version
                    FROM video_localization_operation_summary_authority
                    WHERE authority_key = 'operation-summary'
                ) AS authority_schema_version,
                (
                    SELECT closed_at
                    FROM video_localization_operation_summary_authority
                    WHERE authority_key = 'operation-summary'
                ) AS authority_closed_at
            FROM projects
            LEFT JOIN video_localization_operation_projection_state
                AS state
              ON state.project_id = projects.project_id
            WHERE projects.project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if descriptor_row is None:
            return None
        descriptor = _descriptor_from_row(descriptor_row)
        if not descriptor.repository_ready:
            raise OperationSummaryProjectionSourceChanged(
                "operation summary projection reader source changed"
            )
        state = _read_state(connection, project_id)
        if descriptor.summary_status == "missing":
            _require_empty_authoritative_projection(
                connection,
                project_id,
                descriptor,
                state,
            )
            return OperationSummaryProjectionPage(
                descriptor=descriptor,
                active_records=(),
                history_records=(),
                history_total=0,
                next_cursor=None,
            )
        if state is None:
            raise OperationSummaryProjectionConflict(
                "operation projection state is missing"
            )
        _require_bounded_read_integrity(
            connection,
            project_id,
            state,
        )
        if (
            cursor is not None
            and cursor.history_revision
            != descriptor.history_revision
        ):
            raise OperationSummaryHistoryCursorStale(
                "operation history cursor is stale"
            )
        active_rows = connection.execute(
            """
            SELECT
                ledger.operation_id,
                ledger.project_id,
                ledger.kind,
                ledger.status,
                ledger.cancel_requested,
                ledger.state_revision,
                ledger.created_at,
                ledger.completed_at,
                summary.summary_schema_version,
                summary.ledger_state_revision,
                summary.core_revision,
                summary.content_fingerprint,
                summary.core_json
            FROM video_localization_operations AS ledger
            JOIN video_localization_operation_summaries AS summary
              ON summary.project_id = ledger.project_id
             AND summary.operation_id = ledger.operation_id
            WHERE ledger.project_id = ?
              AND ledger.status IN ('queued', 'running')
            ORDER BY ledger.created_at DESC, ledger.operation_id DESC
            LIMIT ?
            """,
            (project_id, MAX_ACTIVE_OPERATION_COUNT + 1),
        ).fetchall()
        if len(active_rows) > MAX_ACTIVE_OPERATION_COUNT:
            raise OperationSummaryProjectionConflict(
                "active operation count exceeds the feed limit"
            )
        history_total = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM video_localization_operations
                WHERE project_id = ?
                  AND status IN ('success', 'failed', 'cancelled')
                """,
                (project_id,),
            ).fetchone()[0]
        )
        cursor_clause = ""
        parameters: list[object] = [project_id]
        if cursor is not None:
            cursor_clause = """
              AND (
                    ledger.created_at < ?
                 OR (
                        ledger.created_at = ?
                    AND ledger.operation_id < ?
                 )
              )
            """
            parameters.extend(
                [
                    cursor.created_at,
                    cursor.created_at,
                    cursor.operation_id,
                ]
            )
        parameters.append(history_limit + 1)
        history_rows = connection.execute(
            f"""
            SELECT
                ledger.operation_id,
                ledger.project_id,
                ledger.kind,
                ledger.status,
                ledger.cancel_requested,
                ledger.state_revision,
                ledger.created_at,
                ledger.completed_at,
                summary.summary_schema_version,
                summary.ledger_state_revision,
                summary.core_revision,
                summary.content_fingerprint,
                summary.core_json
            FROM video_localization_operations AS ledger
            JOIN video_localization_operation_summaries AS summary
              ON summary.project_id = ledger.project_id
             AND summary.operation_id = ledger.operation_id
            WHERE ledger.project_id = ?
              AND ledger.status IN ('success', 'failed', 'cancelled')
              {cursor_clause}
            ORDER BY ledger.created_at DESC, ledger.operation_id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        page_rows = history_rows[:history_limit]
        next_cursor = (
            encode_history_cursor(
                descriptor.history_revision,
                str(page_rows[-1]["created_at"]),
                str(page_rows[-1]["operation_id"]),
            )
            if len(history_rows) > history_limit and page_rows
            else None
        )
        active_records = (
            tuple(
                _projection_record(project_id, row)
                for row in active_rows
            )
            if cursor is None
            else ()
        )
        history_records = tuple(
            _projection_record(project_id, row)
            for row in page_rows
        )
    return OperationSummaryProjectionPage(
        descriptor=descriptor,
        active_records=active_records,
        history_records=history_records,
        history_total=history_total,
        next_cursor=next_cursor,
    )


def delete_project(
    connection: Connection,
    project_id: str,
) -> None:
    connection.execute(
        """
        DELETE FROM video_localization_operation_summaries
        WHERE project_id = ?
        """,
        (project_id,),
    )


def _descriptor_from_row(row) -> OperationSummaryFeedDescriptor:
    summary_status = str(row["summary_status"])
    if summary_status not in _VALID_STATUSES:
        raise OperationSummaryProjectionConflict(
            "summary projection status is invalid"
        )
    try:
        projection_revision = int(row["projection_revision"])
        history_revision = int(row["history_revision"])
    except (TypeError, ValueError) as exc:
        raise OperationSummaryProjectionConflict(
            "operation projection revision is invalid"
        ) from exc
    if projection_revision < 0 or history_revision < 0:
        raise OperationSummaryProjectionConflict(
            "operation projection revision is invalid"
        )
    authority_schema_version = row["authority_schema_version"]
    authority_closed_at = row["authority_closed_at"]
    authority_closed = authority_schema_version is not None
    if authority_closed and (
        str(authority_schema_version)
        != SUMMARY_CORE_SCHEMA_VERSION
        or not str(authority_closed_at or "")
    ):
        raise OperationSummaryProjectionConflict(
            "operation summary authority marker is invalid"
        )
    return OperationSummaryFeedDescriptor(
        project_id=str(row["project_id"]),
        projection_revision=projection_revision,
        history_revision=history_revision,
        summary_status=cast(
            SummaryProjectionStatus,
            summary_status,
        ),
        authority_closed=authority_closed,
    )


def _require_empty_authoritative_projection(
    connection: Connection,
    project_id: str,
    descriptor: OperationSummaryFeedDescriptor,
    state: OperationSummaryProjectionState | None,
) -> None:
    if (
        not descriptor.authority_closed
        or descriptor.summary_status != "missing"
    ):
        raise OperationSummaryProjectionSourceChanged(
            "operation summary projection reader source changed"
        )
    counts = connection.execute(
        """
        SELECT
            (
                SELECT COUNT(*)
                FROM video_localization_operations
                WHERE project_id = ?
            ) AS ledger_count,
            (
                SELECT COUNT(*)
                FROM video_localization_operation_summaries
                WHERE project_id = ?
            ) AS summary_count
        """,
        (project_id, project_id),
    ).fetchone()
    if (
        state is not None
        and (
            state.summary_status != "missing"
            or state.summary_row_count != 0
            or state.summary_schema_version is not None
            or state.summary_fingerprint is not None
            or state.history_fingerprint is not None
        )
    ):
        raise OperationSummaryProjectionConflict(
            "empty operation summary projection state is invalid"
        )
    if (
        int(counts["ledger_count"]) != 0
        or int(counts["summary_count"]) != 0
    ):
        raise OperationSummaryProjectionConflict(
            "operation rows exist without an authoritative projection"
        )


def _require_read_integrity(
    project_id: str,
    state: OperationSummaryProjectionState,
    integrity: OperationSummaryProjectionIntegrity,
    rows,
) -> None:
    if state.summary_status not in {"verified", "authoritative"}:
        raise OperationSummaryProjectionSourceChanged(
            "operation summary projection reader source changed"
        )
    if (
        state.summary_schema_version
        != SUMMARY_CORE_SCHEMA_VERSION
        or state.summary_row_count != len(rows)
        or integrity.ledger_row_count != len(rows)
        or integrity.summary_row_count != len(rows)
        or integrity.joined_row_count != len(rows)
        or state.summary_fingerprint
        != integrity.summary_fingerprint
        or state.history_fingerprint
        != integrity.history_fingerprint
    ):
        raise OperationSummaryProjectionConflict(
            "operation summary projection integrity check failed"
        )
    if any(str(row["project_id"]) != project_id for row in rows):
        raise OperationSummaryProjectionConflict(
            "operation summary projection identity is invalid"
        )


def _require_bounded_read_integrity(
    connection: Connection,
    project_id: str,
    state: OperationSummaryProjectionState,
) -> None:
    if state.summary_status not in {"verified", "authoritative"}:
        raise OperationSummaryProjectionSourceChanged(
            "operation summary projection reader source changed"
        )
    counts = connection.execute(
        """
        SELECT
            (
                SELECT COUNT(*)
                FROM video_localization_operations
                WHERE project_id = ?
            ) AS ledger_count,
            (
                SELECT COUNT(*)
                FROM video_localization_operation_summaries
                WHERE project_id = ?
            ) AS summary_count,
            (
                SELECT COUNT(*)
                FROM video_localization_operations AS ledger
                JOIN video_localization_operation_summaries AS summary
                  ON summary.project_id = ledger.project_id
                 AND summary.operation_id = ledger.operation_id
                WHERE ledger.project_id = ?
            ) AS joined_count
        """,
        (project_id, project_id, project_id),
    ).fetchone()
    if (
        state.summary_schema_version
        != SUMMARY_CORE_SCHEMA_VERSION
        or state.summary_fingerprint is None
        or state.history_fingerprint is None
        or state.summary_row_count != int(counts["ledger_count"])
        or state.summary_row_count != int(counts["summary_count"])
        or state.summary_row_count != int(counts["joined_count"])
    ):
        raise OperationSummaryProjectionConflict(
            "operation summary projection integrity check failed"
        )


def _canonical_cursor_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _validate_history_cursor(
    cursor: OperationSummaryHistoryCursor,
) -> None:
    if (
        cursor.history_revision < 0
        or not cursor.created_at
        or len(cursor.created_at) > 100
        or not cursor.operation_id
        or len(cursor.operation_id) > 128
    ):
        raise OperationSummaryHistoryCursorInvalid(
            "operation history cursor is invalid"
        )


def _projection_record(
    project_id: str,
    row,
) -> OperationSummaryProjectionRecord:
    operation_id = str(row["operation_id"])
    _validate_existing_summary_row(
        project_id,
        operation_id,
        row,
    )
    try:
        ledger_state_revision = int(row["state_revision"])
        summary_ledger_revision = int(
            row["ledger_state_revision"]
        )
    except (TypeError, ValueError) as exc:
        raise OperationSummaryProjectionConflict(
            "stored summary ledger revision is invalid"
        ) from exc
    if ledger_state_revision != summary_ledger_revision:
        raise OperationSummaryProjectionConflict(
            "stored summary ledger revision is inconsistent"
        )
    try:
        core = OperationSummaryCoreV1.model_validate_json(
            str(row["core_json"])
        )
    except (TypeError, ValueError) as exc:
        raise OperationSummaryProjectionConflict(
            "stored summary core is invalid"
        ) from exc
    return OperationSummaryProjectionRecord(
        core=core,
        kind=str(row["kind"]),
        status=str(row["status"]),
        cancel_requested=bool(row["cancel_requested"]),
        created_at=str(row["created_at"]),
        completed_at=(
            str(row["completed_at"])
            if row["completed_at"] is not None
            else None
        ),
    )


def _validated_cores(
    project_id: str,
    cores: Iterable[OperationSummaryCoreV1],
) -> dict[str, OperationSummaryCoreV1]:
    core_by_id: dict[str, OperationSummaryCoreV1] = {}
    for core in cores:
        if core.project_id != project_id:
            raise OperationSummaryProjectionConflict(
                "summary core belongs to another project"
            )
        if core.summary_schema_version != SUMMARY_CORE_SCHEMA_VERSION:
            raise OperationSummaryProjectionConflict(
                "summary core schema is not supported"
            )
        if core.operation_id in core_by_id:
            raise OperationSummaryProjectionConflict(
                "summary operation identity is duplicated"
            )
        core_by_id[core.operation_id] = core
    return core_by_id


def _projection_fingerprint(rows: Iterable) -> str:
    payload = [
        {
            "operation_id": str(row["operation_id"]),
            "kind": str(row["kind"]),
            "status": str(row["status"]),
            "cancel_requested": bool(
                row["cancel_requested"]
            ),
            "state_revision": int(row["state_revision"]),
            "created_at": str(row["created_at"]),
            "completed_at": (
                str(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
            "core_revision": int(row["core_revision"]),
            "content_fingerprint": str(
                row["content_fingerprint"]
            ),
        }
        for row in rows
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_existing_summary_row(
    project_id: str,
    operation_id: str,
    row,
) -> None:
    if (
        str(row["summary_schema_version"])
        != SUMMARY_CORE_SCHEMA_VERSION
    ):
        raise OperationSummaryProjectionConflict(
            "stored summary core schema is not supported"
        )
    try:
        core = OperationSummaryCoreV1.model_validate_json(
            str(row["core_json"])
        )
    except Exception as exc:
        raise OperationSummaryProjectionConflict(
            "stored summary core is invalid"
        ) from exc
    if (
        core.project_id != project_id
        or core.operation_id != operation_id
    ):
        raise OperationSummaryProjectionConflict(
            "stored summary core identity is invalid"
        )
    if (
        operation_summary_core_fingerprint(core)
        != str(row["content_fingerprint"])
    ):
        raise OperationSummaryProjectionConflict(
            "stored summary core fingerprint is invalid"
        )
    if operation_summary_core_json(core) != str(row["core_json"]):
        raise OperationSummaryProjectionConflict(
            "stored summary core is not canonical"
        )
    try:
        ledger_state_revision = int(
            row["ledger_state_revision"]
        )
        core_revision = int(row["core_revision"])
    except (TypeError, ValueError) as exc:
        raise OperationSummaryProjectionConflict(
            "stored summary revisions are invalid"
        ) from exc
    if ledger_state_revision < 0 or core_revision < 1:
        raise OperationSummaryProjectionConflict(
            "stored summary revisions are invalid"
        )


def _validate_state_before_sync(
    state: OperationSummaryProjectionState,
    *,
    existing_row_count: int,
) -> None:
    if (
        state.projection_revision < 0
        or state.history_revision < 0
        or state.summary_row_count < 0
    ):
        raise OperationSummaryProjectionConflict(
            "operation projection revisions are invalid"
        )
    if state.summary_row_count != existing_row_count:
        raise OperationSummaryProjectionConflict(
            "summary projection row count is inconsistent"
        )
    if state.summary_status == "missing":
        if (
            state.summary_schema_version is not None
            or state.summary_fingerprint is not None
            or state.history_fingerprint is not None
            or existing_row_count != 0
        ):
            raise OperationSummaryProjectionConflict(
                "missing summary projection has persisted state"
            )
        return
    if (
        state.summary_schema_version
        != SUMMARY_CORE_SCHEMA_VERSION
        or state.summary_fingerprint is None
        or state.history_fingerprint is None
    ):
        raise OperationSummaryProjectionConflict(
            "summary projection state is incomplete"
        )


def _read_state(
    connection: Connection,
    project_id: str,
) -> OperationSummaryProjectionState | None:
    row = connection.execute(
        """
        SELECT
            project_id,
            projection_revision,
            summary_schema_version,
            summary_status,
            summary_row_count,
            summary_fingerprint,
            history_revision,
            history_fingerprint,
            last_verified_at
        FROM video_localization_operation_projection_state
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    summary_status = str(row["summary_status"])
    if summary_status not in _VALID_STATUSES:
        raise OperationSummaryProjectionConflict(
            "summary projection status is invalid"
        )
    return OperationSummaryProjectionState(
        project_id=str(row["project_id"]),
        projection_revision=int(row["projection_revision"]),
        summary_schema_version=(
            str(row["summary_schema_version"])
            if row["summary_schema_version"] is not None
            else None
        ),
        summary_status=cast(
            SummaryProjectionStatus,
            summary_status,
        ),
        summary_row_count=int(row["summary_row_count"]),
        summary_fingerprint=(
            str(row["summary_fingerprint"])
            if row["summary_fingerprint"] is not None
            else None
        ),
        history_revision=int(row["history_revision"]),
        history_fingerprint=(
            str(row["history_fingerprint"])
            if row["history_fingerprint"] is not None
            else None
        ),
        last_verified_at=(
            str(row["last_verified_at"])
            if row["last_verified_at"] is not None
            else None
        ),
    )


def _read_summary_authority(
    connection: Connection,
) -> OperationSummaryAuthority | None:
    row = connection.execute(
        """
        SELECT summary_schema_version, closed_at
        FROM video_localization_operation_summary_authority
        WHERE authority_key = 'operation-summary'
        """
    ).fetchone()
    if row is None:
        return None
    schema_version = str(row["summary_schema_version"])
    closed_at = str(row["closed_at"])
    if (
        schema_version != SUMMARY_CORE_SCHEMA_VERSION
        or not closed_at
    ):
        raise OperationSummaryProjectionConflict(
            "operation summary authority marker is invalid"
        )
    return OperationSummaryAuthority(
        summary_schema_version=schema_version,
        closed_at=closed_at,
    )


__all__ = [
    "MAX_ACTIVE_OPERATION_COUNT",
    "MAX_HISTORY_PAGE_SIZE",
    "OperationSummaryAuthority",
    "OperationSummaryFeedDescriptor",
    "OperationSummaryHistoryCursor",
    "OperationSummaryHistoryCursorInvalid",
    "OperationSummaryHistoryCursorStale",
    "OperationSummaryProjectionConflict",
    "OperationSummaryProjectionIntegrity",
    "OperationSummaryProjectionPage",
    "OperationSummaryProjectionRead",
    "OperationSummaryProjectionRecord",
    "OperationSummaryProjectionSourceChanged",
    "OperationSummaryProjectionState",
    "OperationSummaryProjectionSync",
    "SummaryProjectionStatus",
    "backfill_project_summaries",
    "close_project_projection_authority",
    "decode_history_cursor",
    "delete_project",
    "encode_history_cursor",
    "inspect_project_projection",
    "mark_project_repair_required",
    "mark_summary_authority_closed",
    "promote_project_projection",
    "read_feed_descriptor",
    "read_projection_state",
    "read_summary_authority",
    "read_verified_project_summary_page",
    "read_verified_project_summaries",
    "sync_project_summaries",
    "validate_project_projection",
]
