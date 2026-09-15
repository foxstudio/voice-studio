from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domains.video_localization import workflow_submission, operation_queue, service
from app.domains.video_localization.schemas import VideoLocalizationCue, VideoLocalizationDraft
from app.errors import AppException
from app.services import llm_runtime


def _draft():
    return VideoLocalizationDraft(cues=[VideoLocalizationCue(
        cue_id="source", start_ms=0, end_ms=1000, en_subtitle_text="An original sentence.",
    )])


def _binding(draft):
    return workflow_submission.capture_submission_binding(draft, profile_id="test", requirements_id=None)


@pytest.fixture
def profiles(monkeypatch):
    state = {"model": "model-one", "endpoint": "https://example.invalid/v1"}
    def resolve(_profile_id=None):
        return llm_runtime.ResolvedProfile(
            profile_id="test", protocol="openai_compatible", base_url=state["endpoint"],
            model_id=state["model"], api_key="never-persist-this-secret",
        )
    monkeypatch.setattr(llm_runtime, "resolve_profile", resolve)
    return state


def test_binding_preserves_ui_independence_and_contains_no_secrets(profiles):
    draft = _draft()
    binding = _binding(draft)
    edited = draft.model_copy(update={"ui_state": {"timeline_zoom": 20}})
    assert _binding(edited) == binding
    serialized = binding.model_dump_json()
    assert "never-persist" not in serialized
    assert "example.invalid" not in serialized
    assert "An original sentence" not in serialized


@pytest.mark.parametrize("change", ["source", "model", "endpoint", "behavior"])
def test_queued_localization_rejects_changed_input_before_work(monkeypatch, profiles, change):
    draft = _draft()
    binding = _binding(draft)
    if change == "source":
        draft.cues[0].en_subtitle_text = "User changed this sentence."
    elif change == "behavior":
        monkeypatch.setattr(workflow_submission, "node_behavior_fingerprint", lambda _step: "changed")
    else:
        profiles[change] = "changed"
    monkeypatch.setattr(service.project_store, "get_project", lambda _id: object())
    monkeypatch.setattr(service, "get_video_localization", lambda _id: draft)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("No pipeline/model work is allowed for a stale submission")
    monkeypatch.setattr(service.workflow_ledger, "LocalizationWorkflowLedger", forbidden)
    with pytest.raises(AppException) as exc:
        service.run_localization_v3_draft(
            "test", operation_id="queued", profile_id="test",
            expected_submission_binding=binding.model_dump(mode="json"),
        )
    assert exc.value.code == "VIDEO_LOCALIZATION_SUBMISSION_CHANGED"


def test_submit_persists_server_owned_binding(monkeypatch, profiles):
    draft = _draft()
    monkeypatch.setattr(operation_queue.project_store, "get_project", lambda _id: object())
    monkeypatch.setattr(service, "get_video_localization", lambda _id: draft)
    monkeypatch.setattr(operation_queue.video_localization_operation_store, "project_revision", lambda _id: 1)
    monkeypatch.setattr(operation_queue.operation_state, "validate_prerequisites", lambda *_args: None)
    monkeypatch.setattr(operation_queue, "_active_operation_for_kind", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operation_queue, "_enqueue", lambda *_args: None)
    captured = []
    monkeypatch.setattr(service, "save_video_localization", lambda *_args, **kwargs: captured.append(kwargs))
    result = operation_queue.submit("test", "localization_draft", {
        "profile_id": "test", "submission_binding": {"forged": True},
    })
    assert result.parameters["submission_binding"] == _binding(draft).model_dump(mode="json")
    assert captured[0]["operation_command"].operation_id == result.operation_id


@pytest.mark.parametrize("change", ["source", "model", "endpoint", "behavior"])
def test_queued_asr_rejects_changed_input_before_work(monkeypatch, profiles, change):
    draft = _draft()
    binding = workflow_submission.capture_asr_submission_binding(draft, profile_id="test")
    assert workflow_submission.capture_asr_submission_binding(
        draft.model_copy(update={"ui_state": {"timeline_zoom": 20}}), profile_id="test",
    ) == binding
    if change == "source":
        draft.cues[0].en_subtitle_text = "User edited this sentence."
    elif change == "behavior":
        monkeypatch.setattr(
            workflow_submission.asr_development_workflow_nodes,
            "asr_development_behavior_fingerprint", lambda _step: "changed",
        )
    else:
        profiles[change] = "changed"
    monkeypatch.setattr(service.project_store, "get_project", lambda _id: object())
    monkeypatch.setattr(service, "get_video_localization", lambda _id: draft)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Stale ASR must not invoke the source pipeline")
    monkeypatch.setattr(service.source_pipeline, "with_english_asr", forbidden)
    with pytest.raises(AppException) as exc:
        service.transcribe_english_source_audio(
            "test", llm_profile_id="test",
            expected_submission_binding=binding.model_dump(mode="json"),
        )
    assert exc.value.code == "VIDEO_LOCALIZATION_SUBMISSION_CHANGED"
