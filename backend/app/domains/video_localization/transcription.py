from __future__ import annotations

import re
import tempfile
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization import (
    asr_flow,
    asr_timing_contracts,
    audio_boundaries,
    boundary_review,
    media_assets,
    speaker_diarization,
    subtitle_entry_timing,
    transcript_quality_gate,
    web_research,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationAsrIncompleteRange,
    VideoLocalizationBoundaryReview,
    VideoLocalizationGlossaryEntry,
    VideoLocalizationTranscriptEditOperation,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptionState,
)
from app.domains.video_localization.timeline_timecode import (
    format_timeline_duration,
    format_timeline_range,
)
from app.errors import AppException
from app.services import (
    asr_service,
    audio_tools,
    qwen_forced_aligner,
    speaker_diarization_service,
)


ALIGNMENT_WINDOW_MS = 60_000
ALIGNMENT_CONTEXT_MS = 1_000
ZERO_DURATION_MAX_TOKEN_MS = 600
COLLAPSED_WORD_MAX_DURATION_MS = 5
COLLAPSED_WORD_MIN_RUN = 3
COLLAPSED_WORD_RECOVERY_GAP_MS = 600
COLLAPSED_WORD_MAX_LOOKAHEAD = 24
WORD_PATTERN = re.compile(
    r"\d+(?:[.,:]\d+)+|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*|[\u3040-\u30ff\u3400-\u9fff]|[^\w\s]",
    re.UNICODE,
)
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
MIN_DIARIZED_SPEECH_COVERAGE = 0.72
MIN_DIARIZED_SPEECH_MS_FOR_COVERAGE_CHECK = 15_000
MAX_UNCOVERED_DIARIZED_SPEECH_GAP_MS = 12_000
TERMINAL_SENTENCE_END_PATTERN = re.compile(r"[.!?。！？…](?:[\"'”’）)\]}」』】]*)$")
ASR_CONTEXT_TERM_LIMIT = 8
ASR_CONTEXT_TERM_MAX_CHARS = 64

AtomicSnapshotCallback = Callable[[str, BaseModel], None]


class TranscribeRawInput(BaseModel):
    """Serializable input contract for the first, engine-only ASR step."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-raw-v2"] = "asr-raw-v2"
    audio_path: str
    audio_sha256: str
    engine_id: str
    source_track_id: str
    requested_language: str
    duration_ms: int | None = None
    context_terms: list[str] = Field(default_factory=list, max_length=8)


class TranscribeRawQualitySummary(BaseModel):
    """Deterministic completeness checks for the engine-only transcript."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning", "failed"]
    has_text: bool
    has_segments: bool
    timestamps_monotonic: bool
    segment_count: int
    raw_text_char_count: int
    first_start_ms: int | None = None
    last_end_ms: int | None = None
    audio_duration_ms: int | None = None
    trailing_gap_ms: int | None = None
    incomplete_range_count: int
    warning_codes: list[str] = Field(default_factory=list)


