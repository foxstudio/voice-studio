from __future__ import annotations

import hashlib
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.quality_gate import evaluate_quality_gate  # noqa: E402
from app.domains.video_localization.schemas import VideoLocalizationDraft  # noqa: E402


def _cue(cue_id: str, start_ms: int, end_ms: int, *, timing_confidence: str = "high") -> dict:
    return {
        "cue_id": cue_id,
        "speaker_id": "speaker_01",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "audio_route": "preserve_original_audio",
        "en_subtitle_text": f"Source {cue_id}.",
        "zh_localized_subtitle_text": f"字幕 {cue_id}。",
        "tts_recommended_text": f"口播 {cue_id}。",
        "timing_confidence": timing_confidence,
        "transcription_revision_id": "transcription_01",
        "review_status": "ready",
        "quality_flags": ["generated_by_asr"],
    }


def _transcription(*, alignment_status: str = "completed", interpolated: bool = False) -> dict:
    return {
        "revision_id": "transcription_01",
        "alignment_status": alignment_status,
        "timing_confidence": "low" if interpolated else "high",
        "words": [
            {
                "word_id": "word_01",
                "segment_id": "segment_01",
                "text": "Source",
                "start_ms": 0,
                "end_ms": 1000,
                "timing_confidence": "low" if interpolated else "high",
                "timing_source": "asr_segment_interpolation" if interpolated else "forced_aligner",
            }
        ],
    }


def _draft(
    *, cues: list[dict], localized_subtitles: list[dict], transcription: dict | None = None
) -> VideoLocalizationDraft:
    cue_payloads = deepcopy(cues)
    transcription_payload = deepcopy(transcription)
    if transcription_payload is not None and cue_payloads and not any("source_word_ids" in cue for cue in cue_payloads):
        prototype = (transcription_payload.get("words") or [{}])[0]
        generated_words = []
        for index, cue in enumerate(cue_payloads, start=1):
            word_id = f"word_{index:02d}"
            cue["source_word_ids"] = [word_id]
            generated_words.append(
                {
                    **prototype,
                    "word_id": word_id,
                    "segment_id": f"segment_{index:02d}",
                    "text": cue.get("en_subtitle_text") or "",
                    "start_ms": cue["start_ms"],
                    "end_ms": cue["end_ms"],
                }
            )
        transcription_payload["words"] = generated_words
    return VideoLocalizationDraft.model_validate(
        {
            "source_media": {"filename": "source.mp4"},
            "stems": {"separation_status": "completed"},
            "speakers": [{"speaker_id": "speaker_01", "route": "preserve_original_audio"}],
            "cues": cue_payloads,
            "localized_subtitles": localized_subtitles,
            "transcription": transcription_payload,
        }
    )


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_timing_quality_gate_keeps_unstarted_empty_draft_unknown():
    gate = evaluate_quality_gate(VideoLocalizationDraft())

    assert gate.status == "unknown"
    assert gate.pending_issues == 0


def test_timing_quality_gate_checks_imported_localized_track_without_asr_cues():
    draft = VideoLocalizationDraft.model_validate(
        {
            "localized_subtitles": [
                {"subtitle_id": "subtitle_01", "start_ms": 0, "end_ms": 400, "text": "需要复核"},
            ]
        }
    )

    gate = evaluate_quality_gate(draft)

    assert gate.status == "blocked"
    assert "LOCALIZED_SUBTITLE_DURATION_TOO_SHORT" in _codes(gate.blockers)


def test_timing_quality_gate_blocks_failed_alignment_before_cues_exist():
    draft = VideoLocalizationDraft.model_validate(
        {
            "transcription": _transcription(alignment_status="failed", interpolated=True),
        }
    )

    gate = evaluate_quality_gate(draft)

    assert gate.status == "blocked"
    assert {"ASR_ALIGNMENT_FAILED", "ASR_TIMING_INTERPOLATED"}.issubset(_codes(gate.blockers))


