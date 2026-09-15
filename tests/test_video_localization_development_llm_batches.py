from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_video_localization_localization_spoken_script import (
    _source_and_brief,
    _creation_context,
    _script_request,
)
from app.domains.video_localization import development_llm_batches as batches
from app.domains.video_localization import localization_spoken_script as spoken
from app.services import llm_runtime
from app.services.localization_ai_policy import LocalizationAiPhaseRoute


@pytest.fixture(autouse=True)
def fixed_profile(monkeypatch):
    profile = llm_runtime.ResolvedProfile(
        profile_id="profile",
        protocol="openai_compatible",
        base_url="http://localhost:9000/v1",
        model_id="model",
        reasoning_effort="low",
    )
    monkeypatch.setattr(llm_runtime, "resolve_profile", lambda *a, **k: profile)
    return profile


def _journal(root, session="session", execution_id=None, unknown_step_id=None):
    return batches.DevelopmentLlmBatchReplay(
        root=root, project_id="project", development_session_id=session, step_id_prefix="finalize.llm_batch",
        retry_rejected_execution_id=execution_id,
        retry_unknown_batch_step_id=unknown_step_id,
    )


def _attempt(journal, batch="section_1", attempt=0):
    return journal.attempt(
        batch_id=batch,
        attempt=attempt,
        model_id="model",
        call_id=f"{batch}-{attempt}",
        purpose="localization_spoken_script_finalization",
        round_index=1,
    )


def _call(attempt, prompt="prompt", payload=None):
    return attempt.complete_json(
        prompt,
        payload or {"input": "actual"},
        profile_id="profile",
        temperature=0.1,
        max_tokens=5000,
        timeout=300,
        reasoning_effort="low",
    )


def _records(root):
    return [json.loads(path.read_text())["result"] for path in root.rglob("checkpoint.json")]


def _unknown_error(code="codex_cli_execution_failed"):
    return llm_runtime.LlmRuntimeError(
        "execution failed",
        code=code,
        status_code=504 if code == "codex_cli_timeout" else 502,
    )


@pytest.mark.parametrize(
    "unknown_code",
    ["codex_cli_execution_failed", "codex_cli_timeout"],
)
def test_exact_unknown_authorization_preserves_old_failure_and_replays_child_without_permission(
    tmp_path,
    monkeypatch,
    unknown_code,
):
    calls = []

    def first(prompt, payload, *, trace_sink, **kwargs):
        calls.append(payload["batch"])
        _trace(trace_sink)
        if payload["batch"] == "third":
            raise _unknown_error(unknown_code)
        return {"value": payload["batch"]}

    monkeypatch.setattr(llm_runtime, "complete_json", first)
    for batch in ("first", "second"):
        _call(_attempt(_journal(tmp_path, execution_id="op1"), batch=batch), payload={"batch": batch})
    failed = _attempt(_journal(tmp_path, execution_id="op1"), batch="third")
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _call(failed, payload={"batch": "third"})
    old = failed.checkpoint.model_dump(mode="json")
    old_step = failed.step_id
    assert "unknown_retry_authorized_step_id" not in old
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("unauthorized repeat"))
    for execution, authorization in [("op2", None), ("op1", old_step)]:
        with pytest.raises(llm_runtime.LlmRuntimeError):
            _call(_attempt(_journal(tmp_path, execution_id=execution, unknown_step_id=authorization), batch="third"),
                  payload={"batch": "third"})

    def success(prompt, payload, *, trace_sink, **kwargs):
        calls.append(payload["batch"])
        _trace(trace_sink)
        return {"value": "recovered"}

    monkeypatch.setattr(llm_runtime, "complete_json", success)
    retry = _attempt(_journal(tmp_path, execution_id="op2", unknown_step_id=old_step), batch="third")
    assert _call(retry, payload={"batch": "third"}) == {"value": "recovered"}
    assert retry.checkpoint.recovery_parent_step_id == old_step
    assert retry.checkpoint.unknown_retry_authorized_step_id == old_step
    assert retry.checkpoint.input_fingerprint == failed.checkpoint.input_fingerprint
    assert retry.checkpoint.recovery_execution_id == "op2"
    assert old in _records(tmp_path)
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("historical replay called provider"))
    for execution in (None, "op2", "op3"):
        for batch in ("first", "second", "third"):
            replay = _attempt(_journal(tmp_path, execution_id=execution), batch=batch)
            result = _call(replay, payload={"batch": batch})
            assert result == {"value": "recovered" if batch == "third" else batch}
            assert len(replay.reused_calls) == (2 if batch == "third" else 1)
    assert calls == ["first", "second", "third", "third"]


@pytest.mark.parametrize("failure", ["prepared", "timeout", "unknown_again"])
def test_unknown_permission_does_not_flow_to_new_unknown_failure(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: (_ for _ in ()).throw(_unknown_error()))
    failed = _attempt(_journal(tmp_path, execution_id="op1"))
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _call(failed)

    class Interrupted(BaseException):
        pass

    def fail(*args, **kwargs):
        if failure == "prepared":
            raise Interrupted()
        if failure == "timeout":
            raise llm_runtime.LlmRuntimeError("timeout", code="llm_timeout", status_code=504)
        raise _unknown_error()

    monkeypatch.setattr(llm_runtime, "complete_json", fail)
    with pytest.raises((Interrupted, llm_runtime.LlmRuntimeError)):
        _call(_attempt(_journal(tmp_path, execution_id="op2", unknown_step_id=failed.step_id)))
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("permission leaked"))
    for execution in ("op2", "op3"):
        with pytest.raises(llm_runtime.LlmRuntimeError) as error:
            _call(_attempt(_journal(tmp_path, execution_id=execution, unknown_step_id=failed.step_id)))
        assert error.value.code == "development_llm_candidate_unavailable"
    assert len(_records(tmp_path)) == 2


