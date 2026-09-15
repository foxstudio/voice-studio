from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api import video_localization as api
from app.domains.video_localization import service
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.errors import AppException
from app.schemas.video_localization_binding_repair import BindingRepairRequest


def binding_fixture_draft():
    draft = VideoLocalizationDraft.model_validate({
        "source_media": {"duration_ms": 3000},
        "transcription": {
            "revision_id": "source-1", "source_track_id": "vocals",
            "alignment_source_track_id": "vocals", "source_audio_sha256": "a" * 64,
            "alignment_audio_sha256": "a" * 64,
            "words": [{"word_id": f"w{i}", "segment_id": "s", "text": text,
                       "start_ms": (i - 1) * 1000, "end_ms": i * 1000,
                       "timing_source": "forced_aligner"}
                      for i, text in enumerate(["Left.", "Transition.", "Right."], 1)],
        },
        "cues": [{"cue_id": f"c{i}", "start_ms": (i - 1) * 1000, "end_ms": i * 1000,
                  "en_subtitle_text": text, "source_word_ids": [f"w{i}"]}
                 for i, text in enumerate(["Left.", "Transition.", "Right."], 1)],
        "localized_subtitles": [
            {"subtitle_id": "z1", "text": "左。", "tts_text": "左。", "start_ms": 0, "end_ms": 2000,
             "source_word_ids": ["w1", "w2"], "source_cue_ids": ["c1", "c2"], "linked_cue_id": "c1", "spoken_segment_id": "p1"},
            {"subtitle_id": "z2", "text": "过渡。右。", "tts_text": "过渡。右。", "start_ms": 2000, "end_ms": 3000,
             "source_word_ids": ["w3"], "source_cue_ids": ["c3"], "linked_cue_id": "c3", "spoken_segment_id": "p2"},
        ],
        "localized_spoken_segments": [
            {"segment_id": "p1", "paragraph_id": "p1", "text": "左。", "start_ms": 0, "end_ms": 2000,
             "source_word_ids": ["w1", "w2"], "source_cue_ids": ["c1", "c2"]},
            {"segment_id": "p2", "paragraph_id": "p2", "text": "过渡。右。", "start_ms": 2000, "end_ms": 3000,
             "source_word_ids": ["w3"], "source_cue_ids": ["c3"]},
        ],
    })
    return draft


@pytest.fixture
def binding_store(monkeypatch):
    state = {"draft": binding_fixture_draft(), "revision": 1, "writes": 0}
    monkeypatch.setattr(service, "get_video_localization", lambda _: state["draft"].model_copy(deep=True))
    monkeypatch.setattr(service.project_store, "get_project_repository_revision", lambda _: state["revision"])
    monkeypatch.setattr(service.quality_gate, "current_transcription_alignment_audio_sha256", lambda _: "a" * 64)

    def save(_, updated, *, intent):
        assert intent == "content"
        state.update(draft=updated, revision=state["revision"] + 1, writes=state["writes"] + 1)
        return updated

    monkeypatch.setattr(service.draft_store, "save", save)
    request = BindingRepairRequest(expected_project_revision="1", transcription_revision_id="source-1",
        audio_sha256="a" * 64, reason="Transition belongs to the right paragraph.",
        bindings=[{"subtitle_id": "z1", "source_word_ids": ["w1"]},
                  {"subtitle_id": "z2", "source_word_ids": ["w2", "w3"]}])
    return state, request


def test_binding_repair_public_entry_is_atomic_and_idempotent(binding_store):
    state, request = binding_store
    result = api.apply_video_localization_binding_repair("p", request)
    assert result.localized_subtitles[1].start_ms == 1000
    assert result.localized_spoken_segments[1].start_ms == 1000
    assert result.source_binding_repairs[0].request == request
    replay = api.apply_video_localization_binding_repair("p", request)
    assert replay == result
    assert state["writes"] == 1


def test_binding_repair_rejects_stale_repository(binding_store):
    state, request = binding_store
    state["revision"] = 2
    with pytest.raises(AppException) as error:
        api.apply_video_localization_binding_repair("p", request)
    assert error.value.code == "VIDEO_LOCALIZATION_BINDING_REPAIR_PROJECT_CHANGED"
    assert state["writes"] == 0


def test_binding_repair_rejects_changed_audio(binding_store, monkeypatch):
    state, request = binding_store
    monkeypatch.setattr(service.quality_gate, "current_transcription_alignment_audio_sha256", lambda _: "b" * 64)
    with pytest.raises(AppException) as error:
        api.apply_video_localization_binding_repair("p", request)
    assert error.value.code == "VIDEO_LOCALIZATION_BINDING_REPAIR_AUDIO_CHANGED"
    assert state["writes"] == 0


def test_binding_repair_rejects_new_transcription_even_on_replay(binding_store):
    state, request = binding_store
    api.apply_video_localization_binding_repair("p", request)
    state["draft"].transcription.revision_id = "source-2"
    with pytest.raises(AppException) as error:
        api.apply_video_localization_binding_repair("p", request)
    assert error.value.code == "VIDEO_LOCALIZATION_BINDING_REPAIR_TRANSCRIPTION_CHANGED"
    assert state["writes"] == 1


def test_binding_repair_rejects_active_operation(binding_store):
    state, request = binding_store
    from app.schemas.voice_studio import VideoLocalizationOperation
    state["draft"].operations = [VideoLocalizationOperation(operation_id="busy", project_id="p", kind="english_asr", status="running")]
    with pytest.raises(AppException) as error:
        api.apply_video_localization_binding_repair("p", request)
    assert error.value.code == "VIDEO_LOCALIZATION_BINDING_REPAIR_TASK_ACTIVE"
    assert state["writes"] == 0


def test_client_save_cannot_erase_binding_repair_receipt(binding_store, monkeypatch):
    state, request = binding_store
    result = api.apply_video_localization_binding_repair("p", request)
    monkeypatch.setattr(service.draft_store, "get", lambda _: state["draft"])
    incoming = result.model_copy(deep=True)
    incoming.source_binding_repairs = []
    saved = service.replace_video_localization_from_client("p", incoming)
    assert saved.source_binding_repairs == result.source_binding_repairs
