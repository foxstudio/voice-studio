"""Shared user-facing timeline position formatting.

Milliseconds remain the internal storage and API unit. Human-readable task
details use compact Chinese hour/minute/second/frame labels instead.
"""

from __future__ import annotations

import math
import re


DEFAULT_FRAME_RATE = 30.0
_MILLISECOND_RANGE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<start>\d[\d,]*)\s*(?:-|–|—|~|～|至|到)\s*"
    r"(?P<end>\d[\d,]*)\s*(?:ms|毫秒)(?![A-Za-z_])",
    re.IGNORECASE,
)
_MILLISECOND_VALUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?P<value>\d[\d,]*)\s*(?:ms|毫秒)(?![A-Za-z_])",
    re.IGNORECASE,
)
_SECOND_RANGE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<start>\d+(?:\.\d+)?)\s*s\s*"
    r"(?:-|–|—|~|～|至|到)\s*"
    r"(?P<end>\d+(?:\.\d+)?)\s*s(?![A-Za-z_])",
    re.IGNORECASE,
)
_SECOND_VALUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?P<value>\d+(?:\.\d+)?)\s*s(?![A-Za-z_])",
    re.IGNORECASE,
)


def normalize_frame_rate(value: object) -> float:
    try:
        frame_rate = float(value)
    except (TypeError, ValueError):
        return DEFAULT_FRAME_RATE
    if not math.isfinite(frame_rate) or frame_rate <= 0:
        return DEFAULT_FRAME_RATE
    return frame_rate


def format_timeline_position(
    value_ms: object,
    *,
    frame_rate: object = DEFAULT_FRAME_RATE,
) -> str:
    """Format one non-negative timeline position as compact Chinese units."""

    try:
        total_ms = max(0, int(value_ms or 0))
    except (TypeError, ValueError):
        total_ms = 0
    fps = normalize_frame_rate(frame_rate)
    nominal_fps = max(1, round(fps))
    whole_seconds, remainder_ms = divmod(total_ms, 1_000)
    frame = round(remainder_ms * fps / 1_000)
    if frame >= nominal_fps:
        whole_seconds += 1
        frame = 0

    hours, remainder_seconds = divmod(whole_seconds, 3_600)
    minutes, seconds = divmod(remainder_seconds, 60)
    parts = []
    if hours:
        parts.append(f"{hours}时")
    if minutes or (hours and (seconds or frame)):
        parts.append(f"{minutes}分")
    if seconds:
        parts.append(f"{seconds}秒")
    if frame:
        parts.append(f"{frame}帧")
    return "".join(parts) or "0帧"


def format_timeline_range(
    start_ms: object,
    end_ms: object,
    *,
    frame_rate: object = DEFAULT_FRAME_RATE,
) -> str:
    try:
        start_value = max(0, int(start_ms or 0))
        end_value = max(start_value, int(end_ms or 0))
    except (TypeError, ValueError):
        return ""
    return (
        f"{format_timeline_position(start_value, frame_rate=frame_rate)}"
        " – "
        f"{format_timeline_position(end_value, frame_rate=frame_rate)}"
    )


def format_timeline_duration(
    value_ms: object,
    *,
    frame_rate: object = DEFAULT_FRAME_RATE,
) -> str:
    try:
        duration_ms = max(0, int(value_ms or 0))
    except (TypeError, ValueError):
        duration_ms = 0
    fps = normalize_frame_rate(frame_rate)
    if 0 < duration_ms < 1_000 / fps:
        return "<1帧"
    return format_timeline_position(duration_ms, frame_rate=fps)


def format_timeline_mentions(
    value: object,
    *,
    frame_rate: object = DEFAULT_FRAME_RATE,
) -> str:
    """Replace user-facing millisecond mentions in legacy or model text."""

    if not isinstance(value, str) or not value:
        return "" if value is None else str(value)

    def milliseconds(raw: str) -> int:
        return int(raw.replace(",", ""))

    def replace_range(match: re.Match[str]) -> str:
        return format_timeline_range(
            milliseconds(match.group("start")),
            milliseconds(match.group("end")),
            frame_rate=frame_rate,
        )

    def replace_value(match: re.Match[str]) -> str:
        return format_timeline_position(
            milliseconds(match.group("value")),
            frame_rate=frame_rate,
        )

    def seconds(raw: str) -> int:
        return round(float(raw) * 1_000)

    def replace_second_range(match: re.Match[str]) -> str:
        return format_timeline_range(
            seconds(match.group("start")),
            seconds(match.group("end")),
            frame_rate=frame_rate,
        )

    def replace_second_value(match: re.Match[str]) -> str:
        return format_timeline_position(
            seconds(match.group("value")),
            frame_rate=frame_rate,
        )

    localized = _MILLISECOND_VALUE_PATTERN.sub(
        replace_value,
        _MILLISECOND_RANGE_PATTERN.sub(replace_range, value),
    )
    return _SECOND_VALUE_PATTERN.sub(
        replace_second_value,
        _SECOND_RANGE_PATTERN.sub(replace_second_range, localized),
    )


__all__ = [
    "DEFAULT_FRAME_RATE",
    "format_timeline_duration",
    "format_timeline_mentions",
    "format_timeline_position",
    "format_timeline_range",
    "normalize_frame_rate",
]
