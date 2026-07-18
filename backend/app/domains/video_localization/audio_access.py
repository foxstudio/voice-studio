from __future__ import annotations

from pathlib import Path

from app.domains.video_localization import media_assets
from app.domains.video_localization import tts_pipeline
from app.domains.video_localization.schemas import VideoLocalizationDraft


def source_video_path(draft: VideoLocalizationDraft) -> Path | None:
    if not draft.source_media.video_path:
        return None
    path = Path(draft.source_media.video_path)
    return path if path.exists() else None


def source_audio_path(draft: VideoLocalizationDraft) -> Path | None:
    for source_value in (draft.source_media.audio_path, draft.stems.original_audio_path):
        if not source_value:
            continue
        path = Path(source_value)
        if path.exists():
            return path
    return None


def stem_audio_path(draft: VideoLocalizationDraft, kind: str) -> Path | None:
    if kind == "vocals":
        value = draft.stems.vocals_clean_path
    elif kind == "background":
        value = draft.stems.background_path
    else:
        return None
    if not value:
        return None
    path = Path(value)
    return path if path.exists() else None


def tts_audio_path(draft: VideoLocalizationDraft, cue_id: str) -> Path | None:
    return tts_pipeline.tts_audio_path(draft, cue_id)


def timeline_clip_audio_path(draft: VideoLocalizationDraft, clip_id: str) -> Path | None:
    if clip_id == "media_original":
        return source_audio_path(draft)
    if clip_id == "media_vocals":
        return stem_audio_path(draft, "vocals")
    if clip_id == "media_background":
        return stem_audio_path(draft, "background")
    clip = next(
        (
            dict(item)
            for item in draft.timeline_clips
            if dict(item).get("clip_id") == clip_id or dict(item).get("media_source_clip_id") == clip_id
        ),
        None,
    )
    if not clip or not clip.get("audio_path"):
        return None
    path = Path(str(clip["audio_path"]))
    return path if path.exists() else None


def reference_clip_audio_path(draft: VideoLocalizationDraft, reference_clip_id: str) -> Path | None:
    clip = next((item for item in draft.reference_clips if item.reference_clip_id == reference_clip_id), None)
    if not clip or not clip.audio_path:
        return None
    path = Path(clip.audio_path)
    return path if path.exists() else None


def source_cue_audio_path(project_id: str, draft: VideoLocalizationDraft, cue_id: str) -> Path | None:
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
