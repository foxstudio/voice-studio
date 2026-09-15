from __future__ import annotations

import json
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any, Literal, cast

from app.services import database


CleanupAction = Literal["reset", "delete"]


@dataclass(frozen=True)
class ProjectCleanupJob:
    job_id: str
    project_id: str
    action: CleanupAction
    directory_name: str
    cleanup_payload: dict[str, Any]
    package_staged: bool


def enqueue(
    connection: Connection,
    job: ProjectCleanupJob,
    *,
    requested_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO video_localization_project_cleanup_jobs (
            job_id,
            project_id,
            action,
            directory_name,
            cleanup_payload,
            package_staged,
            requested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job.job_id,
            job.project_id,
            job.action,
            job.directory_name,
            json.dumps(
                job.cleanup_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            int(job.package_staged),
            requested_at,
        ),
    )


def load(job_id: str) -> ProjectCleanupJob | None:
    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM video_localization_project_cleanup_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
    return _job_from_row(row) if row is not None else None


def pending_job_ids(*, limit: int) -> list[str]:
    bounded_limit = max(1, min(int(limit), 1_000))
    with database.conn() as connection:
        rows = connection.execute(
            """
            SELECT job_id
            FROM video_localization_project_cleanup_jobs
            ORDER BY requested_at ASC, job_id ASC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
    return [str(row["job_id"]) for row in rows]


def pending_for_project(project_id: str) -> list[ProjectCleanupJob]:
    with database.conn() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM video_localization_project_cleanup_jobs
            WHERE project_id = ?
            ORDER BY requested_at ASC, job_id ASC
            """,
            (project_id,),
        ).fetchall()
    return [_job_from_row(row) for row in rows]


def has_pending_delete(project_id: str) -> bool:
    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM video_localization_project_cleanup_jobs
            WHERE project_id = ?
              AND action = 'delete'
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    return row is not None


def mark_package_staged(job_id: str) -> None:
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_project_cleanup_jobs
            SET package_staged = 1
            WHERE job_id = ?
            """,
            (job_id,),
        )


def record_failure(
    job_id: str,
    *,
    attempted_at: str,
    error: str,
) -> None:
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_project_cleanup_jobs
            SET
                attempt_count = attempt_count + 1,
                last_attempt_at = ?,
                last_error = ?
            WHERE job_id = ?
            """,
            (attempted_at, error[:2_000], job_id),
        )


def complete(job_id: str) -> bool:
    with database.conn() as connection:
        cursor = connection.execute(
            """
            DELETE FROM video_localization_project_cleanup_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        )
        return cursor.rowcount > 0


def _job_from_row(row) -> ProjectCleanupJob:
    action = str(row["action"])
    if action not in {"reset", "delete"}:
        raise ValueError("project cleanup action is invalid")
    payload = json.loads(str(row["cleanup_payload"]))
    return ProjectCleanupJob(
        job_id=str(row["job_id"]),
        project_id=str(row["project_id"]),
        action=cast(CleanupAction, action),
        directory_name=str(row["directory_name"]),
        cleanup_payload=payload if isinstance(payload, dict) else {},
        package_staged=bool(row["package_staged"]),
    )


__all__ = [
    "CleanupAction",
    "ProjectCleanupJob",
    "complete",
    "enqueue",
    "has_pending_delete",
    "load",
    "mark_package_staged",
    "pending_for_project",
    "pending_job_ids",
    "record_failure",
]