def test_timing_quality_gate_passes_for_aligned_non_overlapping_tracks():
    draft = _draft(
        cues=[_cue("cue_01", 0, 1500), _cue("cue_02", 1500, 3000)],
        localized_subtitles=[
            {"subtitle_id": "subtitle_01", "start_ms": 0, "end_ms": 1500, "text": "你好", "linked_cue_id": "cue_01"},
            {"subtitle_id": "subtitle_02", "start_ms": 1500, "end_ms": 3000, "text": "世界", "linked_cue_id": "cue_02"},
        ],
        transcription=_transcription(),
    )

    gate = evaluate_quality_gate(draft)

    assert gate.status == "pass"
    assert gate.pending_issues == 0


def test_timing_quality_gate_blocks_low_confidence_failed_and_interpolated_asr():
    draft = _draft(
        cues=[_cue("cue_low", 0, 1500, timing_confidence="low")],
        localized_subtitles=[],
        transcription=_transcription(alignment_status="failed", interpolated=True),
    )

    gate = evaluate_quality_gate(draft)

    assert gate.status == "blocked"
    assert {
        "ASR_ALIGNMENT_FAILED",
        "ASR_TIMING_INTERPOLATED",
    }.issubset(_codes(gate.blockers))
    assert all(issue.message for issue in gate.blockers)


def test_timing_quality_gate_locates_isolated_low_confidence_cue():
    cue = _cue("cue_low", 0, 1500, timing_confidence="low")
    cue["quality_flags"] = ["generated_by_asr", "manual_timing_verified"]
    draft = _draft(
        cues=[cue],
        localized_subtitles=[],
        transcription=_transcription(),
    )

    gate = evaluate_quality_gate(draft)

    issue = next(item for item in gate.blockers if item.code == "ASR_CUE_TIMING_LOW_CONFIDENCE")
    assert issue.cue_id == "cue_low"


def test_forged_manual_flag_does_not_skip_word_timing_coverage_check():
    cue = _cue("cue_forged", 100, 900)
    cue["source_word_ids"] = ["word_01"]
    cue["quality_flags"] = ["generated_by_asr", "manual_timing_verified"]
    draft = _draft(cues=[cue], localized_subtitles=[], transcription=_transcription())

    gate = evaluate_quality_gate(draft)

    assert "ASR_CUE_EXCLUDES_REFERENCED_WORDS" in _codes(gate.blockers)


def test_effective_speech_onset_is_used_for_word_coverage():
    cue = _cue("cue_onset", 140, 1000)
    cue["source_word_ids"] = ["word_01"]
    cue["en_subtitle_text"] = "Source"
    transcription = _transcription()
    transcription["speech_onset_by_word_id"] = {"word_01": 140}
    draft = _draft(cues=[cue], localized_subtitles=[], transcription=transcription)

    gate = evaluate_quality_gate(draft)

    assert "ASR_CUE_EXCLUDES_REFERENCED_WORDS" not in _codes(gate.blockers)


def test_timing_quality_gate_blocks_semantically_indivisible_oversized_cue():
    cue = _cue("cue_long", 0, 8000)
    cue["quality_flags"] = ["generated_by_asr", "segmentation_review_required"]
    draft = _draft(cues=[cue], localized_subtitles=[], transcription=_transcription())

    gate = evaluate_quality_gate(draft)

    issue = next(item for item in gate.blockers if item.code == "ASR_CUE_SEGMENTATION_LIMIT_EXCEEDED")
    assert issue.cue_id == "cue_long"


def test_timing_quality_gate_accepts_manually_verified_interpolated_cue():
    cue = _cue("cue_manual", 0, 1500, timing_confidence="medium")
    cue["source_word_ids"] = ["word_01"]
    cue["quality_flags"] = ["manual_timing_edit", "manual_timing_verified"]
    cue.update(
        {
            "manual_timing_review_status": "confirmed",
            "manual_timing_confirmed_revision": 0,
            "manual_timing_confirmed_at": "2026-07-15T04:00:00",
            "manual_timing_confirmed_start_ms": 0,
            "manual_timing_confirmed_end_ms": 1500,
            "manual_timing_confirmation_method": "auditioned",
        }
    )
    draft = _draft(
        cues=[cue],
        localized_subtitles=[],
        transcription=_transcription(interpolated=True),
    )

    gate = evaluate_quality_gate(draft)

    assert "ASR_TIMING_INTERPOLATED" not in _codes(gate.blockers)
    assert "ASR_CUE_TIMING_LOW_CONFIDENCE" not in _codes(gate.blockers)


