from __future__ import annotations

from pathlib import Path

from fastapi import UploadFile

from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization import media_assets
from app.domains.video_localization.quality_gate import evaluate_quality_gate
from app.domains.video_localization import reference_clips
from app.domains.video_localization.readiness import build_production_readiness_audit
from app.domains.video_localization import tts_pipeline
from app.domains.video_localization import subtitles
from app.errors import AppException
from app.schemas.voice_studio import (
    BatchGenerateRequest,
    VideoLocalizationCue,
    VideoLocalizationCueUpdate,
    VideoLocalizationDraft,
    VideoLocalizationExport,
    VideoLocalizationReferenceClipUpdate,
    now_iso,
)
from app.services import asr_service, audio_tools, batch_queue, project_store, text_normalizer

VIDEO_LOCALIZATION_KEY = "video_localization"


def get_video_localization(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    raw = project.parameters.get(VIDEO_LOCALIZATION_KEY) or {}
    return VideoLocalizationDraft(**raw)


def save_video_localization(project_id: str, draft: VideoLocalizationDraft) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    next_draft = _with_fresh_gate(draft, updated_at=now_iso())
    project.parameters = {**project.parameters, VIDEO_LOCALIZATION_KEY: next_draft.model_dump()}
    project_store.save_project(project)
    return next_draft


async def import_source_media(project_id: str, file: UploadFile) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    source_path, content = await media_assets.save_uploaded_video(project_id, file)
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    source_media = draft.source_media.model_copy(
        update={
            "filename": file.filename or source_path.name,
            "video_path": str(source_path),
            "size_bytes": len(content),
            "imported_at": now_iso(),
            "metadata": {
                **draft.source_media.metadata,
                "content_type": file.content_type,
                "upload_status": "stored",
            },
        }
    )
    next_draft = draft.model_copy(update={"source_media": source_media, "status": "draft"})
    return save_video_localization(project_id, next_draft)


def extract_source_audio(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    if not draft.source_media.video_path:
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_MISSING", "Import a source video before extracting audio")

    video_path = Path(draft.source_media.video_path)
    if not video_path.exists():
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_NOT_FOUND", "Source video file is missing")

    audio_dir = media_assets.project_video_localization_dir(project_id) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = media_assets.unique_path(audio_dir / f"{video_path.stem}-source.wav")
    audio_meta = media_assets.extract_audio_file(video_path, audio_path)
    source_media = draft.source_media.model_copy(
        update={
            "audio_path": str(audio_path),
            "duration_ms": draft.source_media.duration_ms or audio_meta.get("duration_ms"),
            "metadata": {
                **draft.source_media.metadata,
                "audio_extract_status": "completed",
                "audio_sample_rate": audio_meta.get("sample_rate"),
                "audio_channels": audio_meta.get("channels"),
            },
        }
    )
    stems = draft.stems.model_copy(update={"original_audio_path": str(audio_path)})
    return save_video_localization(project_id, draft.model_copy(update={"source_media": source_media, "stems": stems}))


def separate_source_audio(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    audio_path_value = draft.source_media.audio_path or draft.stems.original_audio_path
    if not audio_path_value:
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING", "Extract source audio before running stem separation")

    audio_path = Path(audio_path_value)
    if not audio_path.exists():
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND", "Source audio file is missing")

    stems_dir = media_assets.project_video_localization_dir(project_id) / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    separation = media_assets.separate_audio_file(audio_path, stems_dir)
    stems = draft.stems.model_copy(
        update={
            "vocals_clean_path": str(separation["vocals_clean_path"]),
            "background_path": str(separation["background_path"]),
            "original_audio_path": str(audio_path),
            "separation_engine_id": separation.get("engine_id", "demucs:htdemucs"),
            "separation_status": "completed",
            "quality_flags": separation.get("quality_flags", []),
        }
    )
    return save_video_localization(project_id, draft.model_copy(update={"stems": stems}))


def transcribe_english_source_audio(project_id: str, engine_id: str = "faster-whisper-turbo") -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    audio_path_value = draft.source_media.audio_path or draft.stems.original_audio_path
    if not audio_path_value:
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING", "Extract source audio before running English ASR")

    audio_path = Path(audio_path_value)
    if not audio_path.exists():
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND", "Source audio file is missing")

    result = asr_service.transcribe(engine_id=engine_id, audio_path=str(audio_path), language="en")
    segments = asr_service.normalize_segments(result.get("segments"))
    generated_cues = cue_tools.from_asr_segments(
        segments=segments,
        fallback_text=str(result.get("text") or "").strip(),
        duration_ms=draft.source_media.duration_ms,
        engine_id=engine_id,
        existing_cue_ids={cue.cue_id for cue in draft.cues},
    )
    if not generated_cues:
        raise AppException(400, "VIDEO_LOCALIZATION_ASR_EMPTY", "English ASR did not return subtitle text")

    preserved_cues = [cue for cue in draft.cues if not cue_tools.is_replaceable_asr_candidate(cue)]
    source_media = draft.source_media.model_copy(
        update={
            "metadata": {
                **draft.source_media.metadata,
                "english_asr_status": "completed",
                "english_asr_engine_id": engine_id,
                "english_asr_segment_count": len(generated_cues),
                "english_asr_completed_at": now_iso(),
            }
        }
    )
    next_draft = draft.model_copy(update={"source_media": source_media, "cues": preserved_cues + generated_cues})
    return save_video_localization(project_id, next_draft)


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
    if not draft.cues:
        raise AppException(400, "VIDEO_LOCALIZATION_CUES_MISSING", "Create English ASR cues before generating Chinese localization draft")

    changed = False
    next_cues: list[VideoLocalizationCue] = []
    for cue in draft.cues:
        patch: dict[str, object] = {}
        flags = list(cue.quality_flags)
        zh_text = (cue.zh_localized_subtitle_text or "").strip()
        if not zh_text:
            source_text = (cue.en_subtitle_text or "").strip()
            if not source_text:
                next_cues.append(cue)
                continue
            zh_text = f"【待本土化】{source_text}"
            patch["zh_localized_subtitle_text"] = zh_text
            flags = cue_tools.add_flags(flags, ["localization_draft", "needs_human_localization"])
            changed = True

        if not (cue.tts_recommended_text or "").strip():
            patch["tts_recommended_text"] = text_normalizer.normalize_spoken_numbers(zh_text)
            flags = cue_tools.add_flags(flags, ["tts_text_normalized"])
            changed = True

        if patch:
            patch["quality_flags"] = flags
            next_cues.append(cue.model_copy(update=patch))
        else:
            next_cues.append(cue)

    if not changed:
        raise AppException(400, "VIDEO_LOCALIZATION_LOCALIZATION_UNCHANGED", "All cues already have Chinese subtitle and TTS text")
    return save_video_localization(project_id, draft.model_copy(update={"cues": next_cues}))


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
    return tts_pipeline.tts_audio_path(draft, cue_id)


def reference_clip_audio_file(project_id: str, reference_clip_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    clip = next((item for item in draft.reference_clips if item.reference_clip_id == reference_clip_id), None)
    if not clip or not clip.audio_path:
        return None
    path = Path(clip.audio_path)
    return path if path.exists() else None


def source_cue_audio_file(project_id: str, cue_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    cue = next((item for item in draft.cues if item.cue_id == cue_id), None)
    if not cue or cue.start_ms is None or cue.end_ms is None or cue.end_ms <= cue.start_ms:
        return None
    source_value = draft.stems.vocals_clean_path or draft.stems.original_audio_path or draft.source_media.audio_path
    if not source_value:
        return None
    source_path = Path(source_value)
    if not source_path.exists():
        return None
    cache_dir = media_assets.project_video_localization_dir(project_id) / "cue-source-audio"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = media_assets.source_cue_cache_path(cache_dir, source_path, cue)
    if not destination.exists():
        media_assets.cut_audio_clip(source_path, destination, cue.start_ms, cue.end_ms)
    return destination if destination.exists() else None


def export_video_localization(project_id: str) -> VideoLocalizationExport | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    next_draft = _with_fresh_gate(draft, updated_at=draft.updated_at)
    project.parameters = {**project.parameters, VIDEO_LOCALIZATION_KEY: next_draft.model_dump()}
    project_store.save_project(project)
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
    next_draft = _with_fresh_gate(draft, updated_at=draft.updated_at)
    return build_production_readiness_audit(project_id=project.project_id, project_name=project.name, draft=next_draft)


def _with_fresh_gate(draft: VideoLocalizationDraft, updated_at: str | None) -> VideoLocalizationDraft:
    gate = evaluate_quality_gate(draft)
    status = _status_for_gate(draft, gate.status)
    return draft.model_copy(update={"quality_gate": gate, "status": status, "updated_at": updated_at})


def _status_for_gate(draft: VideoLocalizationDraft, gate_status: str) -> str:
    if gate_status == "blocked":
        return "blocked"
    if draft.status in {"tts_running", "candidate"}:
        return draft.status
    if gate_status == "pass" and draft.cues:
        return "ready_for_tts"
    if draft.cues:
        return "reviewing"
    return "draft"
