from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationAudioBoundaryEvidence,
)
from app.schemas.video_localization_dubbing_production import (
    DubbingAudioGapEvidence,
    DubbingAutomaticAudioEvidence,
    DubbingPauseEvidence,
    DubbingVoicedSpan,
)
from app.services import audio_tools


ANALYSIS_VERSION = "energy-pause-v2"
MIN_ANALYZED_GAP_MS = 20
FRAME_MS = 20
HOP_MS = 10
SPEECH_REFERENCE_MS = 180
DB_FLOOR = -96.0
CLIPPING_AMPLITUDE = 10 ** (-0.1 / 20)


def analyze_dubbing_candidate_audio(
    audio_path: str | Path,
    *,
    expected_pause_baseline_ms: int | None = None,
    max_leading_silence_ms: int | None = None,
    max_trailing_silence_ms: int | None = None,
    speaking_rate_ratio: float | None = None,
    project_speaking_rate_ratio_min: float | None = None,
    project_speaking_rate_ratio_max: float | None = None,
    content_speed_exception_reason: str | None = None,
    content_speed_exception_evidence_ids: list[str] | None = None,
) -> DubbingAutomaticAudioEvidence:
    """Measure candidate audio without claiming semantic or listening quality.

    Silence is detected relative to the candidate's own speech energy.  The
    returned internal regions are only pause *candidates*: semantic-boundary
    and safe-edit decisions deliberately remain unknown until an Agent aligns
    them with the script and listens to the audio.
    """

    audio, sample_rate = audio_tools.read_audio(audio_path)
    if not sample_rate or not audio.size:
        raise ValueError("音频为空，无法执行配音候选自动检测")
    duration_ms = max(1, round(len(audio) * 1000 / sample_rate))
    frame_dbfs = _overlapping_frame_dbfs(audio, sample_rate)
    if not frame_dbfs.size:
        raise ValueError("音频太短，无法执行配音候选自动检测")

    speech_reference_dbfs = float(np.percentile(frame_dbfs, 80))
    noise_floor_dbfs = float(np.percentile(frame_dbfs, 15))
    threshold_dbfs = min(
        speech_reference_dbfs - 12.0,
        max(noise_floor_dbfs + 3.0, speech_reference_dbfs - 24.0),
    )
    silent_mask, thresholds_dbfs = _consensus_silent_mask(
        frame_dbfs,
        threshold_dbfs=threshold_dbfs,
        speech_reference_dbfs=speech_reference_dbfs,
    )
    runs = _mask_runs_ms(silent_mask, duration_ms=duration_ms)
    leading_ms = runs[0][1] if runs and runs[0][0] == 0 else 0
    trailing_ms = (
        duration_ms - runs[-1][0]
        if runs and runs[-1][1] == duration_ms
        else 0
    )
    internal_pauses = [
        DubbingPauseEvidence(
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=end_ms - start_ms,
            expected_semantic_boundary=False,
            safe_edit_boundary=None,
        )
        for start_ms, end_ms in runs
        if start_ms > 0
        and end_ms < duration_ms
        and end_ms - start_ms >= MIN_ANALYZED_GAP_MS
    ]
    measured_gaps = [
        ("leading" if start_ms == 0 else "trailing" if end_ms == duration_ms else "internal", start_ms, end_ms)
        for start_ms, end_ms in runs
        if end_ms > start_ms
        and (start_ms == 0 or end_ms == duration_ms or end_ms - start_ms >= MIN_ANALYZED_GAP_MS)
    ]
    gap_evidence = [
        DubbingAudioGapEvidence(
            gap_id=f"gap_{kind}_{start_ms}_{end_ms}",
            kind=kind,
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=end_ms - start_ms,
            evidence_sources=["waveform", "energy"],
            evidence_ids=[
                f"{ANALYSIS_VERSION}:waveform:{start_ms}:{end_ms}",
                (
                    f"{ANALYSIS_VERSION}:energy-consensus:"
                    f"{','.join(f'{value:.2f}' for value in thresholds_dbfs)}:"
                    f"{start_ms}:{end_ms}"
                ),
            ],
            boundary_confidence=("ambiguous" if kind == "internal" else "clear"),
            edit_decision="retain",
            retained_duration_ms=end_ms - start_ms,
            decision_reason=(
                "自动能量分析只定位内部静音候选；交给当前片段的词级气口处理器决定"
                if kind == "internal"
                else "保留自动测得的自然首尾气口"
            ),
            safe_edit_boundary=(None if kind == "internal" else True),
        )
        for kind, start_ms, end_ms in measured_gaps
    ]
    voiced_runs = _mask_runs_ms(~silent_mask, duration_ms=duration_ms)
    voiced_spans = [
        DubbingVoicedSpan(start_ms=start_ms, end_ms=end_ms)
        for start_ms, end_ms in voiced_runs
        if end_ms > start_ms
    ]
    speech_start_ms = voiced_spans[0].start_ms if voiced_spans else 0
    speech_end_ms = voiced_spans[-1].end_ms if voiced_spans else duration_ms
    peak = float(np.max(np.abs(audio)))
    clipping_ratio = float(np.mean(np.abs(audio) >= CLIPPING_AMPLITUDE))
    return DubbingAutomaticAudioEvidence(
        duration_ms=duration_ms,
        peak_dbfs=round(20.0 * math.log10(max(peak, 10 ** (DB_FLOOR / 20))), 2),
        clipping_ratio=round(clipping_ratio, 8),
        leading_silence_ms=leading_ms,
        trailing_silence_ms=trailing_ms,
        speech_start_ms=speech_start_ms,
        speech_end_ms=speech_end_ms,
        speech_span_ms=max(1, speech_end_ms - speech_start_ms),
        voiced_spans=voiced_spans,
        voiced_duration_ms=(
            sum(span.end_ms - span.start_ms for span in voiced_spans)
            if voiced_spans
            else None
        ),
        speaking_rate_ratio=speaking_rate_ratio,
        project_speaking_rate_ratio_min=project_speaking_rate_ratio_min,
        project_speaking_rate_ratio_max=project_speaking_rate_ratio_max,
        content_speed_exception_reason=content_speed_exception_reason,
        content_speed_exception_evidence_ids=(
            content_speed_exception_evidence_ids or []
        ),
        gap_evidence=gap_evidence,
        internal_pauses=internal_pauses,
        expected_pause_baseline_ms=expected_pause_baseline_ms,
        max_leading_silence_ms=max_leading_silence_ms,
        max_trailing_silence_ms=max_trailing_silence_ms,
    )


