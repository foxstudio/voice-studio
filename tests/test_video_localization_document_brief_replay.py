from __future__ import annotations

import json

import pytest

from tests.test_video_localization_localization_document_brief import _inputs
from tests.test_video_localization_localization_spoken_script import _source_and_brief
from app.domains.video_localization import localization_document_brief as brief
from app.domains.video_localization import localization_brief_stages as stages
from app.domains.video_localization.development_llm_batches import DevelopmentLlmBatchReplay
from app.services import llm_runtime
from tests.brief_stage_fixtures import staged_candidates


@pytest.fixture
def request_and_candidate(monkeypatch):
    source, context, route = _inputs()
    request = brief.LocalizationDocumentBriefInput(
        source_operation_id="source_operation",
        context_operation_id="context_operation",
        source_lock=source,
        context_intent=context,
        route=route,
    )
    _, existing = _source_and_brief()
    profile = llm_runtime.ResolvedProfile(
        profile_id="profile",
        protocol="openai_compatible",
        base_url="http://localhost:9000/v1",
        model_id="model",
        reasoning_effort="low",
    )
    monkeypatch.setattr(llm_runtime, "resolve_profile", lambda *a, **k: profile)
    return request, existing.content.model_dump(mode="json")


def _journal(root):
    return DevelopmentLlmBatchReplay(
        root=root,
        project_id="project",
        development_session_id="session",
        step_id_prefix="analyze_localization_document.llm_batch",
    )


def _records(root):
    return [result for path in root.rglob("checkpoint.json")
            if (result := json.loads(path.read_text())["result"]).get("contract_version")
            == "localization-development-llm-batch-v1"]


def _assembly(root):
    records = [result for path in root.rglob("checkpoint.json")
               if (result := json.loads(path.read_text())["result"]).get("contract_version")
               == "localization-brief-assembly-validation-v1"]
    assert len(records) == 1
    return records[0]


def _fake_provider(monkeypatch, responses, calls, root):
    def complete(prompt, payload, *, trace_sink, **kwargs):
        assert any(r["status"] == "prepared" and r["call_input"]["user_payload"] == payload for r in _records(root))
        calls.append((prompt, payload))
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=6000,
                timeout_seconds=600,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=1,
                finish_reason="stop",
            )
        )
        return responses[len(calls) - 1]

    monkeypatch.setattr(llm_runtime, "complete_json", complete)


def test_brief_session_reentry_reuses_success_without_double_counting(tmp_path, monkeypatch, request_and_candidate):
    request, candidate = request_and_candidate
    calls = []
    outline, details = staged_candidates(candidate)
    _fake_provider(monkeypatch, [outline, details], calls, tmp_path)
    first = brief.analyze_localization_document(request, batch_journal=_journal(tmp_path))
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("duplicate call"))
    resumed = brief.analyze_localization_document(request, batch_journal=_journal(tmp_path))
    assert resumed == first
    assert resumed.quality_summary.model_call_count == 2
    records = {r["batch_id"]: r for r in _records(tmp_path)}
    assert records["brief-outline"]["raw_json_candidate"] == outline
    assert records["brief-details-chunk_0001"]["raw_json_candidate"] == details
    assert all(r["status"] == "validation_passed" and r["attempt"] == 0 for r in records.values())
    assert all(len(r["validations"]) == 2 for r in records.values())


@pytest.mark.parametrize("repair_stage", ["outline", "details"])
def test_brief_repair_reentry_reuses_both_candidates_and_trace_facts(tmp_path, monkeypatch, request_and_candidate, repair_stage):
    request, candidate = request_and_candidate
    outline, details = staged_candidates(candidate)
    field = "speaker_profile" if repair_stage == "outline" else "immutable_facts"
    invalid = {key: value for key, value in (outline if repair_stage == "outline" else details).items() if key != field}
    responses = [invalid, outline, details] if repair_stage == "outline" else [outline, invalid, details]
    calls = []
    _fake_provider(monkeypatch, responses, calls, tmp_path)
    first = brief.analyze_localization_document(request, batch_journal=_journal(tmp_path))
    assert len(calls) == 3
    repair_call = calls[1 if repair_stage == "outline" else 2]
    assert repair_call[1]["invalid_output"] == invalid
    assert repair_call[1]["validation_errors"][0]["path"] == [field]
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("duplicate repair"))
    resumed = brief.analyze_localization_document(request, batch_journal=_journal(tmp_path))
    assert resumed == first
    assert [call.round_index for call in resumed.llm_calls] == ([1, 2, 3] if repair_stage == "outline" else [1, 3, 4])
    assert resumed.quality_summary.model_call_count == 3
    records = {r["batch_id"]: r for r in _records(tmp_path)}
    batch_id = "brief-outline" if repair_stage == "outline" else "brief-details-chunk_0001"
    assert records[batch_id]["status"] == "validation_failed"
    assert records[batch_id + "-repair"]["status"] == "validation_passed"
    assert {r["attempt"] for r in records.values()} == {0}


