from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import Connection, IntegrityError
from typing import Any, Iterable, Literal

from app.schemas.video_localization_operation_detail import (
    SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
)
from app.schemas.video_localization_reference_candidates_step import (
    REFERENCE_CANDIDATES_WORKFLOW_VERSION,
)
from app.schemas.video_localization_source_audio_step import (
    SOURCE_AUDIO_WORKFLOW_VERSION,
)
from app.schemas.video_localization_speaker_diarization_step import (
    SPEAKER_DIARIZATION_WORKFLOW_VERSION,
)
from app.schemas.video_localization_stem_separation_step import (
    STEM_SEPARATION_WORKFLOW_VERSION,
)
from app.services import database


CommandType = Literal["submit", "cancel", "retry"]
CommandOrigin = Literal["command", "legacy_project"]
OutboxStatus = Literal["pending", "applied"]
_ACTIVE_STATUSES = {"queued", "running"}
_TERMINAL_STATUSES = {"success", "failed", "cancelled"}
_COMMAND_PAYLOAD_VERSION = "operation-command-v1"
_DEFAULT_WORKFLOW_VERSION = "operation-v1"
_DETAIL_MANAGED_WORKFLOW_VERSIONS = frozenset(
    {
        SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
        SOURCE_AUDIO_WORKFLOW_VERSION,
        STEM_SEPARATION_WORKFLOW_VERSION,
        REFERENCE_CANDIDATES_WORKFLOW_VERSION,
        SPEAKER_DIARIZATION_WORKFLOW_VERSION,
    }
)


class OperationCommandConflict(RuntimeError):
    """A command no longer matches the authoritative operation ledger."""


class OperationProjectionConflict(RuntimeError):
    """A Project operation mirror conflicts with immutable ledger identity."""


@dataclass(frozen=True)
class OperationCommand:
    command_id: str
    command_type: CommandType
    project_id: str
    operation_id: str
    kind: str
    status: str
    cancel_requested: bool
    parameters_fingerprint: str
    workflow_version: str
    operation_created_at: str
    operation_completed_at: str | None
    expected_project_revision: int
    created_at: str
    source_operation_id: str | None = None
    payload_version: str = _COMMAND_PAYLOAD_VERSION


@dataclass(frozen=True)
class OperationLedgerEntry:
    operation_id: str
    project_id: str
    kind: str
    status: str
    cancel_requested: bool
    command_revision: int
    state_revision: int
    parameters_fingerprint: str
    workflow_version: str
    origin: CommandOrigin
    created_at: str
    updated_at: str
    completed_at: str | None
    last_command_id: str | None


@dataclass(frozen=True)
class OperationOutboxEvent:
    event_id: str
    command_id: str
    project_id: str
    operation_id: str
    source_operation_id: str | None
    command_type: CommandType
    command_revision: int
    payload_version: str
    status: OutboxStatus
    created_at: str
    applied_at: str | None


@dataclass(frozen=True)
class OperationLedgerInventory:
    entries: tuple[OperationLedgerEntry, ...]
    total_count: int
    truncated: bool


def command_from_operation(
    command_type: CommandType,
    operation: dict[str, Any],
    *,
    expected_project_revision: int,
    source_operation_id: str | None = None,
    command_id: str | None = None,
    created_at: str | None = None,
) -> OperationCommand:
    identity = _operation_identity(operation)
    if expected_project_revision < 0:
        raise ValueError("expected project revision must not be negative")
    if command_type == "retry" and not source_operation_id:
        raise ValueError("retry command requires a source operation")
    if command_type != "retry" and source_operation_id is not None:
        raise ValueError(
            "source operation is only valid for retry commands"
        )
    return OperationCommand(
        command_id=command_id or uuid.uuid4().hex,
        command_type=command_type,
        project_id=identity["project_id"],
        operation_id=identity["operation_id"],
        kind=identity["kind"],
        status=identity["status"],
        cancel_requested=identity["cancel_requested"],
        parameters_fingerprint=identity["parameters_fingerprint"],
        workflow_version=identity["workflow_version"],
        operation_created_at=identity["created_at"],
        operation_completed_at=identity["completed_at"],
        expected_project_revision=expected_project_revision,
        source_operation_id=source_operation_id,
        created_at=created_at or _now_iso(),
    )