@pytest.mark.parametrize("change", ["input", "other_batch", "prepared", "timeout"])
def test_unknown_permission_cannot_cover_a_different_input_batch_or_failure(tmp_path, monkeypatch, change):
    class Interrupted(BaseException):
        pass

    def fail(*args, **kwargs):
        if change == "prepared":
            raise Interrupted()
        if change == "timeout":
            raise llm_runtime.LlmRuntimeError("timeout", code="llm_timeout", status_code=504)
        raise _unknown_error()

    monkeypatch.setattr(llm_runtime, "complete_json", fail)
    failed = _attempt(_journal(tmp_path, execution_id="op1"))
    with pytest.raises((Interrupted, llm_runtime.LlmRuntimeError)):
        _call(failed)
    if change == "other_batch":
        with pytest.raises(llm_runtime.LlmRuntimeError):
            _call(_attempt(_journal(tmp_path, execution_id="op1"), batch="other"))
    before = len(_records(tmp_path))
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("scope escaped"))
    with pytest.raises((ValueError, llm_runtime.LlmRuntimeError)):
        _call(_attempt(_journal(tmp_path, execution_id="op2", unknown_step_id=failed.step_id),
                       batch="other" if change == "other_batch" else "section_1"),
              prompt="changed" if change == "input" else "prompt")
    assert len(_records(tmp_path)) == before


def test_unknown_authorization_requires_exact_journal_scope_and_execution(tmp_path):
    other = "other.llm_batch." + "a" * 24 + ".a0." + "b" * 64
    for step, execution in [(other, "op2"), ("../unsafe", "op2"), ("", "op2"),
                            ("finalize.llm_batch." + "a" * 24 + ".a0." + "b" * 64, None)]:
        with pytest.raises(ValueError):
            _journal(tmp_path, execution_id=execution, unknown_step_id=step)


def test_formal_journal_never_enables_unknown_retry_permission(monkeypatch):
    saved = []
    journal = batches.DevelopmentLlmBatchReplay(
        root=None, project_id="project", development_session_id="formal", step_id_prefix="formal",
        checkpoint_writer=lambda step, record: saved.append(record),
        retry_rejected_execution_id="op2", retry_unknown_batch_step_id="not-a-development-step",
    )
    assert journal.retry_unknown_batch_step_id is None
    monkeypatch.setattr(batches, "load_development_checkpoint", lambda *a, **k: pytest.fail("formal read"))
    monkeypatch.setattr(batches, "load_development_checkpoints_by_prefix", lambda *a, **k: pytest.fail("formal scan"))
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: {"ok": True})
    assert _call(_attempt(journal)) == {"ok": True}
    assert all("unknown_retry_authorized_step_id" not in item.model_dump(mode="json") for item in saved)


def _image(data=b"fixed-image", media_type="image/jpeg"):
    return llm_runtime.LlmImageInput(data=data, media_type=media_type)


def _image_call(attempt, images=None):
    return attempt.complete_multimodal_json(
        "prompt", {"input": "actual"}, [_image()] if images is None else images,
        profile_id="profile", temperature=0.1, max_tokens=5000,
        timeout=300, reasoning_effort="low",
    )


def test_multimodal_candidate_durable_revalidated_and_traces_reused(tmp_path, monkeypatch):
    calls = []

    def complete(prompt, payload, *, images, trace_sink, **kwargs):
        assert _records(tmp_path)[0]["status"] == "prepared"
        assert images == (_image(),)
        calls.append(images)
        _trace(trace_sink)
        return {"answers": [{"question_id": "q1"}]}

    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", complete)
    first = _attempt(_journal(tmp_path))
    candidate = _image_call(first)
    assert _records(tmp_path)[0]["raw_json_candidate"] == candidate
    first.record_validation(validator_version="old", error=ValueError("local constraint"))
    second = _attempt(_journal(tmp_path))
    assert _image_call(second) == candidate
    second.record_validation(validator_version="fixed")
    assert len(calls) == len(second.reused_calls) == 1
    record = _records(tmp_path)[0]
    assert [row["status"] for row in record["validations"]] == ["failed", "passed"]
    assert record["call_input"]["image_inputs"][0]["size_bytes"] == len(b"fixed-image")
    assert "fixed-image" not in json.dumps(record)


@pytest.mark.parametrize("change", ["bytes", "mime", "order", "count"])
def test_multimodal_actual_image_identity_invalidates_candidate(tmp_path, monkeypatch, change):
    calls = []
    monkeypatch.setattr(llm_runtime, "complete_multimodal_json",
        lambda *a, **k: calls.append(k["images"]) or {"call": len(calls)})
    original = [_image(b"one"), _image(b"two")]
    _image_call(_attempt(_journal(tmp_path)), original)
    changed = {
        "bytes": [_image(b"changed"), original[1]],
        "mime": [_image(b"one", "image/png"), original[1]],
        "order": original[::-1], "count": original[:1],
    }[change]
    assert _image_call(_attempt(_journal(tmp_path)), changed) == {"call": 2}
    assert len(_records(tmp_path)) == 2


