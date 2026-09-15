from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import localization_spoken_script as spoken
from app.domains.video_localization.development_llm_batches import DevelopmentLlmBatchReplay
from app.services import llm_runtime
from app.services.localization_ai_policy import LocalizationAiPhaseRoute
from tests.test_video_localization_localization_spoken_script import _source_and_brief, _script_request


@pytest.fixture
def request_data(monkeypatch):
    profile = llm_runtime.ResolvedProfile(
        profile_id="profile", protocol="openai_compatible", base_url="http://localhost:9000/v1",
        model_id="model", reasoning_effort="low",
    )
    monkeypatch.setattr(llm_runtime, "resolve_profile", lambda *a, **k: profile)
    source, brief = _source_and_brief()
    cue = source.input.cues[0]
    source = source.model_copy(update={"input": source.input.model_copy(update={"cues": [
        cue, cue.model_copy(update={"cue_id": "cue_0002", "text": "Goodbye world."}),
    ]})})
    section = brief.content.structure[0]
    brief = brief.model_copy(update={"content": brief.content.model_copy(update={"structure": [
        section, section.model_copy(update={"section_id": "section_0002", "source_cue_ids": ["cue_0002"]}),
    ]})})
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation", profile_id="profile", model_id="model",
        reasoning_effort="low", output_format="markdown", prompt_strategy="adaptive",
    )
    return _script_request(source, brief, route)


def _journal(root, execution="op1", prefix="generation"):
    return DevelopmentLlmBatchReplay(root=root, project_id="project", development_session_id="session",
        step_id_prefix=prefix, retry_rejected_execution_id=execution)


def _trace(sink, *, code=None):
    sink(llm_runtime.LlmCompletionTrace(
        profile_id="profile", model_id="model", provider_host="local", request_chars=10,
        request_body_bytes=20, max_tokens=4500, timeout_seconds=600, reasoning_effort_requested="low",
        reasoning_control_applied=True, duration_ms=20, finish_reason="stop" if code is None else None,
        error_code=code, prompt_tokens=10, completion_tokens=20,
    ))


def _raw(payload):
    return {"chunk_id": payload["chunk_id"],
            "suggested_title": "测试" if payload["chunk_id"] == "chunk_0001" else None,
            "paragraphs": ["这段正文完整。"]}


def _records(root):
    return [json.loads(path.read_text())["result"] for path in root.rglob("checkpoint.json")]


def test_second_chunk_refusal_resumes_only_refused_chunk_then_zero_calls(request_data, tmp_path, monkeypatch):
    calls, checkpoints = [], []

    def complete(prompt, payload, *, trace_sink, **kwargs):
        chunk = payload["chunk_id"]
        calls.append(chunk)
        assert any(row["status"] == "prepared" for row in _records(tmp_path))
        refused = calls == ["chunk_0001", "chunk_0002"]
        _trace(trace_sink, code="llm_rate_limited" if refused else None)
        if refused:
            raise llm_runtime.LlmRuntimeError("quota", code="llm_rate_limited", status_code=429)
        return _raw(payload)

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    with pytest.raises(llm_runtime.LlmRuntimeError):
        spoken.generate_localization_spoken_script(request_data, batch_journal=_journal(tmp_path),
            on_generation_checkpoint=checkpoints.append)
    assert len(checkpoints[-1].completed_chunks) == 1
    with pytest.raises(llm_runtime.LlmRuntimeError):
        spoken.generate_localization_spoken_script(request_data, batch_journal=_journal(tmp_path),
            resume_checkpoint=checkpoints[-1])
    assert calls == ["chunk_0001", "chunk_0002"]
    result = spoken.generate_localization_spoken_script(request_data, batch_journal=_journal(tmp_path, "op2"),
        resume_checkpoint=checkpoints[-1], on_generation_checkpoint=checkpoints.append)
    assert calls == ["chunk_0001", "chunk_0002", "chunk_0002"]
    assert len(result.llm_calls) == 3
    assert sum(call.error_code == "llm_rate_limited" for call in result.llm_calls) == 1
    from_chunks = spoken.generate_localization_spoken_script(request_data, batch_journal=_journal(tmp_path, "op3"),
        resume_checkpoint=checkpoints[-1])
    from_journal = spoken.generate_localization_spoken_script(request_data, batch_journal=_journal(tmp_path, "op3"))
    assert from_chunks.llm_calls == from_journal.llm_calls == result.llm_calls
    assert len(calls) == 3


