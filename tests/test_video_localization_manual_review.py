from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api import video_localization as video_localization_api  # noqa: E402
from app.domains.video_localization import cues  # noqa: E402
from app.domains.video_localization import service as video_localization_service  # noqa: E402
from app.domains.video_localization.quality_gate import evaluate_quality_gate  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAsrVadTimingCorrectionRequest,
    VideoLocalizationCueTimingConfirmationRequest,
    VideoLocalizationCueUpdate,
    VideoLocalizationDraft,
)
from app.errors import AppException  # noqa: E402


def _draft(*, confidence: str = "high") -> VideoLocalizationDraft:
    return VideoLocalizationDraft.model_validate(
        {
            "source_media": {"filename": "source.mp4"},
            "stems": {"separation_status": "completed"},
            "speakers": [{"speaker_id": "speaker_01", "route": "preserve_original_audio"}],
            "transcription": {
                "revision_id": "transcription_01",
                "alignment_status": "completed",
                "timing_confidence": confidence,
                "words": [
                    {
                        "word_id": "word_01",
                        "segment_id": "segment_01",
                        "text": "Hello",
                        "start_ms": 0,
                        "end_ms": 900,
                        "timing_confidence": confidence,
                        "timing_source": "forced_aligner",
                    }
                ],
            },
            "cues": [
                {
                    "cue_id": "cue_01",
                    "speaker_id": "speaker_01",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "audio_route": "preserve_original_audio",
                    "en_subtitle_text": "Hello",
                    "zh_localized_subtitle_text": "你好",
                    "tts_recommended_text": "你好",
                    "source_word_ids": ["word_01"],
                    "source_text_raw": "Hello",
                    "timing_confidence": confidence,
                    "transcription_revision_id": "transcription_01",
                    "review_status": "ready",
                    "quality_flags": ["generated_by_asr"],
                }
            ],
        }
    )


def _blocker_codes(draft: VideoLocalizationDraft) -> set[str]:
    return {issue.code for issue in evaluate_quality_gate(draft).blockers}


def _warning_codes(draft: VideoLocalizationDraft) -> set[str]:
    return {issue.code for issue in evaluate_quality_gate(draft).warnings}


def test_manual_timing_edit_preserves_word_provenance_and_requires_review():
    draft = _draft()

    updated = cues.with_updated_cue(
        draft,
        "cue_01",
        VideoLocalizationCueUpdate(end_ms=1200),
    ).cues[0]

    assert updated.timing_confidence == "low"
    assert updated.source_word_ids == ["word_01"]
    assert updated.transcription_revision_id == "transcription_01"
    assert updated.source_text_raw == "Hello"
    assert updated.manual_timing_revision == 1
    assert updated.manual_timing_review_status == "required"
    assert "manual_timing_edit" in updated.quality_flags
    assert "timing_review_required" in updated.quality_flags
    assert "manual_timing_verified" not in updated.quality_flags
    assert "ASR_CUE_TIMING_LOW_CONFIDENCE" in _warning_codes(draft.model_copy(update={"cues": [updated]}))


def test_audition_confirmation_is_auditable_without_raising_model_confidence():
    draft = cues.with_updated_cue(
        _draft(),
        "cue_01",
        VideoLocalizationCueUpdate(end_ms=1200),
    )

    confirmed = cues.with_updated_cue(
        draft,
        "cue_01",
        VideoLocalizationCueUpdate(
            confirm_timing=True,
            expected_start_ms=0,
            expected_end_ms=1200,
        ),
    ).cues[0]

    assert confirmed.timing_confidence == "low"
    assert confirmed.source_word_ids == ["word_01"]
    assert confirmed.transcription_revision_id == "transcription_01"
    assert confirmed.manual_timing_review_status == "confirmed"
    assert confirmed.manual_timing_confirmed_revision == confirmed.manual_timing_revision == 1
    assert confirmed.manual_timing_confirmed_start_ms == confirmed.start_ms == 0
    assert confirmed.manual_timing_confirmed_end_ms == confirmed.end_ms == 1200
    assert confirmed.manual_timing_confirmation_method == "auditioned"
    assert confirmed.manual_timing_confirmed_at is not None
    datetime.fromisoformat(confirmed.manual_timing_confirmed_at)
    assert "manual_timing_verified" in confirmed.quality_flags
    assert "timing_review_required" not in confirmed.quality_flags
    assert "ASR_CUE_TIMING_LOW_CONFIDENCE" not in _blocker_codes(draft.model_copy(update={"cues": [confirmed]}))