def analyze_word_boundaries(
    audio_path: str | Path,
    words: list[VideoLocalizationAlignedWord],
) -> tuple[list[VideoLocalizationAudioBoundaryEvidence], dict[str, object]]:
    """Measure low-energy evidence between aligned words without changing timing."""

    if len(words) < 2:
        return [], {
            "status": "skipped",
            "analysis_version": ANALYSIS_VERSION,
            "quality_flags": ["audio_boundary_analysis_skipped"],
        }

    try:
        audio, sample_rate = audio_tools.read_audio(audio_path)
        if not sample_rate or not audio.size:
            raise ValueError("音频为空，无法分析停顿")
        frame_samples = max(1, round(sample_rate * FRAME_MS / 1000))
        global_frame_dbfs = _non_overlapping_frame_dbfs(audio, frame_samples)
        noise_floor_dbfs = float(np.percentile(global_frame_dbfs, 20)) if global_frame_dbfs.size else DB_FLOOR
        features = [
            feature
            for left, right in zip(words, words[1:])
            if (
                feature := _boundary_evidence(
                    audio,
                    sample_rate,
                    left,
                    right,
                    noise_floor_dbfs=noise_floor_dbfs,
                )
            )
            is not None
        ]
        return features, {
            "status": "completed",
            "analysis_version": ANALYSIS_VERSION,
            "boundary_count": len(features),
            "threshold_count": 3,
            "quality_flags": ["audio_boundary_analysis_completed"],
        }
    except Exception as exc:
        return [], {
            "status": "failed",
            "analysis_version": ANALYSIS_VERSION,
            "error": str(exc)[:500],
            "quality_flags": ["audio_boundary_analysis_failed"],
        }