def test_multimodal_empty_images_not_text_and_legacy_text_dump_exact(tmp_path, monkeypatch):
    import hashlib

    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: {"text": True})
    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", lambda *a, **k: {"images": True})
    text_attempt = _attempt(_journal(tmp_path))
    _call(text_attempt)
    old_fields = {
        "system_prompt": "prompt", "user_payload": {"input": "actual"},
        "profile_id": "profile", "model_id": "model",
        "profile_configuration_fingerprint": batches.provider_configuration_fingerprint(
            llm_runtime.resolve_profile("profile")),
        "temperature": 0.1, "max_tokens": 5000, "timeout": 300.0,
        "reasoning_effort": "low",
    }
    assert text_attempt.checkpoint.call_input.model_dump(mode="json") == old_fields
    expected = hashlib.sha256(json.dumps(old_fields, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert text_attempt.checkpoint.input_fingerprint == expected
    assert batches.DevelopmentLlmCallInput.model_validate(old_fields).model_dump(mode="json") == old_fields
    image_attempt = _attempt(_journal(tmp_path))
    assert _image_call(image_attempt, []) == {"images": True}
    assert image_attempt.checkpoint.call_input.image_inputs == []
    assert image_attempt.checkpoint.input_fingerprint != expected


def test_multimodal_known_unsupported_error_replays_without_second_provider_call(tmp_path, monkeypatch):
    calls = []

    def unsupported(*a, trace_sink, **k):
        calls.append(1)
        _trace(trace_sink)
        raise llm_runtime.LlmRuntimeError("no images", code="llm_image_input_unsupported", status_code=415)

    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", unsupported)
    for index in range(2):
        attempt = _attempt(_journal(tmp_path))
        with pytest.raises(llm_runtime.LlmRuntimeError) as error:
            _image_call(attempt)
        assert (error.value.code, error.value.status_code) == ("llm_image_input_unsupported", 415)
        if index:
            assert len(attempt.reused_calls) == 1
    assert calls == [1]


@pytest.mark.parametrize("failure", ["prepared", "timeout", "unexpected"])
def test_multimodal_unknown_outcome_fails_closed(tmp_path, monkeypatch, failure):
    class Interrupted(BaseException):
        pass

    def fail(*a, **k):
        if failure == "prepared":
            raise Interrupted()
        if failure == "timeout":
            raise llm_runtime.LlmRuntimeError("timeout", code="llm_timeout", status_code=504)
        raise RuntimeError("unexpected")

    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", fail)
    with pytest.raises((Interrupted, llm_runtime.LlmRuntimeError, RuntimeError)):
        _image_call(_attempt(_journal(tmp_path)))
    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", lambda *a, **k: pytest.fail("duplicate call"))
    with pytest.raises(llm_runtime.LlmRuntimeError) as error:
        _image_call(_attempt(_journal(tmp_path, execution_id="new")))
    assert error.value.code == "development_llm_candidate_unavailable"


def test_multimodal_formal_writer_never_reads_development(tmp_path, monkeypatch):
    saved = []
    calls = []
    journal = batches.DevelopmentLlmBatchReplay(
        root=None, project_id="project", development_session_id="formal",
        step_id_prefix="evidence", checkpoint_writer=lambda step, record: saved.append(record),
    )
    for name in ("load_development_checkpoint", "load_development_checkpoints_by_prefix"):
        monkeypatch.setattr(batches, name, lambda *a, **k: pytest.fail("formal read"))
    monkeypatch.setattr(llm_runtime, "complete_multimodal_json",
        lambda *a, **k: calls.append(1) or {"answers": []})
    for _ in range(2):
        _image_call(_attempt(journal))
    assert len(calls) == 2
    assert [record.status for record in saved] == ["prepared", "response_received"] * 2


def test_candidate_is_durable_before_failed_validation_and_replays_after_restart(tmp_path, monkeypatch):
    calls = []

    def complete(*args, **kwargs):
        assert _records(tmp_path)[-1]["status"] == "prepared"
        calls.append(args)
        return {"edits": [{"replacement": "候选"}]}

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    first = _attempt(_journal(tmp_path))
    raw = _call(first)
    assert _records(tmp_path)[0]["raw_json_candidate"] == raw
    first.record_validation(validator_version="old", error=ValueError("boundary rejected"))
    resumed = _attempt(_journal(tmp_path))
    assert _call(resumed) == raw
    resumed.record_validation(validator_version="fixed")
    record = _records(tmp_path)[0]
    assert [item["status"] for item in record["validations"]] == ["failed", "passed"]
    assert record["status"] == "validation_passed"
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["prompt", "payload", "model", "session"])
def test_changed_real_call_input_or_session_does_not_reuse_candidate(tmp_path, monkeypatch, change):
    calls = []
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: calls.append(a) or {"value": len(calls)})
    _call(_attempt(_journal(tmp_path)))
    attempt = _attempt(_journal(tmp_path, session="other" if change == "session" else "session"))
    if change == "model":
        attempt.model_id = "new-model"
        monkeypatch.setattr(
            llm_runtime,
            "resolve_profile",
            lambda *a, **k: llm_runtime.ResolvedProfile(
                profile_id="profile",
                protocol="openai_compatible",
                base_url="http://localhost:9000/v1",
                model_id="new-model",
                reasoning_effort="low",
            ),
        )
    result = _call(
        attempt,
        prompt="new-prompt" if change == "prompt" else "prompt",
        payload={"input": "changed"} if change == "payload" else None,
    )
    assert result == {"value": 2}
    assert len(_records(tmp_path)) == 2


