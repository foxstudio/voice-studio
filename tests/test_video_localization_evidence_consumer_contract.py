"""Fixed cross-content boundaries; no models or production data are used."""
from __future__ import annotations

import pytest

from tests.test_video_localization_localization_spoken_script import (
    _creation_context, _first_generation_chunk, _source_and_brief,
)
from app.domains.video_localization import (
    localization_creation_context as context,
    localization_generation_request as generation,
    localization_review_request as review,
    localization_spoken_script as spoken,
)
from app.services.localization_ai_policy import LocalizationAiPhaseRoute


@pytest.mark.parametrize("source_text", [
    "Use the original drawing before opening the editor.",
    "Wait here. I will return before dawn.",
])
def test_generation_and_review_preserve_source_uncertainty(source_text):
    source, brief = _source_and_brief()
    source.input.cues[0].text = source_text
    source.input.cues[0].quality_flags = [
        "asr_unresolved_text", "engine:fixture", "generated_by_asr",
    ]
    section, chunk = _first_generation_chunk(source, brief)
    generated = generation.build_localization_generation_request(
        source_lock=source, creation_context=_creation_context(source, brief),
        section=section, chunk=chunk, is_first_chunk=True,
    ).model_dump(mode="json")
    reviewed = review.build_localization_fidelity_review_request(
        source_lock=source, localized_full_text="测试台词。",
    ).model_payload()
    assert generated["editable_source_cues"][0]["quality_flags"] == ["asr_unresolved_text"]
    assert reviewed["source_uncertainties"] == [{
        "source_excerpt": source_text, "quality_flags": ["asr_unresolved_text"],
    }]
    assert reviewed["source_full_text"] == source_text


@pytest.mark.parametrize("observation", [
    "画面可读的工具名称为 Vector Desk。",
    "画面可读的角色姓名为 Captain Vale。",
])
def test_review_keeps_anchorless_evidence_and_original_source_order(monkeypatch, observation):
    source, _brief = _source_and_brief()
    second = source.input.cues[0].model_copy(update={"cue_id": "cue_0002", "text": "Then continue."})
    source.input.cues.append(second)
    constraint = context.LocalizationVerifiedEvidenceConstraint(
        question_id="question_0001", source_cue_ids=["cue_0002", "cue_0001"],
        constraint_zh=observation,
    )
    captured = {}

    def complete(_prompt, payload, *, trace_sink, **_kwargs):
        captured.update(payload)
        trace_sink(spoken.llm_runtime.LlmCompletionTrace(
            profile_id="fixture", model_id="fixture", provider_host="local",
            request_chars=100, request_body_bytes=100, max_tokens=6000,
            timeout_seconds=400, reasoning_effort_requested="low",
            reasoning_control_applied=True, duration_ms=1, finish_reason="stop",
        ))
        return {"status": "passed", "issues": [], "summary_zh": "固定测试。"}

    monkeypatch.setattr(spoken.llm_runtime, "complete_json", complete)
    route = LocalizationAiPhaseRoute(
        phase="fidelity_review", profile_id="fixture", model_id="fixture",
        reasoning_effort="low", output_format="json", prompt_strategy="adaptive",
    )
    script = spoken.LocalizationSpokenScriptResult.model_construct(
        result_fingerprint="a" * 64,
        content=spoken.LocalizationSpokenScriptContent(
            title="固定测试", sections=[spoken.LocalizationSpokenScriptSection(
                section_id="section_0001", heading="正文", paragraphs=["测试台词。"],
            )],
        ),
    )
    spoken.review_localization_spoken_script_fidelity(
        source_lock=source, script=script, route=route,
        verified_evidence_constraints=[constraint],
    )
    assert captured["confirmed_context"]["verified_evidence"] == [{
        "source_excerpt": "Hello world. Then continue.",
        "constraint_zh": context.render_verified_evidence_constraint(constraint),
    }]


def test_clean_source_keeps_minimal_review_payload():
    source, _brief = _source_and_brief()
    source.input.cues[0].quality_flags = ["generated_by_asr", "timing:aligned"]
    payload = review.build_localization_fidelity_review_request(
        source_lock=source, localized_full_text="你好。",
    ).model_payload()
    assert payload == {"source_full_text": "Hello world.", "localized_full_text": "你好。"}


def test_finalization_and_scoped_regression_keep_the_same_evidence(monkeypatch):
    source, brief = _source_and_brief()
    source.input.cues[0].quality_flags = ["asr_unresolved_text"]
    creation = _creation_context(source, brief)
    creation.content.verified_evidence_constraints = [context.LocalizationVerifiedEvidenceConstraint(
        question_id="question_0001", source_cue_ids=["cue_0001"],
        constraint_zh="画面可读标题为 Return。", source_kind="visual",
        observation_zh="只看到 Return 标题，未显示完整对白。", status="supported",
    )]
    request = spoken.LocalizationSpokenScriptFinalizationInput.model_construct(
        source_lock=source, document_brief=brief, creation_context=creation,
    )
    editing = spoken._source_section_for_finalization(request, "section_0001")
    assert editing["source_cues"][0]["quality_flags"] == ["asr_unresolved_text"]
    assert "Return" in editing["verified_evidence"][0]["constraint_zh"]
    captured = {}

    def capture(_prompt, payload, **_kwargs):
        captured.update(payload)
        return object()

    monkeypatch.setattr(spoken, "_run_structured_review", capture)
    script = spoken.LocalizationSpokenScriptResult.model_construct(
        result_fingerprint="a" * 64,
        content=spoken.LocalizationSpokenScriptContent(
            title="固定测试", sections=[spoken.LocalizationSpokenScriptSection(
                section_id="section_0001", heading="正文", paragraphs=["等我回来。"],
            )],
        ),
    )
    route = LocalizationAiPhaseRoute(
        phase="fidelity_review", profile_id="fixture", model_id="fixture",
        reasoning_effort="low", output_format="json", prompt_strategy="adaptive",
    )
    spoken._review_finalization_regression(
        request=request, script=script, revised_section_ids=["section_0001"],
        route=route, round_index=1, review_kind="fidelity",
    )
    assert captured["source_uncertainties"][0]["quality_flags"] == ["asr_unresolved_text"]
    assert captured["confirmed_context"]["verified_evidence"] == editing["verified_evidence"]