def test_asr_vad_confirmation_requires_current_word_audio_and_boundary_evidence():
    draft = _draft(confidence="low")
    assert draft.transcription is not None
    next_transcription = type(draft.transcription).model_validate(
        {
            **draft.transcription.model_dump(mode="json"),
            "alignment_source_track_id": "vocals",
            "alignment_audio_sha256": "a" * 64,
            "audio_boundary_status": "completed",
            "words": [*draft.transcription.words, {
                "word_id": "word_02", "segment_id": "segment_01", "text": "there",
                "start_ms": 900, "end_ms": 1_600, "timing_confidence": "high",
                "timing_source": "forced_aligner",
            }],
            "audio_boundary_features": [{
                "boundary_id": "word_01:word_02", "left_word_id": "word_01",
                "right_word_id": "word_02", "start_ms": 900, "end_ms": 980,
                "gap_ms": 80, "low_energy_ms": 70, "low_energy_ratio": 0.875,
                "gap_rms_dbfs": -40, "speech_reference_dbfs": -20,
                "noise_floor_dbfs": -60, "energy_drop_db": 20, "confidence": "high",
            }],
        }
    )
    draft = draft.model_copy(update={"transcription": next_transcription})
    evidence = {
        "transcription_revision_id": "transcription_01",
        "alignment_source_track_id": "vocals",
        "alignment_audio_sha256": "a" * 64,
        "source_word_ids": ["word_01"],
        "vad_boundary_ids": ["word_01:word_02"],
    }

    confirmed = cues.with_updated_cue(
        draft,
        "cue_01",
        VideoLocalizationCueUpdate(
            confirm_timing=True, expected_start_ms=0, expected_end_ms=1000,
            timing_confirmation_method="asr_vad_verified",
            timing_confirmation_evidence=evidence,
        ),
    ).cues[0]

    assert confirmed.manual_timing_confirmation_method == "asr_vad_verified"
    assert confirmed.manual_timing_confirmation_evidence is not None
    assert cues.manual_timing_confirmation_is_current(confirmed)
    assert "manual_timing_verified" in confirmed.quality_flags

    stale_evidence = {**evidence, "alignment_audio_sha256": "b" * 64}
    with pytest.raises(AppException) as exc_info:
        cues.with_updated_cue(
            draft, "cue_01", VideoLocalizationCueUpdate(
                confirm_timing=True, timing_confirmation_method="asr_vad_verified",
                timing_confirmation_evidence=stale_evidence,
            ),
        )
    assert exc_info.value.code == "VIDEO_LOCALIZATION_CUE_TIMING_EVIDENCE_STALE"

    partial = draft.model_copy(update={
        "transcription": next_transcription.model_copy(update={"alignment_status": "partial"})
    })
    with pytest.raises(AppException) as exc_info:
        cues.with_updated_cue(
            partial,
            "cue_01",
            VideoLocalizationCueUpdate(
                confirm_timing=True, timing_confirmation_method="asr_vad_verified",
                timing_confirmation_evidence=evidence,
            ),
        )
    assert exc_info.value.code == "VIDEO_LOCALIZATION_CUE_TIMING_EVIDENCE_STALE"


def test_public_update_rechecks_live_alignment_track_hash(monkeypatch):
    draft = _draft()
    monkeypatch.setattr(
        video_localization_service.project_store,
        "get_project",
        lambda _project_id: object(),
    )
    monkeypatch.setattr(
        video_localization_service,
        "get_video_localization",
        lambda _project_id: draft,
    )
    monkeypatch.setattr(
        video_localization_service.quality_gate,
        "current_transcription_alignment_audio_sha256",
        lambda _draft: "b" * 64,
    )
    with pytest.raises(AppException) as exc_info:
        video_localization_service.update_cue(
            "project_01",
            "cue_01",
            VideoLocalizationCueUpdate(
                confirm_timing=True,
                timing_confirmation_method="asr_vad_verified",
                timing_confirmation_evidence={
                    "transcription_revision_id": "transcription_01",
                    "alignment_source_track_id": "vocals",
                    "alignment_audio_sha256": "a" * 64,
                    "source_word_ids": ["word_01"],
                    "vad_boundary_ids": ["word_01:word_02"],
                },
            ),
        )
    assert exc_info.value.code == "VIDEO_LOCALIZATION_CUE_TIMING_EVIDENCE_STALE"