def _boundary_evidence(
    audio: np.ndarray,
    sample_rate: int,
    left: VideoLocalizationAlignedWord,
    right: VideoLocalizationAlignedWord,
    *,
    noise_floor_dbfs: float,
) -> VideoLocalizationAudioBoundaryEvidence | None:
    start_ms = max(0, left.end_ms)
    end_ms = max(start_ms, right.start_ms)
    gap_ms = end_ms - start_ms
    gap_audio = _slice_ms(audio, sample_rate, start_ms, end_ms)
    left_reference = _slice_ms(audio, sample_rate, max(left.start_ms, left.end_ms - SPEECH_REFERENCE_MS), left.end_ms)
    right_reference = _slice_ms(audio, sample_rate, right.start_ms, min(right.end_ms, right.start_ms + SPEECH_REFERENCE_MS))
    reference_chunks = [
        chunk
        for chunk in (left_reference, right_reference)
        if chunk.size
    ]
    reference_audio = (
        np.concatenate(reference_chunks)
        if reference_chunks
        else np.array([], dtype=audio.dtype)
    )
    speech_reference_dbfs = _dbfs(reference_audio)
    gap_rms_dbfs = _dbfs(gap_audio)

    # Stay below nearby speech while adapting to the local recording floor. A
    # loud music bed therefore cannot masquerade as a clean speech pause.
    threshold_dbfs = min(
        speech_reference_dbfs - 6.0,
        max(noise_floor_dbfs + 3.0, speech_reference_dbfs - 16.0),
    )
    frame_dbfs = _overlapping_frame_dbfs(gap_audio, sample_rate)
    low_energy_mask, _thresholds_dbfs = _consensus_silent_mask(
        frame_dbfs,
        threshold_dbfs=threshold_dbfs,
        speech_reference_dbfs=speech_reference_dbfs,
    )
    low_energy_ratio = float(np.mean(low_energy_mask)) if low_energy_mask.size else 0.0
    low_energy_ms = min(gap_ms, _longest_low_energy_ms(low_energy_mask))
    energy_drop_db = max(0.0, speech_reference_dbfs - gap_rms_dbfs)
    confidence = _pause_confidence(gap_ms, low_energy_ms, low_energy_ratio, energy_drop_db)
    if "low" in {left.timing_confidence, right.timing_confidence}:
        confidence = "low" if confidence != "none" else "none"
    elif "medium" in {left.timing_confidence, right.timing_confidence} and confidence == "high":
        confidence = "medium"

    return VideoLocalizationAudioBoundaryEvidence(
        boundary_id=f"{left.word_id}:{right.word_id}",
        left_word_id=left.word_id,
        right_word_id=right.word_id,
        start_ms=start_ms,
        end_ms=end_ms,
        gap_ms=gap_ms,
        low_energy_ms=low_energy_ms,
        low_energy_ratio=round(low_energy_ratio, 4),
        gap_rms_dbfs=round(gap_rms_dbfs, 2),
        speech_reference_dbfs=round(speech_reference_dbfs, 2),
        noise_floor_dbfs=round(noise_floor_dbfs, 2),
        energy_drop_db=round(energy_drop_db, 2),
        confidence=confidence,
        analysis_version=ANALYSIS_VERSION,
    )


