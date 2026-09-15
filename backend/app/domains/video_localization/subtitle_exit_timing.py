"""Shared display out-point policy for every video-localization subtitle track."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace


DEFAULT_FRAME_RATE = 30.0
POST_SPEECH_HOLD_MS = 500
MINIMUM_GAP_FRAMES = 2
MINIMUM_EVENT_FRAMES = 20
_DEFAULT_LANE = "subtitle-track"


@dataclass(frozen=True, slots=True)
class SubtitleTimingSpan:
    start_ms: int
    end_ms: int
    lane_ids: frozenset[str] = frozenset({_DEFAULT_LANE})

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("subtitle timing span must have a positive range")


def resolve_display_exit_times(
    spans: list[SubtitleTimingSpan],
    *,
    frame_rate: float | None,
    media_duration_ms: int | None = None,
) -> list[SubtitleTimingSpan]:
    """Extend display ends without moving starts or shortening existing ends."""

    if not spans:
        return []
    fps = _valid_frame_rate(frame_rate)
    minimum_gap_ms = math.ceil(MINIMUM_GAP_FRAMES * 1_000 / fps)
    minimum_event_ms = math.ceil(
        MINIMUM_EVENT_FRAMES * 1_000 / fps
    )
    media_end = (
        int(media_duration_ms)
        if media_duration_ms is not None and int(media_duration_ms) > 0
        else None
    )
    resolved = list(spans)
    next_start_by_lane: dict[str, int] = {}
    ordered = sorted(
        enumerate(spans),
        key=lambda item: (
            item[1].start_ms,
            item[1].end_ms,
            item[0],
        ),
        reverse=True,
    )
    for original_index, span in ordered:
        lanes = span.lane_ids or frozenset({_DEFAULT_LANE})
        next_starts = [
            next_start_by_lane[lane]
            for lane in lanes
            if lane in next_start_by_lane
        ]
        target_end = max(
            span.end_ms + POST_SPEECH_HOLD_MS,
            span.start_ms + minimum_event_ms,
        )
        cap = target_end
        if media_end is not None:
            cap = min(cap, media_end)
        if next_starts:
            cap = min(cap, min(next_starts) - minimum_gap_ms)
        resolved_end = max(span.end_ms, cap)
        resolved[original_index] = replace(span, end_ms=resolved_end)
        for lane in lanes:
            next_start_by_lane[lane] = min(
                span.start_ms,
                next_start_by_lane.get(lane, span.start_ms),
            )
    return resolved


def _valid_frame_rate(value: float | None) -> float:
    try:
        frame_rate = float(value or 0)
    except (TypeError, ValueError):
        return DEFAULT_FRAME_RATE
    if not math.isfinite(frame_rate) or frame_rate <= 0:
        return DEFAULT_FRAME_RATE
    return frame_rate


__all__ = [
    "DEFAULT_FRAME_RATE",
    "MINIMUM_EVENT_FRAMES",
    "MINIMUM_GAP_FRAMES",
    "POST_SPEECH_HOLD_MS",
    "SubtitleTimingSpan",
    "resolve_display_exit_times",
]
