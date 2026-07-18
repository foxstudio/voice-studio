from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from app.models.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationSpeakerCluster,
    VideoLocalizationTimeRange,
    VideoLocalizationTranscriptSegment,
)
from app.services import speaker_verification_service
from app.services.asr_providers.providers.moss_mlx import MossMlxProvider


ENGINE_ID = MossMlxProvider.provider_id
MODEL_ID = "vanch007/mlx-MOSS-Transcribe-Diarize-8bit"
DEFAULT_RUNTIME_PYTHON = Path("~/VoiceStudio/engines/moss-transcribe-diarize/.venv/bin/python").expanduser()
DEFAULT_MODEL_PATH = Path("~/VoiceStudio/models/moss-transcribe-diarize-8bit").expanduser()


class _CancellationEvent:
    def __init__(self, callback: Callable[[], bool] | None) -> None:
        self.callback = callback

    def is_set(self) -> bool:
        return bool(self.callback and self.callback())


def provider() -> MossMlxProvider:
    return MossMlxProvider(
        runtime_python=DEFAULT_RUNTIME_PYTHON,
        model_path=DEFAULT_MODEL_PATH,
        max_new_tokens=8192,
    )


def health_check() -> dict[str, object]:
    moss = provider().health_check()
    return {
        **moss,
        "engine_id": ENGINE_ID,
        "model_id": MODEL_ID,
        "speaker_verifier": speaker_verification_service.health_check(),
    }


def diarize(
    audio_path: str | Path,
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    raw_result = provider().diarize(
        str(audio_path),
        cancel_event=_CancellationEvent(is_cancelled),
    )
    raw_segments = [
        {
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "speaker": item.speaker_cluster,
            "confidence": item.confidence,
        }
        for item in raw_result.segments
        if item.end_ms > item.start_ms
    ]
    verification_error = None
    try:
        verification = speaker_verification_service.consolidate_clusters(
            audio_path=audio_path,
            segments=raw_segments,
            cancel_check=is_cancelled,
        )
    except Exception as exc:
        labels = _source_labels(raw_segments)
        verification = {
            "status": "failed",
            "mapping": {label: f"cluster_{index:02d}" for index, label in enumerate(labels, start=1)},
            "auto_merged": [],
            "needs_review": [],
            "reason": "speaker_verifier_failed",
        }
        verification_error = str(exc)

    mapping = dict(verification["mapping"])
    normalized = [
        {
            **item,
            "source_speaker": item["speaker"],
            "speaker": mapping.get(item["speaker"], item["speaker"]),
        }
        for item in raw_segments
    ]
    overlap_indexes = _overlap_indexes(normalized)
    for index in overlap_indexes:
        normalized[index]["has_speaker_overlap"] = True
    clusters = _clusters(normalized, verification)
    status = "completed"
    quality_flags = []
    if verification.get("status") in {"failed", "partial"}:
        status = "partial"
        quality_flags.append("speaker_verification_incomplete")
    if verification.get("needs_review"):
        status = "partial"
        quality_flags.append("speaker_cluster_review_required")
    if overlap_indexes:
        status = "partial"
        quality_flags.extend(["speaker_overlap_detected", "speaker_overlap_review_required"])
    return {
        "status": status,
        "engine_id": ENGINE_ID,
        "model_id": MODEL_ID,
        "segments": normalized,
        "clusters": clusters,
        "verification": verification,
        "error": verification_error,
        "quality_flags": sorted(set(quality_flags)),
    }


def assign_segments(
    segments: list[VideoLocalizationTranscriptSegment],
    diarization_segments: list[dict[str, Any]],
) -> list[VideoLocalizationTranscriptSegment]:
    return [segment.model_copy(update=_speaker_assignment(segment.start_ms, segment.end_ms, diarization_segments)) for segment in segments]


def assign_words(
    words: list[VideoLocalizationAlignedWord],
    diarization_segments: list[dict[str, Any]],
) -> list[VideoLocalizationAlignedWord]:
    return [word.model_copy(update=_speaker_assignment(word.start_ms, word.end_ms, diarization_segments)) for word in words]


def _speaker_assignment(start_ms: int, end_ms: int, segments: list[dict[str, Any]]) -> dict[str, Any]:
    overlaps: dict[str, int] = defaultdict(int)
    for item in segments:
        overlap = min(end_ms, int(item["end_ms"])) - max(start_ms, int(item["start_ms"]))
        if overlap > 0:
            overlaps[str(item["speaker"])] += overlap
    if not overlaps:
        midpoint = (start_ms + end_ms) // 2
        nearest = next(
            (item for item in segments if int(item["start_ms"]) <= midpoint <= int(item["end_ms"])),
            None,
        )
        return {"speaker_cluster_id": str(nearest["speaker"])} if nearest else {}
    ordered = sorted(overlaps.items(), key=lambda item: (-item[1], item[0]))
    duration = max(1, end_ms - start_ms)
    return {
        "speaker_cluster_id": ordered[0][0],
        "speaker_confidence": min(1.0, ordered[0][1] / duration),
        "has_speaker_overlap": len(ordered) > 1,
    }


def _source_labels(segments: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {str(item["speaker"]) for item in segments},
        key=lambda label: min(int(item["start_ms"]) for item in segments if item["speaker"] == label),
    )


def _overlap_indexes(segments: list[dict[str, Any]]) -> set[int]:
    overlaps: set[int] = set()
    for left_index, left in enumerate(segments):
        for right_index in range(left_index + 1, len(segments)):
            right = segments[right_index]
            if int(right["start_ms"]) >= int(left["end_ms"]):
                break
            if left["speaker"] != right["speaker"]:
                overlaps.update({left_index, right_index})
    return overlaps


def _clusters(segments: list[dict[str, Any]], verification: dict[str, Any]) -> list[VideoLocalizationSpeakerCluster]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        grouped[str(segment["speaker"])].append(segment)
    review_labels = {
        label
        for pair in verification.get("needs_review") or []
        for label in (pair.get("left"), pair.get("right"))
        if label
    }
    output = []
    for cluster_id, items in sorted(grouped.items(), key=lambda item: min(int(row["start_ms"]) for row in item[1])):
        source_labels = sorted({str(item["source_speaker"]) for item in items})
        start_ms = min(int(item["start_ms"]) for item in items)
        end_ms = max(int(item["end_ms"]) for item in items)
        merge_status = "auto_merged" if len(source_labels) > 1 else "original"
        if review_labels.intersection(source_labels):
            merge_status = "needs_review"
        output.append(
            VideoLocalizationSpeakerCluster(
                cluster_id=cluster_id,
                source_label=source_labels[0],
                source_engine_id=ENGINE_ID,
                start_ms=start_ms,
                end_ms=end_ms,
                duration_ms=end_ms - start_ms,
                segment_count=len(items),
                merge_status=merge_status,
                merged_source_labels=source_labels,
                time_ranges=[
                    VideoLocalizationTimeRange(start_ms=int(item["start_ms"]), end_ms=int(item["end_ms"]), source=ENGINE_ID)
                    for item in items
                ],
            )
        )
    return output
