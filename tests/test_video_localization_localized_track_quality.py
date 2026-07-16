from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.exporting import export_subtitles  # noqa: E402
from app.domains.video_localization.quality_gate import (  # noqa: E402
    evaluate_quality_gate,
    subtitle_export_blockers,
)
from app.domains.video_localization.schemas import VideoLocalizationDraft  # noqa: E402


def _draft(localized_subtitles: list[dict]) -> VideoLocalizationDraft:
    return VideoLocalizationDraft.model_validate(
        {
            "source_media": {"filename": "source.mp4", "duration_ms": 5000},
            "stems": {"separation_status": "completed"},
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 0,
                    "end_ms": 900,
                    "en_subtitle_text": "The first source sentence.",
                    "zh_localized_subtitle_text": "这是合并后的一条完整中文字幕",
                    "review_status": "ready",
                },
                {
                    "cue_id": "cue_0002",
                    "start_ms": 900,
                    "end_ms": 3000,
                    "en_subtitle_text": "The second source sentence.",
                    "zh_localized_subtitle_text": None,
                    "review_status": "ready",
                },
            ],
            "localized_subtitles": localized_subtitles,
        }
    )


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_merged_localized_track_is_authoritative_for_quality_and_exports():
    draft = _draft(
        [
            {
                "subtitle_id": "localized_0001",
                "start_ms": 0,
                "end_ms": 3000,
                "text": "这是合并后的一条完整中文字幕",
                "linked_cue_id": "cue_0001",
                "source_cue_ids": ["cue_0001", "cue_0002"],
            }
        ]
    )
    original_cues = deepcopy(draft.cues)

    gate = evaluate_quality_gate(draft)
    export_codes = _codes(subtitle_export_blockers(draft, "bilingual"))

    assert gate.status == "pass"
    assert "ZH_SUBTITLE_MISSING" not in _codes(gate.blockers)
    assert "LOCALIZED_SUBTITLE_CPS_HARD_LIMIT" not in _codes(gate.blockers)
    assert "ZH_SUBTITLE_MISSING" not in export_codes
    assert "LOCALIZED_SUBTITLE_CPS_HARD_LIMIT" not in export_codes
    assert export_subtitles(draft, "zh") == ("1\n00:00:00,000 --> 00:00:03,000\n这是合并后的一条完整中文字幕\n")
    assert export_subtitles(draft, "bilingual") == (
        "1\n00:00:00,000 --> 00:00:03,000\n"
        "The first source sentence. The second source sentence.\n"
        "这是合并后的一条完整中文字幕\n"
    )
    assert draft.cues == original_cues


def test_bilingual_export_maps_legacy_imported_track_by_timing():
    draft = _draft(
        [
            {
                "subtitle_id": "imported_0001",
                "start_ms": 0,
                "end_ms": 900,
                "text": "旧流程导入字幕",
                "linked_cue_id": "cue_0001",
            }
        ]
    )

    assert not subtitle_export_blockers(draft, "bilingual")
    assert export_subtitles(draft, "bilingual") == (
        "1\n00:00:00,000 --> 00:00:00,900\nThe first source sentence.\n旧流程导入字幕\n"
    )


@pytest.mark.parametrize(
    ("localized_subtitles", "expected_code"),
    [
        (
            [
                {
                    "subtitle_id": "localized_short",
                    "start_ms": 0,
                    "end_ms": 700,
                    "text": "短字幕",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "LOCALIZED_SUBTITLE_DURATION_TOO_SHORT",
        ),
        (
            [
                {
                    "subtitle_id": "localized_fast",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "一二三四五六七八九十甲乙丙",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "LOCALIZED_SUBTITLE_CPS_HARD_LIMIT",
        ),
        (
            [
                {
                    "subtitle_id": "localized_first",
                    "start_ms": 0,
                    "end_ms": 1500,
                    "text": "第一条",
                    "source_cue_ids": ["cue_0001"],
                },
                {
                    "subtitle_id": "localized_second",
                    "start_ms": 1000,
                    "end_ms": 2500,
                    "text": "第二条",
                    "source_cue_ids": ["cue_0002"],
                },
            ],
            "LOCALIZED_SUBTITLE_TIMELINE_OVERLAP",
        ),
    ],
)
def test_authoritative_localized_track_still_blocks_real_quality_issues(localized_subtitles, expected_code):
    draft = _draft(localized_subtitles)

    assert expected_code in _codes(evaluate_quality_gate(draft).blockers)
    assert expected_code in _codes(subtitle_export_blockers(draft, "zh"))
    assert expected_code in _codes(subtitle_export_blockers(draft, "bilingual"))
