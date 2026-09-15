from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from app.services import database


@dataclass(frozen=True)
class PendingProjectSnapshot:
    project_id: str
    target_repository_revision: int
    create_autosave: bool
    project_repository_revision: int
    project_data: str


def enqueue(
    connection: Connection,
    *,
    project_id: str,
    target_repository_revision: int,
    create_autosave: bool,
    requested_at: str,
) -> None:
    """Coalesce one durable snapshot projection request per Project."""

    connection.execute(
        """
        INSERT INTO video_localization_project_snapshot_projection (
            project_id,
            target_repository_revision,
            create_autosave,
            requested_at,
            attempt_count,
            last_attempt_at,
            last_error
        )
        VALUES (?, ?, ?, ?, 0, NULL, NULL)
        ON CONFLICT(project_id) DO UPDATE SET
            target_repository_revision = MAX(
                target_repository_revision,
                excluded.target_repository_revision
            ),
            create_autosave = MAX(
                create_autosave,
                excluded.create_autosave
            ),
            requested_at = excluded.requested_at,
            last_error = NULL
        """,
        (
            project_id,
            target_repository_revision,
            int(create_autosave),
            requested_at,
        ),
    )


def pending_project_ids(*, limit: int) -> list[str]:
    bounded_limit = max(1, min(int(limit), 1_000))
    with database.conn() as connection:
        rows = connection.execute(
            """
            SELECT project_id
            FROM video_localization_project_snapshot_projection
            ORDER BY requested_at ASC, project_id ASC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
    return [str(row["project_id"]) for row in rows]


def load(project_id: str) -> PendingProjectSnapshot | None:
    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT
                pending.project_id,
                pending.target_repository_revision,
                pending.create_autosave,
                projects.repository_revision AS project_repository_revision,
                projects.data AS project_data
            FROM video_localization_project_snapshot_projection AS pending
            JOIN projects
              ON projects.project_id = pending.project_id
            WHERE pending.project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                DELETE FROM video_localization_project_snapshot_projection
                WHERE project_id = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM projects
                      WHERE projects.project_id = ?
                  )
                """,
                (project_id, project_id),
            )
            return None
    return PendingProjectSnapshot(
        project_id=str(row["project_id"]),
        target_repository_revision=int(
            row["target_repository_revision"]
        ),
        create_autosave=bool(row["create_autosave"]),
        project_repository_revision=int(
            row["project_repository_revision"]
        ),
        project_data=str(row["project_data"]),
    )


def record_failure(
    project_id: str,
    *,
    target_repository_revision: int,
    attempted_at: str,
    error: str,
) -> None:
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_project_snapshot_projection
            SET
                attempt_count = attempt_count + 1,
                last_attempt_at = ?,
                last_error = ?
            WHERE project_id = ?
              AND target_repository_revision = ?
            """,
            (
                attempted_at,
                error[:2_000],
                project_id,
                target_repository_revision,
            ),
        )


def complete(
    project_id: str,
    *,
    target_repository_revision: int,
) -> bool:
    with database.conn() as connection:
        cursor = connection.execute(
            """
            DELETE FROM video_localization_project_snapshot_projection
            WHERE project_id = ?
              AND target_repository_revision <= ?
            """,
            (project_id, target_repository_revision),
        )
        return cursor.rowcount > 0


def delete(connection: Connection, project_id: str) -> None:
    connection.execute(
        """
        DELETE FROM video_localization_project_snapshot_projection
        WHERE project_id = ?
        """,
        (project_id,),
    )


__all__ = [
    "PendingProjectSnapshot",
    "complete",
    "delete",
    "enqueue",
    "load",
    "pending_project_ids",
    "record_failure",
]
