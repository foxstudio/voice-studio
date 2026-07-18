from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any, Literal

from .contracts import AsrSegment, DiarizationSegment


TimeUnit = Literal["milliseconds", "seconds"]


def to_milliseconds(value: Any, *, unit: TimeUnit = "seconds") -> int:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"Timestamp must be finite, got {value!r}")
    if unit == "seconds":
        numeric *= 1000
    elif unit != "milliseconds":
        raise ValueError(f"Unsupported timestamp unit: {unit}")
    return int(round(numeric))


def normalize_asr_segments(
    items: Iterable[AsrSegment | Mapping[str, Any]],
    *,
    default_time_unit: TimeUnit = "seconds",
) -> tuple[AsrSegment, ...]:
    normalized: list[AsrSegment] = []
    for item in items:
        if isinstance(item, AsrSegment):
            segment = item
        else:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            start_ms, end_ms = _mapping_times_ms(item, default_time_unit=default_time_unit)
            segment = AsrSegment(
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                language=_optional_text(item.get("language")),
                speaker_cluster=_optional_text(
                    item.get("speaker_cluster") or item.get("speaker_cluster_id") or item.get("speaker")
                ),
                confidence=_optional_float(item.get("confidence")),
            )
        if segment.text.strip():
            normalized.append(segment if segment.text == segment.text.strip() else replace(segment, text=segment.text.strip()))

    # Sorting is intentionally the only cross-segment timing operation. Existing
    # overlaps and non-monotonic end points remain untouched for downstream QC.
    return tuple(sorted(normalized, key=lambda segment: (segment.start_ms, segment.end_ms)))


def normalize_diarization_segments(
    items: Iterable[DiarizationSegment | Mapping[str, Any]],
    *,
    default_time_unit: TimeUnit = "seconds",
) -> tuple[DiarizationSegment, ...]:
    normalized: list[DiarizationSegment] = []
    for item in items:
        if isinstance(item, DiarizationSegment):
            segment = item
        else:
            speaker = _optional_text(
                item.get("speaker_cluster") or item.get("speaker_cluster_id") or item.get("speaker")
            )
            if not speaker:
                continue
            start_ms, end_ms = _mapping_times_ms(item, default_time_unit=default_time_unit)
            segment = DiarizationSegment(
                start_ms=start_ms,
                end_ms=end_ms,
                speaker_cluster=speaker,
                confidence=_optional_float(item.get("confidence")),
            )
        if segment.speaker_cluster.strip():
            normalized.append(segment)
    return tuple(sorted(normalized, key=lambda segment: (segment.start_ms, segment.end_ms, segment.speaker_cluster)))


def map_speakers_by_time_overlap(
    segments: Iterable[AsrSegment],
    diarization_segments: Iterable[DiarizationSegment],
) -> tuple[AsrSegment, ...]:
    """Assign the speaker with the largest positive temporal overlap.

    The function works for sentence segments and word-level segments alike.
    Existing timing and overlaps are never changed. If there is no positive
    overlap, an existing speaker assignment is retained.
    """

    speakers = tuple(diarization_segments)
    mapped: list[AsrSegment] = []
    for segment in segments:
        best_speaker: str | None = None
        best_overlap = 0
        for diarization in speakers:
            overlap = min(segment.end_ms, diarization.end_ms) - max(segment.start_ms, diarization.start_ms)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = diarization.speaker_cluster

        if best_speaker is None and segment.start_ms == segment.end_ms:
            best_speaker = next(
                (
                    diarization.speaker_cluster
                    for diarization in speakers
                    if diarization.start_ms <= segment.start_ms <= diarization.end_ms
                ),
                None,
            )
        mapped.append(replace(segment, speaker_cluster=best_speaker) if best_speaker else segment)
    return tuple(mapped)


map_asr_segments_to_speakers = map_speakers_by_time_overlap


def _mapping_times_ms(item: Mapping[str, Any], *, default_time_unit: TimeUnit) -> tuple[int, int]:
    if "start_ms" in item or "end_ms" in item:
        if "start_ms" not in item or "end_ms" not in item:
            raise ValueError("Both start_ms and end_ms are required")
        return (
            to_milliseconds(item["start_ms"], unit="milliseconds"),
            to_milliseconds(item["end_ms"], unit="milliseconds"),
        )
    if "start" not in item or "end" not in item:
        raise ValueError("Segment must define start/end or start_ms/end_ms")
    return (
        to_milliseconds(item["start"], unit=default_time_unit),
        to_milliseconds(item["end"], unit=default_time_unit),
    )


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None
