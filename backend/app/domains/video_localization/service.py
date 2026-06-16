from __future__ import annotations

from pathlib import Path

from fastapi import UploadFile

from app.domains.video_localization import audio_access
from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization import draft_store
from app.domains.video_localization import localization
from app.domains.video_localization import media_assets
from app.domains.video_localization import reference_clips
from app.domains.video_localization.readiness import build_production_readiness_audit
from app.domains.video_localization import source_pipeline
from app.domains.video_localization import tts_pipeline
from app.domains.video_localization import subtitles
from app.errors import AppException
from app.schemas.voice_studio import (
    BatchGenerateRequest,
    VideoLocalizationCueUpdate,
    VideoLocalizationDraft,
    VideoLocalizationExport,
    VideoLocalizationReferenceClipUpdate,
    now_iso,
)
from app.services import batch_queue, project_store

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
    return save_video_localization(project_id, reference_clips.with_reference_clips_from_cues(project_id, draft))


def update_reference_clip(project_id: str, reference_clip_id: str, patch: VideoLocalizationReferenceClipUpdate) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, reference_clips.with_updated_reference_clip(draft, reference_clip_id, patch))


def update_cue(project_id: str, cue_id: str, patch: VideoLocalizationCueUpdate) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, cue_tools.with_updated_cue(draft, cue_id, patch))


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
    return tts_pipeline.build_batch_request(
        project_id=project_id,
        project_name=project.name,
        draft=draft,
        output_dir=media_assets.project_video_localization_dir(project_id) / "tts",
        engine_id=engine_id,
    )


def mark_tts_batch_submitted(project_id: str, batch_task_id: str, cue_ids: list[str]) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = tts_pipeline.with_batch_submitted(draft, batch_task_id, cue_ids, attempted_at=now_iso())
    if next_draft is draft:
        return draft
    return save_video_localization(project_id, next_draft)


def export_subtitles(project_id: str, kind: str) -> str | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return subtitles.export_srt(draft, kind)


def sync_tts_batch_results(project_id: str, batch_task_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    batch = batch_queue.get_batch(batch_task_id)
    if not batch:
        raise AppException(404, "VIDEO_LOCALIZATION_TTS_BATCH_NOT_FOUND", "TTS batch task not found")
    request_parameters = batch.parameters.get("parameters") if isinstance(batch.parameters, dict) else None
    if not isinstance(request_parameters, dict) or request_parameters.get("source") != "video_localization" or request_parameters.get("project_id") != project_id:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_BATCH_PROJECT_MISMATCH", "Batch task does not belong to this video localization project")

    return save_video_localization(project_id, tts_pipeline.with_synced_batch_results(draft, batch))


def sync_single_tts_result(project_id: str, cue_id: str, *, result_id: str, output_path: str, duration_ms: int | None) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, tts_pipeline.with_single_tts_result(draft, cue_id, result_id=result_id, output_path=output_path, duration_ms=duration_ms))


def tts_audio_file(project_id: str, cue_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.tts_audio_path(draft, cue_id)


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
    next_draft = draft_store.save(project_id, draft, updated_at=draft.updated_at)
    if not next_draft:
        return None
    summary = {
        "cue_count": len(next_draft.cues),
        "ready_cue_count": sum(1 for cue in next_draft.cues if cue.review_status in {"ready", "locked"}),
        "blocker_count": len(next_draft.quality_gate.blockers),
        "warning_count": len(next_draft.quality_gate.warnings),
    }
    return VideoLocalizationExport(
        project_id=project.project_id,
        project_name=project.name,
        exported_at=now_iso(),
        export_summary=summary,
        **next_draft.model_dump(),
    )


def production_readiness_audit(project_id: str) -> dict | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    next_draft = draft_store.with_fresh_gate(draft, updated_at=draft.updated_at)
    return build_production_readiness_audit(project_id=project.project_id, project_name=project.name, draft=next_draft)