def test_prepared_without_response_does_not_automatically_repeat_paid_call(tmp_path, monkeypatch):
    class ProcessInterrupted(BaseException):
        pass

    def interrupted(*args, **kwargs):
        raise ProcessInterrupted()

    monkeypatch.setattr(llm_runtime, "complete_json", interrupted)
    with pytest.raises(ProcessInterrupted):
        _call(_attempt(_journal(tmp_path)))
    assert _records(tmp_path)[0]["status"] == "prepared"
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("duplicate paid call"))
    with pytest.raises(llm_runtime.LlmRuntimeError, match="不会自动重复"):
        _call(_attempt(_journal(tmp_path)))


def test_same_profile_id_configuration_change_invalidates_candidate(tmp_path, monkeypatch):
    from dataclasses import replace

    profile = llm_runtime.resolve_profile("profile")
    calls = []

    def complete(*args, resolved_profile, **kwargs):
        calls.append(resolved_profile)
        return {"call": len(calls)}

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    _call(_attempt(_journal(tmp_path)))
    changed = replace(profile, base_url="http://localhost:9100/v1", api_key="never-persist-this")
    monkeypatch.setattr(llm_runtime, "resolve_profile", lambda *a, **k: changed)
    assert _call(_attempt(_journal(tmp_path))) == {"call": 2}
    assert calls[-1] is changed
    assert "never-persist-this" not in json.dumps(_records(tmp_path))


def test_known_invalid_json_keeps_error_and_stable_retry_attempt(tmp_path, monkeypatch):
    calls = []

    def complete(*args, **kwargs):
        calls.append(args)
        raise llm_runtime.LlmRuntimeError("invalid JSON", code="llm_json_invalid", status_code=502)

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    for _ in range(2):
        with pytest.raises(llm_runtime.LlmRuntimeError) as exc:
            _call(_attempt(_journal(tmp_path)))
        assert exc.value.code == "llm_json_invalid"
    assert len(calls) == 1
    assert _records(tmp_path)[0]["raw_json_candidate"] is None
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: {"fixed": True})
    assert _call(_attempt(_journal(tmp_path), attempt=1), payload={"validation_feedback": "invalid JSON"}) == {
        "fixed": True
    }
    assert {r["attempt"] for r in _records(tmp_path)} == {0, 1}


def test_write_only_formal_diagnostics_never_reads(tmp_path, monkeypatch):
    saved = []
    calls = []
    journal = batches.DevelopmentLlmBatchReplay(
        root=None,
        project_id="project",
        development_session_id="formal-op",
        step_id_prefix="finalize.llm_batch",
        checkpoint_writer=lambda step, record: saved.append((step, record.model_dump(mode="json"))),
    )
    monkeypatch.setattr(batches, "load_development_checkpoint", lambda *a, **k: pytest.fail("formal read"))
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: calls.append(a) or {"ok": True})
    for _ in range(2):
        attempt = _attempt(journal)
        _call(attempt)
        attempt.record_validation(validator_version="v1")
    assert len(calls) == 2
    assert [r["status"] for _, r in saved] == ["prepared", "response_received", "validation_passed"] * 2
    assert list(tmp_path.iterdir()) == []


def test_successful_and_failed_batches_resume_independently_with_original_call_facts(tmp_path, monkeypatch):
    calls = []

    def complete(*args, trace_sink, **kwargs):
        calls.append(args)
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=10,
                request_body_bytes=20,
                max_tokens=5000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=1,
                finish_reason="stop",
            )
        )
        return {"batch": args[1]["batch"]}

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    for batch in ("success", "failed"):
        attempt = _attempt(_journal(tmp_path), batch=batch)
        _call(attempt, payload={"batch": batch})
        attempt.record_validation(validator_version="old", error=ValueError("invalid") if batch == "failed" else None)
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("successful or failed raw was lost"))
    for batch in ("success", "failed"):
        attempt = _attempt(_journal(tmp_path), batch=batch)
        assert _call(attempt, payload={"batch": batch}) == {"batch": batch}
        assert attempt.reused_calls[0].call_id == f"{batch}-0"
        attempt.record_validation(validator_version="fixed")
    assert len(calls) == 2


def test_corrupt_existing_checkpoint_does_not_authorize_new_call(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: {"ok": True})
    _call(_attempt(_journal(tmp_path)))
    path = next(tmp_path.rglob("checkpoint.json"))
    path.write_text("invalid envelope")
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("corrupt evidence caused new call"))
    with pytest.raises(ValueError, match="拒绝自动重复"):
        _call(_attempt(_journal(tmp_path)))


def _rate_limit(code="codex_cli_rate_limited", status=429):
    return llm_runtime.LlmRuntimeError("provider rejected", code=code, status_code=status)


