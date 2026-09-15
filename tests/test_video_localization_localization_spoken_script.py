from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    localization_creation_context,
    localization_context_intent,
    localization_document_brief,
    localization_generation_chunks,
    localization_generation_request,
    localization_review_request,
    localization_source,
    localization_spoken_script,
)


@pytest.mark.parametrize("replacement", ["先听我说——", "这件事……"])
def test_prepare_replacement_keeps_continuation_punctuation(replacement):
    assert (
        localization_spoken_script._prepare_replacement_sentence(
            "原句！",
            replacement,
            issue_id="naturalness_0001",
        )
        == replacement
    )


from app.services import llm_runtime  # noqa: E402
from app.services.localization_ai_policy import (  # noqa: E402
    LocalizationAiPhaseRoute,
)
from tests.test_video_localization_localization_context_intent import (  # noqa: E402
    _draft,
    _requirements,
)


def _source_and_brief():
    draft = _draft()
    source = localization_source.DEFAULT_LOCALIZATION_PIPELINE.lock_source(
        localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(draft)
    )
    context_request = localization_context_intent.build_localization_context_intent_input(
        draft,
        source_lock=source,
        upstream_operation_id="source_operation",
        requirements_profile=_requirements(),
        selection_source="project_default",
    )
    context = localization_context_intent.DEFAULT_LOCALIZATION_CONTEXT_INTENT_PIPELINE.lock(context_request)
    brief_route = LocalizationAiPhaseRoute(
        phase="document_understanding",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    content = localization_document_brief.LocalizationDocumentBriefContent(
        purpose="自然讲清楚一个简单事实。",
        audience="中文大众观众",
        structure=[
            localization_document_brief.LocalizationDocumentSection(
                section_id="section_0001",
                title="开场",
                function_zh="说明主题。",
                source_cue_ids=["cue_0001"],
            )
        ],
        speaker_profile=(
            localization_document_brief.LocalizationSpeakerProfile(
                identity_zh="普通讲述者",
                expertise_zh="了解主题",
                audience_distance_zh="平等交流",
                rhythm_zh="自然",
                stable_traits_zh=["直接"],
            )
        ),
        emotional_arc=[
            localization_document_brief.LocalizationEmotionalArcItem(
                section_id="section_0001",
                emotion_zh="平静",
                intensity=2,
                speech_acts=["说明"],
            )
        ],
        immutable_facts=[
            localization_document_brief.LocalizationImmutableFact(
                fact_id="fact_0001",
                statement_zh="说了 Hello world。",
                source_cue_ids=["cue_0001"],
            )
        ],
        cultural_adaptation_rules=["使用自然中文说明"],
        disfluency_policy_zh="没有需要保留的口语不流畅。",
    )
    brief = localization_document_brief.LocalizationDocumentBriefResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        context_intent_fingerprint=(context.context_intent_fingerprint),
        result_fingerprint="b" * 64,
        dynamic_rule_ids=[],
        content=content,
        route=brief_route,
        llm_calls=[],
        quality_summary=(
            localization_document_brief.LocalizationDocumentBriefQualitySummary(
                status="passed",
                section_count=1,
                fact_count=1,
                term_relation_count=0,
                evidence_question_count=0,
                source_reference_complete=True,
                model_call_count=1,
            )
        ),
    )
    return source, brief


def _creation_context(source, brief):
    context_request = localization_context_intent.build_localization_context_intent_input(
        _draft(),
        source_lock=source,
        upstream_operation_id="source_operation",
        requirements_profile=_requirements(),
        selection_source="project_default",
    )
    context = localization_context_intent.DEFAULT_LOCALIZATION_CONTEXT_INTENT_PIPELINE.lock(context_request)
    content = localization_creation_context.LocalizationCreationContextContent(
        delivery_intent=context.input.delivery_intent,
        document_brief=brief.content,
        creative_strategy=brief.content.creative_strategy,
    )
    return localization_creation_context.LocalizationCreationContextResult(
        source_fingerprint=source.source_fingerprint,
        context_intent_fingerprint=context.context_intent_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        evidence_fingerprint="e" * 64,
        result_fingerprint="f" * 64,
        document_brief=brief,
        content=content,
        quality_summary=(
            localization_creation_context.LocalizationCreationContextQualitySummary(
                status="passed",
                terminology_count=len(content.creative_strategy.terminology),
                semantic_attention_count=len(content.creative_strategy.semantic_attention),
                evidence_constraint_count=0,
                unresolved_evidence_count=0,
            )
        ),
    )


def _first_generation_chunk(source, brief):
    manifest = localization_generation_chunks.plan_localization_generation_chunks(
        source_fingerprint=source.source_fingerprint,
        cues=list(source.input.cues),
        sections=list(brief.content.structure),
        pauses=list(source.input.pauses),
    )
    return brief.content.structure[0], manifest.chunks[0]


def _script_request(source, brief, route):
    return localization_spoken_script.LocalizationSpokenScriptInput(
        source_operation_id="source_operation",
        creation_context_operation_id="creation_context_operation",
        source_lock=source,
        creation_context=_creation_context(source, brief),
        route=route,
    )


def test_spoken_script_generates_stable_chunk_with_lineage(
    monkeypatch,
):
    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
    )
    payloads = []

    def complete_json(prompt, payload, *, trace_sink, **kwargs):
        assert kwargs["profile_id"] == "profile"
        assert kwargs["reasoning_effort"] == "low"
        assert prompt == localization_generation_request.SYSTEM_PROMPT
        assert "把它当作 ASR 乱码省略" in prompt
        assert "官方英文写法原样保留" in prompt
        assert "不得改成中文音译" in prompt
        payloads.append(payload)
        assert list(payload) == [
            "contract_version",
            "chunk_id",
            "section_id",
            "section_goal",
            "is_first_chunk",
            "source_language",
            "target_language",
            "target_locale",
            "global_context",
            "editable_source_cues",
            "verified_evidence_constraints",
            "readonly_context_before",
            "readonly_context_after",
        ]
        assert payload["source_language"] == "en"
        assert payload["target_language"] == "zh-Hans"
        assert payload["target_locale"] == "zh-CN"
        assert payload["editable_source_cues"] == [
            {
                "source_cue_id": "cue_0001",
                "speaker_id": None,
                "text": "Hello world.",
                "quality_flags": [],
            }
        ]
        assert payload["verified_evidence_constraints"] == []
        assert list(payload["global_context"]) == [
            "content_purpose",
            "audience",
            "speaker_persona",
            "expression_profile",
            "must_preserve",
            "critical_semantic_attention",
        ]
        assert payload["global_context"]["content_purpose"] == ("自然讲清楚一个简单事实。")
        assert payload["global_context"]["audience"] == "中文大众观众"
        assert payload["global_context"]["must_preserve"] == ["说了 Hello world。"]
        assert "source_cue_count" not in payload
        assert "expected_sections" not in payload
        assert "locked_creation_context" not in payload
        assert "localization_target" not in payload
        assert "current_section_source" not in payload
        assert "previous_chinese_tail" not in payload
        assert "cue ID" in prompt
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=12_000,
                timeout_seconds=600,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "chunk_id": "chunk_0001",
            "suggested_title": "今天聊个简单的事",
            "paragraphs": ["大家好，今天聊个简单的事。"],
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )
    result = localization_spoken_script.generate_localization_spoken_script(_script_request(source, brief, route))

    assert len(payloads) == 1
    assert (
        localization_spoken_script.LocalizationSpokenScriptResult.model_validate(result.model_dump(mode="json"))
        == result
    )
    assert result.quality_summary.status == "passed"
    assert result.quality_summary.section_coverage_complete is True
    assert result.quality_summary.model_call_count == 1
    assert result.chunk_manifest.chunks[0].source_cue_ids == ["cue_0001"]
    assert result.chunk_lineage[0].paragraphs == ["大家好，今天聊个简单的事。"]
    assert [item.section_id for item in result.content.sections] == [
        "section_0001",
    ]
    projected = localization_spoken_script.project_localization_spoken_script_result(result)
    assert projected["label"] == "生成全文本土化初稿"
    assert projected["document"]["title"] == "全文本土化初稿"
    assert projected["sections"][1]["title"] == "输出边界"
    assert "本步骤不生成上屏字幕" in (projected["sections"][1]["items"][0]["text"])
    debug_sections = {section["title"]: section for section in projected["debug"]["sections"]}
    assert "实际输入提示词" in debug_sections
    assert "editable_source_cues" in (debug_sections["实际输入提示词"]["items"][0]["text"])
    assert "本次输入上下文" in debug_sections
    assert {fact["label"]: fact["value"] for fact in (debug_sections["本次输入上下文"]["items"][0]["facts"])}[
        "英文源内容"
    ] == "按稳定连续分块输入；不含时间戳"


def test_fixed_generation_prompt_is_domain_neutral():
    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="fixed",
    )
    prompt, rules = localization_spoken_script.build_spoken_script_prompt(_script_request(source, brief, route))

    assert rules == []
    assert prompt == localization_generation_request.SYSTEM_PROMPT
    assert localization_generation_request.GENERATION_PROMPT_VERSION == "localization-spoken-script-generation-v16"
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
        "632ff777f7569e3d9d8a581ca7abb37bbe3d6b491bb759b6731443deeca10308"
    )
    assert "prompt、mask、reveal" not in prompt
    assert "打开提示词" not in prompt
    assert "我靠、牛逼" not in prompt
    assert "只处理 editable_source_cues" in prompt
    assert "readonly_context_before/after 只用于衔接" in prompt
    assert "不得跨片段搬运内容" in prompt
    assert "不得新增原文没有的台词" in prompt
    assert "把它当作 ASR 乱码省略" in prompt
    assert "即时反应和转折的相对顺序" in prompt
    assert "说话人变化是硬边界" in prompt
    assert "必须保持原文句序" not in prompt
    assert "global_context" in prompt


def test_generation_request_preserves_speaker_identity_and_turn_boundary():
    source, brief = _source_and_brief()
    source = source.model_copy(
        update={
            "input": source.input.model_copy(
                update={"cues": [source.input.cues[0].model_copy(update={"speaker_id": "speaker_01"})]}
            )
        }
    )
    section, chunk = _first_generation_chunk(source, brief)

    request = localization_generation_request.build_localization_generation_request(
        source_lock=source,
        creation_context=_creation_context(source, brief),
        section=section,
        chunk=chunk,
        is_first_chunk=True,
    )

    assert request.contract_version == "localization-generation-request-v5"
    assert request.editable_source_cues[0].model_dump(mode="json") == {
        "source_cue_id": "cue_0001",
        "speaker_id": "speaker_01",
        "text": "Hello world.",
        "quality_flags": [],
    }
    assert "说话人变化是硬边界" in (localization_generation_request.SYSTEM_PROMPT)


def test_structured_review_retry_explains_invalid_json(monkeypatch):
    route = LocalizationAiPhaseRoute(
        phase="fidelity_review",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    payloads = []

    def complete_json(_prompt, payload, **_kwargs):
        payloads.append(payload)
        _kwargs["trace_sink"](
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=20,
                request_body_bytes=20,
                max_tokens=100,
                timeout_seconds=10,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=1,
                finish_reason="stop",
            )
        )
        if len(payloads) == 1:
            raise llm_runtime.LlmRuntimeError(
                "语言模型未返回有效 JSON",
                code="llm_json_invalid",
                status_code=502,
            )
        return {
            "status": "passed",
            "issues": [],
            "summary_zh": "原意一致。",
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )
    summary = localization_spoken_script.LocalizationSpokenScriptReviewRequestSummary(
        prompt_version="test-v1",
        request_fields=["source_full_text", "localized_full_text"],
        request_chars=20,
        source_chars=5,
        localized_chars=5,
        confirmed_evidence_count=0,
        locked_glossary_count=0,
    )

    result = localization_spoken_script._run_structured_review(
        "prompt",
        {"source_full_text": "hello", "localized_full_text": "你好"},
        review_kind="fidelity",
        source_fingerprint="s" * 64,
        script_fingerprint="t" * 64,
        route=route,
        max_tokens=100,
        timeout=10,
        request_summary=summary,
    )

    assert result.status == "passed"
    assert "validation_feedback" not in payloads[0]
    assert "不是可解析的 JSON 对象" in payloads[1]["validation_feedback"]


def test_generation_request_strips_internal_cue_references_from_context():
    source, brief = _source_and_brief()
    attention = localization_document_brief.LocalizationSemanticAttention(
        attention_id="attention_0001",
        source_meaning_zh="cue_0311 的代词指向当前工具。",
        expression_direction_zh="恢复原文主体。",
        avoid_misreading_zh="不要按 cue_0311 编号写进正文。",
        source_cue_ids=["cue_0001"],
        confidence="high",
    )
    strategy = brief.content.creative_strategy.model_copy(update={"semantic_attention": [attention]})
    context = _creation_context(source, brief)
    context = context.model_copy(update={"content": context.content.model_copy(update={"creative_strategy": strategy})})

    section, chunk = _first_generation_chunk(source, brief)
    payload = localization_generation_request.build_localization_generation_request(
        source_lock=source,
        creation_context=context,
        section=section,
        chunk=chunk,
        is_first_chunk=True,
    ).model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "cue_0311" not in serialized
    assert "原文中的代词指向当前工具" in serialized


def test_generation_request_adds_only_locked_target_glossary_to_existing_context():
    source, brief = _source_and_brief()
    source = source.model_copy(
        update={
            "input": source.input.model_copy(
                update={
                    "glossary": [
                        localization_source.LocalizationSourceGlossaryEntry(
                            glossary_id="glossary_0001",
                            source_text="EBITDA",
                            localized_text="息税折旧摊销前利润",
                            notes="财务报告中的锁定译法",
                        ),
                        localization_source.LocalizationSourceGlossaryEntry(
                            glossary_id="glossary_0002",
                            source_text="runway",
                            notes="尚未锁定目标语言写法",
                        ),
                    ]
                }
            )
        }
    )

    section, chunk = _first_generation_chunk(source, brief)
    payload = localization_generation_request.build_localization_generation_request(
        source_lock=source,
        creation_context=_creation_context(source, brief),
        section=section,
        chunk=chunk,
        is_first_chunk=True,
    ).model_dump(mode="json")

    assert list(payload) == [
        "contract_version",
        "chunk_id",
        "section_id",
        "section_goal",
        "is_first_chunk",
        "source_language",
        "target_language",
        "target_locale",
        "global_context",
        "editable_source_cues",
        "verified_evidence_constraints",
        "readonly_context_before",
        "readonly_context_after",
    ]
    assert payload["global_context"]["must_preserve"] == [
        "说了 Hello world。",
        "术语“EBITDA”使用“息税折旧摊销前利润”",
    ]
    assert "runway" not in json.dumps(payload, ensure_ascii=False)
    assert "财务报告中的锁定译法" not in json.dumps(
        payload,
        ensure_ascii=False,
    )


