from __future__ import annotations

import json

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from app.domains.video_localization import service as video_localization_service
from app.errors import AppException
from app.schemas.voice_studio import BatchTask, Project, ProjectCreate, ProjectTranscriptionImportRequest, ProjectTranscriptionImportResponse, Role, ScriptSegment, VideoLocalizationDraft
from app.services import batch_queue, project_store, task_queue

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


@router.get("/{project_id}/video-localization", response_model=VideoLocalizationDraft)
async def get_video_localization(project_id: str):
    draft = video_localization_service.get_video_localization(project_id)
    if not draft:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return draft


@router.put("/{project_id}/video-localization", response_model=VideoLocalizationDraft)
async def put_video_localization(project_id: str, draft: VideoLocalizationDraft):
    updated = video_localization_service.save_video_localization(project_id, draft)
    if not updated:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return updated


@router.post("/{project_id}/video-localization/source-media", response_model=VideoLocalizationDraft)
async def import_video_localization_source_media(project_id: str, file: UploadFile = File(...)):
    updated = await video_localization_service.import_source_media(project_id, file)
    if not updated:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return updated


@router.post("/{project_id}/video-localization/source-audio", response_model=VideoLocalizationDraft)
async def extract_video_localization_source_audio(project_id: str):
    updated = video_localization_service.extract_source_audio(project_id)
    if not updated:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return updated


@router.post("/{project_id}/video-localization/stems", response_model=VideoLocalizationDraft)
async def separate_video_localization_source_audio(project_id: str):
    updated = video_localization_service.separate_source_audio(project_id)
    if not updated:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return updated


@router.post("/{project_id}/video-localization/asr/en", response_model=VideoLocalizationDraft)
async def transcribe_video_localization_english(project_id: str):
    updated = video_localization_service.transcribe_english_source_audio(project_id)
    if not updated:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return updated


@router.post("/{project_id}/video-localization/reference-clips", response_model=VideoLocalizationDraft)
async def create_video_localization_reference_clips(project_id: str):
    updated = video_localization_service.create_reference_clips_from_cues(project_id)
    if not updated:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return updated


@router.post("/{project_id}/video-localization/localize/zh", response_model=VideoLocalizationDraft)
async def generate_video_localization_chinese_draft(project_id: str):
    updated = video_localization_service.generate_localization_draft(project_id)
    if not updated:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return updated


@router.post("/{project_id}/video-localization/tts/batch", response_model=BatchTask)
async def submit_video_localization_tts_batch(project_id: str):
    batch_request = video_localization_service.build_tts_batch_request(project_id)
    if not batch_request:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    try:
        return await batch_queue.submit(batch_request.model_dump())
    except ValueError as exc:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_BATCH_INVALID", str(exc)) from exc


@router.get("/{project_id}/video-localization/subtitles/{kind}")
async def export_video_localization_subtitles(project_id: str, kind: str):
    srt = video_localization_service.export_subtitles(project_id, kind)
    if srt is None:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    filename = f"{project_id}-video-localization-{kind}.srt"
    return PlainTextResponse(
        content=srt,
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project_id}/video-localization/export")
async def export_video_localization(project_id: str):
    data = video_localization_service.export_video_localization(project_id)
    if not data:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    filename = f"{project_id}-video-localization.json"
    return JSONResponse(
        content=json.loads(data.model_dump_json()),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
