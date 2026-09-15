from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Iterator, Literal, Sequence

from app.schemas.voice_studio import (
    Project,
    ProjectCreate,
    ProjectSummary,
    ProjectUpdate,
    ProjectTranscriptionImportRequest,
    ProjectTranscriptionImportResponse,
    Role,
    ScriptSegment,
    SegmentStatus,
    TranscriptionRecord,
)
from app.schemas.video_localization_tts_handoff import TtsHandoffClaim
from app.services import database as db
from app.services import video_localization_operation_ledger_store
from app.services import video_localization_operation_store
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    resolve_execution_fence,
)

if TYPE_CHECKING:
    from app.schemas.video_localization_operation_detail import (
        OperationDetailCoreV1,
    )
    from app.schemas.video_localization_operation_summary import (
        OperationSummaryCoreV1,
    )
    from app.schemas.voice_studio import VideoLocalizationTtsTask
    from app.services.video_localization_project_cleanup_store import (
        ProjectCleanupJob,
    )


ProjectKind = Literal["script", "video_localization"]
_VIDEO_LOCALIZATION_PARAMETER_KEY = "video_localization"
_VIDEO_LOCALIZATION_DIR_PARAMETER_KEY = "video_localization_dir_name"


def list_projects() -> list[Project]:
    with db.conn() as connection:
        rows = connection.execute(
            """
            SELECT
                projects.data,
                projects.repository_revision
            FROM projects
            ORDER BY projects.updated_at DESC
            """
        ).fetchall()
    return [_project_from_repository_row(row) for row in rows]


def list_project_catalog_projects() -> list[Project]:
    """Read only the small fields needed by project menus and media health."""

    with db.conn() as connection:
        projected_rows = connection.execute(
            """
            SELECT projection.catalog_json
            FROM video_localization_workspace_projections AS projection
            JOIN projects
              ON projects.project_id = projection.project_id
            ORDER BY projects.updated_at DESC
            """
        ).fetchall()
        legacy_rows = connection.execute(
            """
            SELECT
                project_id,
                json_extract(data, '$.name') AS name,
                json_extract(data, '$.description') AS description,
                json_extract(data, '$.created_at') AS created_at,
                json_extract(data, '$.updated_at') AS updated_at,
                json_extract(data, '$.parameters.video_localization_dir_name') AS directory_name,
                json_extract(data, '$.parameters.video_localization.source_media') AS source_media,
                json_extract(data, '$.parameters.video_localization.stems') AS stems,
                json_type(data, '$.parameters.video_localization') AS localization_type
            FROM projects
            WHERE NOT EXISTS (
                SELECT 1
                FROM video_localization_workspace_projections AS projection
                WHERE projection.project_id = projects.project_id
            )
            ORDER BY updated_at DESC
            """
        ).fetchall()

    projects = [
        project
        for row in projected_rows
        if (project := _catalog_project_from_projection(row["catalog_json"]))
        is not None
    ]
    projects.extend(_catalog_project_from_row(row) for row in legacy_rows)
    return sorted(
        projects,
        key=lambda project: _timestamp_sort_value(project.updated_at),
        reverse=True,
    )


