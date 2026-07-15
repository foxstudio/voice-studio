from __future__ import annotations

import math
import re
from difflib import SequenceMatcher
from typing import Any

from app.domains.video_localization import subtitles


DEFAULT_BOUNDARY_TOLERANCE_MS = 250
MIN_DURATION_MS = 800
SOFT_MAX_DURATION_MS = 6000
HARD_MAX_DURATION_MS = 7000
TARGET_CPS = 9.0
HARD_MAX_CPS = 12.0


def evaluate_srt_pair(
    predicted_srt: str,
    reference_srt: str,
    *,
    audio_duration_ms: int | None = None,
    boundary_tolerance_ms: int = DEFAULT_BOUNDARY_TOLERANCE_MS,
) -> dict[str, Any]:
    """Compare one generated SRT with a human reference without hiding QC failures."""

    if boundary_tolerance_ms <= 0:
        raise ValueError("boundary_tolerance_ms must be positive")
    predicted, predicted_parse_failures = _parse_with_diagnostics(predicted_srt)
    reference, reference_parse_failures = _parse_with_diagnostics(reference_srt)
    if not predicted:
        raise ValueError("predicted SRT has no valid cues")
    if not reference:
        raise ValueError("reference SRT has no valid cues")

    predicted_qc = _cue_qc(
        predicted,
        audio_duration_ms=audio_duration_ms,
        parse_failure_count=predicted_parse_failures,
    )
    reference_qc = _cue_qc(
        reference,
        audio_duration_ms=audio_duration_ms,
        parse_failure_count=reference_parse_failures,
    )
    start_metrics = _boundary_metrics(
        [int(item["start_ms"]) for item in predicted],
        [int(item["start_ms"]) for item in reference],
        tolerance_ms=boundary_tolerance_ms,
    )
    end_metrics = _boundary_metrics(
        [int(item["end_ms"]) for item in predicted],
        [int(item["end_ms"]) for item in reference],
        tolerance_ms=boundary_tolerance_ms,
    )
    return {
        "metric_version": "subtitle-evaluation-v1",
        "boundary_tolerance_ms": boundary_tolerance_ms,
        "predicted": predicted_qc,
        "reference": reference_qc,
        "text": {
            "normalized_similarity": round(
                SequenceMatcher(
                    None,
                    _normalized_text(predicted),
                    _normalized_text(reference),
                    autojunk=False,
                ).ratio(),
                4,
            ),
        },
        "timing": {
            "start_boundaries": start_metrics,
            "end_boundaries": end_metrics,
            "boundary_f1_mean": round((start_metrics["f1"] + end_metrics["f1"]) / 2, 4),
        },
    }


def _cue_qc(
    cues: list[dict[str, int | str]],
    *,
    audio_duration_ms: int | None,
    parse_failure_count: int,
) -> dict[str, Any]:
    durations = [int(item["end_ms"]) - int(item["start_ms"]) for item in cues]
    cps_values = [
        _visible_units(str(item["text"])) / max(duration_ms / 1000, 0.001)
        for item, duration_ms in zip(cues, durations)
    ]
    overlaps = sum(
        int(right["start_ms"]) < int(left["end_ms"])
        for left, right in zip(cues, cues[1:])
    )
    out_of_range = 0
    if audio_duration_ms is not None:
        out_of_range = sum(
            int(item["start_ms"]) < 0 or int(item["end_ms"]) > audio_duration_ms
            for item in cues
        )
    return {
        "cue_count": len(cues),
        "parse_failure_count": parse_failure_count,
        "first_start_ms": int(cues[0]["start_ms"]),
        "last_end_ms": int(cues[-1]["end_ms"]),
        "overlap_count": overlaps,
        "invalid_duration_count": sum(duration <= 0 for duration in durations),
        "under_800ms_count": sum(0 < duration < MIN_DURATION_MS for duration in durations),
        "over_6000ms_count": sum(duration > SOFT_MAX_DURATION_MS for duration in durations),
        "over_7000ms_count": sum(duration > HARD_MAX_DURATION_MS for duration in durations),
        "over_9cps_count": sum(cps > TARGET_CPS for cps in cps_values),
        "over_12cps_count": sum(cps > HARD_MAX_CPS for cps in cps_values),
        "mean_cps": round(sum(cps_values) / len(cps_values), 3),
        "p95_cps": round(_percentile(cps_values, 95), 3),
        "out_of_audio_range_count": out_of_range,
    }


def _parse_with_diagnostics(text: str) -> tuple[list[dict[str, int | str]], int]:
    entries: list[dict[str, int | str]] = []
    failures = 0
    for block in (item.strip() for item in text.replace("\r", "").split("\n\n")):
        if not block:
            continue
        parsed = subtitles._parse_srt(block)
        if len(parsed) != 1:
            failures += 1
            continue
        entries.append(parsed[0])
    return entries, failures


def _boundary_metrics(predicted: list[int], reference: list[int], *, tolerance_ms: int) -> dict[str, Any]:
    unmatched = set(range(len(reference)))
    errors: list[int] = []
    for value in predicted:
        matches = [index for index in unmatched if abs(reference[index] - value) <= tolerance_ms]
        if not matches:
            continue
        best = min(matches, key=lambda index: abs(reference[index] - value))
        unmatched.remove(best)
        errors.append(abs(reference[best] - value))

    true_positive = len(errors)
    precision = true_positive / len(predicted) if predicted else 0.0
    recall = true_positive / len(reference) if reference else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "matched": true_positive,
        "predicted_count": len(predicted),
        "reference_count": len(reference),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "mean_absolute_error_ms": round(sum(errors) / len(errors), 2) if errors else None,
        "p95_absolute_error_ms": round(_percentile(errors, 95), 2) if errors else None,
    }


def _normalized_text(cues: list[dict[str, int | str]]) -> str:
    text = "".join(str(item["text"]) for item in cues).casefold()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def _visible_units(text: str) -> float:
    units = 0.0
    for character in text:
        if character.isspace():
            continue
        units += 0.5 if character.isascii() and character.isalnum() else 1.0
    return units


def _percentile(values: list[int] | list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile / 100 * len(ordered)) - 1))
    return float(ordered[index])
