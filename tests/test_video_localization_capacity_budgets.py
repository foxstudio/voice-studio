"""Storage capacity and logical generation admission are separate boundaries."""
import copy
import json
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import localization_brief_contracts as contracts  # noqa: E402
from app.domains.video_localization import localization_generation_request as generation  # noqa: E402
from app.domains.video_localization import localization_spoken_script as spoken  # noqa: E402
from app.services import llm_runtime  # noqa: E402
from app.services.localization_ai_policy import LocalizationAiPhaseRoute  # noqa: E402
from tests.test_video_localization_localization_spoken_script import _source_and_brief, _script_request  # noqa: E402
from tests.test_video_localization_localization_document_evidence import _brief  # noqa: E402


def _content_at_size(size, character):
    _, brief = _source_and_brief()
    raw = brief.content.model_dump(mode="json")
    raw["speaker_profile"]["stable_traits_zh"] = [""]
    base = len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode())
    count, remainder = divmod(size - base, len(character.encode()))
    raw["speaker_profile"]["stable_traits_zh"] = [character * count + "x" * remainder]
    return contracts.LocalizationDocumentBriefContent.model_validate(raw)


@pytest.mark.parametrize("character", ["a", "中"])
def test_storage_exact_utf8_boundary_and_one_byte_over(character):
    limit = contracts.MAX_BRIEF_CONTENT_UTF8_BYTES
    content = _content_at_size(limit, character)
    assert contracts.validate_localization_brief_storage_capacity(content) == limit
    oversized = _content_at_size(limit + 1, character)
    before = oversized.model_dump(mode="json")
    with pytest.raises(ValueError, match="未截断"):
        contracts.validate_localization_brief_storage_capacity(oversized)
    assert oversized.model_dump(mode="json") == before


def test_v2_read_does_not_acquire_v3_capacity_or_relabel_history():
    _, brief = _brief(with_question=False)
    raw = brief.model_dump(mode="json")
    raw.update(contract_version="localization-document-brief-v2", prompt_version="localization-document-brief-prompt-v13",
               content=_content_at_size(contracts.MAX_BRIEF_CONTENT_UTF8_BYTES + 1, "中").model_dump(mode="json"))
    restored = contracts.LocalizationDocumentBriefResult.model_validate(raw)
    assert restored.model_dump(mode="json") == raw
    raw["contract_version"] = "localization-document-brief-v3"
    with pytest.raises(ValidationError, match="存储"):
        contracts.LocalizationDocumentBriefResult.model_validate(raw)


def test_all_four_collections_preserve_105_items_under_storage_budget():
    _, brief = _source_and_brief()
    raw = brief.content.model_dump(mode="json")
    raw["immutable_facts"] = [{"fact_id": f"fact_{i:04d}", "statement_zh": f"独立事实{i}", "source_cue_ids": ["cue_0001"]} for i in range(1, 106)]
    raw["term_relations"] = [{"term": f"实体{i}", "relation_zh": "独立关系", "source_cue_ids": ["cue_0001"]} for i in range(105)]
    raw["creative_strategy"]["terminology"] = [{"source_term": f"term{i}", "meaning_zh": "含义", "preferred_target_term": "术语", "source_cue_ids": ["cue_0001"], "confidence": "high"} for i in range(105)]
    raw["creative_strategy"]["semantic_attention"] = [{"attention_id": f"attention_{i:04d}", "source_meaning_zh": "含义", "expression_direction_zh": "方向", "avoid_misreading_zh": "避免", "source_cue_ids": ["cue_0001"], "confidence": "high"} for i in range(1, 106)]
    content = contracts.LocalizationDocumentBriefContent.model_validate(raw)
    assert len(content.immutable_facts) == len(content.term_relations) == len(content.creative_strategy.terminology) == len(content.creative_strategy.semantic_attention) == 105
    assert contracts.validate_localization_brief_storage_capacity(content) < contracts.MAX_BRIEF_CONTENT_UTF8_BYTES
    invalid = copy.deepcopy(raw)
    invalid["immutable_facts"][0]["statement_zh"] = "x" * 501
    with pytest.raises(ValidationError):
        contracts.LocalizationDocumentBriefContent.model_validate(invalid)


@pytest.mark.parametrize("character", ["a", "中"])
def test_request_exact_utf8_boundary_and_one_byte_over_is_nonmutating(character):
    payload = {"global_context": {"must_preserve": ["完整事实"]}, "readonly_context_before": ["只读"], "verified_evidence_constraints": ["证据"], "output_repair_instruction": "严格 JSON"}
    before = copy.deepcopy(payload)
    spare = generation.MAX_GENERATION_LOGICAL_REQUEST_UTF8_BYTES - len(json.dumps(payload, ensure_ascii=False).encode())
    count, remainder = divmod(spare, len(character.encode()))
    prompt = character * count + "x" * remainder
    assert generation.validate_localization_generation_request_capacity(prompt, payload) == generation.MAX_GENERATION_LOGICAL_REQUEST_UTF8_BYTES
    with pytest.raises(ValueError, match="未调用模型"):
        generation.validate_localization_generation_request_capacity(prompt + "x", payload)
    assert payload == before


def _request():
    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(phase="spoken_script_creation", profile_id="profile", model_id="model", reasoning_effort="low", output_format="markdown", prompt_strategy="adaptive")
    return _script_request(source, brief, route)


def test_generation_oversized_global_facts_make_zero_provider_calls(monkeypatch):
    request = _request()
    fact = request.creation_context.content.document_brief.immutable_facts[0]
    request.creation_context.content.document_brief.immutable_facts = [fact.model_copy(update={"fact_id": f"fact_{i:04d}", "statement_zh": f"{i:04d}" + "实" * 496}) for i in range(1, 181)]
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("oversized request reached provider"))
    with pytest.raises(ValueError, match="逻辑请求"):
        spoken.generate_localization_spoken_script(request)


def test_repair_added_fields_are_preflighted_before_second_provider_call(monkeypatch):
    request = _request()
    calls = []

    def invalid_json(prompt, payload, **kwargs):
        calls.append(copy.deepcopy(payload))
        # The first attempt fit exactly; adding repair text must fail admission.
        size = generation.validate_localization_generation_request_capacity(prompt, payload)
        monkeypatch.setattr(generation, "MAX_GENERATION_LOGICAL_REQUEST_UTF8_BYTES", size)
        raise llm_runtime.LlmRuntimeError("invalid", code="llm_json_invalid", status_code=502)

    monkeypatch.setattr(llm_runtime, "complete_json", invalid_json)
    with pytest.raises(ValueError, match="逻辑请求"):
        spoken.generate_localization_spoken_script(request)
    assert len(calls) == 1
    assert "output_repair_instruction" not in calls[0]
