from __future__ import annotations

from copy import deepcopy
import json

import pytest

from app.domains.video_localization import localization_brief_stages as stages
from app.domains.video_localization import localization_document_brief as facade
from app.domains.video_localization.development_llm_batches import DevelopmentLlmBatchReplay
from app.domains.video_localization.localization_brief_contracts import (
    LocalizationDocumentBriefAdaptiveRules,
    LocalizationDocumentBriefSourcePayload,
)
from app.domains.video_localization.localization_source import LocalizationSourceGlossaryEntry
from app.services import llm_runtime
from tests.test_video_localization_localization_document_brief import _inputs


@pytest.fixture
def setup(monkeypatch):
    source, context, route = _inputs()
    request = facade.LocalizationDocumentBriefInput(
        source_operation_id="source-operation", context_operation_id="context-operation",
        source_lock=source, context_intent=context, route=route,
    )
    payload = LocalizationDocumentBriefSourcePayload.model_validate(facade._prompt_payload(request))
    profile = llm_runtime.ResolvedProfile(
        profile_id="profile", protocol="openai_compatible", base_url="http://localhost:9000/v1",
        model_id="model", reasoning_effort="low",
    )
    monkeypatch.setattr(llm_runtime, "resolve_profile", lambda *a, **k: profile)
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("unmocked model call"))
    return request, payload


def _outline(cue_ids):
    return {
        "purpose": "解释行动的条件与后果", "audience": "普通观众",
        "structure": [{"section_id": f"section_{index:04d}", "title": "叙述",
                       "function_zh": "说明关系", "source_cue_ids": [cue_id]}
                      for index, cue_id in enumerate(cue_ids, 1)],
        "speaker_profile": {"identity_zh": "讲述者", "expertise_zh": "只按原文",
                            "audience_distance_zh": "直接", "rhythm_zh": "自然",
                            "stable_traits_zh": ["平实"]},
        "emotional_arc": [{"section_id": f"section_{index:04d}", "emotion_zh": "平静",
                           "intensity": 2, "speech_acts": ["说明"]}
                          for index in range(1, len(cue_ids) + 1)],
        "cultural_adaptation_rules": ["不补事实"], "disfluency_policy_zh": "保留有意义停顿",
        "creative_strategy": {"content_type_zh": "叙述", "register_zh": "自然",
                              "audience_relationship_zh": "直接", "narrative_voice_zh": "平实",
                              "expression_strategy_zh": "按关系解释"},
    }


def _details(cue_id):
    return {
        "immutable_facts": [{"fact_id": "fact_0001", "statement_zh": "只在条件满足时行动。",
                             "source_cue_ids": [cue_id]}],
        "term_relations": [], "speech_qualification_candidates": [], "terminology": [],
        "semantic_attention": [], "evidence_questions": [],
    }


def _journal(root):
    return DevelopmentLlmBatchReplay(root=root, project_id="project",
                                     development_session_id="session", step_id_prefix="brief-stages")


def _records(root):
    return [result for path in root.rglob("checkpoint.json")
            if (result := json.loads(path.read_text())["result"]).get("contract_version")
            == "localization-development-llm-batch-v1"]


def _merge_records(root):
    return [result for path in root.rglob("checkpoint.json")
            if (result := json.loads(path.read_text())["result"]).get("contract_version")
            == "localization-brief-merge-validation-v1"]


def _provider(monkeypatch, responses, calls, root):
    def complete(prompt, payload, *, trace_sink, **kwargs):
        assert kwargs["timeout"] == 600
        assert kwargs["reasoning_effort"] == "low"
        assert kwargs["profile_id"] == "profile"
        assert any(record["status"] == "prepared" and record["call_input"]["user_payload"] == payload
                   for record in _records(root))
        calls.append((prompt, deepcopy(payload)))
        trace_sink(llm_runtime.LlmCompletionTrace(
            profile_id="profile", model_id="model", provider_host="local", request_chars=100,
            request_body_bytes=120, max_tokens=6000, timeout_seconds=600,
            reasoning_effort_requested="low", reasoning_control_applied=True,
            duration_ms=1, finish_reason="stop",
        ))
        response = responses[len(calls) - 1]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)
    monkeypatch.setattr(llm_runtime, "complete_json", complete)