def _pause_confidence(gap_ms: int, low_energy_ms: int, low_energy_ratio: float, energy_drop_db: float) -> str:
    if gap_ms >= 250 and low_energy_ms >= 200 and low_energy_ratio >= 0.65 and energy_drop_db >= 6.0:
        return "high"
    if gap_ms >= 180 and low_energy_ms >= 120 and low_energy_ratio >= 0.45 and energy_drop_db >= 3.0:
        return "medium"
    if gap_ms >= 120 and low_energy_ms >= 80 and low_energy_ratio >= 0.3 and energy_drop_db >= 1.5:
        return "low"
    return "none"


def _consensus_silent_mask(
    frame_dbfs: np.ndarray,
    *,
    threshold_dbfs: float,
    speech_reference_dbfs: float,
) -> tuple[np.ndarray, tuple[float, float, float]]:
    """Return silence supported by at least two nearby energy thresholds.

    A single threshold is brittle around breaths and low-volume syllables.
    Three bounded thresholds keep the detector deterministic while requiring
    a majority before a frame is treated as editable silence.
    """

    thresholds = tuple(
        min(speech_reference_dbfs - 4.0, threshold_dbfs + offset)
        for offset in (-4.0, 0.0, 4.0)
    )
    if not frame_dbfs.size:
        return np.array([], dtype=bool), thresholds
    votes = np.zeros(frame_dbfs.shape, dtype=np.uint8)
    for value in thresholds:
        votes += frame_dbfs <= value
    return votes >= 2, thresholds


def _slice_ms(audio: np.ndarray, sample_rate: int, start_ms: int, end_ms: int) -> np.ndarray:
    start = min(len(audio), max(0, round(sample_rate * start_ms / 1000)))
    end = min(len(audio), max(start, round(sample_rate * end_ms / 1000)))
    return audio[start:end]


def _dbfs(audio: np.ndarray) -> float:
    if not audio.size:
        return DB_FLOOR
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    return max(DB_FLOOR, 20.0 * math.log10(max(rms, 10 ** (DB_FLOOR / 20))))


def _non_overlapping_frame_dbfs(audio: np.ndarray, frame_samples: int) -> np.ndarray:
    if not audio.size:
        return np.array([], dtype=np.float32)
    usable = len(audio) - (len(audio) % frame_samples)
    if usable <= 0:
        return np.array([_dbfs(audio)], dtype=np.float32)
    frames = audio[:usable].reshape(-1, frame_samples)
    rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
    return np.maximum(DB_FLOOR, 20.0 * np.log10(np.maximum(rms, 10 ** (DB_FLOOR / 20))))


def _overlapping_frame_dbfs(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    if not audio.size:
        return np.array([], dtype=np.float32)
    frame_samples = max(1, round(sample_rate * FRAME_MS / 1000))
    hop_samples = max(1, round(sample_rate * HOP_MS / 1000))
    if len(audio) <= frame_samples:
        return np.array([_dbfs(audio)], dtype=np.float32)
    starts = range(0, len(audio) - frame_samples + 1, hop_samples)
    return np.array([_dbfs(audio[start : start + frame_samples]) for start in starts], dtype=np.float32)


def _longest_low_energy_ms(mask: np.ndarray) -> int:
    longest = 0
    current = 0
    for is_low in mask.tolist():
        current = current + 1 if is_low else 0
        longest = max(longest, current)
    if longest <= 0:
        return 0
    return FRAME_MS + (longest - 1) * HOP_MS


def _mask_runs_ms(
    mask: np.ndarray,
    *,
    duration_ms: int,
) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for index, is_silent in enumerate(mask.tolist()):
        if is_silent and run_start is None:
            run_start = index
        if not is_silent and run_start is not None:
            runs.append(
                (
                    run_start * HOP_MS,
                    min(duration_ms, (index - 1) * HOP_MS + FRAME_MS),
                )
            )
            run_start = None
    if run_start is not None:
        runs.append((run_start * HOP_MS, duration_ms))
    return runs