def test_local_parser_failure_reuses_durable_raw_without_model(request_data, tmp_path, monkeypatch):
    calls = []

    def complete(prompt, payload, *, trace_sink, **kwargs):
        calls.append(payload["chunk_id"])
        _trace(trace_sink)
        return _raw(payload)

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    validator = spoken._validate_spoken_script_chunk_output
    monkeypatch.setattr(spoken, "_validate_spoken_script_chunk_output",
        lambda raw: (_ for _ in ()).throw(ValueError("local parser fault")))
    with pytest.raises(ValueError, match="local parser fault"):
        spoken.generate_localization_spoken_script(request_data, batch_journal=_journal(tmp_path))
    assert _records(tmp_path)[0]["status"] == "validation_failed"
    assert _records(tmp_path)[0]["raw_json_candidate"]["chunk_id"] == "chunk_0001"
    monkeypatch.setattr(spoken, "_validate_spoken_script_chunk_output", validator)
    result = spoken.generate_localization_spoken_script(request_data, batch_journal=_journal(tmp_path))
    assert calls == ["chunk_0001", "chunk_0002"]
    assert len(result.llm_calls) == 2


def test_json_repair_attempt_and_all_traces_survive_both_replay_paths(request_data, tmp_path, monkeypatch):
    calls, checkpoints = [], []

    def complete(prompt, payload, *, trace_sink, **kwargs):
        calls.append((dict(payload), kwargs))
        failed = len(calls) == 1
        _trace(trace_sink, code="llm_json_invalid" if failed else None)
        if failed:
            raise llm_runtime.LlmRuntimeError("json", code="llm_json_invalid", status_code=502)
        return _raw(payload)

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    first = spoken.generate_localization_spoken_script(request_data, batch_journal=_journal(tmp_path),
        on_generation_checkpoint=checkpoints.append)
    assert len(calls) == len(first.llm_calls) == 3
    assert "output_repair_instruction" in calls[1][0]
    assert (calls[0][1]["temperature"], calls[0][1]["max_tokens"]) == (0.2, 4500)
    assert (calls[1][1]["temperature"], calls[1][1]["max_tokens"]) == (0.0, 6000)
    for checkpoint in (None, checkpoints[-1]):
        replay = spoken.generate_localization_spoken_script(request_data, batch_journal=_journal(tmp_path),
            resume_checkpoint=checkpoint)
        assert replay.llm_calls == first.llm_calls
    assert len(calls) == 3


@pytest.mark.parametrize("failure", ["prepared", "timeout"])
def test_unknown_generation_attempt_never_automatically_repeats(request_data, tmp_path, monkeypatch, failure):
    class Interrupted(BaseException):
        pass

    def complete(*args, **kwargs):
        if failure == "prepared":
            raise Interrupted()
        raise llm_runtime.LlmRuntimeError("timeout", code="llm_timeout", status_code=504)

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    with pytest.raises((Interrupted, llm_runtime.LlmRuntimeError)):
        spoken.generate_localization_spoken_script(request_data, batch_journal=_journal(tmp_path))
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("unknown call repeated"))
    with pytest.raises(llm_runtime.LlmRuntimeError) as error:
        spoken.generate_localization_spoken_script(request_data, batch_journal=_journal(tmp_path, "op2"))
    assert error.value.code == "development_llm_candidate_unavailable"


