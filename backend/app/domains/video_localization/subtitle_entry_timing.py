"""Single acoustic authority for subtitle display entry timing."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
)
from app.services import audio_tools


MIN_PEAK_OFFSET_FRAMES = 1.0
MAX_PEAK_OFFSET_FRAMES = 3.0
PEAK_FRAME_MS = 10
PEAK_HOP_MS = 2
PEAK_SMOOTH_MS = 10

ONSET_FRAME_MS = 20
ONSET_HOP_MS = 10
ONSET_MAX_SCAN_MS = 600
ONSET_EARLY_TOLERANCE_MS = 50
ONSET_PREROLL_MS = 30
ONSET_PRE_ROLL_ANALYSIS_MS = 250
ONSET_MIN_ACTIVE_FRAMES = 3

ZERO_END_REPAIR_FRAME_MS = 20
ZERO_END_REPAIR_HOP_MS = 10
ZERO_END_REPAIR_MAX_SCAN_MS = 600
ZERO_END_REPAIR_MAX_ONSET_LAG_MS = 80
ZERO_END_REPAIR_MIN_ACTIVE_FRAMES = 2
ZERO_END_REPAIR_MIN_SILENT_FRAMES = 3


def repair_zero_duration_word_ends(
    audio_path: str | Path,
    words: list[VideoLocalizationAlignedWord],
) -> tuple[list[VideoLocalizationAlignedWord], int]:
    """Recover only a real acoustic end for an aligner-provided onset.

    Qwen can occasionally return a valid word onset with ``start == end`` for
    a short interjection. The onset remains authoritative; the end is accepted
    only when the waveform contains a sustained active run followed by a
    sustained low-energy boundary before the next aligned word.
    """

    if not any(word.end_ms <= word.start_ms for word in words):
        return words, 0
    try:
        audio, sample_rate = audio_tools.read_audio(audio_path)
    except Exception:
        return words, 0
    return repair_zero_duration_word_ends_from_audio(
        audio,
        sample_rate,
        words,
    )


def repair_zero_duration_word_ends_from_audio(
    audio: np.ndarray,
    sample_rate: int,
    words: list[VideoLocalizationAlignedWord],
) -> tuple[list[VideoLocalizationAlignedWord], int]:
    if sample_rate <= 0 or not np.asarray(audio).size:
        return words, 0
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim > 1:
        samples = np.mean(samples, axis=-1)
    repaired: list[VideoLocalizationAlignedWord] = []
    repair_count = 0
    for index, word in enumerate(words):
        if word.end_ms > word.start_ms:
            repaired.append(word)
            continue
        next_start_ms = next(
            (
                candidate.start_ms
                for candidate in words[index + 1 :]
                if candidate.segment_id == word.segment_id
                and candidate.start_ms > word.start_ms
            ),
            None,
        )
        if next_start_ms is None:
            repaired.append(word)
            continue
        scan_end_ms = min(
            next_start_ms,
            word.start_ms + ZERO_END_REPAIR_MAX_SCAN_MS,
        )
        detected_end_ms = _sustained_activity_end_ms(
            samples,
            sample_rate,
            start_ms=word.start_ms,
            end_ms=scan_end_ms,
        )
        if detected_end_ms is None:
            repaired.append(word)
            continue
        repaired.append(
            word.model_copy(update={"end_ms": detected_end_ms})
        )
        repair_count += 1
    return repaired, repair_count


def _sustained_activity_end_ms(
    samples: np.ndarray,
    sample_rate: int,
    *,
    start_ms: int,
    end_ms: int,
) -> int | None:
    if end_ms - start_ms < (
        ZERO_END_REPAIR_FRAME_MS
        + ZERO_END_REPAIR_HOP_MS
        * ZERO_END_REPAIR_MIN_SILENT_FRAMES
    ):
        return None
    start_sample = _sample_index(samples, sample_rate, start_ms)
    end_sample = _sample_index(samples, sample_rate, end_ms)
    frame_dbfs = _overlapping_dbfs(
        samples[start_sample:end_sample],
        sample_rate,
        frame_ms=ZERO_END_REPAIR_FRAME_MS,
        hop_ms=ZERO_END_REPAIR_HOP_MS,
    )
    if frame_dbfs.size < (
        ZERO_END_REPAIR_MIN_ACTIVE_FRAMES
        + ZERO_END_REPAIR_MIN_SILENT_FRAMES
    ):
        return None
    pre_roll_start = _sample_index(
        samples,
        sample_rate,
        max(0, start_ms - ONSET_PRE_ROLL_ANALYSIS_MS),
    )
    pre_roll_dbfs = _overlapping_dbfs(
        samples[pre_roll_start:start_sample],
        sample_rate,
        frame_ms=ZERO_END_REPAIR_FRAME_MS,
        hop_ms=ZERO_END_REPAIR_HOP_MS,
    )
    local_floor_dbfs = (
        float(np.percentile(pre_roll_dbfs, 35))
        if pre_roll_dbfs.size
        else float(np.percentile(frame_dbfs, 15))
    )
    speech_reference_dbfs = float(np.percentile(frame_dbfs, 90))
    if speech_reference_dbfs - local_floor_dbfs < 7.0:
        return None
    threshold_dbfs = max(
        local_floor_dbfs + 7.0,
        speech_reference_dbfs - 12.0,
    )
    active = frame_dbfs >= threshold_dbfs
    max_onset_index = min(
        len(active) - ZERO_END_REPAIR_MIN_ACTIVE_FRAMES,
        ZERO_END_REPAIR_MAX_ONSET_LAG_MS
        // ZERO_END_REPAIR_HOP_MS,
    )
    onset_index = next(
        (
            index
            for index in range(max_onset_index + 1)
            if bool(
                np.all(
                    active[
                        index : index
                        + ZERO_END_REPAIR_MIN_ACTIVE_FRAMES
                    ]
                )
            )
        ),
        None,
    )
    if onset_index is None:
        return None
    silence_index = next(
        (
            index
            for index in range(
                onset_index + ZERO_END_REPAIR_MIN_ACTIVE_FRAMES,
                len(active) - ZERO_END_REPAIR_MIN_SILENT_FRAMES + 1,
            )
            if bool(
                np.all(
                    ~active[
                        index : index
                        + ZERO_END_REPAIR_MIN_SILENT_FRAMES
                    ]
                )
            )
        ),
        None,
    )
    if silence_index is None:
        return None
    detected_end_ms = start_ms + (
        silence_index * ZERO_END_REPAIR_HOP_MS
    )
    return (
        detected_end_ms
        if start_ms < detected_end_ms < end_ms
        else None
    )


def detect_subtitle_entries(
    audio_path: str | Path,
    words: list[VideoLocalizationAlignedWord],
    *,
    frame_rate: float | None,
) -> dict[str, int]:
    """Choose each word's display entry from one shared acoustic policy.

    A stable short-time-energy peak between one and three actual video frames
    after the aligner boundary is preferred. If no repeatable peak exists, the
    conservative sustained-speech onset is used; otherwise callers keep FA.
    """

    if not words:
        return {}
    try:
        audio, sample_rate = audio_tools.read_audio(audio_path)
    except Exception:
        return {}
    return detect_subtitle_entries_from_audio(
        audio,
        sample_rate,
        words,
        frame_rate=frame_rate,
    )


def detect_subtitle_entries_from_audio(
    audio: np.ndarray,
    sample_rate: int,
    words: list[VideoLocalizationAlignedWord],
    *,
    frame_rate: float | None,
) -> dict[str, int]:
    if sample_rate <= 0 or not audio.size:
        return {}
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim > 1:
        samples = np.mean(samples, axis=-1)
    resolved_frame_rate = _valid_frame_rate(frame_rate)
    entries = _detect_sustained_onsets(samples, sample_rate, words)
    if resolved_frame_rate is not None:
        for word in words:
            peak_ms = _first_stable_energy_peak_ms(
                samples,
                sample_rate,
                word,
                frame_rate=resolved_frame_rate,
            )
            if peak_ms is None:
                peak_ms = _first_local_energy_peak_ms(
                    samples,
                    sample_rate,
                    word,
                    frame_rate=resolved_frame_rate,
                )
            if peak_ms is None and word.word_id not in entries:
                peak_ms = _strongest_energy_event_ms(
                    samples,
                    sample_rate,
                    word,
                    frame_rate=resolved_frame_rate,
                )
            if peak_ms is not None:
                entries[word.word_id] = peak_ms
    return entries


def _valid_frame_rate(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(resolved) or resolved <= 0:
        return None
    return resolved


def _detect_sustained_onsets(
    samples: np.ndarray,
    sample_rate: int,
    words: list[VideoLocalizationAlignedWord],
) -> dict[str, int]:
    frame_samples = max(1, round(sample_rate * ONSET_FRAME_MS / 1000))
    usable = len(samples) - len(samples) % frame_samples
    if usable > 0:
        frames = samples[:usable].reshape(-1, frame_samples)
        frame_rms = np.sqrt(
            np.mean(np.square(frames, dtype=np.float64), axis=1)
        )
        global_dbfs = 20.0 * np.log10(np.maximum(frame_rms, 1e-6))
        noise_floor_dbfs = float(np.percentile(global_dbfs, 20))
    else:
        noise_floor_dbfs = -96.0

    refined: dict[str, int] = {}
    for word in words:
        scan_end_ms = min(word.end_ms, word.start_ms + ONSET_MAX_SCAN_MS)
        if scan_end_ms - word.start_ms < ONSET_FRAME_MS * 2:
            continue
        start_sample = _sample_index(samples, sample_rate, word.start_ms)
        end_sample = _sample_index(samples, sample_rate, scan_end_ms)
        frame_dbfs = _overlapping_dbfs(
            samples[start_sample:end_sample],
            sample_rate,
            frame_ms=ONSET_FRAME_MS,
            hop_ms=ONSET_HOP_MS,
        )
        if frame_dbfs.size < 2:
            continue
        speech_reference_dbfs = float(np.percentile(frame_dbfs, 90))
        pre_roll_start_ms = max(
            0,
            word.start_ms - ONSET_PRE_ROLL_ANALYSIS_MS,
        )
        pre_roll_start = _sample_index(
            samples,
            sample_rate,
            pre_roll_start_ms,
        )
        pre_roll_end = _sample_index(
            samples,
            sample_rate,
            word.start_ms,
        )
        pre_roll_dbfs = _overlapping_dbfs(
            samples[pre_roll_start:pre_roll_end],
            sample_rate,
            frame_ms=ONSET_FRAME_MS,
            hop_ms=ONSET_HOP_MS,
        )
        local_noise_floor_dbfs = (
            max(
                noise_floor_dbfs,
                float(np.percentile(pre_roll_dbfs, 35)),
            )
            if pre_roll_dbfs.size
            else noise_floor_dbfs
        )
        if speech_reference_dbfs - local_noise_floor_dbfs < 7.0:
            continue
        threshold_dbfs = max(
            local_noise_floor_dbfs + 7.0,
            speech_reference_dbfs - 8.0,
        )
        active = frame_dbfs >= threshold_dbfs
        onset_index = next(
            (
                index
                for index in range(
                    len(active) - ONSET_MIN_ACTIVE_FRAMES + 1
                )
                if bool(
                    np.all(
                        active[index : index + ONSET_MIN_ACTIVE_FRAMES]
                    )
                )
            ),
            None,
        )
        if onset_index is None:
            continue
        detected_onset_ms = word.start_ms + onset_index * ONSET_HOP_MS
        if detected_onset_ms - word.start_ms < ONSET_EARLY_TOLERANCE_MS:
            continue
        refined[word.word_id] = max(
            word.start_ms,
            detected_onset_ms - ONSET_PREROLL_MS,
        )
    return refined


def _first_stable_energy_peak_ms(
    samples: np.ndarray,
    sample_rate: int,
    word: VideoLocalizationAlignedWord,
    *,
    frame_rate: float,
) -> int | None:
    video_frame_ms = 1000.0 / frame_rate
    min_offset_ms = MIN_PEAK_OFFSET_FRAMES * video_frame_ms
    max_offset_ms = MAX_PEAK_OFFSET_FRAMES * video_frame_ms
    # A repaired Forced Aligner token may have an artificially short end_ms.
    # The subtitle entry authority is the first-word acoustic peak, so the
    # actual video-frame search window must not be clipped at that provisional
    # token end boundary.
    scan_end_ms = word.start_ms + math.ceil(
        max_offset_ms + PEAK_FRAME_MS + PEAK_HOP_MS
    )
    if scan_end_ms - word.start_ms < min_offset_ms:
        return None
    start_sample = _sample_index(samples, sample_rate, word.start_ms)
    end_sample = _sample_index(samples, sample_rate, scan_end_ms)
    frame_dbfs = _overlapping_dbfs(
        samples[start_sample:end_sample],
        sample_rate,
        frame_ms=PEAK_FRAME_MS,
        hop_ms=PEAK_HOP_MS,
    )
    if frame_dbfs.size < 3:
        return None
    envelope = _smooth(frame_dbfs)

    pre_roll_start = _sample_index(
        samples,
        sample_rate,
        max(0, word.start_ms - ONSET_PRE_ROLL_ANALYSIS_MS),
    )
    pre_roll_end = _sample_index(samples, sample_rate, word.start_ms)
    pre_roll_dbfs = _overlapping_dbfs(
        samples[pre_roll_start:pre_roll_end],
        sample_rate,
        frame_ms=ONSET_FRAME_MS,
        hop_ms=ONSET_HOP_MS,
    )
    local_noise_floor_dbfs = (
        float(np.percentile(pre_roll_dbfs, 35))
        if pre_roll_dbfs.size
        else -96.0
    )
    speech_reference_dbfs = float(np.percentile(envelope, 90))
    if speech_reference_dbfs - local_noise_floor_dbfs < 7.0:
        return None
    threshold_dbfs = max(
        local_noise_floor_dbfs + 7.0,
        speech_reference_dbfs - 8.0,
    )
    for index in range(1, len(envelope) - 1):
        peak_offset_ms = index * PEAK_HOP_MS + PEAK_FRAME_MS / 2
        if peak_offset_ms < min_offset_ms:
            continue
        if peak_offset_ms > max_offset_ms:
            break
        current = float(envelope[index])
        if current < threshold_dbfs:
            continue
        if not (
            current >= float(envelope[index - 1])
            and current > float(envelope[index + 1])
        ):
            continue
        peak_ms = round(word.start_ms + peak_offset_ms)
        if word.start_ms < peak_ms < scan_end_ms:
            return peak_ms
    return None


def _first_local_energy_peak_ms(
    samples: np.ndarray,
    sample_rate: int,
    word: VideoLocalizationAlignedWord,
    *,
    frame_rate: float,
) -> int | None:
    """Return the first local waveform-energy peak in the 1–3 frame window.

    This is the deterministic fallback for real speech whose local peak does
    not clear the stricter speech-vs-noise threshold. It still requires an
    actual rise and fall in the smoothed energy envelope; a flat plateau falls
    through to the sustained-onset detector.
    """

    video_frame_ms = 1000.0 / frame_rate
    min_offset_ms = MIN_PEAK_OFFSET_FRAMES * video_frame_ms
    max_offset_ms = MAX_PEAK_OFFSET_FRAMES * video_frame_ms
    scan_end_ms = word.start_ms + math.ceil(
        max_offset_ms + PEAK_FRAME_MS + PEAK_HOP_MS
    )
    start_sample = _sample_index(samples, sample_rate, word.start_ms)
    end_sample = _sample_index(samples, sample_rate, scan_end_ms)
    frame_dbfs = _overlapping_dbfs(
        samples[start_sample:end_sample],
        sample_rate,
        frame_ms=PEAK_FRAME_MS,
        hop_ms=PEAK_HOP_MS,
    )
    if frame_dbfs.size < 3:
        return None
    envelope = _smooth(frame_dbfs)
    for index in range(1, len(envelope) - 1):
        peak_offset_ms = index * PEAK_HOP_MS + PEAK_FRAME_MS / 2
        if peak_offset_ms < min_offset_ms:
            continue
        if peak_offset_ms > max_offset_ms:
            break
        current = float(envelope[index])
        if not (
            current >= float(envelope[index - 1])
            and current > float(envelope[index + 1])
        ):
            continue
        peak_ms = round(word.start_ms + peak_offset_ms)
        if word.start_ms < peak_ms < scan_end_ms:
            return peak_ms
    return None


def _strongest_energy_event_ms(
    samples: np.ndarray,
    sample_rate: int,
    word: VideoLocalizationAlignedWord,
    *,
    frame_rate: float,
) -> int | None:
    """Return the strongest acoustic event when no local maximum is separable."""

    video_frame_ms = 1000.0 / frame_rate
    min_offset_ms = MIN_PEAK_OFFSET_FRAMES * video_frame_ms
    max_offset_ms = MAX_PEAK_OFFSET_FRAMES * video_frame_ms
    scan_end_ms = word.start_ms + math.ceil(
        max_offset_ms + PEAK_FRAME_MS + PEAK_HOP_MS
    )
    start_sample = _sample_index(samples, sample_rate, word.start_ms)
    end_sample = _sample_index(samples, sample_rate, scan_end_ms)
    frame_dbfs = _overlapping_dbfs(
        samples[start_sample:end_sample],
        sample_rate,
        frame_ms=PEAK_FRAME_MS,
        hop_ms=PEAK_HOP_MS,
    )
    if not frame_dbfs.size:
        return None
    envelope = _smooth(frame_dbfs)
    candidates = [
        index
        for index in range(len(envelope))
        if min_offset_ms
        <= index * PEAK_HOP_MS + PEAK_FRAME_MS / 2
        <= max_offset_ms
    ]
    if not candidates:
        return None
    best_index = max(candidates, key=lambda index: float(envelope[index]))
    return round(
        word.start_ms
        + best_index * PEAK_HOP_MS
        + PEAK_FRAME_MS / 2
    )


def _sample_index(
    samples: np.ndarray,
    sample_rate: int,
    value_ms: int,
) -> int:
    return min(
        len(samples),
        max(0, round(sample_rate * value_ms / 1000)),
    )


def _smooth(values: np.ndarray) -> np.ndarray:
    width = max(1, round(PEAK_SMOOTH_MS / PEAK_HOP_MS))
    if width <= 1:
        return values
    left = width // 2
    right = width - 1 - left
    padded = np.pad(values, (left, right), mode="edge")
    return np.convolve(
        padded,
        np.ones(width, dtype=np.float32) / width,
        mode="valid",
    )


def _overlapping_dbfs(
    audio: np.ndarray,
    sample_rate: int,
    *,
    frame_ms: int,
    hop_ms: int,
) -> np.ndarray:
    frame_samples = max(1, round(sample_rate * frame_ms / 1000))
    hop_samples = max(1, round(sample_rate * hop_ms / 1000))
    if len(audio) < frame_samples:
        return np.array([], dtype=np.float32)
    values = []
    for start in range(0, len(audio) - frame_samples + 1, hop_samples):
        frame = audio[start : start + frame_samples]
        rms = float(
            np.sqrt(np.mean(np.square(frame, dtype=np.float64)))
        )
        values.append(max(-96.0, 20.0 * math.log10(max(rms, 1e-6))))
    return np.asarray(values, dtype=np.float32)


__all__ = [
    "detect_subtitle_entries",
    "detect_subtitle_entries_from_audio",
]