def _run(setup, root, adaptive=None):
    request, payload = setup
    return stages.run_localization_brief_stages(
        request, source_payload=payload,
        adaptive_rules=adaptive or LocalizationDocumentBriefAdaptiveRules(paragraphs=[], rule_ids=[]),
        batch_journal=_journal(root),
    )


def test_staged_serial_execution_free_reentry_and_core_readonly_boundaries(setup, monkeypatch, tmp_path):
    request, payload = setup
    cue_ids = [cue.cue_id for cue in payload.source_cues]
    outline = _outline(cue_ids)
    responses = [outline, *[_details(cue_id) for cue_id in cue_ids]]
    calls = []
    _provider(monkeypatch, responses, calls, tmp_path)
    rules = LocalizationDocumentBriefAdaptiveRules(paragraphs=["测试共用规则"], rule_ids=["shared_rule"])
    first = _run(setup, tmp_path, rules)
    assert len(calls) == len(cue_ids) + 1
    assert first.dynamic_rule_ids == ["shared_rule"]
    assert calls[0][0] == "\n\n".join([stages.OUTLINE_PROMPT, *rules.paragraphs])
    assert all(prompt == "\n\n".join([
        stages.DETAILS_PROMPT, stages.DETAILS_ADAPTIVE_FIELD_MAPPING, *rules.paragraphs,
    ]) for prompt, _ in calls[1:])
    assert all("测试共用规则" in prompt for prompt, _ in calls)
    assert all(prompt.index(stages.DETAILS_ADAPTIVE_FIELD_MAPPING) < prompt.index("测试共用规则")
               for prompt, _ in calls[1:])
    assert calls[0][1] == payload.model_dump(mode="json")
    cores = [cue["cue_id"] for _, item in calls[1:] for cue in item["core_source_cues"]]
    assert cores == cue_ids
    for index, (_, item) in enumerate(calls[1:]):
        assert item["core_source_cues"] == [payload.source_cues[index].model_dump(mode="json")]
        assert "source_cues" not in item
        assert item["readonly_global_outline"] == outline
        assert [cue["cue_id"] for cue in item["readonly_before"]] == cue_ids[max(0, index - 3):index]
        assert [cue["cue_id"] for cue in item["readonly_after"]] == cue_ids[index + 1:index + 4]
    assert [fact.fact_id for fact in first.content.immutable_facts] == [f"fact_{i:04d}" for i in range(1, len(cue_ids) + 1)]
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("repeat model call"))
    second = _run(setup, tmp_path, rules)
    assert second == first
    assert len(second.llm_calls) == len(cue_ids) + 1
    assert all(record["status"] == "validation_passed" for record in _records(tmp_path))
    assert request.source_lock.source_fingerprint == first.manifest.source_fingerprint
    assert len(_merge_records(tmp_path)) == 1
    assert _merge_records(tmp_path)[0]["status"] == "passed"


def test_schema_repair_is_separate_stable_raw_checkpoint(setup, monkeypatch, tmp_path):
    cue_ids = [cue.cue_id for cue in setup[1].source_cues]
    invalid = _outline(cue_ids)
    del invalid["speaker_profile"]
    calls = []
    _provider(monkeypatch, [invalid, _outline(cue_ids), *[_details(c) for c in cue_ids]], calls, tmp_path)
    first = _run(setup, tmp_path)
    assert calls[1][1]["invalid_output"] == invalid
    assert calls[1][1]["original_input"] == setup[1].model_dump(mode="json")
    records = {record["batch_id"]: record for record in _records(tmp_path)}
    assert records["brief-outline"]["status"] == "validation_failed"
    assert records["brief-outline"]["raw_json_candidate"] == invalid
    assert records["brief-outline-repair"]["status"] == "validation_passed"
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("repeat repair"))
    assert _run(setup, tmp_path) == first


