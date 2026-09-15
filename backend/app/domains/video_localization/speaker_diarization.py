from __future__ import annotations

import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.video_localization.schemas import (
    VideoLocalizationSpeakerCluster,
    VideoLocalizationTranscriptSegment,
)
from app.errors import AppException
from app.services import asr_service, speaker_diarization_service


MAX_SPEAKER_COUNT_GUIDANCE = 50


class _CancellationSignal:
    def __init__(self, callback: Callable[[], bool] | None) -> None:
        self._callback = callback

    def is_set(self) -> bool:
        return bool(self._callback and self._callback())


class DiarizeSpeakersInput(BaseModel):
    """Serializable audio-only input contract for anonymous speaker diarization."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["speaker-diarization-v1"] = "speaker-diarization-v1"
    audio_path: str = Field(min_length=1)
    audio_sha256: str = Field(min_length=1)
    source_track_id: str = Field(min_length=1)
    engine_id: str = Field(min_length=1)
    duration_ms: int | None = Field(default=None, ge=0)
    min_speakers: int | None = Field(default=None, ge=1, le=MAX_SPEAKER_COUNT_GUIDANCE)
    max_speakers: int | None = Field(default=None, ge=1, le=MAX_SPEAKER_COUNT_GUIDANCE)

    @model_validator(mode="after")
    def validate_speaker_range(self) -> DiarizeSpeakersInput:
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError("min_speakers must not be greater than max_speakers")
        return self


class SpeakerDiarizationSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_cluster_id: str = Field(min_length=1)
    source_speaker_label: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    has_speaker_overlap: bool = False

    @model_validator(mode="after")
    def validate_range(self) -> SpeakerDiarizationSegment:
        if self.end_ms <= self.start_ms:
            raise ValueError("speaker diarization segment must have positive duration")
        return self


class SpeakerCountGuidanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_min_speakers: int | None = None
    requested_max_speakers: int | None = None
    detected_speaker_count: int = Field(ge=0)
    engine_applied: bool
    usage: Literal["automatic", "engine_constraint", "quality_check_only"]
    evaluation: Literal[
        "automatic",
        "within_range",
        "below_minimum",
        "above_maximum",
    ]


class SpeakerDiarizationQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning", "failed"]
    segment_count: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    overlap_segment_count: int = Field(ge=0)
    covered_duration_ms: int = Field(ge=0)
    audio_duration_ms: int | None = Field(default=None, ge=0)
    coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    warning_codes: list[str] = Field(default_factory=list)


class DiarizeSpeakersOutput(BaseModel):
    """Complete diarization output before it is attached to ASR text."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["speaker-diarization-v1"] = "speaker-diarization-v1"
    input: DiarizeSpeakersInput
    status: Literal["completed", "partial"]
    engine_id: str
    model_id: str | None = None
    segments: list[SpeakerDiarizationSegment] = Field(default_factory=list)
    clusters: list[VideoLocalizationSpeakerCluster] = Field(default_factory=list)
    count_guidance: SpeakerCountGuidanceResult
    quality_summary: SpeakerDiarizationQualitySummary
    verification: dict[str, Any] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    error: str | None = None
    stage_timing: dict[str, Any] = Field(default_factory=dict)


def diarize_speakers(
    request: DiarizeSpeakersInput,
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> DiarizeSpeakersOutput:
    """Run the audio-only speaker step.

    MOSS currently discovers speaker count automatically. The optional minimum
    and maximum values are therefore evaluated after inference as quality
    guidance; they are never used to force acoustic clusters together.
    """

    if request.engine_id not in {
        "auto",
        speaker_diarization_service.ENGINE_ID,
        "vibevoice-asr-mlx-4bit",
        "vibevoice-asr-mlx-8bit",
    }:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DIARIZATION_ENGINE_UNSUPPORTED",
            f"不支持的说话人区分引擎：{request.engine_id}",
        )

    started_at = time.perf_counter()
    if request.engine_id.startswith("vibevoice-asr-mlx-"):
        _asr_result, provider_result = asr_service.transcribe_and_diarize(
            engine_id=request.engine_id,
            audio_path=request.audio_path,
            language="auto",
            cancel_event=_CancellationSignal(is_cancelled),
        )
        raw_result = speaker_diarization_service.consolidate_provider_segments(
            audio_path=request.audio_path,
            raw_segments=list(provider_result.get("segments") or []),
            engine_id=request.engine_id,
            model_id=str(provider_result.get("model_id") or "") or None,
            is_cancelled=is_cancelled,
        )
        return build_output_from_raw_result(request, raw_result, started_at=started_at)
    raw_result = speaker_diarization_service.diarize(
        request.audio_path,
        is_cancelled=is_cancelled,
    )
    return build_output_from_raw_result(request, raw_result, started_at=started_at)