def test_generation_request_binds_only_current_chunk_evidence():
    source, brief = _source_and_brief()
    context = _creation_context(source, brief)
    context = context.model_copy(
        update={
            "content": context.content.model_copy(
                update={
                    "verified_evidence_constraints": [
                        localization_creation_context.LocalizationVerifiedEvidenceConstraint(
                            question_id="question_0001",
                            source_cue_ids=["cue_0001"],
                            constraint_zh="该句表达对身份的惊疑确认。",
                            anchored_target_text_zh="真的是你吗？",
                        ),
                        localization_creation_context.LocalizationVerifiedEvidenceConstraint(
                            question_id="question_0002",
                            source_cue_ids=["cue_9999"],
                            constraint_zh="另一分块的证据。",
                        ),
                    ]
                }
            )
        }
    )
    section, chunk = _first_generation_chunk(source, brief)

    payload = localization_generation_request.build_localization_generation_request(
        source_lock=source,
        creation_context=context,
        section=section,
        chunk=chunk,
        is_first_chunk=True,
    ).model_dump(mode="json")

    assert payload["editable_source_cues"] == [
        {
            "source_cue_id": "cue_0001",
            "speaker_id": None,
            "text": "Hello world.",
            "quality_flags": [],
        }
    ]
    assert payload["verified_evidence_constraints"] == [
        {
            "question_id": "question_0001",
            "source_cue_ids": ["cue_0001"],
            "constraint_zh": localization_creation_context.render_verified_evidence_constraint(
                context.content.verified_evidence_constraints[0]
            ),
        }
    ]


def test_spoken_script_does_not_silently_fall_back_to_section_calls(
    monkeypatch,
):
    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
    )
    call_count = 0

    def fail_once(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("模型上下文不足")

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        fail_once,
    )
    with pytest.raises(RuntimeError, match="上下文不足"):
        localization_spoken_script.generate_localization_spoken_script(_script_request(source, brief, route))
    assert call_count == 1


def test_spoken_script_retries_invalid_json_once_with_stricter_output_contract(
    monkeypatch,
):
    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
    )
    calls = []

    def complete_json(_prompt, payload, *, trace_sink, **kwargs):
        calls.append((payload, kwargs))
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=kwargs["max_tokens"],
                timeout_seconds=600,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        if len(calls) == 1:
            raise llm_runtime.LlmRuntimeError(
                "语言模型未返回有效 JSON",
                code="llm_json_invalid",
                status_code=502,
            )
        return {
            "chunk_id": "chunk_0001",
            "suggested_title": "标题",
            "paragraphs": ["这是一篇完整中文稿。"],
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )
    result = localization_spoken_script.generate_localization_spoken_script(_script_request(source, brief, route))

    assert len(calls) == 2
    assert "output_repair_instruction" not in calls[0][0]
    assert calls[0][1]["max_tokens"] == 4_500
    assert "output_repair_instruction" in calls[1][0]
    assert calls[1][1]["temperature"] == 0.0
    assert calls[1][1]["max_tokens"] == 6_000
    assert result.content.sections[0].paragraphs == ["这是一篇完整中文稿。"]


def test_spoken_script_replays_one_raw_response_without_another_paid_call(
    monkeypatch,
):
    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
    )
    runtime_call_count = 0

    def complete_json(*_args, trace_sink, **_kwargs):
        nonlocal runtime_call_count
        runtime_call_count += 1
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=12_000,
                timeout_seconds=600,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "chunk_id": "chunk_0001",
            "suggested_title": "标题",
            "paragraphs": ["这是一篇完整中文稿。"],
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )
    checkpoints = []
    request = _script_request(source, brief, route)
    first = localization_spoken_script.generate_localization_spoken_script(
        request,
        on_generation_checkpoint=checkpoints.append,
    )
    assert localization_spoken_script.generation_checkpoint_matches(
        request,
        checkpoints[0],
    )
    changed_route_request = request.model_copy(
        update={"route": request.route.model_copy(update={"reasoning_effort": "high"})}
    )
    assert not localization_spoken_script.generation_checkpoint_matches(
        changed_route_request,
        checkpoints[0],
    )

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("解析重放不应再次调用模型")

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        unexpected_call,
    )
    replay = localization_spoken_script.generate_localization_spoken_script(
        request,
        resume_checkpoint=checkpoints[0],
    )

    assert runtime_call_count == 1
    assert replay.content == first.content
    assert replay.result_fingerprint == first.result_fingerprint
    assert replay.quality_summary.model_call_count == 1

    unrelated_context = request.creation_context.model_copy(
        update={
            "result_fingerprint": "9" * 64,
            "content": request.creation_context.content.model_copy(
                update={
                    "verified_evidence_constraints": [
                        localization_creation_context.LocalizationVerifiedEvidenceConstraint(
                            question_id="question_0009",
                            source_cue_ids=["cue_9999"],
                            constraint_zh="只约束另一个分块。",
                        )
                    ]
                }
            ),
        }
    )
    unrelated_request = request.model_copy(update={"creation_context": unrelated_context})
    assert localization_spoken_script.generation_checkpoint_matches(
        unrelated_request,
        checkpoints[0],
    )
    unrelated_replay = localization_spoken_script.generate_localization_spoken_script(
        unrelated_request,
        resume_checkpoint=checkpoints[0],
    )
    assert unrelated_replay.content == first.content
    assert unrelated_replay.creation_context_fingerprint == "9" * 64
    assert runtime_call_count == 1


def test_spoken_script_accepts_only_semantically_empty_extra_json_fields(
    monkeypatch,
):
    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
    )

    def complete_json(*_args, trace_sink, **_kwargs):
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=4_500,
                timeout_seconds=600,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "chunk_id": "chunk_0001",
            "suggested_title": "标题",
            "paragraphs": ["这是一篇完整中文稿。"],
            "invalid_extra": "",
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )

    result = localization_spoken_script.generate_localization_spoken_script(_script_request(source, brief, route))

    assert result.content.sections[0].paragraphs == ["这是一篇完整中文稿。"]



@pytest.mark.parametrize("resume_invalid", [False, True])
def test_spoken_script_retries_schema_error_without_recalling_cached_batch(
    monkeypatch, resume_invalid,
):
    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
    )

    payloads = []
    def complete_json(*_args, trace_sink, **_kwargs):
        payloads.append(_args[1])
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=4_500,
                timeout_seconds=600,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "chunk_id": "chunk_0001",
            "suggested_title": "标题",
            "paragraphs": ["这是一篇完整中文稿。"],
            **({"unexpected_metadata": "nonempty"} if not resume_invalid and len(payloads) == 1 else {}),
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )

    request = _script_request(source, brief, route)
    checkpoints = []
    result = localization_spoken_script.generate_localization_spoken_script(
        request, on_generation_checkpoint=checkpoints.append,
    )
    if resume_invalid:
        checkpoint = checkpoints[-1]
        chunk = checkpoint.completed_chunks[0]
        raw = {**chunk.raw_output, "unexpected_metadata": "nonempty"}
        broken = chunk.model_copy(update={
            "raw_output": raw,
            "raw_output_fingerprint": localization_spoken_script._fingerprint(raw),
        })
        payloads.clear()
        result = localization_spoken_script.generate_localization_spoken_script(
            request, resume_checkpoint=checkpoint.model_copy(update={"completed_chunks": [broken]}),
        )
    assert len(payloads) == (1 if resume_invalid else 2)
    assert payloads[-1]["output_validation_errors"] == [
        {"field": "unexpected_metadata", "error": "extra_forbidden"},
    ]

    assert result.content.sections[0].paragraphs == ["这是一篇完整中文稿。"]


def test_markdown_document_parser_keeps_natural_paragraphs():
    content = localization_spoken_script.parse_spoken_script_text(
        "# 测试全文\n\n## 开场\n\n第一段有两句。第二句接着讲。\n\n第二段自然收住。\n\n## 结尾\n\n最后自然结束。",
        output_format="markdown",
        expected_section_ids=["section_0001", "section_0002"],
    )

    assert content.sections[0].heading == "开场"
    assert content.sections[0].paragraphs == [
        "第一段有两句。第二句接着讲。",
        "第二段自然收住。",
    ]


def test_markdown_document_parser_preserves_intro_before_first_heading():
    content = localization_spoken_script.parse_spoken_script_text(
        "# 测试全文\n\n"
        "这是完整开场。\n\n"
        "这里介绍人物和创作动机。\n\n"
        "## 第一级\n\n开始讲第一个案例。\n\n"
        "## 第二级\n\n继续讲第二个案例。",
        output_format="markdown",
        expected_section_ids=[
            "section_0001",
            "section_0002",
            "section_0003",
        ],
    )

    assert [item.heading for item in content.sections] == [
        "测试全文",
        "第一级",
        "第二级",
    ]
    assert [item.render_heading for item in content.sections] == [
        False,
        True,
        True,
    ]
    assert content.sections[0].paragraphs == [
        "这是完整开场。",
        "这里介绍人物和创作动机。",
    ]
    assert localization_spoken_script.script_markdown(content).startswith(
        "# 测试全文\n\n这是完整开场。\n\n这里介绍人物和创作动机。\n\n## 第一级"
    )


def test_markdown_document_parser_accepts_body_without_section_headings():
    source = "# 一段自然口述\n\n这里直接开始讲正文。\n\n全文不需要为了满足格式而硬拆章节。"
    content = localization_spoken_script.parse_spoken_script_text(
        source,
        output_format="markdown",
    )

    assert len(content.sections) == 1
    assert content.sections[0].render_heading is False
    assert content.sections[0].paragraphs == [
        "这里直接开始讲正文。",
        "全文不需要为了满足格式而硬拆章节。",
    ]
    assert localization_spoken_script.script_markdown(content) == source


def test_markdown_document_parser_accepts_natural_extra_headings():
    content = localization_spoken_script.parse_spoken_script_text(
        "# 测试全文\n\n## 开场\n\n先把重点说清楚。\n\n## 示例一\n\n第一个例子。\n\n## 收尾\n\n最后自然结束。",
        output_format="markdown",
        expected_section_ids=["section_0001", "section_0002"],
    )

    assert [item.section_id for item in content.sections] == [
        "section_0001",
        "section_0002",
        "section_0003",
    ]


def test_review_issue_locator_accepts_unambiguous_minor_wording_drift():
    content = localization_spoken_script.LocalizationSpokenScriptContent(
        title="测试",
        sections=[
            localization_spoken_script.LocalizationSpokenScriptSection(
                section_id="section_0001",
                heading="开场",
                paragraphs=["这才是最让我震撼的地方。接着往下讲。"],
            ),
            localization_spoken_script.LocalizationSpokenScriptSection(
                section_id="section_0002",
                heading="结尾",
                paragraphs=["最后自然收住。"],
            ),
        ],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="n1",
        severity="medium",
        excerpt="这就是最让我震撼的地方",
        reason_zh="略像成稿",
        required_change_zh="改得更口语",
    )

    located = localization_spoken_script._locate_review_issues(
        content,
        [issue],
    )

    assert list(located) == ["section_0001"]


def test_review_issue_locator_accepts_normalized_excerpt_inside_sentence():
    content = localization_spoken_script.LocalizationSpokenScriptContent(
        title="测试",
        sections=[
            localization_spoken_script.LocalizationSpokenScriptSection(
                section_id="section_0001",
                heading="开场",
                paragraphs=["开始之前先说一件事，那个 Skill 是免费的，链接就在简介里。你可以直接拿走。"],
            ),
            localization_spoken_script.LocalizationSpokenScriptSection(
                section_id="section_0002",
                heading="结尾",
                paragraphs=["最后自然收住。"],
            ),
        ],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="n1",
        severity="low",
        excerpt="那个 skill 是免费的，链接就在简介里",
        reason_zh="术语略密。",
        required_change_zh="换成更自然的中文说法。",
    )

    located = localization_spoken_script._locate_review_issues(
        content,
        [issue],
    )

    assert list(located) == ["section_0001"]
    paragraph_index, sentence = localization_spoken_script._locate_issue_sentence(
        content.sections[0],
        issue,
    )
    assert paragraph_index == 0
    assert sentence.startswith("开始之前先说一件事")


def test_finalization_prompt_does_not_hardcode_first_section_id():
    assert '"section_id":"section_0001"' not in localization_spoken_script.FINALIZATION_PROMPT
    assert "current_section.section_id" in (localization_spoken_script.FINALIZATION_PROMPT)


def test_naturalness_review_classifies_systemic_not_isolated_translation_trace():
    prompt = localization_spoken_script.NATURALNESS_REVIEW_PROMPT

    assert "全文占主导的感觉" in prompt
    assert "反复出现翻译式句法" in prompt
    assert "少量局部问题" in prompt
    assert "中文母语者在当前内容场景下自然表达" in prompt
    assert "不能因为内容正式、专业或克制" in prompt
    assert "中国创作者" not in prompt


def test_review_prompts_are_content_neutral_and_keep_internal_plans_out():
    fidelity = localization_spoken_script.FIDELITY_REVIEW_PROMPT
    naturalness = localization_spoken_script.NATURALNESS_REVIEW_PROMPT

    assert "开场钩子" not in fidelity
    assert "各章节" not in fidelity
    assert "全文简报" not in fidelity
    assert "cue" not in fidelity.lower()
    assert "document_brief" not in fidelity
    assert "cue" not in naturalness.lower()
    assert localization_review_request.FIDELITY_REVIEW_PROMPT_VERSION == "localization-fidelity-review-prompt-v10"
    assert localization_review_request.NATURALNESS_REVIEW_PROMPT_VERSION == "localization-naturalness-review-prompt-v4"
    assert "人物、实体、动作或结果之间的关系" in fidelity
    assert "工具关系" not in fidelity
    assert "自然表达进行必要的合并、拆分和换序" in fidelity
    assert "脱离原来的叙事位置" in fidelity
    assert "只能对应一个可独立替换的完整句子" in fidelity
    assert "必须原样返回目标句的 sentence_id" in fidelity
    assert "必须原样返回目标句的 sentence_id" in naturalness
    assert "同类问题如果" in fidelity
    assert "必须分别返回多条 issue" in fidelity
    assert "重新总结、补充建议、步骤、解释或结论" in fidelity
    assert "即使这些新增内容本身正确、自然" in fidelity
    assert "必须保持原文句序" not in fidelity