def test_legacy_checkpoint_serialization_and_match_unchanged(request_data, tmp_path, monkeypatch):
    checkpoints = []

    def complete(prompt, payload, *, trace_sink, **kwargs):
        _trace(trace_sink)
        return _raw(payload)

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    baseline = spoken.generate_localization_spoken_script(request_data, on_generation_checkpoint=checkpoints.append)
    legacy = checkpoints[-1].model_dump(mode="json")
    for chunk in legacy["completed_chunks"]:
        chunk.pop("llm_calls")
    def canonical(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    restored = spoken.LocalizationSpokenScriptGenerationCheckpoint.model_validate(legacy)
    assert restored.model_dump(mode="json") == legacy
    assert canonical(restored.model_dump(mode="json")) == canonical(legacy)
    assert spoken.generation_checkpoint_matches(request_data, restored)
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("valid old chunk repeated"))
    result = spoken.generate_localization_spoken_script(request_data, resume_checkpoint=restored,
        batch_journal=_journal(tmp_path))
    assert result.content == baseline.content
    assert result.llm_calls == baseline.llm_calls
    assert _records(tmp_path) == []


@pytest.mark.parametrize("kind", ["fidelity", "naturalness"])
def test_initial_review_public_entry_reuses_existing_journal(request_data, tmp_path, monkeypatch, kind):
    calls = []

    def generation(prompt, payload, *, trace_sink, **kwargs):
        _trace(trace_sink)
        return _raw(payload)

    monkeypatch.setattr(llm_runtime, "complete_json", generation)
    script = spoken.generate_localization_spoken_script(request_data)

    def review(prompt, payload, *, trace_sink, **kwargs):
        calls.append(payload)
        _trace(trace_sink)
        return {"status": "passed", "summary_zh": "没有发现问题。", "issues": [],
                **({"impression": "original_chinese_transcript"} if kind == "naturalness" else {})}

    monkeypatch.setattr(llm_runtime, "complete_json", review)
    function = getattr(spoken, f"review_localization_spoken_script_{kind}")
    kwargs = {"source_lock": request_data.source_lock} if kind == "fidelity" else {
        "source_fingerprint": request_data.source_lock.source_fingerprint}
    route = request_data.route.model_copy(update={"phase": f"{kind}_review", "output_format": "json"})
    first = function(script=script, route=route, batch_journal=_journal(tmp_path, prefix=kind), **kwargs)
    second = function(script=script, route=route, batch_journal=_journal(tmp_path, prefix=kind), **kwargs)
    assert len(calls) == 1
    assert first.llm_calls == second.llm_calls
    assert first.result_fingerprint == second.result_fingerprint


def _review_entry(request, monkeypatch, entry, journal):
    def generation(prompt, payload, *, trace_sink, **kwargs):
        _trace(trace_sink)
        return _raw(payload)

    monkeypatch.setattr(llm_runtime, "complete_json", generation)
    script = spoken.generate_localization_spoken_script(request)
    kind = "naturalness" if "naturalness" in entry else "fidelity"
    route = request.route.model_copy(update={"phase": f"{kind}_review", "output_format": "json"})

    def invoke():
        if entry.startswith("closure"):
            return spoken._verify_finalization_issue_closure(
                script=script, issues=[], revised_section_ids=["section_0001"],
                route=route, round_index=1, review_kind=kind, batch_journal=journal,
            )
        if entry.startswith("final"):
            return spoken._review_finalization_regression(
                request=request, script=script, revised_section_ids=["section_0001"],
                route=route, round_index=1, review_kind=kind, batch_journal=journal,
            )
        function = getattr(spoken, f"review_localization_spoken_script_{kind}")
        kwargs = {"source_lock": request.source_lock} if kind == "fidelity" else {
            "source_fingerprint": request.source_lock.source_fingerprint}
        return function(script=script, route=route, batch_journal=journal, **kwargs)

    valid = {"status": "passed", "summary_zh": "没有发现问题。", "issues": [],
             **({"impression": "original_chinese_transcript", "naturalness": 5,
                 "persona": 5, "emotion": 5, "flow": 5} if kind == "naturalness" else {})}
    if entry.startswith("closure"):
        valid = {"status": "passed", "summary_zh": "没有发现问题。", "remaining_issue_ids": []}
    return invoke, valid