def commit_command(
    connection: Connection,
    command: OperationCommand,
) -> OperationOutboxEvent:
    current = _read_entry(
        connection,
        command.project_id,
        command.operation_id,
    )
    if command.command_type in {"submit", "retry"}:
        if current is not None:
            raise OperationCommandConflict(
                "operation command already exists"
            )
        if command.command_type == "retry":
            _require_retry_source(connection, command)
        _require_no_other_active_kind(connection, command)
        command_revision = 1
        try:
            connection.execute(
                """
                INSERT INTO video_localization_operations (
                    operation_id,
                    project_id,
                    kind,
                    status,
                    cancel_requested,
                    command_revision,
                    state_revision,
                    parameters_fingerprint,
                    workflow_version,
                    origin,
                    created_at,
                    updated_at,
                    completed_at,
                    last_command_id
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 'command',
                          ?, ?, ?, ?)
                """,
                (
                    command.operation_id,
                    command.project_id,
                    command.kind,
                    command.status,
                    int(command.cancel_requested),
                    command_revision,
                    command.parameters_fingerprint,
                    command.workflow_version,
                    command.operation_created_at,
                    command.created_at,
                    command.operation_completed_at,
                    command.command_id,
                ),
            )
        except IntegrityError as exc:
            raise OperationCommandConflict(
                "operation command conflicts with an active ledger entry"
            ) from exc
    else:
        if current is None:
            raise OperationCommandConflict(
                "cancelled operation is missing from ledger"
            )
        if (
            current.project_id != command.project_id
            or current.kind != command.kind
            or current.created_at != command.operation_created_at
        ):
            raise OperationCommandConflict(
                "cancel command identity conflicts with ledger"
            )
        if current.status not in _ACTIVE_STATUSES:
            raise OperationCommandConflict(
                "terminal operation cannot accept cancellation"
            )
        if command.status != "cancelled" or not command.cancel_requested:
            raise OperationCommandConflict(
                "cancel command must commit cancelled state"
            )
        if (
            current.parameters_fingerprint
            != command.parameters_fingerprint
        ):
            raise OperationCommandConflict(
                "cancel command parameters conflict with ledger"
            )
        if (
            current.workflow_version
            in _DETAIL_MANAGED_WORKFLOW_VERSIONS
            and current.workflow_version != command.workflow_version
        ):
            raise OperationCommandConflict(
                "cancel command workflow conflicts with ledger"
            )
        command_revision = current.command_revision + 1
        connection.execute(
            """
            UPDATE video_localization_operations
            SET
                status = ?,
                cancel_requested = ?,
                command_revision = ?,
                state_revision = state_revision + 1,
                workflow_version = ?,
                updated_at = ?,
                completed_at = ?,
                last_command_id = ?
            WHERE operation_id = ?
              AND project_id = ?
            """,
            (
                command.status,
                int(command.cancel_requested),
                command_revision,
                command.workflow_version,
                command.created_at,
                command.operation_completed_at,
                command.command_id,
                command.operation_id,
                command.project_id,
            ),
        )

    event = OperationOutboxEvent(
        event_id=uuid.uuid4().hex,
        command_id=command.command_id,
        project_id=command.project_id,
        operation_id=command.operation_id,
        source_operation_id=command.source_operation_id,
        command_type=command.command_type,
        command_revision=command_revision,
        payload_version=command.payload_version,
        status="pending",
        created_at=command.created_at,
        applied_at=None,
    )
    connection.execute(
        """
        INSERT INTO video_localization_operation_outbox (
            event_id,
            command_id,
            project_id,
            operation_id,
            source_operation_id,
            command_type,
            command_revision,
            payload_version,
            status,
            created_at,
            applied_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
        """,
        (
            event.event_id,
            event.command_id,
            event.project_id,
            event.operation_id,
            event.source_operation_id,
            event.command_type,
            event.command_revision,
            event.payload_version,
            event.created_at,
        ),
    )
    return event