def build_output_from_raw_result(
    request: DiarizeSpeakersInput,
    raw_result: dict[str, Any],
    *,
    started_at: float | None = None,
) -> DiarizeSpeakersOutput:
    """Normalize already-computed provider segments into the domain contract."""

    stage_started_at = time.perf_counter() if started_at is None else started_at
    segments = [
        SpeakerDiarizationSegment(
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
            speaker_cluster_id=str(item["speaker"]),
            source_speaker_label=str(item.get("source_speaker") or item["speaker"]),
            confidence=item.get("confidence"),
            has_speaker_overlap=bool(item.get("has_speaker_overlap")),
        )
        for item in raw_result.get("segments") or []
        if int(item.get("end_ms") or 0) > int(item.get("start_ms") or 0)
    ]
    clusters = [
        item
        if isinstance(item, VideoLocalizationSpeakerCluster)
        else VideoLocalizationSpeakerCluster.model_validate(item)
        for item in raw_result.get("clusters") or []
    ]
    count_guidance = _speaker_count_guidance(request, len(clusters))
    quality_flags = set(str(item) for item in raw_result.get("quality_flags") or [])
    if count_guidance.evaluation == "below_minimum":
        quality_flags.add("speaker_count_below_requested_minimum")
    elif count_guidance.evaluation == "above_maximum":
        quality_flags.add("speaker_count_above_requested_maximum")
    if not segments:
        quality_flags.add("speaker_segments_empty")
    if not clusters:
        quality_flags.add("speaker_clusters_empty")

    quality_summary = _quality_summary(
        request,
        segments,
        clusters,
        sorted(quality_flags),
    )
    status = str(raw_result.get("status") or "completed")
    if status not in {"completed", "partial"}:
        raise RuntimeError(str(raw_result.get("error") or "说话人区分没有返回可用结果"))
    if quality_summary.status != "passed":
        status = "partial"

    return DiarizeSpeakersOutput(
        input=request,
        status=status,
        engine_id=str(raw_result.get("engine_id") or speaker_diarization_service.ENGINE_ID),
        model_id=str(raw_result.get("model_id") or "") or None,
        segments=segments,
        clusters=clusters,
        count_guidance=count_guidance,
        quality_summary=quality_summary,
        verification=dict(raw_result.get("verification") or {}),
        quality_flags=sorted(quality_flags),
        error=str(raw_result.get("error") or "") or None,
        stage_timing={
            "duration_ms": max(0, int(round((time.perf_counter() - stage_started_at) * 1000))),
            "segment_count": len(segments),
            "cluster_count": len(clusters),
            "status": status,
        },
    )


def attach_to_transcript_segments(
    segments: list[VideoLocalizationTranscriptSegment],
    result: DiarizeSpeakersOutput,
    *,
    audio_sha256: str,
    source_track_id: str,
) -> list[VideoLocalizationTranscriptSegment]:
    """Attach speaker labels only when both artifacts share one audio timeline."""

    if result.input.audio_sha256 != audio_sha256 or result.input.source_track_id != source_track_id:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DIARIZATION_SOURCE_MISMATCH",
            "原始听写与说话人区分使用的音轨或音频指纹不一致，不能合并。",
        )
    return speaker_diarization_service.assign_segments(
        segments,
        assignment_segments(result.segments),
    )


def assignment_segments(
    segments: list[SpeakerDiarizationSegment],
) -> list[dict[str, Any]]:
    """Adapt typed domain segments to the provider-neutral assignment shape."""

    return [
        {
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "speaker": item.speaker_cluster_id,
            "confidence": item.confidence,
            "has_speaker_overlap": item.has_speaker_overlap,
        }
        for item in segments
    ]


def should_apply_speaker_grouping(
    result: DiarizeSpeakersOutput,
) -> bool:
    """Return whether anonymous labels add information to the transcript."""

    return (
        len(result.clusters) >= 2
        and bool(result.segments)
        and result.quality_summary.status != "failed"
    )


def _speaker_count_guidance(
    request: DiarizeSpeakersInput,
    detected_count: int,
) -> SpeakerCountGuidanceResult:
    has_guidance = request.min_speakers is not None or request.max_speakers is not None
    if request.min_speakers is not None and detected_count < request.min_speakers:
        evaluation = "below_minimum"
    elif request.max_speakers is not None and detected_count > request.max_speakers:
        evaluation = "above_maximum"
    elif has_guidance:
        evaluation = "within_range"
    else:
        evaluation = "automatic"
    return SpeakerCountGuidanceResult(
        requested_min_speakers=request.min_speakers,
        requested_max_speakers=request.max_speakers,
        detected_speaker_count=detected_count,
        engine_applied=False,
        usage="quality_check_only" if has_guidance else "automatic",
        evaluation=evaluation,
    )


def _quality_summary(
    request: DiarizeSpeakersInput,
    segments: list[SpeakerDiarizationSegment],
    clusters: list[VideoLocalizationSpeakerCluster],
    warning_codes: list[str],
) -> SpeakerDiarizationQualitySummary:
    covered_duration_ms = _covered_duration_ms(segments)
    duration_ms = request.duration_ms
    coverage_ratio = (
        min(1.0, covered_duration_ms / duration_ms)
        if duration_ms and duration_ms > 0
        else None
    )
    failed = not segments or not clusters
    return SpeakerDiarizationQualitySummary(
        status="failed" if failed else ("warning" if warning_codes else "passed"),
        segment_count=len(segments),
        cluster_count=len(clusters),
        overlap_segment_count=sum(item.has_speaker_overlap for item in segments),
        covered_duration_ms=covered_duration_ms,
        audio_duration_ms=duration_ms,
        coverage_ratio=coverage_ratio,
        warning_codes=warning_codes,
    )


def _covered_duration_ms(segments: list[SpeakerDiarizationSegment]) -> int:
    ranges = sorted((item.start_ms, item.end_ms) for item in segments)
    if not ranges:
        return 0
    covered = 0
    current_start, current_end = ranges[0]
    for start_ms, end_ms in ranges[1:]:
        if start_ms <= current_end:
            current_end = max(current_end, end_ms)
            continue
        covered += current_end - current_start
        current_start, current_end = start_ms, end_ms
    return covered + current_end - current_start
