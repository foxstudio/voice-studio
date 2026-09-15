from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.asr_source_repair import repair_asr_source
from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationReferenceClip,
    VideoLocalizationSubtitleCue,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptionState,
)
from app.schemas.video_localization_asr_repair import (
    AsrSourceRepairEvidence,
    AsrSourceRepairRequest,
)


_AUDIO_SHA256 = "a" * 64


def _request(*segment_ids: str) -> AsrSourceRepairRequest:
    return AsrSourceRepairRequest(
        expected_project_revision="7",
        transcription_revision_id="asr-revision-1",
        audio_sha256=_AUDIO_SHA256,
        request_id="remove-asr-tail-1",
        excluded_segment_ids=list(segment_ids),
        evidence=AsrSourceRepairEvidence(
            source_audio_start_ms=1_000,
            source_audio_end_ms=1_500,
            observed_text="",
            engine_id="independent-listener-v1",
            reason="source range is silent while ASR emitted prompt text",
        ),
    )


def _draft() -> VideoLocalizationDraft:
    words = [
        VideoLocalizationAlignedWord(
            word_id="word_0001", segment_id="asr_0022", text="hello", start_ms=0, end_ms=500,
            timing_confidence="high", timing_source="forced_aligner",
        ),
        VideoLocalizationAlignedWord(
            word_id="word_0002", segment_id="asr_0022", text="one.", start_ms=500, end_ms=1_000,
            timing_confidence="high", timing_source="forced_aligner",
        ),
        VideoLocalizationAlignedWord(
            word_id="word_0003", segment_id="asr_0023", text="Context", start_ms=1_000, end_ms=1_250
        ),
        VideoLocalizationAlignedWord(
            word_id="word_0004", segment_id="asr_0023", text="terms", start_ms=1_250, end_ms=1_500
        ),
    ]
    transcript = VideoLocalizationTranscriptionState(
        revision_id="asr-revision-1",
        source_track_id="vocals",
        source_audio_sha256=_AUDIO_SHA256,
        alignment_source_track_id="vocals",
        alignment_audio_sha256=_AUDIO_SHA256,
        engine_id="original-asr-v1",
        raw_text="hello one. Context terms",
        corrected_text="Hello one. Context terms",
        review_status="completed",
        transcript_quality_cycle={"decision": "passed"},
        audio_boundary_status="completed",
        boundary_review_status="completed",
        subtitle_entry_by_word_id={"word_0002": 500, "word_0003": 1_000},
        segments=[
            VideoLocalizationTranscriptSegment(
                segment_id="asr_0022", start_ms=0, end_ms=1_000,
                raw_text="hello one.", corrected_text="Hello one.",
            ),
            VideoLocalizationTranscriptSegment(
                segment_id="asr_0023", start_ms=1_000, end_ms=1_500,
                raw_text="Context terms", corrected_text="Context terms",
            ),
        ],
        words=words,
    )
    draft = VideoLocalizationDraft(
        transcription=transcript,
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0307", start_ms=0, end_ms=500,
                en_subtitle_text="hello", source_text_raw="hello",
                source_word_ids=["word_0001"], transcription_revision_id="asr-revision-1",
            ),
            VideoLocalizationCue(
                cue_id="cue_0308", start_ms=500, end_ms=1_500,
                en_subtitle_text="one. Context terms", source_text_raw="one. Context terms",
                zh_localized_subtitle_text="一。",
                source_word_ids=["word_0002", "word_0003", "word_0004"],
                transcription_revision_id="asr-revision-1",
                quality_flags=["segment_timing_interpolated", "custom_warning"],
            ),
            VideoLocalizationCue(
                cue_id="cue_0309", start_ms=1_000, end_ms=1_500,
                en_subtitle_text="Context terms", source_text_raw="Context terms",
                source_word_ids=["word_0003", "word_0004"],
                transcription_revision_id="asr-revision-1",
            ),
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="zh_0190", start_ms=500, end_ms=1_000, text="一。",
                linked_cue_id="cue_0308", source_cue_ids=["cue_0308"],
                source_word_ids=["word_0002"],
            )
        ],
        localization_state={
            "status": "passed",
            "source_fingerprint": "b" * 64,
            "quality_gate_fingerprint": "c" * 64,
            "keep": "Chinese content remains valid only for its surviving source word",
        },
    )
    draft._repository_revision = 7
    return draft