def sync_project_operations(
    connection: Connection,
    project_id: str,
    operations: list[dict[str, Any]],
    *,
    updated_at: str,
) -> None:
    incoming_operation_ids: set[str] = set()
    for operation in operations:
        identity = _operation_identity(operation)
        incoming_operation_ids.add(identity["operation_id"])
        if identity["project_id"] != project_id:
            raise OperationProjectionConflict(
                "Project operation belongs to another project"
            )
        current = _read_entry(
            connection,
            project_id,
            identity["operation_id"],
        )
        if current is None:
            _insert_legacy_operation(
                connection,
                project_id,
                identity,
                updated_at=updated_at,
            )
            continue
        if (
            current.project_id != project_id
            or current.kind != identity["kind"]
            or current.created_at != identity["created_at"]
        ):
            raise OperationProjectionConflict(
                "Project operation identity conflicts with ledger"
            )
        if (
            current.command_revision > 0
            and current.parameters_fingerprint
            != identity["parameters_fingerprint"]
        ):
            raise OperationProjectionConflict(
                "command-owned operation parameters cannot be replaced"
            )
        if (
            current.command_revision > 0
            and current.workflow_version
            in _DETAIL_MANAGED_WORKFLOW_VERSIONS
            and current.workflow_version
            != identity["workflow_version"]
        ):
            raise OperationProjectionConflict(
                "command-owned operation workflow cannot be replaced"
            )
        next_parameters_fingerprint = (
            current.parameters_fingerprint
            if current.command_revision > 0
            else identity["parameters_fingerprint"]
        )
        next_workflow_version = _compatible_workflow_version(
            current.workflow_version,
            identity["workflow_version"],
        )
        if (
            current.status == identity["status"]
            and current.cancel_requested
            == identity["cancel_requested"]
            and current.parameters_fingerprint
            == next_parameters_fingerprint
            and current.workflow_version
            == next_workflow_version
            and current.completed_at == identity["completed_at"]
        ):
            continue
        connection.execute(
            """
            UPDATE video_localization_operations
            SET
                status = ?,
                cancel_requested = ?,
                state_revision = state_revision + 1,
                parameters_fingerprint = ?,
                workflow_version = ?,
                updated_at = ?,
                completed_at = ?
            WHERE operation_id = ?
              AND project_id = ?
            """,
            (
                identity["status"],
                int(identity["cancel_requested"]),
                next_parameters_fingerprint,
                next_workflow_version,
                updated_at,
                identity["completed_at"],
                identity["operation_id"],
                project_id,
            ),
        )
    existing_rows = connection.execute(
        """
        SELECT operation_id, origin
        FROM video_localization_operations
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchall()
    missing_command_ids = [
        str(row["operation_id"])
        for row in existing_rows
        if str(row["operation_id"]) not in incoming_operation_ids
        and str(row["origin"]) == "command"
    ]
    if missing_command_ids:
        raise OperationProjectionConflict(
            "command-owned operation history cannot be removed"
        )
    removable_legacy_ids = [
        str(row["operation_id"])
        for row in existing_rows
        if str(row["operation_id"]) not in incoming_operation_ids
        and str(row["origin"]) == "legacy_project"
    ]
    connection.executemany(
        """
        DELETE FROM video_localization_operations
        WHERE project_id = ?
          AND operation_id = ?
          AND origin = 'legacy_project'
        """,
        [
            (project_id, operation_id)
            for operation_id in removable_legacy_ids
        ],
    )


def backfill_project_operations(
    connection: Connection,
    project_id: str,
    operations: list[dict[str, Any]],
    *,
    updated_at: str,
) -> None:
    """Insert legacy ledger identities without changing existing revisions."""
    for operation in operations:
        identity = _operation_identity(operation)
        if identity["project_id"] != project_id:
            raise OperationProjectionConflict(
                "Project operation belongs to another project"
            )
        current = _read_entry(
            connection,
            project_id,
            identity["operation_id"],
        )
        if current is None:
            _insert_legacy_operation(
                connection,
                project_id,
                identity,
                updated_at=updated_at,
            )
        elif (
            current.project_id != project_id
            or current.kind != identity["kind"]
            or current.created_at != identity["created_at"]
        ):
            raise OperationProjectionConflict(
                "Project operation identity conflicts with ledger"
            )


def mark_outbox_applied(
    connection: Connection,
    event_id: str,
    *,
    applied_at: str,
) -> None:
    cursor = connection.execute(
        """
        UPDATE video_localization_operation_outbox
        SET status = 'applied', applied_at = ?
        WHERE event_id = ? AND status = 'pending'
        """,
        (applied_at, event_id),
    )
    if cursor.rowcount != 1:
        raise OperationCommandConflict(
            "operation outbox event is missing or already applied"
        )


def get_operation(
    project_id: str,
    operation_id: str,
) -> OperationLedgerEntry | None:
    with database.conn() as connection:
        return _read_entry(connection, project_id, operation_id)


def list_project_operations(
    project_id: str,
) -> list[OperationLedgerEntry]:
    with database.conn() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM video_localization_operations
            WHERE project_id = ?
            ORDER BY created_at, operation_id
            """,
            (project_id,),
        ).fetchall()
    return [_entry_from_row(row) for row in rows]