def test_fidelity_review_receives_verified_evidence_constraints(
    monkeypatch,
):
    source, brief = _source_and_brief()
    source = source.model_copy(
        update={
            "input": source.input.model_copy(
                update={
                    "glossary": [
                        localization_source.LocalizationSourceGlossaryEntry(
                            glossary_id="glossary_0001",
                            source_text="Seedance",
                            corrected_source_text="Seedance 2.0",
                            localized_text="Seedance 2.0",
                            notes="产品名不翻译",
                        )
                    ]
                }
            )
        }
    )
    route = LocalizationAiPhaseRoute(
        phase="fidelity_review",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    script = localization_spoken_script.LocalizationSpokenScriptResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        creation_context_fingerprint="c" * 64,
        result_fingerprint="d" * 64,
        content=localization_spoken_script.LocalizationSpokenScriptContent(
            title="测试",
            sections=[
                localization_spoken_script.LocalizationSpokenScriptSection(
                    section_id="section_0001",
                    heading="正文",
                    paragraphs=["我调用了画面确认过的功能。"],
                )
            ],
        ),
        route=route.model_copy(update={"phase": "spoken_script_creation"}),
        llm_calls=[],
        quality_summary=(
            localization_spoken_script.LocalizationSpokenScriptQualitySummary(
                status="passed",
                section_count=1,
                paragraph_count=1,
                chinese_character_count=12,
                section_coverage_complete=True,
                output_format="json",
                model_call_count=1,
            )
        ),
    )
    captured = {}

    def complete_json(_prompt, payload, *, trace_sink, **_kwargs):
        captured.update(payload)
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=100,
                max_tokens=6_000,
                timeout_seconds=400,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=1,
                finish_reason="stop",
            )
        )
        return {
            "status": "passed",
            "issues": [],
            "summary_zh": "证据与原意一致。",
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )

    result = localization_spoken_script.review_localization_spoken_script_fidelity(
        source_lock=source,
        script=script,
        route=route,
        verified_evidence_constraints=[
            localization_creation_context.LocalizationVerifiedEvidenceConstraint(
                question_id="question_0001",
                source_cue_ids=["cue_0001"],
                constraint_zh="画面确认该动作确实发生。",
                anchored_target_text_zh="确认过的功能",
            ),
            localization_creation_context.LocalizationVerifiedEvidenceConstraint(
                question_id="question_0002",
                source_cue_ids=["cue_0001"],
                constraint_zh="画面没有显示品牌，不得补写品牌。",
            ),
        ],
    )

    assert list(captured) == [
        "source_full_text",
        "localized_full_text",
        "confirmed_context",
    ]
    assert captured["source_full_text"] == "Hello world."
    assert captured["localized_full_text"] == ("[section_0001.paragraph_0001.sentence_0001] 我调用了画面确认过的功能。")
    evidence = captured["confirmed_context"]["verified_evidence"]
    assert len(evidence) == 2
    assert [row["source_excerpt"] for row in evidence] == ["Hello world."] * 2
    assert "画面确认该动作确实发生。" in evidence[0]["constraint_zh"]
    assert "确认过的功能" in evidence[0]["constraint_zh"]
    assert "画面没有显示品牌，不得补写品牌。" in evidence[1]["constraint_zh"]
    assert captured["confirmed_context"]["locked_glossary"] == [
        {
            "source_text": "Seedance",
            "corrected_source_text": "Seedance 2.0",
            "localized_text": "Seedance 2.0",
            "notes": "产品名不翻译",
        }
    ]
    serialized = json.dumps(captured, ensure_ascii=False)
    assert "cue_0001" not in serialized
    assert "document_brief" not in serialized
    assert "validation_feedback" not in captured
    assert result.request_summary is not None
    assert result.request_summary.request_fields == list(captured)
    assert result.request_summary.source_chars == len("Hello world.")
    projected = localization_spoken_script.project_localization_spoken_script_review_result(result)
    assert [section["title"] for section in projected["debug"]["sections"]] == ["实际质检提示词", "本次请求组成"]


def test_blind_naturalness_request_contains_only_localized_full_text(
    monkeypatch,
):
    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(
        phase="naturalness_review",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    script = localization_spoken_script.LocalizationSpokenScriptResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        creation_context_fingerprint="c" * 64,
        result_fingerprint="d" * 64,
        content=localization_spoken_script.LocalizationSpokenScriptContent(
            title="测试",
            sections=[
                localization_spoken_script.LocalizationSpokenScriptSection(
                    section_id="section_0001",
                    heading="正文",
                    paragraphs=["这是一段自然的中文口播。"],
                )
            ],
        ),
        route=route.model_copy(update={"phase": "spoken_script_creation"}),
        llm_calls=[],
        quality_summary=None,
    )
    captured = {}

    def complete_json(_prompt, payload, *, trace_sink, **_kwargs):
        captured.update(payload)
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=100,
                max_tokens=5_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=1,
                finish_reason="stop",
            )
        )
        return {
            "impression": "original_chinese_transcript",
            "naturalness": 4.8,
            "persona": 4.8,
            "emotion": 4.7,
            "flow": 4.8,
            "issues": [],
            "summary_zh": "自然。",
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )

    result = localization_spoken_script.review_localization_spoken_script_naturalness(
        source_fingerprint=source.source_fingerprint,
        script=script,
        route=route,
    )

    assert captured == {"localized_full_text": ("[section_0001.paragraph_0001.sentence_0001] 这是一段自然的中文口播。")}
    assert result.request_summary is not None
    assert result.request_summary.source_chars == 0
    assert result.request_summary.request_fields == ["localized_full_text"]


def test_empty_confirmed_context_is_omitted_from_fidelity_payload():
    source, _brief = _source_and_brief()

    request = localization_review_request.build_localization_fidelity_review_request(
        source_lock=source,
        localized_full_text="这是一段本土化全文。",
    )

    assert request.model_payload() == {
        "source_full_text": "Hello world.",
        "localized_full_text": "这是一段本土化全文。",
    }


def test_review_projection_explains_scope_and_blind_verdict():
    route = LocalizationAiPhaseRoute(
        phase="naturalness_review",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    review = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="naturalness",
        source_fingerprint="a" * 64,
        script_fingerprint="b" * 64,
        result_fingerprint="c" * 64,
        status="passed",
        impression="original_chinese_transcript",
        scores={
            "naturalness": 4.5,
            "persona": 4.7,
            "emotion": 4.6,
            "flow": 4.7,
        },
        issues=[],
        summary_zh="整体像中文创作者的原创口述。",
        route=route,
        llm_calls=[],
    )

    projected = localization_spoken_script.project_localization_spoken_script_review_result(review)

    assert projected["summary"] == "盲测通过：整体以中文原创口述为主。"
    assert projected["sections"][0]["title"] == "盲测结论"
    assert projected["sections"][0]["items"][0]["text"] == ("整体像中文创作者的原创口述。")
    assert {item["label"]: item["value"] for item in projected["sections"][0]["items"][0]["facts"]}[
        "输入范围"
    ] == "只看完整中文台词；不提供英文原文"


def test_finalization_rejects_an_unchanged_replacement():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="开场",
        paragraphs=["这句话还是有点书面。"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="naturalness_0001",
        severity="high",
        excerpt="这句话还是有点书面",
        reason_zh="不像自然口语。",
        required_change_zh="改成自然口语。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="这句话还是有点书面。",
            )
        ],
    )

    with pytest.raises(
        ValueError,
        match="替换内容与原句相同",
    ):
        localization_spoken_script._apply_section_edits(
            section,
            plan,
            issues=[issue],
        )


def test_finalization_allows_unchanged_replacement_on_last_bounded_round():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="开场",
        paragraphs=["这句已经按要求改好了。"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        excerpt="这句已经按要求改好了",
        reason_zh="限定复核仍报告原问题。",
        required_change_zh="保持当前准确表达。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="这句已经按要求改好了。",
            )
        ],
    )

    result = localization_spoken_script._apply_section_edits(
        section,
        plan,
        issues=[issue],
        allow_unchanged=True,
    )

    assert result == section


def test_finalization_allows_explicit_whole_sentence_deletion():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="开场",
        paragraphs=["保留这句。删除这句话。下一句继续。"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="addition",
        excerpt="删除这句话",
        reason_zh="原文没有这项信息。",
        required_change_zh="删除这句话，只保留上下文已有信息。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="",
            )
        ],
    )

    revised = localization_spoken_script._apply_section_edits(
        section,
        plan,
        issues=[issue],
    )

    assert revised.paragraphs == ["保留这句。下一句继续。"]


def test_finalization_allows_deleting_a_sentence_identified_as_added():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="结尾",
        paragraphs=["保留这句。新增的总结。下一句继续。"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="addition",
        excerpt="新增的总结",
        reason_zh="原文没有这项总结。",
        required_change_zh="删除这句新增的总结。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="",
            )
        ],
    )

    revised = localization_spoken_script._apply_section_edits(
        section,
        plan,
        issues=[issue],
    )

    assert revised.paragraphs == ["保留这句。下一句继续。"]


@pytest.mark.parametrize(
    "instruction",
    [
        "删除整句。",
        "删除该引导句，直接呈现下一句台词。",
        "删掉此过渡句。",
        "删除这句衔接句。",
        "删除该提示句。",
    ],
)
def test_finalization_recognizes_explicit_delete_whole_sentence_wording(
    instruction: str,
):
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="addition",
        excerpt="新增的总结",
        reason_zh="原文没有这项总结。",
        required_change_zh=instruction,
    )

    assert localization_spoken_script._issue_explicitly_requires_sentence_deletion(issue) is True


@pytest.mark.parametrize(
    "instruction",
    [
        "删除该新增总结句。",
        "删除该总结句。",
        "删掉这句补充解释。",
        "删除这句额外结论。",
        "删除该旁白，不要保留无来源的叙事总结。",
        "删掉这段台词。",
    ],
)
def test_finalization_recognizes_deletion_of_described_added_sentence(
    instruction: str,
):
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="addition",
        excerpt="新增的总结",
        reason_zh="原文没有这项总结。",
        required_change_zh=instruction,
    )

    assert localization_spoken_script._issue_explicitly_requires_sentence_deletion(issue) is True


@pytest.mark.parametrize(
    "instruction",
    [
        "删除该句中的错误词。",
        "删除该引导句中的情绪词，保留说话人提示。",
    ],
)
def test_finalization_does_not_treat_partial_sentence_edit_as_deletion(
    instruction: str,
):
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="addition",
        excerpt="句中的错误词",
        reason_zh="只有一个词没有依据。",
        required_change_zh=instruction,
    )

    assert localization_spoken_script._issue_explicitly_requires_sentence_deletion(issue) is False


def test_finalization_does_not_treat_partial_narration_edit_as_deletion():
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="addition",
        excerpt="旁白中的错误总结",
        reason_zh="只有旁白中的一个总结没有依据。",
        required_change_zh="删除该旁白中的错误总结，保留其他内容。",
    )

    assert localization_spoken_script._issue_explicitly_requires_sentence_deletion(issue) is False


def test_finalization_keeps_low_severity_explicit_addition_deletions_actionable():
    deletion = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="low",
        kind="addition",
        excerpt="原文没有的总结",
        reason_zh="原文没有这项总结。",
        required_change_zh="删除整句。",
    )
    wording = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0002",
        severity="low",
        kind="meaning",
        excerpt="只是措辞略弱",
        reason_zh="可以更精确。",
        required_change_zh="改得更精确。",
    )
    fidelity = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        issues=[deletion, wording]
    )

    actionable = localization_spoken_script._actionable_review_issues(
        fidelity,
        localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(issues=[]),
    )

    assert [item.issue_id for item in actionable] == ["fidelity_0001"]


def test_finalization_rejects_unrequested_sentence_deletion():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="开场",
        paragraphs=["这句话需要改写。"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="meaning",
        excerpt="这句话需要改写",
        reason_zh="表达不准确。",
        required_change_zh="改成准确表达。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="",
            )
        ],
    )

    with pytest.raises(ValueError, match="没有要求删除整句"):
        localization_spoken_script._apply_section_edits(
            section,
            plan,
            issues=[issue],
        )


def test_finalization_accepts_question_to_exclamation_as_effective_edit():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="正文",
        paragraphs=["他在守护遗物？"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0008",
        severity="medium",
        kind="meaning",
        excerpt="他在守护遗物？",
        reason_zh="原文是肯定语气。",
        required_change_zh="改成肯定表达，如“他在守护遗物！”。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="他在守护遗物！",
            )
        ],
    )

    revised = localization_spoken_script._apply_section_edits(
        section,
        plan,
        issues=[issue],
    )

    assert revised.paragraphs == ["他在守护遗物！"]


def test_finalization_rejects_replacement_that_repeats_unchanged_tail():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="正文",
        paragraphs=["“我找到坐标了。信号指向西藏，很可能是一座寺院。”"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0013",
        severity="high",
        kind="meaning",
        excerpt="“我找到坐标了。”",
        reason_zh="本句前遗漏了训练和现场互动。",
        required_change_zh="改写本句并保留相邻内容。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement=("“他们完成训练后，我找到坐标了。信号指向西藏，很可能是一座寺院。信号指向西藏，很可能是一座寺院。”"),
            )
        ],
    )

    with pytest.raises(ValueError, match="只读相邻内容"):
        localization_spoken_script._apply_section_edits(
            section,
            plan,
            issues=[issue],
        )


def test_finalization_retry_explains_that_adjacent_sentences_are_readonly():
    feedback = localization_spoken_script._finalization_validation_feedback(
        ValueError("替换内容重复了相邻的未修改内容")
    )

    assert "只能包含对应 editable_sentence 的完整替换句" in feedback
    assert "不得复制编辑单元外相邻的未修改句" in feedback
    assert "readonly_fragments 必须按原文、顺序和次数保留" in feedback


def test_finalization_allows_repeated_context_explicitly_required_by_review():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="歌词",
        paragraphs=["谁会带我继续走？我的朋友们，谁还在？"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0039",
        severity="medium",
        kind="meaning",
        excerpt="我的朋友们，谁还在？",
        reason_zh="原意问的是谁会带我继续走。",
        required_change_zh=("改为表达“我的伙伴们，谁会带我继续走？”的歌词。"),
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="我的伙伴们，谁会带我继续走？",
            )
        ],
    )

    revised = localization_spoken_script._apply_section_edits(
        section,
        plan,
        issues=[issue],
    )

    assert revised.paragraphs == ["谁会带我继续走？我的伙伴们，谁会带我继续走？"]