def test_brief_postvalidation_failure_preserves_candidate_for_local_revalidation(
    tmp_path, monkeypatch, request_and_candidate
):
    request, candidate = request_and_candidate
    calls = []
    _fake_provider(monkeypatch, list(staged_candidates(candidate)), calls, tmp_path)
    original = brief._validate_references

    def broken(*args):
        assert all(r["status"] == "validation_passed" for r in _records(tmp_path))
        raise ValueError("local reference validator defect")

    monkeypatch.setattr(brief, "_validate_references", broken)
    with pytest.raises(ValueError, match="local reference validator"):
        brief.analyze_localization_document(request, batch_journal=_journal(tmp_path))
    assert _assembly(tmp_path)["status"] == "failed"
    assert _assembly(tmp_path)["error_type"] == "ValueError"
    monkeypatch.setattr(brief, "_validate_references", original)
    result = brief.analyze_localization_document(request, batch_journal=_journal(tmp_path))
    assert result.quality_summary.status == "passed"
    assert len(calls) == 2
    assert _assembly(tmp_path)["status"] == "passed"
    assert _assembly(tmp_path)["output_fingerprint"]
    assert all(len(r["validations"]) == 2 for r in _records(tmp_path))


@pytest.mark.parametrize("failure_stage", ["outline", "details"])
def test_brief_second_schema_failure_retains_candidates_without_extra_model_call(
    tmp_path, monkeypatch, request_and_candidate, failure_stage
):
    request, candidate = request_and_candidate
    outline, details = staged_candidates(candidate)
    field = "speaker_profile" if failure_stage == "outline" else "immutable_facts"
    invalid = {key: value for key, value in (outline if failure_stage == "outline" else details).items() if key != field}
    responses = [invalid, invalid] if failure_stage == "outline" else [outline, invalid, invalid]
    calls = []
    _fake_provider(monkeypatch, responses, calls, tmp_path)
    for _ in range(2):
        with pytest.raises(ValueError, match="字段修复后仍未通过契约"):
            brief.analyze_localization_document(request, batch_journal=_journal(tmp_path))
    assert len(calls) == len(responses)
    assert len(_records(tmp_path)) == len(responses)
    failed = [record for record in _records(tmp_path) if record["status"] == "validation_failed"]
    assert len(failed) == 2
    assert all(record["raw_json_candidate"] == invalid for record in failed)


@pytest.mark.parametrize("failure_stage", ["outline", "outline-repair", "details", "details-repair"])
def test_brief_unknown_provider_result_fails_closed_without_repair_or_reissue(
    tmp_path,
    monkeypatch,
    request_and_candidate,
    failure_stage,
):
    request, candidate = request_and_candidate
    outline, details = staged_candidates(candidate)
    calls = []
    responses = {
        "outline": [],
        "outline-repair": [{k: v for k, v in outline.items() if k != "speaker_profile"}],
        "details": [outline],
        "details-repair": [outline, {k: v for k, v in details.items() if k != "immutable_facts"}],
    }[failure_stage]

    def unknown(*args, trace_sink, **kwargs):
        calls.append(args)
        if len(calls) <= len(responses):
            return responses[len(calls) - 1]
        raise llm_runtime.LlmRuntimeError("unknown provider result", code="codex_cli_timeout", status_code=504)

    monkeypatch.setattr(llm_runtime, "complete_json", unknown)
    with pytest.raises(llm_runtime.LlmRuntimeError) as first:
        brief.analyze_localization_document(request, batch_journal=_journal(tmp_path))
    assert first.value.code == "codex_cli_timeout"
    with pytest.raises(llm_runtime.LlmRuntimeError) as replay:
        brief.analyze_localization_document(request, batch_journal=_journal(tmp_path))
    assert replay.value.code == "development_llm_candidate_unavailable"
    assert len(calls) == len(responses) + 1


def test_brief_changed_actual_prompt_does_not_reuse_previous_candidate(tmp_path, monkeypatch, request_and_candidate):
    request, candidate = request_and_candidate
    calls = []
    outline, details = staged_candidates(candidate)
    _fake_provider(monkeypatch, [outline, details, details], calls, tmp_path)
    brief.analyze_localization_document(request, batch_journal=_journal(tmp_path))
    monkeypatch.setattr(stages, "DETAILS_PROMPT", stages.DETAILS_PROMPT + "\n只补充本次明确的额外指令。")
    brief.analyze_localization_document(request, batch_journal=_journal(tmp_path))
    assert len(calls) == 3
    records = _records(tmp_path)
    assert len([r for r in records if r["batch_id"] == "brief-outline"]) == 1
    assert len({r["input_fingerprint"] for r in records if r["batch_id"] == "brief-details-chunk_0001"}) == 2