class TranscribeRawOutput(BaseModel):
    """Complete engine-only ASR result, before any downstream review or timing work."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-raw-v2"] = "asr-raw-v2"
    input: TranscribeRawInput
    raw_text: str
    language: str
    segments: list[VideoLocalizationTranscriptSegment] = Field(default_factory=list)
    incomplete_chunk_ranges: list[dict[str, Any]] = Field(default_factory=list)
    usage_seconds: float | None = None
    provider_response_id: str | None = None
    stage_timing: dict[str, Any] = Field(default_factory=dict)
    quality_summary: TranscribeRawQualitySummary


@dataclass(frozen=True)
class InitialSpeechAnalysisRun:
    raw_asr: TranscribeRawOutput
    diarization: speaker_diarization.DiarizeSpeakersOutput | None
    diarization_error: str | None = None


class _CallbackCancellationSignal:
    def __init__(self, callback: Callable[[], bool] | None) -> None:
        self._callback = callback

    def is_set(self) -> bool:
        return bool(self._callback and self._callback())


def automatic_asr_context_terms(
    *,
    source_filename: str | Path | None = None,
    source_video_path: str | Path | None,
    glossary: list[VideoLocalizationGlossaryEntry] | None,
) -> list[str]:
    """Use explicit vocabulary only, never infer speech from media metadata.

    Filename arguments remain accepted for existing callers; titles and source
    identifiers belong to research context, not the acoustic decoder's prompt.
    """

    candidates = [
        item.corrected_source_text or item.source_text
        for item in (glossary or [])
    ]
    result: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        term = " ".join(str(raw or "").strip(" \t\r\n\"'").split())
        if (
            len(term) < 2
            or len(term) > ASR_CONTEXT_TERM_MAX_CHARS
            or not any(character.isalnum() for character in term)
        ):
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(term)
        if len(result) >= ASR_CONTEXT_TERM_LIMIT:
            break
    return result


def summarize_transcribe_raw_quality(
    *,
    request: TranscribeRawInput,
    raw_text: str,
    segments: list[VideoLocalizationTranscriptSegment],
    incomplete_chunk_ranges: list[dict[str, Any]],
) -> TranscribeRawQualitySummary:
    has_text = bool(raw_text.strip())
    has_segments = bool(segments)
    timestamps_monotonic = all(
        segment.end_ms >= segment.start_ms and (index == 0 or segment.start_ms >= segments[index - 1].start_ms)
        for index, segment in enumerate(segments)
    )
    first_start_ms = segments[0].start_ms if segments else None
    last_end_ms = segments[-1].end_ms if segments else None
    duration_ms = request.duration_ms
    trailing_gap_ms = max(0, duration_ms - last_end_ms) if duration_ms is not None and last_end_ms is not None else None
    review_gap_ms = max(2_000, round((duration_ms or 0) * 0.02))
    warning_codes: list[str] = []
    if not has_text:
        warning_codes.append("empty_raw_text")
    if not has_segments:
        warning_codes.append("empty_segments")
    if any(not segment.raw_text.strip() for segment in segments):
        warning_codes.append("empty_segment_text")
    if not timestamps_monotonic:
        warning_codes.append("non_monotonic_timestamps")
    if incomplete_chunk_ranges:
        warning_codes.append("incomplete_chunk_ranges")
    if first_start_ms is not None and first_start_ms > review_gap_ms:
        warning_codes.append("leading_gap_review_required")
    if trailing_gap_ms is not None and trailing_gap_ms > review_gap_ms:
        warning_codes.append("terminal_fragment_review_required")
    if duration_ms is not None and last_end_ms is not None and last_end_ms > duration_ms + 100:
        warning_codes.append("media_end_clipped")

    failed = not has_text or not has_segments or not timestamps_monotonic
    return TranscribeRawQualitySummary(
        status="failed" if failed else ("warning" if warning_codes else "passed"),
        has_text=has_text,
        has_segments=has_segments,
        timestamps_monotonic=timestamps_monotonic,
        segment_count=len(segments),
        raw_text_char_count=len(raw_text),
        first_start_ms=first_start_ms,
        last_end_ms=last_end_ms,
        audio_duration_ms=duration_ms,
        trailing_gap_ms=trailing_gap_ms,
        incomplete_range_count=len(incomplete_chunk_ranges),
        warning_codes=warning_codes,
    )


def build_transcribe_raw_output(
    *,
    input: TranscribeRawInput,
    raw_text: str,
    language: str,
    segments: list[VideoLocalizationTranscriptSegment] | None = None,
    incomplete_chunk_ranges: list[dict[str, Any]] | None = None,
    usage_seconds: float | None = None,
    provider_response_id: str | None = None,
    stage_timing: dict[str, Any] | None = None,
) -> TranscribeRawOutput:
    current_segments = list(segments or [])
    current_incomplete_ranges = list(incomplete_chunk_ranges or [])
    return TranscribeRawOutput(
        input=input,
        raw_text=raw_text,
        language=language,
        segments=current_segments,
        incomplete_chunk_ranges=current_incomplete_ranges,
        usage_seconds=usage_seconds,
        provider_response_id=provider_response_id,
        stage_timing=dict(stage_timing or {}),
        quality_summary=summarize_transcribe_raw_quality(
            request=input,
            raw_text=raw_text,
            segments=current_segments,
            incomplete_chunk_ranges=current_incomplete_ranges,
        ),
    )


def transcribe_raw(
    request: TranscribeRawInput,
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> TranscribeRawOutput:
    """Run only the ASR engine and normalize its raw segments.

    This intentionally excludes diarization, language review, research,
    alignment, acoustic boundary analysis, and subtitle segmentation.
    """

    def ensure_active() -> None:
        if is_cancelled and is_cancelled():
            raise AppException(409, "VIDEO_LOCALIZATION_OPERATION_CANCELLED", "字幕听写任务已取消")

    ensure_active()
    stage_started_at = time.perf_counter()
    transcribe_kwargs = {
        "engine_id": request.engine_id,
        "audio_path": request.audio_path,
        "language": request.requested_language,
    }
    if request.context_terms:
        transcribe_kwargs["hotwords"] = tuple(request.context_terms)
    result = asr_service.transcribe(**transcribe_kwargs)
    ensure_active()
    raw_segments = asr_service.normalize_segments(result.get("segments"))
    segments = _build_segments(
        raw_segments,
        str(result.get("text") or "").strip(),
        request.duration_ms,
    )
    raw_text = _join_segment_text(segment.raw_text for segment in segments)
    incomplete_chunk_ranges = list(result.get("incomplete_chunk_ranges") or [])
    return build_transcribe_raw_output(
        input=request,
        raw_text=raw_text,
        language=_resolve_transcript_language(request.requested_language, raw_segments, raw_text),
        segments=segments,
        incomplete_chunk_ranges=incomplete_chunk_ranges,
        usage_seconds=result.get("usage_seconds"),
        provider_response_id=result.get("provider_response_id"),
        stage_timing={
            "duration_ms": _elapsed_ms(stage_started_at),
            "segment_count": len(segments),
        },
    )


def transcribe_raw_and_diarize(
    asr_request: TranscribeRawInput,
    diarization_request: speaker_diarization.DiarizeSpeakersInput,
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> InitialSpeechAnalysisRun:
    """Run a joint provider once, then project both existing domain contracts."""

    if asr_request.audio_sha256 != diarization_request.audio_sha256 or (
        asr_request.source_track_id != diarization_request.source_track_id
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_JOINT_ASR_SOURCE_MISMATCH",
            "联合听写与说话人分析必须使用同一份音频。",
        )
    stage_started_at = time.perf_counter()
    asr_payload, diarization_payload = asr_service.transcribe_and_diarize(
        engine_id=asr_request.engine_id,
        audio_path=asr_request.audio_path,
        language=asr_request.requested_language,
        hotwords=tuple(asr_request.context_terms),
        cancel_event=_CallbackCancellationSignal(is_cancelled),
    )
    raw_segments = asr_service.normalize_segments(asr_payload.get("segments"))
    segments = _build_segments(
        raw_segments,
        str(asr_payload.get("text") or "").strip(),
        asr_request.duration_ms,
    )
    raw_text = _join_segment_text(segment.raw_text for segment in segments)
    raw_output = build_transcribe_raw_output(
        input=asr_request,
        raw_text=raw_text,
        language=_resolve_transcript_language(
            asr_request.requested_language,
            raw_segments,
            raw_text,
        ),
        segments=segments,
        incomplete_chunk_ranges=list(asr_payload.get("incomplete_chunk_ranges") or []),
        usage_seconds=asr_payload.get("usage_seconds"),
        provider_response_id=asr_payload.get("provider_response_id"),
        stage_timing={
            "duration_ms": _elapsed_ms(stage_started_at),
            "segment_count": len(segments),
            "joint_analysis": True,
        },
    )
    consolidated = speaker_diarization_service.consolidate_provider_segments(
        audio_path=diarization_request.audio_path,
        raw_segments=list(diarization_payload.get("segments") or []),
        engine_id=asr_request.engine_id,
        model_id=str(diarization_payload.get("model_id") or "") or None,
        is_cancelled=is_cancelled,
    )
    diarization_output = speaker_diarization.build_output_from_raw_result(
        diarization_request,
        consolidated,
        started_at=stage_started_at,
    )
    return InitialSpeechAnalysisRun(raw_asr=raw_output, diarization=diarization_output)


def run_initial_speech_analysis(
    *,
    asr_request: TranscribeRawInput,
    diarization_request: speaker_diarization.DiarizeSpeakersInput | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_raw_asr_complete: Callable[[TranscribeRawOutput], None] | None = None,
    raw_runner: Callable[[TranscribeRawInput], TranscribeRawOutput] | None = None,
    diarization_runner: Callable[
        [speaker_diarization.DiarizeSpeakersInput],
        speaker_diarization.DiarizeSpeakersOutput,
    ]
    | None = None,
    is_fatal_diarization_error: (Callable[[Exception], bool] | None) = None,
) -> InitialSpeechAnalysisRun:
    """Run independent audio-only analysis branches and join their results.

    A diarization failure is intentionally non-fatal for the formal ASR flow;
    the caller receives the error and can continue without speaker labels.
    """

    def run_raw(request: TranscribeRawInput) -> TranscribeRawOutput:
        if raw_runner is not None:
            return raw_runner(request)
        return transcribe_raw(request, is_cancelled=is_cancelled)

    def run_diarization(
        request: speaker_diarization.DiarizeSpeakersInput,
    ) -> speaker_diarization.DiarizeSpeakersOutput:
        if diarization_runner is not None:
            return diarization_runner(request)
        return speaker_diarization.diarize_speakers(
            request,
            is_cancelled=is_cancelled,
        )

    if diarization_request is None:
        raw_asr = run_raw(asr_request)
        if on_raw_asr_complete is not None:
            on_raw_asr_complete(raw_asr)
        return InitialSpeechAnalysisRun(
            raw_asr=raw_asr,
            diarization=None,
        )

    if (
        raw_runner is None
        and diarization_runner is None
        and asr_service.supports_joint_analysis(asr_request.engine_id)
        and diarization_request.engine_id in {"auto", asr_request.engine_id}
    ):
        result = transcribe_raw_and_diarize(
            asr_request,
            diarization_request,
            is_cancelled=is_cancelled,
        )
        if on_raw_asr_complete is not None:
            on_raw_asr_complete(result.raw_asr)
        return result

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="video-localization-initial") as executor:
        asr_future = executor.submit(run_raw, asr_request)
        diarization_future = executor.submit(run_diarization, diarization_request)
        raw_result = asr_future.result()
        if on_raw_asr_complete is not None:
            on_raw_asr_complete(raw_result)
        try:
            diarization_result = diarization_future.result()
        except Exception as exc:
            if is_fatal_diarization_error is not None and is_fatal_diarization_error(exc):
                raise
            return InitialSpeechAnalysisRun(
                raw_asr=raw_result,
                diarization=None,
                diarization_error=str(exc),
            )
    return InitialSpeechAnalysisRun(
        raw_asr=raw_result,
        diarization=diarization_result,
    )


def run_alignment_step(
    request: asr_timing_contracts.AsrAlignmentInput,
) -> asr_timing_contracts.AsrAlignmentResult:
    words, metadata = align_segments(
        request.alignment_audio_path,
        request.segments,
        language=request.language,
        max_duration_ms=request.duration_ms,
    )
    if request.diarization_segments:
        words = speaker_diarization_service.assign_words(
            words,
            speaker_diarization.assignment_segments(
                request.diarization_segments
            ),
        )
    return asr_timing_contracts.AsrAlignmentResult(
        input=request.model_copy(deep=True),
        words=words,
        metadata=metadata,
    )


def run_strict_alignment_step(
    request: asr_timing_contracts.AsrAlignmentInput,
) -> asr_timing_contracts.AsrAlignmentResult:
    """Run the same aligner without interpolation or guessed word times."""

    words, metadata = align_segments_strict(
        request.alignment_audio_path,
        request.segments,
        language=request.language,
        max_duration_ms=request.duration_ms,
    )
    if request.diarization_segments:
        words = speaker_diarization_service.assign_words(
            words,
            speaker_diarization.assignment_segments(
                request.diarization_segments
            ),
        )
    return asr_timing_contracts.AsrAlignmentResult(
        input=request.model_copy(deep=True),
        words=words,
        metadata=metadata,
    )


def run_audio_boundaries_step(
    request: asr_timing_contracts.AsrAudioBoundariesInput,
) -> asr_timing_contracts.AsrAudioBoundariesResult:
    boundary_features, metadata = (
        audio_boundaries.analyze_word_boundaries(
            request.audio_path,
            request.words,
        )
    )
    return asr_timing_contracts.AsrAudioBoundariesResult(
        input=request.model_copy(deep=True),
        boundary_features=boundary_features,
        subtitle_entry_by_word_id=(
            subtitle_entry_timing.detect_subtitle_entries(
                request.audio_path,
                request.words,
                frame_rate=request.video_frame_rate,
            )
        ),
        metadata=metadata,
    )


def run_boundary_review_step(
    request: asr_timing_contracts.AsrBoundaryReviewInput,
    *,
    progress_callback: (
        Callable[[float, int, int], None] | None
    ) = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> asr_timing_contracts.AsrBoundaryReviewResult:
    reviews, metadata = boundary_review.review_candidate_boundaries(
        request.words,
        request.boundary_features,
        language=request.language,
        segmentation_profile_id=request.segmentation_profile_id,
        audio_analysis_available=request.audio_analysis_available,
        profile_id=request.profile_id,
        existing_reviews=request.existing_reviews,
        progress_callback=progress_callback,
        is_cancelled=is_cancelled,
    )
    return asr_timing_contracts.AsrBoundaryReviewResult(
        input=request.model_copy(deep=True),
        reviews=reviews,
        metadata=metadata,
    )


def transcribe_and_process(
    *,
    audio_path: str | Path,
    alignment_audio_path: str | Path | None = None,
    engine_id: str,
    source_track_id: str,
    alignment_source_track_id: str | None = None,
    language: str,
    duration_ms: int | None,
    llm_profile_id: str | None = None,
    glossary: list[VideoLocalizationGlossaryEntry] | None = None,
    source_filename: str | Path | None = None,
    source_video_path: str | Path | None = None,
    source_video_frame_rate: float | None = None,
    scene_context: str = "",
    research_cache_dir: str | Path | None = None,
    segmentation_profile_id: str = "generic_zh",
    existing_boundary_reviews: list[VideoLocalizationBoundaryReview] | None = None,
    source_audio_sha256: str | None = None,
    alignment_audio_sha256: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    preview_callback: Callable[[str, list[dict[str, Any]]], None] | None = None,
    report_callback: Callable[[str, dict], None] | None = None,
    diarization_engine_id: str | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    initial_analysis_runner: Callable[..., Any] | None = None,
    initial_analysis_joiner: Callable[[Any], Any] | None = None,
    transcript_review_runner: Callable[..., Any],
    atomic_snapshot_callback: AtomicSnapshotCallback | None = None,
) -> VideoLocalizationTranscriptionState:
    pipeline_started_at = time.perf_counter()
    stage_timings: dict[str, dict[str, Any]] = {}

    def ensure_active() -> None:
        if is_cancelled and is_cancelled():
            raise AppException(409, "VIDEO_LOCALIZATION_OPERATION_CANCELLED", "字幕听写任务已取消")

    resolved_alignment_audio_path = Path(alignment_audio_path or audio_path)
    ensure_active()
    audio_sha256 = source_audio_sha256 or media_assets.file_sha256(audio_path)
    asr_request = TranscribeRawInput(
        audio_path=str(audio_path),
        audio_sha256=audio_sha256,
        engine_id=engine_id,
        source_track_id=source_track_id,
        requested_language=language,
        duration_ms=duration_ms,
        context_terms=automatic_asr_context_terms(
            source_filename=source_filename,
            source_video_path=source_video_path,
            glossary=glossary,
        ),
    )
    diarization_request = (
        speaker_diarization.DiarizeSpeakersInput(
            audio_path=str(audio_path),
            audio_sha256=audio_sha256,
            engine_id=diarization_engine_id,
            source_track_id=source_track_id,
            duration_ms=duration_ms,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        if diarization_engine_id
        else None
    )
    asr_execution_mode = (
        "与说话人分析并行"
        if diarization_request is not None
        else "单独执行"
    )
    _report_task_step(
        report_callback,
        "asr",
        purpose="把人声音频转换为带粗略时间范围的源语言文本。",
        summary=(
            "正在与说话人分析并行生成原始听写。"
            if diarization_request is not None
            else "正在识别人声内容。"
        ),
        metrics=(("执行方式", asr_execution_mode),),
        status="running",
    )
    if diarization_request is not None:
        _report_task_step(
            report_callback,
            "diarization",
            purpose=("根据声音特征区分不同说话人，只建立声纹分组，不猜测真实姓名。"),
            summary="正在与原始听写并行分析说话人。",
            metrics=(("执行方式", "与原始听写并行"),),
            status="running",
        )
    _report_progress(
        progress_callback,
        0.15,
        "正在并行生成原始听写并分析说话人" if diarization_request else "正在识别人声内容",
    )

    def publish_raw_asr(raw_result: TranscribeRawOutput) -> None:
        incomplete_range_items = [
            {
                "title": format_timeline_range(
                    int(item.get("start_ms") or 0),
                    int(item.get("end_ms") or 0),
                ),
                "text": "这段没有生成可用的源语言听写，后续字幕和配音会跳过。",
                "meta": str(item.get("reason") or "未返回可用结果"),
                "facts": [],
                "links": [],
                "tone": "warning",
            }
            for item in raw_result.incomplete_chunk_ranges
        ]
        _report_task_step(
            report_callback,
            "asr",
            purpose="把人声音频转换为带粗略时间范围的源语言文本。",
            summary=(
                f"已识别 {len(raw_result.segments)} 个讲话片段，共 {len(raw_result.raw_text)} 个字符；"
                f"另有 {len(incomplete_range_items)} 段未识别。"
                if incomplete_range_items
                else f"已识别 {len(raw_result.segments)} 个讲话片段，共 {len(raw_result.raw_text)} 个字符。"
            ),
            metrics=(
                ("讲话片段", len(raw_result.segments)),
                ("文本字符", len(raw_result.raw_text)),
                ("未识别范围", len(incomplete_range_items)),
                ("识别引擎", engine_id),
            ),
            sections=(
                ("识别文本样例", _segment_result_items(raw_result.segments, corrected=False)),
                ("未识别范围", incomplete_range_items),
            ),
            status="warning" if raw_result.quality_summary.status == "warning" else "success",
            duration_ms=raw_result.stage_timing.get("duration_ms"),
        )
        _report_preview(
            preview_callback,
            "asr_draft",
            raw_result.segments,
            corrected=False,
        )

    initial_analysis = (initial_analysis_runner or run_initial_speech_analysis)(
        asr_request=asr_request,
        diarization_request=diarization_request,
        is_cancelled=is_cancelled,
        on_raw_asr_complete=publish_raw_asr,
    )
    raw_result = initial_analysis.raw_asr
    ensure_transcribe_raw_usable(raw_result)
    join_started_at = time.perf_counter()
    joined_initial_analysis = initial_analysis_joiner(initial_analysis) if initial_analysis_joiner is not None else None
    join_duration_ms = max(
        0,
        int(round((time.perf_counter() - join_started_at) * 1000)),
    )
    segments = joined_initial_analysis.segments if joined_initial_analysis is not None else raw_result.segments
    incomplete_chunk_ranges = raw_result.incomplete_chunk_ranges
    raw_text = raw_result.raw_text
    resolved_language = raw_result.language
    stage_timings["asr"] = raw_result.stage_timing
    diarization: dict[str, Any] = {
        "status": "not_run",
        "engine_id": None,
        "model_id": None,
        "segments": [],
        "clusters": [],
        "quality_flags": [],
        "error": None,
    }
    if diarization_request:
        diarization_result = initial_analysis.diarization
        if diarization_result is not None:
            speaker_grouping_applied = (
                joined_initial_analysis.speaker_grouping_applied
                if joined_initial_analysis is not None
                else speaker_diarization.should_apply_speaker_grouping(
                    diarization_result
                )
            )
            if (
                joined_initial_analysis is None
                and speaker_grouping_applied
            ):
                segments = speaker_diarization.attach_to_transcript_segments(
                    segments,
                    diarization_result,
                    audio_sha256=asr_request.audio_sha256,
                    source_track_id=asr_request.source_track_id,
                )
            diarization = {
                "status": diarization_result.status,
                "engine_id": diarization_result.engine_id,
                "model_id": diarization_result.model_id,
                "segments": [
                    {
                        "start_ms": item.start_ms,
                        "end_ms": item.end_ms,
                        "speaker": item.speaker_cluster_id,
                        "source_speaker": item.source_speaker_label,
                        "confidence": item.confidence,
                        "has_speaker_overlap": item.has_speaker_overlap,
                    }
                    for item in diarization_result.segments
                ],
                "clusters": diarization_result.clusters,
                "quality_flags": diarization_result.quality_flags,
                "error": diarization_result.error,
                "speaker_grouping_applied": (
                    speaker_grouping_applied
                ),
            }
        else:
            diarization = {
                **diarization,
                "status": "failed",
                "engine_id": speaker_diarization_service.ENGINE_ID,
                "model_id": speaker_diarization_service.MODEL_ID,
                "error": initial_analysis.diarization_error or "说话人区分没有返回结果",
                "quality_flags": ["speaker_diarization_failed"],
                "speaker_grouping_applied": False,
            }
        stage_timings["diarization"] = (
            dict(diarization_result.stage_timing)
            if diarization_result is not None
            else {
                "duration_ms": 0,
                "status": diarization["status"],
                "cluster_count": 0,
            }
        )
        _report_progress(progress_callback, 0.27, "原始听写与说话人分析已汇合")
        _report_task_step(
            report_callback,
            "diarization",
            purpose="根据声音特征区分不同说话人，只建立声纹分组，不猜测真实姓名。",
            summary=(
                (
                    f"已区分 {len(diarization['clusters'])} 个说话人分组。"
                    if diarization.get("speaker_grouping_applied")
                    else "没有检测到多个稳定声音角色，无需添加说话人标签。"
                )
                if diarization["status"] != "failed"
                else f"说话人区分未完成：{diarization.get('error') or '未知错误'}"
            ),
            metrics=(("说话人分组", len(diarization["clusters"])), ("状态", diarization["status"])),
            status="warning" if diarization["status"] in {"failed", "partial"} else "success",
            duration_ms=stage_timings["diarization"].get("duration_ms"),
        )
    if joined_initial_analysis is not None or diarization_request is not None:
        matched_segments = sum(bool(segment.speaker_cluster_id) for segment in segments)
        low_confidence_segments = sum(
            segment.speaker_confidence is not None and segment.speaker_confidence < 0.6
            for segment in segments
        )
        overlap_segments = sum(segment.has_speaker_overlap for segment in segments)
        join_warnings = list(joined_initial_analysis.warnings) if joined_initial_analysis is not None else []
        stage_timings["initial_analysis_join"] = {
            "duration_ms": join_duration_ms,
            "input_segment_count": len(raw_result.segments),
            "output_segment_count": len(segments),
            "matched_segment_count": matched_segments,
            "warning_count": len(join_warnings),
        }
        _report_task_step(
            report_callback,
            "initial_analysis_join",
            purpose="确认听写和说话人结果来自同一音轨，再整理成谁在什么时候说了什么。",
            summary=(f"已汇合 {len(segments)} 个听写片段，其中 {matched_segments} 个已匹配匿名说话人。"),
            metrics=(
                ("听写片段", len(raw_result.segments)),
                ("汇合后片段", len(segments)),
                ("已匹配说话人", matched_segments),
                ("未匹配", len(segments) - matched_segments),
                ("低可信度", low_confidence_segments),
                ("重叠讲话", overlap_segments),
            ),
            sections=(
                (
                    "汇合后的讲话样例",
                    _joined_segment_result_items(segments),
                ),
            ),
            status="warning" if join_warnings else "success",
            duration_ms=join_duration_ms,
            debug={
                "description": "用于确认两份上游结果的来源、契约和汇合完整性。",
                "metrics": [
                    {"label": "听写输入契约", "value": raw_result.contract_version},
                    {
                        "label": "说话人输入契约",
                        "value": (
                            initial_analysis.diarization.contract_version
                            if initial_analysis.diarization is not None
                            else "未生成"
                        ),
                    },
                    {
                        "label": "汇合输出契约",
                        "value": (
                            joined_initial_analysis.contract_version
                            if joined_initial_analysis is not None
                            else "兼容模式"
                        ),
                    },
                    {"label": "来源音轨", "value": raw_result.input.source_track_id},
                    {
                        "label": "音频指纹",
                        "value": _short_fingerprint(raw_result.input.audio_sha256),
                    },
                ],
                "sections": [],
                "notes": join_warnings,
            },
        )
    if joined_initial_analysis is None and diarization_request is not None:
        ensure_transcript_covers_diarized_speech(
            segments,
            diarization,
            incomplete_chunk_ranges=incomplete_chunk_ranges,
        )
    if not segments:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_ASR_EMPTY",
            ("语音识别没有返回有效的字幕文本，请检查音轨内容或更换识别引擎后重试。"),
        )
    language_review = transcript_review_runner(
        segments,
        language=resolved_language,
        profile_id=llm_profile_id,
        glossary=glossary,
        scene_context=scene_context,
        research_cache_dir=research_cache_dir,
        context_terms=list(asr_request.context_terms),
        is_cancelled=is_cancelled,
        on_progress=progress_callback,
        on_report=report_callback,
    )
    reviewed_segments = language_review.segments
    research = language_review.research
    review_profile_id = language_review.profile_id
    review_meta = language_review.review_meta
    transcript_quality_report = language_review.report
    stage_timings.update(language_review.stage_timings)
    reviewed_segments = _record_final_transcript_quality_operations(
        segments,
        reviewed_segments,
        language_review.report.get("changes") or [],
        language_review.report.get("warnings") or [],
    )
    _report_preview(preview_callback, "text_review", reviewed_segments, corrected=True)
    has_transcript_gate_contract = hasattr(
        language_review,
        "quality_gate",
    )
    transcript_gate = getattr(language_review, "quality_gate", None)
    if has_transcript_gate_contract and transcript_gate is None:
        _report_task_step(
            report_callback,
            "transcript_quality_gate",
            purpose=("确认全文和时间结构可以安全进入校时；低把握文字采用当前最高概率结果继续，并保留建议复听提示。"),
            summary="上游复核没有形成可检查的终态，已停止生成逐词时间。",
            metrics=(
                ("检查结论", "上游结果未就绪"),
                ("模型调用", 0),
            ),
            sections=(),
            status="failed",
            debug={
                "description": "用于确认质量门为什么没有执行。",
                "metrics": [],
                "sections": [],
                "notes": ["正式流程缺少全文复核终态时，不允许绕过检查直接进入校时。"],
            },
        )
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_MISSING",
            "全文复核没有形成终态，未执行逐词时间对齐。",
        )
    if transcript_gate is not None:
        gate_labels = {
            "ready_for_alignment": "可以进入校时",
            "manual_review_required": "建议人工复听",
            "failed": "上游结果未就绪",
        }
        alignment_is_blocked = transcript_quality_gate.blocks_alignment(transcript_gate)
        hard_blockers = transcript_quality_gate.hard_alignment_blockers(transcript_gate)
        review_recommended = bool(
            transcript_gate.review_targets
            or transcript_gate.warnings
            or transcript_gate.decision == "manual_review_required"
        )
        transcript_quality_step_result = _report_task_step(
            report_callback,
            "transcript_quality_gate",
            purpose=("确认全文和时间结构可以安全进入校时；低把握文字采用当前最高概率结果继续，并保留建议复听提示。"),
            summary=(
                (
                    (f"已自动继续校时，建议复听 {len(transcript_gate.review_targets)} 处。")
                    if transcript_gate.review_targets
                    else ("已自动继续校时，当前最高概率文字已保留，并记录了质量提醒。")
                    if review_recommended
                    else "文字已经定稿，可以开始生成逐词时间。"
                )
                if not alignment_is_blocked
                else "进入校时前检查未通过，请先修复上游技术或结构问题。"
            ),
            metrics=(
                ("检查结论", gate_labels[transcript_gate.decision]),
                ("建议复听", len(transcript_gate.review_targets)),
                ("阻断项", len(hard_blockers)),
                ("提醒", len(transcript_gate.warnings)),
            ),
            sections=(
                (
                    "建议复听",
                    [
                        {
                            "title": item.title,
                            "text": item.detail,
                            "meta": item.location,
                            "tone": "warning",
                            "facts": (
                                [
                                    {
                                        "label": "当前听写原文",
                                        "value": item.excerpt,
                                    }
                                ]
                                if item.excerpt
                                else []
                            ),
                            "links": [],
                        }
                        for item in transcript_gate.review_targets
                    ],
                ),
            ),
            review_targets=[
                {
                    "title": item.title,
                    "location": item.location,
                    "detail": item.detail,
                    "excerpt": item.excerpt,
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                }
                for item in transcript_gate.review_targets
            ],
            status=("failed" if alignment_is_blocked else "warning" if review_recommended else "success"),
            debug={
                "description": "用于核对质量门输入、输出契约和只读检查。",
                "metrics": [
                    {
                        "label": "输入契约",
                        "value": transcript_gate.input.contract_version,
                    },
                    {
                        "label": "输出契约",
                        "value": transcript_gate.contract_version,
                    },
                    {
                        "label": "上游全文复核",
                        "value": transcript_gate.input.upstream_operation_id,
                    },
                    {
                        "label": "字幕文字不变",
                        "value": ("是" if transcript_gate.quality_summary.source_text_unchanged else "否"),
                    },
                    {
                        "label": "时间码不变",
                        "value": ("是" if transcript_gate.quality_summary.source_timing_unchanged else "否"),
                    },
                    {"label": "模型调用", "value": "0"},
                ],
                "sections": [],
                "notes": ["本步骤只做本地规则判断，不调用语言模型。"],
            },
        )
        task_step_results = transcript_quality_report.setdefault(
            "task_step_results",
            {},
        )
        if isinstance(task_step_results, dict):
            task_step_results["transcript_quality_gate"] = transcript_quality_step_result
        if alignment_is_blocked:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_FAILED",
                "进入校时前检查发现技术或结构问题，未执行逐词时间对齐。",
            )
    ensure_active()
    _report_progress(progress_callback, 0.78, "正在生成逐词时间码")
    stage_started_at = time.perf_counter()
    alignment_input = asr_timing_contracts.AsrAlignmentInput(
        alignment_audio_path=str(resolved_alignment_audio_path),
        alignment_audio_sha256=(
            alignment_audio_sha256
            or media_assets.file_sha256(resolved_alignment_audio_path)
        ),
        alignment_source_track_id=(
            alignment_source_track_id or source_track_id
        ),
        segments=reviewed_segments,
        diarization_segments=(
            initial_analysis.diarization.segments
            if initial_analysis.diarization is not None
            else []
        ),
        language=resolved_language,
        duration_ms=duration_ms,
    )
    if atomic_snapshot_callback is not None:
        atomic_snapshot_callback("alignment_input", alignment_input)
    alignment_result = run_alignment_step(alignment_input)
    if atomic_snapshot_callback is not None:
        atomic_snapshot_callback("alignment_result", alignment_result)
    words = alignment_result.words
    alignment_meta = alignment_result.metadata
    stage_timings["alignment"] = {
        "duration_ms": _elapsed_ms(stage_started_at),
        "word_count": len(words),
    }
    _report_task_step(
        report_callback,
        "alignment",
        purpose="把复核后的文字重新对齐到真实人声音频，生成逐词时间。",
        summary=f"已生成 {len(words)} 个逐词时间点。",
        metrics=(("逐词时间点", len(words)), ("对齐状态", alignment_meta.get("status") or "completed")),
    )
    ensure_active()
    _report_progress(progress_callback, 0.84, "正在分析停顿与声学边界")
    stage_started_at = time.perf_counter()
    audio_boundaries_input = (
        asr_timing_contracts.AsrAudioBoundariesInput(
            audio_path=str(audio_path),
            audio_sha256=audio_sha256,
            source_track_id=source_track_id,
            words=words,
            video_frame_rate=source_video_frame_rate,
        )
    )
    if atomic_snapshot_callback is not None:
        atomic_snapshot_callback(
            "audio_boundaries_input",
            audio_boundaries_input,
        )
    audio_boundaries_result = run_audio_boundaries_step(
        audio_boundaries_input
    )
    if atomic_snapshot_callback is not None:
        atomic_snapshot_callback(
            "audio_boundaries_result",
            audio_boundaries_result,
        )
    boundary_features = audio_boundaries_result.boundary_features
    subtitle_entry_by_word_id = (
        audio_boundaries_result.subtitle_entry_by_word_id
    )
    boundary_meta = audio_boundaries_result.metadata
    stage_timings["audio_boundaries"] = {
        "duration_ms": _elapsed_ms(stage_started_at),
        "boundary_count": len(boundary_features),
        "refined_entry_count": len(subtitle_entry_by_word_id),
    }
    _report_task_step(
        report_callback,
        "audio_boundaries",
        purpose="分析真实停顿、词间间隔和首字显示入点，为字幕断句与上屏时间提供声学依据。",
        summary=f"已分析 {len(boundary_features)} 个候选停顿，校准 {len(subtitle_entry_by_word_id)} 个字幕入点。",
        metrics=(("候选停顿", len(boundary_features)), ("字幕入点", len(subtitle_entry_by_word_id))),
    )
    ensure_active()
    _report_progress(progress_callback, 0.90, "正在按时间码与停顿生成断句")
    stage_started_at = time.perf_counter()

    def report_boundary_progress(round_progress: float, max_rounds: int, _review_count: int) -> None:
        stage_progress = 0.90 + 0.07 * min(
            1.0,
            round_progress / max(1, max_rounds),
        )
        round_label = max(1, min(max_rounds, int(round_progress + 0.999)))
        _report_progress(progress_callback, stage_progress, f"正在按时间码与停顿生成断句 · 第 {round_label} 轮")

    boundary_review_input = asr_timing_contracts.AsrBoundaryReviewInput(
        words=words,
        boundary_features=boundary_features,
        language=resolved_language,
        segmentation_profile_id=segmentation_profile_id,
        audio_analysis_available=boundary_meta.get("status") == "completed",
        profile_id=review_profile_id,
        existing_reviews=existing_boundary_reviews or [],
    )
    if atomic_snapshot_callback is not None:
        atomic_snapshot_callback(
            "boundary_review_input",
            boundary_review_input,
        )
    boundary_review_result = run_boundary_review_step(
        boundary_review_input,
        progress_callback=report_boundary_progress,
        is_cancelled=is_cancelled,
    )
    if atomic_snapshot_callback is not None:
        atomic_snapshot_callback(
            "boundary_review_result",
            boundary_review_result,
        )
    boundary_reviews = boundary_review_result.reviews
    boundary_review_meta = boundary_review_result.metadata
    stage_timings["boundary_review"] = {
        "duration_ms": _elapsed_ms(stage_started_at),
        "candidate_count": int(boundary_review_meta.get("candidate_count") or 0),
        "batch_count": int(boundary_review_meta.get("review_batch_count") or 0),
        "round_count": int(boundary_review_meta.get("review_round_count") or 0),
        "reused_review_count": int(boundary_review_meta.get("reused_review_count") or 0),
        "rounds": boundary_review_meta.get("rounds") or [],
        "profile_id": boundary_review_meta.get("profile_id"),
        "model_id": boundary_review_meta.get("model_id"),
    }
    _report_task_step(
        report_callback,
        "boundary_review",
        purpose="综合语义、逐词时间和声音停顿确定字幕断句，不改写已经锁定的 ASR 文本。",
        summary=f"已完成 {int(boundary_review_meta.get('candidate_count') or 0)} 个断句候选的本地优化。",
        metrics=(
            ("断句候选", int(boundary_review_meta.get("candidate_count") or 0)),
            ("处理轮次", int(boundary_review_meta.get("review_round_count") or 0)),
        ),
        status=(
            "warning"
            if boundary_review_meta.get("error") or int(boundary_review_meta.get("unresolved_candidate_count") or 0)
            else "success"
        ),
    )
    ensure_active()
    _report_progress(progress_callback, 0.98, "正在生成字幕轨")
    corrected_text = _join_segment_text((segment.corrected_text or segment.raw_text) for segment in reviewed_segments)
    quality_flags = sorted(
        set(
            [
                *raw_result.quality_summary.warning_codes,
                *review_meta["quality_flags"],
                *alignment_meta["quality_flags"],
                *boundary_meta["quality_flags"],
                *boundary_review_meta["quality_flags"],
                *diarization["quality_flags"],
            ]
        )
    )
    pipeline_timing = {
        "total_duration_ms": _elapsed_ms(pipeline_started_at),
        "stages": stage_timings,
    }

    return VideoLocalizationTranscriptionState(
        language=resolved_language,
        source_track_id=source_track_id,
        source_audio_sha256=source_audio_sha256 or media_assets.file_sha256(audio_path),
        alignment_source_track_id=alignment_source_track_id or source_track_id,
        alignment_audio_sha256=alignment_audio_sha256 or media_assets.file_sha256(resolved_alignment_audio_path),
        engine_id=engine_id,
        raw_text=raw_text,
        raw_asr_warning_codes=list(raw_result.quality_summary.warning_codes),
        raw_asr_incomplete_ranges=[
            VideoLocalizationAsrIncompleteRange.model_validate(item)
            for item in incomplete_chunk_ranges
        ],
        corrected_text=corrected_text,
        segments=reviewed_segments,
        words=words,
        diarization_status=diarization["status"],
        diarization_engine_id=diarization["engine_id"],
        diarization_model_id=diarization["model_id"],
        diarization_error=diarization["error"],
        speaker_clusters=diarization["clusters"],
        review_status=review_meta["status"],
        review_profile_id=review_meta.get("profile_id"),
        review_model_id=review_meta.get("model_id"),
        review_prompt_version=asr_flow.PROMPT_VERSION
        if review_meta["status"] not in {"not_configured", "skipped"}
        else None,
        review_error=review_meta.get("error"),
        transcript_quality_cycle=transcript_quality_report,
        research=research,
        alignment_status=alignment_meta["status"],
        alignment_engine_id=alignment_meta.get("engine_id"),
        alignment_error=alignment_meta.get("error"),
        timing_confidence=alignment_meta["timing_confidence"],
        audio_boundary_status=boundary_meta["status"],
        audio_boundary_analysis_version=boundary_meta.get("analysis_version"),
        audio_boundary_error=boundary_meta.get("error"),
        audio_boundary_features=boundary_features,
        boundary_review_status=boundary_review_meta["status"],
        boundary_review_profile_id=boundary_review_meta.get("profile_id"),
        boundary_review_model_id=boundary_review_meta.get("model_id"),
        boundary_review_prompt_version=boundary_review_meta.get("prompt_version"),
        boundary_review_error=boundary_review_meta.get("error"),
        boundary_reviews=boundary_reviews,
        segmentation_profile_id=segmentation_profile_id,
        quality_flags=quality_flags,
        pipeline_timing=pipeline_timing,
        subtitle_entry_by_word_id=subtitle_entry_by_word_id,
    )


def _record_final_transcript_quality_operations(
    before: list[VideoLocalizationTranscriptSegment],
    after: list[VideoLocalizationTranscriptSegment],
    changes: list[dict],
    warnings: list[dict] | None = None,
) -> list[VideoLocalizationTranscriptSegment]:
    changes_by_id: dict[str, list[dict]] = {}
    for change in changes:
        changes_by_id.setdefault(
            str(change.get("segment_id") or ""),
            [],
        ).append(change)
    warnings_by_id: dict[str, list[dict]] = {}
    for warning in warnings or []:
        if str(warning.get("code") or "") != "needs_confirmation":
            continue
        warnings_by_id.setdefault(
            str(warning.get("segment_id") or ""),
            [],
        ).append(warning)
    token_ids = _review_tokens(before)
    result = []
    for source, updated in zip(before, after):
        ids = token_ids.get(source.segment_id) or []
        if not ids:
            result.append(updated)
            continue
        operations = list(updated.review_operations)
        for change in changes_by_id.get(source.segment_id, []):
            operations.append(
                VideoLocalizationTranscriptEditOperation(
                    start_word_id=ids[0][0],
                    end_word_id=ids[-1][0],
                    source_text=str(change.get("before") or ""),
                    replacement_text=str(change.get("after") or ""),
                    reason=str(change.get("reason") or "")[:300],
                    confidence=float(change.get("confidence") or 0),
                    status="accepted",
                    evidence_source_ids=list(
                        change.get("evidence_source_ids") or []
                    ),
                )
            )
        for warning in warnings_by_id.get(source.segment_id, []):
            excerpt = str(warning.get("excerpt") or "").strip()
            if not excerpt:
                continue
            start_word_id, end_word_id = _review_operation_word_range(
                ids,
                excerpt,
            )
            message = str(warning.get("message") or "").strip()
            operations.append(
                VideoLocalizationTranscriptEditOperation(
                    start_word_id=start_word_id,
                    end_word_id=end_word_id,
                    source_text=excerpt,
                    replacement_text="",
                    reason=message[:300],
                    confidence=0.0,
                    status="rejected",
                    rejection_reason=message[:300] or None,
                    evidence_source_ids=[],
                )
            )
        if operations == updated.review_operations:
            result.append(updated)
            continue
        result.append(
            updated.model_copy(
                update={"review_operations": operations},
                deep=True,
            )
        )
    return result


def _review_operation_word_range(
    ids: list[tuple[str, str]],
    excerpt: str,
) -> tuple[str, str]:
    target = [
        token.casefold()
        for token in _display_tokens(excerpt)
        if token.strip()
    ]
    source = [token.casefold() for _, token in ids]
    if target:
        for start in range(0, len(source) - len(target) + 1):
            if source[start : start + len(target)] == target:
                return ids[start][0], ids[start + len(target) - 1][0]
    return ids[0][0], ids[-1][0]


def _report_preview(
    callback: Callable[[str, list[dict[str, Any]]], None] | None,
    phase: str,
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    corrected: bool,
) -> None:
    if callback is None:
        return
    cues = []
    for index, segment in enumerate(segments):
        text = (segment.corrected_text if corrected else segment.raw_text) or segment.raw_text
        if not text.strip():
            continue
        cues.append(
            {
                "cue_id": f"preview_{index + 1:04d}",
                "start_ms": max(0, int(segment.start_ms)),
                "end_ms": max(int(segment.start_ms) + 1, int(segment.end_ms)),
                "text": text.strip(),
            }
        )
    callback(phase, cues)


def _report_progress(callback: Callable[[float, str], None] | None, progress: float, label: str) -> None:
    if callback is not None:
        callback(progress, label)


def _report_task_step(
    callback: Callable[[str, dict], None] | None,
    step_id: str,
    *,
    purpose: str,
    summary: str,
    metrics: tuple[tuple[str, object], ...] = (),
    sections: tuple[tuple[str, list[dict]], ...] = (),
    review_targets: list[dict[str, Any]] | None = None,
    status: str = "success",
    debug: dict[str, Any] | None = None,
    duration_ms: object | None = None,
) -> dict[str, Any]:
    result = {
        "status": status,
        "purpose": purpose,
        "summary": summary,
        "metrics": [{"label": label, "value": str(value)} for label, value in metrics if value not in {None, ""}],
        "sections": [{"title": title, "items": items} for title, items in sections if items],
        "notes": [],
    }
    if review_targets:
        result["review_targets"] = review_targets
    if isinstance(duration_ms, (int, float)):
        result["duration_ms"] = max(0, int(duration_ms))
    if debug:
        result["debug"] = debug
    if callback is not None:
        callback(step_id, result)
    return result


def _short_fingerprint(value: str) -> str:
    normalized = value.strip()
    if len(normalized) <= 12:
        return normalized
    return f"{normalized[:8]}…{normalized[-4:]}"


def _joined_segment_result_items(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    limit: int = 8,
) -> list[dict]:
    if not segments:
        return []
    selected = (
        segments
        if len(segments) <= limit
        else [segments[min(len(segments) - 1, index * len(segments) // limit)] for index in range(limit)]
    )
    return [
        {
            "title": segment.speaker_cluster_id or "说话人待确认",
            "text": segment.raw_text.strip(),
            "meta": (format_timeline_range(segment.start_ms, segment.end_ms)),
            "facts": [
                {
                    "label": "匹配可信度",
                    "value": (
                        f"{round(segment.speaker_confidence * 100)}%"
                        if segment.speaker_confidence is not None
                        else "未提供"
                    ),
                },
                {
                    "label": "重叠讲话",
                    "value": "是" if segment.has_speaker_overlap else "否",
                },
            ],
            "links": [],
            "tone": (
                "warning"
                if not segment.speaker_cluster_id
                or segment.has_speaker_overlap
                or (segment.speaker_confidence is not None and segment.speaker_confidence < 0.6)
                else "neutral"
            ),
        }
        for segment in selected
    ]


def _segment_result_items(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    corrected: bool,
    limit: int = 12,
) -> list[dict]:
    rows = []
    for segment in segments[:limit]:
        text = (segment.corrected_text if corrected else segment.raw_text) or segment.raw_text
        rows.append(
            {
                "title": segment.segment_id,
                "text": text.strip(),
                "meta": format_timeline_range(
                    segment.start_ms,
                    segment.end_ms,
                ),
                "facts": [],
                "links": [],
                "tone": "neutral",
            }
        )
    return rows


def ensure_transcribe_raw_usable(result: TranscribeRawOutput) -> None:
    quality = result.quality_summary
    if not quality.has_text or not quality.has_segments:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_ASR_EMPTY",
            "语音识别没有返回有效的字幕文本，请检查音轨内容或更换识别引擎后重试。",
        )
    if not quality.timestamps_monotonic or any(
        "segment_timing_missing" in item.review_flags
        for item in result.segments
    ):
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_ASR_INCOMPLETE",
            "语音识别没有返回可验证的分段时间戳，已停止写入可能错位的字幕。请重新识别或更换识别引擎。",
        )


def ensure_transcript_covers_diarized_speech(
    segments: list[VideoLocalizationTranscriptSegment],
    diarization: dict[str, Any],
    *,
    incomplete_chunk_ranges: list[dict[str, Any]] | None = None,
) -> None:
    if diarization.get("status") != "not_run" and any(
        "segment_timing_missing" in item.review_flags for item in segments
    ):
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_ASR_INCOMPLETE",
            "语音识别没有返回可验证的分段时间戳，已停止写入可能不完整的字幕。请重新识别或更换识别引擎。",
        )
    incomplete_intervals = _merged_intervals(
        (int(item.get("start_ms") or 0), int(item.get("end_ms") or 0)) for item in (incomplete_chunk_ranges or [])
    )
    if incomplete_intervals and diarization.get("status") != "completed":
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_ASR_INCOMPLETE",
            "语音识别存在未完成的音频分块，且说话人检测未能完整确认这些范围内没有讲话，已停止写入不完整字幕。请重试。",
        )
    if diarization.get("status") not in {"completed", "partial"}:
        return
    speech_intervals = _merged_intervals(
        (int(item.get("start_ms") or 0), int(item.get("end_ms") or 0)) for item in (diarization.get("segments") or [])
    )
    speech_ms = sum(end_ms - start_ms for start_ms, end_ms in speech_intervals)
    overlapping_incomplete = next(
        (interval for interval in incomplete_intervals if _interval_overlap_ms(speech_intervals, [interval]) > 0),
        None,
    )
    if overlapping_incomplete is not None:
        first_start_ms, first_end_ms = overlapping_incomplete
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_ASR_INCOMPLETE",
            "语音识别未完成 "
            f"{format_timeline_range(first_start_ms, first_end_ms)}"
            " 的讲话分块，"
            "已停止写入不完整字幕。请重试。",
        )
    if speech_ms < MIN_DIARIZED_SPEECH_MS_FOR_COVERAGE_CHECK:
        return
    transcript_intervals = _merged_intervals((item.start_ms, item.end_ms) for item in segments if item.raw_text.strip())
    covered_ms = _interval_overlap_ms(speech_intervals, transcript_intervals)
    coverage = covered_ms / max(1, speech_ms)
    max_gap_ms = _max_uncovered_interval_ms(speech_intervals, transcript_intervals)
    if coverage >= MIN_DIARIZED_SPEECH_COVERAGE and max_gap_ms <= MAX_UNCOVERED_DIARIZED_SPEECH_GAP_MS:
        return
    raise AppException(
        400,
        "VIDEO_LOCALIZATION_ASR_INCOMPLETE",
        f"语音识别结果只覆盖了约 {round(coverage * 100)}% 的有效讲话，"
        "最长遗漏约 "
        f"{format_timeline_duration(max_gap_ms)}，"
        "已停止写入不完整字幕。请重新识别或更换识别引擎。",
    )


_ensure_transcript_covers_diarized_speech = ensure_transcript_covers_diarized_speech


def _merged_intervals(values) -> list[tuple[int, int]]:
    ordered = sorted((max(0, int(start)), max(0, int(end))) for start, end in values if int(end) > int(start))
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _interval_overlap_ms(left: list[tuple[int, int]], right: list[tuple[int, int]]) -> int:
    total = 0
    left_index = 0
    right_index = 0
    while left_index < len(left) and right_index < len(right):
        left_start, left_end = left[left_index]
        right_start, right_end = right[right_index]
        total += max(0, min(left_end, right_end) - max(left_start, right_start))
        if left_end <= right_end:
            left_index += 1
        else:
            right_index += 1
    return total


def _max_uncovered_interval_ms(speech: list[tuple[int, int]], transcript: list[tuple[int, int]]) -> int:
    max_gap = 0
    transcript_index = 0
    for speech_start, speech_end in speech:
        cursor = speech_start
        while transcript_index < len(transcript) and transcript[transcript_index][1] <= speech_start:
            transcript_index += 1
        scan_index = transcript_index
        while scan_index < len(transcript) and transcript[scan_index][0] < speech_end:
            transcript_start, transcript_end = transcript[scan_index]
            if transcript_end > cursor:
                max_gap = max(max_gap, max(0, min(speech_end, transcript_start) - cursor))
                cursor = max(cursor, transcript_end)
            scan_index += 1
        max_gap = max(max_gap, speech_end - min(cursor, speech_end))
    return max_gap


def _ensure_active(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled and is_cancelled():
        raise AppException(409, "VIDEO_LOCALIZATION_OPERATION_CANCELLED", "字幕听写任务已取消")


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _build_segments(
    items: list, fallback_text: str, duration_ms: int | None
) -> list[VideoLocalizationTranscriptSegment]:
    segments: list[VideoLocalizationTranscriptSegment] = []
    media_end_ms = int(duration_ms or 0) if duration_ms is not None else 0
    for index, item in enumerate(items, start=1):
        text = str(item.text or "").strip()
        reported_start_ms = max(0, int(item.start_ms))
        reported_end_ms = max(reported_start_ms, int(item.end_ms))
        if media_end_ms > 0 and reported_start_ms >= media_end_ms:
            continue
        start_ms = min(media_end_ms, reported_start_ms) if media_end_ms > 0 else reported_start_ms
        end_ms = min(media_end_ms, reported_end_ms) if media_end_ms > 0 else reported_end_ms
        review_flags = ["media_end_clipped"] if media_end_ms > 0 and reported_end_ms > media_end_ms else []
        if not text or end_ms <= start_ms:
            continue
        segments.append(
            VideoLocalizationTranscriptSegment(
                segment_id=f"asr_{index:04d}",
                start_ms=start_ms,
                end_ms=end_ms,
                raw_text=text,
                review_flags=review_flags,
            )
        )
    if (
        segments
        and "media_end_clipped" in segments[-1].review_flags
        and not TERMINAL_SENTENCE_END_PATTERN.search(segments[-1].raw_text.rstrip())
    ):
        segments[-1] = segments[-1].model_copy(
            update={
                "review_flags": [
                    *segments[-1].review_flags,
                    "terminal_fragment_review_required",
                ]
            }
        )
    if segments or not fallback_text:
        return segments
    return [
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0001",
            start_ms=0,
            end_ms=max(0, int(duration_ms or 0)),
            raw_text=fallback_text,
            review_flags=["segment_timing_missing"],
        )
    ]


def _resolve_transcript_language(language: str, raw_segments: list, text: str) -> str:
    requested = str(language or "auto").strip().lower()
    if requested in {"en", "zh"}:
        return requested

    aliases = {
        "en": "en",
        "english": "en",
        "zh": "zh",
        "zh-cn": "zh",
        "chinese": "zh",
    }
    detected = [aliases.get(str(getattr(segment, "language", "") or "").strip().lower()) for segment in raw_segments]
    detected = [item for item in detected if item]
    if detected:
        counts = Counter(detected)
        highest = max(counts.values())
        return next(item for item in detected if counts[item] == highest)

    cjk_count = len(CJK_PATTERN.findall(text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    return "zh" if cjk_count > latin_count else "en"


def align_segments(
    audio_path: str | Path,
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    language: str,
    max_duration_ms: int | None = None,
) -> tuple[list[VideoLocalizationAlignedWord], dict[str, Any]]:
    if not segments:
        return [], {"status": "failed", "timing_confidence": "low", "quality_flags": ["alignment_no_segments"]}

    alignment_failures: list[str] = []
    try:
        audio, sample_rate = audio_tools.read_audio(audio_path)
        health = qwen_forced_aligner.health_check()
        aligner_ready = bool(health.get("healthy"))
        if not aligner_ready:
            alignment_failures.append(str(health.get("detail") or health.get("status") or "对齐器当前不可用"))
    except Exception as exc:
        audio = None
        sample_rate = 0
        aligner_ready = False
        alignment_failures.append(str(exc))

    words: list[VideoLocalizationAlignedWord] = []
    aligned_count = 0
    alignment_quality_flags: set[str] = set()
    audio_duration_ms = int(round(len(audio) / sample_rate * 1000)) if audio is not None and sample_rate > 0 else 0
    if max_duration_ms is not None and max_duration_ms > 0:
        audio_duration_ms = (
            min(audio_duration_ms, int(max_duration_ms)) if audio_duration_ms > 0 else int(max_duration_ms)
        )
    with tempfile.TemporaryDirectory(prefix="video-localization-align-") as temp_dir:
        for window_index, (window_segments, crop_start_ms, crop_end_ms) in enumerate(
            _alignment_windows(segments, audio_duration_ms),
            start=1,
        ):
            text = _alignment_transcript_text(
                _join_segment_text((segment.corrected_text or segment.raw_text) for segment in window_segments)
            )
            aligned_items: list[dict[str, Any]] = []
            window_failed = False
            if aligner_ready and audio is not None and sample_rate > 0 and crop_end_ms > crop_start_ms and text:
                start_frame = max(0, int(sample_rate * crop_start_ms / 1000))
                end_frame = min(len(audio), max(start_frame + 1, int(sample_rate * crop_end_ms / 1000)))
                segment_path = Path(temp_dir) / f"window-{window_index:04d}.wav"
                try:
                    audio_tools.write_audio(
                        segment_path,
                        audio[start_frame:end_frame],
                        sample_rate,
                        fmt="wav",
                    )
                except Exception as exc:
                    window_failed = True
                    aligner_ready = False
                    alignment_quality_flags.add("alignment_runtime_circuit_open")
                    alignment_failures.append(
                        (
                            f"片段 {window_segments[0].segment_id}"
                            f"–{window_segments[-1].segment_id} "
                            f"对齐音频准备失败：{str(exc)[:360]}"
                        )
                    )
                else:
                    for attempt in range(1, 3):
                        try:
                            aligned_items = qwen_forced_aligner.align_audio(
                                audio_path=str(segment_path),
                                transcript_text=text,
                                language=_alignment_language(
                                    language,
                                    text,
                                ),
                            )
                        except Exception as exc:
                            aligned_items = []
                            alignment_failures.append(
                                (
                                    f"片段 {window_segments[0].segment_id}"
                                    f"–{window_segments[-1].segment_id} "
                                    f"第 {attempt} 次对齐调用失败："
                                    f"{str(exc)[:360]}"
                                )
                            )
                            if attempt == 1:
                                alignment_quality_flags.add("alignment_runtime_retried")
                                continue
                            window_failed = True
                            aligner_ready = False
                            alignment_quality_flags.add("alignment_runtime_circuit_open")
                        else:
                            if attempt == 2:
                                alignment_quality_flags.add("alignment_runtime_recovered")
                            break
            if not window_failed and aligner_ready and audio is not None and sample_rate > 0:
                window_quality_flags: set[str] = set()
                window_words = _aligned_window_words(
                    window_segments,
                    aligned_items,
                    offset=len(words),
                    window_start_ms=crop_start_ms,
                    window_end_ms=crop_end_ms,
                    quality_flags=window_quality_flags,
                )
                if window_words:
                    alignment_quality_flags.update(window_quality_flags)
                    aligned_count += len(window_segments)
                    words.extend(window_words)
                    continue
                retry_words, retry_aligned_count, retry_flags = _realign_mismatched_window(
                    segments=window_segments,
                    audio=audio,
                    sample_rate=sample_rate,
                    audio_duration_ms=audio_duration_ms,
                    temp_dir=Path(temp_dir),
                    window_key=f"{window_index:04d}",
                    offset=len(words),
                    language=language,
                    alignment_failures=alignment_failures,
                )
                retry_flags.update(window_quality_flags)
                words.extend(retry_words)
                aligned_count += retry_aligned_count
                alignment_quality_flags.update(retry_flags)
                continue
            for segment in window_segments:
                fallback_text = (segment.corrected_text or segment.raw_text).strip()
                words.extend(_interpolated_words(segment, fallback_text, len(words)))

        if aligner_ready:
            words, recovered_count, recovery_flags = _recover_interpolated_segments_with_context(
                words=words,
                segments=segments,
                audio=audio,
                sample_rate=sample_rate,
                audio_duration_ms=audio_duration_ms,
                temp_dir=Path(temp_dir),
                language=language,
                alignment_failures=alignment_failures,
            )
            aligned_count += recovered_count
            alignment_quality_flags.update(recovery_flags)

    if not any(word.timing_source == "asr_segment_interpolation" for word in words):
        alignment_quality_flags.discard("alignment_leaf_interpolated")

    words = _normalize_aligned_word_times(
        words,
        segments=segments,
        quality_flags=alignment_quality_flags,
    )
    words = _clip_words_to_duration(
        words,
        max_duration_ms=audio_duration_ms,
        quality_flags=alignment_quality_flags,
    )
    if aligned_count == len(segments):
        timing_confidence = "high" if all(word.timing_confidence == "high" for word in words) else "medium"
        quality_flags = set(alignment_quality_flags)
        if timing_confidence != "high":
            quality_flags.update({"alignment_timing_adjusted", "timing_review_required"})
        return words, {
            "status": "completed",
            "engine_id": "qwen3-forced-aligner-0.6B",
            "timing_confidence": timing_confidence,
            "quality_flags": sorted(quality_flags),
        }
    if aligned_count:
        quality_flags = set(alignment_quality_flags)
        quality_flags.update({"alignment_partial", "timing_review_required"})
        return words, {
            "status": "partial",
            "engine_id": "qwen3-forced-aligner-0.6B",
            "timing_confidence": "medium",
            "error": alignment_failures[0][:500] if alignment_failures else None,
            "quality_flags": sorted(quality_flags),
        }
    quality_flags = set(alignment_quality_flags)
    quality_flags.update({"alignment_unavailable", "timing_review_required"})
    return words, {
        "status": "failed",
        "timing_confidence": "low",
        "error": alignment_failures[0][:500] if alignment_failures else "未生成有效的词级对齐结果",
        "quality_flags": sorted(quality_flags),
    }


def align_segments_strict(
    audio_path: str | Path,
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    language: str,
    max_duration_ms: int | None = None,
) -> tuple[list[VideoLocalizationAlignedWord], dict[str, Any]]:
    """Return only Forced Aligner timings; never interpolate missing times."""

    if not segments:
        raise RuntimeError("没有可进行声学对齐的听写文字")
    health = qwen_forced_aligner.health_check()
    if not health.get("healthy"):
        raise RuntimeError(
            str(
                health.get("detail")
                or health.get("status")
                or "Qwen Forced Aligner 当前不可用"
            )
        )
    audio, sample_rate = audio_tools.read_audio(audio_path)
    if sample_rate <= 0 or len(audio) <= 0:
        raise RuntimeError("完整合成音频无法读取")
    audio_duration_ms = int(round(len(audio) / sample_rate * 1000))
    if max_duration_ms is not None:
        expected_duration_ms = int(max_duration_ms)
        if abs(audio_duration_ms - expected_duration_ms) > 1:
            raise RuntimeError(
                "完整合成音频时长与视频时长不一致："
                f"音频 {audio_duration_ms}ms，视频 {expected_duration_ms}ms"
            )

    words: list[VideoLocalizationAlignedWord] = []
    ordered = sorted(
        segments,
        key=lambda item: (
            item.start_ms,
            item.end_ms,
            item.segment_id,
        ),
    )
    with tempfile.TemporaryDirectory(
        prefix="video-localization-strict-align-"
    ) as temp_dir:
        for window_index, segment in enumerate(ordered, start=1):
            start_ms = int(segment.start_ms)
            end_ms = int(segment.end_ms)
            if (
                start_ms < 0
                or end_ms <= start_ms
                or end_ms > audio_duration_ms
            ):
                raise RuntimeError(
                    f"听写音频块 {segment.segment_id} 的范围无效："
                    f"{start_ms}–{end_ms}ms"
                )
            text = (
                segment.corrected_text or segment.raw_text
            ).strip()
            if not text:
                raise RuntimeError(
                    f"听写音频块 {segment.segment_id} 没有可对齐文字"
                )
            start_frame = int(sample_rate * start_ms / 1000)
            end_frame = int(sample_rate * end_ms / 1000)
            crop_path = (
                Path(temp_dir)
                / f"window-{window_index:04d}.wav"
            )
            audio_tools.write_audio(
                crop_path,
                audio[start_frame:end_frame],
                sample_rate,
                fmt="wav",
            )
            try:
                aligned_items = qwen_forced_aligner.align_audio(
                    audio_path=str(crop_path),
                    transcript_text=_alignment_transcript_text(text),
                    language=_alignment_language(language, text),
                )
            except Exception as exc:
                raise RuntimeError(
                    f"听写音频块 {segment.segment_id} 声学对齐失败：{exc}"
                ) from exc
            segment_words = _strict_aligned_words(
                segment,
                aligned_items,
                offset=len(words),
                window_start_ms=start_ms,
                window_end_ms=end_ms,
            )
            words.extend(segment_words)

    if not words:
        raise RuntimeError("声学对齐没有返回任何真实字词时间")
    return words, {
        "status": "completed",
        "engine_id": "qwen3-forced-aligner-0.6B",
        "timing_confidence": "high",
        "quality_flags": ["timing:forced-aligner"],
        "alignment_call_count": len(ordered),
    }


def coalesce_zero_width_alignment_words(
    words: list[VideoLocalizationAlignedWord],
) -> tuple[list[VideoLocalizationAlignedWord], dict[str, int]]:
    """Build delivery-safe acoustic units without inventing word boundaries.

    Qwen timestamps use discrete 80 ms bins and can return a lexical item as
    a point anchor. Internal dub-subtitle workflows deliberately retain those
    raw anchors. External subtitle evidence instead needs positive-duration,
    non-overlapping units, so every point-anchor run is conservatively grouped
    with one adjacent real span in the same transcript segment. The right
    neighbour is preferred when it does not overlap the preceding span;
    phrase-closing punctuation stays with the left span. This preserves more
    genuine lexical boundaries for semantic subtitle layout. Existing
    overlapping spans are grouped for the same reason. The merged unit keeps
    the real outer aligner bounds; no internal millisecond boundary is
    synthesized.
    """

    if not words:
        return [], {
            "coalesced_group_count": 0,
            "zero_width_token_count": 0,
        }

    output: list[VideoLocalizationAlignedWord] = []
    zero_width_token_count = sum(
        item.end_ms == item.start_ms for item in words
    )
    coalesced_group_count = 0
    cursor = 0
    while cursor < len(words):
        segment_id = words[cursor].segment_id
        segment_end = cursor + 1
        while (
            segment_end < len(words)
            and words[segment_end].segment_id == segment_id
        ):
            segment_end += 1
        segment_words = words[cursor:segment_end]
        intervals: list[tuple[int, int]] = []

        index = 0
        while index < len(segment_words):
            item = segment_words[index]
            if item.end_ms < item.start_ms:
                raise RuntimeError(
                    f"声学字词 {item.word_id} 的结束时间早于开始时间"
                )
            if item.end_ms > item.start_ms:
                index += 1
                continue
            run_start = index
            while (
                index + 1 < len(segment_words)
                and segment_words[index + 1].end_ms
                == segment_words[index + 1].start_ms
            ):
                index += 1
            run_end = index
            left_index = run_start - 1
            right_index = run_end + 1
            left = (
                segment_words[left_index]
                if left_index >= 0
                else None
            )
            right = (
                segment_words[right_index]
                if right_index < len(segment_words)
                else None
            )
            point_start_ms = min(
                candidate.start_ms
                for candidate in segment_words[run_start : run_end + 1]
            )
            point_end_ms = max(
                candidate.end_ms
                for candidate in segment_words[run_start : run_end + 1]
            )
            right_group_start_ms = (
                min(point_start_ms, right.start_ms)
                if right is not None
                else -1
            )
            right_is_safe = (
                right is not None
                and right.end_ms > right.start_ms
                and (
                    left is None
                    or right_group_start_ms >= left.end_ms
                )
            )
            left_group_end_ms = (
                max(left.end_ms, point_end_ms)
                if left is not None
                else -1
            )
            left_is_safe = (
                left is not None
                and left.end_ms > left.start_ms
                and (
                    right is None
                    or left_group_end_ms <= right.start_ms
                )
            )
            point_text = "".join(
                candidate.text
                for candidate in segment_words[run_start : run_end + 1]
            )
            point_closes_phrase = point_text.rstrip().endswith(
                ("，", "、", "。", "；", "：", "！", "？", ",", ";", ":", "!", "?")
            )
            if point_closes_phrase and left_is_safe:
                intervals.append((left_index, run_end))
            elif right_is_safe:
                intervals.append((run_start, right_index))
            elif left_is_safe:
                intervals.append((left_index, run_end))
            else:
                raise RuntimeError(
                    f"听写音频块 {segment_id} 的零时长字词"
                    "没有可用的正时长相邻声学单元"
                )
            index += 1

        for index in range(1, len(segment_words)):
            if (
                segment_words[index].start_ms
                < segment_words[index - 1].end_ms
            ):
                intervals.append((index - 1, index))

        merged_intervals: list[tuple[int, int]] = []
        for start, end in sorted(intervals):
            if (
                merged_intervals
                and start <= merged_intervals[-1][1]
            ):
                previous_start, previous_end = merged_intervals[-1]
                merged_intervals[-1] = (
                    previous_start,
                    max(previous_end, end),
                )
            else:
                merged_intervals.append((start, end))

        local_cursor = 0
        for interval_start, interval_end in merged_intervals:
            output.extend(segment_words[local_cursor:interval_start])
            grouped = segment_words[interval_start : interval_end + 1]
            grouped_start_ms = min(item.start_ms for item in grouped)
            grouped_end_ms = max(item.end_ms for item in grouped)
            if grouped_end_ms <= grouped_start_ms:
                raise RuntimeError(
                    f"听写音频块 {segment_id} 的合并声学单元没有正时长"
                )
            output.append(
                grouped[0].model_copy(
                    update={
                        "text": "".join(item.text for item in grouped),
                        "start_ms": grouped_start_ms,
                        "end_ms": grouped_end_ms,
                    }
                )
            )
            coalesced_group_count += 1
            local_cursor = interval_end + 1
        output.extend(segment_words[local_cursor:])
        cursor = segment_end

    previous_end_ms = -1
    for item in output:
        if item.end_ms <= item.start_ms:
            raise RuntimeError(
                f"声学字词 {item.word_id} 仍然没有正时长"
            )
        if previous_end_ms >= 0 and item.start_ms < previous_end_ms:
            raise RuntimeError(
                f"声学字词 {item.word_id} 与前一单元时间重叠"
            )
        previous_end_ms = item.end_ms

    return output, {
        "coalesced_group_count": coalesced_group_count,
        "zero_width_token_count": zero_width_token_count,
    }


def _strict_aligned_words(
    segment: VideoLocalizationTranscriptSegment,
    items: list[dict[str, Any]],
    *,
    offset: int,
    window_start_ms: int,
    window_end_ms: int,
) -> list[VideoLocalizationAlignedWord]:
    expected = _display_tokens(
        (segment.corrected_text or segment.raw_text).strip()
    )
    parsed: list[tuple[str, int, int]] = []
    previous_start_ms = window_start_ms
    audible_items = [
        item
        for item in items
        if str(item.get("text") or "").strip()
    ]
    for item_index, item in enumerate(audible_items):
        raw = str(item.get("text") or "").strip()
        try:
            start_ms = window_start_ms + int(
                round(float(item["start_time"]) * 1000)
            )
            end_ms = window_start_ms + int(
                round(float(item["end_time"]) * 1000)
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"听写音频块 {segment.segment_id} 返回了无效声学时间"
            ) from exc
        if (
            item_index == len(audible_items) - 1
            and end_ms > window_end_ms
            and end_ms - window_end_ms <= 80
            and start_ms <= window_end_ms
        ):
            # Qwen emits 80 ms timestamp ticks. The final tick can round just
            # past an integer-ms crop boundary, so cap only that final token
            # to the real readable audio window.
            end_ms = window_end_ms
        if (
            start_ms < window_start_ms
            or end_ms > window_end_ms
            or end_ms < start_ms
            or start_ms < previous_start_ms
        ):
            raise RuntimeError(
                f"听写音频块 {segment.segment_id} 返回了越界或倒序声学时间"
            )
        parsed.append((raw, start_ms, end_ms))
        previous_start_ms = start_ms

    if not parsed:
        raise RuntimeError(
            f"听写音频块 {segment.segment_id} 的声学字词数量不匹配："
            f"文字 {len(expected)}，时间 {len(parsed)}"
        )
    reconciled = _reconcile_strict_alignment_tokens(expected, parsed)
    if reconciled is None:
        if len(parsed) != len(expected):
            raise RuntimeError(
                f"听写音频块 {segment.segment_id} 的声学字词数量不匹配："
                f"文字 {len(expected)}，时间 {len(parsed)}"
            )
        raise RuntimeError(
            f"听写音频块 {segment.segment_id} 的声学字词与校对文字不一致"
        )
    words: list[VideoLocalizationAlignedWord] = []
    for expected_group, aligned_group in reconciled:
        group_start_ms = aligned_group[0][1]
        group_end_ms = aligned_group[-1][2]
        for display_text in expected_group:
            words.append(
                VideoLocalizationAlignedWord(
                    word_id=f"word_{offset + len(words) + 1:06d}",
                    segment_id=segment.segment_id,
                    text=display_text,
                    start_ms=group_start_ms,
                    end_ms=group_end_ms,
                    timing_confidence="high",
                    timing_source="forced_aligner",
                )
            )
    return words


def _reconcile_strict_alignment_tokens(
    expected: list[str],
    parsed: list[tuple[str, int, int]],
) -> list[
    tuple[list[str], list[tuple[str, int, int]]]
] | None:
    """Match exact text when the aligner joins or splits adjacent tokens.

    Qwen occasionally returns one real acoustic span for adjacent Latin
    names, such as ``Anthropic、OpenAI`` -> ``AnthropicOpenAI``. Keep the
    original display tokens, but assign every token in that exact joined
    group the same aligner-provided span. No internal boundary is invented.
    """

    expected_tokens = [
        _normalize_alignment_token(item) for item in expected
    ]
    parsed_tokens = [
        _normalize_alignment_token(item[0]) for item in parsed
    ]
    if (
        not expected_tokens
        or not parsed_tokens
        or any(not item for item in expected_tokens)
        or any(not item for item in parsed_tokens)
    ):
        return None

    groups: list[
        tuple[list[str], list[tuple[str, int, int]]]
    ] = []
    expected_index = 0
    parsed_index = 0
    while (
        expected_index < len(expected)
        and parsed_index < len(parsed)
    ):
        expected_end = expected_index + 1
        parsed_end = parsed_index + 1
        expected_joined = expected_tokens[expected_index]
        parsed_joined = parsed_tokens[parsed_index]
        while expected_joined != parsed_joined:
            if parsed_joined.startswith(expected_joined):
                if expected_end >= len(expected):
                    return None
                expected_joined += expected_tokens[expected_end]
                expected_end += 1
                continue
            if expected_joined.startswith(parsed_joined):
                if parsed_end >= len(parsed):
                    return None
                parsed_joined += parsed_tokens[parsed_end]
                parsed_end += 1
                continue
            return None
        groups.append(
            (
                expected[expected_index:expected_end],
                parsed[parsed_index:parsed_end],
            )
        )
        expected_index = expected_end
        parsed_index = parsed_end
    if expected_index != len(expected) or parsed_index != len(parsed):
        return None
    return groups


def _aligned_window_words(
    segments: list[VideoLocalizationTranscriptSegment],
    items: list[dict[str, Any]],
    *,
    offset: int,
    window_start_ms: int,
    window_end_ms: int,
    quality_flags: set[str] | None = None,
) -> list[VideoLocalizationAlignedWord]:
    expected: list[tuple[VideoLocalizationTranscriptSegment, str]] = []
    for segment in segments:
        text = (segment.corrected_text or segment.raw_text).strip()
        expected.extend((segment, token) for token in _display_tokens(text))

    parsed: list[tuple[str, int, int, bool]] = []
    for item in items:
        raw = str(item.get("text") or "").strip()
        if not raw:
            continue
        try:
            reported_start_ms = window_start_ms + int(round(float(item.get("start_time", 0)) * 1000))
            reported_end_ms = window_start_ms + int(round(float(item.get("end_time", 0)) * 1000))
        except (TypeError, ValueError):
            continue
        boundary_clipped = (
            reported_start_ms < window_start_ms
            or reported_start_ms > window_end_ms
            or reported_end_ms < window_start_ms
            or reported_end_ms > window_end_ms
        )
        start_ms = min(window_end_ms, max(window_start_ms, reported_start_ms))
        end_ms = min(window_end_ms, max(start_ms, reported_end_ms))
        parsed.append((raw, start_ms, end_ms, boundary_clipped))

    if not parsed:
        if quality_flags is not None:
            quality_flags.add("alignment_token_count_mismatch")
        return []

    if len(parsed) != len(expected):
        reconciled = _reconcile_window_alignment_boundaries(
            [token for _segment, token in expected],
            parsed,
        )
        if reconciled is None:
            if quality_flags is not None:
                quality_flags.add("alignment_token_count_mismatch")
            return []
        parsed = reconciled
        if quality_flags is not None:
            quality_flags.add("alignment_token_boundary_reconciled")

    expected_tokens = [_normalize_alignment_token(token) for _segment, token in expected]
    aligned_tokens = [_normalize_alignment_token(item[0]) for item in parsed]
    if any(not token for token in expected_tokens):
        if quality_flags is not None:
            quality_flags.add("alignment_token_mismatch")
        return []
    fuzzy_token_indexes: set[int] = set()
    if expected_tokens != aligned_tokens:
        for token_index, (expected_token, aligned_token) in enumerate(zip(expected_tokens, aligned_tokens)):
            if expected_token == aligned_token:
                continue
            if not _alignment_tokens_compatible(expected_token, aligned_token):
                if quality_flags is not None:
                    quality_flags.add("alignment_token_mismatch")
                return []
            fuzzy_token_indexes.add(token_index)
        if quality_flags is not None:
            quality_flags.add("alignment_fuzzy_token_match")

    words: list[VideoLocalizationAlignedWord] = []
    index = 0
    while index < len(parsed):
        group_end_index = index + 1
        group_start_ms = parsed[index][1]
        while group_end_index < len(parsed) and parsed[group_end_index][1] == group_start_ms:
            group_end_index += 1

        group = parsed[index:group_end_index]
        max_reported_end_ms = max(item[2] for item in group)
        fallback_end_ms = max(expected[item_index][0].end_ms for item_index in range(index, group_end_index))
        next_anchor_ms = parsed[group_end_index][1] if group_end_index < len(parsed) else fallback_end_ms
        repaired = len(group) > 1 or max_reported_end_ms <= group_start_ms
        if quality_flags is not None:
            if len(group) > 1:
                quality_flags.add("alignment_shared_anchor_repaired")
            if max_reported_end_ms <= group_start_ms:
                quality_flags.add("alignment_zero_duration_repaired")
            if any(item[3] for item in group):
                quality_flags.add("alignment_boundary_clipped")
        if max_reported_end_ms > group_start_ms:
            group_end_ms = max_reported_end_ms
        else:
            available_end_ms = next_anchor_ms
            if fallback_end_ms > group_start_ms:
                available_end_ms = min(available_end_ms, fallback_end_ms)
            local_cap_ms = group_start_ms + ZERO_DURATION_MAX_TOKEN_MS * len(group)
            group_end_ms = min(available_end_ms, local_cap_ms)
            if quality_flags is not None and group_end_ms < available_end_ms:
                quality_flags.add("alignment_zero_duration_capped")
        group_end_ms = min(window_end_ms, group_end_ms)
        if group_end_ms - group_start_ms < len(group):
            return []

        duration_ms = group_end_ms - group_start_ms
        for group_offset, item in enumerate(group):
            source_segment, display_token = expected[index + group_offset]
            start_ms = group_start_ms + round(duration_ms * group_offset / len(group))
            end_ms = group_start_ms + round(duration_ms * (group_offset + 1) / len(group))
            words.append(
                VideoLocalizationAlignedWord(
                    word_id=f"word_{offset + len(words) + 1:06d}",
                    segment_id=source_segment.segment_id,
                    text=display_token,
                    start_ms=start_ms,
                    end_ms=max(start_ms + 1, end_ms),
                    timing_confidence="medium"
                    if repaired or item[3] or index + group_offset in fuzzy_token_indexes
                    else "high",
                    timing_source="forced_aligner",
                )
            )
        index = group_end_index
    return words


def _reconcile_window_alignment_boundaries(
    expected: list[str],
    parsed: list[tuple[str, int, int, bool]],
) -> list[tuple[str, int, int, bool]] | None:
    """Reconcile exact tokenizer joins/splits without accepting changed text.

    The display tokenizer can split a decimal or domain at punctuation while
    the aligner returns one lexical item. The inverse can also happen. Reuse
    the strict exact-concatenation matcher, then project only those proven
    boundary differences back to the display-token count.
    """

    groups = _reconcile_strict_alignment_tokens(
        expected,
        [(text, start_ms, end_ms) for text, start_ms, end_ms, _clipped in parsed],
    )
    if groups is None:
        return None

    reconciled: list[tuple[str, int, int, bool]] = []
    parsed_index = 0
    for expected_group, aligned_group in groups:
        source_group = parsed[parsed_index : parsed_index + len(aligned_group)]
        parsed_index += len(aligned_group)
        if len(expected_group) == len(source_group):
            reconciled.extend(source_group)
            continue

        group_start_ms = source_group[0][1]
        group_end_ms = max(item[2] for item in source_group)
        boundary_clipped = any(item[3] for item in source_group)
        reconciled.extend(
            (token, group_start_ms, group_end_ms, boundary_clipped)
            for token in expected_group
        )
    return reconciled


def _alignment_tokens_compatible(expected: str, aligned: str) -> bool:
    if not expected or not aligned or expected[0] != aligned[0]:
        return False
    if any(character.isdigit() for character in expected + aligned):
        return False
    return SequenceMatcher(None, expected, aligned).ratio() >= 0.72


def _alignment_transcript_text(text: str) -> str:
    """Keep dash-adjacent words separate for the forced aligner tokenizer."""

    return re.sub(r"([—–])(?=\S)", r"\1 ", text)


def _alignment_windows(
    segments: list[VideoLocalizationTranscriptSegment],
    audio_duration_ms: int,
) -> list[tuple[list[VideoLocalizationTranscriptSegment], int, int]]:
    if not segments:
        return []
    duration_ms = audio_duration_ms if audio_duration_ms > 0 else max(segment.end_ms for segment in segments)
    windows: list[tuple[list[VideoLocalizationTranscriptSegment], int, int]] = []
    start = 0
    while start < len(segments):
        end = start + 1
        while end < len(segments):
            candidate_span_ms = segments[end].end_ms - segments[start].start_ms
            if candidate_span_ms > ALIGNMENT_WINDOW_MS:
                break
            end += 1
        selected = segments[start:end]
        requested_start_ms = 0 if start == 0 else max(0, selected[0].start_ms - ALIGNMENT_CONTEXT_MS)
        crop_start_ms = min(max(0, duration_ms - 1), requested_start_ms)
        requested_end_ms = duration_ms if end == len(segments) else selected[-1].end_ms + ALIGNMENT_CONTEXT_MS
        crop_end_ms = max(crop_start_ms + 1, min(duration_ms, requested_end_ms))
        windows.append((selected, crop_start_ms, crop_end_ms))
        start = end
    return windows


def _realign_mismatched_window(
    *,
    segments: list[VideoLocalizationTranscriptSegment],
    audio,
    sample_rate: int,
    audio_duration_ms: int,
    temp_dir: Path,
    window_key: str,
    offset: int,
    language: str,
    alignment_failures: list[str],
) -> tuple[list[VideoLocalizationAlignedWord], int, set[str]]:
    if len(segments) <= 1:
        segment = segments[0]
        alignment_failures.append(f"片段 {segment.segment_id} 的对齐 token 与转录文本不一致")
        text = (segment.corrected_text or segment.raw_text).strip()
        return _interpolated_words(segment, text, offset), 0, {"alignment_leaf_interpolated"}

    midpoint = len(segments) // 2
    combined_words: list[VideoLocalizationAlignedWord] = []
    aligned_count = 0
    quality_flags: set[str] = {"alignment_window_split_retried"}
    for part_index, part in enumerate((segments[:midpoint], segments[midpoint:]), start=1):
        crop_start_ms, crop_end_ms = _alignment_crop(part, audio_duration_ms)
        text = _alignment_transcript_text(
            _join_segment_text((segment.corrected_text or segment.raw_text) for segment in part)
        )
        part_path = temp_dir / f"window-{window_key}-{part_index}.wav"
        start_frame = max(0, int(sample_rate * crop_start_ms / 1000))
        end_frame = min(len(audio), max(start_frame + 1, int(sample_rate * crop_end_ms / 1000)))
        part_words: list[VideoLocalizationAlignedWord] = []
        part_flags: set[str] = set()
        part_failed = False
        try:
            audio_tools.write_audio(part_path, audio[start_frame:end_frame], sample_rate, fmt="wav")
            aligned_items = qwen_forced_aligner.align_audio(
                audio_path=str(part_path),
                transcript_text=text,
                language=_alignment_language(language, text),
            )
            part_words = _aligned_window_words(
                part,
                aligned_items,
                offset=offset + len(combined_words),
                window_start_ms=crop_start_ms,
                window_end_ms=crop_end_ms,
                quality_flags=part_flags,
            )
        except Exception as exc:
            part_failed = True
            alignment_failures.append(str(exc))

        if part_words:
            combined_words.extend(part_words)
            aligned_count += len(part)
            quality_flags.update(part_flags)
            continue
        if len(part) > 1 and not part_failed:
            nested_words, nested_count, nested_flags = _realign_mismatched_window(
                segments=part,
                audio=audio,
                sample_rate=sample_rate,
                audio_duration_ms=audio_duration_ms,
                temp_dir=temp_dir,
                window_key=f"{window_key}-{part_index}",
                offset=offset + len(combined_words),
                language=language,
                alignment_failures=alignment_failures,
            )
            combined_words.extend(nested_words)
            aligned_count += nested_count
            quality_flags.update(nested_flags)
            continue
        for segment in part:
            fallback_text = (segment.corrected_text or segment.raw_text).strip()
            combined_words.extend(_interpolated_words(segment, fallback_text, offset + len(combined_words)))
            alignment_failures.append(f"片段 {segment.segment_id} 未生成可用的词级对齐结果")
        quality_flags.add("alignment_leaf_interpolated")

    if aligned_count:
        quality_flags.add("alignment_window_split_recovered")
    return combined_words, aligned_count, quality_flags


def _alignment_crop(
    segments: list[VideoLocalizationTranscriptSegment],
    audio_duration_ms: int,
) -> tuple[int, int]:
    requested_start_ms = max(0, segments[0].start_ms - ALIGNMENT_CONTEXT_MS)
    crop_start_ms = min(max(0, audio_duration_ms - 1), requested_start_ms)
    requested_end_ms = segments[-1].end_ms + ALIGNMENT_CONTEXT_MS
    crop_end_ms = max(crop_start_ms + 1, min(audio_duration_ms, requested_end_ms))
    return crop_start_ms, crop_end_ms


def _recover_interpolated_segments_with_context(
    *,
    words: list[VideoLocalizationAlignedWord],
    segments: list[VideoLocalizationTranscriptSegment],
    audio,
    sample_rate: int,
    audio_duration_ms: int,
    temp_dir: Path,
    language: str,
    alignment_failures: list[str],
) -> tuple[list[VideoLocalizationAlignedWord], int, set[str]]:
    if audio is None or sample_rate <= 0:
        return words, 0, set()

    segment_index = {segment.segment_id: index for index, segment in enumerate(segments)}
    word_indexes_by_segment: dict[str, list[int]] = {}
    for index, word in enumerate(words):
        if word.timing_source == "asr_segment_interpolation":
            word_indexes_by_segment.setdefault(word.segment_id, []).append(index)

    recovered = list(words)
    recovered_count = 0
    quality_flags: set[str] = set()
    for segment_id, word_indexes in word_indexes_by_segment.items():
        index = segment_index.get(segment_id)
        if index is None:
            continue
        target = segments[index]
        context_start = max(0, index - 1)
        context_end = min(len(segments), index + 2)
        context_segments = segments[context_start:context_end]
        if len(context_segments) <= 1:
            continue

        crop_start_ms, crop_end_ms = _alignment_crop(context_segments, audio_duration_ms)
        if crop_end_ms <= crop_start_ms:
            continue
        start_frame = max(0, int(sample_rate * crop_start_ms / 1000))
        end_frame = min(len(audio), max(start_frame + 1, int(sample_rate * crop_end_ms / 1000)))
        context_path = temp_dir / f"context-{index:04d}.wav"
        context_text = _alignment_transcript_text(
            _join_segment_text((segment.corrected_text or segment.raw_text) for segment in context_segments)
        )
        try:
            audio_tools.write_audio(context_path, audio[start_frame:end_frame], sample_rate, fmt="wav")
            aligned_items = qwen_forced_aligner.align_audio(
                audio_path=str(context_path),
                transcript_text=context_text,
                language=_alignment_language(language, context_text),
            )
        except Exception as exc:
            alignment_failures.append(str(exc))
            continue

        context_token_counts = [
            len(_display_tokens((segment.corrected_text or segment.raw_text).strip())) for segment in context_segments
        ]
        if len(aligned_items) != sum(context_token_counts):
            alignment_failures.append(f"片段 {segment_id} 的上下文对齐 token 数量不一致")
            continue
        target_context_index = index - context_start
        token_start = sum(context_token_counts[:target_context_index])
        token_end = token_start + context_token_counts[target_context_index]
        target_items = aligned_items[token_start:token_end]
        target_flags: set[str] = set()
        target_words = _aligned_window_words(
            [target],
            target_items,
            offset=word_indexes[0],
            window_start_ms=crop_start_ms,
            window_end_ms=crop_end_ms,
            quality_flags=target_flags,
        )
        if len(target_words) != len(word_indexes):
            alignment_failures.append(f"片段 {segment_id} 的上下文对齐结果无法映射回原词序")
            continue
        for word_index, word in zip(word_indexes, target_words):
            recovered[word_index] = word
        recovered_count += 1
        quality_flags.update(target_flags)
        quality_flags.add("alignment_context_recovered")

    return recovered, recovered_count, quality_flags


def _interpolated_words(
    segment: VideoLocalizationTranscriptSegment,
    text: str,
    offset: int,
) -> list[VideoLocalizationAlignedWord]:
    tokens = _display_tokens(text)
    if not tokens:
        return []
    duration = max(1, segment.end_ms - segment.start_ms)
    weights = [max(1, len(re.sub(r"[^\w\u3400-\u9fff]", "", token))) for token in tokens]
    total = sum(weights)
    elapsed = 0
    words: list[VideoLocalizationAlignedWord] = []
    for index, (token, weight) in enumerate(zip(tokens, weights)):
        start_ms = segment.start_ms + round(duration * elapsed / total)
        elapsed += weight
        end_ms = segment.end_ms if index == len(tokens) - 1 else segment.start_ms + round(duration * elapsed / total)
        words.append(
            VideoLocalizationAlignedWord(
                word_id=f"word_{offset + index + 1:06d}",
                segment_id=segment.segment_id,
                text=token,
                start_ms=start_ms,
                end_ms=max(start_ms + 1, end_ms),
                timing_confidence="low",
                timing_source="asr_segment_interpolation",
            )
        )
    return words


def _ensure_monotonic_word_times(
    words: list[VideoLocalizationAlignedWord],
    *,
    quality_flags: set[str] | None = None,
) -> list[VideoLocalizationAlignedWord]:
    normalized: list[VideoLocalizationAlignedWord] = []
    previous_end_ms = 0
    for word in words:
        zero_duration = word.end_ms <= word.start_ms
        start_ms = max(previous_end_ms, word.start_ms)
        end_ms = max(start_ms + 1, word.end_ms)
        repaired = start_ms != word.start_ms or end_ms != word.end_ms
        if repaired and quality_flags is not None:
            quality_flags.add("alignment_monotonic_repaired")
            if zero_duration:
                quality_flags.add("alignment_zero_duration_repaired")
        timing_confidence = word.timing_confidence
        if repaired and timing_confidence == "high":
            timing_confidence = "medium"
        normalized.append(
            word.model_copy(
                update={
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "timing_confidence": timing_confidence,
                }
            )
        )
        previous_end_ms = end_ms
    return normalized


def _normalize_aligned_word_times(
    words: list[VideoLocalizationAlignedWord],
    *,
    segments: list[VideoLocalizationTranscriptSegment],
    quality_flags: set[str] | None = None,
) -> list[VideoLocalizationAlignedWord]:
    """Repair timing collapses created while enforcing monotonic word order."""

    normalized = _ensure_monotonic_word_times(
        words,
        quality_flags=quality_flags,
    )
    normalized = _repair_collapsed_word_runs(
        normalized,
        segments=segments,
        quality_flags=quality_flags,
    )
    return _ensure_monotonic_word_times(
        normalized,
        quality_flags=quality_flags,
    )


def _clip_words_to_duration(
    words: list[VideoLocalizationAlignedWord],
    *,
    max_duration_ms: int,
    quality_flags: set[str] | None = None,
) -> list[VideoLocalizationAlignedWord]:
    if max_duration_ms <= 0 or not words or all(word.end_ms <= max_duration_ms for word in words):
        return words
    if max_duration_ms < len(words):
        return words

    bounded: list[VideoLocalizationAlignedWord] = []
    previous_end_ms = 0
    for index, word in enumerate(words):
        remaining = len(words) - index - 1
        latest_end_ms = max_duration_ms - remaining
        start_ms = min(max(previous_end_ms, word.start_ms), latest_end_ms - 1)
        end_ms = min(latest_end_ms, max(start_ms + 1, word.end_ms))
        timing_changed = start_ms != word.start_ms or end_ms != word.end_ms
        bounded.append(
            word.model_copy(
                update={
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "timing_confidence": (
                        "medium" if timing_changed and word.timing_confidence == "high" else word.timing_confidence
                    ),
                }
            )
        )
        previous_end_ms = end_ms
    if quality_flags is not None:
        quality_flags.update({"alignment_media_duration_clipped", "timing_review_required"})
    return bounded


def _repair_collapsed_word_runs(
    words: list[VideoLocalizationAlignedWord],
    *,
    segments: list[VideoLocalizationTranscriptSegment],
    quality_flags: set[str] | None = None,
) -> list[VideoLocalizationAlignedWord]:
    repaired = list(words)
    segment_by_id = {segment.segment_id: segment for segment in segments}
    index = 0
    while index < len(repaired):
        if repaired[index].end_ms - repaired[index].start_ms > COLLAPSED_WORD_MAX_DURATION_MS:
            index += 1
            continue
        run_end = index + 1
        while (
            run_end < len(repaired)
            and repaired[run_end].end_ms - repaired[run_end].start_ms <= COLLAPSED_WORD_MAX_DURATION_MS
        ):
            run_end += 1
        if run_end - index < COLLAPSED_WORD_MIN_RUN:
            index = run_end
            continue

        repair_end = run_end
        right_ms = None
        lookahead_end = min(len(repaired) - 1, run_end + COLLAPSED_WORD_MAX_LOOKAHEAD)
        for probe in range(run_end - 1, lookahead_end):
            current = repaired[probe]
            following = repaired[probe + 1]
            gap_ms = following.start_ms - current.end_ms
            if gap_ms >= COLLAPSED_WORD_RECOVERY_GAP_MS and current.segment_id == following.segment_id:
                repair_end = probe + 1
                right_ms = following.start_ms
                break

        left_ms = repaired[index - 1].end_ms if index else repaired[index].start_ms
        if right_ms is None:
            impacted_segments = {repaired[word_index].segment_id for word_index in range(index, repair_end)}
            segment_end_ms = max(
                (segment_by_id[segment_id].end_ms for segment_id in impacted_segments if segment_id in segment_by_id),
                default=repaired[repair_end - 1].end_ms,
            )
            next_start_ms = repaired[repair_end].start_ms if repair_end < len(repaired) else segment_end_ms
            right_ms = min(segment_end_ms, next_start_ms) if next_start_ms > left_ms else segment_end_ms

        group = repaired[index:repair_end]
        if right_ms - left_ms < len(group) * 20:
            index = run_end
            continue
        weights = [max(1, len(re.sub(r"[^\w\u3400-\u9fff]", "", word.text))) for word in group]
        total_weight = sum(weights)
        consumed = 0
        for offset, (word, weight) in enumerate(zip(group, weights)):
            start_ms = left_ms + round((right_ms - left_ms) * consumed / total_weight)
            consumed += weight
            end_ms = (
                right_ms
                if offset == len(group) - 1
                else left_ms + round((right_ms - left_ms) * consumed / total_weight)
            )
            repaired[index + offset] = word.model_copy(
                update={
                    "start_ms": start_ms,
                    "end_ms": max(start_ms + 1, end_ms),
                    "timing_confidence": "low",
                    "timing_source": "asr_segment_interpolation",
                }
            )
        if quality_flags is not None:
            quality_flags.update(
                {
                    "alignment_collapsed_run_repaired",
                    "alignment_timing_adjusted",
                    "timing_review_required",
                }
            )
        index = repair_end
    return repaired


def _normalize_alignment_token(token: str) -> str:
    normalized = unicodedata.normalize("NFKC", token).casefold()
    return "".join(char for char in normalized if unicodedata.category(char)[0] in {"L", "N"})


def _display_tokens(text: str) -> list[str]:
    units = WORD_PATTERN.findall(text)
    output: list[str] = []
    leading_punctuation = ""
    for unit in units:
        if re.fullmatch(r"[^\w\s\u3400-\u9fff]", unit):
            if output:
                output[-1] += unit
            else:
                leading_punctuation += unit
        elif unit.strip():
            output.append(f"{leading_punctuation}{unit}")
            leading_punctuation = ""
    if leading_punctuation and output:
        output[-1] += leading_punctuation
    return output


def display_tokens(text: str) -> list[str]:
    """Public tokenizer shared by strict alignment consumers."""

    return _display_tokens(text)


def _review_tokens(
    segments: list[VideoLocalizationTranscriptSegment],
) -> dict[str, list[tuple[str, str]]]:
    output: dict[str, list[tuple[str, str]]] = {}
    word_index = 1
    for segment in segments:
        segment_tokens = []
        for token in _display_tokens(segment.raw_text):
            segment_tokens.append((f"source_word_{word_index:06d}", token))
            word_index += 1
        output[segment.segment_id] = segment_tokens
    return output


def _alignment_language(language: str, text: str) -> str:
    if language == "zh":
        return "Chinese"
    if language == "en":
        return "English"
    return "Chinese" if len(CJK_PATTERN.findall(text)) > len(re.findall(r"[A-Za-z]", text)) else "English"


def _join_segment_text(values) -> str:
    return " ".join(str(value or "").strip() for value in values if str(value or "").strip()).strip()
