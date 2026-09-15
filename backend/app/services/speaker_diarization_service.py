from __future__ import annotations

from collections import defaultdict
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

import soundfile as sf

from app.models.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationSpeakerCluster,
    VideoLocalizationTimeRange,
    VideoLocalizationTranscriptSegment,
)
from app.services import settings_store, speaker_verification_service
from app.services.paths import expand_path
from app.services.python_runtime import engine_virtualenv_python
from app.services.asr_providers.providers.moss_mlx import MossMlxProvider
from app.services.asr_providers.contracts import DiarizationSegment


ENGINE_ID = MossMlxProvider.provider_id
MODEL_ID = "vanch007/mlx-MOSS-Transcribe-Diarize-8bit"
LONG_AUDIO_CHUNK_MS = 15 * 60 * 1000
LONG_AUDIO_CONTEXT_MS = 2_000


class _CancellationEvent:
    def __init__(self, callback: Callable[[], bool] | None) -> None:
        self.callback = callback

    def is_set(self) -> bool:
        return bool(self.callback and self.callback())


def runtime_root() -> Path:
    if configured := os.environ.get("VOICE_STUDIO_MOSS_RUNTIME_ROOT"):
        return expand_path(configured)
    return expand_path(settings_store.get().data_dir) / "engines" / "moss-transcribe-diarize"


def model_path() -> Path:
    if configured := os.environ.get("VOICE_STUDIO_MOSS_MODEL_DIR"):
        return expand_path(configured)
    return settings_store.model_path(ENGINE_ID)


class ManagedMossMlxProvider(MossMlxProvider):
    """Resolve the managed runtime only when a request or health check runs."""

    def runtime_python(self) -> str | None:
        path = engine_virtualenv_python(runtime_root())
        return str(path) if path.is_file() else None

    def model_path(self) -> Path | None:
        path = model_path()
        return path if path.is_dir() else None