def get_project_catalog_project(project_id: str) -> Project | None:
    """Read one project's menu and media-locator fields without its large draft."""

    with db.conn() as connection:
        projection = connection.execute(
            """
            SELECT projection.catalog_json
            FROM video_localization_workspace_projections AS projection
            JOIN projects
              ON projects.project_id = projection.project_id
            WHERE projection.project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if projection is not None:
            projected = _catalog_project_from_projection(
                projection["catalog_json"]
            )
            if projected is not None:
                return projected
        row = connection.execute(
            """
            SELECT
                project_id,
                json_extract(data, '$.name') AS name,
                json_extract(data, '$.description') AS description,
                json_extract(data, '$.created_at') AS created_at,
                json_extract(data, '$.updated_at') AS updated_at,
                json_extract(data, '$.parameters.video_localization_dir_name') AS directory_name,
                json_extract(data, '$.parameters.video_localization.source_media') AS source_media,
                json_extract(data, '$.parameters.video_localization.stems') AS stems,
                json_type(data, '$.parameters.video_localization') AS localization_type
            FROM projects
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
    return _catalog_project_from_row(row) if row is not None else None


def get_video_localization_workspace_payload(
    project_id: str,
) -> tuple[bool, str | None, dict | None]:
    """Read one revision-bound workspace snapshot without server-owned details."""

    with db.conn() as connection:
        projection = connection.execute(
            """
            SELECT
                projection.repository_revision,
                projection.workspace_json
            FROM video_localization_workspace_projections AS projection
            JOIN projects
              ON projects.project_id = projection.project_id
            WHERE projection.project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if projection is not None:
            return (
                True,
                str(projection["repository_revision"]),
                _json_dict_or_none(projection["workspace_json"]),
            )
        row = connection.execute(
            """
            SELECT
                repository_revision,
                json_remove(
                    json_extract(data, '$.parameters.video_localization'),
                    '$.operations',
                    '$.tts_tasks',
                    '$.transcription',
                    '$.reference_clips',
                    '$.generated_candidates',
                    '$.dubbing_production'
                ) AS draft
            FROM projects
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
    if row is None:
        return False, None, None
    revision = str(row["repository_revision"])
    if row["draft"] is None:
        return True, revision, None
    try:
        parsed = json.loads(str(row["draft"]))
    except (TypeError, ValueError):
        return True, revision, None
    return True, revision, parsed if isinstance(parsed, dict) else None


_VIDEO_LOCALIZATION_WORKSPACE_DETAIL_PATHS = {
    "transcription": "$.parameters.video_localization.transcription",
    "reference_clips": "$.parameters.video_localization.reference_clips",
    "generated_candidates": "$.parameters.video_localization.generated_candidates",
    "dubbing_production": "$.parameters.video_localization.dubbing_production",
}


def get_video_localization_workspace_detail_payload(
    project_id: str,
    section: str,
) -> tuple[bool, object | None]:
    """Read one whitelisted detail section without deserializing the project."""

    json_path = _VIDEO_LOCALIZATION_WORKSPACE_DETAIL_PATHS.get(section)
    if json_path is None:
        raise ValueError(f"Unsupported workspace detail section: {section}")
    with db.conn() as connection:
        row = connection.execute(
            "SELECT json_extract(data, ?) AS detail FROM projects WHERE project_id = ?",
            (json_path, project_id),
        ).fetchone()
    if row is None:
        return False, None
    if row["detail"] is None:
        return True, None
    try:
        return True, json.loads(str(row["detail"]))
    except (TypeError, ValueError):
        return True, None


def get_video_localization_semantic_group_summaries(
    project_id: str,
) -> tuple[bool, list[dict]]:
    """Read the group picker projection without the complete production plan."""

    with db.conn() as connection:
        projection = connection.execute(
            """
            SELECT projection.semantic_group_summaries_json AS groups
            FROM video_localization_workspace_projections AS projection
            JOIN projects
              ON projects.project_id = projection.project_id
            WHERE projection.project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if projection is not None:
            parsed = _json_list(projection["groups"])
            return True, [item for item in parsed if isinstance(item, dict)]
        row = connection.execute(
            """
            SELECT json_extract(
                data,
                '$.parameters.video_localization.dubbing_production.active_plan.groups'
            ) AS groups
            FROM projects
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
    if row is None:
        return False, []
    try:
        groups = json.loads(str(row["groups"])) if row["groups"] is not None else []
    except (TypeError, ValueError):
        groups = []
    summaries = []
    for raw_group in groups if isinstance(groups, list) else []:
        if not isinstance(raw_group, dict):
            continue
        spoken_text = str(raw_group.get("spoken_text") or "")
        summaries.append(
            {
                "group_id": str(raw_group.get("group_id") or ""),
                "subtitle_ids": [
                    str(value)
                    for value in raw_group.get("subtitle_ids", [])
                    if value
                ],
                "text_preview": spoken_text[:120],
                "char_count": len(spoken_text),
            }
        )
    return True, [item for item in summaries if item["group_id"]]


def _catalog_project_from_row(row) -> Project:
    parameters: dict[str, object] = {}
    if row["directory_name"]:
        parameters[_VIDEO_LOCALIZATION_DIR_PARAMETER_KEY] = str(row["directory_name"])
    if row["localization_type"] is not None:
        parameters[_VIDEO_LOCALIZATION_PARAMETER_KEY] = {
            "source_media": _json_object(row["source_media"]),
            "stems": _json_object(row["stems"]),
        }
    return Project(
        project_id=str(row["project_id"]),
        name=str(row["name"] or ""),
        description=str(row["description"] or ""),
        parameters=parameters,
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )


def _catalog_project_from_projection(value: object) -> Project | None:
    payload = _json_dict_or_none(value)
    if payload is None:
        return None
    parameters: dict[str, object] = {}
    if payload.get("directory_name"):
        parameters[_VIDEO_LOCALIZATION_DIR_PARAMETER_KEY] = str(
            payload["directory_name"]
        )
    if payload.get("has_localization"):
        parameters[_VIDEO_LOCALIZATION_PARAMETER_KEY] = {
            "source_media": (
                payload.get("source_media")
                if isinstance(payload.get("source_media"), dict)
                else {}
            ),
            "stems": (
                payload.get("stems")
                if isinstance(payload.get("stems"), dict)
                else {}
            ),
        }
    return Project(
        project_id=str(payload.get("project_id") or ""),
        name=str(payload.get("name") or ""),
        description=str(payload.get("description") or ""),
        parameters=parameters,
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


def _json_dict_or_none(value: object) -> dict | None:
    if value is None:
        return None
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_list(value: object) -> list:
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: object) -> dict:
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def project_kind(project: Project) -> ProjectKind:
    if (
        _VIDEO_LOCALIZATION_PARAMETER_KEY in project.parameters
        or _VIDEO_LOCALIZATION_DIR_PARAMETER_KEY in project.parameters
    ):
        return "video_localization"
    return "script"


def summarize_project(
    project: Project,
    *,
    kind: ProjectKind | None = None,
    has_local_package: bool = False,
    source_media_status: Literal[
        "unknown",
        "unconfigured",
        "available",
        "missing",
        "not_file",
        "unreadable",
    ] = "unknown",
    package_status: Literal[
        "unknown",
        "available",
        "missing",
        "invalid",
        "repair_required",
    ] = "unknown",
) -> ProjectSummary:
    resolved_kind = kind or project_kind(project)
    localization = project.parameters.get(_VIDEO_LOCALIZATION_PARAMETER_KEY)
    source_media = localization.get("source_media") if isinstance(localization, dict) else None
    source_media_configured = bool(
        isinstance(source_media, dict)
        and (source_media.get("video_path") or source_media.get("filename"))
    )
    has_source_media = (
        source_media_status == "available"
        if source_media_status != "unknown"
        else source_media_configured
    )
    return ProjectSummary(
        project_id=project.project_id,
        name=project.name,
        description=project.description,
        kind=resolved_kind,
        has_source_media=has_source_media,
        source_media_configured=source_media_configured,
        source_media_status=source_media_status,
        has_local_package=has_local_package,
        package_status=package_status,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def list_project_summaries(kind: ProjectKind | None = None) -> list[ProjectSummary]:
    summaries = [summarize_project(project) for project in list_project_catalog_projects()]
    if kind:
        summaries = [summary for summary in summaries if summary.kind == kind]
    return sort_project_summaries(summaries)


def sort_project_summaries(summaries: list[ProjectSummary]) -> list[ProjectSummary]:
    """Put the most recently mutated project first, with deterministic ties."""
    return sorted(
        summaries,
        key=lambda summary: (
            _timestamp_sort_value(summary.updated_at),
            _timestamp_sort_value(summary.created_at),
            summary.project_id,
        ),
        reverse=True,
    )


def _timestamp_sort_value(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (OSError, OverflowError, ValueError):
        return float("-inf")


def get_project(project_id: str) -> Project | None:
    with db.conn() as connection:
        row = connection.execute(
            """
            SELECT
                projects.data,
                projects.repository_revision
            FROM projects
            WHERE projects.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    return _project_from_repository_row(row) if row is not None else None


def get_project_repository_revision(project_id: str) -> str | None:
    """Read the small durable-write revision without deserializing project data.

    ``updated_at`` is the user-visible business edit time and intentionally does
    not move for runtime writes.  Live workspace consumers need the repository
    revision instead because background task registration and result placement
    are runtime writes that must still become visible immediately.
    """

    with db.conn() as connection:
        row = connection.execute(
            """
            SELECT repository_revision
            FROM projects
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
    return str(row["repository_revision"]) if row is not None else None


def get_video_localization_tts_tasks_projection(
    project_id: str,
) -> tuple[bool, list[dict] | None]:
    """Read only the persisted localization TTS task list.

    Large localization projects can contain tens of megabytes of waveform and
    subtitle data. The task panel must not deserialize that entire document on
    every progress poll when it only needs this bounded task projection.
    ``None`` as the payload means the stored JSON is malformed and the caller
    should use the normal draft reader so its repair semantics stay intact.
    """

    with db.conn() as connection:
        row = connection.execute(
            """
            SELECT json_extract(
                projects.data,
                '$.parameters.video_localization.tts_tasks'
            ) AS tts_tasks
            FROM projects
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
    if row is None:
        return False, None
    payload = row["tts_tasks"]
    if payload is None:
        return True, []
    try:
        parsed = json.loads(str(payload))
    except (TypeError, ValueError):
        return True, None
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        return True, None
    return True, parsed


def get_video_localization_timeline_projection(
    project_id: str,
) -> tuple[bool, str | None, list[dict] | None]:
    """Read the live timeline without deserializing the complete project.

    The localization workbench polls this projection while background TTS
    tasks are placing clips.  Keeping the query limited to ``timeline_clips``
    prevents a progress update from repeatedly parsing and transporting the
    ASR word graph, reference clips, and task histories.

    ``None`` as the clip payload means the stored JSON is malformed.  Callers
    must surface that as repair-required instead of silently treating a broken
    project as an empty timeline.
    """

    with db.conn() as connection:
        projection = connection.execute(
            """
            SELECT
                projection.repository_revision,
                projection.timeline_json AS timeline_clips
            FROM video_localization_workspace_projections AS projection
            JOIN projects
              ON projects.project_id = projection.project_id
            WHERE projection.project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if projection is not None:
            parsed = _json_list(projection["timeline_clips"])
            if not all(isinstance(item, dict) for item in parsed):
                return True, str(projection["repository_revision"]), None
            return True, str(projection["repository_revision"]), parsed
        row = connection.execute(
            """
            SELECT
                repository_revision,
                json_extract(
                    projects.data,
                    '$.parameters.video_localization.timeline_clips'
                ) AS timeline_clips
            FROM projects
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
    if row is None:
        return False, None, None
    payload = row["timeline_clips"]
    if payload is None:
        return True, str(row["repository_revision"]), []
    try:
        parsed = json.loads(str(payload))
    except (TypeError, ValueError):
        return True, str(row["repository_revision"]), None
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        return True, str(row["repository_revision"]), None
    return True, str(row["repository_revision"]), parsed


def get_video_localization_directory_locator(
    project_id: str,
) -> tuple[str | None, str] | None:
    """Read only the fields needed to locate a project's media package.

    Media range requests must not deserialize the potentially large
    video-localization draft merely to resolve its package directory.
    """

    with db.conn() as connection:
        projection = connection.execute(
            """
            SELECT projection.catalog_json
            FROM video_localization_workspace_projections AS projection
            JOIN projects
              ON projects.project_id = projection.project_id
            WHERE projection.project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if projection is not None:
            catalog = _json_dict_or_none(projection["catalog_json"])
            if catalog is not None:
                directory_name = catalog.get("directory_name")
                return (
                    str(directory_name).strip() if directory_name else None,
                    str(catalog.get("name") or ""),
                )
        row = connection.execute(
            """
            SELECT
                json_extract(
                    projects.data,
                    '$.parameters.video_localization_dir_name'
                ) AS directory_name,
                json_extract(projects.data, '$.name') AS project_name
            FROM projects
            WHERE projects.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    if row is None:
        return None
    directory_name = row["directory_name"]
    return (
        str(directory_name).strip() if directory_name else None,
        str(row["project_name"] or ""),
    )


def _project_from_repository_row(row) -> Project:
    project = Project(**json.loads(row["data"]))
    project._repository_revision = int(row["repository_revision"])
    return project


@contextmanager
def publication_guard(project_id: str) -> Iterator[Project | None]:
    """Hold the SQLite writer boundary while one artifact is published.

    The in-process draft lock only coordinates threads in one application
    process.  Exporters also run beside the standalone localization worker, so
    the final read/validate/publish boundary must use SQLite's cross-process
    write lock.  Callers must not mutate the project through another connection
    while this guard is held.
    """

    with db.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT data, repository_revision
            FROM projects
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        yield _project_from_repository_row(row) if row is not None else None


def save_project(
    project: Project,
    *,
    touch_updated_at: bool = True,
    operation_updated_at: str | None = None,
    execution_fence: ExecutionFence | None = None,
    tts_handoff_claim: TtsHandoffClaim | None = None,
    observed_at_ms: int | None = None,
    operation_command: (
        video_localization_operation_ledger_store.OperationCommand | None
    ) = None,
    operation_summary_cores: (
        Sequence["OperationSummaryCoreV1"] | None
    ) = None,
    operation_detail_core: "OperationDetailCoreV1 | None" = None,
    tts_workflows: Sequence["VideoLocalizationTtsTask"] | None = None,
    snapshot_create_autosave: bool = False,
) -> Project:
    if operation_command is not None:
        if execution_fence is not None:
            raise ValueError(
                "operation commands cannot share a worker execution fence"
            )
        # A user command is authorized by its ledger revision/CAS boundary.
        # It may intentionally cancel the worker whose contextual fence is
        # active on this call stack, so it must not inherit that capability.
        execution_fence = None
    else:
        execution_fence = resolve_execution_fence(execution_fence)
    previous_updated_at = project.updated_at
    if touch_updated_at:
        project.updated_at = datetime.now().isoformat(timespec="microseconds")
    try:
        next_revision = (
            video_localization_operation_store
            .save_project_with_projection(
                project.project_id,
                project.model_dump(mode="json"),
                updated_at=project.updated_at,
                expected_repository_revision=(
                    project._repository_revision
                ),
                operation_updated_at=operation_updated_at,
                execution_fence=execution_fence,
                tts_handoff_claim=tts_handoff_claim,
                observed_at_ms=observed_at_ms,
                operation_command=operation_command,
                operation_summary_cores=operation_summary_cores,
                operation_detail_core=operation_detail_core,
                tts_workflows=tts_workflows,
                snapshot_create_autosave=snapshot_create_autosave,
            )
        )
    except Exception:
        project.updated_at = previous_updated_at
        raise
    project._repository_revision = next_revision
    return project


def create_project(data: ProjectCreate) -> Project:
    return save_project(Project(**data.model_dump()))


def update_project(project_id: str, data: ProjectUpdate) -> Project | None:
    project = get_project(project_id)
    if not project:
        return None
    patch = data.model_dump(exclude_unset=True)
    if "name" in patch and patch["name"] is not None:
        project.name = patch["name"].strip() or project.name
    if "description" in patch and patch["description"] is not None:
        project.description = patch["description"]
    if "default_engine_id" in patch:
        project.default_engine_id = patch["default_engine_id"]
    return save_project(project)


def reset_video_localization_project(
    project: Project,
    *,
    operation_updated_at: str,
    cleanup_job: "ProjectCleanupJob",
) -> Project:
    previous_updated_at = project.updated_at
    project.updated_at = datetime.now().isoformat(timespec="microseconds")
    try:
        next_revision = (
            video_localization_operation_store
            .reset_project_with_projection(
                project.project_id,
                project.model_dump(mode="json"),
                updated_at=project.updated_at,
                expected_repository_revision=project._repository_revision,
                operation_updated_at=operation_updated_at,
                cleanup_job=cleanup_job,
            )
        )
    except Exception:
        project.updated_at = previous_updated_at
        raise
    project._repository_revision = next_revision
    return project


def delete_project(
    project_id: str,
    *,
    expected_repository_revision: int | None = None,
    cleanup_job: "ProjectCleanupJob | None" = None,
    requested_at: str | None = None,
) -> None:
    if cleanup_job is None:
        video_localization_operation_store.delete_project_with_projection(
            project_id
        )
        return
    if expected_repository_revision is None or requested_at is None:
        raise ValueError(
            "cleanup-backed project delete requires revision and timestamp"
        )
    video_localization_operation_store.delete_project_with_cleanup(
        project_id,
        expected_repository_revision=expected_repository_revision,
        cleanup_job=cleanup_job,
        requested_at=requested_at,
    )


def add_role(project_id: str, role: Role) -> Project | None:
    project = get_project(project_id)
    if not project:
        return None
    project.roles.append(role)
    return save_project(project)


def upsert_segments(project_id: str, segments: list[ScriptSegment]) -> Project | None:
    project = get_project(project_id)
    if not project:
        return None
    normalized = []
    for idx, seg in enumerate(segments):
        seg.index = idx
        if seg.text.strip() and seg.status == SegmentStatus.empty:
            seg.status = SegmentStatus.ready
        normalized.append(seg)
    project.segments = normalized
    return save_project(project)


def import_transcriptions(project_id: str, data: ProjectTranscriptionImportRequest) -> ProjectTranscriptionImportResponse | None:
    project = get_project(project_id)
    if not project:
        return None

    role = _role_for_import(project, data.role_id)
    base_segments = [] if data.mode == "replace" else list(project.segments)
    base_index = len(base_segments)
    imported: list[ScriptSegment] = []
    skipped = 0

    for transcription_id in data.transcription_ids:
        stored = db.get_one("transcriptions", "transcription_id", transcription_id)
        if not stored:
            skipped += 1
            continue
        record = TranscriptionRecord(**stored)
        pieces = _transcription_pieces(record)
        if not pieces:
            skipped += 1
            continue
        for piece in pieces:
            imported.append(
                ScriptSegment(
                    index=base_index + len(imported),
                    text=piece["text"],
                    source_start_ms=piece["source_start_ms"],
                    source_end_ms=piece["source_end_ms"],
                    role_id=role.role_id if role else None,
                    voice_id=data.default_voice_id or (role.default_voice_id if role else None),
                    engine_id=data.default_engine_id or (role.default_engine_id if role else None) or project.default_engine_id or "indextts-v2",
                    language="zh" if record.language == "auto" else record.language,
                    emotion=(role.default_emotion if role else None) or "calm",
                    speed=(role.default_speed if role else 1.0) or 1.0,
                    status=SegmentStatus.ready,
                )
            )

    project.segments = _normalize_segment_indexes(base_segments + imported)
    save_project(project)
    return ProjectTranscriptionImportResponse(project=project, imported_count=len(imported), skipped_count=skipped)


def update_segment_result(
    project_id: str,
    segment_id: str,
    result_audio_id: str | None,
    result_id: str | None,
    status: SegmentStatus,
    error: str | None = None,
) -> bool:
    project = get_project(project_id)
    if not project:
        return False
    for seg in project.segments:
        if seg.segment_id == segment_id:
            if (
                seg.result_audio_id == result_audio_id
                and seg.result_id == result_id
                and seg.status == status
                and seg.error_message == error
            ):
                return False
            seg.result_audio_id = result_audio_id
            seg.result_id = result_id
            seg.status = status
            seg.error_message = error
            save_project(project)
            return True
    return False


def _role_for_import(project: Project, role_id: str | None) -> Role | None:
    if role_id:
        return next((role for role in project.roles if role.role_id == role_id), None)
    return project.roles[0] if project.roles else None


def _transcription_pieces(record: TranscriptionRecord) -> list[dict[str, int | str | None]]:
    if record.segments:
        return [
            {
                "text": segment.text.strip(),
                "source_start_ms": segment.start_ms,
                "source_end_ms": segment.end_ms,
            }
            for segment in record.segments
            if segment.text.strip()
        ]
    return [
        {
            "text": text,
            "source_start_ms": None,
            "source_end_ms": None,
        }
        for text in _split_transcript_text(record.text)
    ]


def _split_transcript_text(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"\n+|(?<=[。！？!?；;])", text.strip()) if item.strip()]


def _normalize_segment_indexes(segments: list[ScriptSegment]) -> list[ScriptSegment]:
    for index, segment in enumerate(segments):
        segment.index = index
        if segment.text.strip() and segment.status == SegmentStatus.empty:
            segment.status = SegmentStatus.ready
    return segments
