from __future__ import annotations

import json

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from app.domains.video_localization import operation_queue
from app.domains.video_localization import service as video_localization_service
from app.errors import AppException
from app.schemas.voice_studio import BatchTask, VideoLocalizationCueUpdate, VideoLocalizationDraft, VideoLocalizationOperation, VideoLocalizationOperationRequest, VideoLocalizationReferenceClipUpdate
from app.services import batch_queue

router = APIRouter()


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


@router.get("/{project_id}/video-localization/operations", response_model=list[VideoLocalizationOperation])
async def list_video_localization_operations(project_id: str):
    operations = operation_queue.list_operations(project_id)
    if operations is None:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return operations


@router.post("/{project_id}/video-localization/operations", response_model=VideoLocalizationOperation)
async def submit_video_localization_operation(project_id: str, request: VideoLocalizationOperationRequest):
    operation = operation_queue.submit(project_id, request.kind, request.parameters)
    if not operation:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return operation


@router.get("/{project_id}/video-localization/operations/{operation_id}", response_model=VideoLocalizationOperation)
async def get_video_localization_operation(project_id: str, operation_id: str):
    operation = operation_queue.get_operation(project_id, operation_id)
    if not operation:
        raise AppException(404, "VIDEO_LOCALIZATION_OPERATION_NOT_FOUND", "Operation not found")
    return operation


@router.post("/{project_id}/video-localization/operations/{operation_id}/cancel", response_model=VideoLocalizationOperation)
async def cancel_video_localization_operation(project_id: str, operation_id: str):
    operation = operation_queue.cancel(project_id, operation_id)
    if not operation:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return operation


@router.post("/{project_id}/video-localization/operations/{operation_id}/retry", response_model=VideoLocalizationOperation)
async def retry_video_localization_operation(project_id: str, operation_id: str):
    operation = operation_queue.retry(project_id, operation_id)
    if not operation:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return operation


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


@router.patch("/{project_id}/video-localization/reference-clips/{reference_clip_id}", response_model=VideoLocalizationDraft)
async def update_video_localization_reference_clip(project_id: str, reference_clip_id: str, patch: VideoLocalizationReferenceClipUpdate):
    updated = video_localization_service.update_reference_clip(project_id, reference_clip_id, patch)
    if not updated:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return updated


@router.patch("/{project_id}/video-localization/cues/{cue_id}", response_model=VideoLocalizationDraft)
async def update_video_localization_cue(project_id: str, cue_id: str, patch: VideoLocalizationCueUpdate):
    updated = video_localization_service.update_cue(project_id, cue_id, patch)
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
        batch = await batch_queue.submit(batch_request.model_dump())
        video_localization_service.mark_tts_batch_submitted(project_id, batch.batch_task_id, [segment.segment_id for segment in batch.segments])
        return batch
    except ValueError as exc:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_BATCH_INVALID", str(exc)) from exc


@router.post("/{project_id}/video-localization/tts/batch/{batch_task_id}/sync", response_model=VideoLocalizationDraft)
async def sync_video_localization_tts_batch(project_id: str, batch_task_id: str):
    updated = video_localization_service.sync_tts_batch_results(project_id, batch_task_id)
    if not updated:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return updated


@router.get("/{project_id}/video-localization/cues/{cue_id}/tts-audio")
async def get_video_localization_cue_tts_audio(project_id: str, cue_id: str):
    audio_path = video_localization_service.tts_audio_file(project_id, cue_id)
    if not audio_path:
        raise AppException(404, "VIDEO_LOCALIZATION_TTS_AUDIO_NOT_FOUND", "TTS audio file not found")
    return FileResponse(audio_path, filename=audio_path.name)


@router.get("/{project_id}/video-localization/cues/{cue_id}/source-audio")
async def get_video_localization_cue_source_audio(project_id: str, cue_id: str):
    audio_path = video_localization_service.source_cue_audio_file(project_id, cue_id)
    if not audio_path:
        raise AppException(404, "VIDEO_LOCALIZATION_SOURCE_CUE_AUDIO_NOT_FOUND", "Source cue audio file not found")
    return FileResponse(audio_path, filename=audio_path.name)


@router.get("/{project_id}/video-localization/reference-clips/{reference_clip_id}/audio")
async def get_video_localization_reference_clip_audio(project_id: str, reference_clip_id: str):
    audio_path = video_localization_service.reference_clip_audio_file(project_id, reference_clip_id)
    if not audio_path:
        raise AppException(404, "VIDEO_LOCALIZATION_REFERENCE_AUDIO_NOT_FOUND", "Reference audio file not found")
    return FileResponse(audio_path, filename=audio_path.name)


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


@router.get("/{project_id}/video-localization/readiness")
async def export_video_localization_readiness(project_id: str):
    data = video_localization_service.production_readiness_audit(project_id)
    if not data:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    filename = f"{project_id}-video-localization-readiness.json"
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
