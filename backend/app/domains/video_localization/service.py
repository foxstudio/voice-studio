from __future__ import annotations

from pathlib import Path

from fastapi import UploadFile

from app.domains.video_localization import audio_access
from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization import draft_store
from app.domains.video_localization import exporting
from app.domains.video_localization import localization
from app.domains.video_localization import reference_clips
from app.domains.video_localization import speakers
from app.domains.video_localization import source_pipeline
from app.domains.video_localization import tts_orchestration
from app.domains.video_localization import tts_pipeline
from app.errors import AppException
from app.domains.video_localization.schemas import (
    BatchGenerateRequest,
    VideoLocalizationCueUpdate,
    VideoLocalizationDraft,
    VideoLocalizationExport,
    VideoLocalizationReferenceClipUpdate,
    VideoLocalizationSpeakerCreate,
    VideoLocalizationSpeakerUpdate,
)
from app.services import project_store

VIDEO_LOCALIZATION_KEY = draft_store.VIDEO_LOCALIZATION_KEY


def get_video_localization(project_id: str) -> VideoLocalizationDraft | None:
    return draft_store.get(project_id)


def save_video_localization(project_id: str, draft: VideoLocalizationDraft) -> VideoLocalizationDraft | None:
    return draft_store.save(project_id, draft)


async def import_source_media(project_id: str, file: UploadFile) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, await source_pipeline.with_imported_source_media(project_id, draft, file))


def extract_source_audio(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, source_pipeline.with_extracted_source_audio(project_id, draft))


def separate_source_audio(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, source_pipeline.with_separated_source_audio(project_id, draft))


def transcribe_english_source_audio(project_id: str, engine_id: str = "faster-whisper-turbo") -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, source_pipeline.with_english_asr(draft, engine_id))


def create_reference_clips_from_cues(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = reference_clips.with_reference_clips_from_cues(project_id, draft)
    return save_video_localization(project_id, speakers.reconcile_speakers(next_draft))


def update_reference_clip(project_id: str, reference_clip_id: str, patch: VideoLocalizationReferenceClipUpdate) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = reference_clips.with_updated_reference_clip(draft, reference_clip_id, patch)
    return save_video_localization(project_id, speakers.reconcile_speakers(next_draft))


def update_cue(project_id: str, cue_id: str, patch: VideoLocalizationCueUpdate) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = cue_tools.with_updated_cue(draft, cue_id, patch)
    return save_video_localization(project_id, speakers.reconcile_speakers(next_draft))


def create_speaker(project_id: str, payload: VideoLocalizationSpeakerCreate) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, speakers.with_created_speaker(draft, payload))


def update_speaker(project_id: str, speaker_id: str, payload: VideoLocalizationSpeakerUpdate) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, speakers.with_updated_speaker(draft, speaker_id, payload))


def generate_localization_draft(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, localization.with_chinese_draft(draft))


def build_tts_batch_request(project_id: str, engine_id: str = "indextts-v2") -> BatchGenerateRequest | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return tts_orchestration.build_batch_request(
        project_id=project_id,
        project_name=project.name,
        draft=draft,
        engine_id=engine_id,
    )


def mark_tts_batch_submitted(project_id: str, batch_task_id: str, cue_ids: list[str]) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = tts_orchestration.mark_batch_submitted(draft, batch_task_id, cue_ids)
    if next_draft is draft:
        return draft
    return save_video_localization(project_id, next_draft)


def export_subtitles(project_id: str, kind: str) -> str | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return exporting.export_subtitles(draft, kind)


def sync_tts_batch_results(project_id: str, batch_task_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, tts_orchestration.sync_batch_results(project_id, draft, batch_task_id))


def sync_single_tts_result(project_id: str, cue_id: str, *, result_id: str, output_path: str, duration_ms: int | None) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, tts_orchestration.sync_single_result(draft, cue_id, result_id=result_id, output_path=output_path, duration_ms=duration_ms))


def tts_audio_file(project_id: str, cue_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.tts_audio_path(draft, cue_id)


def source_video_file(project_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.source_video_path(draft)


def source_audio_file(project_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.source_audio_path(draft)


def stem_audio_file(project_id: str, kind: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.stem_audio_path(draft, kind)


def reference_clip_audio_file(project_id: str, reference_clip_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.reference_clip_audio_path(draft, reference_clip_id)


def source_cue_audio_file(project_id: str, cue_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.source_cue_audio_path(project_id, draft, cue_id)


def export_video_localization(project_id: str) -> VideoLocalizationExport | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return exporting.export_bundle(project.project_id, project.name, draft)


def production_readiness_audit(project_id: str) -> dict | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return exporting.production_readiness(project.project_id, project.name, draft)