def test_repair_excludes_only_bad_segment_and_preserves_mixed_cue_and_chinese() -> None:
    original = _draft()

    repaired, receipt = repair_asr_source(
        original, _request("asr_0023"), new_revision_id="asr-revision-2"
    )

    assert original.transcription is not None
    assert original.transcription.raw_text == "hello one. Context terms"
    assert repaired.transcription is not None
    assert repaired.transcription.revision_id == "asr-revision-2"
    assert repaired.transcription.raw_text == "hello one."
    assert repaired.transcription.corrected_text == "Hello one."
    assert [item.segment_id for item in repaired.transcription.segments] == ["asr_0022"]
    assert [item.word_id for item in repaired.transcription.words] == ["word_0001", "word_0002"]
    assert repaired.transcription.subtitle_entry_by_word_id == {"word_0002": 500}
    assert repaired.transcription.review_status == "partial"
    assert repaired.transcription.transcript_quality_cycle == {
        "status": "stale",
        "reason": "asr_source_repair_requires_review",
        "previous_review_status": "completed",
        "previous_diagnostics": {"decision": "passed"},
    }
    assert "asr_source_repair_requires_review" in repaired.transcription.quality_flags

    mixed = next(item for item in repaired.cues if item.cue_id == "cue_0308")
    assert mixed.en_subtitle_text == "one."
    assert mixed.source_text_raw == "one."
    assert mixed.start_ms == 500
    assert mixed.end_ms == 1_000
    assert mixed.source_word_ids == ["word_0002"]
    assert mixed.transcription_revision_id == "asr-revision-2"
    assert mixed.zh_localized_subtitle_text == "一。"
    assert "segment_timing_interpolated" not in mixed.quality_flags
    assert "custom_warning" in mixed.quality_flags
    assert "asr_source_repair_requires_review" in mixed.quality_flags
    assert [item.cue_id for item in repaired.cues] == ["cue_0307", "cue_0308"]

    subtitle = repaired.localized_subtitles[0]
    assert (subtitle.subtitle_id, subtitle.text, subtitle.start_ms, subtitle.end_ms) == (
        "zh_0190", "一。", 500, 1_000,
    )
    assert subtitle.source_cue_ids == ["cue_0308"]
    assert subtitle.source_word_ids == ["word_0002"]
    assert repaired.localization_state == {
        "status": "edited",
        "keep": "Chinese content remains valid only for its surviving source word",
    }
    assert repaired.quality_gate.status == "unknown"
    assert repaired._repository_revision == 7

    assert receipt.before_revision_id == "asr-revision-1"
    assert receipt.after_revision_id == "asr-revision-2"
    assert receipt.request_fingerprint == _request("asr_0023").fingerprint()
    assert [item.segment_id for item in receipt.deleted_segments] == ["asr_0023"]
    assert [item.word_id for item in receipt.deleted_words] == ["word_0003", "word_0004"]
    assert [item.cue_id for item in receipt.deleted_cues] == ["cue_0309"]
    assert receipt.updated_cue_ids == ["cue_0308"]


def test_repair_keeps_interpolation_warning_when_a_surviving_word_is_interpolated() -> None:
    original = _draft()
    assert original.transcription is not None
    revised_words = [
        word.model_copy(
            update={
                "timing_confidence": "low",
                "timing_source": "asr_segment_interpolation",
            }
        )
        if word.word_id == "word_0002"
        else word
        for word in original.transcription.words
    ]
    original = original.model_copy(
        update={"transcription": original.transcription.model_copy(update={"words": revised_words})}
    )

    repaired, _ = repair_asr_source(
        original, _request("asr_0023"), new_revision_id="asr-revision-2"
    )

    mixed = next(item for item in repaired.cues if item.cue_id == "cue_0308")
    assert "segment_timing_interpolated" in mixed.quality_flags
    assert "custom_warning" in mixed.quality_flags


def test_repair_rejects_deleted_word_already_used_by_chinese_subtitle() -> None:
    original = _draft()
    blocked_subtitle = original.localized_subtitles[0].model_copy(
        update={"source_word_ids": ["word_0002", "word_0003"]}
    )
    original = original.model_copy(update={"localized_subtitles": [blocked_subtitle]})

    with pytest.raises(ValueError, match="Chinese subtitle source words"):
        repair_asr_source(original, _request("asr_0023"), new_revision_id="asr-revision-2")


def test_repair_rejects_generated_clip_using_an_affected_cue() -> None:
    original = _draft().model_copy(
        update={"timeline_clips": [{"clip_id": "clip_01", "source_cue_ids": ["cue_0308"]}]}
    )

    with pytest.raises(ValueError, match="timeline clip source"):
        repair_asr_source(original, _request("asr_0023"), new_revision_id="asr-revision-2")


def test_repair_rejects_overlapping_source_audio_reference() -> None:
    original = _draft().model_copy(
        update={
            "reference_clips": [
                VideoLocalizationReferenceClip(
                    reference_clip_id="ref_01",
                    source_stem="vocals_clean",
                    start_ms=1_200,
                    end_ms=1_300,
                    duration_ms=100,
                )
            ]
        }
    )

    with pytest.raises(ValueError, match="source-audio reference clip"):
        repair_asr_source(original, _request("asr_0023"), new_revision_id="asr-revision-2")


@pytest.mark.parametrize(
    ("draft_update", "message"),
    [
        ({"source_track_id": "original_audio"}, "vocals source and alignment tracks"),
        ({"alignment_audio_sha256": "d" * 64}, "audio hash does not match"),
    ],
)
def test_repair_rejects_non_current_vocals_alignment(
    draft_update: dict[str, str], message: str
) -> None:
    original = _draft()
    assert original.transcription is not None
    original = original.model_copy(
        update={"transcription": original.transcription.model_copy(update=draft_update)}
    )

    with pytest.raises(ValueError, match=message):
        repair_asr_source(original, _request("asr_0023"), new_revision_id="asr-revision-2")


def test_repair_rejects_all_segments_and_reused_revision() -> None:
    original = _draft()

    with pytest.raises(ValueError, match="cannot delete every"):
        repair_asr_source(
            original,
            _request("asr_0022", "asr_0023").model_copy(
                update={
                    "evidence": AsrSourceRepairEvidence(
                        source_audio_start_ms=0,
                        source_audio_end_ms=1_500,
                        observed_text="",
                        engine_id="independent-listener-v1",
                        reason="whole track was incorrectly recognized",
                    )
                }
            ),
            new_revision_id="asr-revision-2",
        )
    with pytest.raises(ValueError, match="distinct new revision"):
        repair_asr_source(
            original, _request("asr_0023"), new_revision_id="asr-revision-1"
        )
