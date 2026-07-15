from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.subtitle_evaluation import evaluate_srt_pair  # noqa: E402


def _srt(entries: list[tuple[int, str, str]]) -> str:
    blocks = []
    for index, (start_ms, end_ms, text) in enumerate(entries, start=1):
        blocks.append(f"{index}\n{_time(start_ms)} --> {_time(end_ms)}\n{text}")
    return "\n\n".join(blocks) + "\n"


def _time(value: int) -> str:
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def test_evaluation_reports_boundary_accuracy_text_coverage_and_qc_counts():
    reference = _srt([(1000, 2200, "第一句话"), (2300, 3600, "第二句话")])
    predicted = _srt([(900, 2100, "第一句话"), (2200, 3500, "第二句话")])

    report = evaluate_srt_pair(predicted, reference, audio_duration_ms=4000, boundary_tolerance_ms=150)

    assert report["text"]["normalized_similarity"] == 1.0
    assert report["timing"]["start_boundaries"]["f1"] == 1.0
    assert report["timing"]["end_boundaries"]["mean_absolute_error_ms"] == 100
    assert report["predicted"]["overlap_count"] == 0
    assert report["predicted"]["out_of_audio_range_count"] == 0


def test_evaluation_exposes_overlap_short_duration_and_reading_speed_failures():
    reference = _srt([(0, 1000, "正常字幕")])
    predicted = _srt([(0, 500, "这是一条非常长而且来不及阅读的字幕"), (400, 900, "重叠")])

    report = evaluate_srt_pair(predicted, reference, audio_duration_ms=800)

    assert report["predicted"]["overlap_count"] == 1
    assert report["predicted"]["under_800ms_count"] == 2
    assert report["predicted"]["over_12cps_count"] >= 1
    assert report["predicted"]["out_of_audio_range_count"] == 1


def test_evaluation_rejects_empty_or_invalid_inputs():
    valid = _srt([(0, 1000, "字幕")])
    with pytest.raises(ValueError, match="predicted"):
        evaluate_srt_pair("not an srt", valid)
    with pytest.raises(ValueError, match="positive"):
        evaluate_srt_pair(valid, valid, boundary_tolerance_ms=0)


def test_evaluation_counts_malformed_blocks_instead_of_silently_hiding_them():
    valid = _srt([(0, 1000, "字幕")])
    malformed = valid + "\n2\n00:00:02,000 --> 00:00:01,000\n倒序时间\n"

    report = evaluate_srt_pair(malformed, valid)

    assert report["predicted"]["cue_count"] == 1
    assert report["predicted"]["parse_failure_count"] == 1
