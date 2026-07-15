from __future__ import annotations

import asyncio
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
    assert "ASR_CUE_TIMING_LOW_CONFIDENCE" in _blocker_codes(draft.model_copy(update={"cues": [updated]}))


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
    assert {"ASR_ALIGNMENT_FAILED", "ASR_TIMING_INTERPOLATED"} <= _blocker_codes(draft)

    confirmed = cues.with_updated_cue(
        draft,
        "cue_01",
        VideoLocalizationCueUpdate(confirm_timing=True),
    )

    assert confirmed.cues[0].source_word_ids == ["word_01"]
    assert confirmed.cues[0].timing_confidence == "low"
    assert "ASR_ALIGNMENT_FAILED" not in _blocker_codes(confirmed)
    assert "ASR_TIMING_INTERPOLATED" not in _blocker_codes(confirmed)


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
    assert "ASR_CUE_TIMING_LOW_CONFIDENCE" in _blocker_codes(_draft(confidence="low").model_copy(update={"cues": [updated]}))


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
    monkeypatch.setattr(video_localization_service.draft_store, "save", lambda _project_id, draft: draft)

    saved = video_localization_service.replace_video_localization_from_client("project_01", incoming)

    assert saved is not None
    cue = saved.cues[0]
    assert cue.manual_timing_review_status == "not_reviewed"
    assert "manual_timing_verified" not in cue.quality_flags
    assert "ASR_CUE_TIMING_LOW_CONFIDENCE" in _blocker_codes(saved)


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

    result = asyncio.run(
        video_localization_api.confirm_video_localization_cue_timing(
            "project_01",
            "cue_01",
            VideoLocalizationCueTimingConfirmationRequest(start_ms=0, end_ms=1000),
        )
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