def _reject(monkeypatch, calls, error=None):
    def complete(*args, **kwargs):
        calls.append(args)
        raise error or _rate_limit()
    monkeypatch.setattr(llm_runtime, "complete_json", complete)


@pytest.mark.parametrize("code", ["codex_cli_rate_limited", "llm_rate_limited"])
def test_rate_limit_explicit_recovery_preserves_failure_and_reuses_success(tmp_path, monkeypatch, code):
    calls = []
    _reject(monkeypatch, calls, _rate_limit(code))
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _call(_attempt(_journal(tmp_path)))
    old_path = next(tmp_path.rglob("checkpoint.json"))
    old_bytes = old_path.read_bytes()
    # This old shape deliberately contains none of the newly added metadata.
    assert "recovery_ordinal" not in json.loads(old_bytes)["result"]
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("default recovery"))
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _call(_attempt(_journal(tmp_path)))
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: calls.append(a) or {"ok": True})
    recovered = _attempt(_journal(tmp_path, execution_id="explicit-op-2"))
    assert _call(recovered) == {"ok": True}
    assert recovered.checkpoint.recovery_ordinal == 1
    assert recovered.checkpoint.attempt == 0
    assert recovered.checkpoint.recovery_execution_id == "explicit-op-2"
    assert old_path.read_bytes() == old_bytes
    assert {record["input_fingerprint"] for record in _records(tmp_path)} == {recovered.checkpoint.input_fingerprint}
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("repeat successful recovery"))
    for execution_id in (None, "explicit-op-2", "explicit-op-3"):
        assert _call(_attempt(_journal(tmp_path, execution_id=execution_id))) == {"ok": True}
    assert len(calls) == 2
    assert old_path.read_bytes() == old_bytes


def test_same_execution_cannot_retry_its_own_original_or_recovery_rejection(tmp_path, monkeypatch):
    calls = []
    _reject(monkeypatch, calls)
    for execution in ("op1", "op1", "op2", "op2", "op1"):
        with pytest.raises(llm_runtime.LlmRuntimeError):
            _call(_attempt(_journal(tmp_path, execution_id=execution)))
    assert len(calls) == 2
    assert sorted(record["recovery_ordinal"] for record in _records(tmp_path)) == [0, 1]
    # New explicit runs remain legitimate; no arbitrary lifetime retry cap.
    for execution in ("op3", "op4", "op5"):
        with pytest.raises(llm_runtime.LlmRuntimeError):
            _call(_attempt(_journal(tmp_path, execution_id=execution)))
    assert len(calls) == 5
    assert {record["attempt"] for record in _records(tmp_path)} == {0}
    assert len({record["batch_id"] for record in _records(tmp_path)}) == 1


@pytest.mark.parametrize("code,status", [
    ("codex_cli_timeout", 504), ("llm_timeout", 504),
    ("codex_cli_rate_limited", 503), ("unknown", 429),
    ("llm_provider_unavailable", 503), ("llm_json_invalid", 502),
])
def test_explicit_recovery_does_not_reissue_unknown_or_non_rejection(tmp_path, monkeypatch, code, status):
    calls = []
    _reject(monkeypatch, calls, _rate_limit(code, status))
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _call(_attempt(_journal(tmp_path, execution_id="op1")))
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("unknown result reissued"))
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _call(_attempt(_journal(tmp_path, execution_id="op2")))
    assert len(calls) == 1


@pytest.mark.parametrize("outcome", ["prepared", "timeout"])
def test_recovery_unknown_outcome_stops_later_explicit_executions(tmp_path, monkeypatch, outcome):
    calls = []
    _reject(monkeypatch, calls)
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _call(_attempt(_journal(tmp_path, execution_id="op1")))
    class Interrupted(BaseException):
        pass
    def interrupted(*args, **kwargs):
        calls.append(args)
        if outcome == "prepared":
            raise Interrupted()
        raise _rate_limit("codex_cli_timeout", 504)
    monkeypatch.setattr(llm_runtime, "complete_json", interrupted)
    with pytest.raises((Interrupted, llm_runtime.LlmRuntimeError)):
        _call(_attempt(_journal(tmp_path, execution_id="op2")))
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("unknown recovery reissued"))
    for execution in ("op2", "op3"):
        with pytest.raises(llm_runtime.LlmRuntimeError):
            _call(_attempt(_journal(tmp_path, execution_id=execution)))
    assert len(calls) == 2


@pytest.mark.parametrize("corruption", ["unreadable", "missing_parent", "wrong_parent", "wrong_ordinal"])
def test_recovery_chain_corruption_fails_closed(tmp_path, monkeypatch, corruption):
    calls = []
    _reject(monkeypatch, calls)
    for execution in ("op1", "op2", "op3"):
        with pytest.raises(llm_runtime.LlmRuntimeError):
            _call(_attempt(_journal(tmp_path, execution_id=execution)))
    paths = {json.loads(path.read_text())["result"]["recovery_ordinal"]: path
             for path in tmp_path.rglob("checkpoint.json")}
    if corruption == "unreadable":
        paths[1].write_text("broken checkpoint")
    elif corruption == "missing_parent":
        paths[1].unlink()
    else:
        # Use the public writer to create a valid envelope with invalid linkage.
        envelope = json.loads(paths[1].read_text())
        record = batches.DevelopmentLlmBatchCheckpoint.model_validate(envelope["result"])
        field, value = ("recovery_parent_step_id", "not-parent") if corruption == "wrong_parent" else ("recovery_ordinal", 9)
        changed = record.model_copy(update={field: value})
        _journal(tmp_path).writer(envelope["step_id"], changed)
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("corrupt chain reissued"))
    with pytest.raises(ValueError):
        _call(_attempt(_journal(tmp_path, execution_id="op4")))


