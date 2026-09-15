from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.timeline_timecode import (  # noqa: E402
    format_timeline_duration,
    format_timeline_mentions,
    format_timeline_position,
    format_timeline_range,
)


def test_timeline_position_uses_compact_chinese_time_and_frames():
    assert format_timeline_position(0, frame_rate=29.97) == "0帧"
    assert format_timeline_position(500, frame_rate=30) == "15帧"
    assert format_timeline_position(65_100, frame_rate=30) == "1分5秒3帧"
    assert format_timeline_position(3_600_000, frame_rate=30) == "1时"
    assert format_timeline_position(3_633_000, frame_rate=30) == "1时0分33秒"
    assert format_timeline_duration(16, frame_rate=29.97) == "<1帧"


def test_timeline_range_omits_zero_units_at_both_ends():
    assert format_timeline_range(60_000, 62_500, frame_rate=30) == (
        "1分 – 1分2秒15帧"
    )


def test_formats_millisecond_mentions_in_legacy_free_text():
    assert (
        format_timeline_mentions(
            "第4帧（45000ms）可见姓名；约210091毫秒出现图表。",
            frame_rate=30,
        )
        == "第4帧（45秒）可见姓名；约3分30秒3帧出现图表。"
    )
    assert (
        format_timeline_mentions(
            "需要查看0-12620ms，并补看208000至210000毫秒。",
            frame_rate=30,
        )
        == "需要查看0帧 – 12秒19帧，并补看3分28秒 – 3分30秒。"
    )
    assert (
        format_timeline_mentions("内部字段 duration_ms 保持不变。")
        == "内部字段 duration_ms 保持不变。"
    )


def test_formats_legacy_second_ranges_in_free_text():
    assert (
        format_timeline_mentions(
            "抽查 29.2s - 31.2s，另看 321.3s。",
            frame_rate=30,
        )
        == "抽查 29秒6帧 – 31秒6帧，另看 5分21秒9帧。"
    )
