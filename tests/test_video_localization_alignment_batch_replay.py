"""Fixed-provider replay through the shared alignment/display boundary entry."""
import json

import pytest

from tests.test_video_localization_development_llm_batches import fixed_profile, _trace  # noqa: F401
from tests.test_video_localization_localization_alignment_adjudication import _ambiguous_fixture
from tests.test_video_localization_localization_display_adjudication import _request, _KeywordEncoder, _word
from app.domains.video_localization import localization_alignment_adjudication as alignment
from app.domains.video_localization import localization_display_adjudication as display
from app.domains.video_localization.development_llm_batches import DevelopmentLlmBatchReplay
from app.services import llm_runtime


def _journal(root, execution="op1"):
    return DevelopmentLlmBatchReplay(root=root, project_id="project", development_session_id="session",
        step_id_prefix="alignment.llm_batch", retry_rejected_execution_id=execution)


def _records(root):
    return [json.loads(path.read_text())["result"] for path in root.rglob("checkpoint.json")]


def _choice(payload):
    return {"choices": [{"boundary_id": item["boundary_id"],
        "candidate_id": item["candidates"][-1]["candidate_id"], "reason_zh": "保留当前边界。"}
        for item in payload["boundary_packets"]]}


def _packets():
    return [alignment.LocalizationAlignmentBoundaryPacket(
        boundary_id=f"boundary_{i:04d}", left_block_id=f"localized_cue_{i:04d}",
        right_block_id=f"localized_cue_{i+1:04d}", left_target_text="左侧意义", right_target_text="右侧意义",
        candidates=[alignment.LocalizationAlignmentBoundaryCandidate(
            candidate_id=f"candidate_{i:04d}_{j:02d}", split_after_word_id=f"word_{i+j:04d}",
            left_tail_source="left content", right_head_source="right content", is_original=j == 2,
        ) for j in (1, 2)],
    ) for i in (1, 2)]


def _shared(root, execution="op1"):
    return alignment.adjudicate_alignment_boundary_packets(_packets(), route=_ambiguous_fixture().route,
        call_id_prefix="fixed-boundary", maximum_packets_per_call=1, batch_journal=_journal(root, execution))


def test_second_refusal_only_reissues_second_batch_and_preserves_traces(tmp_path, monkeypatch):
    calls = []
    def complete(prompt, payload, *, trace_sink, **kwargs):
        assert any(record["status"] == "prepared" for record in _records(tmp_path))
        calls.append(payload["boundary_packets"][0]["boundary_id"])
        _trace(trace_sink)
        if len(calls) == 2:
            raise llm_runtime.LlmRuntimeError("fixed refusal", code="codex_cli_rate_limited", status_code=429)
        return _choice(payload)
    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _shared(tmp_path)
    with pytest.raises(llm_runtime.LlmRuntimeError, match="显式开发执行"):
        _shared(tmp_path)
    choices, traces = _shared(tmp_path, "op2")
    assert calls == ["boundary_0001", "boundary_0002", "boundary_0002"]
    assert len(choices) == 2 and len(traces) == 3
    assert _shared(tmp_path, "op3") == (choices, traces)
    assert len(calls) == 3


@pytest.mark.parametrize("failure", ["prepared", "timeout"])
def test_unknown_second_batch_never_automatically_reissues(tmp_path, monkeypatch, failure):
    class Interrupted(BaseException):
        pass
    calls = []
    def complete(prompt, payload, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            if failure == "prepared":
                raise Interrupted()
            raise llm_runtime.LlmRuntimeError("fixed timeout", code="llm_timeout", status_code=504)
        return _choice(payload)
    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    with pytest.raises((Interrupted, llm_runtime.LlmRuntimeError)):
        _shared(tmp_path)
    with pytest.raises(llm_runtime.LlmRuntimeError, match="不会自动重复"):
        _shared(tmp_path, "op2")
    assert len(calls) == 2


def test_domain_parse_failure_preserves_raw_and_revalidates_without_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: calls.append(1) or {"choices": []})
    for execution in ("op1", "op2"):
        with pytest.raises(ValueError, match="全部待判断边界"):
            _shared(tmp_path, execution)
    record = _records(tmp_path)[0]
    assert record["raw_json_candidate"] == {"choices": []}
    assert record["status"] == "validation_failed"
    assert [item["status"] for item in record["validations"]] == ["failed", "failed"]
    assert calls == [1]