def test_asr_vad_source_timing_correction_is_atomic_and_does_not_verify_zero_width_word():
    draft = VideoLocalizationDraft.model_validate(
        {
            "source_media": {"filename": "source.mp4"},
            "stems": {"separation_status": "completed", "vocals_clean_sha256": "a" * 64},
            "speakers": [{"speaker_id": "speaker_01", "route": "preserve_original_audio"}],
            "transcription": {
                "revision_id": "transcription_01",
                "alignment_status": "completed",
                "alignment_source_track_id": "vocals",
                "alignment_audio_sha256": "a" * 64,
                "audio_boundary_status": "completed",
                "audio_boundary_analysis_version": "vad-energy-v1",
                "subtitle_entry_by_word_id": {"word_01": 10, "word_02": 900},
                "words": [
                    {
                        "word_id": "word_01", "segment_id": "segment_01", "text": "Now",
                        "start_ms": 0, "end_ms": 800, "timing_confidence": "high",
                        "timing_source": "forced_aligner", "speaker_cluster_id": "cluster_01",
                        "acoustic_support_start_ms": 50, "acoustic_support_end_ms": 700,
                        "acoustic_support_source": "speaker_diarization",
                    },
                    {
                        "word_id": "word_02", "segment_id": "segment_01", "text": "a",
                        "start_ms": 900, "end_ms": 900, "timing_confidence": "low",
                        "timing_source": "forced_aligner",
                    },
                    {
                        "word_id": "word_03", "segment_id": "segment_01", "text": "void",
                        "start_ms": 920, "end_ms": 1_400, "timing_confidence": "high",
                        "timing_source": "forced_aligner",
                    },
                ],
            },
            "cues": [
                {
                    "cue_id": "cue_01", "speaker_id": "speaker_01", "start_ms": 0, "end_ms": 850,
                    "audio_route": "preserve_original_audio", "en_subtitle_text": "Now",
                    "zh_localized_subtitle_text": "现在", "tts_recommended_text": "现在",
                    "source_word_ids": ["word_01"], "source_text_raw": "Now",
                    "transcription_revision_id": "transcription_01", "review_status": "ready",
                },
                {
                    "cue_id": "cue_02", "speaker_id": "speaker_01", "start_ms": 850, "end_ms": 1_500,
                    "audio_route": "preserve_original_audio", "en_subtitle_text": "a void",
                    "zh_localized_subtitle_text": "一个空洞", "tts_recommended_text": "一个空洞",
                    "source_word_ids": ["word_02", "word_03"], "source_text_raw": "a void",
                    "transcription_revision_id": "transcription_01", "review_status": "ready",
                },
            ],
        }
    )

    corrected = cues.apply_asr_vad_source_timing_correction(
        draft,
        VideoLocalizationAsrVadTimingCorrectionRequest(
            **{
            "expected_project_revision": "8",
            "transcription_revision_id": "transcription_01",
            "source_track_id": "vocals",
            "audio_sha256": "a" * 64,
            "analysis_protocol": "local-vad-v1",
            "analysis_start_ms": 1_000,
            "analysis_end_ms": 1_500,
            "word_timings": [
                {"word_id": "word_01", "text": "Now", "start_ms": 1_000, "end_ms": 1_100},
                {"word_id": "word_02", "text": "a", "start_ms": 1_120, "end_ms": 1_120},
                {"word_id": "word_03", "text": "void", "start_ms": 1_120, "end_ms": 1_400},
            ],
            "vad_intervals": [{"start_ms": 1_105, "end_ms": 1_115, "duration_ms": 10}],
            "issues": [{"code": "initial_alignment_wrong", "result": "rechecked on vocals", "word_ids": ["word_01"]}],
            "cue_corrections": [
                {
                    "cue_id": "cue_01", "expected_start_ms": 0, "expected_end_ms": 850,
                    "start_ms": 1_000, "end_ms": 1_100, "source_word_ids": ["word_01"],
                    "vad_interval_ids": ["vad_1105_1115"], "confirm_timing": True,
                },
                {
                    "cue_id": "cue_02", "expected_start_ms": 850, "expected_end_ms": 1_500,
                    "start_ms": 1_120, "end_ms": 1_400, "source_word_ids": ["word_02", "word_03"],
                    "confirm_timing": False,
                },
            ],
            },
        ),
    )

    assert corrected.transcription is not None
    words = {word.word_id: word for word in corrected.transcription.words}
    assert words["word_01"].timing_source == "asr_vad_verified"
    assert words["word_01"].speaker_cluster_id == "cluster_01"
    assert words["word_01"].acoustic_support_start_ms is None
    assert words["word_02"].end_ms == words["word_02"].start_ms == 1_120
    assert words["word_02"].timing_confidence == "low"
    assert "word_02" not in corrected.transcription.subtitle_entry_by_word_id
    assert corrected.transcription.subtitle_entry_by_word_id["word_01"] == 1_000
    trace = corrected.transcription.asr_vad_source_timing_corrections[-1]
    assert trace.issues[0].code == "initial_alignment_wrong"
    assert trace.superseded_acoustic_support[0].word_id == "word_01"

    first, second = corrected.cues
    assert first.start_ms == 1_000 and first.end_ms == 1_100
    assert cues.manual_timing_confirmation_is_current(first)
    assert first.manual_timing_confirmation_evidence is not None
    assert first.manual_timing_confirmation_evidence.source_timing_correction_id == trace.correction_id
    assert second.start_ms == 1_120 and second.end_ms == 1_400
    assert second.manual_timing_review_status == "required"
    assert not cues.manual_timing_confirmation_is_current(second)


