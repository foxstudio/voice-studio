from __future__ import annotations

import math
from typing import Any

from app.schemas.voice_studio import VideoLocalizationDraft


DEFAULT_FRAME_RATE = 30.0
AUDIO_TRACK_IDS = frozenset(
    {"original", "vocals", "background", "dub"}
)


def normalize_audio_clip(
    clip: dict[str, Any],
    *,
    frame_rate: float | int | None,
    timeline_duration_ms: int | None,
) -> dict[str, Any]:
    """Return one frame-aligned clip whose visible and audible spans agree."""

    if str(clip.get("track_id") or "") not in AUDIO_TRACK_IDS:
        return clip
    if clip.get("timeline_timing_version") == "editorial-v1":
        # The bounded editor already validated this placement. Legacy repair
        # must never snap or shorten an explicitly saved millisecond range.
        return clip
    fps = explicit_frame_rate(frame_rate)
    if fps is None:
        # A frame boundary only exists after the source video has supplied
        # its real frame rate. Inventing a default here would move audio in
        # media-less drafts and older projects during an unrelated save.
        return clip
    start_ms = _optional_int(clip.get("start_ms"))
    end_ms = _optional_int(clip.get("end_ms"))
    if start_ms is None or end_ms is None:
        return clip

    timeline_start_frame = frame_index(start_ms, fps)
    timeline_start_ms = frame_time_ms(timeline_start_frame, fps)
    requested_timeline_duration_ms = max(1, end_ms - start_ms)
    source_start_ms = max(
        0,
        _optional_int(clip.get("source_start_ms")) or 0,
    )
    source_end_value = _optional_int(clip.get("source_end_ms"))
    if source_end_value is None:
        requested_timeline_end_frame = max(
            timeline_start_frame + 1,
            frame_index(end_ms, fps),
        )
        requested_timeline_duration_ms = (
            frame_time_ms(requested_timeline_end_frame, fps)
            - timeline_start_ms
        )
        source_duration_ms = requested_timeline_duration_ms
    else:
        # A formally placed audio crop already owns a millisecond-accurate
        # semantic anchor. Snapping each such clip independently to a video
        # frame can create a new overlap even when placement produced a clean
        # single lane, so retain the exact timeline start as well as the exact
        # source crop.
        timeline_start_ms = max(0, start_ms)
        source_duration_ms = max(1, source_end_value - source_start_ms)
    playable_duration_ms = max(
        1,
        min(
            requested_timeline_duration_ms,
            source_duration_ms,
        ),
    )

    normalized = dict(clip)
    normalized.update(
        {
            # Explicit audio placement retains its millisecond semantic anchor
            # and sample-derived crop. Legacy clips without source bounds use
            # the established frame-to-frame projection above.
            "start_ms": timeline_start_ms,
            "end_ms": timeline_start_ms + playable_duration_ms,
            "source_start_ms": source_start_ms,
            "source_end_ms": source_start_ms + playable_duration_ms,
        }
    )
    return normalized


def normalize_draft(
    draft: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    frame_rate = draft.source_media.frame_rate
    duration_ms = draft.source_media.duration_ms
    clips = [
        normalize_audio_clip(
            dict(clip),
            frame_rate=frame_rate,
            timeline_duration_ms=duration_ms,
        )
        for clip in draft.timeline_clips
    ]
    if clips == draft.timeline_clips:
        return draft
    return draft.model_copy(update={"timeline_clips": clips})


def normalize_draft_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    source_media = payload.get("source_media")
    source_payload = (
        source_media if isinstance(source_media, dict) else {}
    )
    clips = payload.get("timeline_clips")
    if not isinstance(clips, list):
        return payload
    normalized = dict(payload)
    normalized["timeline_clips"] = [
        normalize_audio_clip(
            dict(clip),
            frame_rate=source_payload.get("frame_rate"),
            timeline_duration_ms=_optional_int(
                source_payload.get("duration_ms")
            ),
        )
        if isinstance(clip, dict)
        else clip
        for clip in clips
    ]
    return normalized


def normalize_frame_rate(value: float | int | None) -> float:
    return explicit_frame_rate(value) or DEFAULT_FRAME_RATE


def explicit_frame_rate(
    value: float | int | None,
) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 1 or parsed > 240:
        return None
    return parsed


def frame_index(
    time_ms: int | None,
    frame_rate: float,
    *,
    mode: str = "nearest",
) -> int:
    raw = max(0, int(time_ms or 0)) / (1_000 / frame_rate)
    if mode == "floor":
        return math.floor(raw + 1e-7)
    return math.floor(raw + 0.5)


def frame_time_ms(frame: int, frame_rate: float) -> int:
    return math.floor(
        max(0, int(frame)) * (1_000 / frame_rate) + 0.5
    )


def _optional_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
