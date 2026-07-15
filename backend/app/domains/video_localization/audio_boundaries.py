from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationAudioBoundaryEvidence,
)
from app.services import audio_tools


ANALYSIS_VERSION = "energy-pause-v1"
MIN_ANALYZED_GAP_MS = 80
FRAME_MS = 20
HOP_MS = 10
SPEECH_REFERENCE_MS = 180
DB_FLOOR = -96.0


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
    if gap_ms < MIN_ANALYZED_GAP_MS:
        return None

    gap_audio = _slice_ms(audio, sample_rate, start_ms, end_ms)
    left_reference = _slice_ms(audio, sample_rate, max(left.start_ms, left.end_ms - SPEECH_REFERENCE_MS), left.end_ms)
    right_reference = _slice_ms(audio, sample_rate, right.start_ms, min(right.end_ms, right.start_ms + SPEECH_REFERENCE_MS))
    reference_audio = np.concatenate([chunk for chunk in (left_reference, right_reference) if chunk.size])
    speech_reference_dbfs = _dbfs(reference_audio)
    gap_rms_dbfs = _dbfs(gap_audio)

    # Stay below nearby speech while adapting to the local recording floor. A
    # loud music bed therefore cannot masquerade as a clean speech pause.
    threshold_dbfs = min(
        speech_reference_dbfs - 6.0,
        max(noise_floor_dbfs + 3.0, speech_reference_dbfs - 16.0),
    )
    frame_dbfs = _overlapping_frame_dbfs(gap_audio, sample_rate)
    low_energy_mask = frame_dbfs <= threshold_dbfs
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
