from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any, Sequence

from app.errors import ProjectRevisionConflict
from app.schemas.video_localization_operation_summary import (
    OperationSummaryCoreV1,
)
from app.schemas.video_localization_operation_detail import (
    OperationDetailCoreV1,
)
from app.schemas.video_localization_tts_handoff import TtsHandoffClaim
from app.schemas.voice_studio import VideoLocalizationTtsTask
from app.services import database
from app.services import video_localization_operation_artifact_store
from app.services import video_localization_operation_detail_core_store
from app.services import video_localization_operation_ledger_store
from app.services import (
    video_localization_operation_step_adjudication_store,
)
from app.services import video_localization_operation_step_store
from app.services import video_localization_operation_summary_store
from app.services import video_localization_project_snapshot_store
from app.services import video_localization_project_cleanup_store
from app.services import video_localization_tts_handoff_store
from app.services import video_localization_tts_workflow_store
from app.services import video_localization_workspace_projection_store
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    require_active_execution_fence,
)


_OPERATIONS_PATH = "$.parameters.video_localization.operations"
_SOURCE_MEDIA_PATH = "$.parameters.video_localization.source_media"
_STEMS_PATH = "$.parameters.video_localization.stems"
_backfilled_paths: set[str] = set()
_backfill_lock = threading.Lock()


class OperationProjectRevisionConflict(ProjectRevisionConflict):
    """A command was built from an older Project operation projection."""


class ProjectLifecycleActiveConflict(ProjectRevisionConflict):
    """Reset/delete lost a race with newly active project work."""


@dataclass(frozen=True)
class ProjectOperationSlice:
    """Read-only projection from the Project compatibility mirror."""

    project_exists: bool
    projection_revision: int = 0
    changed: bool = True
    operations: tuple[dict[str, Any], ...] = ()
    source_media: dict[str, Any] | None = None
    stems: dict[str, Any] | None = None


def save_project_with_projection(
    project_id: str,
    project_payload: dict[str, Any],
    *,
    updated_at: str,
    expected_repository_revision: int | None = None,
    operation_updated_at: str | None = None,
    execution_fence: ExecutionFence | None = None,
    tts_handoff_claim: TtsHandoffClaim | None = None,
    observed_at_ms: int | None = None,
    operation_command: (
        video_localization_operation_ledger_store.OperationCommand | None
    ) = None,
    operation_summary_cores: (
        Sequence[OperationSummaryCoreV1] | None
    ) = None,
    operation_detail_core: OperationDetailCoreV1 | None = None,
    tts_workflows: Sequence[VideoLocalizationTtsTask] | None = None,
    snapshot_create_autosave: bool = False,
) -> int:
    projection_updated_at = (
        operation_updated_at
        or _draft_updated_at(project_payload)
        or updated_at
    )
    operation_payloads = _operation_payloads(project_payload)
    if operation_command is not None:
        _require_command_operation(
            operation_command,
            operation_payloads,
        )
    with database.conn() as connection:
        if (
            expected_repository_revision is not None
            or execution_fence is not None
            or tts_handoff_claim is not None
            or operation_command is not None
            or operation_summary_cores is not None
            or operation_detail_core is not None
            or tts_workflows is not None
        ):
            connection.execute("BEGIN IMMEDIATE")
        current_revision = _project_revision(
            connection,
            project_id,
        )
        current_repository_revision = _repository_revision(
            connection,
            project_id,
        )
        if (
            operation_command is not None
            and current_revision
            != operation_command.expected_project_revision
        ):
            raise OperationProjectRevisionConflict(
                "Project operation projection changed before command commit"
            )
        if (
            expected_repository_revision is not None
            and current_repository_revision
            != expected_repository_revision
        ):
            raise ProjectRevisionConflict(
                "Project changed before repository commit"
            )
        if execution_fence is not None:
            require_active_execution_fence(
                connection,
                execution_fence,
                target_project_id=project_id,
                observed_at_ms=(
                    time.time_ns() // 1_000_000
                    if observed_at_ms is None
                    else observed_at_ms
                ),
            )
        if tts_handoff_claim is not None:
            video_localization_tts_handoff_store.require_active_claim(
                connection,
                tts_handoff_claim,
                target_project_id=project_id,
                observed_at_ms=(
                    time.time_ns() // 1_000_000
                    if observed_at_ms is None
                    else observed_at_ms
                ),
            )
        if operation_summary_cores is not None:
            (
                video_localization_operation_summary_store
                .validate_project_projection(
                    connection,
                    project_id,
                )
            )
        outbox_event = (
            video_localization_operation_ledger_store.commit_command(
                connection,
                operation_command,
            )
            if operation_command is not None
            else None
        )
        if operation_detail_core is not None:
            (
                video_localization_operation_detail_core_store
                .put_detail_core_from_connection(
                    connection,
                    operation_detail_core,
                    written_at=projection_updated_at,
                )
            )
        connection.execute(
            """
            INSERT INTO projects (
                project_id,
                data,
                updated_at,
                repository_revision
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                data = excluded.data,
                updated_at = excluded.updated_at,
                repository_revision = excluded.repository_revision
            """,
            (
                project_id,
                json.dumps(project_payload, ensure_ascii=False),
                updated_at,
                current_repository_revision + 1,
            ),
        )
        video_localization_workspace_projection_store.sync_project_from_connection(
            connection,
            project_id,
            project_payload,
            repository_revision=current_repository_revision + 1,
            projected_at=projection_updated_at,
        )
        if _video_localization_payload(project_payload) is not None:
            video_localization_project_snapshot_store.enqueue(
                connection,
                project_id=project_id,
                target_repository_revision=(
                    current_repository_revision + 1
                ),
                create_autosave=snapshot_create_autosave,
                requested_at=projection_updated_at,
            )
        _write_project_revision(
            connection,
            project_id,
            projection_revision=current_revision + 1,
        )
        if operation_payloads is not None:
            video_localization_operation_ledger_store.sync_project_operations(
                connection,
                project_id,
                operation_payloads,
                updated_at=projection_updated_at,
            )
        if operation_summary_cores is not None:
            (
                video_localization_operation_summary_store
                .sync_project_summaries(
                    connection,
                    project_id,
                    operation_summary_cores,
                    projected_at=projection_updated_at,
                )
            )
        if tts_workflows is not None:
            video_localization_tts_workflow_store.sync_project_from_connection(
                connection,
                project_id,
                tts_workflows,
                projected_at=projection_updated_at,
            )
        if outbox_event is not None:
            video_localization_operation_ledger_store.mark_outbox_applied(
                connection,
                outbox_event.event_id,
                applied_at=projection_updated_at,
            )
        return current_repository_revision + 1


