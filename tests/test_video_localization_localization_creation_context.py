from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    localization_creation_context,
    localization_document_brief,
    localization_document_evidence,
)
from tests.test_video_localization_localization_document_brief import (  # noqa: E402
    _inputs,
)
from tests.test_video_localization_localization_document_evidence import (  # noqa: E402
    _brief,
)


def _request():
    source, brief = _brief(with_question=False)
    _same_source, context, _route = _inputs()
    strategy = localization_document_brief.LocalizationCreativeStrategyDraft(
        content_type_zh="工具演示",
        register_zh="自然、专业",
        audience_relationship_zh="像同行分享",
        narrative_voice_zh="边演示边解释",
        expression_strategy_zh="先展示效果，再说明方法。",
        terminology=[
            localization_document_brief.LocalizationTerminologyDecision(
                source_term="Example",
                meaning_zh="产品名称",
                preferred_target_term="Example",
                preserve_source_term=True,
                source_cue_ids=["cue_0001"],
                confidence="high",
            )
        ],
        semantic_attention=[
            localization_document_brief.LocalizationSemanticAttention(
                attention_id="attention_0001",
                source_meaning_zh="正在使用一个工具能力",
                expression_direction_zh="说明使用关系",
                avoid_misreading_zh="把能力说成页面开关",
                source_cue_ids=["cue_0001"],
                confidence="high",
            )
        ],
    )
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={"creative_strategy": strategy}
            )
        }
    )
    evidence = (
        localization_document_evidence
        .LocalizationDocumentEvidenceAdjudicationResult(
            brief_fingerprint=brief.result_fingerprint,
            result_fingerprint="e" * 64,
            status="not_needed",
        )
    )
    return localization_creation_context.LocalizationCreationContextInput(
        source_operation_id="source",
        context_operation_id="context",
        brief_operation_id="brief",
        evidence_operation_id="evidence",
        source_lock=source,
        context_intent=context,
        document_brief=brief,
        evidence=evidence,
    )


def test_creation_context_locks_dynamic_strategy_without_model_call():
    result = (
        localization_creation_context.lock_localization_creation_context(
            _request()
        )
    )

    assert result.quality_summary.status == "passed"
    assert result.quality_summary.model_call_count == 0
    assert result.quality_summary.terminology_count == 1
    assert result.quality_summary.semantic_attention_count == 1
    assert (
        result.content.creative_strategy.terminology[0]
        .preferred_target_term
        == "Example"
    )
    projected = (
        localization_creation_context
        .project_localization_creation_context_result(result)
    )
    assert projected["label"] == "锁定本土化创作策略"
    assert projected["debug"]["metrics"] == [
        {"label": "模型调用", "value": "0"}
    ]


def test_creation_context_rejects_evidence_from_another_brief():
    request = _request()
    request = request.model_copy(
        update={
            "evidence": request.evidence.model_copy(
                update={"brief_fingerprint": "x" * 64}
            )
        }
    )

    with pytest.raises(ValueError, match="不是同一版本"):
        localization_creation_context.lock_localization_creation_context(
            request
        )


def test_creation_context_binds_supported_evidence_to_source_cues():
    source, brief = _brief(with_question=True)
    _same_source, context, _route = _inputs()
    evidence = (
        localization_document_evidence
        .LocalizationDocumentEvidenceAdjudicationResult(
            brief_fingerprint=brief.result_fingerprint,
            result_fingerprint="e" * 64,
            answers=[
                {
                    "question_id": "question_0001",
                    "source_cue_ids": ["cue_0001"],
                    "status": "supported",
                    "conclusion_zh": "画面确认产品名称。",
                    "constraint_zh": "使用画面确认的产品名称。",
                }
            ],
            constraints=["使用画面确认的产品名称。"],
            status="passed",
        )
    )

    result = localization_creation_context.lock_localization_creation_context(
        localization_creation_context.LocalizationCreationContextInput(
            source_operation_id="source",
            context_operation_id="context",
            brief_operation_id="brief",
            evidence_operation_id="evidence",
            source_lock=source,
            context_intent=context,
            document_brief=brief,
            evidence=evidence,
        )
    )

    assert result.contract_version == "localization-creation-context-v6"
    assert [
        item.model_dump(mode="json")
        for item in result.content.verified_evidence_constraints
    ] == [
        {
            "question_id": "question_0001",
            "source_cue_ids": ["cue_0001"],
            "constraint_zh": "使用画面确认的产品名称。",
            "anchored_target_text_zh": "",
        }
    ]