def test_recovery_keeps_successful_other_batches_and_profile_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: {"existing": True})
    _call(_attempt(_journal(tmp_path), batch="completed"))
    calls = []
    _reject(monkeypatch, calls)
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _call(_attempt(_journal(tmp_path), batch="rejected"))
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: calls.append(a) or {"new": True})
    journal = _journal(tmp_path, execution_id="resume-op")
    assert _call(_attempt(journal, batch="completed")) == {"existing": True}
    assert _call(_attempt(journal, batch="rejected")) == {"new": True}
    assert len(calls) == 2
    # A genuinely changed input remains a separate identity, not a recovery.
    changed = _attempt(_journal(tmp_path, execution_id="new-input"), batch="rejected")
    assert _call(changed, payload={"changed": True}) == {"new": True}
    assert changed.checkpoint.recovery_ordinal == 0


def test_formal_write_only_never_enables_rejection_recovery(tmp_path, monkeypatch):
    saved = []
    journal = batches.DevelopmentLlmBatchReplay(
        root=None, project_id="project", development_session_id="formal", step_id_prefix="brief",
        checkpoint_writer=lambda step, record: saved.append(record), retry_rejected_execution_id="ignored",
    )
    assert journal.retry_rejected_execution_id is None
    monkeypatch.setattr(batches, "load_development_checkpoints_by_prefix", lambda *a, **k: pytest.fail("formal scan"))
    calls = []
    _reject(monkeypatch, calls)
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _call(_attempt(journal))
    assert len(calls) == 1
    assert saved[-1].recovery_ordinal is None


def test_recovery_trace_history_has_identical_first_run_and_replay_facts(tmp_path, monkeypatch):
    def traced(*args, trace_sink, **kwargs):
        trace_sink(llm_runtime.LlmCompletionTrace(
            profile_id="profile", model_id="model", provider_host="local", request_chars=10,
            request_body_bytes=20, max_tokens=5000, timeout_seconds=300,
            reasoning_effort_requested="low", reasoning_control_applied=True,
            duration_ms=1, finish_reason=None, error_code="codex_cli_rate_limited",
        ))
        raise _rate_limit()
    monkeypatch.setattr(llm_runtime, "complete_json", traced)
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _call(_attempt(_journal(tmp_path, execution_id="old")))
    captured = []
    def success(*args, trace_sink, **kwargs):
        trace = llm_runtime.LlmCompletionTrace(
            profile_id="profile", model_id="model", provider_host="local", request_chars=10,
            request_body_bytes=20, max_tokens=5000, timeout_seconds=300,
            reasoning_effort_requested="low", reasoning_control_applied=True,
            duration_ms=2, finish_reason="stop", total_tokens=12,
        )
        trace_sink(trace)
        return {"ok": True}
    monkeypatch.setattr(llm_runtime, "complete_json", success)
    recovered = _attempt(_journal(tmp_path, execution_id="new"))
    assert recovered.complete_json("prompt", {"input": "actual"}, profile_id="profile",
        temperature=0.1, max_tokens=5000, timeout=300, reasoning_effort="low",
        trace_sink=captured.append) == {"ok": True}
    original_records = [*recovered.reused_calls, *recovered.checkpoint.llm_calls]
    assert len(original_records) == 2
    assert original_records[0].error_code == "codex_cli_rate_limited"
    assert original_records[1].total_tokens == 12
    assert len(captured) == 1
    replay = _attempt(_journal(tmp_path, execution_id="later"))
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("trace replay called model"))
    assert _call(replay) == {"ok": True}
    assert replay.reused_calls == original_records


@pytest.mark.parametrize("change", ["model", "endpoint"])
def test_explicit_recovery_never_loads_different_profile_fingerprint(tmp_path, monkeypatch, change):
    from dataclasses import replace
    calls = []
    _reject(monkeypatch, calls)
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _call(_attempt(_journal(tmp_path, execution_id="old")))
    profile = llm_runtime.resolve_profile("profile")
    modified = replace(profile, **({"model_id": "another-model"} if change == "model"
                                   else {"base_url": "http://localhost:9900/v1"}))
    monkeypatch.setattr(llm_runtime, "resolve_profile", lambda *a, **k: modified)
    attempt = _attempt(_journal(tmp_path, execution_id="new"))
    if change == "model":
        attempt.model_id = "another-model"
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: {"changed": True})
    assert _call(attempt) == {"changed": True}
    assert attempt.checkpoint.recovery_ordinal == 0
    assert len({record["input_fingerprint"] for record in _records(tmp_path)}) == 2


def test_rate_limit_with_raw_candidate_is_reused_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: {"raw": "already present"})
    original = _attempt(_journal(tmp_path))
    _call(original)
    # Defensive invariant: a saved candidate takes priority over retry eligibility.
    original.checkpoint = original.checkpoint.model_copy(update={
        "status": "call_failed", "call_error_code": "codex_cli_rate_limited", "call_error_status_code": 429,
    })
    original.journal.writer(original.step_id, original.checkpoint)
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("existing raw retried"))
    assert _call(_attempt(_journal(tmp_path, execution_id="new"))) == {"raw": "already present"}