def delete_project_with_projection(project_id: str) -> None:
    with database.conn() as connection:
        _delete_project_with_projection_from_connection(
            connection,
            project_id,
        )


def reset_project_with_projection(
    project_id: str,
    project_payload: dict[str, Any],
    *,
    updated_at: str,
    expected_repository_revision: int,
    operation_updated_at: str,
    cleanup_job: (
        video_localization_project_cleanup_store.ProjectCleanupJob
    ),
) -> int:
    """Atomically replace Draft state and retire all operation projections."""

    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current_repository_revision = _repository_revision(
            connection,
            project_id,
        )
        if current_repository_revision != expected_repository_revision:
            raise ProjectRevisionConflict(
                "Project changed before reset commit"
            )
        _require_no_active_project_work(connection, project_id)
        next_repository_revision = current_repository_revision + 1
        connection.execute(
            """
            UPDATE projects
            SET
                data = ?,
                updated_at = ?,
                repository_revision = ?
            WHERE project_id = ?
            """,
            (
                json.dumps(project_payload, ensure_ascii=False),
                updated_at,
                next_repository_revision,
                project_id,
            ),
        )
        video_localization_workspace_projection_store.sync_project_from_connection(
            connection,
            project_id,
            project_payload,
            repository_revision=next_repository_revision,
            projected_at=operation_updated_at,
        )
        _delete_operation_records(connection, project_id)
        video_localization_tts_workflow_store.delete_project_from_connection(
            connection,
            project_id,
        )
        video_localization_tts_handoff_store.abandon_project(
            connection,
            project_id,
            abandoned_at=operation_updated_at,
            reason="video-localization project was reset",
        )
        video_localization_project_snapshot_store.enqueue(
            connection,
            project_id=project_id,
            target_repository_revision=next_repository_revision,
            create_autosave=True,
            requested_at=operation_updated_at,
        )
        video_localization_project_cleanup_store.enqueue(
            connection,
            cleanup_job,
            requested_at=operation_updated_at,
        )
        return next_repository_revision