def list_recoverable_project_ids(
    active_statuses: Iterable[str],
) -> list[str]:
    statuses = sorted(set(active_statuses))
    if not statuses:
        return []
    placeholders = ", ".join("?" for _status in statuses)
    with database.conn() as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT project_id
            FROM video_localization_operations
            WHERE status IN ({placeholders})
            ORDER BY project_id
            """,
            tuple(statuses),
        ).fetchall()
    return [str(row["project_id"]) for row in rows]


def ledger_inventory(
    *,
    limit: int = 1_000,
) -> OperationLedgerInventory:
    if limit < 1:
        raise ValueError("ledger inventory limit must be positive")
    with database.conn() as connection:
        total_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM video_localization_operations
                """
            ).fetchone()[0]
        )
        rows = connection.execute(
            """
            SELECT *
            FROM video_localization_operations
            ORDER BY project_id, created_at, operation_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return OperationLedgerInventory(
        entries=tuple(_entry_from_row(row) for row in rows),
        total_count=total_count,
        truncated=total_count > len(rows),
    )


def pending_outbox_count() -> int:
    with database.conn() as connection:
        return int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM video_localization_operation_outbox
                WHERE status <> 'applied'
                """
            ).fetchone()[0]
        )


def list_outbox(
    project_id: str,
    operation_id: str,
) -> list[OperationOutboxEvent]:
    with database.conn() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM video_localization_operation_outbox
            WHERE project_id = ?
              AND operation_id = ?
            ORDER BY command_revision, created_at, event_id
            """,
            (project_id, operation_id),
        ).fetchall()
    return [_outbox_from_row(row) for row in rows]


def delete_project(
    connection: Connection,
    project_id: str,
) -> None:
    connection.execute(
        """
        DELETE FROM video_localization_operation_outbox
        WHERE project_id = ?
        """,
        (project_id,),
    )
    connection.execute(
        """
        DELETE FROM video_localization_operations
        WHERE project_id = ?
        """,
        (project_id,),
    )


def _require_retry_source(
    connection: Connection,
    command: OperationCommand,
) -> None:
    source = _read_entry(
        connection,
        command.project_id,
        str(command.source_operation_id or ""),
    )
    if (
        source is None
        or source.project_id != command.project_id
        or source.kind != command.kind
        or source.status not in _TERMINAL_STATUSES
    ):
        raise OperationCommandConflict(
            "retry source is missing, active, or incompatible"
        )


def _require_no_other_active_kind(
    connection: Connection,
    command: OperationCommand,
) -> None:
    if command.status not in _ACTIVE_STATUSES:
        return
    row = connection.execute(
        """
        SELECT operation_id
        FROM video_localization_operations
        WHERE project_id = ?
          AND kind = ?
          AND status IN ('queued', 'running')
        LIMIT 1
        """,
        (command.project_id, command.kind),
    ).fetchone()
    if row is not None:
        raise OperationCommandConflict(
            "operation command conflicts with an active ledger entry"
        )


def _insert_legacy_operation(
    connection: Connection,
    project_id: str,
    identity: dict[str, Any],
    *,
    updated_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO video_localization_operations (
            operation_id,
            project_id,
            kind,
            status,
            cancel_requested,
            command_revision,
            state_revision,
            parameters_fingerprint,
            workflow_version,
            origin,
            created_at,
            updated_at,
            completed_at,
            last_command_id
        ) VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?, 'legacy_project',
                  ?, ?, ?, NULL)
        """,
        (
            identity["operation_id"],
            project_id,
            identity["kind"],
            identity["status"],
            int(identity["cancel_requested"]),
            identity["parameters_fingerprint"],
            identity["workflow_version"],
            identity["created_at"],
            updated_at,
            identity["completed_at"],
        ),
    )


def _read_entry(
    connection: Connection,
    project_id: str,
    operation_id: str,
) -> OperationLedgerEntry | None:
    row = connection.execute(
        """
        SELECT *
        FROM video_localization_operations
        WHERE project_id = ?
          AND operation_id = ?
        """,
        (project_id, operation_id),
    ).fetchone()
    return _entry_from_row(row) if row is not None else None


