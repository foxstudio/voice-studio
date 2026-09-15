from __future__ import annotations

import json
from sqlite3 import Connection
from typing import Any


_LOCALIZATION_KEY = "video_localization"
_DIRECTORY_KEY = "video_localization_dir_name"
_OMITTED_WORKSPACE_SECTIONS = {
    "operations",
    "tts_tasks",
    "history_placement_receipts",
    "timeline_edit_receipts",
    "transcription",
    "reference_clips",
    "generated_candidates",
    "dubbing_production",
}
_SERVER_ONLY_CLIP_FIELDS = {"cqc_report", "timeline_edit_gate"}


def sync_project_from_connection(
    connection: Connection,
    project_id: str,
    project_payload: dict[str, Any],
    *,
    repository_revision: int,
    projected_at: str,
) -> None:
    """Publish bounded workbench readers in the Project commit transaction."""

    parameters = project_payload.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    localization = parameters.get(_LOCALIZATION_KEY)
    localization = localization if isinstance(localization, dict) else None
    catalog = {
        "project_id": project_id,
        "name": str(project_payload.get("name") or ""),
        "description": str(project_payload.get("description") or ""),
        "created_at": str(project_payload.get("created_at") or ""),
        "updated_at": str(project_payload.get("updated_at") or ""),
        "directory_name": parameters.get(_DIRECTORY_KEY),
        "has_localization": localization is not None,
        "source_media": (
            localization.get("source_media") if localization is not None else None
        ),
        "stems": localization.get("stems") if localization is not None else None,
    }
    workspace = _workspace_projection(localization)
    timeline = _timeline_projection(localization)
    semantic_groups = _semantic_group_summaries(localization)
    connection.execute(
        """
        INSERT INTO video_localization_workspace_projections (
            project_id,
            repository_revision,
            catalog_json,
            workspace_json,
            timeline_json,
            semantic_group_summaries_json,
            projected_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            repository_revision = excluded.repository_revision,
            catalog_json = excluded.catalog_json,
            workspace_json = excluded.workspace_json,
            timeline_json = excluded.timeline_json,
            semantic_group_summaries_json = excluded.semantic_group_summaries_json,
            projected_at = excluded.projected_at
        """,
        (
            project_id,
            repository_revision,
            _dump(catalog),
            _dump(workspace) if workspace is not None else None,
            _dump(timeline),
            _dump(semantic_groups),
            projected_at,
        ),
    )


def delete_project_from_connection(
    connection: Connection,
    project_id: str,
) -> None:
    connection.execute(
        "DELETE FROM video_localization_workspace_projections WHERE project_id = ?",
        (project_id,),
    )


def _workspace_projection(
    localization: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if localization is None:
        return None
    workspace = {
        key: value
        for key, value in localization.items()
        if key not in _OMITTED_WORKSPACE_SECTIONS
    }
    workspace["timeline_clips"] = _timeline_projection(localization)
    return workspace


def _timeline_projection(
    localization: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if localization is None:
        return []
    clips = localization.get("timeline_clips")
    if not isinstance(clips, list):
        return []
    return [
        {
            key: value
            for key, value in clip.items()
            if key not in _SERVER_ONLY_CLIP_FIELDS
        }
        for clip in clips
        if isinstance(clip, dict)
    ]


def _semantic_group_summaries(
    localization: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if localization is None:
        return []
    production = localization.get("dubbing_production")
    production = production if isinstance(production, dict) else {}
    active_plan = production.get("active_plan")
    active_plan = active_plan if isinstance(active_plan, dict) else {}
    groups = active_plan.get("groups")
    summaries: list[dict[str, Any]] = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict) or not group.get("group_id"):
            continue
        spoken_text = str(group.get("spoken_text") or "")
        summaries.append(
            {
                "group_id": str(group["group_id"]),
                "subtitle_ids": [
                    str(value)
                    for value in group.get("subtitle_ids", [])
                    if value
                ],
                "text_preview": spoken_text[:120],
                "char_count": len(spoken_text),
            }
        )
    return summaries


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
