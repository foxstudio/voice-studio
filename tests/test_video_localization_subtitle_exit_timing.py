from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import subtitle_exit_timing  # noqa: E402


def test_display_exit_timing_holds_after_speech_without_moving_entries():
    spans = [
        subtitle_exit_timing.SubtitleTimingSpan(
            start_ms=1_000,
            end_ms=1_100,
        ),
        subtitle_exit_timing.SubtitleTimingSpan(
            start_ms=2_000,
            end_ms=2_600,
        ),
    ]

    resolved = subtitle_exit_timing.resolve_display_exit_times(
        spans,
        frame_rate=25.0,
        media_duration_ms=3_000,
    )

    assert [item.start_ms for item in resolved] == [1_000, 2_000]
    assert [item.end_ms for item in resolved] == [1_800, 3_000]


def test_display_exit_timing_keeps_two_frames_before_next_same_lane():
    spans = [
        subtitle_exit_timing.SubtitleTimingSpan(
            start_ms=0,
            end_ms=700,
            lane_ids=frozenset({"dub:0"}),
        ),
        subtitle_exit_timing.SubtitleTimingSpan(
            start_ms=1_000,
            end_ms=1_300,
            lane_ids=frozenset({"dub:0"}),
        ),
    ]

    resolved = subtitle_exit_timing.resolve_display_exit_times(
        spans,
        frame_rate=25.0,
    )

    assert resolved[0].end_ms == 920
    assert resolved[1].end_ms == 1_800


def test_display_exit_timing_only_caps_against_the_next_shared_lane():
    spans = [
        subtitle_exit_timing.SubtitleTimingSpan(
            start_ms=0,
            end_ms=600,
            lane_ids=frozenset({"dub:0"}),
        ),
        subtitle_exit_timing.SubtitleTimingSpan(
            start_ms=700,
            end_ms=1_000,
            lane_ids=frozenset({"dub:1"}),
        ),
    ]

    resolved = subtitle_exit_timing.resolve_display_exit_times(
        spans,
        frame_rate=25.0,
        media_duration_ms=2_000,
    )

    assert [item.end_ms for item in resolved] == [1_100, 1_500]


def test_display_exit_timing_never_shortens_existing_out_points():
    spans = [
        subtitle_exit_timing.SubtitleTimingSpan(
            start_ms=0,
            end_ms=980,
        ),
        subtitle_exit_timing.SubtitleTimingSpan(
            start_ms=1_000,
            end_ms=1_500,
        ),
    ]

    resolved = subtitle_exit_timing.resolve_display_exit_times(
        spans,
        frame_rate=25.0,
    )

    assert resolved[0].end_ms == 980