def test_finalization_preserves_quote_that_closes_after_unchanged_tail():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="正文",
        paragraphs=["“我找到坐标了。信号指向西藏，很可能是一座寺院。”"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0013",
        severity="high",
        kind="meaning",
        excerpt="“我找到坐标了。”",
        reason_zh="本句前遗漏了训练和现场互动。",
        required_change_zh="改写本句并保留相邻内容。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement=("停车后，他们完成训练，随后才有人说道：“我找到坐标了。信号指向西藏，很可能是一座寺院。”"),
            )
        ],
    )

    revised = localization_spoken_script._apply_section_edits(
        section,
        plan,
        issues=[issue],
    )

    assert revised.paragraphs == [
        ("停车后，他们完成训练，随后才有人说道：“我找到坐标了。信号指向西藏，很可能是一座寺院。”")
    ]


def test_finalization_inserts_missing_sentences_before_stable_target():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0005",
        heading="正文",
        paragraphs=["“我找到坐标了。信号指向西藏，很可能是一座寺院。”"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0013",
        severity="high",
        kind="omission",
        excerpt="“我找到坐标了。”",
        reason_zh="本句前遗漏了停车、警方命令和训练。",
        required_change_zh="在本句前补回这些事件，不要概括。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                operation="replace",
                replacement=("“停车！把手举起来！”他们测试了武器和开门装置，装备仍未完成。“我找到坐标了。信号指向西藏，很可能是一座寺院。”"),
            )
        ],
    )

    revised = localization_spoken_script._apply_section_edits(
        section,
        plan,
        issues=[issue],
    )

    assert revised.paragraphs == [
        ("“停车！把手举起来！”他们测试了武器和开门装置，装备仍未完成。“我找到坐标了。信号指向西藏，很可能是一座寺院。”")
    ]


def test_finalization_assigns_a_sentence_range_to_multi_sentence_excerpt():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0002",
        heading="会议",
        paragraphs=["“请稍等！先别关门！”"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0002",
        severity="medium",
        kind="omission",
        excerpt="“请稍等！先别关门！”",
        reason_zh="这段话之前遗漏了来访者已经登记。",
        required_change_zh="在本句前补回登记过程。",
    )

    sentence_id, paragraph_index, editable_text = localization_spoken_script._locate_issue_sentence_reference(
        section,
        issue,
    )

    assert sentence_id == ("section_0002.paragraph_0001.sentence_0001")
    assert paragraph_index == 0
    assert editable_text == "“请稍等！先别关门！”"


def test_finalization_merges_multiple_requirements_for_one_sentence():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0003",
        heading="承诺与转折",
        paragraphs=["“我们会没事的。我保证。”"],
    )
    issues = [
        localization_spoken_script.LocalizationSpokenScriptReviewIssue(
            issue_id="fidelity_0008",
            severity="high",
            kind="meaning",
            excerpt="“我们会没事的。我保证。”",
            reason_zh="承诺出现得太晚。",
            required_change_zh="将该句恢复到事件发生之前。",
        ),
        localization_spoken_script.LocalizationSpokenScriptReviewIssue(
            issue_id="fidelity_0009",
            severity="medium",
            kind="omission",
            excerpt="“我们会没事的。我保证。”",
            reason_zh="承诺后遗漏了一句挑战。",
            required_change_zh="在该句之后补回挑战，再进入下一事件。",
        ),
    ]

    merged = localization_spoken_script._merge_review_issues_by_target_sentence(section, issues)

    assert len(merged) == 1
    assert merged[0].issue_id == "fidelity_0008+fidelity_0009"
    assert merged[0].severity == "high"
    assert merged[0].kind == "meaning"
    assert "恢复到事件发生之前" in merged[0].required_change_zh
    assert "在该句之后补回挑战" in merged[0].required_change_zh
    assert localization_spoken_script._issue_explicitly_requires_sentence_move(merged[0])
    assert localization_spoken_script._allowed_edit_operations(merged[0]) == ["replace"]


def test_finalization_merge_preserves_target_when_identical_sentence_repeats():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0007",
        heading="重复问句",
        paragraphs=[
            "先处理前一件事。下一个地点是哪里？",
            "下一个地点是哪里？",
        ],
    )
    first_sentence_id = "section_0007.paragraph_0001.sentence_0002"
    second_sentence_id = "section_0007.paragraph_0002.sentence_0001"
    issues = [
        localization_spoken_script.LocalizationSpokenScriptReviewIssue(
            issue_id="fidelity_0001",
            sentence_id=first_sentence_id,
            severity="medium",
            kind="omission",
            excerpt="下一个地点是哪里？",
            reason_zh="前面漏了一句确认。",
            required_change_zh="在本句之前补回确认。",
        ),
        localization_spoken_script.LocalizationSpokenScriptReviewIssue(
            issue_id="fidelity_0002",
            sentence_id=first_sentence_id,
            severity="medium",
            kind="omission",
            excerpt="下一个地点是哪里？",
            reason_zh="前面漏了一句回答。",
            required_change_zh="在本句之前补回答复。",
        ),
        localization_spoken_script.LocalizationSpokenScriptReviewIssue(
            issue_id="fidelity_0003",
            sentence_id=second_sentence_id,
            severity="medium",
            kind="addition",
            excerpt="下一个地点是哪里？",
            reason_zh="这是重复句。",
            required_change_zh="删除这一处重复句。",
        ),
    ]

    merged = localization_spoken_script._merge_review_issues_by_target_sentence(section, issues)

    assert len(merged) == 2
    assert merged[0].issue_id == "fidelity_0001+fidelity_0002"
    assert merged[0].sentence_id == first_sentence_id
    assert merged[1].sentence_id == second_sentence_id
    assert [
        localization_spoken_script._locate_issue_sentence_reference(
            section,
            issue,
        )[0]
        for issue in merged
    ] == [first_sentence_id, second_sentence_id]


def test_finalization_merges_overlapping_review_ranges_into_one_atomic_edit():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0006",
        heading="终战",
        paragraphs=["糟了！杰克斯，准备！啊！強何前望叶！这只会是你灭亡的开始！我说过，我不需要开口。"],
    )
    issues = [
        localization_spoken_script.LocalizationSpokenScriptReviewIssue(
            issue_id="fidelity_0001",
            severity="high",
            kind="addition",
            excerpt="強何前望叶！",
            reason_zh="这是没有语义依据的乱码。",
            required_change_zh=("删除完整句子“強何前望叶！”，保留后面的正确台词。"),
        ),
        localization_spoken_script.LocalizationSpokenScriptReviewIssue(
            issue_id="naturalness_0004",
            severity="high",
            kind="naturalness",
            excerpt="啊！強何前望叶！",
            reason_zh="乱码破坏了对白理解。",
            required_change_zh="恢复成完整、可理解的中文。",
        ),
    ]

    merged = localization_spoken_script._merge_review_issues_by_target_sentence(section, issues)

    assert len(merged) == 1
    assert merged[0].excerpt == "啊！強何前望叶！"
    assert localization_spoken_script._allowed_edit_operations(merged[0]) == ["replace"]

    revised = localization_spoken_script._apply_section_edits(
        section,
        localization_spoken_script.LocalizationSpokenScriptSectionEdits(
            section_id=section.section_id,
            edits=[
                localization_spoken_script.LocalizationSpokenScriptEditOperation(
                    issue_id=merged[0].issue_id,
                    replacement="啊！",
                )
            ],
        ),
        issues=merged,
    )

    assert revised.paragraphs == ["糟了！杰克斯，准备！啊！这只会是你灭亡的开始！我说过，我不需要开口。"]


def test_finalization_numbers_and_edits_each_repeated_expression_occurrence():
    content = localization_spoken_script.LocalizationSpokenScriptContent(
        title="电影对白",
        sections=[
            localization_spoken_script.LocalizationSpokenScriptSection(
                section_id="section_0004",
                heading="问候",
                paragraphs=["合十礼。蕾恩，你知道圣所在哪儿吗？合十礼。快走！"],
            )
        ],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="naturalness_0002",
        severity="medium",
        kind="naturalness",
        excerpt="合十礼。",
        reason_zh="直译生硬。",
        required_change_zh="改成符合语境的自然问候。",
    )

    expanded = localization_spoken_script._expand_repeated_expression_issues(
        content,
        [issue],
    )

    assert [localization_spoken_script._issue_target_sentence_id(item) for item in expanded] == [
        "section_0004.paragraph_0001.sentence_0001",
        "section_0004.paragraph_0001.sentence_0003",
    ]
    revised = localization_spoken_script._apply_section_edits(
        content.sections[0],
        localization_spoken_script.LocalizationSpokenScriptSectionEdits(
            section_id="section_0004",
            edits=[
                localization_spoken_script.LocalizationSpokenScriptEditOperation(
                    issue_id=expanded[0].issue_id,
                    target_sentence_id=("section_0004.paragraph_0001.sentence_0001"),
                    replacement="你好。",
                ),
                localization_spoken_script.LocalizationSpokenScriptEditOperation(
                    issue_id=expanded[1].issue_id,
                    target_sentence_id=("section_0004.paragraph_0001.sentence_0003"),
                    replacement="你好。",
                ),
            ],
        ),
        issues=expanded,
    )

    assert revised.paragraphs == ["你好。蕾恩，你知道圣所在哪儿吗？你好。快走！"]


def test_finalization_maps_multi_sentence_excerpt_after_whitespace():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0007",
        heading="诀别",
        paragraphs=["告诉她，我想见宝宝。 我妈妈的。这是她留给我的唯一东西。只剩下这个名字。"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="naturalness_0005",
        severity="medium",
        kind="naturalness",
        excerpt="我妈妈的。这是她留给我的唯一东西。",
        reason_zh="所属结构悬空。",
        required_change_zh="补出所指物并顺畅连接。",
    )

    sentence_id, paragraph_index, editable = localization_spoken_script._locate_issue_sentence_reference(
        section,
        issue,
    )

    assert sentence_id == ("section_0007.paragraph_0001.sentence_0002_to_sentence_0003")
    assert paragraph_index == 0
    assert editable == "我妈妈的。这是她留给我的唯一东西。"
    revised = localization_spoken_script._apply_section_edits(
        section,
        localization_spoken_script.LocalizationSpokenScriptSectionEdits(
            section_id=section.section_id,
            edits=[
                localization_spoken_script.LocalizationSpokenScriptEditOperation(
                    issue_id=issue.issue_id,
                    target_sentence_id=sentence_id,
                    replacement="这是我妈妈留下的唯一东西。",
                )
            ],
        ),
        issues=[issue],
    )
    assert revised.paragraphs == ["告诉她，我想见宝宝。 这是我妈妈留下的唯一东西。只剩下这个名字。"]


def test_finalization_does_not_guess_between_repeated_story_facts():
    content = localization_spoken_script.LocalizationSpokenScriptContent(
        title="电影对白",
        sections=[
            localization_spoken_script.LocalizationSpokenScriptSection(
                section_id="section_0001",
                heading="冲突",
                paragraphs=["他来了。先躲起来。他来了。快跑！"],
            )
        ],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="high",
        kind="meaning",
        excerpt="他来了。",
        reason_zh="其中一处人物指代错误。",
        required_change_zh="只修正有原文依据的那一处。",
    )

    expanded = localization_spoken_script._expand_repeated_expression_issues(
        content,
        [issue],
    )

    assert expanded == [issue]
    with pytest.raises(ValueError, match="无法映射到唯一"):
        localization_spoken_script._locate_issue_sentence_reference(
            content.sections[0],
            issue,
        )


def test_finalization_uses_review_sentence_id_for_repeated_story_fact():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="冲突",
        paragraphs=["他来了。先躲起来。他来了。快跑！"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        sentence_id="section_0001.paragraph_0001.sentence_0003",
        severity="high",
        kind="addition",
        excerpt="他来了。",
        reason_zh="第二次出现没有原文依据。",
        required_change_zh="删除第二次出现的这句话。",
    )

    sentence_id, paragraph_index, sentence = localization_spoken_script._locate_issue_sentence_reference(
        section,
        issue,
    )

    assert sentence_id == "section_0001.paragraph_0001.sentence_0003"
    assert paragraph_index == 0
    assert sentence == "他来了。"


def test_finalization_maps_excerpt_without_trailing_closing_quote():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0003",
        heading="复核",
        paragraphs=["“材料已经核对。价值判断没有依据。”"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0003",
        severity="medium",
        kind="addition",
        excerpt="价值判断没有依据。",
        reason_zh="这句是额外增加的判断。",
        required_change_zh="删除这句无依据的价值判断。",
    )

    sentence_id, _, editable_text = localization_spoken_script._locate_issue_sentence_reference(
        section,
        issue,
    )

    assert sentence_id == ("section_0003.paragraph_0001.sentence_0001")
    assert editable_text == "“材料已经核对。价值判断没有依据。”"

    revised = localization_spoken_script._apply_section_edits(
        section,
        localization_spoken_script.LocalizationSpokenScriptSectionEdits(
            section_id=section.section_id,
            edits=[
                localization_spoken_script.LocalizationSpokenScriptEditOperation(
                    issue_id=issue.issue_id,
                    operation="replace",
                    replacement="“材料已经核对。”",
                )
            ],
        ),
        issues=[issue],
    )

    assert revised.paragraphs == ["“材料已经核对。”"]


def test_finalization_moves_and_rewrites_sentence_across_sections_atomically():
    content = localization_spoken_script.LocalizationSpokenScriptContent(
        title="设备演示",
        sections=[
            localization_spoken_script.LocalizationSpokenScriptSection(
                section_id="section_0001",
                heading="准备",
                paragraphs=[
                    "设备说明还在这里。",
                    "第一项功能已经就绪。",
                    "第二项功能已经就绪。",
                    "其他准备工作已经完成。",
                ],
            ),
            localization_spoken_script.LocalizationSpokenScriptSection(
                section_id="section_0002",
                heading="演示",
                paragraphs=["演示人员说很简单。随后开始演示。"],
            ),
        ],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0004",
        severity="medium",
        kind="meaning",
        excerpt="设备说明还在这里。",
        reason_zh="设备说明出现得太早。",
        required_change_zh=(
            "将该句及紧随其后的两项功能介绍移到“演示人员说很简单。”之后，并将该句改为“升级完成，设备现在可以投入使用。”"
        ),
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id="section_0001",
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                target_sentence_id=("section_0001.paragraph_0001.sentence_0001"),
                replacement="升级完成，设备现在可以投入使用。",
                placement="after",
                anchor_sentence_id=("section_0002.paragraph_0001.sentence_0001"),
                anchor_sentence="演示人员说很简单。",
                companion_sentence_ids=[
                    "section_0001.paragraph_0002.sentence_0001",
                    "section_0001.paragraph_0003.sentence_0001",
                ],
            )
        ],
    )

    revised, revised_section_ids = localization_spoken_script._apply_content_edits(
        content,
        plan,
        issues=[issue],
    )

    assert revised_section_ids == ["section_0001", "section_0002"]
    assert revised.sections[0].paragraphs == ["其他准备工作已经完成。"]
    assert revised.sections[1].paragraphs == [
        "演示人员说很简单。",
        "升级完成，设备现在可以投入使用。",
        "第一项功能已经就绪。",
        "第二项功能已经就绪。",
        "随后开始演示。",
    ]
    assert localization_spoken_script._ordered_relevant_section_ids(
        revised,
        source_section_ids=["section_0001"],
        revised_section_ids=revised_section_ids,
    ) == ["section_0001", "section_0002"]