def test_asr_vad_source_timing_correction_rejects_stale_or_partial_source_words():
    draft = _draft()
    assert draft.transcription is not None
    draft = draft.model_copy(update={"transcription": draft.transcription.model_copy(update={
        "alignment_source_track_id": "vocals",
        "alignment_audio_sha256": "a" * 64,
        "audio_boundary_status": "completed",
    })})
    request = {
        "expected_project_revision": "8",
        "transcription_revision_id": "transcription_01",
        "source_track_id": "vocals", "audio_sha256": "a" * 64,
        "analysis_protocol": "local-vad-v1", "analysis_start_ms": 0, "analysis_end_ms": 1_000,
        "word_timings": [{"word_id": "word_01", "text": "Changed", "start_ms": 0, "end_ms": 900}],
        "cue_corrections": [{
            "cue_id": "cue_01", "expected_start_ms": 0, "expected_end_ms": 1_000,
            "start_ms": 0, "end_ms": 900, "source_word_ids": ["word_01"],
            "confirm_timing": False,
        }],
    }
    with pytest.raises(AppException) as exc_info:
        cues.apply_asr_vad_source_timing_correction(
            draft, VideoLocalizationAsrVadTimingCorrectionRequest(**request)
        )
    assert exc_info.value.code == "VIDEO_LOCALIZATION_SOURCE_TIMING_TEXT_CHANGED"


