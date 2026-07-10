from __future__ import annotations

from fastapi import APIRouter

from app.errors import AppException
from app.schemas.voice_studio import (
    Project,
    ProjectCreate,
    ProjectUpdate,
    ProjectTranscriptionImportRequest,
    ProjectTranscriptionImportResponse,
    Role,
    ScriptSegment,
)
from app.services import project_store, task_queue

router = APIRouter()


@router.get("", response_model=list[Project])
async def list_projects():
    return project_store.list_projects()


@router.post("", response_model=Project)
async def create_project(data: ProjectCreate):
    return project_store.create_project(data)


@router.get("/{project_id}", response_model=Project)
async def get_project(project_id: str):
    project = project_store.get_project(project_id)
    if not project:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return project


@router.patch("/{project_id}", response_model=Project)
async def update_project(project_id: str, data: ProjectUpdate):
    project = project_store.get_project(project_id)
    if not project:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    patch = data.model_dump(exclude_unset=True)
    if patch.get("name") is not None:
        from app.domains.video_localization import service as video_localization_service

        project = video_localization_service.prepare_project_rename(project, str(patch["name"]).strip())
    if patch.get("description") is not None:
        project.description = str(patch["description"])
    if "default_engine_id" in patch:
        project.default_engine_id = patch["default_engine_id"]
    return project_store.save_project(project)


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    project_store.delete_project(project_id)
    return {"status": "deleted"}


@router.post("/{project_id}/roles", response_model=Project)
async def add_role(project_id: str, role: Role):
    project = project_store.add_role(project_id, role)
    if not project:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return project


@router.put("/{project_id}/segments", response_model=Project)
async def put_segments(project_id: str, segments: list[ScriptSegment]):
    project = project_store.upsert_segments(project_id, segments)
    if not project:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return project


@router.post("/{project_id}/transcriptions/import", response_model=ProjectTranscriptionImportResponse)
async def import_transcriptions(project_id: str, data: ProjectTranscriptionImportRequest):
    if not data.transcription_ids:
        raise AppException(400, "TRANSCRIPTION_IMPORT_EMPTY", "No transcription records selected")
    result = project_store.import_transcriptions(project_id, data)
    if not result:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return result


@router.post("/{project_id}/generate")
async def generate_project(project_id: str):
    project = project_store.get_project(project_id)
    if not project:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    task_ids = await task_queue.submit_project(project)
    return {"task_ids": task_ids, "status": "queued"}
