from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import cues  # noqa: E402
from app.domains.video_localization.schemas import VideoLocalizationCueUpdate, VideoLocalizationDraft  # noqa: E402
from app.errors import AppException  # noqa: E402
from app.schemas.voice_studio import TranscriptionSegment  # noqa: E402


def test_from_asr_segments_uses_fallback_text_when_segments_are_blank():
    generated = cues.from_asr_segments(
        segments=[TranscriptionSegment(start_ms=0, end_ms=2000, text="   ", language="en")],
        fallback_text=" Full fallback transcript. ",
        duration_ms=2000,
        engine_id="qwen3-asr-mlx",
        existing_cue_ids=set(),
    )

    assert len(generated) == 1
    assert generated[0].cue_id == "cue_0001"
    assert generated[0].start_ms == 0
    assert generated[0].end_ms == 2000
    assert generated[0].en_subtitle_text == "Full fallback transcript."
    assert "segment_timing_missing" in generated[0].quality_flags


def test_from_asr_segments_returns_no_cues_when_segments_and_fallback_are_blank():
    generated = cues.from_asr_segments(
        segments=[TranscriptionSegment(start_ms=0, end_ms=2000, text="", language="en")],
        fallback_text="  ",
        duration_ms=2000,
        engine_id="qwen3-asr-mlx",
        existing_cue_ids=set(),
    )

    assert generated == []


def test_from_asr_segments_sorts_clamps_and_drops_invalid_overlaps():
    generated = cues.from_asr_segments(
        segments=[
            TranscriptionSegment(start_ms=1200, end_ms=2200, text="Second", language="en"),
            TranscriptionSegment(start_ms=0, end_ms=1000, text="First", language="en"),
            TranscriptionSegment(start_ms=900, end_ms=950, text="Too short after clamp", language="en"),
            TranscriptionSegment(start_ms=2200, end_ms=2200, text="Zero duration", language="en"),
            TranscriptionSegment(start_ms=-100, end_ms=200, text="Lead in", language="en"),
        ],
        fallback_text="",
        duration_ms=2200,
        engine_id="qwen3-asr-mlx",
        existing_cue_ids=set(),
    )

    assert [(cue.start_ms, cue.end_ms, cue.en_subtitle_text) for cue in generated] == [
        (0, 200, "Lead in"),
        (200, 1000, "First"),
        (1200, 2200, "Second"),
    ]


def test_from_asr_segments_fallback_without_positive_duration_keeps_timing_missing():
    generated = cues.from_asr_segments(
        segments=[],
        fallback_text="Fallback only",
        duration_ms=0,
        engine_id="qwen3-asr-mlx",
        existing_cue_ids=set(),
    )

    assert len(generated) == 1
    assert generated[0].start_ms == 0
    assert generated[0].end_ms is None
    assert generated[0].source_duration_ms is None
    assert "segment_timing_missing" in generated[0].quality_flags