def test_asr_vad_batch_accepts_internal_zero_width_anchor_and_adjacent_cue_boundary():
    draft = _draft()
    assert draft.transcription is not None
    transcription = type(draft.transcription).model_validate(
        {
            **draft.transcription.model_dump(mode="json"),
            "alignment_source_track_id": "vocals",
            "alignment_audio_sha256": "a" * 64,
            "alignment_status": "partial",
            "audio_boundary_status": "completed",
            "words": [
                {"word_id": "word_01", "segment_id": "segment_01", "text": "I", "start_ms": 0, "end_ms": 50, "timing_confidence": "high", "timing_source": "forced_aligner"},
                {"word_id": "word_02", "segment_id": "segment_01", "text": "a", "start_ms": 50, "end_ms": 50, "timing_confidence": "low", "timing_source": "forced_aligner"},
                {"word_id": "word_03", "segment_id": "segment_01", "text": "piece", "start_ms": 50, "end_ms": 100, "timing_confidence": "high", "timing_source": "forced_aligner"},
                {"word_id": "word_04", "segment_id": "segment_01", "text": "we", "start_ms": 100, "end_ms": 150, "timing_confidence": "high", "timing_source": "forced_aligner"},
            ],
        }
    )
    first = draft.cues[0].model_copy(update={
        "start_ms": 0, "end_ms": 100, "source_word_ids": ["word_01", "word_02", "word_03"],
        "source_text_raw": "I a piece", "en_subtitle_text": "I a piece",
    })
    second = draft.cues[0].model_copy(update={
        "cue_id": "cue_02", "start_ms": 100, "end_ms": 200, "source_word_ids": ["word_04"],
        "source_text_raw": "we", "en_subtitle_text": "we",
    })
    draft = draft.model_copy(update={"transcription": transcription, "cues": [first, second]})

    corrected = cues.apply_asr_vad_source_timing_correction(
        draft,
        VideoLocalizationAsrVadTimingCorrectionRequest(
            expected_project_revision="8", transcription_revision_id="transcription_01",
            source_track_id="vocals", audio_sha256="a" * 64, analysis_protocol="local-vad-v1",
            analysis_start_ms=1_000, analysis_end_ms=1_500,
            word_timings=[
                {"word_id": "word_01", "text": "I", "start_ms": 1_000, "end_ms": 1_050},
                {"word_id": "word_02", "text": "a", "start_ms": 1_100, "end_ms": 1_100},
                {"word_id": "word_03", "text": "piece", "start_ms": 1_100, "end_ms": 1_200},
                {"word_id": "word_04", "text": "we", "start_ms": 1_200, "end_ms": 1_400},
            ],
            vad_intervals=[{"start_ms": 1_060, "end_ms": 1_090, "duration_ms": 30}],
            cue_corrections=[
                {"cue_id": "cue_01", "expected_start_ms": 0, "expected_end_ms": 100, "start_ms": 1_000, "end_ms": 1_200, "source_word_ids": ["word_01", "word_02", "word_03"], "vad_interval_ids": ["vad_1060_1090"]},
                {"cue_id": "cue_02", "expected_start_ms": 100, "expected_end_ms": 200, "start_ms": 1_200, "end_ms": 1_400, "source_word_ids": ["word_04"]},
            ],
        ),
    )

    first, second = corrected.cues
    assert first.end_ms == second.start_ms == 1_200
    assert cues.timing_confirmation_is_current_for_draft(corrected, first)
    assert cues.timing_confirmation_is_current_for_draft(corrected, second)
    assert "source_timing_zero_width" in first.quality_flags
    assert "ASR_CUE_ZERO_WIDTH_ALIGNMENT" in _warning_codes(corrected)

    assert corrected.transcription is not None
    changed_word = corrected.transcription.words[0].model_copy(update={"end_ms": 1_060})
    stale = corrected.model_copy(update={
        "transcription": corrected.transcription.model_copy(
            update={"words": [changed_word, *corrected.transcription.words[1:]]}
        )
    })
    assert not cues.timing_confirmation_is_current_for_draft(stale, stale.cues[0])


def test_public_source_timing_correction_rechecks_project_revision(monkeypatch):
    monkeypatch.setattr(video_localization_service.project_store, "get_project", lambda _project_id: object())
    monkeypatch.setattr(video_localization_service.project_store, "get_project_repository_revision", lambda _project_id: 9)
    request = video_localization_service.VideoLocalizationAsrVadTimingCorrectionRequest(
        expected_project_revision="8", transcription_revision_id="transcription_01",
        source_track_id="vocals", audio_sha256="a" * 64, analysis_protocol="local-vad-v1",
        analysis_start_ms=0, analysis_end_ms=1, word_timings=[{
            "word_id": "word_01", "text": "Hello", "start_ms": 0, "end_ms": 1,
        }], cue_corrections=[{
            "cue_id": "cue_01", "expected_start_ms": 0, "expected_end_ms": 1,
            "start_ms": 0, "end_ms": 1, "source_word_ids": ["word_01"], "confirm_timing": False,
        }],
    )
    with pytest.raises(AppException) as exc_info:
        video_localization_service.apply_asr_vad_source_timing_correction("project_01", request)
    assert exc_info.value.code == "VIDEO_LOCALIZATION_SOURCE_TIMING_PROJECT_CHANGED"


