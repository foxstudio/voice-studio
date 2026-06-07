from __future__ import annotations

import re

from app.models.schemas import (
    Project,
    ProjectCreate,
    ProjectTranscriptionImportRequest,
    ProjectTranscriptionImportResponse,
    Role,
    ScriptSegment,
    SegmentStatus,
    TranscriptionRecord,
    now_iso,
)
from app.services import database as db


def list_projects() -> list[Project]:
    return [Project(**d) for d in db.list_all("projects", "updated_at")]


def get_project(project_id: str) -> Project | None:
    data = db.get_one("projects", "project_id", project_id)
    return Project(**data) if data else None


def save_project(project: Project) -> Project:
    project.updated_at = now_iso()
    db.upsert("projects", project.project_id, project.model_dump())
    return project


def create_project(data: ProjectCreate) -> Project:
    return save_project(Project(**data.model_dump()))


def delete_project(project_id: str) -> None:
    db.delete_one("projects", "project_id", project_id)


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


def update_segment_result(project_id: str, segment_id: str, result_audio_id: str | None, result_id: str | None, status: SegmentStatus, error: str | None = None) -> None:
    project = get_project(project_id)
    if not project:
        return
    for seg in project.segments:
        if seg.segment_id == segment_id:
            seg.result_audio_id = result_audio_id
            seg.result_id = result_id
            seg.status = status
            seg.error_message = error
            break
    save_project(project)


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
