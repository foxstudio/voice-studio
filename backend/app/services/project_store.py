from __future__ import annotations

from app.models.schemas import Project, ProjectCreate, Role, ScriptSegment, SegmentStatus, now_iso
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

