from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from app.domains.video_localization import tts_pipeline
from app.domains.video_localization.schemas import VideoLocalizationDraft


def resolve_dub_clip_audio_paths(
    draft: VideoLocalizationDraft,
    clips: Iterable[Mapping[str, object]] | None = None,
) -> dict[str, Path]:
    """Resolve every dub clip through its stable timeline media source."""

    timeline_clips = [
        dict(item)
        for item in draft.timeline_clips
        if dict(item).get("track_id") == "dub"
    ]
    source_paths: dict[str, Path] = {}
    for clip in timeline_clips:
        path = _existing_file(clip.get("audio_path"))
        if path is None:
            continue
        clip_id = _identifier(clip.get("clip_id"))
        media_source_clip_id = _identifier(
            clip.get("media_source_clip_id")
        )
        if clip_id:
            source_paths[clip_id] = path
        if media_source_clip_id:
            source_paths[media_source_clip_id] = path

    candidates = (
        timeline_clips
        if clips is None
        else [dict(item) for item in clips]
    )
    resolved: dict[str, Path] = {}
    for clip in candidates:
        clip_id = _identifier(clip.get("clip_id"))
        if not clip_id:
            continue
        path = (
            _existing_file(clip.get("audio_path"))
            or source_paths.get(clip_id)
            or source_paths.get(
                _identifier(clip.get("media_source_clip_id"))
            )
            or _fallback_tts_audio_path(draft, clip)
        )
        if path is not None and path.is_file():
            resolved[clip_id] = path.resolve()
    return resolved


def resolve_dub_clip_audio_path(
    draft: VideoLocalizationDraft,
    clip: Mapping[str, object],
) -> Path | None:
    clip_id = _identifier(clip.get("clip_id"))
    if not clip_id:
        return None
    return resolve_dub_clip_audio_paths(draft, [clip]).get(clip_id)


def _fallback_tts_audio_path(
    draft: VideoLocalizationDraft,
    clip: Mapping[str, object],
) -> Path | None:
    source_ids = [
        *[
            _identifier(value)
            for value in (clip.get("target_subtitle_ids") or [])
        ],
        _identifier(clip.get("subtitle_id")),
        _identifier(clip.get("cue_id")),
    ]
    for source_id in source_ids:
        if not source_id:
            continue
        path = tts_pipeline.tts_audio_path(draft, source_id)
        if path is not None and path.is_file():
            return path.resolve()
    return None


def _existing_file(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip())
    return path.resolve() if path.is_file() else None


def _identifier(value: object) -> str:
    return str(value or "").strip()


__all__ = [
    "resolve_dub_clip_audio_path",
    "resolve_dub_clip_audio_paths",
]
