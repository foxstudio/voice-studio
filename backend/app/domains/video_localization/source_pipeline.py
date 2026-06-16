from __future__ import annotations

from pathlib import Path

from fastapi import UploadFile

from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization import media_assets
from app.errors import AppException
from app.schemas.voice_studio import VideoLocalizationDraft, now_iso
from app.services import asr_service


async def with_imported_source_media(project_id: str, draft: VideoLocalizationDraft, file: UploadFile) -> VideoLocalizationDraft:
    source_path, content = await media_assets.save_uploaded_video(project_id, file)
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
    return draft.model_copy(update={"source_media": source_media, "status": "draft"})


def with_extracted_source_audio(project_id: str, draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
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
    return draft.model_copy(update={"source_media": source_media, "stems": stems})


def with_separated_source_audio(project_id: str, draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
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
    return draft.model_copy(update={"stems": stems})


def with_english_asr(draft: VideoLocalizationDraft, engine_id: str = "faster-whisper-turbo") -> VideoLocalizationDraft:
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
    return draft.model_copy(update={"source_media": source_media, "cues": preserved_cues + generated_cues})