def test_recovery_sequence_metadata_is_bounded_but_has_no_three_attempt_cap():
    from pydantic import ValidationError
    raw = {"batch_id": "batch", "attempt": 0, "input_fingerprint": "a" * 64,
           "call_input": {"system_prompt": "prompt", "user_payload": {}, "profile_id": "profile",
               "model_id": "model", "profile_configuration_fingerprint": "b" * 64,
               "temperature": 0, "max_tokens": 10, "timeout": 10, "reasoning_effort": "low"},
           "status": "prepared"}
    for value in (-1, 2_147_483_648):
        with pytest.raises(ValidationError):
            batches.DevelopmentLlmBatchCheckpoint.model_validate({**raw, "recovery_ordinal": value})
    assert batches.DevelopmentLlmBatchCheckpoint.model_validate({**raw, "recovery_ordinal": 4}).recovery_ordinal == 4


def _request(monkeypatch):
    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
    )

    def generate(*args, trace_sink, **kwargs):
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=10,
                request_body_bytes=20,
                max_tokens=4500,
                timeout_seconds=600,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=1,
                finish_reason="stop",
            )
        )
        return {"chunk_id": "chunk_0001", "suggested_title": "测试", "paragraphs": ["这里把 Skill 打开。"]}

    monkeypatch.setattr(llm_runtime, "complete_json", generate)
    script = spoken.generate_localization_spoken_script(_script_request(source, brief, route))
    fidelity_route = route.model_copy(update={"phase": "fidelity_review", "output_format": "json"})
    naturalness_route = fidelity_route.model_copy(update={"phase": "naturalness_review"})
    review = spoken.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="fidelity",
        source_fingerprint=source.source_fingerprint,
        script_fingerprint=script.result_fingerprint,
        result_fingerprint="d" * 64,
        status="needs_revision",
        impression="not_applicable",
        scores={},
        issues=[
            spoken.LocalizationSpokenScriptReviewIssue(
                issue_id="fidelity_0001",
                severity="high",
                kind="term",
                excerpt="这里把 Skill 打开",
                reason_zh="工具关系错误。",
                required_change_zh="改成使用 Skill。",
            )
        ],
        summary_zh="一处错误",
        route=fidelity_route,
        llm_calls=[],
    )
    naturalness = review.model_copy(
        update={
            "review_kind": "naturalness",
            "result_fingerprint": "e" * 64,
            "status": "needs_revision",
            "impression": "localized_translation",
            "scores": {"naturalness": 3.7, "persona": 4.2, "emotion": 4.2, "flow": 4.2},
            "issues": [review.issues[0].model_copy(update={"issue_id": "naturalness_0001", "kind": "naturalness"})],
            "route": naturalness_route,
        }
    )
    return spoken.LocalizationSpokenScriptFinalizationInput(
        script_operation_id="script",
        fidelity_review_operation_id="fidelity",
        naturalness_review_operation_id="naturalness",
        source_lock=source,
        document_brief=brief,
        creation_context=_creation_context(source, brief),
        script=script,
        fidelity_review=review,
        naturalness_review=naturalness,
        route=route.model_copy(update={"phase": "spoken_script_finalization", "output_format": "json"}),
        post_fidelity_route=fidelity_route,
        post_naturalness_route=naturalness_route,
    )


