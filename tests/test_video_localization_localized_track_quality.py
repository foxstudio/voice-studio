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
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationSpeaker,
)


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


def test_merged_localized_track_is_authoritative_for_quality_and_exports(
    tmp_path: Path,
):
    video_path = tmp_path / "source.mp4"
    vocals_path = tmp_path / "vocals.wav"
    background_path = tmp_path / "background.wav"
    for path in (video_path, vocals_path, background_path):
        path.write_bytes(b"fixture")
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
    draft = draft.model_copy(
        update={
            "source_media": draft.source_media.model_copy(
                update={"video_path": str(video_path)}
            ),
            "stems": draft.stems.model_copy(
                update={
                    "vocals_clean_path": str(vocals_path),
                    "background_path": str(background_path),
                }
            ),
        }
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


def test_localized_track_surfaces_strong_pause_as_manual_review_warning():
    draft = _draft(
        [
            {
                "subtitle_id": "localized_0001",
                "start_ms": 0,
                "end_ms": 3000,
                "text": "这句需要人工听一下",
                "linked_cue_id": "cue_0001",
                "source_cue_ids": ["cue_0001", "cue_0002"],
                "quality_flags": [
                    "display_strong_pause_review_required"
                ],
            }
        ]
    )

    gate = evaluate_quality_gate(draft)

    assert "LOCALIZED_SUBTITLE_STRONG_PAUSE_REVIEW_REQUIRED" in _codes(
        gate.warnings
    )
    assert "LOCALIZED_SUBTITLE_STRONG_PAUSE_REVIEW_REQUIRED" not in _codes(
        gate.blockers
    )


def test_formal_localized_track_owns_tts_text_when_dubbing_work_exists():
    draft = _draft(
        [
            {
                "subtitle_id": "localized_0001",
                "start_ms": 0,
                "end_ms": 3000,
                "text": "这是合并后的一条完整中文字幕",
                "tts_text": "这是合并后的一条完整中文口播。",
                "linked_cue_id": "cue_0001",
                "source_cue_ids": ["cue_0001", "cue_0002"],
            }
        ]
    )
    draft.cues = [
        item.model_copy(
            update={
                "speaker_id": "speaker_01",
                "audio_route": "clone_from_source",
                "tts_recommended_text": None,
            }
        )
        for item in draft.cues
    ]
    draft.speakers = [
        VideoLocalizationSpeaker(
            speaker_id="speaker_01",
            display_name="主讲人",
        )
    ]

    gate = evaluate_quality_gate(draft)

    assert "TTS_TEXT_MISSING" not in _codes(gate.blockers)
    assert "TTS_TEXT_PLACEHOLDER" not in _codes(gate.blockers)
    assert "CUE_SPEAKER_MISSING" not in _codes(gate.blockers)


def test_formal_localized_track_allows_unknown_speaker_for_dubbing():
    draft = _draft(
        [
            {
                "subtitle_id": "localized_0001",
                "start_ms": 0,
                "end_ms": 3000,
                "text": "这是合并后的一条完整中文字幕",
                "tts_text": "这是合并后的一条完整中文口播。",
                "linked_cue_id": "cue_0001",
                "source_cue_ids": ["cue_0001", "cue_0002"],
            }
        ]
    )
    draft.cues = [
        item.model_copy(
            update={
                "audio_route": "preset_tts",
                "speaker_id": None,
            }
        )
        for item in draft.cues
    ]

    gate = evaluate_quality_gate(draft)

    assert "CUE_SPEAKER_MISSING" not in _codes(gate.blockers)
    assert "CUE_SPEAKER_MISSING" in _codes(gate.warnings)
    assert "TTS_TEXT_MISSING" not in _codes(gate.blockers)


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
    "localized_subtitles",
    [
        [
            {
                "subtitle_id": "localized_short",
                "start_ms": 0,
                "end_ms": 700,
                "text": "短字幕",
                "source_cue_ids": ["cue_0001"],
            }
        ],
        [
            {
                "subtitle_id": "localized_fast",
                "start_ms": 0,
                "end_ms": 1000,
                "text": "一二三四五六七八九十甲乙丙",
                "source_cue_ids": ["cue_0001"],
            }
        ],
    ],
)
def test_authoritative_localized_track_does_not_apply_duration_or_cps_limits(localized_subtitles):
    draft = _draft(localized_subtitles)
    gate = evaluate_quality_gate(draft)
    export_codes = _codes(subtitle_export_blockers(draft, "zh"))

    removed_codes = {
        "LOCALIZED_SUBTITLE_DURATION_TOO_SHORT",
        "LOCALIZED_SUBTITLE_DURATION_TOO_LONG",
        "LOCALIZED_SUBTITLE_DURATION_ABOVE_TARGET",
        "LOCALIZED_SUBTITLE_CPS_HIGH",
        "LOCALIZED_SUBTITLE_CPS_HARD_LIMIT",
    }
    assert removed_codes.isdisjoint(_codes(gate.warnings))
    assert removed_codes.isdisjoint(_codes(gate.blockers))
    assert removed_codes.isdisjoint(export_codes)


def test_authoritative_localized_track_still_blocks_timeline_overlap():
    draft = _draft(
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
        ]
    )

    gate = evaluate_quality_gate(draft)
    export_codes = _codes(subtitle_export_blockers(draft, "zh"))

    assert "LOCALIZED_SUBTITLE_TIMELINE_OVERLAP" in _codes(gate.blockers)
    assert "LOCALIZED_SUBTITLE_TIMELINE_OVERLAP" in export_codes
