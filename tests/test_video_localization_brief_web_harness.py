"""No-service/no-browser checks of the isolated brief recovery Web fixture."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from tests.test_video_localization_brief_service_journal import service_case  # noqa: F401


@pytest.fixture
def harness(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("brief_web_harness", scripts / "verify_brief_journal_web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FixedError(RuntimeError):
    def __init__(self, message, *, code, status_code):
        super().__init__(message)
        self.code, self.status_code = code, status_code


def test_fixture_provider_rejects_only_first_detail_and_never_calls_network(harness):
    provider = harness.FixedBriefProvider({"fixed": True},
        candidate_factory=lambda candidate, payload: {**candidate, "detail": "core_source_cues" in payload},
        trace_factory=lambda **kwargs: kwargs, error_factory=FixedError)
    provider.release("initial")
    traces = []
    outline = provider("outline", {"source_cues": []}, trace_sink=traces.append, profile_id="fixture-profile")
    assert outline == {"fixed": True, "detail": False}
    with pytest.raises(FixedError) as rejected:
        provider("detail", {"core_source_cues": []}, trace_sink=traces.append, profile_id="fixture-profile")
    assert rejected.value.code == "codex_cli_rate_limited"
    assert rejected.value.status_code == 429
    assert provider.snapshot() == {"model_calls": 2, "outline_calls": 1, "detail_calls": 1,
                                   "call_order": ["outline", "details"]}
    assert traces[-1]["error_code"] == "codex_cli_rate_limited"
    provider.release("recovery")
    recovered = provider("detail", {"core_source_cues": []}, trace_sink=traces.append, profile_id="fixture-profile")
    assert recovered == {"fixed": True, "detail": True}
    assert traces[-1]["error_code"] is None
    assert traces[-1]["finish_reason"] == "stop"
    with pytest.raises(RuntimeError, match="extra provider call"):
        provider("detail", {"core_source_cues": []}, trace_sink=traces.append, profile_id="fixture-profile")


def test_fixture_provider_rejects_unexpected_profile_and_release_phase(harness):
    provider = harness.FixedBriefProvider({}, candidate_factory=lambda *args: {},
        trace_factory=lambda **kwargs: kwargs, error_factory=FixedError)
    with pytest.raises(RuntimeError, match="Unexpected model profile"):
        provider("prompt", {}, trace_sink=lambda trace: None, profile_id="real-profile")
    assert provider.model_calls == 0
    with pytest.raises(ValueError, match="Unknown"):
        provider.release("other")


def test_journal_summary_filters_non_model_records_and_preserves_hash(harness, tmp_path):
    failed = {"contract_version": "localization-development-llm-batch-v1", "batch_id": "brief-details-chunk_0001",
        "status": "call_failed", "validations": [], "recovery_ordinal": 0,
        "recovery_execution_id": "op1", "call_error_code": "codex_cli_rate_limited", "input_fingerprint": "a" * 64}
    for name, result in (("failed", failed), ("merge", {"contract_version": "localization-brief-merge-validation-v1"})):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "checkpoint.json").write_text(json.dumps({"result": result}))
    first = harness.journal_summary(tmp_path)
    assert len(first) == 1
    assert first[0]["recovery_execution_id"] == "op1"
    assert first[0]["candidate_fingerprint"] is None
    assert len(first[0]["file_sha256"]) == 64
    assert harness.journal_summary(tmp_path) == first


def test_unowned_service_root_refused_before_app_import(harness, tmp_path):
    with pytest.raises(RuntimeError, match="owned acceptance root"):
        harness.serve(tmp_path, 12345)


@pytest.mark.parametrize("scenario", ["brief", "evidence", "generation"])
def test_failed_service_launch_cleans_owned_root_without_starting_browser(harness, monkeypatch, tmp_path, scenario):
    repository = tmp_path / "repository"
    build = repository / "frontend" / "build"
    build.mkdir(parents=True)
    (build / "index.html").write_text("fixed no-browser fixture")
    monkeypatch.setattr(harness, "ROOT", repository)
    monkeypatch.setattr(harness, "available_port", lambda: 12345)
    real_temporary_directory = tempfile.TemporaryDirectory
    created = []
    def temporary_directory(*args, **kwargs):
        context = real_temporary_directory(*args, **kwargs, dir=tmp_path)
        created.append(Path(context.name))
        return context
    monkeypatch.setattr(harness.tempfile, "TemporaryDirectory", temporary_directory)
    commands = []
    def never_launch(command, **kwargs):
        commands.append(command)
        owned = created[-1]
        assert Path(kwargs["env"]["VOICE_STUDIO_DATA_DIR"]) == owned
        assert Path(kwargs["env"]["VOICE_STUDIO_DB_PATH"]).is_relative_to(owned)
        raise RuntimeError("fixed startup failure")
    monkeypatch.setattr(harness.subprocess, "Popen", never_launch)
    with pytest.raises(RuntimeError, match="fixed startup failure"):
        harness.run(0, scenario)
    assert len(commands) == 1  # Browser/build were never started.
    assert commands[0][-2:] == ["--scenario", scenario]
    assert len(created) == 1 and not created[0].exists()


def test_evidence_fixture_preserves_image_batch_and_forbids_extra_calls(harness):
    provider = harness.FixedEvidenceProvider({}, candidate_factory=lambda candidate, payload: candidate,
        trace_factory=lambda **kwargs: kwargs, error_factory=FixedError)
    traces = []
    kwargs = {"trace_sink": traces.append, "profile_id": "fixture-profile"}
    provider("outline", {}, **kwargs)
    provider("details", {"core_source_cues": []}, **kwargs)
    images = [SimpleNamespace(data=b"\xff\xd8" + bytes([i])) for i in range(3)]
    provider.release("initial")
    first = provider.complete_images("images", {"questions": [{"question_id": "question_0001"}]}, images, **kwargs)
    assert first["answers"][0]["evidence_image_positions"] == [1, 2, 3]
    with pytest.raises(FixedError) as rejected:
        provider.complete_images("images", {"questions": [{"question_id": "question_0002"}]}, images, **kwargs)
    assert rejected.value.status_code == 429
    assert traces[-1]["error_code"] == "codex_cli_rate_limited"
    provider.release("recovery")
    provider.complete_images("images", {"questions": [{"question_id": "question_0002"}]}, images, **kwargs)
    snapshot = provider.snapshot()
    assert snapshot["image_order"] == ["question_0001", "question_0002", "question_0002"]
    assert snapshot["image_hashes"][1] == snapshot["image_hashes"][2]
    assert snapshot["text_calls"] == 2
    with pytest.raises(RuntimeError, match="Unexpected image call"):
        provider.complete_images("images", {"questions": [{"question_id": "question_0002"}]}, images, **kwargs)
    with pytest.raises(RuntimeError, match="Unexpected text call"):
        provider("text fallback", {}, **kwargs)


@pytest.mark.parametrize("profile,count,jpeg", [("real-profile", 3, True), ("fixture-profile", 2, True), ("fixture-profile", 3, False)])
def test_evidence_fixture_rejects_wrong_profile_or_image_shape(harness, profile, count, jpeg):
    provider = harness.FixedEvidenceProvider({}, candidate_factory=lambda *args: {},
        trace_factory=lambda **kwargs: kwargs, error_factory=FixedError)
    images = [SimpleNamespace(data=b"\xff\xd8" if jpeg else b"not-jpeg") for _ in range(count)]
    with pytest.raises(RuntimeError):
        provider.complete_images("prompt", {"questions": [{"question_id": "question_0001"}]}, images,
            trace_sink=lambda trace: pytest.fail("invalid fixture emitted trace"), profile_id=profile)
    assert provider.snapshot()["image_calls"] == 0


def test_evidence_video_is_imported_as_managed_playable_media(harness, tmp_path, request):
    from app.domains.video_localization import media_assets, media_health, service

    project_id, calls = request.getfixturevalue("service_case")
    media = harness.import_fixture_video(tmp_path, project_id)
    path = Path(media.video_path)
    assert path.is_file() and path.is_relative_to(media_assets.project_video_localization_dir(project_id))
    assert media.content_sha256 == media_assets.file_sha256(path)
    assert media.duration_ms == 1000 and media.width == 320 and media.height == 180
    assert media_health.inspect_project_media(service.get_video_localization(project_id)).paths.source_video == path
    assert calls == []


def test_content_snapshot_ignores_only_operation_bookkeeping_not_media_or_subtitles(harness):
    from tests.test_video_localization_localization_context_intent import _draft
    from app.domains.video_localization.operation_state import with_kind_status

    draft = _draft()
    before = harness.project_content_snapshot(draft)
    failed = with_kind_status(draft, "localization_draft", "failed", error_code="fixed", error_message="fixed")
    failed.updated_at = "2026-01-01T00:00:00Z"
    failed.quality_gate.checked_at = "2026-01-01T00:00:00Z"
    assert harness.project_content_snapshot(failed) == before
    changed = draft.model_copy(deep=True)
    changed.source_media.video_path = "/changed-source.mp4"
    assert harness.project_content_snapshot(changed) != before
    changed = draft.model_copy(deep=True)
    changed.cues[0].en_subtitle_text = "Changed content."
    assert harness.project_content_snapshot(changed) != before
    changed = draft.model_copy(deep=True)
    changed.source_media.metadata["audio_extract_status"] = "failed"
    assert harness.project_content_snapshot(changed) != before


def test_generation_fixture_uses_real_two_section_chunk_plan(harness):
    from tests.test_video_localization_localization_context_intent import _draft, _source
    from tests.test_video_localization_localization_spoken_script import _source_and_brief
    from app.domains.video_localization.localization_document_brief import LocalizationDocumentBriefContent
    from app.domains.video_localization.localization_generation_chunks import plan_localization_generation_chunks

    draft = _draft(include_brief=False)
    _, brief = _source_and_brief()
    candidate = brief.content.model_dump(mode="json")
    harness.prepare_generation_fixture(draft, candidate)
    source = _source(draft)
    content = LocalizationDocumentBriefContent.model_validate(candidate)
    manifest = plan_localization_generation_chunks(source_fingerprint=source.source_fingerprint,
        cues=list(source.input.cues), sections=content.structure, pauses=list(source.input.pauses))
    assert [chunk.chunk_id for chunk in manifest.chunks] == ["chunk_0001", "chunk_0002"]
    assert [chunk.source_cue_ids for chunk in manifest.chunks] == [["cue_0001"], ["cue_0002"]]
    assert [chunk.source_text for chunk in manifest.chunks] == ["Hello world.", "Goodbye world."]
    assert content.evidence_questions == []
    assert [word.word_id for word in draft.transcription.words] == ["word_0001", "word_0002", "word_0003", "word_0004"]


def test_generation_fixture_refuses_only_second_batch_and_keeps_identical_inputs(harness):
    provider = harness.FixedGenerationProvider({}, candidate_factory=lambda candidate, payload: candidate,
        trace_factory=lambda **kwargs: kwargs, error_factory=FixedError)
    traces = []
    kwargs = {"trace_sink": traces.append, "profile_id": "fixture-profile"}
    provider("outline", {}, **kwargs)
    provider("details", {"core_source_cues": []}, **kwargs)
    provider("details", {"core_source_cues": []}, **kwargs)
    provider.release("initial")
    first = provider("generate", {"chunk_id": "chunk_0001", "editable_source_cues": []}, **kwargs)
    assert first == {"chunk_id": "chunk_0001", "suggested_title": "问候与告别", "paragraphs": ["你好，世界。"]}
    second_input = {"chunk_id": "chunk_0002", "editable_source_cues": []}
    with pytest.raises(FixedError) as rejected:
        provider("generate", second_input, **kwargs)
    assert rejected.value.status_code == 429
    assert traces[-1]["error_code"] == "codex_cli_rate_limited"
    provider.release("recovery")
    second = provider("generate", second_input, **kwargs)
    assert second == {"chunk_id": "chunk_0002", "suggested_title": None, "paragraphs": ["再见，世界。"]}
    snapshot = provider.snapshot()
    assert snapshot["text_calls"] == 3
    assert snapshot["generation_order"] == ["chunk_0001", "chunk_0002", "chunk_0002"]
    assert snapshot["generation_inputs"][1] == snapshot["generation_inputs"][2]
    with pytest.raises(RuntimeError, match="Unexpected generation call"):
        provider("generate", second_input, **kwargs)
    with pytest.raises(RuntimeError, match="Unexpected fixed generation profile"):
        provider("generate", second_input, trace_sink=traces.append, profile_id="real-profile")


@pytest.fixture
def completed_fixture(tmp_path, request):
    from app.domains.video_localization import service
    from app.domains.video_localization.localization_workflow_execution import LocalizationDevelopmentExecutionConfig

    project_id, calls = request.getfixturevalue("service_case")
    root = tmp_path / "receipt-acceptance"
    service.run_localization_v3_draft(project_id, operation_id="source-op", profile_id="profile",
        development_execution=LocalizationDevelopmentExecutionConfig(development_session_id="source-session",
            target_step_id="analyze_localization_document", snapshot_root=root))
    assert len(calls) == 2
    return root, project_id, calls


def test_fixture_import_uses_verified_full_evidence_and_public_recovery(harness, completed_fixture, monkeypatch):
    from app.domains.video_localization import service
    from app.domains.video_localization.localization_workflow_execution import LocalizationDevelopmentExecutionConfig
    from app.services import llm_runtime

    root, project_id, calls = completed_fixture
    before = harness.journal_summary(root / "source-session")
    receipts = harness.import_fixture_candidates(root, project_id=project_id,
        source_session="source-session", target_session="receipt-session")
    assert len(receipts) == 2
    for receipt in receipts:
        original = next(record for record in before if record["batch_id"] == receipt["batch_id"])
        assert receipt["source_evidence_fingerprint"] == original["file_sha256"]
        assert receipt["candidate_fingerprint"] == original["candidate_fingerprint"]
        assert receipt["telemetry"] == "unavailable"
    assert harness.import_fixture_candidates(root, project_id=project_id,
        source_session="source-session", target_session="receipt-session") == receipts
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("receipt recovery called model"))
    service.run_localization_v3_draft(project_id, operation_id="receipt-op", profile_id="profile",
        development_execution=LocalizationDevelopmentExecutionConfig(development_session_id="receipt-session",
            target_step_id="analyze_localization_document", snapshot_root=root, recover_verified_candidates=True))
    assert len(calls) == 2
    assert harness.journal_summary(root / "source-session") == before
    recovered = harness.journal_summary(root / "receipt-session")
    assert len(recovered) == 2
    assert all(record["status"] == "validation_passed" and record["recorded_call_count"] == 0
               and record["recovered_candidate"]["telemetry"] == "unavailable" for record in recovered)


@pytest.mark.parametrize("damage", ["missing", "tampered", "wrong-project", "same-session"])
def test_fixture_import_refuses_incomplete_or_unverified_evidence(harness, completed_fixture, damage):
    root, project_id, _ = completed_fixture
    path = next(path for path in (root / "source-session").glob("*/checkpoint.json")
                if json.loads(path.read_text())["result"].get("batch_id") == "brief-outline")
    if damage == "missing":
        path.unlink()
    elif damage == "tampered":
        content = json.loads(path.read_text())
        content["result"]["raw_json_candidate"]["purpose"] = "not the recorded candidate"
        path.write_text(json.dumps(content))
    with pytest.raises(ValueError):
        harness.import_fixture_candidates(root,
            project_id="another-project" if damage == "wrong-project" else project_id,
            source_session="source-session",
            target_session="source-session" if damage == "same-session" else "receipt-session")
    assert not (root / "candidate-recovery").exists()