@pytest.mark.parametrize(
    ("required_change_zh", "companion_sentence_ids", "message"),
    [
        (
            "将该句移到“随后开始演示。”之前。",
            ["section_0001.paragraph_0002.sentence_0001"],
            "没有要求移动后续内容",
        ),
        (
            "将该句及紧随其后的内容移到“随后开始演示。”之前。",
            ["section_0001.paragraph_0003.sentence_0001"],
            "必须是原句后连续的候选",
        ),
    ],
)
def test_finalization_rejects_unapproved_or_nonconsecutive_companion_moves(
    required_change_zh,
    companion_sentence_ids,
    message,
):
    content = localization_spoken_script.LocalizationSpokenScriptContent(
        title="演示",
        sections=[
            localization_spoken_script.LocalizationSpokenScriptSection(
                section_id="section_0001",
                heading="准备",
                paragraphs=["设备已就绪。", "第一项功能。", "第二项功能。"],
            ),
            localization_spoken_script.LocalizationSpokenScriptSection(
                section_id="section_0002",
                heading="现场",
                paragraphs=["随后开始演示。"],
            ),
        ],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="meaning",
        excerpt="设备已就绪。",
        reason_zh="内容位置不正确。",
        required_change_zh=required_change_zh,
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id="section_0001",
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                target_sentence_id=("section_0001.paragraph_0001.sentence_0001"),
                replacement="设备已就绪。",
                placement="before",
                anchor_sentence_id=("section_0002.paragraph_0001.sentence_0001"),
                anchor_sentence="随后开始演示。",
                companion_sentence_ids=companion_sentence_ids,
            )
        ],
    )

    with pytest.raises(ValueError, match=message):
        localization_spoken_script._apply_content_edits(
            content,
            plan,
            issues=[issue],
        )


def test_finalization_rejects_replacement_when_issue_requires_insertion():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0005",
        heading="正文",
        paragraphs=["我找到坐标了。"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0013",
        severity="high",
        kind="omission",
        excerpt="我找到坐标了。",
        reason_zh="本句前遗漏了现场互动。",
        required_change_zh="在本句前恢复遗漏内容。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="现场互动后，我找到坐标了。",
            )
        ],
    )

    with pytest.raises(ValueError, match="必须在原句前插入内容"):
        localization_spoken_script._apply_section_edits(
            section,
            plan,
            issues=[issue],
        )


def test_finalization_replaces_a_quoted_sentence_without_duplicate_marks():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="正文",
        paragraphs=["流程很简单。我只说：“让我的卷发燃起火焰。”"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="naturalness_0001",
        severity="medium",
        excerpt="让我的卷发燃起火焰",
        reason_zh="搭配生硬。",
        required_change_zh="改成自然口语。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="我只说：“让我的卷发直接烧起来。”",
            )
        ],
    )

    revised = localization_spoken_script._apply_section_edits(
        section,
        plan,
        issues=[issue],
    )

    assert revised.paragraphs == ["流程很简单。我只说：“让我的卷发直接烧起来。”"]


def test_finalization_moves_an_existing_reaction_after_its_anchor():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="开场",
        paragraphs=[
            (
                "我还能继续往里加东西：在身后放一个现实中绝不该"
                "出现的庞然大物；让自己的头发直接烧起来；"
                "开车的时候，把周围整个世界全部换掉。"
            ),
            "是不是有点离谱？",
        ],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="meaning",
        excerpt="是不是有点离谱？",
        reason_zh="这应当是对身后庞然大物的即时反应。",
        required_change_zh=("将这句移到庞然大物之后、头发着火案例之前。"),
    )
    anchor = "我还能继续往里加东西：在身后放一个现实中绝不该出现的庞然大物；"
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="是不是有点离谱？",
                placement="after",
                anchor_sentence_id=("section_0001.paragraph_0001.sentence_0001"),
                anchor_sentence=anchor,
            )
        ],
    )

    revised = localization_spoken_script._apply_section_edits(
        section,
        plan,
        issues=[issue],
    )

    assert revised.paragraphs == [
        (f"{anchor}是不是有点离谱？让自己的头发直接烧起来；开车的时候，把周围整个世界全部换掉。")
    ]


def test_finalization_uses_anchor_id_when_anchor_text_is_repeated():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="重复对话",
        paragraphs=["出发。", "继续。", "出发。", "真的？"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="meaning",
        excerpt="真的？",
        reason_zh="反应属于后一次重复台词。",
        required_change_zh="将这句移到后一次“出发。”之后。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="真的？",
                placement="after",
                anchor_sentence_id=("section_0001.paragraph_0003.sentence_0001"),
                anchor_sentence="出发。",
            )
        ],
    )

    revised = localization_spoken_script._apply_section_edits(
        section,
        plan,
        issues=[issue],
    )

    assert revised.paragraphs == ["出发。", "继续。", "出发。真的？"]


def test_finalization_rejects_move_without_explicit_review_instruction():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="开场",
        paragraphs=["第一句。第二句。"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="meaning",
        excerpt="第二句。",
        reason_zh="表达不准确。",
        required_change_zh="改成准确表达。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="第二句。",
                placement="before",
                anchor_sentence_id=("section_0001.paragraph_0001.sentence_0001"),
                anchor_sentence="第一句。",
            )
        ],
    )

    with pytest.raises(ValueError, match="没有要求移动整句"):
        localization_spoken_script._apply_section_edits(
            section,
            plan,
            issues=[issue],
        )


def test_finalization_rejects_a_broader_move_anchor_than_the_candidates():
    section = localization_spoken_script.LocalizationSpokenScriptSection(
        section_id="section_0001",
        heading="开场",
        paragraphs=["第一个案例；第二个案例；第三个案例。", "离谱吧？"],
    )
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="medium",
        kind="meaning",
        excerpt="离谱吧？",
        reason_zh="这是对第一个案例的即时反应。",
        required_change_zh="将这句移到第一个案例之后。",
    )
    plan = localization_spoken_script.LocalizationSpokenScriptSectionEdits(
        section_id=section.section_id,
        edits=[
            localization_spoken_script.LocalizationSpokenScriptEditOperation(
                issue_id=issue.issue_id,
                replacement="离谱吧？",
                placement="after",
                anchor_sentence_id=("section_0001.paragraph_0001.sentence_0001"),
                anchor_sentence="第一个案例；第二个案例；第三个案例。",
            )
        ],
    )

    with pytest.raises(ValueError, match="移动位置不在候选范围内"):
        localization_spoken_script._apply_section_edits(
            section,
            plan,
            issues=[issue],
        )


def test_review_normalizes_trailing_colon_in_issue_field_name():
    issues = localization_spoken_script._normalize_review_issue_fields(
        [
            {
                "issue_id": "naturalness_0001",
                "severity:": "low",
                "excerpt": "示例",
                "reason_zh": "示例原因",
                "required_change_zh": "示例修改",
            }
        ]
    )

    assert issues[0]["severity"] == "low"
    assert "severity:" not in issues[0]


def test_finalization_passes_through_when_both_reviews_pass(
    monkeypatch,
):
    source, brief = _source_and_brief()
    creation_route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
    )

    def complete_text(*_args, trace_sink, **_kwargs):
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=5_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "chunk_id": "chunk_0001",
            "suggested_title": "今天聊个简单的事",
            "paragraphs": ["大家好，今天聊个简单的事。"],
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_text,
    )
    script = localization_spoken_script.generate_localization_spoken_script(
        _script_request(source, brief, creation_route)
    )
    review_route = LocalizationAiPhaseRoute(
        phase="fidelity_review",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    fidelity = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="fidelity",
        source_fingerprint=source.source_fingerprint,
        script_fingerprint=script.result_fingerprint,
        result_fingerprint="d" * 64,
        status="passed",
        impression="not_applicable",
        scores={},
        issues=[],
        summary_zh="原意完整。",
        route=review_route,
        llm_calls=[],
    )
    naturalness = fidelity.model_copy(
        update={
            "review_kind": "naturalness",
            "result_fingerprint": "e" * 64,
            "impression": "original_chinese_transcript",
        }
    )
    final_route = LocalizationAiPhaseRoute(
        phase="spoken_script_finalization",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )

    result = localization_spoken_script.finalize_localization_spoken_script(
        localization_spoken_script.LocalizationSpokenScriptFinalizationInput(
            script_operation_id="script_operation",
            fidelity_review_operation_id="fidelity_operation",
            naturalness_review_operation_id="naturalness_operation",
            source_lock=source,
            document_brief=brief,
            script=script,
            fidelity_review=fidelity,
            naturalness_review=naturalness,
            route=final_route,
        )
    )

    assert result.content == script.content
    assert result.quality_summary.status == "passed"
    assert result.quality_summary.model_call_count == 0
    projected = localization_spoken_script.project_localization_spoken_script_final_result(result)
    assert projected["label"] == "本土化台词终审"
    assert projected["document"]["title"] == "终审本土化台词"
    assert projected["metrics"][0] == {
        "label": "处理结论",
        "value": "无需修改，原样锁定",
    }
    assert projected["debug"]["metrics"][0] == {
        "label": "提示词版本",
        "value": (localization_spoken_script.FINALIZATION_BEHAVIOR_VERSION),
    }
    assert [section["title"] for section in projected["debug"]["sections"]] == ["实际修订提示词", "请求边界"]
    assert "不包含 cue、时间戳、字幕切段" in (projected["debug"]["sections"][1]["items"][0]["text"])


def test_finalization_escalates_second_quality_round_only_when_allowed():
    auto_route = LocalizationAiPhaseRoute(
        phase="spoken_script_finalization",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
        may_escalate=True,
    )

    assert (
        localization_spoken_script._finalization_reasoning_effort(
            auto_route,
            round_index=1,
            attempt=0,
        )
        == "low"
    )
    assert (
        localization_spoken_script._finalization_reasoning_effort(
            auto_route,
            round_index=2,
            attempt=0,
        )
        == "high"
    )
    assert (
        localization_spoken_script._finalization_reasoning_effort(
            auto_route,
            round_index=1,
            attempt=1,
        )
        == "high"
    )
    assert (
        localization_spoken_script._finalization_reasoning_effort(
            auto_route.model_copy(update={"may_escalate": False}),
            round_index=2,
            attempt=0,
        )
        == "low"
    )


def test_naturalness_adjudication_requires_original_chinese_impression():
    route = LocalizationAiPhaseRoute(
        phase="naturalness_review",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    preliminary = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="naturalness",
        source_fingerprint="a" * 64,
        script_fingerprint="b" * 64,
        result_fingerprint="c" * 64,
        status="passed",
        impression="localized_translation",
        scores={"naturalness": 4.0},
        issues=[
            localization_spoken_script.LocalizationSpokenScriptReviewIssue(
                issue_id="n1",
                severity="medium",
                excerpt="这就是怎么做",
                reason_zh="英语句法残留",
                required_change_zh="改成自然中文",
            )
        ],
        summary_zh="分数够用，但第一印象仍像本土化稿。",
        route=route,
        llm_calls=[],
    )
    adjudicated = preliminary.model_copy(
        update={
            "result_fingerprint": "d" * 64,
            "status": "passed",
            "impression": "original_chinese_transcript",
            "issues": [],
            "summary_zh": "像中国创作者原生口述。",
        }
    )

    assert localization_spoken_script._naturalness_strictly_passes(preliminary) is False
    merged = localization_spoken_script._merge_naturalness_adjudication(
        preliminary,
        adjudicated,
    )
    assert localization_spoken_script._naturalness_strictly_passes(merged) is True


def test_naturalness_gate_requires_actionable_local_polish_to_close():
    route = LocalizationAiPhaseRoute(
        phase="naturalness_review",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    review = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="naturalness",
        source_fingerprint="a" * 64,
        script_fingerprint="b" * 64,
        result_fingerprint="c" * 64,
        status="needs_revision",
        impression="original_chinese_transcript",
        scores={
            "naturalness": 4.3,
            "persona": 4.7,
            "emotion": 4.6,
            "flow": 4.7,
        },
        issues=[
            localization_spoken_script.LocalizationSpokenScriptReviewIssue(
                issue_id="n1",
                severity="high",
                excerpt="这就是怎样做",
                reason_zh="局部仍可说得更自然。",
                required_change_zh="改成更自然的中文。",
            )
        ],
        summary_zh="整体像中国创作者原生口述，局部仍可润色。",
        route=route,
        llm_calls=[],
    )

    assert localization_spoken_script._naturalness_strictly_passes(review) is False


def test_native_script_with_local_issues_can_enter_targeted_finalization():
    route = LocalizationAiPhaseRoute(
        phase="naturalness_review",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    local_issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="naturalness_0001",
        severity="medium",
        kind="naturalness",
        excerpt="沿途帮助你们",
        reason_zh="局部搭配生硬。",
        required_change_zh="改成自然且不增加事实的中文。",
    )
    naturalness = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="naturalness",
        source_fingerprint="a" * 64,
        script_fingerprint="b" * 64,
        result_fingerprint="c" * 64,
        status="needs_revision",
        impression="original_chinese_transcript",
        scores={
            "naturalness": 4.2,
            "persona": 4.4,
            "emotion": 4.5,
            "flow": 3.9,
        },
        issues=[local_issue],
        summary_zh="整体像中文原创口述，但有明确的局部问题。",
        route=route,
        llm_calls=[],
    )
    fidelity = naturalness.model_copy(
        update={
            "review_kind": "fidelity",
            "result_fingerprint": "d" * 64,
            "status": "passed",
            "impression": "not_applicable",
            "scores": {},
            "issues": [],
        }
    )

    assert localization_spoken_script._naturalness_strictly_passes(naturalness) is False
    assert localization_spoken_script._naturalness_allows_targeted_finalization(naturalness) is True
    assert [
        item.issue_id
        for item in localization_spoken_script._actionable_review_issues(
            fidelity,
            naturalness,
        )
    ] == ["naturalness_0001"]


