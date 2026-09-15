from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import timeline_audio_renderer  # noqa: E402
from app.domains.video_localization import timeline_clip_timing  # noqa: E402


def test_normalize_audio_clip_uses_one_shared_frame_span():
    normalized = timeline_clip_timing.normalize_audio_clip(
        {
            "clip_id": "clip-current",
            "track_id": "dub",
            "start_ms": 203_708,
            "end_ms": 216_250,
            "source_start_ms": 375,
            "source_end_ms": 12_875,
        },
        frame_rate=24,
        timeline_duration_ms=5_416_668,
    )

    assert normalized == {
        "clip_id": "clip-current",
        "track_id": "dub",
        "start_ms": 203_708,
        "end_ms": 216_208,
        "source_start_ms": 375,
        "source_end_ms": 12_875,
    }
    assert normalized["end_ms"] - normalized["start_ms"] == (
        normalized["source_end_ms"]
        - normalized["source_start_ms"]
    )


def test_normalize_audio_clip_preserves_non_frame_aligned_source_tail():
    normalized = timeline_clip_timing.normalize_audio_clip(
        {
            "clip_id": "clip-quiet-tail",
            "track_id": "dub",
            "start_ms": 1_007,
            "end_ms": 3_596,
            "source_start_ms": 7,
            "source_end_ms": 2_596,
        },
        frame_rate=24,
        timeline_duration_ms=10_000,
    )

    assert normalized == {
        "clip_id": "clip-quiet-tail",
        "track_id": "dub",
        "start_ms": 1_007,
        "end_ms": 3_596,
        "source_start_ms": 7,
        "source_end_ms": 2_596,
    }


def test_normalize_audio_clip_keeps_non_audio_timeline_items_unchanged():
    cue = {
        "clip_id": "future-visual",
        "track_id": "visual",
        "start_ms": 100,
        "end_ms": 900,
    }

    assert timeline_clip_timing.normalize_audio_clip(
        cue,
        frame_rate=25,
        timeline_duration_ms=2_000,
    ) is cue


def test_normalize_audio_clip_does_not_invent_a_frame_rate():
    clip = {
        "clip_id": "clip-without-video-rate",
        "track_id": "dub",
        "start_ms": 1_000,
        "end_ms": 2_400,
    }

    assert timeline_clip_timing.normalize_audio_clip(
        clip,
        frame_rate=None,
        timeline_duration_ms=None,
    ) is clip


def test_renderer_rejects_display_and_playback_spans_that_disagree():
    with pytest.raises(
        ValidationError,
        match="时间线显示范围必须与源音频播放范围一致",
    ):
        timeline_audio_renderer.TimelineAudioRenderItem(
            item_id="clip",
            source_id="source",
            track_id="dub",
            timeline_start_ms=1_000,
            timeline_end_ms=2_040,
            source_start_ms=0,
            source_end_ms=1_000,
        )