def test_timing_quality_gate_only_requires_interpolated_cues_to_be_verified():
    transcription = _transcription(interpolated=True)
    transcription["words"].append(
        {
            **transcription["words"][0],
            "word_id": "word_02",
            "segment_id": "segment_02",
            "text": "Aligned",
            "start_ms": 1500,
            "end_ms": 3000,
            "timing_confidence": "high",
            "timing_source": "forced_aligner",
        }
    )
    interpolated_cue = _cue("cue_interpolated", 0, 1500, timing_confidence="medium")
    interpolated_cue["source_word_ids"] = ["word_01"]
    interpolated_cue["quality_flags"] = ["manual_timing_edit", "manual_timing_verified"]
    interpolated_cue.update(
        {
            "manual_timing_review_status": "confirmed",
            "manual_timing_confirmed_revision": 0,
            "manual_timing_confirmed_at": "2026-07-15T04:00:00",
            "manual_timing_confirmed_start_ms": 0,
            "manual_timing_confirmed_end_ms": 1500,
            "manual_timing_confirmation_method": "auditioned",
        }
    )
    aligned_cue = _cue("cue_aligned", 1500, 3000)
    aligned_cue["source_word_ids"] = ["word_02"]
    draft = _draft(
        cues=[interpolated_cue, aligned_cue],
        localized_subtitles=[],
        transcription=transcription,
    )

    gate = evaluate_quality_gate(draft)

    assert "ASR_TIMING_INTERPOLATED" not in _codes(gate.blockers)


def test_timing_quality_gate_blocks_adjacent_track_overlaps():
    draft = _draft(
        cues=[_cue("cue_01", 0, 1500), _cue("cue_02", 1000, 2500)],
        localized_subtitles=[
            {"subtitle_id": "subtitle_01", "start_ms": 0, "end_ms": 1500, "text": "第一句", "linked_cue_id": "cue_01"},
            {
                "subtitle_id": "subtitle_02",
                "start_ms": 1000,
                "end_ms": 2500,
                "text": "第二句",
                "linked_cue_id": "cue_02",
            },
        ],
        transcription=_transcription(),
    )

    gate = evaluate_quality_gate(draft)

    assert gate.status == "blocked"
    assert {"ASR_CUE_TIMELINE_OVERLAP", "LOCALIZED_SUBTITLE_TIMELINE_OVERLAP"}.issubset(_codes(gate.blockers))


def test_timing_quality_gate_blocks_out_of_order_tracks():
    draft = _draft(
        cues=[_cue("cue_01", 3000, 4000), _cue("cue_02", 1000, 2000)],
        localized_subtitles=[
            {
                "subtitle_id": "subtitle_01",
                "start_ms": 3000,
                "end_ms": 4000,
                "text": "第一句",
                "linked_cue_id": "cue_01",
            },
            {
                "subtitle_id": "subtitle_02",
                "start_ms": 1000,
                "end_ms": 2000,
                "text": "第二句",
                "linked_cue_id": "cue_02",
            },
        ],
        transcription=_transcription(),
    )

    gate = evaluate_quality_gate(draft)

    assert gate.status == "blocked"
    assert {"ASR_CUE_TIMELINE_OUT_OF_ORDER", "LOCALIZED_SUBTITLE_TIMELINE_OUT_OF_ORDER"}.issubset(_codes(gate.blockers))