def test_second_schema_failure_does_not_retry_or_advance(setup, monkeypatch, tmp_path):
    calls = []
    _provider(monkeypatch, [{}, {}], calls, tmp_path)
    for _ in range(2):
        with pytest.raises(ValueError, match="字段修复后"):
            _run(setup, tmp_path)
    assert len(calls) == 2
    assert {record["batch_id"] for record in _records(tmp_path)} == {"brief-outline", "brief-outline-repair"}


def test_bad_reference_keeps_raw_and_revalidates_without_field_repair(setup, monkeypatch, tmp_path):
    cue_ids = [cue.cue_id for cue in setup[1].source_cues]
    original = stages._validate_details
    calls = []
    _provider(monkeypatch, [_outline(cue_ids), *[_details(c) for c in cue_ids]], calls, tmp_path)
    def broken(candidate, core):
        assert any(record["status"] == "response_received" for record in _records(tmp_path))
        raise ValueError("local validator bug")
    monkeypatch.setattr(stages, "_validate_details", broken)
    with pytest.raises(ValueError, match="local validator bug"):
        _run(setup, tmp_path)
    assert len(calls) == 2
    monkeypatch.setattr(stages, "_validate_details", original)
    result = _run(setup, tmp_path)
    assert len(calls) == len(cue_ids) + 1
    assert len(result.llm_calls) == len(calls)
    assert not any(record["batch_id"].endswith("repair") for record in _records(tmp_path))


@pytest.mark.parametrize("mutation", ["readonly", "duplicate_ref", "out_of_order", "duplicate_id", "unknown_question", "unknown_speech", "discontinuous_speech"])
def test_detail_local_reference_invariants(mutation):
    raw = _details("cue_0001")
    if mutation == "readonly":
        raw["immutable_facts"][0]["source_cue_ids"] = ["cue_0004"]
    elif mutation == "duplicate_ref":
        raw["immutable_facts"][0]["source_cue_ids"] = ["cue_0001", "cue_0001"]
    elif mutation == "out_of_order":
        raw["immutable_facts"][0]["source_cue_ids"] = ["cue_0002", "cue_0001"]
    elif mutation == "duplicate_id":
        raw["immutable_facts"].append(deepcopy(raw["immutable_facts"][0]))
    elif mutation == "unknown_question":
        raw["semantic_attention"] = [{"attention_id": "attention_0001", "source_meaning_zh": "含义",
            "expression_direction_zh": "方向", "avoid_misreading_zh": "避免", "confidence": "low",
            "source_cue_ids": ["cue_0001"], "evidence_question_id": "question_0001"}]
    elif mutation == "unknown_speech":
        raw["evidence_questions"] = [{"question_id": "question_0001", "kind": "visual",
            "question_zh": "核对", "reason_zh": "未知", "source_cue_ids": ["cue_0001"],
            "purpose": "speech_qualification", "speech_candidate_id": "speech_candidate_0001"}]
    else:
        raw["speech_qualification_candidates"] = [{"candidate_id": "speech_candidate_0001",
            "source_cue_ids": ["cue_0001", "cue_0003"], "reason_zh": "待核"}]
    with pytest.raises(ValueError):
        stages._validate_details(stages.LocalizationBriefDetails.model_validate(raw), ["cue_0001", "cue_0002", "cue_0003"])


