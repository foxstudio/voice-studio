from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.errors import AppException, ProjectRevisionConflict
from app.api.video_localization_public_contracts import (
    PublicProject as Project,
    PublicProjectTranscriptionImportResponse as ProjectTranscriptionImportResponse,
)
from app.schemas.voice_studio import (
    ProjectCreate,
    ProjectSummary,
    ProjectUpdate,
    ProjectTranscriptionImportRequest,
    Role,
    ScriptSegment,
)
from app.services import project_store, task_queue

router = APIRouter()


class ProjectDeleteResponse(BaseModel):
    status: Literal["deleted"]
    cleanup_status: Literal["pending"]


@router.get("", response_model=list[Project])
def list_projects():
    return project_store.list_projects()


@router.get(
    "/summaries",
    response_model=list[ProjectSummary],
    description=(
        "只返回项目下拉菜单需要的名称、类型和时间等摘要信息，"
        "不传输完整脚本、角色或视频本土化草稿。可按项目类型筛选。"
    ),
)
def list_project_summaries(
    kind: Literal["script", "video_localization"] | None = Query(default=None),
):
    if kind == "video_localization":
        from app.domains.video_localization import (
            service as video_localization_service,
        )

        return video_localization_service.list_indexed_video_localization_project_summaries()
    if kind == "script":
        return project_store.list_project_summaries("script")
    from app.domains.video_localization import service as video_localization_service

    return project_store.sort_project_summaries(
        [
            *project_store.list_project_summaries("script"),
            *video_localization_service.list_indexed_video_localization_project_summaries(),
        ]
    )


@router.post("", response_model=Project)
def create_project(data: ProjectCreate):
    return project_store.create_project(data)


@router.get("/{project_id}", response_model=Project)
def get_project(project_id: str):
    project = project_store.get_project(project_id)
    if not project:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return project


@router.patch("/{project_id}", response_model=Project)
def update_project(project_id: str, data: ProjectUpdate):
    project = project_store.get_project(project_id)
    if not project:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    patch = data.model_dump(exclude_unset=True)
    video_localization_service = None
    if patch.get("name") is not None:
        from app.domains.video_localization import service as video_localization_service

        project = video_localization_service.prepare_project_rename(project, str(patch["name"]).strip())
    if patch.get("description") is not None:
        project.description = str(patch["description"])
    if "default_engine_id" in patch:
        project.default_engine_id = patch["default_engine_id"]
    try:
        if "video_localization" in project.parameters:
            if video_localization_service is None:
                from app.domains.video_localization import service as video_localization_service

            saved = video_localization_service.save_project_metadata(
                project
            )
        else:
            saved = project_store.save_project(project)
    except ProjectRevisionConflict as exc:
        raise AppException(
            409,
            "PROJECT_REVISION_CONFLICT",
            "项目刚刚被其他操作更新，请刷新后重试。",
        ) from exc
    return saved


@router.delete(
    "/{project_id}",
    response_model=ProjectDeleteResponse,
)
def delete_project(project_id: str):
    from app.domains.video_localization import service as video_localization_service

    if not video_localization_service.delete_project(project_id):
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return {
        "status": "deleted",
        "cleanup_status": "pending",
    }


@router.post("/{project_id}/roles", response_model=Project)
def add_role(project_id: str, role: Role):
    project = project_store.add_role(project_id, role)
    if not project:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return project


@router.put("/{project_id}/segments", response_model=Project)
def put_segments(project_id: str, segments: list[ScriptSegment]):
    project = project_store.upsert_segments(project_id, segments)
    if not project:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return project


@router.post("/{project_id}/transcriptions/import", response_model=ProjectTranscriptionImportResponse)
def import_transcriptions(project_id: str, data: ProjectTranscriptionImportRequest):
    if not data.transcription_ids:
        raise AppException(400, "TRANSCRIPTION_IMPORT_EMPTY", "No transcription records selected")
    result = project_store.import_transcriptions(project_id, data)
    if not result:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return result


@router.post("/{project_id}/generate")
async def generate_project(project_id: str):
    project = await asyncio.to_thread(project_store.get_project, project_id)
    if not project:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    task_ids = await task_queue.submit_project(project)
    return {"task_ids": task_ids, "status": "queued"}
