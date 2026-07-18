from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Body, File, Query, UploadFile
from pydantic import BaseModel, Field

from app.api.video_localization_responses import audio_file_response, download_file_response, json_attachment, media_file_response, require_resource, srt_attachment
from app.domains.video_localization import operation_queue
from app.domains.video_localization import service as video_localization_service
from app.errors import AppException
from app.domains.video_localization.schemas import (
    BatchTask,
    VideoLocalizationCueTimingConfirmationRequest,
    VideoLocalizationCueUpdate,
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationOperationRequest,
    VideoLocalizationReferenceClipCreate,
    VideoLocalizationReferenceClipUpdate,
    VideoLocalizationSpeakerCreate,
    VideoLocalizationSpeakerUpdate,
    VideoLocalizationSubtitleCueUpdate,
    VideoLocalizationSubtitleImportRequest,
)
from app.schemas.voice_studio import GenerateRequest, Project
from app.services import batch_queue, waveform_cache

router = APIRouter()


class WaveformPeaksResponse(BaseModel):
    peaks: list[float]
    duration: float
    bins: int


class TtsHistoryTimelineApplyRequest(BaseModel):
    segment_id: str
    clip_id: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    dub_lane: int | None = Field(default=None, ge=0)
    force_new: bool = False


@router.post("/video-localization/sync-projects", response_model=list[Project])
async def sync_video_localization_projects():
    return video_localization_service.sync_local_projects()


@router.get("/{project_id}/video-localization", response_model=VideoLocalizationDraft)
async def get_video_localization(project_id: str):
    draft = video_localization_service.get_video_localization(project_id)
    return require_resource(draft)


@router.put("/{project_id}/video-localization", response_model=VideoLocalizationDraft)
async def put_video_localization(project_id: str, draft: VideoLocalizationDraft):
    updated = video_localization_service.replace_video_localization_from_client(project_id, draft)
    return require_resource(updated)


@router.patch("/{project_id}/video-localization/ui-state", response_model=VideoLocalizationDraft)
async def patch_video_localization_ui_state(project_id: str, patch: dict = Body(default_factory=dict)):
    updated = video_localization_service.update_video_localization_ui_state(project_id, patch)
    return require_resource(updated)


@router.delete("/{project_id}/video-localization", response_model=VideoLocalizationDraft)
async def reset_video_localization(project_id: str):
    updated = video_localization_service.reset_video_localization(project_id)
    return require_resource(updated)


@router.post("/{project_id}/video-localization/open-directory")
async def open_video_localization_project_directory(project_id: str):
    opened = video_localization_service.open_project_directory(project_id)
    return require_resource(opened, code="VIDEO_LOCALIZATION_PROJECT_NOT_FOUND", message="Project not found")