def test_merge_rewrites_all_ids_and_references_without_collapsing_facts():
    outline = stages.LocalizationBriefOutline.model_validate(_outline(["cue_0001", "cue_0002"]))
    batches = []
    for cue_id in ("cue_0001", "cue_0002"):
        raw = _details(cue_id)
        raw["immutable_facts"].append({"fact_id": "fact_0002", "statement_zh": "另一个必要条件。", "source_cue_ids": [cue_id]})
        raw["speech_qualification_candidates"] = [{"candidate_id": "speech_candidate_0001", "source_cue_ids": [cue_id], "reason_zh": "待核"}]
        raw["evidence_questions"] = [{"question_id": "question_0001", "kind": "visual", "question_zh": "核对", "reason_zh": "未知",
            "source_cue_ids": [cue_id], "purpose": "speech_qualification", "speech_candidate_id": "speech_candidate_0001"}]
        raw["semantic_attention"] = [{"attention_id": "attention_0001", "source_meaning_zh": "含义", "expression_direction_zh": "方向",
            "avoid_misreading_zh": "避免", "confidence": "low", "source_cue_ids": [cue_id], "evidence_question_id": "question_0001"}]
        batch = stages.LocalizationBriefDetails.model_validate(raw)
        stages._validate_details(batch, [cue_id])
        batches.append(batch)
    content = stages._merge_details(outline, outline.structure, batches)
    assert len(content.immutable_facts) == 4
    assert content.evidence_questions[1].speech_candidate_id == "speech_candidate_0002"
    assert content.creative_strategy.semantic_attention[1].evidence_question_id == "question_0002"
    assert content.creative_strategy.semantic_attention[1].attention_id == "attention_0002"
    assert batches[1].evidence_questions[0].speech_candidate_id == "speech_candidate_0001"


def test_merge_105_facts_preserves_all_items():
    outline = stages.LocalizationBriefOutline.model_validate(_outline(["cue_0001"]))
    raw = _details("cue_0001")
    batch = stages.LocalizationBriefDetails.model_validate(raw)
    content = stages._merge_details(outline, outline.structure, [batch] * 105)
    assert len(content.immutable_facts) == 105
    assert content.immutable_facts[-1].fact_id == "fact_0105"
    assert batch.model_dump(mode="json") == raw


@pytest.mark.parametrize("mutation", ["first", "reverse", "duplicate", "multiple", "emotion"])
def test_outline_anchor_invariants(mutation):
    raw = _outline(["cue_0001", "cue_0002"])
    if mutation == "first":
        raw["structure"][0]["source_cue_ids"] = ["cue_0002"]
    elif mutation == "reverse":
        raw["structure"].reverse()
    elif mutation == "duplicate":
        raw["structure"][1]["source_cue_ids"] = ["cue_0001"]
    elif mutation == "multiple":
        raw["structure"][0]["source_cue_ids"] = ["cue_0001", "cue_0002"]
    else:
        raw["emotional_arc"].pop()
    with pytest.raises(ValueError):
        stages._expand_outline(stages.LocalizationBriefOutline.model_validate(raw), ["cue_0001", "cue_0002"])


@pytest.mark.parametrize("field,value", [("text", "changed source"), ("speaker_id", "other")])
def test_projection_mismatch_refused_before_model_call(setup, tmp_path, field, value):
    request, payload = setup
    modified = payload.model_copy(deep=True)
    setattr(modified.source_cues[0], field, value)
    with pytest.raises(ValueError, match="改变了源文"):
        _run((request, modified), tmp_path)
    assert not _records(tmp_path)


@pytest.mark.parametrize("field", ["target_language", "document_context", "delivery_intent", "glossary", "uncertainty"])
def test_projection_metadata_mismatch_refused_before_model_call(setup, tmp_path, field):
    request, payload = setup
    modified = payload.model_copy(deep=True)
    if field == "target_language":
        modified.target_language = "ja"
    elif field == "document_context":
        modified.document_context.summary = "不同背景"
    elif field == "delivery_intent":
        modified.delivery_intent.target_language = "ja"
    elif field == "glossary":
        if modified.glossary:
            modified.glossary.clear()
        else:
            modified.glossary.append(LocalizationSourceGlossaryEntry(
                glossary_id="other", source_text="term", localized_text="另一术语",
            ))
    else:
        request = request.model_copy(deep=True)
        request.source_lock.input.cues[0].quality_flags.append("asr_unresolved_text")
        modified.source_cues[0].quality_flags = []
    with pytest.raises(ValueError):
        _run((request, modified), tmp_path)
    assert not _records(tmp_path)


