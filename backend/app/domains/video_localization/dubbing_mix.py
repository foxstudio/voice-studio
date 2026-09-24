"""Dubbing mix defaults shared by timeline placement and export.

配音片段正式进入时间线以后，原音轨与人声轨应当静音、背景声与合成配音保持有声。
导出混音、页面预览和落轨流程必须引用同一组默认值，避免三处各写一份。
"""

from __future__ import annotations

from typing import Any

from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.domains.video_localization.timeline_clip_ownership import (
    is_provisional_timeline_clip,
)

TrackState = dict[str, float | bool]

DEFAULT_DUBBING_TRACK_STATES: dict[str, TrackState] = {
    "original": {"muted": True, "solo": False, "volume": 1.0},
    "vocals": {"muted": True, "solo": False, "volume": 1.0},
    "background": {"muted": False, "solo": False, "volume": 1.0},
    "dub": {"muted": False, "solo": False, "volume": 1.0},
}


def default_dubbing_track_states() -> dict[str, TrackState]:
    """Return a mutable copy of the shared dubbing mix defaults."""

    return {
        track_id: dict(state)
        for track_id, state in DEFAULT_DUBBING_TRACK_STATES.items()
    }


def has_placed_dubbing_clip(draft: VideoLocalizationDraft) -> bool:
    """Return whether the timeline already owns a durable dubbing clip."""

    return any(
        str(dict(clip).get("track_id") or "dub") == "dub"
        and not is_provisional_timeline_clip(clip)
        for clip in draft.timeline_clips
    )


def ensure_initial_dubbing_mix(
    draft: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Write the usable mix once, before the user has arranged the mix themselves.

    The first durable dubbing clip makes the original audio and the separated
    vocals redundant; leaving them audible would double the dialogue. An
    existing ``track_states`` value means either the user already arranged the
    mix or this default was written earlier, and must stay untouched.
    """

    ui_state = dict(draft.ui_state or {})
    if ui_state.get("track_states"):
        return draft
    if not has_placed_dubbing_clip(draft):
        return draft
    return draft.model_copy(
        update={
            "ui_state": {
                **ui_state,
                "track_states": default_dubbing_track_states(),
            }
        }
    )


__all__ = [
    "DEFAULT_DUBBING_TRACK_STATES",
    "default_dubbing_track_states",
    "ensure_initial_dubbing_mix",
    "has_placed_dubbing_clip",
]
