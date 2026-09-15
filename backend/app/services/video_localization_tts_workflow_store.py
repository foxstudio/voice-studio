from __future__ import annotations

import json
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Sequence

from app.schemas.voice_studio import TaskStatus, VideoLocalizationTtsTask
from app.services import database


@dataclass(frozen=True)
class TtsWorkflowProjection:
    authoritative: bool
    revision: int = 0
    tasks: tuple[VideoLocalizationTtsTask, ...] = ()


@dataclass(frozen=True)
class TtsGenerationRuntime:
    task_id: str
    status: TaskStatus
    progress: float = 0.0
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    result_id: str | None = None


@dataclass(frozen=True)
class TtsWorkflowFeedProjection:
    authoritative: bool
    revision: int = 0
    changed: bool = False
    workflow_ids: tuple[str, ...] = ()
    tasks: tuple[VideoLocalizationTtsTask, ...] = ()
    generation_runtimes: tuple[tuple[str, TtsGenerationRuntime], ...] = ()


def sync_project_from_connection(
    connection: Connection,
    project_id: str,
    tasks: Sequence[VideoLocalizationTtsTask],
    *,
    projected_at: str,
) -> int:
    payloads = [
        VideoLocalizationTtsTask.model_validate(task).model_dump(mode="json")
        for task in tasks
    ]
    encoded = {
        str(payload["workflow_id"]): json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        for payload in payloads
    }
    rows = connection.execute(
        """
        SELECT workflow_id, ordinal, generation_task_id, workflow_json,
               row_revision
        FROM video_localization_tts_workflows
        WHERE project_id = ?
        ORDER BY ordinal
        """,
        (project_id,),
    ).fetchall()
    current_by_id = {str(row["workflow_id"]): row for row in rows}
    current = [
        (str(row["workflow_id"]), int(row["ordinal"]), str(row["workflow_json"]))
        for row in rows
    ]
    desired = [
        (str(payload["workflow_id"]), ordinal, encoded[str(payload["workflow_id"])])
        for ordinal, payload in enumerate(payloads)
    ]
    state = connection.execute(
        """
        SELECT projection_revision
        FROM video_localization_tts_workflow_projection_state
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    current_revision = int(state["projection_revision"]) if state is not None else 0
    if state is not None and current == desired:
        return current_revision

    next_revision = current_revision + 1
    desired_ids = {str(payload["workflow_id"]) for payload in payloads}
    if desired_ids:
        placeholders = ", ".join("?" for _ in desired_ids)
        connection.execute(
            f"""
            DELETE FROM video_localization_tts_workflows
            WHERE project_id = ?
              AND workflow_id NOT IN ({placeholders})
            """,
            (project_id, *sorted(desired_ids)),
        )
    else:
        connection.execute(
            "DELETE FROM video_localization_tts_workflows WHERE project_id = ?",
            (project_id,),
        )
    for ordinal, payload in enumerate(payloads):
        workflow_id = str(payload["workflow_id"])
        existing = current_by_id.get(workflow_id)
        generation_task_id = payload.get("generation_task_id")
        if existing is None:
            connection.execute(
                """
                INSERT INTO video_localization_tts_workflows (
                    project_id, workflow_id, ordinal, generation_task_id,
                    segment_id, status, workflow_json, projected_at,
                    row_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    workflow_id,
                    ordinal,
                    generation_task_id,
                    str(payload.get("segment_id") or ""),
                    str(payload.get("status") or "prepared"),
                    encoded[workflow_id],
                    projected_at,
                    next_revision,
                ),
            )
            continue
        canonical_changed = (
            int(existing["ordinal"]) != ordinal
            or str(existing["workflow_json"]) != encoded[workflow_id]
        )
        if not canonical_changed:
            continue
        generation_changed = existing["generation_task_id"] != generation_task_id
        connection.execute(
            """
            UPDATE video_localization_tts_workflows
            SET ordinal = ?, generation_task_id = ?, segment_id = ?,
                status = ?, workflow_json = ?, projected_at = ?,
                row_revision = ?,
                generation_status = CASE WHEN ? THEN NULL ELSE generation_status END,
                generation_progress = CASE WHEN ? THEN NULL ELSE generation_progress END,
                generation_error_message = CASE WHEN ? THEN NULL ELSE generation_error_message END,
                generation_started_at = CASE WHEN ? THEN NULL ELSE generation_started_at END,
                generation_completed_at = CASE WHEN ? THEN NULL ELSE generation_completed_at END,
                generation_result_id = CASE WHEN ? THEN NULL ELSE generation_result_id END
            WHERE project_id = ? AND workflow_id = ?
            """,
            (
                ordinal,
                generation_task_id,
                str(payload.get("segment_id") or ""),
                str(payload.get("status") or "prepared"),
                encoded[workflow_id],
                projected_at,
                next_revision,
                *([generation_changed] * 6),
                project_id,
                workflow_id,
            ),
        )
    connection.execute(
        """
        INSERT INTO video_localization_tts_workflow_projection_state (
            project_id,
            projection_revision,
            projected_at
        ) VALUES (?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            projection_revision = excluded.projection_revision,
            projected_at = excluded.projected_at
        """,
        (project_id, next_revision, projected_at),
    )
    return next_revision