def test_finalization_session_reentry_revalidates_failed_candidate_without_repeating_model(tmp_path, monkeypatch):
    request = _request(monkeypatch)
    calls = []

    def complete(prompt, payload, **kwargs):
        if "current_section" not in payload:
            raise RuntimeError("stop before downstream model")
        calls.append(payload)
        return {
            "section_id": "section_0001",
            "edits": [
                {"issue_id": payload["issues"][0]["issue_id"], "replacement": "这里使用 Skill。"},
            ],
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    original_parse = spoken._parse_section_edits

    def broken(*args, **kwargs):
        assert _records(tmp_path)[-1]["status"] == "response_received"
        raise ValueError("validator implementation defect")

    monkeypatch.setattr(spoken, "_parse_section_edits", broken)
    with pytest.raises(ValueError, match="validator implementation"):
        spoken.finalize_localization_spoken_script(request, batch_journal=_journal(tmp_path))
    assert _records(tmp_path)[0]["status"] == "validation_failed"
    monkeypatch.setattr(spoken, "_parse_section_edits", original_parse)
    monkeypatch.setattr(spoken, "FINALIZATION_BEHAVIOR_VERSION", "validator-fixed")
    saved_sections = []
    with pytest.raises(RuntimeError, match="stop before downstream"):
        spoken.finalize_localization_spoken_script(
            request, batch_journal=_journal(tmp_path), on_section_checkpoint=saved_sections.append
        )
    assert len(calls) == 1
    assert saved_sections[-1].working_content.sections[0].paragraphs == ["这里使用 Skill。"]
    section_record = next(r for r in _records(tmp_path) if r["batch_id"] == "r1-section_0001")
    assert section_record["status"] == "validation_passed"
    # A fresh invocation resumes a successful section and proceeds to downstream.
    with pytest.raises(llm_runtime.LlmRuntimeError, match="不会自动重复"):
        spoken.finalize_localization_spoken_script(
            request, batch_journal=_journal(tmp_path), resume_checkpoint=saved_sections[-1]
        )
    assert len(calls) == 1


def test_formal_finalization_without_journal_does_not_write_checkpoints(tmp_path, monkeypatch):
    request = _request(monkeypatch)
    monkeypatch.setattr(
        batches.LocalizationDevelopmentCheckpointWriter,
        "__call__",
        lambda *a, **k: pytest.fail("disabled journal write"),
    )
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stopped")))
    with pytest.raises(RuntimeError, match="stopped"):
        spoken.finalize_localization_spoken_script(request)
    assert list(tmp_path.iterdir()) == []


def _trace(trace_sink):
    trace_sink(
        llm_runtime.LlmCompletionTrace(
            profile_id="profile",
            model_id="model",
            provider_host="local",
            request_chars=10,
            request_body_bytes=20,
            max_tokens=5000,
            timeout_seconds=300,
            reasoning_effort_requested="low",
            reasoning_control_applied=True,
            duration_ms=1,
            finish_reason="stop",
        )
    )


@pytest.mark.parametrize("review_kind", ["fidelity", "naturalness"])
@pytest.mark.parametrize("review_stage", ["closure", "regression"])
def test_post_reviews_revalidate_returned_candidates_after_restart(
    tmp_path,
    monkeypatch,
    review_kind,
    review_stage,
):
    request = _request(monkeypatch)
    calls = []

    def complete(prompt, payload, *, trace_sink, **kwargs):
        calls.append(payload)
        _trace(trace_sink)
        if review_stage == "closure":
            return {"status": "passed", "remaining_issue_ids": [], "summary_zh": "修订完成。"}
        if review_kind == "fidelity":
            return {"status": "passed", "issues": [], "summary_zh": "原意无误。"}
        return {
            "impression": "original_chinese_transcript",
            "naturalness": 4.5,
            "persona": 4.5,
            "emotion": 4.5,
            "flow": 4.5,
            "issues": [],
            "summary_zh": "自然。",
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    route = request.post_fidelity_route if review_kind == "fidelity" else request.post_naturalness_route

    def execute():
        common = dict(
            script=request.script,
            revised_section_ids=["section_0001"],
            route=route,
            round_index=1,
            review_kind=review_kind,
            batch_journal=_journal(tmp_path),
        )
        if review_stage == "closure":
            return spoken._verify_finalization_issue_closure(issues=request.fidelity_review.issues, **common)
        return spoken._review_finalization_regression(request=request, **common)

    def invalid(*args, **kwargs):
        raise ValueError("local validation defect")

    if review_stage == "closure":
        owner, name = spoken.LocalizationSpokenScriptClosureVerdict, "model_validate"
    else:
        owner, name = spoken, "_build_review"
    original = getattr(owner, name)
    monkeypatch.setattr(owner, name, invalid)
    with pytest.raises(ValueError, match="local validation defect"):
        execute()
    assert len(calls) == 2  # Existing bounded retry policy, each attempt retained.
    assert {r["status"] for r in _records(tmp_path)} == {"validation_failed"}
    assert {r["attempt"] for r in _records(tmp_path)} == {0, 1}
    monkeypatch.setattr(owner, name, original)
    monkeypatch.setattr(spoken, "FINALIZATION_BEHAVIOR_VERSION", "local-validator-fixed")
    result = execute()
    assert result.status == "passed"
    assert len(calls) == 2
    assert len(result.llm_calls) == 1
    assert result.llm_calls[0].round_index == 1
    assert all(r["batch_id"] == f"r1-{review_stage}-{review_kind}" for r in _records(tmp_path))


def test_complete_finalization_reentry_reuses_all_five_calls(tmp_path, monkeypatch):
    request = _request(monkeypatch)
    calls = []

    def complete(prompt, payload, *, trace_sink, **kwargs):
        calls.append(payload)
        _trace(trace_sink)
        if "current_section" in payload:
            return {
                "section_id": "section_0001",
                "edits": [
                    {"issue_id": payload["issues"][0]["issue_id"], "replacement": "这里使用 Skill。"},
                ],
            }
        if "revised_sections" in payload:
            return {"status": "passed", "remaining_issue_ids": [], "summary_zh": "修订完成。"}
        if "source_full_text" in payload:
            return {"status": "passed", "issues": [], "summary_zh": "原意无误。"}
        return {
            "impression": "original_chinese_transcript",
            "naturalness": 4.5,
            "persona": 4.5,
            "emotion": 4.5,
            "flow": 4.5,
            "issues": [],
            "summary_zh": "自然。",
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    checkpoints = []
    first = spoken.finalize_localization_spoken_script(
        request, batch_journal=_journal(tmp_path), on_section_checkpoint=checkpoints.append
    )
    assert len(calls) == 5
    assert len(_records(tmp_path)) == 5
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("reentry repeated model call"))
    resumed = spoken.finalize_localization_spoken_script(
        request, batch_journal=_journal(tmp_path), resume_checkpoint=checkpoints[-1]
    )
    assert resumed.content == first.content
    assert resumed.quality_summary.status == first.quality_summary.status
    assert len({item.call_id for item in resumed.llm_calls}) == 5
