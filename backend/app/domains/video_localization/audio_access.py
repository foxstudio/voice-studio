from __future__ import annotations

from pathlib import Path

from app.domains.video_localization import (
    media_assets,
    media_health,
    timeline_audio_sources,
)
from app.domains.video_localization import tts_pipeline
from app.domains.video_localization.schemas import VideoLocalizationDraft


def source_video_path(draft: VideoLocalizationDraft) -> Path | None:
    return media_health.inspect_project_media(draft).paths.source_video


def source_audio_path(draft: VideoLocalizationDraft) -> Path | None:
    return media_health.inspect_project_media(draft).paths.source_audio


def stem_audio_path(draft: VideoLocalizationDraft, kind: str) -> Path | None:
    paths = media_health.inspect_project_media(draft).paths
    if kind == "vocals":
        return paths.vocals
    if kind == "background":
        return paths.background
    return None


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
            if dict(item).get("clip_id") == clip_id
        ),
        None,
    )
    if clip is None:
        clip = next(
            (
                dict(item)
                for item in draft.timeline_clips
                if dict(item).get("media_source_clip_id") == clip_id
            ),
            None,
        )
    if not clip:
        return None
    return timeline_audio_sources.resolve_dub_clip_audio_path(
        draft,
        clip,
    )


def reference_clip_audio_path(draft: VideoLocalizationDraft, reference_clip_id: str) -> Path | None:
    clip = next((item for item in draft.reference_clips if item.reference_clip_id == reference_clip_id), None)
    if not clip or not clip.audio_path:
        return None
    path = Path(clip.audio_path)
    return path.resolve() if path.is_file() else None


def source_cue_audio_path(project_id: str, draft: VideoLocalizationDraft, cue_id: str) -> Path | None:
    cue = next((item for item in draft.cues if item.cue_id == cue_id), None)
    if not cue or cue.start_ms is None or cue.end_ms is None or cue.end_ms <= cue.start_ms:
        return None
    resolution = media_health.inspect_project_media(
        draft,
        package_root=media_assets.project_video_localization_dir(project_id),
    )
    source_path = resolution.paths.vocals or resolution.paths.source_audio
    if source_path is None:
        return None
    cache_dir = media_assets.project_video_localization_dir(project_id) / "cue-source-audio"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = media_assets.source_cue_cache_path(cache_dir, source_path, cue)
    if not destination.exists():
        media_assets.cut_audio_clip(source_path, destination, cue.start_ms, cue.end_ms)
    return destination if destination.is_file() else None