def test_timing_quality_gate_applies_hard_localized_subtitle_limits():
    draft = _draft(
        cues=[_cue("cue_01", 0, 12000)],
        localized_subtitles=[
            {"subtitle_id": "subtitle_short", "start_ms": 0, "end_ms": 700, "text": "短", "linked_cue_id": "cue_01"},
            {
                "subtitle_id": "subtitle_fast",
                "start_ms": 1000,
                "end_ms": 2000,
                "text": "一二三四五六七八九十甲乙丙",
                "linked_cue_id": "cue_01",
            },
            {
                "subtitle_id": "subtitle_long",
                "start_ms": 3000,
                "end_ms": 11001,
                "text": "长",
                "linked_cue_id": "cue_01",
            },
        ],
        transcription=_transcription(),
    )

    gate = evaluate_quality_gate(draft)

    assert gate.status == "blocked"
    assert {
        "LOCALIZED_SUBTITLE_DURATION_TOO_SHORT",
        "LOCALIZED_SUBTITLE_DURATION_TOO_LONG",
        "LOCALIZED_SUBTITLE_CPS_HARD_LIMIT",
    }.issubset(_codes(gate.blockers))


def test_timing_quality_gate_warns_above_duration_and_cps_targets():
    draft = _draft(
        cues=[_cue("cue_01", 0, 7000)],
        localized_subtitles=[
            {
                "subtitle_id": "subtitle_fast",
                "start_ms": 0,
                "end_ms": 1000,
                "text": "一二三四五六七八九十",
                "linked_cue_id": "cue_01",
            },
            {
                "subtitle_id": "subtitle_long",
                "start_ms": 1000,
                "end_ms": 7500,
                "text": "较长字幕",
                "linked_cue_id": "cue_01",
            },
        ],
        transcription=_transcription(),
    )

    gate = evaluate_quality_gate(draft)

    assert gate.status == "warning"
    assert {
        "LOCALIZED_SUBTITLE_CPS_HIGH",
        "LOCALIZED_SUBTITLE_DURATION_ABOVE_TARGET",
    }.issubset(_codes(gate.warnings))
    assert not gate.blockers


def test_quality_gate_checks_localized_text_stored_directly_on_cue():
    cue = _cue("cue_01", 0, 1000)
    cue["zh_localized_subtitle_text"] = "这是一个明显超过十四个可视汉字而且阅读速度过快的本土化字幕"
    draft = _draft(cues=[cue], localized_subtitles=[], transcription=_transcription())

    gate = evaluate_quality_gate(draft)

    assert "LOCALIZED_SUBTITLE_CPS_HARD_LIMIT" in _codes(gate.blockers)
    assert {
        "LOCALIZED_SUBTITLE_LINE_TOO_LONG",
        "LOCALIZED_SUBTITLE_TOTAL_TOO_LONG",
    }.issubset(_codes(gate.warnings))


def test_quality_gate_uses_localized_track_instead_of_asr_cue_mirror():
    cue = _cue("cue_01", 0, 1000)
    cue["zh_localized_subtitle_text"] = "一二三四五六七八九十甲乙丙"
    draft = _draft(
        cues=[cue],
        localized_subtitles=[
            {
                "subtitle_id": "subtitle_01",
                "start_ms": 0,
                "end_ms": 1000,
                "text": "独立字幕",
                "linked_cue_id": "cue_01",
            }
        ],
        transcription=_transcription(),
    )

    gate = evaluate_quality_gate(draft)

    assert "LOCALIZED_SUBTITLE_CPS_HARD_LIMIT" not in _codes(gate.blockers)


def test_quality_gate_warns_when_localized_cue_has_more_than_two_lines():
    cue = _cue("cue_01", 0, 3000)
    cue["zh_localized_subtitle_text"] = "第一行\n第二行\n第三行"
    draft = _draft(cues=[cue], localized_subtitles=[], transcription=_transcription())

    gate = evaluate_quality_gate(draft)

    assert "LOCALIZED_SUBTITLE_TOO_MANY_LINES" in _codes(gate.warnings)