def test_localized_script_can_enter_targeted_naturalness_rewrite():
    naturalness = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        status="needs_revision",
        impression="localized_translation",
        scores={"naturalness": 3.9},
        issues=[
            localization_spoken_script.LocalizationSpokenScriptReviewIssue(
                issue_id="naturalness_0001",
                severity="high",
                kind="naturalness",
                excerpt="这就是怎样做",
                reason_zh="全文仍有翻译腔。",
                required_change_zh="重写成自然中文。",
            )
        ],
    )

    assert localization_spoken_script._naturalness_allows_targeted_finalization(naturalness) is True


def test_finalization_stops_when_initial_naturalness_is_not_native(
    monkeypatch,
):
    source, brief = _source_and_brief()
    creation_route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="quality",
        model_id="quality-model",
        reasoning_effort="high",
        output_format="markdown",
        prompt_strategy="adaptive",
    )
    script = localization_spoken_script.LocalizationSpokenScriptResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        result_fingerprint="s" * 64,
        dynamic_rule_ids=[],
        content=(
            localization_spoken_script.LocalizationSpokenScriptContent(
                title="测试",
                sections=[
                    localization_spoken_script.LocalizationSpokenScriptSection(
                        section_id="section_0001",
                        heading="开场",
                        paragraphs=["这是一段自然口播。"],
                    )
                ],
            )
        ),
        route=creation_route,
        llm_calls=[],
        quality_summary=None,
    )
    review_route = creation_route.model_copy(
        update={
            "phase": "naturalness_review",
            "output_format": "json",
        }
    )
    fidelity_route = review_route.model_copy(update={"phase": "fidelity_review"})
    fidelity = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="fidelity",
        source_fingerprint=source.source_fingerprint,
        script_fingerprint=script.result_fingerprint,
        result_fingerprint="f" * 64,
        status="passed",
        impression="not_applicable",
        scores={},
        issues=[],
        summary_zh="原意完整。",
        route=fidelity_route,
        llm_calls=[],
    )
    preliminary = fidelity.model_copy(
        update={
            "review_kind": "naturalness",
            "result_fingerprint": "n" * 64,
            "status": "needs_revision",
            "impression": "localized_translation",
            "scores": {"naturalness": 3.8},
            "issues": [],
        }
    )
    adjudicated = preliminary.model_copy(
        update={
            "result_fingerprint": "a" * 64,
            "status": "passed",
            "impression": "original_chinese_transcript",
            "scores": {"naturalness": 4.3},
            "issues": [],
            "summary_zh": "像中文创作者原生口述。",
        }
    )
    monkeypatch.setattr(
        localization_spoken_script,
        "review_localization_spoken_script_naturalness",
        lambda **_kwargs: adjudicated,
    )

    result = localization_spoken_script.finalize_localization_spoken_script(
        localization_spoken_script.LocalizationSpokenScriptFinalizationInput(
            script_operation_id="script_operation",
            fidelity_review_operation_id="fidelity_operation",
            naturalness_review_operation_id="naturalness_operation",
            source_lock=source,
            document_brief=brief,
            script=script,
            fidelity_review=fidelity,
            naturalness_review=preliminary,
            route=creation_route.model_copy(update={"phase": "spoken_script_finalization"}),
            post_naturalness_adjudication_route=review_route,
        )
    )

    assert result.content == script.content
    assert result.quality_summary.status == "warning"
    assert result.post_naturalness_review == preliminary
    projected = localization_spoken_script.project_localization_spoken_script_final_result(result)
    assert projected["status"] == "warning"
    assert "带提醒继续" in projected["metrics"][0]["value"]
    assert "不阻断" in projected["notes"][0]


def test_finalization_repairs_actionable_local_issue_in_native_script(
    monkeypatch,
):
    source, brief = _source_and_brief()
    creation_route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="quality",
        model_id="quality-model",
        reasoning_effort="high",
        output_format="markdown",
        prompt_strategy="adaptive",
    )
    script = localization_spoken_script.LocalizationSpokenScriptResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        result_fingerprint="s" * 64,
        dynamic_rule_ids=[],
        content=(
            localization_spoken_script.LocalizationSpokenScriptContent(
                title="测试",
                sections=[
                    localization_spoken_script.LocalizationSpokenScriptSection(
                        section_id="section_0001",
                        heading="正文",
                        paragraphs=["而在这里，用户入口只是提出一个需求。"],
                    )
                ],
            )
        ),
        route=creation_route,
        llm_calls=[],
        quality_summary=None,
    )
    fidelity_route = creation_route.model_copy(
        update={
            "phase": "fidelity_review",
            "output_format": "json",
        }
    )
    naturalness_route = fidelity_route.model_copy(update={"phase": "naturalness_review"})
    fidelity = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="fidelity",
        source_fingerprint=source.source_fingerprint,
        script_fingerprint=script.result_fingerprint,
        result_fingerprint="f" * 64,
        status="passed",
        impression="not_applicable",
        scores={},
        issues=[],
        summary_zh="原意完整。",
        route=fidelity_route,
        llm_calls=[],
    )
    local_issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="naturalness_0001",
        severity="high",
        kind="naturalness",
        excerpt="而在这里，用户入口只是提出一个需求",
        reason_zh="局部表达不成立。",
        required_change_zh="改成自然中文。",
    )
    naturalness = fidelity.model_copy(
        update={
            "review_kind": "naturalness",
            "result_fingerprint": "n" * 64,
            "status": "needs_revision",
            "impression": "original_chinese_transcript",
            "scores": {
                "naturalness": 4.2,
                "persona": 4.5,
                "emotion": 4.4,
                "flow": 4.5,
            },
            "issues": [local_issue],
            "summary_zh": "全文自然，只有一个必须修正的局部病句。",
            "route": naturalness_route,
        }
    )

    calls = 0

    def complete_json(prompt, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        _kwargs["trace_sink"](
            llm_runtime.LlmCompletionTrace(
                profile_id="quality",
                model_id="quality-model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=5_000,
                timeout_seconds=300,
                reasoning_effort_requested="high",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        if prompt == localization_spoken_script.FINALIZATION_PROMPT:
            return {
                "section_id": "section_0001",
                "edits": [
                    {
                        "issue_id": "naturalness_0001",
                        "replacement": "用户从这里提出需求。",
                    }
                ],
            }
        if prompt == localization_spoken_script.FINALIZATION_FIDELITY_REGRESSION_PROMPT:
            return {
                "status": "passed",
                "issues": [],
                "summary_zh": "修改没有制造新的原意问题。",
            }
        if prompt == localization_spoken_script.FINALIZATION_NATURALNESS_REGRESSION_PROMPT:
            return {
                "impression": "original_chinese_transcript",
                "naturalness": 4.5,
                "persona": 4.5,
                "emotion": 4.5,
                "flow": 4.5,
                "issues": [],
                "summary_zh": "修改后中文自然。",
            }
        return {
            "status": "passed",
            "remaining_issue_ids": [],
            "summary_zh": "已修正。",
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )

    result = localization_spoken_script.finalize_localization_spoken_script(
        localization_spoken_script.LocalizationSpokenScriptFinalizationInput(
            script_operation_id="script_operation",
            fidelity_review_operation_id="fidelity_operation",
            naturalness_review_operation_id="naturalness_operation",
            source_lock=source,
            document_brief=brief,
            script=script,
            fidelity_review=fidelity,
            naturalness_review=naturalness,
            route=creation_route.model_copy(update={"phase": "spoken_script_finalization"}),
            post_fidelity_route=fidelity_route,
            post_naturalness_route=naturalness_route,
        ),
        max_revision_rounds=1,
    )

    assert result.quality_summary.status == "revised"
    assert result.revised_section_ids == ["section_0001"]
    assert result.content.sections[0].paragraphs == ["用户从这里提出需求。"]
    projected = localization_spoken_script.project_localization_spoken_script_review_result(naturalness)
    assert projected["summary"] == ("整体像中文原创口述，但仍有必须修正的局部表达。")


@pytest.mark.parametrize(
    ("localized_text", "kind", "excerpt"),
    [
        (
            "可以导入素材、生成完整场景；也能导入素材并生成完整场景。",
            "meaning",
            "也能导入素材并生成完整场景",
        ),
        (
            "我在工作室运行提示，然后描述场景并点击生成。",
            "addition",
            "描述场景并点击生成",
        ),
    ],
)
def test_finalization_regression_reports_new_defects_in_revised_sections(
    monkeypatch,
    localized_text,
    kind,
    excerpt,
):
    source, brief = _source_and_brief()
    creation_route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="high",
        output_format="markdown",
        prompt_strategy="adaptive",
    )
    fidelity_route = creation_route.model_copy(
        update={"phase": "fidelity_review", "output_format": "json"}
    )
    naturalness_route = fidelity_route.model_copy(
        update={"phase": "naturalness_review"}
    )
    script = localization_spoken_script.LocalizationSpokenScriptResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        result_fingerprint="s" * 64,
        dynamic_rule_ids=[],
        content=localization_spoken_script.LocalizationSpokenScriptContent(
            title="测试",
            sections=[
                localization_spoken_script.LocalizationSpokenScriptSection(
                    section_id="section_0001",
                    heading="正文",
                    paragraphs=[localized_text],
                )
            ],
        ),
        route=creation_route,
        llm_calls=[],
        quality_summary=None,
    )
    fidelity = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="fidelity",
        source_fingerprint=source.source_fingerprint,
        script_fingerprint=script.result_fingerprint,
        result_fingerprint="f" * 64,
        status="passed",
        impression="not_applicable",
        scores={},
        issues=[],
        summary_zh="旧问题已经关闭。",
        route=fidelity_route,
        llm_calls=[],
    )
    naturalness = fidelity.model_copy(
        update={
            "review_kind": "naturalness",
            "result_fingerprint": "n" * 64,
            "impression": "original_chinese_transcript",
            "scores": {
                "naturalness": 4.5,
                "persona": 4.5,
                "emotion": 4.5,
                "flow": 4.5,
            },
            "route": naturalness_route,
        }
    )
    request = localization_spoken_script.LocalizationSpokenScriptFinalizationInput(
        script_operation_id="script_operation",
        fidelity_review_operation_id="fidelity_operation",
        naturalness_review_operation_id="naturalness_operation",
        source_lock=source,
        document_brief=brief,
        script=script,
        fidelity_review=fidelity,
        naturalness_review=naturalness,
        route=creation_route.model_copy(
            update={"phase": "spoken_script_finalization"}
        ),
        post_fidelity_route=fidelity_route,
        post_naturalness_route=naturalness_route,
    )
    captured = {}

    def complete_json(prompt, payload, *, trace_sink, **_kwargs):
        captured["prompt"] = prompt
        captured["payload"] = payload
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=4_000,
                timeout_seconds=350,
                reasoning_effort_requested="high",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "status": "needs_revision",
            "issues": [
                {
                    "issue_id": "fidelity_regression_0001",
                    "severity": "high",
                    "sentence_id": (
                        "section_0001.paragraph_0001.sentence_0001"
                    ),
                    "kind": kind,
                    "excerpt": excerpt,
                    "reason_zh": "本轮修改制造了新的内容问题。",
                    "required_change_zh": "删除无依据或重复的信息。",
                }
            ],
            "summary_zh": "修改后仍有一个新问题。",
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )

    result = localization_spoken_script._review_finalization_regression(
        request=request,
        script=script,
        revised_section_ids=["section_0001"],
        route=fidelity_route,
        round_index=1,
        review_kind="fidelity",
    )

    assert captured["prompt"] == (
        localization_spoken_script.FINALIZATION_FIDELITY_REGRESSION_PROMPT
    )
    assert set(captured["payload"]) == {
        "source_full_text",
        "localized_full_text",
    }
    assert excerpt in captured["payload"]["localized_full_text"]
    assert result.status == "needs_revision"
    assert result.issues[0].kind == kind
    assert result.issues[0].excerpt == excerpt


