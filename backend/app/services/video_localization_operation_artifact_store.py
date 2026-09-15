from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import Connection
from typing import Literal

from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
    ManagedArtifactStorageIntegrityError,
    ManagedArtifactStoragePathError,
)
from app.services import database
from app.services.video_localization_execution_fence import ExecutionFence
from app.services.video_localization_operation_step_store import (
    OperationStepAttempt,
    get_step_attempt_from_connection,
    require_step_execution_fence,
)


ARTIFACT_SCHEMA_VERSION = "operation-artifact-v1"
MAX_MANAGED_ARTIFACT_BYTES = 16 * 1024 * 1024
ArtifactStatus = Literal["staged", "committed"]
ArtifactStageOutcome = Literal["created", "reused"]
_SUPPORTED_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "text/plain",
        "application/octet-stream",
        "image/jpeg",
    }
)


class ArtifactIdentityConflict(RuntimeError):
    """One semantic artifact slot was reused with different content."""


class ArtifactIntegrityError(RuntimeError):
    """A committed artifact file differs from durable metadata."""


class ArtifactPathError(RuntimeError):
    """A persisted or generated artifact path is unsafe."""


class ArtifactSchemaError(RuntimeError):
    """A persisted artifact uses an unsupported metadata contract."""


@dataclass(frozen=True)
class ManagedOperationArtifact:
    artifact_id: str
    artifact_schema_version: str
    project_id: str
    operation_id: str
    step_attempt_id: str
    artifact_kind: str
    artifact_key: str
    payload_schema_version: str
    media_type: str
    storage_backend: str
    storage_key: str
    staging_key: str | None
    content_fingerprint: str
    size_bytes: int
    status: ArtifactStatus
    status_revision: int
    created_at: str
    committed_at: str | None = None


@dataclass(frozen=True)
class ArtifactStageDecision:
    outcome: ArtifactStageOutcome
    artifact: ManagedOperationArtifact


@dataclass(frozen=True)
class ManagedArtifactRead:
    artifact: ManagedOperationArtifact
    content: bytes


@dataclass(frozen=True)
class _ArtifactWriteAuthority:
    project_id: str
    operation_id: str
    step_attempt_id: str
    observed_at_ms: int
    execution_fence: ExecutionFence | None = None
    expected_status_revision: int | None = None


def stage_artifact(
    execution_fence: ExecutionFence,
    *,
    file_backend: ManagedArtifactFileBackend,
    step_attempt_id: str,
    artifact_kind: str,
    artifact_key: str,
    payload_schema_version: str,
    media_type: str,
    content: bytes,
    observed_at: datetime,
) -> ArtifactStageDecision:
    authority = _execution_authority(
        execution_fence,
        step_attempt_id=step_attempt_id,
        observed_at=observed_at,
    )
    return _stage_authorized_artifact(
        authority,
        file_backend=file_backend,
        artifact_kind=artifact_kind,
        artifact_key=artifact_key,
        payload_schema_version=payload_schema_version,
        media_type=media_type,
        content=content,
        observed_at=observed_at,
    )


def stage_result_unknown_artifact(
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    expected_status_revision: int,
    file_backend: ManagedArtifactFileBackend,
    artifact_kind: str,
    artifact_key: str,
    payload_schema_version: str,
    media_type: str,
    content: bytes,
    observed_at: datetime,
) -> ArtifactStageDecision:
    """Stage recovery evidence for one exact result_unknown revision."""

    authority = _result_unknown_authority(
        project_id,
        operation_id,
        step_attempt_id,
        expected_status_revision=expected_status_revision,
        observed_at=observed_at,
    )
    return _stage_authorized_artifact(
        authority,
        file_backend=file_backend,
        artifact_kind=artifact_kind,
        artifact_key=artifact_key,
        payload_schema_version=payload_schema_version,
        media_type=media_type,
        content=content,
        observed_at=observed_at,
    )