def sync_generation_task_from_connection(
    connection: Connection,
    *,
    task_id: str,
    status: str,
    progress: float,
    error_message: str | None,
    started_at: str | None,
    completed_at: str | None,
    result_id: str | None,
    projected_at: str,
) -> bool:
    """Advance only the workflow row owned by one generation task."""

    row = connection.execute(
        """
        SELECT project_id, workflow_id, generation_status,
               generation_progress, generation_error_message,
               generation_started_at, generation_completed_at,
               generation_result_id
        FROM video_localization_tts_workflows
        WHERE generation_task_id = ?
        """,
        (task_id,),
    ).fetchone()
    if row is None:
        return False
    desired = (
        status,
        float(progress),
        error_message,
        started_at,
        completed_at,
        result_id,
    )
    current = (
        row["generation_status"],
        row["generation_progress"],
        row["generation_error_message"],
        row["generation_started_at"],
        row["generation_completed_at"],
        row["generation_result_id"],
    )
    if current == desired:
        return False
    project_id = str(row["project_id"])
    state = connection.execute(
        """
        SELECT projection_revision
        FROM video_localization_tts_workflow_projection_state
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    next_revision = int(state["projection_revision"] if state else 0) + 1
    connection.execute(
        """
        UPDATE video_localization_tts_workflows
        SET generation_status = ?, generation_progress = ?,
            generation_error_message = ?, generation_started_at = ?,
            generation_completed_at = ?, generation_result_id = ?,
            projected_at = ?, row_revision = ?
        WHERE project_id = ? AND workflow_id = ?
        """,
        (*desired, projected_at, next_revision, project_id, str(row["workflow_id"])),
    )
    connection.execute(
        """
        UPDATE video_localization_tts_workflow_projection_state
        SET projection_revision = ?, projected_at = ?
        WHERE project_id = ?
        """,
        (next_revision, projected_at, project_id),
    )
    return True


def read_feed(
    project_id: str,
    *,
    after_revision: int = 0,
) -> TtsWorkflowFeedProjection:
    with database.read_conn() as connection:
        state = connection.execute(
            """
            SELECT projection_revision
            FROM video_localization_tts_workflow_projection_state
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if state is None:
            return TtsWorkflowFeedProjection(authoritative=False)
        revision = int(state["projection_revision"])
        if revision == after_revision:
            return TtsWorkflowFeedProjection(
                authoritative=True,
                revision=revision,
                changed=False,
            )
        id_rows = connection.execute(
            """
            SELECT workflow_id
            FROM video_localization_tts_workflows
            WHERE project_id = ?
            ORDER BY ordinal
            """,
            (project_id,),
        ).fetchall()
        rows = connection.execute(
            """
            SELECT workflow_id, generation_task_id, workflow_json,
                   generation_status, generation_progress,
                   generation_error_message, generation_started_at,
                   generation_completed_at, generation_result_id
            FROM video_localization_tts_workflows
            WHERE project_id = ?
              AND (
                    row_revision > ?
                    OR segment_id IN (
                        SELECT segment_id
                        FROM video_localization_tts_workflows
                        WHERE project_id = ? AND row_revision > ?
                    )
              )
            ORDER BY ordinal
            """,
            (
                project_id,
                max(0, after_revision),
                project_id,
                max(0, after_revision),
            ),
        ).fetchall()
    tasks = tuple(
        VideoLocalizationTtsTask.model_validate(
            json.loads(str(row["workflow_json"]))
        )
        for row in rows
    )
    runtimes: list[tuple[str, TtsGenerationRuntime]] = []
    for row in rows:
        generation_task_id = str(row["generation_task_id"] or "")
        generation_status = str(row["generation_status"] or "")
        if not generation_task_id or not generation_status:
            continue
        runtimes.append(
            (
                str(row["workflow_id"]),
                TtsGenerationRuntime(
                    task_id=generation_task_id,
                    status=TaskStatus(generation_status),
                    progress=float(row["generation_progress"] or 0.0),
                    error_message=row["generation_error_message"],
                    started_at=row["generation_started_at"],
                    completed_at=row["generation_completed_at"],
                    result_id=row["generation_result_id"],
                ),
            )
        )
    return TtsWorkflowFeedProjection(
        authoritative=True,
        revision=revision,
        changed=True,
        workflow_ids=tuple(str(row["workflow_id"]) for row in id_rows),
        tasks=tasks,
        generation_runtimes=tuple(runtimes),
    )


def read_project(project_id: str) -> TtsWorkflowProjection:
    with database.read_conn() as connection:
        state = connection.execute(
            """
            SELECT projection_revision
            FROM video_localization_tts_workflow_projection_state
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if state is None:
            return TtsWorkflowProjection(authoritative=False)
        rows = connection.execute(
            """
            SELECT workflow_json
            FROM video_localization_tts_workflows
            WHERE project_id = ?
            ORDER BY ordinal
            """,
            (project_id,),
        ).fetchall()
    return TtsWorkflowProjection(
        authoritative=True,
        revision=int(state["projection_revision"]),
        tasks=tuple(
            VideoLocalizationTtsTask.model_validate(
                json.loads(str(row["workflow_json"]))
            )
            for row in rows
        ),
    )


def delete_project_from_connection(
    connection: Connection,
    project_id: str,
) -> None:
    connection.execute(
        "DELETE FROM video_localization_tts_workflows WHERE project_id = ?",
        (project_id,),
    )
    connection.execute(
        """
        DELETE FROM video_localization_tts_workflow_projection_state
        WHERE project_id = ?
        """,
        (project_id,),
    )


__all__ = [
    "TtsGenerationRuntime",
    "TtsWorkflowFeedProjection",
    "TtsWorkflowProjection",
    "delete_project_from_connection",
    "read_feed",
    "read_project",
    "sync_generation_task_from_connection",
    "sync_project_from_connection",
]
