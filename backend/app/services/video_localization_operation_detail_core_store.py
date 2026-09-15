from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Literal

from pydantic import ValidationError

from app.schemas.video_localization_operation_detail import (
    OPERATION_DETAIL_CORE_SCHEMA_VERSION,
    OperationDetailCoreV1,
    operation_detail_core_fingerprint,
    operation_detail_core_json,
)
from app.services import database


DetailCoreWriteOutcome = Literal["created", "reused"]


class OperationDetailCoreConflict(RuntimeError):
    """An immutable operation detail identity was reused with new content."""


class OperationDetailCoreIdentityConflict(RuntimeError):
    """A detail core does not match its authoritative ledger identity."""


class OperationDetailCoreIntegrityError(RuntimeError):
    """Stored detail bytes do not match their durable fingerprint."""


class OperationDetailCoreSchemaError(RuntimeError):
    """Stored detail data uses an unsupported or invalid contract."""


@dataclass(frozen=True)
class OperationDetailCoreRecord:
    core: OperationDetailCoreV1
    content_fingerprint: str
    written_at: str


def put_detail_core(
    core: OperationDetailCoreV1,
    *,
    written_at: str,
) -> DetailCoreWriteOutcome:
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        return put_detail_core_from_connection(
            connection,
            core,
            written_at=written_at,
        )


def put_detail_core_from_connection(
    connection: Connection,
    core: OperationDetailCoreV1,
    *,
    written_at: str,
) -> DetailCoreWriteOutcome:
    normalized_written_at = _required_text(
        written_at,
        "detail core written_at",
    )
    validated_core = OperationDetailCoreV1.model_validate(core)
    _require_ledger_identity(connection, validated_core)
    existing = get_detail_core_from_connection(
        connection,
        validated_core.project_id,
        validated_core.operation_id,
    )
    if existing is not None:
        if existing.core != validated_core:
            raise OperationDetailCoreConflict(
                "operation detail core is immutable"
            )
        return "reused"

    core_json = operation_detail_core_json(validated_core)
    content_fingerprint = operation_detail_core_fingerprint(
        validated_core
    )
    connection.execute(
        """
        INSERT INTO video_localization_operation_detail_cores (
            project_id,
            operation_id,
            detail_schema_version,
            workflow_version,
            content_fingerprint,
            core_json,
            written_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            validated_core.project_id,
            validated_core.operation_id,
            validated_core.detail_schema_version,
            validated_core.workflow_version,
            content_fingerprint,
            core_json,
            normalized_written_at,
        ),
    )
    return "created"


def get_detail_core(
    project_id: str,
    operation_id: str,
) -> OperationDetailCoreRecord | None:
    with database.conn() as connection:
        return get_detail_core_from_connection(
            connection,
            project_id,
            operation_id,
        )


def get_detail_core_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
) -> OperationDetailCoreRecord | None:
    normalized_project_id = _required_text(
        project_id,
        "project ID",
    )
    normalized_operation_id = _required_text(
        operation_id,
        "operation ID",
    )
    row = connection.execute(
        """
        SELECT
            detail.project_id,
            detail.operation_id,
            detail.detail_schema_version,
            detail.workflow_version,
            detail.content_fingerprint,
            detail.core_json,
            detail.written_at,
            ledger.kind AS ledger_kind,
            ledger.workflow_version AS ledger_workflow_version
        FROM video_localization_operation_detail_cores AS detail
        LEFT JOIN video_localization_operations AS ledger
          ON ledger.project_id = detail.project_id
         AND ledger.operation_id = detail.operation_id
        WHERE detail.project_id = ?
          AND detail.operation_id = ?
        """,
        (normalized_project_id, normalized_operation_id),
    ).fetchone()
    if row is None:
        return None
    if row["ledger_kind"] is None:
        raise OperationDetailCoreIdentityConflict(
            "operation detail core has no ledger operation"
        )
    if (
        str(row["detail_schema_version"])
        != OPERATION_DETAIL_CORE_SCHEMA_VERSION
    ):
        raise OperationDetailCoreSchemaError(
            "operation detail core schema is not supported"
        )
    try:
        payload = json.loads(str(row["core_json"]))
        core = OperationDetailCoreV1.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise OperationDetailCoreSchemaError(
            "operation detail core payload is invalid"
        ) from exc
    _require_row_identity(row, core)
    canonical = operation_detail_core_json(core)
    expected_fingerprint = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    if (
        canonical != str(row["core_json"])
        or expected_fingerprint != str(row["content_fingerprint"])
    ):
        raise OperationDetailCoreIntegrityError(
            "operation detail core content fingerprint is inconsistent"
        )
    return OperationDetailCoreRecord(
        core=core,
        content_fingerprint=expected_fingerprint,
        written_at=str(row["written_at"]),
    )


def delete_project(
    connection: Connection,
    project_id: str,
) -> None:
    connection.execute(
        """
        DELETE FROM video_localization_operation_detail_cores
        WHERE project_id = ?
        """,
        (_required_text(project_id, "project ID"),),
    )


def _require_ledger_identity(
    connection: Connection,
    core: OperationDetailCoreV1,
) -> None:
    row = connection.execute(
        """
        SELECT kind, workflow_version
        FROM video_localization_operations
        WHERE project_id = ?
          AND operation_id = ?
        """,
        (core.project_id, core.operation_id),
    ).fetchone()
    if row is None:
        raise OperationDetailCoreIdentityConflict(
            "operation detail core requires an existing ledger operation"
        )
    if (
        str(row["kind"]) != core.kind
        or str(row["workflow_version"]) != core.workflow_version
    ):
        raise OperationDetailCoreIdentityConflict(
            "operation detail core differs from ledger identity"
        )


def _require_row_identity(
    row,
    core: OperationDetailCoreV1,
) -> None:
    if (
        str(row["project_id"]) != core.project_id
        or str(row["operation_id"]) != core.operation_id
        or str(row["workflow_version"]) != core.workflow_version
        or str(row["ledger_kind"]) != core.kind
        or str(row["ledger_workflow_version"])
        != core.workflow_version
    ):
        raise OperationDetailCoreIdentityConflict(
            "stored operation detail core differs from ledger identity"
        )


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) > 256:
        raise ValueError(f"{label} is too long")
    return normalized


__all__ = [
    "DetailCoreWriteOutcome",
    "OperationDetailCoreConflict",
    "OperationDetailCoreIdentityConflict",
    "OperationDetailCoreIntegrityError",
    "OperationDetailCoreRecord",
    "OperationDetailCoreSchemaError",
    "delete_project",
    "get_detail_core",
    "get_detail_core_from_connection",
    "put_detail_core",
    "put_detail_core_from_connection",
]