def test_quality_gate_blocks_selected_boundary_rejected_by_semantic_review():
    transcription = _transcription()
    transcription["words"] = [
        {**transcription["words"][0], "word_id": "word_01", "text": "The", "start_ms": 0, "end_ms": 500},
        {**transcription["words"][0], "word_id": "word_02", "text": "scale", "start_ms": 500, "end_ms": 1000},
    ]
    transcription.update(
        {
            "audio_boundary_status": "completed",
            "boundary_review_status": "completed",
            "audio_boundary_features": [
                {
                    "boundary_id": "word_01:word_02",
                    "left_word_id": "word_01",
                    "right_word_id": "word_02",
                    "start_ms": 1000,
                    "end_ms": 1300,
                    "gap_ms": 300,
                    "low_energy_ms": 260,
                    "low_energy_ratio": 0.9,
                    "gap_rms_dbfs": -48,
                    "speech_reference_dbfs": -18,
                    "noise_floor_dbfs": -55,
                    "energy_drop_db": 30,
                    "confidence": "high",
                }
            ],
            "boundary_reviews": [
                {
                    "boundary_id": "word_01:word_02",
                    "left_word_id": "word_01",
                    "right_word_id": "word_02",
                    "decision": "avoid",
                    "confidence": 0.95,
                    "reason": "incomplete_syntax",
                }
            ],
        }
    )
    first = _cue("cue_01", 0, 500)
    first.update({"en_subtitle_text": "The", "source_word_ids": ["word_01"]})
    second = _cue("cue_02", 500, 1000)
    second.update({"en_subtitle_text": "scale", "source_word_ids": ["word_02"]})
    draft = _draft(
        cues=[first, second],
        localized_subtitles=[],
        transcription=transcription,
    )

    gate = evaluate_quality_gate(draft)

    assert "ASR_SELECTED_BOUNDARY_REVIEW_AVOIDED" in _codes(gate.blockers)


def test_quality_gate_blocks_duplicate_track_ids():
    gate = evaluate_quality_gate(
        _draft(
            cues=[_cue("cue_duplicate", 0, 1000), _cue("cue_duplicate", 1000, 2000)],
            localized_subtitles=[
                {"subtitle_id": "subtitle_duplicate", "start_ms": 0, "end_ms": 1000, "text": "第一句"},
                {"subtitle_id": "subtitle_duplicate", "start_ms": 1000, "end_ms": 2000, "text": "第二句"},
            ],
            transcription=_transcription(),
        )
    )

    assert {
        "CUE_ID_DUPLICATED",
        "LOCALIZED_SUBTITLE_ID_DUPLICATED",
    }.issubset(_codes(gate.blockers))


def test_timing_quality_gate_blocks_stale_transcription_source_and_revision():
    draft = VideoLocalizationDraft.model_validate(
        {
            "source_media": {"filename": "source.mp4", "audio_sha256": "new-audio"},
            "transcription": {
                "revision_id": "new-revision",
                "source_track_id": "original",
                "source_audio_sha256": "old-audio",
                "raw_text": "Hello",
                "corrected_text": "Hello",
                "alignment_status": "completed",
                "words": [
                    {
                        "word_id": "word_1",
                        "segment_id": "segment_1",
                        "text": "Hello",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "timing_confidence": "high",
                        "timing_source": "forced_aligner",
                    }
                ],
            },
            "cues": [
                {
                    "cue_id": "cue_1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "en_subtitle_text": "Hello",
                    "source_word_ids": ["word_1"],
                    "transcription_revision_id": "old-revision",
                }
            ],
        }
    )

    gate = evaluate_quality_gate(draft)

    codes = _codes(gate.blockers)
    assert "ASR_SOURCE_CHANGED" in codes
    assert "ASR_CUE_REVISION_STALE" in codes