def _entry_from_row(row) -> OperationLedgerEntry:
    return OperationLedgerEntry(
        operation_id=str(row["operation_id"]),
        project_id=str(row["project_id"]),
        kind=str(row["kind"]),
        status=str(row["status"]),
        cancel_requested=bool(row["cancel_requested"]),
        command_revision=int(row["command_revision"]),
        state_revision=int(row["state_revision"]),
        parameters_fingerprint=str(row["parameters_fingerprint"]),
        workflow_version=str(row["workflow_version"]),
        origin=str(row["origin"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=(
            str(row["completed_at"])
            if row["completed_at"] is not None
            else None
        ),
        last_command_id=(
            str(row["last_command_id"])
            if row["last_command_id"] is not None
            else None
        ),
    )


def _outbox_from_row(row) -> OperationOutboxEvent:
    return OperationOutboxEvent(
        event_id=str(row["event_id"]),
        command_id=str(row["command_id"]),
        project_id=str(row["project_id"]),
        operation_id=str(row["operation_id"]),
        source_operation_id=(
            str(row["source_operation_id"])
            if row["source_operation_id"] is not None
            else None
        ),
        command_type=str(row["command_type"]),
        command_revision=int(row["command_revision"]),
        payload_version=str(row["payload_version"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        applied_at=(
            str(row["applied_at"])
            if row["applied_at"] is not None
            else None
        ),
    )


def _operation_identity(
    operation: dict[str, Any],
) -> dict[str, Any]:
    operation_id = str(operation.get("operation_id") or "").strip()
    project_id = str(operation.get("project_id") or "").strip()
    kind = str(operation.get("kind") or "").strip()
    status = str(operation.get("status") or "").strip()
    created_at = str(operation.get("created_at") or "").strip()
    if not all(
        [operation_id, project_id, kind, status, created_at]
    ):
        raise ValueError(
            "operation ledger identity fields must not be empty"
        )
    parameters = operation.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("operation parameters must be an object")
    workflow_version = workflow_version_from_operation(operation)
    completed_at = operation.get("completed_at")
    return {
        "operation_id": operation_id,
        "project_id": project_id,
        "kind": kind,
        "status": status,
        "cancel_requested": bool(
            operation.get("cancel_requested")
        ),
        "parameters_fingerprint": _fingerprint(parameters),
        "workflow_version": workflow_version,
        "created_at": created_at,
        "completed_at": (
            str(completed_at) if completed_at is not None else None
        ),
    }


def _compatible_workflow_version(
    current: str,
    incoming: str,
) -> str:
    """Keep the legacy null sentinel until its explicit atomic migration."""

    if current == "None" and incoming == _DEFAULT_WORKFLOW_VERSION:
        return current
    return incoming


def workflow_version_from_operation(
    operation: dict[str, Any],
) -> str:
    """Resolve workflow identity without turning JSON null into text."""

    result_summary = operation.get("result_summary")
    raw_version = (
        result_summary.get("workflow_schema_version")
        if isinstance(result_summary, dict)
        else None
    )
    if raw_version is None:
        return _DEFAULT_WORKFLOW_VERSION
    if not isinstance(raw_version, str):
        raise ValueError(
            "operation workflow version must be a string"
        )
    normalized = raw_version.strip()
    if not normalized:
        return _DEFAULT_WORKFLOW_VERSION
    if normalized == "None":
        raise ValueError(
            "operation workflow version must not be the legacy null sentinel"
        )
    return normalized


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parameters_fingerprint(payload: dict[str, Any]) -> str:
    return _fingerprint(payload)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="microseconds"
    )


__all__ = [
    "CommandType",
    "OperationCommand",
    "OperationCommandConflict",
    "OperationLedgerEntry",
    "OperationLedgerInventory",
    "OperationOutboxEvent",
    "OperationProjectionConflict",
    "backfill_project_operations",
    "command_from_operation",
    "commit_command",
    "delete_project",
    "get_operation",
    "ledger_inventory",
    "list_outbox",
    "list_project_operations",
    "list_recoverable_project_ids",
    "mark_outbox_applied",
    "parameters_fingerprint",
    "pending_outbox_count",
    "sync_project_operations",
    "workflow_version_from_operation",
]