def test_source_timing_correction_api_uses_typed_public_facade(monkeypatch):
    draft = _draft()
    captured: dict[str, object] = {}

    def fake_apply(project_id: str, request: VideoLocalizationAsrVadTimingCorrectionRequest):
        captured.update(project_id=project_id, request=request)
        return draft

    monkeypatch.setattr(
        video_localization_api.video_localization_service,
        "apply_asr_vad_source_timing_correction",
        fake_apply,
    )
    request = VideoLocalizationAsrVadTimingCorrectionRequest(
        expected_project_revision="8", transcription_revision_id="transcription_01",
        source_track_id="vocals", audio_sha256="a" * 64, analysis_protocol="local-vad-v1",
        analysis_start_ms=0, analysis_end_ms=1_000,
        word_timings=[{"word_id": "word_01", "text": "Hello", "start_ms": 0, "end_ms": 900}],
        cue_corrections=[{
            "cue_id": "cue_01", "expected_start_ms": 0, "expected_end_ms": 1_000,
            "start_ms": 0, "end_ms": 900, "source_word_ids": ["word_01"],
            "confirm_timing": False,
        }],
    )
    result = video_localization_api.apply_video_localization_asr_vad_timing_correction(
        "project_01", request
    )
    assert result is draft
    assert captured["project_id"] == "project_01"
    assert captured["request"] is request


def test_auditioned_edge_correction_keeps_word_provenance_without_aligner_window_blocker():
    draft = cues.with_updated_cue(
        _draft(),
        "cue_01",
        VideoLocalizationCueUpdate(start_ms=50, end_ms=850),
    )

    confirmed = cues.with_updated_cue(
        draft,
        "cue_01",
        VideoLocalizationCueUpdate(confirm_timing=True, expected_start_ms=50, expected_end_ms=850),
    )

    assert confirmed.cues[0].source_word_ids == ["word_01"]
    assert "ASR_CUE_EXCLUDES_REFERENCED_WORDS" not in _blocker_codes(confirmed)


def test_confirmation_can_release_alignment_fallback_blockers_without_dropping_word_ids():
    draft = _draft(confidence="low")
    assert draft.transcription is not None
    interpolated_word = draft.transcription.words[0].model_copy(
        update={"timing_source": "asr_segment_interpolation", "timing_confidence": "low"}
    )
    draft = draft.model_copy(
        update={
            "transcription": draft.transcription.model_copy(
                update={
                    "alignment_status": "failed",
                    "timing_confidence": "low",
                    "words": [interpolated_word],
                }
            )
        }
    )
    assert "ASR_ALIGNMENT_FAILED" in _blocker_codes(draft)
    assert "ASR_TIMING_INTERPOLATED" in _warning_codes(draft)

    confirmed = cues.with_updated_cue(
        draft,
        "cue_01",
        VideoLocalizationCueUpdate(confirm_timing=True),
    )

    assert confirmed.cues[0].source_word_ids == ["word_01"]
    assert confirmed.cues[0].timing_confidence == "low"
    assert "ASR_ALIGNMENT_FAILED" not in _blocker_codes(confirmed)
    assert "ASR_TIMING_INTERPOLATED" not in _warning_codes(confirmed)


def test_second_timing_edit_invalidates_confirmation_but_keeps_audit_record():
    edited = cues.with_updated_cue(
        _draft(),
        "cue_01",
        VideoLocalizationCueUpdate(end_ms=1200, confirm_timing=True),
    )
    previous = edited.cues[0]

    changed = cues.with_updated_cue(
        edited,
        "cue_01",
        VideoLocalizationCueUpdate(end_ms=1300),
    ).cues[0]

    assert changed.manual_timing_revision == 2
    assert changed.manual_timing_review_status == "required"
    assert changed.manual_timing_confirmed_revision == 1
    assert changed.manual_timing_confirmed_at == previous.manual_timing_confirmed_at
    assert changed.manual_timing_confirmed_start_ms == 0
    assert changed.manual_timing_confirmed_end_ms == 1200
    assert "manual_timing_verified" not in changed.quality_flags
    assert "timing_review_required" in changed.quality_flags