def delete_project_with_cleanup(
    project_id: str,
    *,
    expected_repository_revision: int,
    cleanup_job: (
        video_localization_project_cleanup_store.ProjectCleanupJob
    ),
    requested_at: str,
) -> None:
    """Commit the delete tombstone and all project-owned DB rows together."""

    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current_repository_revision = _repository_revision(
            connection,
            project_id,
        )
        if current_repository_revision != expected_repository_revision:
            raise ProjectRevisionConflict(
                "Project changed before delete commit"
            )
        _require_no_active_project_work(connection, project_id)
        video_localization_project_cleanup_store.enqueue(
            connection,
            cleanup_job,
            requested_at=requested_at,
        )
        _delete_project_with_projection_from_connection(
            connection,
            project_id,
        )
        connection.execute(
            """
            DELETE FROM tasks
            WHERE json_extract(data, '$.project_id') = ?
            """,
            (project_id,),
        )
        connection.execute(
            """
            DELETE FROM history
            WHERE json_extract(data, '$.project_id') = ?
            """,
            (project_id,),
        )
        connection.execute(
            """
            DELETE FROM batches
            WHERE json_extract(data, '$.parameters.project_id') = ?
            """,
            (project_id,),
        )


def _delete_project_with_projection_from_connection(
    connection: Connection,
    project_id: str,
) -> None:
    connection.execute(
        "DELETE FROM projects WHERE project_id = ?",
        (project_id,),
    )
    video_localization_project_snapshot_store.delete(
        connection,
        project_id,
    )
    video_localization_tts_handoff_store.delete_project(
        connection,
        project_id,
    )
    video_localization_tts_workflow_store.delete_project_from_connection(
        connection,
        project_id,
    )
    video_localization_workspace_projection_store.delete_project_from_connection(
        connection,
        project_id,
    )
    _delete_operation_records(connection, project_id)


def _delete_operation_records(
    connection: Connection,
    project_id: str,
) -> None:
    video_localization_operation_artifact_store.delete_project(
        connection,
        project_id,
    )
    (
        video_localization_operation_step_adjudication_store
        .delete_project(
            connection,
            project_id,
        )
    )
    video_localization_operation_step_store.delete_project(
        connection,
        project_id,
    )
    video_localization_operation_detail_core_store.delete_project(
        connection,
        project_id,
    )
    video_localization_operation_ledger_store.delete_project(
        connection,
        project_id,
    )
    video_localization_operation_summary_store.delete_project(
        connection,
        project_id,
    )
    connection.execute(
        """
        DELETE FROM video_localization_operation_projection_state
        WHERE project_id = ?
        """,
        (project_id,),
    )
    connection.execute(
        """
        DELETE FROM video_localization_operation_attempts
        WHERE project_id = ?
        """,
        (project_id,),
    )


def _require_no_active_project_work(
    connection: Connection,
    project_id: str,
) -> None:
    checks = (
        (
            """
            SELECT 1
            FROM video_localization_operations
            WHERE project_id = ?
              AND status IN ('queued', 'running')
            LIMIT 1
            """,
            (project_id,),
        ),
        (
            """
            SELECT 1
            FROM tasks
            WHERE json_extract(data, '$.project_id') = ?
              AND status NOT IN ('success', 'failed', 'cancelled')
            LIMIT 1
            """,
            (project_id,),
        ),
        (
            """
            SELECT 1
            FROM batches
            WHERE json_extract(data, '$.parameters.project_id') = ?
              AND status NOT IN ('success', 'failed', 'cancelled')
            LIMIT 1
            """,
            (project_id,),
        ),
    )
    if any(
        connection.execute(query, values).fetchone() is not None
        for query, values in checks
    ):
        raise ProjectLifecycleActiveConflict(
            "Project gained active work before lifecycle commit"
        )


def read_project_mirror_operations(
    project_id: str,
    *,
    include_media: bool = False,
) -> ProjectOperationSlice:
    with database.conn() as connection:
        return read_project_mirror_operations_from_connection(
            connection,
            project_id,
            include_media=include_media,
        )


