from __future__ import annotations

from app.domains.video_localization import media_health
from app.domains.video_localization.schemas import VideoLocalizationDraft


_SYSTEM_MEDIA_TRACKS = (
    ("original", "media_original", "source_audio"),
    ("vocals", "media_vocals", "vocals"),
    ("background", "media_background", "background"),
)


def with_available_system_media_tracks(
    draft: VideoLocalizationDraft,
    health: media_health.ProjectMediaHealth,
) -> VideoLocalizationDraft:
    """Project available assets onto default tracks for one workspace read.

    The projection is deterministic and path-free. It never persists defaults
    or changes user-owned mix state. Explicitly disabled tracks stay detached,
    while any saved arrangement remains authoritative for that track.
    """

    raw_disabled_tracks = draft.ui_state.get("disabled_media_tracks", [])
    disabled_tracks = (
        {str(track_id) for track_id in raw_disabled_tracks}
        if isinstance(raw_disabled_tracks, list)
        else set()
    )
    existing_tracks = {
        str(clip.get("track_id") or "")
        for clip in draft.timeline_clips
    }
    duration_ms = max(300, round(draft.source_media.duration_ms or 0))
    additions: list[dict] = []
    for track_id, clip_id, health_field in _SYSTEM_MEDIA_TRACKS:
        asset = getattr(health, health_field)
        if (
            asset.status != "available"
            or not asset.resource_id
            or track_id in disabled_tracks
            or track_id in existing_tracks
        ):
            continue
        additions.append(
            {
                "clip_id": clip_id,
                "media_source_clip_id": clip_id,
                "track_id": track_id,
                "start_ms": 0,
                "end_ms": duration_ms,
                "source_start_ms": 0,
                "source_end_ms": duration_ms,
                "status": "ready",
                "media_clip": True,
            }
        )
    if not additions:
        return draft
    return draft.model_copy(
        update={"timeline_clips": [*draft.timeline_clips, *additions]}
    )


def materialize_referenced_system_media_tracks(
    draft: VideoLocalizationDraft,
    resolution: media_health.ProjectMediaResolution,
    referenced_clip_ids: set[str],
) -> VideoLocalizationDraft:
    """Persist only eligible read-projected system clips used by one command."""

    referenced = {str(clip_id) for clip_id in referenced_clip_ids if clip_id}
    if not referenced:
        return draft
    projected = with_available_system_media_tracks(draft, resolution.health)
    stored_ids = {
        str(clip.get("clip_id") or "")
        for clip in draft.timeline_clips
    }
    path_by_clip_id = {
        "media_original": resolution.paths.source_audio,
        "media_vocals": resolution.paths.vocals,
        "media_background": resolution.paths.background,
    }
    additions = []
    for clip in projected.timeline_clips:
        clip_id = str(clip.get("clip_id") or "")
        if clip_id in stored_ids or clip_id not in referenced:
            continue
        audio_path = path_by_clip_id.get(clip_id)
        if audio_path is None:
            continue
        additions.append({**clip, "audio_path": str(audio_path)})
    if not additions:
        return draft
    return draft.model_copy(
        update={"timeline_clips": [*draft.timeline_clips, *additions]}
    )