@pytest.mark.parametrize("entry", ["initial_fidelity", "initial_naturalness", "final_fidelity", "final_naturalness",
                                 "closure_fidelity", "closure_naturalness"])
def test_review_corrupt_journal_does_not_become_paid_schema_repair(request_data, tmp_path, monkeypatch, entry):
    invoke, valid = _review_entry(request_data, monkeypatch, entry, _journal(tmp_path))
    calls = []

    def complete(*args, trace_sink, **kwargs):
        calls.append(1)
        _trace(trace_sink)
        return valid

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    invoke()
    paths = list(tmp_path.rglob("checkpoint.json"))
    assert len(paths) == 1
    paths[0].write_text("broken checkpoint", encoding="utf-8")
    with pytest.raises(ValueError, match="不可读取"):
        invoke()
    assert calls == [1]
    assert list(tmp_path.rglob("checkpoint.json")) == paths


@pytest.mark.parametrize("entry", ["initial_fidelity", "final_naturalness", "closure_fidelity"])
@pytest.mark.parametrize("failure", ["schema", "json"])
def test_review_only_returned_content_enters_existing_repair(request_data, tmp_path, monkeypatch, entry, failure):
    invoke, valid = _review_entry(request_data, monkeypatch, entry, _journal(tmp_path))
    calls = []

    def complete(prompt, payload, *, trace_sink, **kwargs):
        calls.append(dict(payload))
        _trace(trace_sink)
        if len(calls) == 1:
            if failure == "json":
                raise llm_runtime.LlmRuntimeError("json", code="llm_json_invalid", status_code=502)
            return {**valid, "status": "invalid-status"} if "fidelity" in entry else {**valid, "impression": "invalid"}
        return valid

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    first = invoke()
    assert len(calls) == 2
    assert "validation_feedback" not in calls[0]
    assert "validation_feedback" in calls[1]
    second = invoke()
    assert len(calls) == 2
    assert first.llm_calls == second.llm_calls
    assert len(second.llm_calls) == 2
    assert {record["attempt"] for record in _records(tmp_path)} == {0, 1}


@pytest.mark.parametrize("entry", ["initial_fidelity", "final_naturalness", "closure_naturalness"])
@pytest.mark.parametrize("failure", ["prepared", "timeout"])
def test_review_unknown_journal_outcome_never_enters_repair(request_data, tmp_path, monkeypatch, entry, failure):
    invoke, _ = _review_entry(request_data, monkeypatch, entry, _journal(tmp_path))
    calls = []

    class Interrupted(BaseException):
        pass

    def complete(*args, **kwargs):
        calls.append(1)
        if failure == "prepared":
            raise Interrupted()
        raise llm_runtime.LlmRuntimeError("timeout", code="llm_timeout", status_code=504)

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    with pytest.raises((Interrupted, llm_runtime.LlmRuntimeError)):
        invoke()
    with pytest.raises(llm_runtime.LlmRuntimeError) as error:
        invoke()
    assert error.value.code == "development_llm_candidate_unavailable"
    assert calls == [1]
    assert {record["attempt"] for record in _records(tmp_path)} == {0}


def test_schema_retry_failure_is_bounded_and_replay_keeps_failed_candidates(request_data, tmp_path, monkeypatch):
    calls = []

    def complete(prompt, payload, *, trace_sink, **kwargs):
        calls.append(payload["chunk_id"])
        _trace(trace_sink)
        return {**_raw(payload), "unexpected_metadata": "not allowed"}

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    for execution in ["first", "replay"]:
        with pytest.raises(ValueError, match="JSON 字段不符合输出契约"):
            spoken.generate_localization_spoken_script(
                request_data, batch_journal=_journal(tmp_path, execution),
            )
    assert calls == ["chunk_0001", "chunk_0001"]
    records = _records(tmp_path)
    assert len(records) == 2
    assert all(record["status"] == "validation_failed" for record in records)
    assert all(record["raw_json_candidate"]["unexpected_metadata"] == "not allowed" for record in records)