def test_creation_context_keeps_confirmed_text_from_partly_uncertain_evidence():
    source, brief = _brief(with_question=True)
    _same_source, context, _route = _inputs()
    evidence = (
        localization_document_evidence
        .LocalizationDocumentEvidenceAdjudicationResult(
            brief_fingerprint=brief.result_fingerprint,
            result_fingerprint="e" * 64,
            answers=[
                {
                    "question_id": "question_0001",
                    "source_cue_ids": ["cue_0001"],
                    "anchored_source_cue_ids": ["cue_0001"],
                    "status": "uncertain",
                    "conclusion_zh": "画面确认了字幕，但没有确认说话人。",
                    "anchored_target_text_zh": "这只会是你毁灭的开始！",
                }
            ],
            status="warning",
        )
    )

    result = localization_creation_context.lock_localization_creation_context(
        localization_creation_context.LocalizationCreationContextInput(
            source_operation_id="source",
            context_operation_id="context",
            brief_operation_id="brief",
            evidence_operation_id="evidence",
            source_lock=source,
            context_intent=context,
            document_brief=brief,
            evidence=evidence,
        )
    )

    assert result.quality_summary.status == "warning"
    assert result.content.unresolved_evidence_question_ids == [
        "question_0001"
    ]
    assert [
        item.model_dump(mode="json")
        for item in result.content.verified_evidence_constraints
    ] == [
        {
            "question_id": "question_0001",
            "source_cue_ids": ["cue_0001"],
            "constraint_zh": (
                localization_creation_context
                .PARTIAL_VISUAL_ANCHOR_CONSTRAINT_ZH
            ),
            "anchored_target_text_zh": "这只会是你毁灭的开始！",
        }
    ]


def test_creation_context_excludes_only_supported_non_language_performance():
    request = _request()
    candidate = (
        localization_document_brief.LocalizationSpeechQualificationCandidate(
            candidate_id="speech_candidate_0001",
            source_cue_ids=["cue_0001"],
            reason_zh="可能是角色尖叫。",
        )
    )
    question = localization_document_brief.LocalizationEvidenceQuestion(
        question_id="question_0001",
        kind="visual",
        purpose="speech_qualification",
        speech_candidate_id="speech_candidate_0001",
        question_zh="判断是否为非语言表演。",
        source_cue_ids=["cue_0001"],
        reason_zh="避免给尖叫配成一句短台词。",
    )
    brief = request.document_brief.model_copy(
        update={
            "content": request.document_brief.content.model_copy(
                update={
                    "speech_qualification_candidates": [candidate],
                    "evidence_questions": [question],
                }
            )
        }
    )
    evidence = (
        localization_document_evidence
        .LocalizationDocumentEvidenceAdjudicationResult(
            brief_fingerprint=brief.result_fingerprint,
            result_fingerprint="f" * 64,
            answers=[{
                "question_id": "question_0001",
                "source_cue_ids": ["cue_0001"],
                "evidence_image_positions": [1],
                "status": "supported",
                "speech_classification": "preserve_non_language",
                "conclusion_zh": "画面确认角色正在坠落并持续尖叫。",
                "constraint_zh": "保留尖叫，不要改写成台词。",
            }],
            status="passed",
        )
    )

    result = localization_creation_context.lock_localization_creation_context(
        request.model_copy(
            update={"document_brief": brief, "evidence": evidence}
        )
    )

    assert result.content.preserved_non_language_source_cue_ids == [
        "cue_0001"
    ]
    assert result.content.translatable_source_cue_ids == []
    assert result.content.speech_qualification_decisions[0].policy == (
        "preserve_non_language"
    )
    assert result.content.verified_evidence_constraints == []