def test_text_edit_does_not_invalidate_timing_confirmation_or_provenance():
    confirmed_draft = cues.with_updated_cue(
        _draft(),
        "cue_01",
        VideoLocalizationCueUpdate(confirm_timing=True),
    )

    corrected = cues.with_updated_cue(
        confirmed_draft,
        "cue_01",
        VideoLocalizationCueUpdate(en_subtitle_text="Hello!"),
    ).cues[0]

    assert corrected.source_word_ids == ["word_01"]
    assert corrected.transcription_revision_id == "transcription_01"
    assert corrected.manual_timing_review_status == "confirmed"
    assert "manual_timing_verified" in corrected.quality_flags
    assert "timing_review_required" not in corrected.quality_flags


def test_client_cannot_forge_confirmation_with_quality_flag_only():
    updated = cues.with_updated_cue(
        _draft(confidence="low"),
        "cue_01",
        VideoLocalizationCueUpdate(quality_flags=["generated_by_asr", "manual_timing_verified"]),
    ).cues[0]

    assert updated.manual_timing_review_status == "not_reviewed"
    assert "manual_timing_verified" not in updated.quality_flags
    assert "ASR_CUE_TIMING_LOW_CONFIDENCE" in _warning_codes(_draft(confidence="low").model_copy(update={"cues": [updated]}))


def test_full_draft_replace_cannot_forge_backend_timing_audit(monkeypatch):
    current = _draft(confidence="low")
    forged = current.cues[0].model_copy(
        update={
            "manual_timing_review_status": "confirmed",
            "manual_timing_confirmed_revision": 0,
            "manual_timing_confirmed_at": "2026-07-15T04:00:00",
            "manual_timing_confirmed_start_ms": 0,
            "manual_timing_confirmed_end_ms": 1000,
            "manual_timing_confirmation_method": "auditioned",
            "quality_flags": ["generated_by_asr", "manual_timing_verified"],
        }
    )
    incoming = current.model_copy(update={"cues": [forged]})
    monkeypatch.setattr(video_localization_service.draft_store, "get", lambda _project_id: current)
    monkeypatch.setattr(
        video_localization_service.draft_store,
        "save",
        lambda _project_id, draft, **_kwargs: draft,
    )

    saved = video_localization_service.replace_video_localization_from_client("project_01", incoming)

    assert saved is not None
    cue = saved.cues[0]
    assert cue.manual_timing_review_status == "not_reviewed"
    assert "manual_timing_verified" not in cue.quality_flags
    assert "ASR_CUE_TIMING_LOW_CONFIDENCE" in _warning_codes(saved)


def test_stale_expected_timing_cannot_be_confirmed():
    draft = cues.with_updated_cue(
        _draft(),
        "cue_01",
        VideoLocalizationCueUpdate(end_ms=1200),
    )

    with pytest.raises(AppException) as exc_info:
        cues.with_updated_cue(
            draft,
            "cue_01",
            VideoLocalizationCueUpdate(
                confirm_timing=True,
                expected_start_ms=0,
                expected_end_ms=1000,
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "VIDEO_LOCALIZATION_CUE_TIMING_CHANGED"


def test_confirmation_api_uses_expected_timing_and_explicit_audition_method(monkeypatch):
    draft = _draft()
    captured: dict[str, object] = {}

    def fake_update(project_id: str, cue_id: str, patch: VideoLocalizationCueUpdate):
        captured.update(project_id=project_id, cue_id=cue_id, patch=patch)
        return draft

    monkeypatch.setattr(video_localization_api.video_localization_service, "update_cue", fake_update)

    result = video_localization_api.confirm_video_localization_cue_timing(
        "project_01",
        "cue_01",
        VideoLocalizationCueTimingConfirmationRequest(
            start_ms=0,
            end_ms=1000,
        ),
    )

    assert result is draft
    assert captured["project_id"] == "project_01"
    assert captured["cue_id"] == "cue_01"
    patch = captured["patch"]
    assert isinstance(patch, VideoLocalizationCueUpdate)
    assert patch.confirm_timing is True
    assert patch.expected_start_ms == 0
    assert patch.expected_end_ms == 1000
    assert patch.timing_confirmation_method == "auditioned"