@router.post("/{project_id}/video-localization/source-media", response_model=VideoLocalizationDraft)
async def import_video_localization_source_media(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    updated = await video_localization_service.import_source_media(project_id, file)
    if updated is not None:
        background_tasks.add_task(video_localization_service.prepare_source_preview_video, project_id)
    return require_resource(updated)


@router.get("/{project_id}/video-localization/source-media/video")
async def get_video_localization_source_video(project_id: str):
    video_path = video_localization_service.source_video_file(project_id)
    return audio_file_response(video_path, code="VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND", message="Source video file not found")


@router.get("/{project_id}/video-localization/source-media/preview-video")
async def get_video_localization_source_preview_video(project_id: str):
    video_path = video_localization_service.source_preview_video_file(project_id)
    return media_file_response(video_path, code="VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND", message="Source video file not found")


@router.post("/{project_id}/video-localization/source-media/preview-video")
async def prepare_video_localization_source_preview_video(project_id: str):
    previous_path = video_localization_service.source_preview_video_file(project_id)
    video_path = await asyncio.to_thread(video_localization_service.ensure_source_preview_video, project_id)
    return require_resource(
        {
            "status": "ready",
            "profile": video_localization_service.source_preview_profile(video_path),
            "changed": previous_path != video_path,
        },
        code="VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND",
        message="Source video file not found",
    )


@router.get("/{project_id}/video-localization/source-media/audio")
async def get_video_localization_source_audio(project_id: str):
    audio_path = video_localization_service.source_audio_file(project_id)
    return audio_file_response(audio_path, code="VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND", message="Source audio file not found")


@router.get("/{project_id}/video-localization/operations", response_model=list[VideoLocalizationOperation])
async def list_video_localization_operations(project_id: str):
    operations = operation_queue.list_operations(project_id)
    return require_resource(operations)


@router.get("/{project_id}/video-localization/operations/summaries", response_model=list[VideoLocalizationOperation])
async def list_video_localization_operation_summaries(project_id: str):
    operations = operation_queue.list_operation_summaries(project_id)
    return require_resource(operations)


@router.post("/{project_id}/video-localization/operations", response_model=VideoLocalizationOperation)
async def submit_video_localization_operation(project_id: str, request: VideoLocalizationOperationRequest):
    operation = video_localization_service.submit_operation(project_id, request.kind, request.parameters)
    return require_resource(operation)


@router.get("/{project_id}/video-localization/operations/{operation_id}", response_model=VideoLocalizationOperation)
async def get_video_localization_operation(project_id: str, operation_id: str):
    operation = operation_queue.get_operation(project_id, operation_id)
    return require_resource(operation, code="VIDEO_LOCALIZATION_OPERATION_NOT_FOUND", message="Operation not found")


@router.post("/{project_id}/video-localization/operations/{operation_id}/cancel", response_model=VideoLocalizationOperation)
async def cancel_video_localization_operation(project_id: str, operation_id: str):
    operation = operation_queue.cancel(project_id, operation_id)
    return require_resource(operation)


@router.post("/{project_id}/video-localization/operations/{operation_id}/retry", response_model=VideoLocalizationOperation)
async def retry_video_localization_operation(project_id: str, operation_id: str):
    operation = operation_queue.retry(project_id, operation_id)
    return require_resource(operation)


@router.post("/{project_id}/video-localization/source-audio", response_model=VideoLocalizationDraft)
async def extract_video_localization_source_audio(project_id: str):
    updated = video_localization_service.extract_source_audio(project_id)
    return require_resource(updated)


@router.post("/{project_id}/video-localization/stems", response_model=VideoLocalizationDraft)
async def separate_video_localization_source_audio(project_id: str):
    updated = video_localization_service.separate_source_audio(project_id)
    return require_resource(updated)


@router.get("/{project_id}/video-localization/stems/{kind}/audio")
async def get_video_localization_stem_audio(project_id: str, kind: str):
    audio_path = video_localization_service.stem_audio_file(project_id, kind)
    return audio_file_response(audio_path, code="VIDEO_LOCALIZATION_STEM_AUDIO_NOT_FOUND", message="Stem audio file not found")


@router.post("/{project_id}/video-localization/asr/en", response_model=VideoLocalizationDraft)
async def transcribe_video_localization_english(
    project_id: str,
    source_track_id: Literal["auto", "original", "vocals", "dub"] = Query(default="auto"),
    source_language: Literal["auto", "en", "zh"] = Query(default="auto"),
    segmentation_profile_id: Literal["generic_zh", "short_video_large_text", "conservative_release"] = Query(default="generic_zh"),
):
    updated = await asyncio.to_thread(
        video_localization_service.transcribe_english_source_audio,
        project_id,
        source_track_id=source_track_id,
        source_language=source_language,
        segmentation_profile_id=segmentation_profile_id,
    )
    return require_resource(updated)


@router.post("/{project_id}/video-localization/reference-clips", response_model=VideoLocalizationDraft)
async def create_video_localization_reference_clips(project_id: str, payload: VideoLocalizationReferenceClipCreate | None = Body(default=None)):
    updated = video_localization_service.create_reference_clips_from_cues(project_id, payload)
    return require_resource(updated)


@router.patch("/{project_id}/video-localization/reference-clips/{reference_clip_id}", response_model=VideoLocalizationDraft)
async def update_video_localization_reference_clip(project_id: str, reference_clip_id: str, patch: VideoLocalizationReferenceClipUpdate):
    updated = video_localization_service.update_reference_clip(project_id, reference_clip_id, patch)
    return require_resource(updated)


@router.delete("/{project_id}/video-localization/reference-clips/{reference_clip_id}", response_model=VideoLocalizationDraft)
async def delete_video_localization_reference_clip(project_id: str, reference_clip_id: str):
    updated = video_localization_service.delete_reference_clip(project_id, reference_clip_id)
    return require_resource(updated)


@router.patch("/{project_id}/video-localization/cues/{cue_id}", response_model=VideoLocalizationDraft)
async def update_video_localization_cue(project_id: str, cue_id: str, patch: VideoLocalizationCueUpdate):
    updated = video_localization_service.update_cue(project_id, cue_id, patch)
    return require_resource(updated)


@router.post(
    "/{project_id}/video-localization/cues/{cue_id}/timing-confirmation",
    response_model=VideoLocalizationDraft,
)
async def confirm_video_localization_cue_timing(
    project_id: str,
    cue_id: str,
    request: VideoLocalizationCueTimingConfirmationRequest,
):
    updated = video_localization_service.update_cue(
        project_id,
        cue_id,
        VideoLocalizationCueUpdate(
            confirm_timing=True,
            expected_start_ms=request.start_ms,
            expected_end_ms=request.end_ms,
            timing_confirmation_method=request.confirmation_method,
        ),
    )
    return require_resource(updated)


@router.patch("/{project_id}/video-localization/localized-subtitles/{subtitle_id}", response_model=VideoLocalizationDraft)
async def update_video_localization_localized_subtitle(
    project_id: str,
    subtitle_id: str,
    patch: VideoLocalizationSubtitleCueUpdate,
):
    updated = video_localization_service.update_localized_subtitle(project_id, subtitle_id, patch)
    return require_resource(updated)


@router.post("/{project_id}/video-localization/speakers", response_model=VideoLocalizationDraft)
async def create_video_localization_speaker(project_id: str, payload: VideoLocalizationSpeakerCreate):
    updated = video_localization_service.create_speaker(project_id, payload)
    return require_resource(updated)


@router.patch("/{project_id}/video-localization/speakers/{speaker_id}", response_model=VideoLocalizationDraft)
async def update_video_localization_speaker(project_id: str, speaker_id: str, payload: VideoLocalizationSpeakerUpdate):
    updated = video_localization_service.update_speaker(project_id, speaker_id, payload)
    return require_resource(updated)


@router.post("/{project_id}/video-localization/localize/zh", response_model=VideoLocalizationOperation)
async def generate_video_localization_chinese_draft(project_id: str):
    operation = video_localization_service.submit_operation(project_id, "localization_draft", {})
    return require_resource(operation)


@router.post("/{project_id}/video-localization/tts/batch", response_model=BatchTask)
async def submit_video_localization_tts_batch(project_id: str):
    batch_request = video_localization_service.build_tts_batch_request(project_id)
    batch_request = require_resource(batch_request)
    try:
        batch = await batch_queue.submit(batch_request.model_dump())
        video_localization_service.mark_tts_batch_submitted(project_id, batch.batch_task_id, [segment.segment_id for segment in batch.segments])
        return batch
    except ValueError as exc:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_BATCH_INVALID", str(exc)) from exc


@router.post("/{project_id}/video-localization/tts/handoff/{segment_id}", response_model=GenerateRequest)
async def prepare_video_localization_tts_handoff(project_id: str, segment_id: str):
    request = await asyncio.to_thread(video_localization_service.build_single_tts_handoff, project_id, segment_id)
    return require_resource(request)


@router.post("/{project_id}/video-localization/tts/batch/{batch_task_id}/sync", response_model=VideoLocalizationDraft)
async def sync_video_localization_tts_batch(project_id: str, batch_task_id: str):
    updated = video_localization_service.sync_tts_batch_results(project_id, batch_task_id)
    return require_resource(updated)


@router.get("/{project_id}/video-localization/cues/{cue_id}/tts-audio")
async def get_video_localization_cue_tts_audio(project_id: str, cue_id: str):
    audio_path = video_localization_service.tts_audio_file(project_id, cue_id)
    return audio_file_response(audio_path, code="VIDEO_LOCALIZATION_TTS_AUDIO_NOT_FOUND", message="TTS audio file not found")


@router.get("/{project_id}/video-localization/candidates/{candidate_id}/audio")
async def get_video_localization_candidate_audio(project_id: str, candidate_id: str):
    audio_path = video_localization_service.generated_candidate_audio_file(project_id, candidate_id)
    return audio_file_response(audio_path, code="VIDEO_LOCALIZATION_CANDIDATE_AUDIO_NOT_FOUND", message="Generated candidate audio not found")


@router.post("/{project_id}/video-localization/candidates/{candidate_id}/apply", response_model=VideoLocalizationDraft)
async def apply_video_localization_candidate(project_id: str, candidate_id: str):
    updated = video_localization_service.apply_generated_candidate(project_id, candidate_id)
    return require_resource(updated)


@router.get("/{project_id}/video-localization/timeline-clips/{clip_id}/audio")
async def get_video_localization_timeline_clip_audio(project_id: str, clip_id: str):
    audio_path = video_localization_service.timeline_clip_audio_file(project_id, clip_id)
    return audio_file_response(audio_path, code="VIDEO_LOCALIZATION_TIMELINE_CLIP_AUDIO_NOT_FOUND", message="Timeline clip audio not found")


@router.post("/{project_id}/video-localization/timeline-clips/{clip_id}/history/{result_id}/apply", response_model=VideoLocalizationDraft)
async def apply_video_localization_history_to_timeline_clip(project_id: str, clip_id: str, result_id: str):
    updated = video_localization_service.apply_tts_history_to_timeline_clip(project_id, clip_id, result_id)
    return require_resource(updated)


@router.post("/{project_id}/video-localization/timeline-clips/history/{result_id}/apply", response_model=VideoLocalizationDraft)
async def apply_video_localization_history_to_timeline(
    project_id: str,
    result_id: str,
    payload: TtsHistoryTimelineApplyRequest,
):
    updated = video_localization_service.apply_tts_history_to_timeline(
        project_id,
        result_id,
        segment_id=payload.segment_id,
        clip_id=payload.clip_id,
        start_ms=payload.start_ms,
        dub_lane=payload.dub_lane,
        force_new=payload.force_new,
    )
    return require_resource(updated)


@router.get("/{project_id}/video-localization/timeline-clips/{clip_id}/waveform", response_model=WaveformPeaksResponse)
async def get_video_localization_timeline_clip_waveform(
    project_id: str,
    clip_id: str,
    bins: int | None = Query(default=None, ge=waveform_cache.MIN_BINS, le=waveform_cache.MAX_BINS),
):
    audio_path = video_localization_service.timeline_clip_audio_file(project_id, clip_id)
    if not audio_path:
        raise AppException(404, "VIDEO_LOCALIZATION_TIMELINE_CLIP_AUDIO_NOT_FOUND", "Timeline clip audio not found")
    cache_id = f"video-localization-{project_id}-{clip_id}"
    return await asyncio.to_thread(
        waveform_cache.waveform_peaks,
        audio_path,
        result_id=cache_id,
        bins=bins,
        max_bins=waveform_cache.MAX_BINS,
    )


@router.get("/{project_id}/video-localization/cues/{cue_id}/source-audio")
async def get_video_localization_cue_source_audio(project_id: str, cue_id: str):
    audio_path = video_localization_service.source_cue_audio_file(project_id, cue_id)
    return audio_file_response(audio_path, code="VIDEO_LOCALIZATION_SOURCE_CUE_AUDIO_NOT_FOUND", message="Source cue audio file not found")


@router.get("/{project_id}/video-localization/reference-clips/{reference_clip_id}/audio")
async def get_video_localization_reference_clip_audio(project_id: str, reference_clip_id: str):
    audio_path = video_localization_service.reference_clip_audio_file(project_id, reference_clip_id)
    return audio_file_response(audio_path, code="VIDEO_LOCALIZATION_REFERENCE_AUDIO_NOT_FOUND", message="Reference audio file not found")


@router.get("/{project_id}/video-localization/reference-clips/{reference_clip_id}/cover")
async def get_video_localization_reference_clip_cover(project_id: str, reference_clip_id: str):
    cover_path = video_localization_service.reference_clip_cover_file(project_id, reference_clip_id)
    return audio_file_response(cover_path, code="VIDEO_LOCALIZATION_REFERENCE_COVER_NOT_FOUND", message="Reference cover frame not found")


@router.get("/{project_id}/video-localization/subtitles/{kind}")
async def export_video_localization_subtitles(project_id: str, kind: str):
    srt = video_localization_service.export_subtitles(project_id, kind)
    srt = require_resource(srt)
    filename = f"{project_id}-video-localization-{kind}.srt"
    return srt_attachment(srt, filename=filename)


@router.delete("/{project_id}/video-localization/subtitles/{kind}", response_model=VideoLocalizationDraft)
async def clear_video_localization_subtitles(project_id: str, kind: Literal["en", "zh"]):
    updated = video_localization_service.clear_subtitles(project_id, kind)
    return require_resource(updated)


@router.post("/{project_id}/video-localization/subtitles/{kind}/import", response_model=VideoLocalizationDraft)
async def import_video_localization_subtitles(project_id: str, kind: str, request: VideoLocalizationSubtitleImportRequest):
    updated = video_localization_service.import_subtitles(project_id, kind, request)
    return require_resource(updated)


@router.get("/{project_id}/video-localization/export")
async def export_video_localization(project_id: str):
    data = video_localization_service.export_video_localization(project_id)
    data = require_resource(data)
    filename = f"{project_id}-video-localization.json"
    return json_attachment(data, filename=filename)


@router.get("/{project_id}/video-localization/export/timeline")
async def export_video_localization_timeline(project_id: str):
    data = video_localization_service.export_timeline_edl(project_id)
    data = require_resource(data)
    filename = f"{project_id}-video-localization-edl.json"
    return json_attachment(data, filename=filename)


@router.get("/{project_id}/video-localization/export/timeline/audio-package")
async def export_video_localization_timeline_audio_package(project_id: str):
    path = video_localization_service.export_timeline_audio_package(project_id)
    path = require_resource(path)
    return download_file_response(
        path,
        filename=f"{project_id}-video-localization-audio-package.zip",
        code="VIDEO_LOCALIZATION_AUDIO_PACKAGE_NOT_FOUND",
        message="Timeline audio package not found",
    )


@router.get("/{project_id}/video-localization/export/timeline/video")
async def export_video_localization_timeline_video(project_id: str):
    path = video_localization_service.export_localized_video(project_id)
    path = require_resource(path)
    return download_file_response(
        path,
        filename=f"{project_id}-video-localization-localized-video.mp4",
        code="VIDEO_LOCALIZATION_LOCALIZED_VIDEO_NOT_FOUND",
        message="Localized video not found",
    )


@router.get("/{project_id}/video-localization/readiness")
async def export_video_localization_readiness(project_id: str):
    data = video_localization_service.production_readiness_audit(project_id)
    data = require_resource(data)
    filename = f"{project_id}-video-localization-readiness.json"
    return json_attachment(data, filename=filename)