def test_quality_gate_blocks_missing_out_of_order_and_shared_word_ids():
    transcription = _transcription()
    transcription["words"] = [
        {**transcription["words"][0], "word_id": "word_01", "start_ms": 0, "end_ms": 500},
        {**transcription["words"][0], "word_id": "word_02", "start_ms": 500, "end_ms": 1000},
    ]
    first = _cue("cue_01", 0, 750)
    first["source_word_ids"] = ["word_02", "word_01"]
    second = _cue("cue_02", 1000, 2000)
    second["source_word_ids"] = ["word_02", "word_missing"]

    gate = evaluate_quality_gate(_draft(cues=[first, second], localized_subtitles=[], transcription=transcription))

    assert {
        "ASR_CUE_WORD_IDS_MISSING",
        "ASR_CUE_WORD_IDS_OUT_OF_ORDER",
        "ASR_CUE_EXCLUDES_REFERENCED_WORDS",
    }.issubset(_codes(gate.blockers))


def test_quality_gate_blocks_word_id_claimed_by_two_cues():
    transcription = _transcription()
    first = _cue("cue_01", 0, 1000)
    first["source_word_ids"] = ["word_01"]
    second = _cue("cue_02", 1000, 2000)
    second["source_word_ids"] = ["word_01"]

    gate = evaluate_quality_gate(_draft(cues=[first, second], localized_subtitles=[], transcription=transcription))

    assert "ASR_WORD_ID_SHARED_BY_CUES" in _codes(gate.blockers)


def test_quality_gate_blocks_zero_duration_source_cue():
    gate = evaluate_quality_gate(
        _draft(cues=[_cue("cue_zero", 1000, 1000)], localized_subtitles=[], transcription=None)
    )

    assert "CUE_DURATION_INVALID" in _codes(gate.blockers)


def test_quality_gate_reports_final_unreviewed_boundary_instead_of_only_pipeline_status():
    transcription = _transcription()
    transcription.update(
        {
            "boundary_review_status": "partial",
            "quality_flags": ["boundary_review_incomplete"],
            "words": [
                {**transcription["words"][0], "word_id": "word_01", "text": "The", "start_ms": 0, "end_ms": 500},
                {**transcription["words"][0], "word_id": "word_02", "text": "skill", "start_ms": 500, "end_ms": 1000},
            ],
        }
    )
    first = _cue("cue_01", 0, 500)
    first.update({"en_subtitle_text": "The", "source_word_ids": ["word_01"]})
    second = _cue("cue_02", 500, 1000)
    second.update({"en_subtitle_text": "skill", "source_word_ids": ["word_02"]})

    gate = evaluate_quality_gate(_draft(cues=[first, second], localized_subtitles=[], transcription=transcription))

    issue = next(item for item in gate.warnings if item.code == "ASR_SELECTED_BOUNDARY_UNREVIEWED")
    assert "1 处实际断句边界" in issue.message
    assert "word_01/word_02" in issue.message
    assert "ASR_BOUNDARY_REVIEW_INCOMPLETE" not in _codes(gate.warnings)


def test_quality_gate_requires_exact_word_coverage_and_robust_text_match():
    transcription = _transcription()
    transcription["words"] = [
        {**transcription["words"][0], "word_id": "word_01", "text": "Hello", "start_ms": 0, "end_ms": 400},
        {**transcription["words"][0], "word_id": "word_02", "text": ", world!", "start_ms": 400, "end_ms": 1000},
    ]
    cue = _cue("cue_01", 0, 1000)
    cue.update(
        {
            "en_subtitle_text": "HELLO, WORLD!",
            "source_word_ids": ["word_01", "word_02"],
        }
    )

    gate = evaluate_quality_gate(_draft(cues=[cue], localized_subtitles=[], transcription=transcription))

    assert not {
        "ASR_WORD_IDS_UNCOVERED",
        "ASR_CUE_TEXT_WORD_MISMATCH",
        "ASR_CUE_WORD_SEQUENCE_MISMATCH",
    }.intersection(_codes(gate.blockers))

    cue["source_word_ids"] = ["word_01"]
    cue["en_subtitle_text"] = "Different"
    broken = evaluate_quality_gate(_draft(cues=[cue], localized_subtitles=[], transcription=transcription))
    assert {
        "ASR_WORD_IDS_UNCOVERED",
        "ASR_CUE_TEXT_WORD_MISMATCH",
    }.issubset(_codes(broken.blockers))