@pytest.mark.parametrize("failure_stage", ["outline", "detail"])
def test_unknown_provider_outcome_does_not_retry(setup, monkeypatch, tmp_path, failure_stage):
    cue_ids = [cue.cue_id for cue in setup[1].source_cues]
    timeout = llm_runtime.LlmRuntimeError("unknown outcome", code="test_timeout", status_code=504)
    responses = [timeout] if failure_stage == "outline" else [_outline(cue_ids), timeout]
    calls = []
    _provider(monkeypatch, responses, calls, tmp_path)
    expected_batch = "brief-outline" if failure_stage == "outline" else "brief-details-chunk_0001"
    with pytest.raises(llm_runtime.LlmRuntimeError) as first:
        _run(setup, tmp_path)
    assert first.value.code == "test_timeout"
    assert first.value.status_code == 504
    assert f"batch_id={expected_batch};attempt_id={expected_batch}" in str(first.value)
    with pytest.raises(llm_runtime.LlmRuntimeError):
        _run(setup, tmp_path)
    assert len(calls) == len(responses)
    assert not any(record["batch_id"].endswith("repair") for record in _records(tmp_path))


def test_merge_failure_is_separate_local_record_and_can_revalidate_free(setup, monkeypatch, tmp_path):
    cue_ids = [cue.cue_id for cue in setup[1].source_cues]
    calls = []
    _provider(monkeypatch, [_outline(cue_ids), *[_details(c) for c in cue_ids]], calls, tmp_path)
    original = stages._merge_details
    def broken(*args):
        raise ValueError("merge validator bug")
    monkeypatch.setattr(stages, "_merge_details", broken)
    with pytest.raises(ValueError, match="merge validator bug"):
        _run(setup, tmp_path)
    assert all(record["status"] == "validation_passed" for record in _records(tmp_path))
    assert _merge_records(tmp_path)[0]["status"] == "failed"
    assert "raw_json_candidate" not in _merge_records(tmp_path)[0]
    monkeypatch.setattr(stages, "_merge_details", original)
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("repeat model call"))
    result = _run(setup, tmp_path)
    assert _merge_records(tmp_path)[0]["status"] == "passed"
    assert len(result.llm_calls) == len(calls)


def test_actual_cap_failure_retains_all_raw_candidates_without_model_repair(setup, monkeypatch, tmp_path):
    cue_ids = [cue.cue_id for cue in setup[1].source_cues]
    details = [_details(cue_id) for cue_id in cue_ids]
    details[0]["immutable_facts"] = [
        {"fact_id": f"fact_{index:04d}", "statement_zh": "实" * 500,
         "source_cue_ids": [cue_ids[0]]} for index in range(1, 802)
    ]
    calls = []
    _provider(monkeypatch, [_outline(cue_ids), *details], calls, tmp_path)
    for _ in range(2):
        with pytest.raises(ValueError, match="未截断"):
            _run(setup, tmp_path)
    assert len(calls) == len(cue_ids) + 1
    records = {record["batch_id"]: record for record in _records(tmp_path)}
    assert len(records["brief-details-chunk_0001"]["raw_json_candidate"]["immutable_facts"]) == 801
    assert all(record["status"] == "validation_passed" for record in records.values())
    assert _merge_records(tmp_path)[0]["status"] == "failed"


def test_detail_schema_repair_replays_independently_from_valid_outline(setup, monkeypatch, tmp_path):
    cue_ids = [cue.cue_id for cue in setup[1].source_cues]
    invalid = _details(cue_ids[0])
    del invalid["terminology"]
    calls = []
    _provider(monkeypatch, [_outline(cue_ids), invalid, *[_details(c) for c in cue_ids]], calls, tmp_path)
    first = _run(setup, tmp_path)
    assert calls[2][1]["invalid_output"] == invalid
    assert "core_source_cues" in calls[2][1]["original_input"]
    records = {record["batch_id"]: record for record in _records(tmp_path)}
    assert records["brief-details-chunk_0001"]["status"] == "validation_failed"
    assert records["brief-details-chunk_0001-repair"]["status"] == "validation_passed"
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("repeat model call"))
    assert _run(setup, tmp_path) == first