def read_project_mirror_operations_from_connection(
    connection: Connection,
    project_id: str,
    *,
    include_media: bool = False,
) -> ProjectOperationSlice:
    project = connection.execute(
        """
        SELECT
            json_extract(data, ?) AS source_media,
            json_extract(data, ?) AS stems
        FROM projects
        WHERE project_id = ?
        """,
        (_SOURCE_MEDIA_PATH, _STEMS_PATH, project_id),
    ).fetchone()
    if project is None:
        return ProjectOperationSlice(project_exists=False)
    rows = connection.execute(
        """
        SELECT operation.value AS operation_data
        FROM projects
        JOIN json_each(projects.data, ?) AS operation
        WHERE projects.project_id = ?
        """,
        (_OPERATIONS_PATH, project_id),
    ).fetchall()
    return ProjectOperationSlice(
        project_exists=True,
        operations=tuple(
            _json_object(row["operation_data"]) for row in rows
        ),
        source_media=(
            _optional_json_object(project["source_media"])
            if include_media
            else None
        ),
        stems=(
            _optional_json_object(project["stems"])
            if include_media
            else None
        ),
    )


def read_project_operations(
    project_id: str,
    *,
    include_media: bool = False,
    after_revision: int | None = None,
) -> ProjectOperationSlice:
    """Read the Project mirror with ledger state, or short-circuit unchanged.

    ``after_revision`` is only a transport optimization. A changed read still
    comes from the same Project/ledger transaction as the full reader.
    """
    if after_revision is not None and after_revision < 0:
        raise ValueError("after revision must not be negative")
    with database.conn() as connection:
        connection.execute("BEGIN")
        projection_revision = _operation_feed_revision(
            connection,
            project_id,
        )
        if projection_revision is None:
            return ProjectOperationSlice(project_exists=False)
        if (
            after_revision is not None
            and after_revision == projection_revision
        ):
            return ProjectOperationSlice(
                project_exists=True,
                projection_revision=projection_revision,
                changed=False,
            )
        media = connection.execute(
            """
            SELECT
                json_extract(data, ?) AS source_media,
                json_extract(data, ?) AS stems
            FROM projects
            WHERE project_id = ?
            """,
            (_SOURCE_MEDIA_PATH, _STEMS_PATH, project_id),
        ).fetchone()
        assert media is not None
        mirror_rows = connection.execute(
            """
            SELECT operation.value AS operation_data
            FROM projects
            JOIN json_each(projects.data, ?) AS operation
            WHERE projects.project_id = ?
            """,
            (_OPERATIONS_PATH, project_id),
        ).fetchall()
        ledger_rows = connection.execute(
            """
            SELECT
                operation_id,
                project_id,
                kind,
                status,
                cancel_requested,
                created_at,
                completed_at
            FROM video_localization_operations
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchall()
    ledger_by_id = {
        str(row["operation_id"]): row
        for row in ledger_rows
    }
    return ProjectOperationSlice(
        project_exists=True,
        projection_revision=projection_revision,
        operations=_overlay_ledger_rows(
            mirror_rows,
            ledger_by_id,
        ),
        source_media=(
            _optional_json_object(media["source_media"])
            if include_media
            else None
        ),
        stems=(
            _optional_json_object(media["stems"])
            if include_media
            else None
        ),
    )


def read_project_mirror_operation(
    project_id: str,
    operation_id: str,
) -> tuple[bool, dict[str, Any] | None]:
    with database.conn() as connection:
        return read_project_mirror_operation_from_connection(
            connection,
            project_id,
            operation_id,
        )


def read_project_mirror_operation_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Read one compatibility record inside the caller's snapshot."""

    project = connection.execute(
        "SELECT 1 FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if project is None:
        return False, None
    row = _read_operation_row(
        connection,
        project_id,
        operation_id,
    )
    return True, _json_object(row["operation_data"]) if row else None


def read_operation(
    project_id: str,
    operation_id: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Read one full mirror record with ledger-owned state overlaid."""
    with database.conn() as connection:
        connection.execute("BEGIN")
        project = connection.execute(
            "SELECT 1 FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if project is None:
            return False, None
        mirror_row = _read_operation_row(
            connection,
            project_id,
            operation_id,
        )
        if mirror_row is None:
            return True, None
        ledger_row = connection.execute(
            """
            SELECT
                operation_id,
                project_id,
                kind,
                status,
                cancel_requested,
                created_at,
                completed_at
            FROM video_localization_operations
            WHERE project_id = ?
              AND operation_id = ?
            """,
            (project_id, operation_id),
        ).fetchone()
    return True, _overlay_ledger_state(
        _json_object(mirror_row["operation_data"]),
        ledger_row,
    )


def backfill_legacy_projects() -> None:
    """Explicit startup migration for legacy operation ledger identities."""

    path_key = str(database.DB_PATH.resolve())
    if path_key in _backfilled_paths:
        return
    with _backfill_lock:
        if path_key in _backfilled_paths:
            return
        with database.conn() as connection:
            rows = connection.execute(
                """
                SELECT
                    projects.project_id,
                    projects.data,
                    projects.updated_at,
                    projects.repository_revision
                FROM projects
                """,
            ).fetchall()
            for row in rows:
                project_id = str(row["project_id"])
                payload = json.loads(row["data"])
                operation_payloads = _operation_payloads(payload)
                normalized_project_ids = (
                    operation_payloads is not None
                    and _canonicalize_operation_project_ids(
                        operation_payloads,
                        project_id,
                    )
                )
                if normalized_project_ids:
                    connection.execute(
                        """
                        UPDATE projects
                        SET
                            data = ?,
                            repository_revision = repository_revision + 1
                        WHERE project_id = ?
                        """,
                        (
                            json.dumps(
                                payload,
                                ensure_ascii=False,
                            ),
                            project_id,
                        ),
                    )
                (
                    video_localization_workspace_projection_store
                    .sync_project_from_connection(
                        connection,
                        project_id,
                        payload,
                        repository_revision=(
                            int(row["repository_revision"])
                            + (1 if normalized_project_ids else 0)
                        ),
                        projected_at=str(row["updated_at"]),
                    )
                )
                localization_payload = _video_localization_payload(payload)
                raw_tts_workflows = (
                    localization_payload.get("tts_tasks")
                    if localization_payload is not None
                    else None
                )
                if isinstance(raw_tts_workflows, list):
                    try:
                        tts_workflows = [
                            VideoLocalizationTtsTask.model_validate(item)
                            for item in raw_tts_workflows
                        ]
                    except (TypeError, ValueError):
                        # A malformed legacy subdocument must keep using the
                        # explicit repair/fallback path instead of becoming
                        # an authoritative empty projection.
                        tts_workflows = None
                    if tts_workflows is not None:
                        (
                            video_localization_tts_workflow_store
                            .sync_project_from_connection(
                                connection,
                                project_id,
                                tts_workflows,
                                projected_at=str(row["updated_at"]),
                            )
                        )
                if operation_payloads is None:
                    continue
                connection.execute(
                    """
                    INSERT OR IGNORE INTO
                        video_localization_operation_projection_state (
                            project_id,
                            projection_revision
                        )
                    VALUES (?, 0)
                    """,
                    (project_id,),
                )
                (
                    video_localization_operation_ledger_store
                    .backfill_project_operations(
                        connection,
                        project_id,
                        operation_payloads,
                        updated_at=str(row["updated_at"]),
                    )
                )
        _backfilled_paths.add(path_key)


def _write_project_revision(
    connection: Connection,
    project_id: str,
    *,
    projection_revision: int,
) -> None:
    connection.execute(
        """
        INSERT INTO video_localization_operation_projection_state (
            project_id,
            projection_revision
        )
        VALUES (?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            projection_revision = excluded.projection_revision
        """,
        (
            project_id,
            projection_revision,
        ),
    )


def _operation_payloads(
    project_payload: dict[str, Any],
) -> list[dict[str, Any]] | None:
    parameters = project_payload.get("parameters")
    if not isinstance(parameters, dict):
        return []
    localization = parameters.get("video_localization")
    if localization is None:
        return []
    if not isinstance(localization, dict):
        return None
    operations = localization.get("operations", [])
    if not isinstance(operations, list):
        return None
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            return None
        operation_id = str(operation.get("operation_id") or "").strip()
        if not operation_id or operation_id in seen:
            return None
        seen.add(operation_id)
        payloads.append(operation)
    return payloads


def _video_localization_payload(
    project_payload: dict[str, Any],
) -> dict[str, Any] | None:
    parameters = project_payload.get("parameters")
    if not isinstance(parameters, dict):
        return None
    localization = parameters.get("video_localization")
    return localization if isinstance(localization, dict) else None


def _draft_updated_at(
    project_payload: dict[str, Any],
) -> str | None:
    localization = _video_localization_payload(project_payload)
    if localization is None:
        return None
    value = str(localization.get("updated_at") or "").strip()
    return value or None


def _canonicalize_operation_project_ids(
    operations: list[dict[str, Any]],
    project_id: str,
) -> bool:
    """Repair legacy clone metadata to the containing Project identity."""
    changed = False
    for operation in operations:
        if str(operation.get("project_id") or "") == project_id:
            continue
        operation["project_id"] = project_id
        changed = True
    return changed


def project_revision(project_id: str) -> int:
    with database.conn() as connection:
        return _project_revision(connection, project_id)


def operation_feed_revision(project_id: str) -> int | None:
    """Read the conservative feed invalidation revision without Project JSON."""

    with database.conn() as connection:
        return _operation_feed_revision(connection, project_id)


def _operation_feed_revision(
    connection: Connection,
    project_id: str,
) -> int | None:
    row = connection.execute(
        """
        SELECT
            COALESCE(
                projection.projection_revision,
                0
            ) AS projection_revision
        FROM projects
        LEFT JOIN video_localization_operation_projection_state
            AS projection
          ON projection.project_id = projects.project_id
        WHERE projects.project_id = ?
        """,
        (project_id,),
    ).fetchone()
    return int(row["projection_revision"]) if row is not None else None


def _project_revision(
    connection: Connection,
    project_id: str,
) -> int:
    row = connection.execute(
        """
        SELECT projection_revision
        FROM video_localization_operation_projection_state
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    return int(row["projection_revision"]) if row is not None else 0


def _repository_revision(
    connection: Connection,
    project_id: str,
) -> int:
    row = connection.execute(
        """
        SELECT repository_revision
        FROM projects
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    return int(row["repository_revision"]) if row is not None else 0


def _require_command_operation(
    command: (
        video_localization_operation_ledger_store.OperationCommand
    ),
    operation_payloads: list[dict[str, Any]] | None,
) -> None:
    matching = [
        operation
        for operation in (operation_payloads or [])
        if str(operation.get("operation_id") or "")
        == command.operation_id
    ]
    if len(matching) != 1:
        raise ValueError(
            "command operation must exist exactly once in Project mirror"
        )
    operation = matching[0]
    if (
        str(operation.get("project_id") or "")
        != command.project_id
        or str(operation.get("kind") or "") != command.kind
        or str(operation.get("status") or "") != command.status
        or bool(operation.get("cancel_requested"))
        != command.cancel_requested
    ):
        raise ValueError(
            "command operation does not match Project mirror"
        )


def _read_operation_row(
    connection: Connection,
    project_id: str,
    operation_id: str,
):
    return connection.execute(
        """
        SELECT operation.value AS operation_data
        FROM projects
        JOIN json_each(projects.data, ?) AS operation
        WHERE projects.project_id = ?
          AND json_extract(operation.value, '$.operation_id') = ?
        LIMIT 1
        """,
        (_OPERATIONS_PATH, project_id, operation_id),
    ).fetchone()


def _overlay_ledger_state(
    operation: dict[str, Any],
    ledger_row,
) -> dict[str, Any]:
    if ledger_row is None:
        return operation
    return {
        **operation,
        "operation_id": str(ledger_row["operation_id"]),
        "project_id": str(ledger_row["project_id"]),
        "kind": str(ledger_row["kind"]),
        "status": str(ledger_row["status"]),
        "cancel_requested": bool(ledger_row["cancel_requested"]),
        "created_at": str(ledger_row["created_at"]),
        "completed_at": (
            str(ledger_row["completed_at"])
            if ledger_row["completed_at"] is not None
            else None
        ),
    }


def _overlay_ledger_rows(
    mirror_rows,
    ledger_by_id: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    operations: list[dict[str, Any]] = []
    for row in mirror_rows:
        operation = _json_object(row["operation_data"])
        operation_id = str(
            operation.get("operation_id") or ""
        )
        operations.append(
            _overlay_ledger_state(
                operation,
                ledger_by_id.get(operation_id),
            )
        )
    return tuple(operations)


def _json_object(value: object) -> dict[str, Any]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise ValueError("stored video-localization operation must be an object")
    return decoded


def _optional_json_object(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    return _json_object(value)