def test_quality_gate_blocks_globally_reordered_word_coverage():
    transcription = _transcription()
    transcription["words"] = [
        {**transcription["words"][0], "word_id": "word_01", "text": "First", "start_ms": 0, "end_ms": 500},
        {**transcription["words"][0], "word_id": "word_02", "text": "Second", "start_ms": 500, "end_ms": 1000},
    ]
    first = _cue("cue_01", 0, 1000)
    first.update({"en_subtitle_text": "Second", "source_word_ids": ["word_02"]})
    second = _cue("cue_02", 1000, 1500)
    second.update({"en_subtitle_text": "First", "source_word_ids": ["word_01"]})

    gate = evaluate_quality_gate(_draft(cues=[first, second], localized_subtitles=[], transcription=transcription))

    assert "ASR_CUE_WORD_SEQUENCE_MISMATCH" in _codes(gate.blockers)


def test_quality_gate_blocks_source_and_localized_cues_past_media_duration():
    draft = VideoLocalizationDraft.model_validate(
        {
            "source_media": {"filename": "source.mp4", "duration_ms": 1000},
            "stems": {"separation_status": "completed"},
            "cues": [
                {
                    "cue_id": "cue_overrun",
                    "start_ms": 900,
                    "end_ms": 1100,
                    "en_subtitle_text": "Overrun",
                    "review_status": "ready",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "subtitle_overrun",
                    "start_ms": 900,
                    "end_ms": 1200,
                    "text": "越界",
                }
            ],
        }
    )

    gate = evaluate_quality_gate(draft)

    assert {
        "ASR_CUE_EXCEEDS_MEDIA_DURATION",
        "LOCALIZED_SUBTITLE_EXCEEDS_MEDIA_DURATION",
    }.issubset(_codes(gate.blockers))


def test_quality_gate_hashes_real_asr_and_alignment_sources(tmp_path: Path):
    vocals_path = tmp_path / "vocals.wav"
    original_path = tmp_path / "original.wav"
    vocals_path.write_bytes(b"current vocals")
    original_path.write_bytes(b"current original")

    transcription = _transcription()
    transcription.update(
        {
            "source_track_id": "vocals",
            "source_audio_sha256": hashlib.sha256(vocals_path.read_bytes()).hexdigest(),
            "alignment_source_track_id": "original",
            "alignment_audio_sha256": hashlib.sha256(original_path.read_bytes()).hexdigest(),
        }
    )
    cue = _cue("cue_01", 0, 1000)
    cue.update({"en_subtitle_text": "Source", "source_word_ids": ["word_01"]})
    draft = VideoLocalizationDraft.model_validate(
        {
            "source_media": {
                "filename": "source.mp4",
                "audio_path": str(original_path),
                "audio_sha256": "stale-cached-original",
            },
            "stems": {
                "separation_status": "completed",
                "vocals_clean_path": str(vocals_path),
                "vocals_clean_sha256": "stale-cached-vocals",
            },
            "cues": [cue],
            "transcription": transcription,
        }
    )

    current = evaluate_quality_gate(draft)
    assert not {"ASR_SOURCE_CHANGED", "ASR_ALIGNMENT_SOURCE_CHANGED"}.intersection(_codes(current.blockers))

    vocals_path.write_bytes(b"changed vocals")
    original_path.write_bytes(b"changed original")
    changed = evaluate_quality_gate(draft)
    assert {"ASR_SOURCE_CHANGED", "ASR_ALIGNMENT_SOURCE_CHANGED"}.issubset(_codes(changed.blockers))


def test_quality_gate_aggregates_large_repeated_issue_sets():
    cues = []
    for index in range(340):
        cue = _cue(f"cue_{index:04d}", index * 1000, (index + 1) * 1000)
        cue["review_status"] = "needs_review"
        cues.append(cue)

    gate = evaluate_quality_gate(_draft(cues=cues, localized_subtitles=[], transcription=None))

    review_issues = [item for item in gate.warnings if item.code == "CUE_NEEDS_REVIEW"]
    assert len(review_issues) == 1
    assert "同类问题共 340 项" in review_issues[0].message
