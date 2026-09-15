from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api import video_localization as api  # noqa: E402
from app.domains.video_localization import service  # noqa: E402
from app.domains.video_localization.schemas import VideoLocalizationDraft  # noqa: E402
from app.errors import AppException  # noqa: E402
from app.schemas.video_localization_asr_repair import AsrSourceRepairRequest  # noqa: E402


@pytest.fixture
def repair_store(monkeypatch):
    draft = VideoLocalizationDraft.model_validate({
        "source_media": {"duration_ms": 2000},
        "transcription": {
            "revision_id": "old", "engine_id": "qwen3-asr-mlx",
            "source_track_id": "vocals", "alignment_source_track_id": "vocals",
            "source_audio_sha256": "a" * 64, "alignment_audio_sha256": "a" * 64,
            "raw_text": "Hello. Imagined.", "corrected_text": "Hello. Imagined.",
            "segments": [
                {"segment_id": "s1", "start_ms": 0, "end_ms": 1000, "raw_text": "Hello."},
                {"segment_id": "s2", "start_ms": 1000, "end_ms": 2000, "raw_text": "Imagined."},
            ],
            "words": [
                {"word_id": "w1", "segment_id": "s1", "text": "Hello.", "start_ms": 0, "end_ms": 900},
                {"word_id": "w2", "segment_id": "s2", "text": "Imagined.", "start_ms": 1000, "end_ms": 2000},
            ],
        },
        "cues": [
            {"cue_id": "c1", "start_ms": 0, "end_ms": 1000, "en_subtitle_text": "Hello.", "source_word_ids": ["w1"]},
            {"cue_id": "c2", "start_ms": 1000, "end_ms": 2000, "en_subtitle_text": "Imagined.", "source_word_ids": ["w2"]},
        ],
    })
    state = {"draft": draft, "revision": 1, "writes": 0}
    monkeypatch.setattr(service, "get_video_localization", lambda _: state["draft"].model_copy(deep=True))
    monkeypatch.setattr(service.project_store, "get_project_repository_revision", lambda _: state["revision"])
    monkeypatch.setattr(service.quality_gate, "current_transcription_alignment_audio_sha256", lambda _: "a" * 64)

    def save(_, updated, *, intent):
        assert intent == "content"
        state.update(draft=updated, revision=state["revision"] + 1, writes=state["writes"] + 1)
        return updated

    monkeypatch.setattr(service.draft_store, "save", save)
    request = AsrSourceRepairRequest(
        expected_project_revision="1", transcription_revision_id="old", audio_sha256="a" * 64,
        request_id="repair-1", excluded_segment_ids=["s2"], evidence={
            "source_audio_start_ms": 0, "source_audio_end_ms": 2000,
            "observed_text": "Hello.", "engine_id": "qwen3-asr-mlx", "reason": "Independent observation has no second utterance.",
        },
    )
    return state, request


def test_source_repair_public_entry_persists_receipt_and_replay_is_read_only(repair_store):
    state, request = repair_store
    result = api.apply_video_localization_asr_source_repair("p", request)
    assert result.transcription.corrected_text == "Hello."
    assert result.transcription.asr_source_repairs[0].request_fingerprint == request.fingerprint()
    replay = api.apply_video_localization_asr_source_repair("p", request)
    assert replay.model_dump() == result.model_dump()
    assert state["writes"] == 1


def test_source_repair_rejects_stale_project_without_writes(repair_store):
    state, request = repair_store
    state["revision"] = 2
    with pytest.raises(AppException) as error:
        service.apply_asr_source_repair("p", request)
    assert error.value.code == "VIDEO_LOCALIZATION_ASR_REPAIR_PROJECT_CHANGED"
    assert state["writes"] == 0


def test_source_repair_rejects_changed_request_identity(repair_store):
    state, request = repair_store
    service.apply_asr_source_repair("p", request)
    with pytest.raises(AppException) as error:
        service.apply_asr_source_repair("p", request.model_copy(update={"excluded_segment_ids": ["s1"]}))
    assert error.value.code == "VIDEO_LOCALIZATION_ASR_REPAIR_REQUEST_CONFLICT"
    assert state["writes"] == 1


def test_source_repair_rejects_changed_audio(repair_store, monkeypatch):
    state, request = repair_store
    monkeypatch.setattr(service.quality_gate, "current_transcription_alignment_audio_sha256", lambda _: "b" * 64)
    with pytest.raises(AppException) as error:
        service.apply_asr_source_repair("p", request)
    assert error.value.code == "VIDEO_LOCALIZATION_ASR_REPAIR_AUDIO_CHANGED"
    assert state["writes"] == 0


def test_source_repair_rejects_running_operation(repair_store):
    state, request = repair_store
    from app.schemas.voice_studio import VideoLocalizationOperation
    state["draft"].operations = [VideoLocalizationOperation(
        operation_id="busy", project_id="p", kind="english_asr", status="running",
    )]
    with pytest.raises(AppException) as error:
        service.apply_asr_source_repair("p", request)
    assert error.value.code == "VIDEO_LOCALIZATION_ASR_REPAIR_TASK_ACTIVE"
    assert state["writes"] == 0


def test_source_repair_replay_never_resurrects_superseded_transcript(repair_store):
    state, request = repair_store
    service.apply_asr_source_repair("p", request)
    state["draft"].transcription.revision_id = "newer-user-edit"
    with pytest.raises(AppException) as error:
        service.apply_asr_source_repair("p", request)
    assert error.value.code == "VIDEO_LOCALIZATION_ASR_REPAIR_SUPERSEDED"
    assert state["writes"] == 1