def _obsolete_finalization_default_budget_allows_three_targeted_rounds(
    monkeypatch,
):
    source, brief = _source_and_brief()
    creation_route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="quality",
        model_id="quality-model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
        may_escalate=True,
    )
    script = localization_spoken_script.LocalizationSpokenScriptResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        result_fingerprint="s" * 64,
        dynamic_rule_ids=[],
        content=(
            localization_spoken_script.LocalizationSpokenScriptContent(
                title="测试",
                sections=[
                    localization_spoken_script.LocalizationSpokenScriptSection(
                        section_id="section_0001",
                        heading="正文",
                        paragraphs=[
                            "第一处需要修正。",
                            "第二处也需要修正。",
                            "第三处仍需要修正。",
                        ],
                    )
                ],
            )
        ),
        route=creation_route,
        llm_calls=[],
        quality_summary=None,
    )
    fidelity_route = creation_route.model_copy(
        update={
            "phase": "fidelity_review",
            "output_format": "json",
        }
    )
    naturalness_route = fidelity_route.model_copy(update={"phase": "naturalness_review"})

    def fidelity_issue(
        *,
        issue_id,
        excerpt,
        required_change,
    ):
        return localization_spoken_script.LocalizationSpokenScriptReviewIssue(
            issue_id=issue_id,
            severity="high",
            kind="meaning",
            excerpt=excerpt,
            reason_zh="局部事实不准确。",
            required_change_zh=required_change,
        )

    first_issue = fidelity_issue(
        issue_id="fidelity_0001",
        excerpt="第一处需要修正",
        required_change="改成第一处已经修正。",
    )
    second_issue = fidelity_issue(
        issue_id="fidelity_0002",
        excerpt="第二处也需要修正",
        required_change="改成第二处也已修正。",
    )
    third_issue = fidelity_issue(
        issue_id="fidelity_0003",
        excerpt="第三处仍需要修正",
        required_change="改成第三处也已修正。",
    )

    def review(
        *,
        kind,
        script_fingerprint,
        status,
        issues,
    ):
        return localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
            review_kind=kind,
            source_fingerprint=source.source_fingerprint,
            script_fingerprint=script_fingerprint,
            result_fingerprint=hashlib.sha256(
                f"{kind}:{script_fingerprint}:{status}:{','.join(item.issue_id for item in issues)}".encode()
            ).hexdigest(),
            status=status,
            impression=("not_applicable" if kind == "fidelity" else "original_chinese_transcript"),
            scores=(
                {}
                if kind == "fidelity"
                else {
                    "naturalness": 4.5,
                    "persona": 4.5,
                    "emotion": 4.5,
                    "flow": 4.5,
                }
            ),
            issues=issues,
            summary_zh="通过。" if status == "passed" else "需要修正。",
            route=(fidelity_route if kind == "fidelity" else naturalness_route),
            llm_calls=[],
        )

    initial_fidelity = review(
        kind="fidelity",
        script_fingerprint=script.result_fingerprint,
        status="needs_revision",
        issues=[first_issue],
    )
    initial_naturalness = review(
        kind="naturalness",
        script_fingerprint=script.result_fingerprint,
        status="passed",
        issues=[],
    )
    revision_rounds = []

    closure_review_count = 0

    def complete_json(prompt, payload, **_kwargs):
        nonlocal closure_review_count
        if prompt == localization_spoken_script.FINALIZATION_CLOSURE_PROMPT:
            closure_review_count += 1
            assert set(payload) == {"issues", "revised_sections"}
            assert [item["issue_id"] for item in payload["issues"]] == ["fidelity_0003"]
            _kwargs["trace_sink"](
                llm_runtime.LlmCompletionTrace(
                    profile_id="quality",
                    model_id="quality-model",
                    provider_host="local",
                    request_chars=100,
                    request_body_bytes=120,
                    max_tokens=2_000,
                    timeout_seconds=300,
                    reasoning_effort_requested="low",
                    reasoning_control_applied=True,
                    duration_ms=20,
                    finish_reason="stop",
                )
            )
            return {
                "status": "passed",
                "remaining_issue_ids": [],
                "summary_zh": "第二处既定问题已落实。",
            }
        revision_rounds.append(payload["revision_round"])
        issue_id = payload["issues"][0]["issue_id"]
        return {
            "section_id": "section_0001",
            "edits": [
                {
                    "issue_id": issue_id,
                    "replacement": (
                        "第一处已经修正。"
                        if issue_id == "fidelity_0001"
                        else ("第二处也已修正。" if issue_id == "fidelity_0002" else "第三处也已修正。")
                    ),
                }
            ],
        }

    fidelity_review_count = 0

    def post_fidelity(*, script, **_kwargs):
        nonlocal fidelity_review_count
        fidelity_review_count += 1
        if fidelity_review_count == 1:
            return review(
                kind="fidelity",
                script_fingerprint=script.result_fingerprint,
                status="needs_revision",
                issues=[second_issue],
            )
        return review(
            kind="fidelity",
            script_fingerprint=script.result_fingerprint,
            status="needs_revision",
            issues=[third_issue],
        )

    def post_naturalness(*, script, **_kwargs):
        return review(
            kind="naturalness",
            script_fingerprint=script.result_fingerprint,
            status="passed",
            issues=[],
        )

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )
    monkeypatch.setattr(
        localization_spoken_script,
        "review_localization_spoken_script_fidelity",
        post_fidelity,
    )
    monkeypatch.setattr(
        localization_spoken_script,
        "review_localization_spoken_script_naturalness",
        post_naturalness,
    )

    result = localization_spoken_script.finalize_localization_spoken_script(
        localization_spoken_script.LocalizationSpokenScriptFinalizationInput(
            script_operation_id="script_operation",
            fidelity_review_operation_id="fidelity_operation",
            naturalness_review_operation_id="naturalness_operation",
            source_lock=source,
            document_brief=brief,
            script=script,
            fidelity_review=initial_fidelity,
            naturalness_review=initial_naturalness,
            route=creation_route.model_copy(update={"phase": "spoken_script_finalization"}),
            post_fidelity_route=fidelity_route,
            post_naturalness_route=naturalness_route,
        ),
        max_revision_rounds=(localization_spoken_script.FINALIZATION_MAX_REVISION_ROUNDS),
    )

    assert revision_rounds == [1, 2, 3]
    assert fidelity_review_count == 2
    assert closure_review_count == 1
    assert result.quality_summary.status == "revised"
    assert result.revised_section_ids == ["section_0001"]
    assert result.content.sections[0].paragraphs == [
        "第一处已经修正。",
        "第二处也已修正。",
        "第三处也已修正。",
    ]


def _obsolete_finalization_falls_back_when_cheap_post_review_is_invalid(
    monkeypatch,
):
    source, brief = _source_and_brief()
    script = localization_spoken_script.LocalizationSpokenScriptResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        result_fingerprint="s" * 64,
        dynamic_rule_ids=[],
        content=(
            localization_spoken_script.LocalizationSpokenScriptContent(
                title="测试",
                sections=[
                    localization_spoken_script.LocalizationSpokenScriptSection(
                        section_id="section_0001",
                        heading="开场",
                        paragraphs=["这里把 Skill 打开。"],
                    )
                ],
            )
        ),
        route=LocalizationAiPhaseRoute(
            phase="spoken_script_creation",
            profile_id="quality",
            model_id="quality-model",
            reasoning_effort="high",
            output_format="markdown",
            prompt_strategy="adaptive",
        ),
        llm_calls=[],
        quality_summary=None,
    )
    fidelity_route = LocalizationAiPhaseRoute(
        phase="fidelity_review",
        profile_id="cheap",
        model_id="cheap-model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    naturalness_route = fidelity_route.model_copy(update={"phase": "naturalness_review"})
    fidelity_issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="f1",
        severity="high",
        kind="term",
        excerpt="这里把 Skill 打开",
        reason_zh="工具关系错误。",
        required_change_zh="改成使用 Skill。",
    )
    fidelity = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="fidelity",
        source_fingerprint=source.source_fingerprint,
        script_fingerprint=script.result_fingerprint,
        result_fingerprint="f" * 64,
        status="needs_revision",
        impression="not_applicable",
        scores={},
        issues=[fidelity_issue],
        summary_zh="需要修正工具关系。",
        route=fidelity_route,
        llm_calls=[],
    )
    naturalness = fidelity.model_copy(
        update={
            "review_kind": "naturalness",
            "result_fingerprint": "n" * 64,
            "status": "passed",
            "impression": "original_chinese_transcript",
            "scores": {
                "naturalness": 4.2,
                "persona": 4.2,
                "emotion": 4.2,
                "flow": 4.2,
            },
            "issues": [],
            "summary_zh": "自然。",
            "route": naturalness_route,
        }
    )

    captured_payload = {}

    def complete_json(_prompt, payload, **_kwargs):
        captured_payload.update(payload)
        return {
            "section_id": "section_0001",
            "edits": [
                {
                    "issue_id": "f1",
                    "replacement": "这里使用 Skill。",
                }
            ],
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_json,
    )

    post_fidelity_constraints = []

    def passed_fidelity(
        *,
        script,
        verified_evidence_constraints,
        **_kwargs,
    ):
        post_fidelity_constraints.extend(verified_evidence_constraints)
        return fidelity.model_copy(
            update={
                "script_fingerprint": script.result_fingerprint,
                "result_fingerprint": "p" * 64,
                "status": "passed",
                "issues": [],
                "summary_zh": "原意完整。",
            }
        )

    adjudicated = naturalness.model_copy(
        update={
            "result_fingerprint": "a" * 64,
            "route": naturalness_route.model_copy(
                update={
                    "profile_id": "quality",
                    "model_id": "quality-model",
                    "reasoning_effort": "high",
                }
            ),
        }
    )

    def review_naturalness(*, route, **_kwargs):
        if route.profile_id == "cheap":
            raise ValueError("便宜模型连续返回非法 JSON")
        return adjudicated

    monkeypatch.setattr(
        localization_spoken_script,
        "review_localization_spoken_script_fidelity",
        passed_fidelity,
    )
    monkeypatch.setattr(
        localization_spoken_script,
        "review_localization_spoken_script_naturalness",
        review_naturalness,
    )
    result = localization_spoken_script.finalize_localization_spoken_script(
        localization_spoken_script.LocalizationSpokenScriptFinalizationInput(
            script_operation_id="script_operation",
            fidelity_review_operation_id="fidelity_operation",
            naturalness_review_operation_id="naturalness_operation",
            source_lock=source,
            document_brief=brief,
            script=script,
            fidelity_review=fidelity,
            naturalness_review=naturalness,
            route=script.route.model_copy(update={"phase": "spoken_script_finalization"}),
            post_fidelity_route=fidelity_route,
            post_naturalness_route=naturalness_route,
            post_naturalness_adjudication_route=(adjudicated.route),
        ),
        max_revision_rounds=2,
    )

    assert result.quality_summary.status == "revised"
    assert result.content.sections[0].paragraphs == ["这里使用 Skill。"]
    assert result.post_naturalness_review == adjudicated
    assert "document_brief" not in captured_payload
    assert set(captured_payload) == {
        "revision_round",
        "current_section",
        "issues",
        "previous_chinese_tail",
        "next_section_outline",
        "validation_feedback",
    }
    assert captured_payload["issues"][0]["whole_sentence_deletion_allowed"] is False
    assert captured_payload["issues"][0]["sentence_move_allowed"] is False
    assert captured_payload["issues"][0]["editable_sentence_id"] == "section_0001.paragraph_0001.sentence_0001"
    assert captured_payload["issues"][0]["allowed_operations"] == ["replace"]
    assert "whole_sentence_deletion_allowed" in (localization_spoken_script.FINALIZATION_PROMPT)
    assert "target_sentence_id" in (localization_spoken_script.FINALIZATION_PROMPT)
    assert "insert_before" in (localization_spoken_script.FINALIZATION_PROMPT)
    assert any("终审已确认问题片段" in item and "改成使用 Skill" in item for item in post_fidelity_constraints)


def test_naturalness_review_normalizes_zero_to_one_scores(
    monkeypatch,
):
    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
    )

    def complete_section(*_args, trace_sink, **_kwargs):
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=5_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "chunk_id": "chunk_0001",
            "suggested_title": "今天聊个简单的事",
            "paragraphs": ["大家好，今天聊个简单的事。"],
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_section,
    )
    script = localization_spoken_script.generate_localization_spoken_script(_script_request(source, brief, route))

    def complete_review(*_args, trace_sink, **_kwargs):
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=5_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "impression": "original_chinese_transcript",
            "naturalness": 0.8,
            "persona": 0.82,
            "emotion": 0.9,
            "flow": 0.84,
            "issues": [],
            "summary_zh": "像自然中文口述。",
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_review,
    )
    review = localization_spoken_script.review_localization_spoken_script_naturalness(
        source_fingerprint=source.source_fingerprint,
        script=script,
        route=LocalizationAiPhaseRoute(
            phase="naturalness_review",
            profile_id="profile",
            model_id="model",
            reasoning_effort="low",
            output_format="json",
            prompt_strategy="adaptive",
        ),
    )

    assert review.status == "passed"
    assert review.scores == {
        "naturalness": 4.0,
        "persona": 4.1,
        "emotion": 4.5,
        "flow": 4.2,
    }


def test_naturalness_review_normalizes_accidental_ten_point_scores(
    monkeypatch,
):
    source, brief = _source_and_brief()
    script = localization_spoken_script.LocalizationSpokenScriptResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        result_fingerprint="s" * 64,
        dynamic_rule_ids=[],
        content=(
            localization_spoken_script.LocalizationSpokenScriptContent(
                title="测试",
                sections=[
                    localization_spoken_script.LocalizationSpokenScriptSection(
                        section_id="section_0001",
                        heading="正文",
                        paragraphs=["这是一段自然口播。"],
                    )
                ],
            )
        ),
        route=LocalizationAiPhaseRoute(
            phase="spoken_script_creation",
            profile_id="profile",
            model_id="model",
            reasoning_effort="low",
            output_format="json",
            prompt_strategy="adaptive",
        ),
        llm_calls=[],
        quality_summary=None,
    )

    def complete_review(*_args, trace_sink, **_kwargs):
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=5_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "impression": "localized_translation",
            "naturalness": 6.3,
            "persona": 6.8,
            "emotion": 6.9,
            "flow": 6.1,
            "issues": [],
            "summary_zh": "仍有本土化痕迹。",
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_review,
    )
    review = localization_spoken_script.review_localization_spoken_script_naturalness(
        source_fingerprint=source.source_fingerprint,
        script=script,
        route=LocalizationAiPhaseRoute(
            phase="naturalness_review",
            profile_id="profile",
            model_id="model",
            reasoning_effort="low",
            output_format="json",
            prompt_strategy="adaptive",
        ),
    )

    assert review.scores == {
        "naturalness": 3.15,
        "persona": 3.4,
        "emotion": 3.45,
        "flow": 3.05,
    }


def test_naturalness_rejects_localized_impression_despite_usable_scores(
    monkeypatch,
):
    source, brief = _source_and_brief()
    script = localization_spoken_script.LocalizationSpokenScriptResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        result_fingerprint="s" * 64,
        content=(
            localization_spoken_script.LocalizationSpokenScriptContent(
                title="测试",
                sections=[
                    localization_spoken_script.LocalizationSpokenScriptSection(
                        section_id="section_0001",
                        heading="正文",
                        paragraphs=["这是一段可以使用的自然口播。"],
                    )
                ],
            )
        ),
    )

    def complete_review(*_args, trace_sink, **_kwargs):
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=5_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "impression": "localized_translation",
            "naturalness": 3.9,
            "persona": 4.2,
            "emotion": 4.1,
            "flow": 3.8,
            "issues": [
                {
                    "issue_id": "n1",
                    "sentence_id": ("section_0001.paragraph_0001.sentence_0001"),
                    "severity": "medium",
                    "excerpt": "可以使用",
                    "reason_zh": "还能更口语",
                    "required_change_zh": "可选润色",
                }
            ],
            "summary_zh": "达到可用线，仍有一条建议。",
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        complete_review,
    )
    review = localization_spoken_script.review_localization_spoken_script_naturalness(
        source_fingerprint=source.source_fingerprint,
        script=script,
        route=LocalizationAiPhaseRoute(
            phase="naturalness_review",
            profile_id="profile",
            model_id="model",
            reasoning_effort="low",
            output_format="json",
            prompt_strategy="adaptive",
        ),
    )

    assert review.status == "needs_revision"
    assert len(review.issues) == 1