def test_validator_only_fix_uses_exact_saved_candidate(tmp_path, monkeypatch):
    calls = []
    def complete(prompt, payload, **kwargs):
        calls.append(1)
        return _choice(payload)
    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    parse = alignment._parse_choices
    def reject_locally(*args):
        # The actual provider response must already be durable at this boundary.
        assert _records(tmp_path)[0]["raw_json_candidate"]["choices"]
        raise ValueError("fixed local parser failure")
    monkeypatch.setattr(alignment, "_parse_choices", reject_locally)
    with pytest.raises(ValueError, match="local parser failure"):
        _shared(tmp_path)
    monkeypatch.setattr(alignment, "_parse_choices", parse)
    choices, _ = _shared(tmp_path, "op2")
    assert len(choices) == 2 and len(calls) == 2  # one call per distinct batch
    first = next(record for record in _records(tmp_path) if record["batch_id"].endswith("b001"))
    assert [item["status"] for item in first["validations"]] == ["failed", "passed"]
    assert first["status"] == "validation_passed"


def test_formal_boundary_journal_is_write_only(monkeypatch):
    from app.domains.video_localization import development_llm_batches as batches
    saved, calls = [], []
    journal = DevelopmentLlmBatchReplay(root=None, project_id="project", development_session_id="formal",
        step_id_prefix="alignment.llm_batch", checkpoint_writer=lambda step, record: saved.append(record))
    for name in ("load_development_checkpoint", "load_development_checkpoints_by_prefix"):
        monkeypatch.setattr(batches, name, lambda *a, **k: pytest.fail("formal journal read"))
    def complete(prompt, payload, **kwargs):
        calls.append(1)
        return _choice(payload)
    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    for _ in range(2):
        alignment.adjudicate_alignment_boundary_packets(_packets(), route=_ambiguous_fixture().route,
            call_id_prefix="fixed-boundary", batch_journal=journal)
    assert len(calls) == 2
    assert [item.status for item in saved] == ["prepared", "response_received", "validation_passed"] * 2


def test_alignment_public_entry_keeps_no_journal_behavior(tmp_path, monkeypatch):
    request = _ambiguous_fixture()
    calls = []
    def complete(prompt, payload, *, trace_sink, **kwargs):
        calls.append(payload)
        _trace(trace_sink)
        return _choice(payload)
    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    baseline = alignment.adjudicate_localization_alignment(request)
    first = alignment.adjudicate_localization_alignment(request, batch_journal=_journal(tmp_path))
    replay = alignment.adjudicate_localization_alignment(request, batch_journal=_journal(tmp_path, "op2"))
    assert first == replay == baseline
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize("use_checkpoint", [False, True])
def test_display_outer_batch_identity_survives_partial_checkpoint_resume(tmp_path, monkeypatch, use_checkpoint):
    request = _request()
    third = request.dual_tracks.display_cues[-1].model_copy(update={
        "cue_id": "localized_cue_0003", "text": "第三件事", "tts_text": "第三件事",
        "start_ms": 4000, "end_ms": 5000, "source_word_ids": ["word_0005"],
    })
    request = request.model_copy(update={
        "source_words": [*request.source_words, _word(5, "third")],
        "dual_tracks": request.dual_tracks.model_copy(update={"display_cues": [*request.dual_tracks.display_cues, third]}),
        "policy": display.LocalizationDisplayAdjudicationPolicy(maximum_packets_per_call=1),
    })
    packets = _packets()
    monkeypatch.setattr(display, "build_display_boundary_packets", lambda *a, **k: packets)
    calls, checkpoints = [], []
    def complete(prompt, payload, *, trace_sink, **kwargs):
        calls.append(payload["boundary_packets"][0]["boundary_id"])
        _trace(trace_sink)
        if len(calls) == 2:
            raise llm_runtime.LlmRuntimeError("fixed refusal", code="llm_rate_limited", status_code=429)
        return _choice(payload)
    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    with pytest.raises(llm_runtime.LlmRuntimeError):
        display.adjudicate_display_boundaries(request, encoder=_KeywordEncoder(),
            batch_journal=_journal(tmp_path), on_batch_checkpoint=checkpoints.append)
    checkpoint = checkpoints[-1]
    frozen = checkpoint.model_dump_json()
    assert checkpoint.completed_packet_ids == ["boundary_0001"]
    result = display.adjudicate_display_boundaries(request, encoder=_KeywordEncoder(),
        batch_journal=_journal(tmp_path, "op2"), resume_checkpoint=checkpoint if use_checkpoint else None)
    assert checkpoint.model_dump_json() == frozen
    replay = display.adjudicate_display_boundaries(request, encoder=_KeywordEncoder(), batch_journal=_journal(tmp_path, "op3"))
    assert result == replay and len(result.llm_calls) == 3
    assert calls == ["boundary_0001", "boundary_0002", "boundary_0002"]
    assert {item["batch_id"] for item in _records(tmp_path)} == {
        "localization-display-adjudication-001-b001", "localization-display-adjudication-002-b001"}