def provider() -> MossMlxProvider:
    return ManagedMossMlxProvider(
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
    raw_segments = _diarize_raw_segments(
        Path(audio_path),
        moss=provider(),
        is_cancelled=is_cancelled,
    )
    raw_segment_items = [
        {
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "speaker": item.speaker_cluster,
            "confidence": item.confidence,
        }
        for item in raw_segments
        if item.end_ms > item.start_ms
    ]
    return consolidate_provider_segments(
        audio_path=audio_path,
        raw_segments=raw_segment_items,
        engine_id=ENGINE_ID,
        model_id=MODEL_ID,
        is_cancelled=is_cancelled,
    )


def consolidate_provider_segments(
    *,
    audio_path: str | Path,
    raw_segments: list[dict[str, Any]],
    engine_id: str,
    model_id: str | None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Unify provider-local speaker labels with the shared CAM++ verifier."""

    raw_segment_items = [
        {
            "start_ms": int(item["start_ms"]),
            "end_ms": int(item["end_ms"]),
            "speaker": str(item["speaker"]),
            "confidence": item.get("confidence"),
        }
        for item in raw_segments
        if int(item.get("end_ms") or 0) > int(item.get("start_ms") or 0)
    ]
    verification_error = None
    try:
        verification = speaker_verification_service.consolidate_clusters(
            audio_path=audio_path,
            segments=raw_segment_items,
            cancel_check=is_cancelled,
        )
    except Exception as exc:
        labels = _source_labels(raw_segment_items)
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
        for item in raw_segment_items
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
        "engine_id": engine_id,
        "model_id": model_id,
        "segments": normalized,
        "clusters": clusters,
        "verification": verification,
        "error": verification_error,
        "quality_flags": sorted(set(quality_flags)),
    }


def _diarize_raw_segments(
    audio_path: Path,
    *,
    moss: MossMlxProvider,
    is_cancelled: Callable[[], bool] | None,
) -> tuple[DiarizationSegment, ...]:
    info = sf.info(str(audio_path))
    duration_ms = max(
        1,
        int(round(info.frames * 1000 / info.samplerate)),
    )
    windows = _long_audio_windows(duration_ms)
    cancel_event = _CancellationEvent(is_cancelled)
    if len(windows) == 1:
        return moss.diarize(
            str(audio_path),
            cancel_event=cancel_event,
        ).segments

    stitched: list[DiarizationSegment] = []
    with tempfile.TemporaryDirectory(
        prefix="voice-studio-moss-chunks-"
    ) as temp_dir:
        for index, window in enumerate(windows, start=1):
            if cancel_event.is_set():
                raise RuntimeError("Speaker diarization cancelled")
            core_start_ms, core_end_ms, read_start_ms, read_end_ms = window
            chunk_path = Path(temp_dir) / f"chunk-{index:03d}.wav"
            _write_audio_window(
                audio_path,
                chunk_path,
                start_ms=read_start_ms,
                end_ms=read_end_ms,
            )
            chunk_result = moss.diarize(
                str(chunk_path),
                cancel_event=cancel_event,
            )
            stitched.extend(
                _globalize_chunk_segments(
                    chunk_result.segments,
                    chunk_index=index,
                    read_start_ms=read_start_ms,
                    core_start_ms=core_start_ms,
                    core_end_ms=core_end_ms,
                    duration_ms=duration_ms,
                )
            )
    return tuple(
        sorted(
            stitched,
            key=lambda item: (
                item.start_ms,
                item.end_ms,
                item.speaker_cluster,
            ),
        )
    )


def _long_audio_windows(
    duration_ms: int,
) -> tuple[tuple[int, int, int, int], ...]:
    windows = []
    for core_start_ms in range(0, duration_ms, LONG_AUDIO_CHUNK_MS):
        core_end_ms = min(
            duration_ms,
            core_start_ms + LONG_AUDIO_CHUNK_MS,
        )
        windows.append(
            (
                core_start_ms,
                core_end_ms,
                max(0, core_start_ms - LONG_AUDIO_CONTEXT_MS),
                min(duration_ms, core_end_ms + LONG_AUDIO_CONTEXT_MS),
            )
        )
    return tuple(windows)


def _write_audio_window(
    source_path: Path,
    destination: Path,
    *,
    start_ms: int,
    end_ms: int,
) -> None:
    with sf.SoundFile(str(source_path)) as source:
        start_frame = max(
            0,
            int(round(start_ms * source.samplerate / 1000)),
        )
        end_frame = min(
            source.frames,
            max(
                start_frame + 1,
                int(round(end_ms * source.samplerate / 1000)),
            ),
        )
        source.seek(start_frame)
        audio = source.read(
            end_frame - start_frame,
            dtype="float32",
            always_2d=True,
        )
        sf.write(
            str(destination),
            audio,
            source.samplerate,
            subtype="PCM_16",
        )


def _globalize_chunk_segments(
    segments: tuple[DiarizationSegment, ...],
    *,
    chunk_index: int,
    read_start_ms: int,
    core_start_ms: int,
    core_end_ms: int,
    duration_ms: int,
) -> tuple[DiarizationSegment, ...]:
    result = []
    for segment in segments:
        start_ms = max(
            0,
            min(duration_ms, read_start_ms + segment.start_ms),
        )
        end_ms = max(
            start_ms,
            min(duration_ms, read_start_ms + segment.end_ms),
        )
        midpoint_ms = start_ms + (end_ms - start_ms) // 2
        if (
            end_ms <= start_ms
            or midpoint_ms < core_start_ms
            or midpoint_ms >= core_end_ms
        ):
            continue
        result.append(
            DiarizationSegment(
                start_ms=start_ms,
                end_ms=end_ms,
                speaker_cluster=(
                    f"chunk_{chunk_index:03d}:"
                    f"{segment.speaker_cluster}"
                ),
                confidence=segment.confidence,
            )
        )
    return tuple(result)


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
    overlap_ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for item in segments:
        overlap_start = max(start_ms, int(item["start_ms"]))
        overlap_end = min(end_ms, int(item["end_ms"]))
        overlap = overlap_end - overlap_start
        if overlap > 0:
            speaker = str(item["speaker"])
            overlaps[speaker] += overlap
            overlap_ranges[speaker].append((overlap_start, overlap_end))
    if not overlaps:
        midpoint = (start_ms + end_ms) // 2
        nearest = next(
            (item for item in segments if int(item["start_ms"]) <= midpoint <= int(item["end_ms"])),
            None,
        )
        return {"speaker_cluster_id": str(nearest["speaker"])} if nearest else {}
    ordered = sorted(overlaps.items(), key=lambda item: (-item[1], item[0]))
    duration = max(1, end_ms - start_ms)
    result = {
        "speaker_cluster_id": ordered[0][0],
        "speaker_confidence": min(1.0, ordered[0][1] / duration),
        "has_speaker_overlap": len(ordered) > 1,
    }
    dominant_ranges = sorted(overlap_ranges[ordered[0][0]])
    merged_ranges: list[tuple[int, int]] = []
    for range_start, range_end in dominant_ranges:
        if merged_ranges and range_start <= merged_ranges[-1][1]:
            merged_ranges[-1] = (
                merged_ranges[-1][0],
                max(merged_ranges[-1][1], range_end),
            )
        else:
            merged_ranges.append((range_start, range_end))
    if len(merged_ranges) == 1:
        result.update(
            {
                "acoustic_support_start_ms": merged_ranges[0][0],
                "acoustic_support_end_ms": merged_ranges[0][1],
                "acoustic_support_source": "speaker_diarization",
            }
        )
    return result


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