def _obsolete_finalization_resumes_only_failed_section(
    monkeypatch,
):
    source, brief = _source_and_brief()
    second = localization_document_brief.LocalizationDocumentSection(
        section_id="section_0002",
        title="结尾",
        function_zh="收住主题。",
        source_cue_ids=["cue_0001"],
    )
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={
                    "structure": [*brief.content.structure, second],
                    "emotional_arc": [
                        *brief.content.emotional_arc,
                        localization_document_brief.LocalizationEmotionalArcItem(
                            section_id="section_0002",
                            emotion_zh="平静",
                            intensity=2,
                            speech_acts=["总结"],
                        ),
                    ],
                }
            ),
            "result_fingerprint": "c" * 64,
        }
    )
    creation_route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    script = localization_spoken_script.LocalizationSpokenScriptResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        result_fingerprint="a" * 64,
        dynamic_rule_ids=[],
        content=(
            localization_spoken_script.LocalizationSpokenScriptContent(
                title="测试",
                sections=[
                    localization_spoken_script.LocalizationSpokenScriptSection(
                        section_id="section_0001",
                        heading="开场",
                        paragraphs=["第一段有点书面。"],
                    ),
                    localization_spoken_script.LocalizationSpokenScriptSection(
                        section_id="section_0002",
                        heading="结尾",
                        paragraphs=["第二段也有点书面。"],
                    ),
                ],
            )
        ),
        route=creation_route,
        llm_calls=[],
        quality_summary=(
            localization_spoken_script.LocalizationSpokenScriptQualitySummary(
                status="passed",
                section_count=2,
                paragraph_count=2,
                chinese_character_count=14,
                section_coverage_complete=True,
                output_format="json",
                model_call_count=1,
            )
        ),
    )
    review_route = LocalizationAiPhaseRoute(
        phase="naturalness_review",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    naturalness = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="naturalness",
        source_fingerprint=source.source_fingerprint,
        script_fingerprint=script.result_fingerprint,
        result_fingerprint="n" * 64,
        status="passed",
        impression="original_chinese_transcript",
        scores={"naturalness": 4.2},
        issues=[],
        summary_zh="中文自然度通过。",
        route=review_route,
        llm_calls=[],
    )
    fidelity = naturalness.model_copy(
        update={
            "review_kind": "fidelity",
            "result_fingerprint": "f" * 64,
            "status": "needs_revision",
            "impression": "not_applicable",
            "scores": {},
            "issues": [
                localization_spoken_script.LocalizationSpokenScriptReviewIssue(
                    issue_id="f1",
                    severity="medium",
                    kind="meaning",
                    excerpt="第一段有点书面",
                    reason_zh="第一段原意有偏差。",
                    required_change_zh="修正第一段原意。",
                ),
                localization_spoken_script.LocalizationSpokenScriptReviewIssue(
                    issue_id="f2",
                    severity="medium",
                    kind="meaning",
                    excerpt="第二段也有点书面",
                    reason_zh="第二段原意有偏差。",
                    required_change_zh="修正第二段原意。",
                ),
            ],
            "summary_zh": "两处原意需要修改。",
        }
    )
    final_route = LocalizationAiPhaseRoute(
        phase="spoken_script_finalization",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    request = localization_spoken_script.LocalizationSpokenScriptFinalizationInput(
        script_operation_id="script_operation",
        fidelity_review_operation_id="fidelity_operation",
        naturalness_review_operation_id="naturalness_operation",
        source_lock=source,
        document_brief=brief,
        script=script,
        fidelity_review=fidelity,
        naturalness_review=naturalness,
        route=final_route,
        post_fidelity_route=final_route.model_copy(update={"phase": "fidelity_review"}),
        post_naturalness_route=review_route,
    )
    call_count = 0

    def first_attempt(*_args, trace_sink, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("第二个章节暂时失败")
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=5_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "section_id": "section_0001",
            "edits": [
                {
                    "issue_id": "f1",
                    "replacement": "第一段现在顺口了。",
                }
            ],
        }

    checkpoints = []
    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        first_attempt,
    )
    try:
        localization_spoken_script.finalize_localization_spoken_script(
            request,
            max_revision_rounds=2,
            on_section_checkpoint=checkpoints.append,
        )
    except RuntimeError:
        pass

    assert checkpoints[-1].next_section_index == 1
    assert checkpoints[-1].completed_sections[0].paragraphs == ["第一段现在顺口了。"]

    def resumed_attempt(*_args, trace_sink, **_kwargs):
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=5_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "section_id": "section_0002",
            "edits": [
                {
                    "issue_id": "f2",
                    "replacement": "第二段单独重试后也顺口了。",
                }
            ],
        }

    def passed_review(*, script, route, **_kwargs):
        return naturalness.model_copy(
            update={
                "review_kind": route.phase.replace("_review", ""),
                "script_fingerprint": script.result_fingerprint,
                "result_fingerprint": ("p" * 64 if route.phase == "fidelity_review" else "q" * 64),
                "status": "passed",
                "impression": ("not_applicable" if route.phase == "fidelity_review" else "original_chinese_transcript"),
                "scores": {},
                "issues": [],
                "summary_zh": "通过。",
                "llm_calls": [],
            }
        )

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        resumed_attempt,
    )
    monkeypatch.setattr(
        localization_spoken_script,
        "review_localization_spoken_script_fidelity",
        passed_review,
    )

    def failed_naturalness_once(**_kwargs):
        raise RuntimeError("自然度复核暂时返回非法结果")

    monkeypatch.setattr(
        localization_spoken_script,
        "review_localization_spoken_script_naturalness",
        failed_naturalness_once,
    )
    review_checkpoints = []
    try:
        localization_spoken_script.finalize_localization_spoken_script(
            request,
            max_revision_rounds=2,
            resume_checkpoint=checkpoints[-1],
            on_section_checkpoint=review_checkpoints.append,
        )
    except RuntimeError:
        pass

    assert review_checkpoints[-1].review_stage == "post_fidelity"
    assert review_checkpoints[-1].post_fidelity_review is not None

    def fidelity_must_not_repeat(**_kwargs):
        raise AssertionError("已通过的原意复核不应重复调用")

    monkeypatch.setattr(
        localization_spoken_script,
        "review_localization_spoken_script_fidelity",
        fidelity_must_not_repeat,
    )
    monkeypatch.setattr(
        localization_spoken_script,
        "review_localization_spoken_script_naturalness",
        passed_review,
    )
    result = localization_spoken_script.finalize_localization_spoken_script(
        request,
        max_revision_rounds=2,
        resume_checkpoint=review_checkpoints[-1],
    )

    assert result.content.sections[0].paragraphs == ["第一段现在顺口了。"]
    assert result.content.sections[0].heading == "开场"
    assert result.content.sections[1].paragraphs == ["第二段单独重试后也顺口了。"]
    assert result.quality_summary.status == "revised"
    assert result.quality_summary.model_call_count == 2


def test_sentence_finalization_edits_once_and_resumes_from_snapshot(
    monkeypatch,
):
    source, brief = _source_and_brief()
    creation_context = _creation_context(source, brief)
    creation_route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
    )

    def generate_once(*_args, trace_sink, **_kwargs):
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=4_500,
                timeout_seconds=600,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "chunk_id": "chunk_0001",
            "suggested_title": "测试",
            "paragraphs": ["这里把 Skill 打开。"],
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        generate_once,
    )
    script = localization_spoken_script.generate_localization_spoken_script(
        _script_request(source, brief, creation_route)
    )
    fidelity_route = creation_route.model_copy(
        update={
            "phase": "fidelity_review",
            "output_format": "json",
        }
    )
    naturalness_route = fidelity_route.model_copy(update={"phase": "naturalness_review"})
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="high",
        kind="term",
        excerpt="这里把 Skill 打开",
        reason_zh="工具关系错误。",
        required_change_zh="改成使用 Skill。",
    )
    fidelity = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="fidelity",
        source_fingerprint=source.source_fingerprint,
        script_fingerprint=script.result_fingerprint,
        result_fingerprint="d" * 64,
        status="needs_revision",
        impression="not_applicable",
        scores={},
        issues=[issue],
        summary_zh="一处关系错误。",
        route=fidelity_route,
        llm_calls=[],
    )
    naturalness_issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="naturalness_0001",
        severity="medium",
        kind="naturalness",
        excerpt="这里把 Skill 打开",
        reason_zh="中文表达像逐词翻译。",
        required_change_zh="改成自然的中文工具关系表达。",
    )
    naturalness = fidelity.model_copy(
        update={
            "review_kind": "naturalness",
            "result_fingerprint": "e" * 64,
            "status": "needs_revision",
            "impression": "localized_translation",
            "scores": {
                "naturalness": 3.7,
                "persona": 4.2,
                "emotion": 4.2,
                "flow": 4.2,
            },
            "issues": [naturalness_issue],
            "summary_zh": "整体可用，但有一处翻译痕迹。",
            "route": naturalness_route,
        }
    )
    request = localization_spoken_script.LocalizationSpokenScriptFinalizationInput(
        script_operation_id="script_operation",
        fidelity_review_operation_id="fidelity_operation",
        naturalness_review_operation_id="naturalness_operation",
        source_lock=source,
        document_brief=brief,
        creation_context=creation_context,
        script=script,
        fidelity_review=fidelity,
        naturalness_review=naturalness,
        route=creation_route.model_copy(
            update={
                "phase": "spoken_script_finalization",
                "output_format": "json",
            }
        ),
        post_fidelity_route=fidelity_route,
        post_naturalness_route=naturalness_route,
    )
    finalization_payloads = []

    def revise_sentence(*_args, trace_sink, **_kwargs):
        if "current_section" not in _args[1]:
            raise RuntimeError("模拟复核中断")
        finalization_payloads.append(_args[1])
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=4_500,
                timeout_seconds=600,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "section_id": "section_0001",
            "edits": [
                {
                    "issue_id": _args[1]["issues"][0]["issue_id"],
                    "replacement": "这里使用 Skill。",
                }
            ],
        }

    checkpoints = []
    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        revise_sentence,
    )

    with pytest.raises(RuntimeError, match="模拟复核中断"):
        localization_spoken_script.finalize_localization_spoken_script(
            request,
            on_section_checkpoint=checkpoints.append,
        )

    assert len(finalization_payloads) == 1
    assert finalization_payloads[0]["current_section"]["paragraphs"] == ["这里把 Skill 打开。"]
    assert finalization_payloads[0]["source_section"] == {
        "section_id": "section_0001",
        "source_cues": [{"cue_id": "cue_0001", "text": "Hello world.", "quality_flags": []}],
    }
    assert finalization_payloads[0]["issues"][0]["issue_id"] == ("fidelity_0001+naturalness_0001")
    assert checkpoints[-1].completed_sections[0].paragraphs == ["这里使用 Skill。"]

    def close_without_repeating_edit(prompt, *_args, trace_sink, **_kwargs):
        assert "current_section" not in _args[0]
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=2_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        if prompt == localization_spoken_script.FINALIZATION_FIDELITY_REGRESSION_PROMPT:
            return {
                "status": "passed",
                "issues": [],
                "summary_zh": "修改没有制造新的原意问题。",
            }
        if prompt == localization_spoken_script.FINALIZATION_NATURALNESS_REGRESSION_PROMPT:
            return {
                "impression": "original_chinese_transcript",
                "naturalness": 4.2,
                "persona": 4.2,
                "emotion": 4.2,
                "flow": 4.2,
                "issues": [],
                "summary_zh": "修改后中文自然。",
            }
        return {
            "status": "passed",
            "remaining_issue_ids": [],
            "summary_zh": "已修正。",
        }

    def passed_fidelity(*, script, **_kwargs):
        return fidelity.model_copy(
            update={
                "script_fingerprint": script.result_fingerprint,
                "result_fingerprint": "f" * 64,
                "status": "passed",
                "issues": [],
                "summary_zh": "原意通过。",
            }
        )

    def passed_naturalness(*, script, **_kwargs):
        return naturalness.model_copy(
            update={
                "script_fingerprint": script.result_fingerprint,
                "result_fingerprint": "a" * 64,
                "status": "passed",
                "impression": "original_chinese_transcript",
                "scores": {
                    "naturalness": 4.2,
                    "persona": 4.2,
                    "emotion": 4.2,
                    "flow": 4.2,
                },
                "issues": [],
            }
        )

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        close_without_repeating_edit,
    )
    monkeypatch.setattr(
        localization_spoken_script,
        "review_localization_spoken_script_fidelity",
        passed_fidelity,
    )
    monkeypatch.setattr(
        localization_spoken_script,
        "review_localization_spoken_script_naturalness",
        passed_naturalness,
    )
    result = localization_spoken_script.finalize_localization_spoken_script(
        request,
        resume_checkpoint=checkpoints[-1],
    )

    assert result.quality_summary.status == "revised"
    assert result.content.sections[0].paragraphs == ["这里使用 Skill。"]
    assert result.quality_summary.model_call_count == 5


def test_sentence_finalization_warns_instead_of_guessing_unlocatable_issue(
    monkeypatch,
):
    source, brief = _source_and_brief()
    creation_context = _creation_context(source, brief)
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_creation",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="markdown",
        prompt_strategy="adaptive",
    )

    def generate(*_args, trace_sink, **_kwargs):
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=10,
                request_body_bytes=20,
                max_tokens=4_500,
                timeout_seconds=600,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=10,
                finish_reason="stop",
            )
        )
        return {
            "chunk_id": "chunk_0001",
            "suggested_title": "测试",
            "paragraphs": ["这是正确内容。"],
        }

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        generate,
    )
    script = localization_spoken_script.generate_localization_spoken_script(_script_request(source, brief, route))
    review_route = route.model_copy(update={"phase": "fidelity_review", "output_format": "json"})
    issue = localization_spoken_script.LocalizationSpokenScriptReviewIssue(
        issue_id="fidelity_0001",
        severity="high",
        kind="omission",
        excerpt="既不在英文也不在中文的内容",
        reason_zh="无法定位。",
        required_change_zh="补回。",
    )
    fidelity = localization_spoken_script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="fidelity",
        source_fingerprint=source.source_fingerprint,
        script_fingerprint=script.result_fingerprint,
        result_fingerprint="b" * 64,
        status="needs_revision",
        impression="not_applicable",
        scores={},
        issues=[issue],
        summary_zh="无法定位。",
        route=review_route,
        llm_calls=[],
    )
    naturalness = fidelity.model_copy(
        update={
            "review_kind": "naturalness",
            "result_fingerprint": "c" * 64,
            "status": "passed",
            "impression": "original_chinese_transcript",
            "scores": {"naturalness": 4.2},
            "issues": [],
            "route": review_route.model_copy(update={"phase": "naturalness_review"}),
        }
    )

    def no_model(*_args, **_kwargs):
        raise AssertionError("无法定位时不应猜测修改")

    monkeypatch.setattr(
        localization_spoken_script.llm_runtime,
        "complete_json",
        no_model,
    )
    result = localization_spoken_script.finalize_localization_spoken_script(
        localization_spoken_script.LocalizationSpokenScriptFinalizationInput(
            script_operation_id="script_operation",
            fidelity_review_operation_id="fidelity_operation",
            naturalness_review_operation_id="naturalness_operation",
            source_lock=source,
            document_brief=brief,
            creation_context=creation_context,
            script=script,
            fidelity_review=fidelity,
            naturalness_review=naturalness,
            route=route.model_copy(
                update={
                    "phase": "spoken_script_finalization",
                    "output_format": "json",
                }
            ),
            post_fidelity_route=review_route,
            post_naturalness_route=naturalness.route,
        )
    )

    assert result.quality_summary.status == "warning"
    assert result.content == script.content
    assert result.quality_summary.model_call_count == 0