def test_updated_cue_can_touch_adjacent_cue_without_overlapping():
    draft = VideoLocalizationDraft(
        cues=[
            {"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1000},
            {"cue_id": "cue_0002", "start_ms": 1300, "end_ms": 2200},
        ]
    )

    updated = cues.with_updated_cue(draft, "cue_0002", VideoLocalizationCueUpdate(start_ms=1000))

    assert updated.cues[1].start_ms == 1000


def test_updated_cue_rejects_overlap_with_adjacent_cue():
    draft = VideoLocalizationDraft(
        cues=[
            {"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1000},
            {"cue_id": "cue_0002", "start_ms": 1300, "end_ms": 2200},
        ]
    )

    try:
        cues.with_updated_cue(draft, "cue_0002", VideoLocalizationCueUpdate(start_ms=900))
    except AppException as exc:
        assert exc.code == "VIDEO_LOCALIZATION_CUE_OVERLAP"
        assert "不能重叠" in exc.message
    else:
        raise AssertionError("overlapping subtitle cue should be rejected")


def test_manual_source_text_edit_preserves_asr_provenance_and_protects_cue():
    draft = VideoLocalizationDraft(
        cues=[
            {
                "cue_id": "cue_0001",
                "start_ms": 0,
                "end_ms": 1000,
                "en_subtitle_text": "AI draft",
                "source_word_ids": ["word_000001"],
                "transcription_revision_id": "revision-a",
                "timing_confidence": "high",
                "quality_flags": ["generated_by_asr", "engine:qwen3-asr-mlx"],
            }
        ]
    )

    updated = cues.with_updated_cue(
        draft,
        "cue_0001",
        VideoLocalizationCueUpdate(en_subtitle_text="Human correction"),
    ).cues[0]

    assert "generated_by_asr" in updated.quality_flags
    assert {"manual_text_edit", "protected_manual_edit"} <= set(updated.quality_flags)
    assert "timing_review_required" not in updated.quality_flags
    assert updated.source_word_ids == ["word_000001"]
    assert updated.transcription_revision_id == "revision-a"
    assert updated.timing_confidence == "high"
    assert not cues.is_replaceable_asr_candidate(updated)


def test_manual_timing_edit_preserves_source_provenance_but_requires_review():
    draft = VideoLocalizationDraft(
        cues=[
            {
                "cue_id": "cue_0001",
                "start_ms": 0,
                "end_ms": 1000,
                "source_word_ids": ["word_000001"],
                "transcription_revision_id": "revision-a",
                "timing_confidence": "high",
                "quality_flags": ["generated_by_asr"],
            }
        ]
    )

    updated = cues.with_updated_cue(
        draft,
        "cue_0001",
        VideoLocalizationCueUpdate(end_ms=1200),
    ).cues[0]

    assert "manual_timing_edit" in updated.quality_flags
    assert "generated_by_asr" in updated.quality_flags
    assert updated.source_word_ids == ["word_000001"]
    assert updated.transcription_revision_id == "revision-a"
    assert updated.timing_confidence == "low"


def test_manual_timing_confirmation_marks_cue_as_verified_without_word_provenance():
    draft = VideoLocalizationDraft(
        cues=[
            {
                "cue_id": "cue_0001",
                "start_ms": 0,
                "end_ms": 1200,
                "source_word_ids": [],
                "timing_confidence": "low",
                "quality_flags": ["protected_manual_edit", "manual_timing_edit", "timing_review_required"],
            }
        ]
    )

    updated = cues.with_updated_cue(
        draft,
        "cue_0001",
        VideoLocalizationCueUpdate(confirm_timing=True),
    ).cues[0]

    assert updated.timing_confidence == "low"
    assert updated.manual_timing_review_status == "confirmed"
    assert "manual_timing_verified" in updated.quality_flags
    assert "timing_review_required" not in updated.quality_flags
    assert updated.source_word_ids == []
    assert updated.transcription_revision_id is None


def test_manual_timing_edit_can_be_confirmed_in_the_same_update():
    draft = VideoLocalizationDraft(
        cues=[
            {
                "cue_id": "cue_0001",
                "start_ms": 0,
                "end_ms": 1000,
                "source_word_ids": ["word_000001"],
                "transcription_revision_id": "revision-a",
                "timing_confidence": "high",
                "quality_flags": ["generated_by_asr"],
            }
        ]
    )

    updated = cues.with_updated_cue(
        draft,
        "cue_0001",
        VideoLocalizationCueUpdate(end_ms=1200, confirm_timing=True),
    ).cues[0]

    assert updated.end_ms == 1200
    assert updated.timing_confidence == "low"
    assert updated.manual_timing_review_status == "confirmed"
    assert {"manual_timing_edit", "manual_timing_verified"} <= set(updated.quality_flags)
    assert "timing_review_required" not in updated.quality_flags


def test_non_source_edit_keeps_asr_provenance():
    draft = VideoLocalizationDraft(
        cues=[
            {
                "cue_id": "cue_0001",
                "start_ms": 0,
                "end_ms": 1000,
                "quality_flags": ["generated_by_asr"],
            }
        ]
    )

    updated = cues.with_updated_cue(
        draft,
        "cue_0001",
        VideoLocalizationCueUpdate(speaker_id="speaker_01"),
    ).cues[0]

    assert "generated_by_asr" in updated.quality_flags
    assert "protected_manual_edit" not in updated.quality_flags