def _stage_authorized_artifact(
    authority: _ArtifactWriteAuthority,
    *,
    file_backend: ManagedArtifactFileBackend,
    artifact_kind: str,
    artifact_key: str,
    payload_schema_version: str,
    media_type: str,
    content: bytes,
    observed_at: datetime,
) -> ArtifactStageDecision:
    normalized = _validate_stage_input(
        artifact_kind=artifact_kind,
        artifact_key=artifact_key,
        payload_schema_version=payload_schema_version,
        media_type=media_type,
        content=content,
    )
    created_at = _utc_iso(observed_at)
    fingerprint = file_backend.content_fingerprint(content)
    with database.conn() as connection:
        step = _authorize_artifact_step(connection, authority)
        existing = _read_slot(
            connection,
            step,
            artifact_kind=normalized["artifact_kind"],
            artifact_key=normalized["artifact_key"],
        )
        if existing is not None:
            _require_storage_backend(existing, file_backend)
            _require_same_artifact(
                existing,
                normalized=normalized,
                fingerprint=fingerprint,
                size_bytes=len(content),
            )
            return ArtifactStageDecision(
                outcome="reused",
                artifact=existing,
            )
    artifact_id = uuid.uuid4().hex
    keys = file_backend.build_storage_keys(
        operation_id=authority.operation_id,
        step_attempt_id=authority.step_attempt_id,
        artifact_kind=normalized["artifact_kind"],
        artifact_key=normalized["artifact_key"],
        artifact_id=artifact_id,
        media_type=normalized["media_type"],
    )
    _write_staging(
        file_backend,
        authority.project_id,
        keys.staging_key,
        content,
    )
    keep_staging = False
    try:
        with database.conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            step = _authorize_artifact_step(
                connection,
                authority,
            )
            existing = _read_slot(
                connection,
                step,
                artifact_kind=normalized["artifact_kind"],
                artifact_key=normalized["artifact_key"],
            )
            if existing is not None:
                _require_storage_backend(existing, file_backend)
                _require_same_artifact(
                    existing,
                    normalized=normalized,
                    fingerprint=fingerprint,
                    size_bytes=len(content),
                )
                return ArtifactStageDecision(
                    outcome="reused",
                    artifact=existing,
                )
            try:
                connection.execute(
                    """
                    INSERT INTO video_localization_operation_artifacts (
                        artifact_id,
                        artifact_schema_version,
                        project_id,
                        operation_id,
                        step_attempt_id,
                        artifact_kind,
                        artifact_key,
                        payload_schema_version,
                        media_type,
                        storage_backend,
                        storage_key,
                        staging_key,
                        content_fingerprint,
                        size_bytes,
                        status,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              'staged', ?)
                    """,
                    (
                        artifact_id,
                        ARTIFACT_SCHEMA_VERSION,
                        authority.project_id,
                        authority.operation_id,
                        authority.step_attempt_id,
                        normalized["artifact_kind"],
                        normalized["artifact_key"],
                        normalized["payload_schema_version"],
                        normalized["media_type"],
                        file_backend.ARTIFACT_STORAGE_BACKEND,
                        keys.storage_key,
                        keys.staging_key,
                        fingerprint,
                        len(content),
                        created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ArtifactIdentityConflict(
                    "artifact semantic slot or storage key already exists"
                ) from exc
            created = _read_artifact(connection, artifact_id)
            assert created is not None
            keep_staging = True
        return ArtifactStageDecision(
            outcome="created",
            artifact=created,
        )
    finally:
        if not keep_staging:
            _remove_staging(
                file_backend,
                authority.project_id,
                keys.staging_key,
            )


def commit_artifact(
    artifact_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
    execution_fence: ExecutionFence,
    observed_at: datetime,
) -> ManagedOperationArtifact:
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = _read_artifact(connection, artifact_id)
        if current is None:
            raise ArtifactIdentityConflict(
                "managed artifact does not exist"
            )
        authority = _execution_authority(
            execution_fence,
            step_attempt_id=current.step_attempt_id,
            observed_at=observed_at,
        )
        return _commit_authorized_artifact_from_connection(
            connection,
            current,
            authority=authority,
            file_backend=file_backend,
            committed_at=_utc_iso(observed_at),
        )


def commit_result_unknown_artifact_from_connection(
    connection: Connection,
    artifact_id: str,
    *,
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    expected_status_revision: int,
    file_backend: ManagedArtifactFileBackend,
    observed_at: datetime,
) -> ManagedOperationArtifact:
    """Commit recovery evidence inside a caller-owned write transaction."""

    if not connection.in_transaction:
        raise RuntimeError(
            "recovery artifact commit requires a caller-owned transaction"
        )
    connection.execute(
        """
        UPDATE video_localization_operation_artifacts
        SET status = status
        WHERE 0
        """
    )
    authority = _result_unknown_authority(
        project_id,
        operation_id,
        step_attempt_id,
        expected_status_revision=expected_status_revision,
        observed_at=observed_at,
    )
    current = _read_artifact(
        connection,
        _required_text(artifact_id, "artifact ID"),
    )
    if current is None:
        raise ArtifactIdentityConflict(
            "managed artifact does not exist"
        )
    return _commit_authorized_artifact_from_connection(
        connection,
        current,
        authority=authority,
        file_backend=file_backend,
        committed_at=_utc_iso(observed_at),
    )


def _commit_authorized_artifact_from_connection(
    connection: Connection,
    current: ManagedOperationArtifact,
    *,
    authority: _ArtifactWriteAuthority,
    file_backend: ManagedArtifactFileBackend,
    committed_at: str,
) -> ManagedOperationArtifact:
    _authorize_artifact_step(connection, authority)
    if (
        current.project_id != authority.project_id
        or current.operation_id != authority.operation_id
        or current.step_attempt_id != authority.step_attempt_id
    ):
        raise ArtifactIdentityConflict(
            "artifact write identity does not match"
        )
    _require_storage_backend(current, file_backend)
    if current.status == "committed":
        _read_file(current, file_backend)
        return current
    if current.staging_key is None:
        raise ArtifactIntegrityError(
            "staged artifact has no staging key"
        )
    _commit_file(current, file_backend)
    updated_row = connection.execute(
        """
        UPDATE video_localization_operation_artifacts
        SET
            status = 'committed',
            status_revision = status_revision + 1,
            staging_key = NULL,
            committed_at = ?
        WHERE artifact_id = ?
          AND status = 'staged'
          AND status_revision = ?
        """,
        (
            committed_at,
            current.artifact_id,
            current.status_revision,
        ),
    )
    if updated_row.rowcount != 1:
        raise ArtifactIdentityConflict(
            "artifact status changed during commit"
        )
    updated = _read_artifact(connection, current.artifact_id)
    assert updated is not None
    return updated


def read_artifact(
    artifact_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> ManagedArtifactRead:
    with database.conn() as connection:
        return read_artifact_from_connection(
            connection,
            artifact_id,
            file_backend=file_backend,
        )


def read_artifact_from_connection(
    connection: Connection,
    artifact_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> ManagedArtifactRead:
    """Verify one artifact using metadata from the caller's snapshot."""

    artifact = _read_artifact(
        connection,
        _required_text(artifact_id, "artifact ID"),
    )
    if artifact is None:
        raise ArtifactIdentityConflict(
            "managed artifact does not exist"
        )
    if artifact.status != "committed":
        raise ArtifactIntegrityError(
            "managed artifact has not been committed"
        )
    return ManagedArtifactRead(
        artifact=artifact,
        content=_read_file(artifact, file_backend),
    )


def get_artifact(
    artifact_id: str,
) -> ManagedOperationArtifact | None:
    with database.conn() as connection:
        artifact = _read_artifact(connection, artifact_id)
    return artifact


def get_step_artifact(
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    artifact_kind: str,
    artifact_key: str,
) -> ManagedOperationArtifact | None:
    with database.conn() as connection:
        return get_step_artifact_from_connection(
            connection,
            project_id,
            operation_id,
            step_attempt_id,
            artifact_kind=artifact_kind,
            artifact_key=artifact_key,
        )


def get_step_artifact_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    artifact_kind: str,
    artifact_key: str,
) -> ManagedOperationArtifact | None:
    identity = tuple(
        _required_text(value, label)
        for value, label in (
            (project_id, "project ID"),
            (operation_id, "operation ID"),
            (step_attempt_id, "step attempt ID"),
            (artifact_kind, "artifact kind"),
            (artifact_key, "artifact key"),
        )
    )
    row = connection.execute(
        """
        SELECT *
        FROM video_localization_operation_artifacts
        WHERE project_id = ?
          AND operation_id = ?
          AND step_attempt_id = ?
          AND artifact_kind = ?
          AND artifact_key = ?
        """,
        identity,
    ).fetchone()
    return _artifact_from_row(row) if row is not None else None


def delete_project(
    connection: Connection,
    project_id: str,
) -> None:
    connection.execute(
        """
        DELETE FROM video_localization_operation_artifacts
        WHERE project_id = ?
        """,
        (project_id,),
    )


def _execution_authority(
    execution_fence: ExecutionFence,
    *,
    step_attempt_id: str,
    observed_at: datetime,
) -> _ArtifactWriteAuthority:
    return _ArtifactWriteAuthority(
        project_id=execution_fence.project_id,
        operation_id=execution_fence.operation_id,
        step_attempt_id=_required_text(
            step_attempt_id,
            "step attempt ID",
        ),
        observed_at_ms=_epoch_milliseconds(observed_at),
        execution_fence=execution_fence,
    )


def _result_unknown_authority(
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    expected_status_revision: int,
    observed_at: datetime,
) -> _ArtifactWriteAuthority:
    if (
        not isinstance(expected_status_revision, int)
        or isinstance(expected_status_revision, bool)
        or expected_status_revision <= 0
    ):
        raise ValueError(
            "expected status revision must be a positive integer"
        )
    return _ArtifactWriteAuthority(
        project_id=_required_text(project_id, "project ID"),
        operation_id=_required_text(operation_id, "operation ID"),
        step_attempt_id=_required_text(
            step_attempt_id,
            "step attempt ID",
        ),
        observed_at_ms=_epoch_milliseconds(observed_at),
        expected_status_revision=expected_status_revision,
    )


def _authorize_artifact_step(
    connection: Connection,
    authority: _ArtifactWriteAuthority,
) -> OperationStepAttempt:
    if authority.execution_fence is not None:
        step = require_step_execution_fence(
            connection,
            authority.step_attempt_id,
            execution_fence=authority.execution_fence,
            observed_at_ms=authority.observed_at_ms,
        )
        _require_open_step(step)
        return step

    step = get_step_attempt_from_connection(
        connection,
        authority.step_attempt_id,
    )
    if step is None:
        raise ArtifactIdentityConflict(
            "result_unknown step attempt does not exist"
        )
    if (
        step.project_id != authority.project_id
        or step.operation_id != authority.operation_id
    ):
        raise ArtifactIdentityConflict(
            "result_unknown artifact identity does not match"
        )
    if (
        step.status != "result_unknown"
        or step.status_revision
        != authority.expected_status_revision
    ):
        raise ArtifactIdentityConflict(
            "result_unknown artifact revision is stale"
        )
    if step.cost_class == "local_free":
        raise ArtifactIdentityConflict(
            "result_unknown artifact requires an external step"
        )
    operation = connection.execute(
        """
        SELECT 1
        FROM video_localization_operations
        WHERE project_id = ?
          AND operation_id = ?
        """,
        (
            authority.project_id,
            authority.operation_id,
        ),
    ).fetchone()
    if operation is None:
        raise ArtifactIdentityConflict(
            "result_unknown operation ledger identity does not exist"
        )
    return step


def _require_open_step(step: OperationStepAttempt) -> None:
    if step.status not in {"prepared", "submitted"}:
        raise ArtifactIdentityConflict(
            "artifact write requires an active step"
        )


def _read_slot(
    connection: Connection,
    step: OperationStepAttempt,
    *,
    artifact_kind: str,
    artifact_key: str,
) -> ManagedOperationArtifact | None:
    row = connection.execute(
        """
        SELECT *
        FROM video_localization_operation_artifacts
        WHERE project_id = ?
          AND operation_id = ?
          AND step_attempt_id = ?
          AND artifact_kind = ?
          AND artifact_key = ?
        """,
        (
            step.project_id,
            step.operation_id,
            step.step_attempt_id,
            artifact_kind,
            artifact_key,
        ),
    ).fetchone()
    return _artifact_from_row(row) if row is not None else None


def _require_same_artifact(
    artifact: ManagedOperationArtifact,
    *,
    normalized: dict[str, str],
    fingerprint: str,
    size_bytes: int,
) -> None:
    if (
        artifact.payload_schema_version
        != normalized["payload_schema_version"]
        or artifact.media_type != normalized["media_type"]
        or artifact.content_fingerprint != fingerprint
        or artifact.size_bytes != size_bytes
    ):
        raise ArtifactIdentityConflict(
            "artifact semantic slot already contains different content"
        )


def _require_storage_backend(
    artifact: ManagedOperationArtifact,
    file_backend: ManagedArtifactFileBackend,
) -> None:
    if (
        artifact.storage_backend
        != file_backend.ARTIFACT_STORAGE_BACKEND
    ):
        raise ArtifactSchemaError(
            "persisted artifact storage backend is unsupported"
        )


def _read_artifact(
    connection: Connection,
    artifact_id: str,
) -> ManagedOperationArtifact | None:
    row = connection.execute(
        """
        SELECT *
        FROM video_localization_operation_artifacts
        WHERE artifact_id = ?
        """,
        (artifact_id,),
    ).fetchone()
    return _artifact_from_row(row) if row is not None else None


def _artifact_from_row(row) -> ManagedOperationArtifact:
    schema_version = str(row["artifact_schema_version"])
    if schema_version != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactSchemaError(
            "persisted artifact schema version is unsupported"
        )
    status = str(row["status"])
    staging_key = (
        str(row["staging_key"])
        if row["staging_key"] is not None
        else None
    )
    committed_at = (
        str(row["committed_at"])
        if row["committed_at"] is not None
        else None
    )
    fingerprint = str(row["content_fingerprint"])
    size_bytes = int(row["size_bytes"])
    status_revision = int(row["status_revision"])
    if status not in {"staged", "committed"}:
        raise ArtifactSchemaError(
            "persisted artifact status is unsupported"
        )
    if (
        size_bytes <= 0
        or size_bytes > MAX_MANAGED_ARTIFACT_BYTES
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
        or status_revision <= 0
    ):
        raise ArtifactSchemaError(
            "persisted artifact metadata is invalid"
        )
    if (
        status == "staged"
        and (staging_key is None or committed_at is not None)
    ) or (
        status == "committed"
        and (staging_key is not None or committed_at is None)
    ):
        raise ArtifactSchemaError(
            "persisted artifact lifecycle metadata is inconsistent"
        )
    return ManagedOperationArtifact(
        artifact_id=str(row["artifact_id"]),
        artifact_schema_version=schema_version,
        project_id=str(row["project_id"]),
        operation_id=str(row["operation_id"]),
        step_attempt_id=str(row["step_attempt_id"]),
        artifact_kind=str(row["artifact_kind"]),
        artifact_key=str(row["artifact_key"]),
        payload_schema_version=str(row["payload_schema_version"]),
        media_type=str(row["media_type"]),
        storage_backend=str(row["storage_backend"]),
        storage_key=str(row["storage_key"]),
        staging_key=staging_key,
        content_fingerprint=fingerprint,
        size_bytes=size_bytes,
        status=status,
        status_revision=status_revision,
        created_at=str(row["created_at"]),
        committed_at=committed_at,
    )


def _validate_stage_input(
    *,
    artifact_kind: str,
    artifact_key: str,
    payload_schema_version: str,
    media_type: str,
    content: bytes,
) -> dict[str, str]:
    if not isinstance(content, bytes):
        raise ValueError("managed artifact content must be bytes")
    if not content or len(content) > MAX_MANAGED_ARTIFACT_BYTES:
        raise ValueError(
            "managed artifact size must be between 1 byte and 16 MiB"
        )
    normalized_media = _required_text(media_type, "media type")
    if normalized_media not in _SUPPORTED_MEDIA_TYPES:
        raise ValueError("managed artifact media type is unsupported")
    schema_version = _required_text(
        payload_schema_version,
        "payload schema version",
    )
    if normalized_media == "application/json":
        try:
            decoded = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "managed JSON artifact must contain valid UTF-8 JSON"
            ) from exc
        if (
            not isinstance(decoded, dict)
            or decoded.get("schema_version") != schema_version
        ):
            raise ValueError(
                "managed JSON artifact schema_version does not match"
            )
    elif normalized_media == "text/plain":
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "managed text artifact must contain valid UTF-8"
            ) from exc
    elif normalized_media == "image/jpeg" and not (
        len(content) >= 4
        and content.startswith(b"\xff\xd8\xff")
        and content.endswith(b"\xff\xd9")
    ):
        raise ValueError(
            "managed JPEG artifact must contain a complete JPEG image"
        )
    return {
        "artifact_kind": _required_text(
            artifact_kind,
            "artifact kind",
        ),
        "artifact_key": _required_text(
            artifact_key,
            "artifact key",
        ),
        "payload_schema_version": schema_version,
        "media_type": normalized_media,
    }


def _write_staging(
    file_backend: ManagedArtifactFileBackend,
    project_id: str,
    staging_key: str,
    content: bytes,
) -> None:
    try:
        file_backend.write_staging_file(
            project_id,
            staging_key,
            content,
        )
    except ManagedArtifactStoragePathError as exc:
        raise ArtifactPathError(str(exc)) from exc
    except ManagedArtifactStorageIntegrityError as exc:
        raise ArtifactIntegrityError(str(exc)) from exc


def _commit_file(
    artifact: ManagedOperationArtifact,
    file_backend: ManagedArtifactFileBackend,
) -> None:
    assert artifact.staging_key is not None
    try:
        file_backend.commit_staging_file(
            artifact.project_id,
            staging_key=artifact.staging_key,
            storage_key=artifact.storage_key,
            expected_size=artifact.size_bytes,
            expected_fingerprint=artifact.content_fingerprint,
        )
    except ManagedArtifactStoragePathError as exc:
        raise ArtifactPathError(str(exc)) from exc
    except ManagedArtifactStorageIntegrityError as exc:
        raise ArtifactIntegrityError(str(exc)) from exc


def _read_file(
    artifact: ManagedOperationArtifact,
    file_backend: ManagedArtifactFileBackend,
) -> bytes:
    _require_storage_backend(artifact, file_backend)
    try:
        return file_backend.read_verified_file(
            artifact.project_id,
            artifact.storage_key,
            expected_size=artifact.size_bytes,
            expected_fingerprint=artifact.content_fingerprint,
        )
    except ManagedArtifactStoragePathError as exc:
        raise ArtifactPathError(str(exc)) from exc
    except ManagedArtifactStorageIntegrityError as exc:
        raise ArtifactIntegrityError(str(exc)) from exc


def _remove_staging(
    file_backend: ManagedArtifactFileBackend,
    project_id: str,
    staging_key: str,
) -> None:
    try:
        file_backend.remove_staging_file(
            project_id,
            staging_key,
        )
    except (
        ManagedArtifactStoragePathError,
        ManagedArtifactStorageIntegrityError,
    ):
        return


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) > 128:
        raise ValueError(f"{label} is too long")
    return normalized


def _epoch_milliseconds(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("artifact timestamp must include timezone")
    return int(value.timestamp() * 1_000)


def _utc_iso(value: datetime) -> str:
    _epoch_milliseconds(value)
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "ArtifactIdentityConflict",
    "ArtifactIntegrityError",
    "ArtifactPathError",
    "ArtifactSchemaError",
    "ArtifactStageDecision",
    "MAX_MANAGED_ARTIFACT_BYTES",
    "ManagedArtifactRead",
    "ManagedOperationArtifact",
    "commit_artifact",
    "commit_result_unknown_artifact_from_connection",
    "get_artifact",
    "get_step_artifact",
    "get_step_artifact_from_connection",
    "read_artifact",
    "read_artifact_from_connection",
    "stage_artifact",
    "stage_result_unknown_artifact",
]
