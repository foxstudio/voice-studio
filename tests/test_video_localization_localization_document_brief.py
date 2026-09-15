from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.brief_stage_fixtures import stage_candidate, staged_candidates


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    localization_context_intent,
    localization_document_brief,
    localization_source,
)
from app.services import llm_runtime  # noqa: E402
from app.services.localization_ai_policy import (  # noqa: E402
    LocalizationAiPhaseRoute,
)
from tests.test_video_localization_localization_context_intent import (  # noqa: E402
    _draft,
    _requirements,
)


def _inputs():
    draft = _draft()
    source = localization_source.DEFAULT_LOCALIZATION_PIPELINE.lock_source(
        localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(
            draft
        )
    )
    context_request = (
        localization_context_intent
        .build_localization_context_intent_input(
            draft,
            source_lock=source,
            upstream_operation_id="source_operation",
            requirements_profile=_requirements(),
            selection_source="project_default",
        )
    )
    context = (
        localization_context_intent
        .DEFAULT_LOCALIZATION_CONTEXT_INTENT_PIPELINE.lock(
            context_request
        )
    )
    route = LocalizationAiPhaseRoute(
        phase="document_understanding",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    return source, context, route


def test_adaptive_document_brief_records_dynamic_rules():
    source, context, route = _inputs()
    request = localization_document_brief.LocalizationDocumentBriefInput(
        source_operation_id="source_operation",
        context_operation_id="context_operation",
        source_lock=source,
        context_intent=context,
        route=route,
    )

    adaptive = localization_document_brief.build_document_brief_adaptive_rules(request)
    rule_ids = adaptive.rule_ids
    assert "reuse_asr_speaker_style" in rule_ids
    assert "preserve_speakable_persona" in rule_ids
    assert "resolve_upstream_source_uncertainties" in rule_ids
    assert "pragmatic_cultural_equivalence" in rule_ids
    assert (
        localization_document_brief.PROMPT_VERSION
        == "localization-document-brief-prompt-v14"
    )


def test_document_brief_adds_visual_question_for_upstream_uncertainty():
    source, context, route = _inputs()
    request = localization_document_brief.LocalizationDocumentBriefInput(
        source_operation_id="source_operation",
        context_operation_id="context_operation",
        source_lock=source,
        context_intent=context,
        route=route,
    )
    content = localization_document_brief.LocalizationDocumentBriefContent(
        purpose="解释内容。",
        audience="中文观众",
        structure=[{
            "section_id": "section_0001",
            "title": "内容",
            "function_zh": "解释内容。",
            "source_cue_ids": ["cue_0001"],
        }],
        speaker_profile={
            "identity_zh": "讲述者",
            "expertise_zh": "未知",
            "audience_distance_zh": "直接交流",
            "rhythm_zh": "自然",
            "stable_traits_zh": ["清楚"],
        },
        emotional_arc=[{
            "section_id": "section_0001",
            "emotion_zh": "平静",
            "intensity": 1,
            "speech_acts": ["说明"],
        }],
        immutable_facts=[{
            "fact_id": "fact_0001",
            "statement_zh": "出现一句话。",
            "source_cue_ids": ["cue_0001"],
        }],
        cultural_adaptation_rules=["忠实原意"],
        disfluency_policy_zh="保守处理。",
    )

    result = localization_document_brief._ensure_source_uncertainty_questions(
        request,
        content,
    )

    assert len(result.evidence_questions) == 1
    assert result.evidence_questions[0].kind == "visual"
    assert result.evidence_questions[0].source_cue_ids == ["cue_0001"]
    assert "world" in result.evidence_questions[0].question_zh


def test_document_brief_turns_model_speech_candidate_into_visual_question():
    source, context, route = _inputs()
    request = localization_document_brief.LocalizationDocumentBriefInput(
        source_operation_id="source_operation",
        context_operation_id="context_operation",
        source_lock=source,
        context_intent=context,
        route=route,
    )
    content = localization_document_brief.LocalizationDocumentBriefContent(
        purpose="解释内容。",
        audience="中文观众",
        structure=[{
            "section_id": "section_0001",
            "title": "内容",
            "function_zh": "解释内容。",
            "source_cue_ids": ["cue_0001"],
        }],
        speaker_profile={
            "identity_zh": "讲述者",
            "expertise_zh": "未知",
            "audience_distance_zh": "直接交流",
            "rhythm_zh": "自然",
            "stable_traits_zh": ["清楚"],
        },
        emotional_arc=[{
            "section_id": "section_0001",
            "emotion_zh": "强烈",
            "intensity": 4,
            "speech_acts": ["反应"],
        }],
        immutable_facts=[{
            "fact_id": "fact_0001",
            "statement_zh": "出现一段声音。",
            "source_cue_ids": ["cue_0001"],
        }],
        cultural_adaptation_rules=["忠实原意"],
        disfluency_policy_zh="保守处理。",
        speech_qualification_candidates=[{
            "candidate_id": "speech_candidate_0001",
            "source_cue_ids": ["cue_0001"],
            "reason_zh": "可能是角色表演而非语言。",
        }],
    )

    result = localization_document_brief._ensure_speech_qualification_questions(
        request,
        content,
    )

    assert len(result.evidence_questions) == 1
    question = result.evidence_questions[0]
    assert question.purpose == "speech_qualification"
    assert question.speech_candidate_id == "speech_candidate_0001"
    assert source.input.cues[0].text in question.question_zh
    assert "Demo 片段本身不是跳过理由" in question.question_zh


def test_document_brief_keeps_uncertainty_question_owned_by_hint_cues():
    source, context, route = _inputs()
    cue_template = source.input.cues[0]
    cues = [
        cue_template.model_copy(
            update={
                "cue_id": f"cue_{index:04d}",
                "start_ms": (index - 1) * 1_000,
                "end_ms": index * 1_000,
            }
        )
        for index in range(1, 6)
    ]
    source = source.model_copy(
        update={"input": source.input.model_copy(update={"cues": cues})}
    )
    document_context = context.input.document_context.model_copy(
        update={
            "source_uncertainties": [
                localization_context_intent.LocalizationSourceUncertainty(
                    term="Basha",
                    reason="罕见词可能来自异语台词的错误听写。",
                    source_cue_ids=["cue_0003"],
                )
            ]
        }
    )
    context = context.model_copy(
        update={
            "input": context.input.model_copy(
                update={"document_context": document_context}
            )
        }
    )
    request = localization_document_brief.LocalizationDocumentBriefInput(
        source_operation_id="source_operation",
        context_operation_id="context_operation",
        source_lock=source,
        context_intent=context,
        route=route,
    )
    content = localization_document_brief.LocalizationDocumentBriefContent(
        purpose="解释内容。",
        audience="中文观众",
        structure=[{
            "section_id": "section_0001",
            "title": "内容",
            "function_zh": "解释内容。",
            "source_cue_ids": [cue.cue_id for cue in cues],
        }],
        speaker_profile={
            "identity_zh": "讲述者",
            "expertise_zh": "未知",
            "audience_distance_zh": "直接交流",
            "rhythm_zh": "自然",
            "stable_traits_zh": ["清楚"],
        },
        emotional_arc=[{
            "section_id": "section_0001",
            "emotion_zh": "平静",
            "intensity": 1,
            "speech_acts": ["说明"],
        }],
        immutable_facts=[{
            "fact_id": "fact_0001",
            "statement_zh": "出现一句话。",
            "source_cue_ids": ["cue_0003"],
        }],
        cultural_adaptation_rules=["忠实原意"],
        disfluency_policy_zh="保守处理。",
        evidence_questions=[{
            "question_id": "question_0001",
            "kind": "visual",
            "question_zh": "读取待核实词所在画面字幕。",
            "source_cue_ids": ["cue_0003"],
            "reason_zh": "需要核对异语台词。",
        }],
    )

    result = localization_document_brief._ensure_source_uncertainty_questions(
        request,
        content,
    )

    assert result.evidence_questions[0].source_cue_ids == ["cue_0003"]


def test_document_brief_exposes_unresolved_asr_cues_as_evidence_input():
    source, context, route = _inputs()
    flagged_cue = source.input.cues[0].model_copy(
        update={
            "quality_flags": [
                *source.input.cues[0].quality_flags,
                "asr_unresolved_text",
            ]
        }
    )
    flagged_input = source.input.model_copy(
        update={
            "cues": [flagged_cue, *source.input.cues[1:]],
        }
    )
    flagged_source = source.model_copy(
        update={"input": flagged_input}
    )
    request = localization_document_brief.LocalizationDocumentBriefInput(
        source_operation_id="source_operation",
        context_operation_id="context_operation",
        source_lock=flagged_source,
        context_intent=context,
        route=route,
    )

    adaptive = localization_document_brief.build_document_brief_adaptive_rules(request)
    rule_ids = adaptive.rule_ids
    prompt = "\n".join(adaptive.paragraphs)
    payload = localization_document_brief._prompt_payload(request)

    assert "prioritize_unresolved_asr_evidence" in rule_ids
    assert "asr_unresolved_text" in prompt
    assert payload["source_cues"][0]["quality_flags"] == [
        "asr_unresolved_text",
    ]


@pytest.mark.parametrize(("text", "speaker_id", "semantic_flags"), [
    ("Revenue fell, but demand did not.", "analyst", ["language:en", "overlapping_speech", "boundary:audio-pause-high"]),
    ("You said you would return. Arakna!", None, ["asr_unresolved_text", "speaker-cluster:cluster_02", "boundary:audio-pause-low"]),
])
def test_document_brief_projection_preserves_content_across_fixed_content_types(text, speaker_id, semantic_flags):
    source, context, route = _inputs()
    operational = ["generated_by_asr", "llm_transcript_reviewed", "engine:qwen3-asr-mlx",
                   "segmentation:generic_zh", "timing:high", "timing:entry-lead-in",
                   "timing:acoustic-entry-refined",
                   "boundary-analysis:energy-pause-v2", "punctuation:minimal-style-normalized"]
    cue = source.input.cues[0].model_copy(update={"text": text, "speaker_id": speaker_id,
                                                "quality_flags": operational + semantic_flags})
    locked_input = source.input.model_copy(update={"cues": [cue]})
    source = source.model_copy(update={"input": locked_input})
    request = localization_document_brief.LocalizationDocumentBriefInput(
        source_operation_id="source_operation", context_operation_id="context_operation",
        source_lock=source, context_intent=context, route=route,
    )
    before = request.model_dump(mode="json")
    old_cue = {"cue_id": cue.cue_id, "text": cue.text, "speaker_id": cue.speaker_id,
               "quality_flags": list(cue.quality_flags)}
    payload = localization_document_brief._prompt_payload(request)
    assert payload["source_cues"] == [{**old_cue, "quality_flags": semantic_flags}]
    assert payload["document_context"] == context.input.document_context.model_dump(mode="json")
    assert payload["delivery_intent"] == context.input.delivery_intent.model_dump(mode="json")
    assert payload["source_fingerprint"] == source.source_fingerprint
    assert request.model_dump(mode="json") == before
    assert len(json.dumps(payload["source_cues"])) < len(json.dumps([old_cue])) * 0.6


def test_document_brief_splits_noncontiguous_visual_questions():
    content = localization_document_brief.LocalizationDocumentBriefContent(
        purpose="解释剧情。",
        audience="中文观众",
        structure=[
            {
                "section_id": "section_0001",
                "title": "剧情",
                "function_zh": "推进剧情。",
                "source_cue_ids": ["cue_0001"],
            }
        ],
        speaker_profile={
            "identity_zh": "角色",
            "expertise_zh": "未知",
            "audience_distance_zh": "角色对话",
            "rhythm_zh": "自然",
            "stable_traits_zh": ["直接"],
        },
        emotional_arc=[
            {
                "section_id": "section_0001",
                "emotion_zh": "紧张",
                "intensity": 3,
                "speech_acts": ["追问"],
            }
        ],
        immutable_facts=[
            {
                "fact_id": "fact_0001",
                "statement_zh": "存在两处疑点。",
                "source_cue_ids": ["cue_0001"],
            }
        ],
        cultural_adaptation_rules=["忠实原意"],
        disfluency_policy_zh="保守处理。",
        creative_strategy={
            "semantic_attention": [
                {
                    "attention_id": "attention_0001",
                    "source_meaning_zh": "两处需看画面。",
                    "expression_direction_zh": "按画面表达。",
                    "avoid_misreading_zh": "不得猜写。",
                    "source_cue_ids": ["cue_0166", "cue_0169"],
                    "confidence": "low",
                    "evidence_question_id": "question_0001",
                }
            ]
        },
        evidence_questions=[
            {
                "question_id": "question_0001",
                "kind": "visual",
                "question_zh": "读取两个镜头的字幕。",
                "source_cue_ids": ["cue_0166", "cue_0169"],
                "reason_zh": "ASR 文字不可靠。",
            }
        ],
    )

    result = (
        localization_document_brief
        ._split_noncontiguous_visual_questions(content)
    )

    assert [
        item.source_cue_ids for item in result.evidence_questions
    ] == [["cue_0166"], ["cue_0169"]]
    assert [
        item.question_id for item in result.evidence_questions
    ] == ["question_0001", "question_0002"]
    assert (
        result.creative_strategy.semantic_attention[0]
        .evidence_question_id
        == "question_0001"
    )


def test_split_visual_questions_remain_valid_after_expanding_past_model_cap():
    base = localization_document_brief.LocalizationDocumentBriefContent(
        purpose="解释剧情。",
        audience="中文观众",
        structure=[
            {
                "section_id": "section_0001",
                "title": "剧情",
                "function_zh": "推进剧情。",
                "source_cue_ids": ["cue_0001"],
            }
        ],
        speaker_profile={
            "identity_zh": "角色",
            "expertise_zh": "未知",
            "audience_distance_zh": "角色对话",
            "rhythm_zh": "自然",
            "stable_traits_zh": ["直接"],
        },
        emotional_arc=[
            {
                "section_id": "section_0001",
                "emotion_zh": "平静",
                "intensity": 2,
                "speech_acts": ["说明"],
            }
        ],
        immutable_facts=[
            {
                "fact_id": "fact_0001",
                "statement_zh": "存在画面问题。",
                "source_cue_ids": ["cue_0001"],
            }
        ],
        cultural_adaptation_rules=["忠实原意"],
        disfluency_policy_zh="保守处理。",
    )
    questions = [
        localization_document_brief.LocalizationEvidenceQuestion(
            question_id=f"question_{index:04d}",
            kind="visual",
            question_zh="查看画面。",
            source_cue_ids=(
                ["cue_0001", "cue_0010", "cue_0020", "cue_0030"]
                if index == 32
                else ["cue_0001"]
            ),
            reason_zh="需要确认。",
        )
        for index in range(1, 33)
    ]

    expanded = (
        localization_document_brief
        ._split_noncontiguous_visual_questions(
            base.model_copy(
                update={"evidence_questions": questions},
                deep=True,
            )
        )
    )

    assert len(expanded.evidence_questions) == 35
    assert (
        localization_document_brief.LocalizationDocumentBriefContent
        .model_validate(expanded.model_dump(mode="json"))
        == expanded
    )


def test_document_brief_cue_labels_distinguish_ranges_from_scattered_sources():
    assert localization_document_brief._cue_range_label(
        ["cue_0001", "cue_0002", "cue_0003"]
    ) == "cue_0001 – cue_0003（3 条）"
    assert localization_document_brief._cue_range_label(
        ["cue_0001", "cue_0040", "cue_0100"]
    ) == "cue_0001、cue_0040、cue_0100"


def test_visual_question_splits_long_contiguous_ranges_for_frame_coverage():
    content = localization_document_brief.LocalizationDocumentBriefContent(
        purpose="读取长镜头中的硬字幕。",
        audience="中文观众",
        structure=[
            {
                "section_id": "section_0001",
                "title": "片段",
                "function_zh": "读取字幕。",
                "source_cue_ids": [
                    f"cue_{index:04d}" for index in range(1, 10)
                ],
            }
        ],
        speaker_profile={
            "identity_zh": "电影角色",
            "expertise_zh": "未知",
            "audience_distance_zh": "对白",
            "rhythm_zh": "自然",
            "stable_traits_zh": ["按画面表达"],
        },
        emotional_arc=[
            {
                "section_id": "section_0001",
                "emotion_zh": "紧张",
                "intensity": 3,
                "speech_acts": ["对话"],
            }
        ],
        immutable_facts=[
            {
                "fact_id": "fact_0001",
                "statement_zh": "原片存在硬字幕。",
                "source_cue_ids": ["cue_0001"],
            }
        ],
        cultural_adaptation_rules=["忠实画面"],
        disfluency_policy_zh="不猜写。",
        evidence_questions=[
            {
                "question_id": "question_0001",
                "kind": "visual",
                "question_zh": "读取连续九条字幕。",
                "source_cue_ids": [
                    f"cue_{index:04d}" for index in range(1, 10)
                ],
                "reason_zh": "三张图不足以覆盖九条字幕。",
            }
        ],
    )

    result = (
        localization_document_brief
        ._split_noncontiguous_visual_questions(content)
    )

    assert [
        item.source_cue_ids for item in result.evidence_questions
    ] == [
        [
            "cue_0001",
            "cue_0002",
            "cue_0003",
            "cue_0004",
            "cue_0005",
            "cue_0006",
            "cue_0007",
            "cue_0008",
        ],
        ["cue_0009"],
    ]


def test_visual_question_keeps_five_cue_sentence_context_together():
    content = localization_document_brief.LocalizationDocumentBriefContent(
        purpose="读取被 ASR 拆开的完整硬字幕。",
        audience="中文观众",
        structure=[
            {
                "section_id": "section_0001",
                "title": "片段",
                "function_zh": "确认完整句意。",
                "source_cue_ids": [
                    f"cue_{index:04d}" for index in range(1, 6)
                ],
            }
        ],
        speaker_profile={
            "identity_zh": "电影角色",
            "expertise_zh": "未知",
            "audience_distance_zh": "对白",
            "rhythm_zh": "自然",
            "stable_traits_zh": ["按画面表达"],
        },
        emotional_arc=[
            {
                "section_id": "section_0001",
                "emotion_zh": "紧张",
                "intensity": 3,
                "speech_acts": ["对话"],
            }
        ],
        immutable_facts=[
            {
                "fact_id": "fact_0001",
                "statement_zh": "硬字幕跨越五个 ASR 片段。",
                "source_cue_ids": ["cue_0001"],
            }
        ],
        cultural_adaptation_rules=["忠实画面"],
        disfluency_policy_zh="不猜写。",
        evidence_questions=[
            {
                "question_id": "question_0001",
                "kind": "visual",
                "question_zh": "读取连续五条字幕。",
                "source_cue_ids": [
                    f"cue_{index:04d}" for index in range(1, 6)
                ],
                "reason_zh": "必须结合前后短句理解语法关系。",
            }
        ],
    )

    result = (
        localization_document_brief
        ._split_noncontiguous_visual_questions(content)
    )

    assert [
        item.source_cue_ids for item in result.evidence_questions
    ] == [[f"cue_{index:04d}" for index in range(1, 6)]]


def test_document_brief_uses_only_section_starts_and_ignores_extra_anchors():
    source, context, route = _inputs()
    base_cue = source.input.cues[0]
    source = source.model_copy(
        update={
            "input": source.input.model_copy(
                update={
                    "cues": [
                        base_cue,
                        base_cue.model_copy(
                            update={
                                "cue_id": "cue_0002",
                                "start_ms": 1_000,
                                "end_ms": 1_900,
                            }
                        ),
                        base_cue.model_copy(
                            update={
                                "cue_id": "cue_0003",
                                "start_ms": 2_000,
                                "end_ms": 2_900,
                            }
                        ),
                    ]
                }
            )
        }
    )
    request = localization_document_brief.LocalizationDocumentBriefInput(
        source_operation_id="source_operation",
        context_operation_id="context_operation",
        source_lock=source,
        context_intent=context,
        route=route,
    )
    content = localization_document_brief.LocalizationDocumentBriefContent(
        purpose="解释一件事。",
        audience="中文观众",
        structure=[
            localization_document_brief.LocalizationDocumentSection(
                section_id="section_0001",
                title="开始",
                function_zh="提出主题。",
                source_cue_ids=["cue_0001", "cue_0003"],
            ),
            localization_document_brief.LocalizationDocumentSection(
                section_id="section_0002",
                title="继续",
                function_zh="展开主题。",
                source_cue_ids=["cue_0002"],
            ),
        ],
        speaker_profile=(
            localization_document_brief.LocalizationSpeakerProfile(
                identity_zh="讲述者",
                expertise_zh="了解主题",
                audience_distance_zh="自然交流",
                rhythm_zh="清楚",
                stable_traits_zh=["直接"],
            )
        ),
        emotional_arc=[
            localization_document_brief.LocalizationEmotionalArcItem(
                section_id="section_0001",
                emotion_zh="平静",
                intensity=2,
                speech_acts=["说明"],
            ),
            localization_document_brief.LocalizationEmotionalArcItem(
                section_id="section_0002",
                emotion_zh="平静",
                intensity=2,
                speech_acts=["展开"],
            ),
        ],
        immutable_facts=[
            localization_document_brief.LocalizationImmutableFact(
                fact_id="fact_0001",
                statement_zh="原文说明了一件事。",
                source_cue_ids=["cue_0001"],
            )
        ],
        cultural_adaptation_rules=["按当前内容场景自然表达"],
        disfluency_policy_zh="没有需要保留的口语不流畅。",
    )

    expanded = (
        localization_document_brief
        ._expand_structure_to_complete_source_coverage(
            request,
            content,
        )
    )

    assert [
        section.source_cue_ids for section in expanded.structure
    ] == [["cue_0001"], ["cue_0002", "cue_0003"]]


def test_document_brief_validates_source_references_and_projects_details(
    monkeypatch,
):
    source, context, route = _inputs()
    requested_timeouts = []

    def complete_json(_prompt, payload, *, trace_sink, timeout, **_kwargs):
        requested_timeouts.append(timeout)
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=6_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        full_candidate = {
            "purpose": "解释一个简单事实。",
            "audience": "中文大众观众",
            "structure": [
                {
                    "section_id": "section_0001",
                    "title": "开场",
                    "function_zh": "说明主题。",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "speaker_profile": {
                "identity_zh": "科技访谈参与者",
                "expertise_zh": "专业",
                "audience_distance_zh": "平等交流",
                "rhythm_zh": "简洁",
                "stable_traits_zh": ["审慎"],
            },
            "emotional_arc": [
                {
                    "section_id": "section_0001",
                    "emotion_zh": "平静",
                    "intensity": 2,
                    "speech_acts": ["说明"],
                }
            ],
            "immutable_facts": [
                {
                    "fact_id": "fact_0001",
                    "statement_zh": "说了 Hello world。",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "term_relations": [
                {
                    "term": "Seedance",
                    "relation_zh": "视频生成模型名称",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "cultural_adaptation_rules": ["使用自然中文说明"],
            "disfluency_policy_zh": "没有有价值的口语不流畅。",
            "creative_strategy": {
                "content_type_zh": "工具介绍",
                "register_zh": "自然、专业",
                "audience_relationship_zh": "平等分享",
                "narrative_voice_zh": "直接讲解",
                "expression_strategy_zh": "先讲结论，再解释原因。",
                "terminology": [
                    {
                        "source_term": "Seedance",
                        "meaning_zh": "视频生成模型名称",
                        "preferred_target_term": "Seedance",
                        "allowed_variants": [],
                        "preserve_source_term": True,
                        "source_cue_ids": ["cue_0001"],
                        "confidence": "high",
                    }
                ],
                "semantic_attention": [
                    {
                        "attention_id": "attention_0001",
                        "source_meaning_zh": "介绍一个模型",
                        "expression_direction_zh": "按产品关系自然说明",
                        "avoid_misreading_zh": "把模型说成平台",
                        "source_cue_ids": ["cue_0001"],
                        "confidence": "high",
                        "evidence_question_id": None,
                    }
                ],
            },
            "evidence_questions": [
                {
                    "question_id": "question_0001",
                    "kind": "web",
                    "question_zh": "确认 Seedance 的正式写法。",
                    "query": "Seedance official model name",
                    "source_cue_ids": ["cue_0001"],
                    "reason_zh": "避免把产品名写错。",
                }
            ],
        }

        return stage_candidate(full_candidate, payload)

    monkeypatch.setattr(
        localization_document_brief.llm_runtime,
        "complete_json",
        complete_json,
    )
    result = (
        localization_document_brief.analyze_localization_document(
            localization_document_brief
            .LocalizationDocumentBriefInput(
                source_operation_id="source_operation",
                context_operation_id="context_operation",
                source_lock=source,
                context_intent=context,
                route=route,
            )
        )
    )

    assert result.quality_summary.status == "passed"
    assert requested_timeouts == [600, 600]
    assert result.quality_summary.source_reference_complete is True
    assert result.quality_summary.model_call_count == 2
    projected = (
        localization_document_brief
        .project_localization_document_brief_step_result(result)
    )
    assert projected["status"] == "success"
    assert projected["metrics"][0] == {"label": "篇章", "value": "1"}
    assert projected["metrics"][-2] == {
        "label": "需要查证",
        "value": "2",
    }
    assert projected["metrics"][-1] == {
        "label": "推荐术语",
        "value": "1",
    }
    assert [
        section["title"] for section in projected["sections"]
    ] == [
        "内容定位",
        "篇章结构",
        "人物与表达",
        "各篇章的情绪与说话作用",
        "不能改错的事实",
        "专业词与关系",
        "文化表达与口语处理",
        "创作策略草案",
        "推荐中文术语",
        "容易误解的重点语义",
        "交给后续流程自动补证",
    ]
    assert "不代表需要人工审核" in projected["notes"][1]
    debug_metrics = {
        item["label"]: item["value"]
        for item in projected["debug"]["metrics"]
    }
    assert debug_metrics["调用模型"] == "model"
    assert debug_metrics["调用方式"] == "local"
    assert debug_metrics["模型调用"] == "2"
    assert projected["debug"]["sections"][0]["title"] == "模型调用明细"


def test_document_brief_expands_section_anchors_without_losing_cues(
    monkeypatch,
):
    source, context, route = _inputs()
    extra_cues = [
        source.input.cues[0].model_copy(
            update={
                "cue_id": f"cue_{index:04d}",
                "text": f"part {index}",
                "start_ms": index * 1_000,
                "end_ms": index * 1_000 + 800,
            }
        )
        for index in range(2, 6)
    ]
    source = source.model_copy(
        update={
            "input": source.input.model_copy(
                update={"cues": [source.input.cues[0], *extra_cues]}
            )
        }
    )
    context = context.model_copy(
        update={"source_fingerprint": source.source_fingerprint}
    )

    def complete_json(_prompt, payload, *, trace_sink, **_kwargs):
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=6_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        full_candidate = {
            "purpose": "完整解释。",
            "audience": "中文大众观众",
            "structure": [
                {
                    "section_id": "section_0001",
                    "title": "前半",
                    "function_zh": "开场。",
                    "source_cue_ids": ["cue_0001"],
                },
                {
                    "section_id": "section_0002",
                    "title": "后半",
                    "function_zh": "收束。",
                    "source_cue_ids": ["cue_0004"],
                },
            ],
            "speaker_profile": {
                "identity_zh": "讲述者",
                "expertise_zh": "了解主题",
                "audience_distance_zh": "平等交流",
                "rhythm_zh": "自然",
                "stable_traits_zh": ["直接"],
            },
            "emotional_arc": [
                {
                    "section_id": "section_0001",
                    "emotion_zh": "平静",
                    "intensity": 2,
                    "speech_acts": ["说明"],
                },
                {
                    "section_id": "section_0002",
                    "emotion_zh": "平静",
                    "intensity": 2,
                    "speech_acts": ["总结"],
                },
            ],
            "immutable_facts": [
                {
                    "fact_id": "fact_0001",
                    "statement_zh": "内容完整。",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "term_relations": [],
            "cultural_adaptation_rules": ["自然表达"],
            "disfluency_policy_zh": "按人设保留。",
            "evidence_questions": [],
        }

        return stage_candidate(full_candidate, payload)

    monkeypatch.setattr(
        localization_document_brief.llm_runtime,
        "complete_json",
        complete_json,
    )
    result = localization_document_brief.analyze_localization_document(
        localization_document_brief.LocalizationDocumentBriefInput(
            source_operation_id="source_operation",
            context_operation_id="context_operation",
            source_lock=source,
            context_intent=context,
            route=route,
        )
    )

    assert result.content.structure[0].source_cue_ids == [
        "cue_0001",
        "cue_0002",
        "cue_0003",
    ]
    assert result.content.structure[1].source_cue_ids == [
        "cue_0004",
        "cue_0005",
    ]
    assert result.quality_summary.source_reference_complete is True


def test_document_brief_repairs_one_schema_invalid_json_response(
    monkeypatch,
):
    source, context, route = _inputs()
    valid_payload = {
        "purpose": "解释一个简单事实。",
        "audience": "中文大众观众",
        "structure": [
            {
                "section_id": "section_0001",
                "title": "开场",
                "function_zh": "说明主题。",
                "source_cue_ids": ["cue_0001"],
            }
        ],
        "speaker_profile": {
            "identity_zh": "科技访谈参与者",
            "expertise_zh": "专业",
            "audience_distance_zh": "平等交流",
            "rhythm_zh": "简洁",
            "stable_traits_zh": ["审慎"],
        },
        "emotional_arc": [
            {
                "section_id": "section_0001",
                "emotion_zh": "平静",
                "intensity": 2,
                "speech_acts": ["说明"],
            }
        ],
        "immutable_facts": [
            {
                "fact_id": "fact_0001",
                "statement_zh": "说了 Hello world。",
                "source_cue_ids": ["cue_0001"],
            }
        ],
        "term_relations": [],
        "cultural_adaptation_rules": ["使用自然中文说明"],
        "disfluency_policy_zh": "没有有价值的口语不流畅。",
        "evidence_questions": [],
    }
    outline, details = staged_candidates(valid_payload)
    responses = [
        {
            key: value
            for key, value in outline.items()
            if key != "speaker_profile"
        },
        outline,
        details,
    ]
    calls: list[dict] = []

    def complete_json(_prompt, payload, *args, trace_sink, **kwargs):
        round_index = len(calls) + 1
        calls.append(payload)
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=6_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return responses[round_index - 1]

    monkeypatch.setattr(
        localization_document_brief.llm_runtime,
        "complete_json",
        complete_json,
    )

    result = localization_document_brief.analyze_localization_document(
        localization_document_brief.LocalizationDocumentBriefInput(
            source_operation_id="source_operation",
            context_operation_id="context_operation",
            source_lock=source,
            context_intent=context,
            route=route,
        )
    )

    assert len(calls) == 3
    assert calls[1]["invalid_output"] == responses[0]
    assert calls[1]["validation_errors"] == [
        {
            "path": ["speaker_profile"],
            "message": "Field required",
        }
    ]
    assert [call.round_index for call in result.llm_calls] == [1, 2, 3]
    assert result.quality_summary.model_call_count == 3
