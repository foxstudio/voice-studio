"""Deterministic assembly of the locked localization creation context.

The whole-document model produces a content-specific strategy draft. Evidence
tasks resolve only the questions that require external or visual support. This
module joins both results into the single versioned package consumed by spoken
script generation; it does not ask another model to rewrite a free-form prompt.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from app.domains.video_localization.localization_context_intent import (
    LocalizationContextIntentResult,
    LocalizationDeliveryIntent,
)
from app.domains.video_localization.localization_document_brief import (
    LocalizationCreativeStrategyDraft,
    LocalizationDocumentBriefContent,
    LocalizationDocumentBriefResult,
)
from app.domains.video_localization.localization_document_evidence import (
    LocalizationDocumentEvidenceAdjudicationResult,
)
from app.domains.video_localization.localization_source import (
    DEFAULT_LOCALIZATION_PIPELINE,
    LocalizationSourceLockResult,
)


CONTEXT_VERSION = "localization-creation-context-v6"

PARTIAL_VISUAL_ANCHOR_CONSTRAINT_ZH = (
    "只在所列源片段采用画面直接确认的文字；问题的其他部分仍按原文保守处理，"
    "不得据此补写。"
)


class LocalizationCreationContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-creation-context-input-v1"
    ] = "localization-creation-context-input-v1"
    source_operation_id: str = Field(min_length=1)
    context_operation_id: str = Field(min_length=1)
    brief_operation_id: str = Field(min_length=1)
    evidence_operation_id: str = Field(min_length=1)
    source_lock: LocalizationSourceLockResult
    context_intent: LocalizationContextIntentResult
    document_brief: LocalizationDocumentBriefResult
    evidence: LocalizationDocumentEvidenceAdjudicationResult


class LocalizationVerifiedEvidenceConstraint(BaseModel):
    """One evidence conclusion bound to the exact source cues it governs."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^question_\d{4}$")
    source_cue_ids: list[str] = Field(min_length=1)
    constraint_zh: str = Field(min_length=1, max_length=1_000)
    anchored_target_text_zh: str = Field(default="", max_length=500)
    source_kind: Literal["visual", "web"] | None = None
    observation_zh: str | None = Field(default=None, max_length=1_000)
    status: Literal["supported", "uncertain"] | None = None

    @model_serializer(mode="wrap")
    def _serialize_without_inventing_legacy_authority(self, handler):
        result = handler(self)
        for field in ("source_kind", "observation_zh", "status"):
            if getattr(self, field) is None:
                result.pop(field, None)
        return result


class LocalizationSpeechQualificationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^speech_candidate_\d{4}$")
    question_id: str | None = Field(default=None, pattern=r"^question_\d{4}$")
    source_cue_ids: list[str] = Field(min_length=1, max_length=16)
    policy: Literal[
        "translate",
        "preserve_non_language",
        "uncertain",
    ]
    reason_zh: str = Field(min_length=1, max_length=1_000)


def render_verified_evidence_constraint(
    constraint: LocalizationVerifiedEvidenceConstraint,
) -> str:
    """Render one verified rule consistently for generation and review."""

    if constraint.source_kind is not None and constraint.observation_zh is not None:
        origin = "画面观察" if constraint.source_kind == "visual" else "外部资料观察"
        text = (
            f"证据类型：{origin}；问题状态：{constraint.status or '未记录'}。"
            f"观察事实：{constraint.observation_zh} "
            "适用范围仅限本项所列核心源片段；相邻画面只是只读佐证。"
            f"证据采集限制（不是覆盖源文的编辑指令）：{constraint.constraint_zh} "
        )
    else:
        text = (
            "历史证据来源未分型，不能推断其确认权限或扩大适用范围。"
            f"原记录（不是覆盖源文的编辑指令）：{constraint.constraint_zh} "
        )
    anchored_text = constraint.anchored_target_text_zh.strip()
    if anchored_text:
        text += f"记录的可见文字译文：{anchored_text}；它不是自动新增或替换旁白的授权。"
    return text + "画面未显示某项内容不等于否定源文；源文中的未确认内容仍须保留不确定性。"


class LocalizationCreationContextContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_intent: LocalizationDeliveryIntent
    document_brief: LocalizationDocumentBriefContent
    creative_strategy: LocalizationCreativeStrategyDraft
    verified_evidence_constraints: list[
        LocalizationVerifiedEvidenceConstraint
    ] = Field(
        default_factory=list,
        max_length=100,
    )
    unresolved_evidence_question_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
    )
    speech_qualification_decisions: list[
        LocalizationSpeechQualificationDecision
    ] = Field(default_factory=list, max_length=32)
    translatable_source_cue_ids: list[str] = Field(default_factory=list)
    preserved_non_language_source_cue_ids: list[str] = Field(
        default_factory=list,
    )


class LocalizationCreationContextQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning"]
    terminology_count: int = Field(ge=0)
    semantic_attention_count: int = Field(ge=0)
    evidence_constraint_count: int = Field(ge=0)
    unresolved_evidence_count: int = Field(ge=0)
    model_call_count: Literal[0] = 0


class LocalizationCreationContextResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-creation-context-v6"
    ] = CONTEXT_VERSION
    source_fingerprint: str = Field(min_length=64, max_length=64)
    context_intent_fingerprint: str = Field(min_length=64, max_length=64)
    brief_fingerprint: str = Field(min_length=64, max_length=64)
    evidence_fingerprint: str = Field(min_length=64, max_length=64)
    result_fingerprint: str = Field(min_length=64, max_length=64)
    document_brief: LocalizationDocumentBriefResult
    content: LocalizationCreationContextContent
    quality_summary: LocalizationCreationContextQualitySummary


def lock_localization_creation_context(
    request: LocalizationCreationContextInput,
) -> LocalizationCreationContextResult:
    """Validate lineage and lock one auditable context package."""

    source_fingerprint = request.source_lock.source_fingerprint
    if request.context_intent.source_fingerprint != source_fingerprint:
        raise ValueError("本土化要求与英文源输入不是同一版本。")
    if request.document_brief.source_fingerprint != source_fingerprint:
        raise ValueError("全文创作提纲与英文源输入不是同一版本。")
    if (
        request.document_brief.context_intent_fingerprint
        != request.context_intent.context_intent_fingerprint
    ):
        raise ValueError("全文创作提纲与本土化要求不是同一版本。")
    if (
        request.evidence.brief_fingerprint
        != request.document_brief.result_fingerprint
    ):
        raise ValueError("资料与画面结论和全文创作提纲不是同一版本。")

    unresolved_question_ids = [
        item.question_id
        for item in request.evidence.answers
        if item.status == "uncertain"
    ]
    question_by_id = {
        item.question_id: item
        for item in request.document_brief.content.evidence_questions
    }
    valid_source_cue_ids = {
        item.cue_id for item in request.source_lock.input.cues
    }
    verified_constraints = []
    for answer in request.evidence.answers:
        constraint = answer.constraint_zh.strip()
        anchored_text = answer.anchored_target_text_zh.strip()
        question = question_by_id.get(answer.question_id)
        if question is None:
            continue
        # Historical answers may have absorbed read-only frame neighbours.
        # Reassembly may narrow their scope, but never rewrites that history.
        scoped_set = set(answer.source_cue_ids).intersection(question.source_cue_ids)
        scoped_cue_ids = [cue.cue_id for cue in request.source_lock.input.cues if cue.cue_id in scoped_set]
        # Speech qualification owns a separate, candidate-scoped decision
        # below. Visual context cues are read-only evidence and must never
        # leak into ordinary creation constraints.
        if question.purpose == "speech_qualification":
            continue
        if answer.status == "supported" and constraint:
            effective_constraint = constraint
        elif anchored_text and answer.anchored_source_cue_ids:
            effective_constraint = PARTIAL_VISUAL_ANCHOR_CONSTRAINT_ZH
        else:
            continue
        if not scoped_cue_ids:
            continue
        verified_constraints.append(
            LocalizationVerifiedEvidenceConstraint(
                question_id=answer.question_id,
                source_cue_ids=scoped_cue_ids,
                constraint_zh=effective_constraint,
                anchored_target_text_zh=anchored_text,
                source_kind=question.kind if answer.observed_source_cue_ids is not None else None,
                observation_zh=answer.conclusion_zh if answer.observed_source_cue_ids is not None else None,
                status=answer.status if answer.observed_source_cue_ids is not None else None,
            )
        )
    answer_by_question_id = {
        item.question_id: item for item in request.evidence.answers
    }
    speech_decisions = []
    preserved_cue_ids: list[str] = []
    for candidate in (
        request.document_brief.content.speech_qualification_candidates
    ):
        question = next(
            (
                item
                for item in request.document_brief.content.evidence_questions
                if item.purpose == "speech_qualification"
                and item.speech_candidate_id == candidate.candidate_id
            ),
            None,
        )
        answer = (
            answer_by_question_id.get(question.question_id)
            if question is not None
            else None
        )
        classification = (
            answer.speech_classification if answer is not None else "uncertain"
        )
        if (
            answer is not None
            and answer.status == "supported"
            and classification == "preserve_non_language"
        ):
            policy = "preserve_non_language"
            preserved_cue_ids.extend(candidate.source_cue_ids)
        elif (
            answer is not None
            and answer.status == "supported"
            and classification == "translatable_speech"
        ):
            policy = "translate"
        else:
            policy = "uncertain"
        speech_decisions.append(
            LocalizationSpeechQualificationDecision(
                candidate_id=candidate.candidate_id,
                question_id=question.question_id if question is not None else None,
                source_cue_ids=list(candidate.source_cue_ids),
                policy=policy,
                reason_zh=(
                    answer.conclusion_zh
                    if answer is not None
                    else "缺少足够画面证据，继续按可翻译语言处理并留待人工核对。"
                ),
            )
        )
    preserved_cue_ids = list(dict.fromkeys(preserved_cue_ids))
    preserved_cue_id_set = set(preserved_cue_ids)
    translatable_cue_ids = [
        cue.cue_id
        for cue in request.source_lock.input.cues
        if cue.cue_id not in preserved_cue_id_set
    ]
    content = LocalizationCreationContextContent(
        delivery_intent=(
            request.context_intent.input.delivery_intent
        ),
        document_brief=request.document_brief.content,
        creative_strategy=(
            request.document_brief.content.creative_strategy
        ),
        verified_evidence_constraints=verified_constraints,
        unresolved_evidence_question_ids=unresolved_question_ids,
        speech_qualification_decisions=speech_decisions,
        translatable_source_cue_ids=translatable_cue_ids,
        preserved_non_language_source_cue_ids=preserved_cue_ids,
    )
    payload = {
        "source_fingerprint": source_fingerprint,
        "context_intent_fingerprint": (
            request.context_intent.context_intent_fingerprint
        ),
        "brief_fingerprint": request.document_brief.result_fingerprint,
        "evidence_fingerprint": request.evidence.result_fingerprint,
        "content": content.model_dump(mode="json"),
    }
    status: Literal["passed", "warning"] = (
        "warning"
        if unresolved_question_ids
        or any(item.policy == "uncertain" for item in speech_decisions)
        else "passed"
    )
    return LocalizationCreationContextResult(
        source_fingerprint=source_fingerprint,
        context_intent_fingerprint=(
            request.context_intent.context_intent_fingerprint
        ),
        brief_fingerprint=request.document_brief.result_fingerprint,
        evidence_fingerprint=request.evidence.result_fingerprint,
        result_fingerprint=_fingerprint(payload),
        document_brief=request.document_brief,
        content=content,
        quality_summary=LocalizationCreationContextQualitySummary(
            status=status,
            terminology_count=len(content.creative_strategy.terminology),
            semantic_attention_count=len(
                content.creative_strategy.semantic_attention
            ),
            evidence_constraint_count=len(
                content.verified_evidence_constraints
            ),
            unresolved_evidence_count=len(unresolved_question_ids),
        ),
    )


def project_localization_creation_source_lock(
    source_lock: LocalizationSourceLockResult,
    creation_context: LocalizationCreationContextResult,
) -> LocalizationSourceLockResult:
    """Create the exact source view consumed by Chinese generation.

    The original immutable source fingerprint remains the lineage identity.
    The creation-context fingerprint owns the model-decided exclusions.
    """

    if creation_context.source_fingerprint != source_lock.source_fingerprint:
        raise ValueError("中文创作来源与原始英文源不是同一版本。")
    included = set(creation_context.content.translatable_source_cue_ids)
    if not included and not creation_context.content.speech_qualification_decisions:
        included = {cue.cue_id for cue in source_lock.input.cues}
    cues = [cue for cue in source_lock.input.cues if cue.cue_id in included]
    if not cues:
        raise ValueError("当前视频没有需要翻译的真实语言片段。")
    word_ids = [word_id for cue in cues for word_id in cue.source_word_ids]
    word_id_set = set(word_ids)
    words = [
        word for word in source_lock.input.words if word.word_id in word_id_set
    ]
    pauses = [
        pause
        for pause in source_lock.input.pauses
        if pause.left_word_id in word_id_set
        and pause.right_word_id in word_id_set
    ]
    projected = DEFAULT_LOCALIZATION_PIPELINE.lock_source(
        source_lock.input.model_copy(
            update={"cues": cues, "words": words, "pauses": pauses},
            deep=True,
        )
    )
    return projected.model_copy(
        update={"source_fingerprint": source_lock.source_fingerprint},
        deep=True,
    )


def project_localization_creation_sections(
    creation_context: LocalizationCreationContextResult,
):
    included = set(creation_context.content.translatable_source_cue_ids)
    if not included and not creation_context.content.speech_qualification_decisions:
        included = {
            cue_id
            for section in creation_context.document_brief.content.structure
            for cue_id in section.source_cue_ids
        }
    return [
        section.model_copy(
            update={
                "source_cue_ids": [
                    cue_id
                    for cue_id in section.source_cue_ids
                    if cue_id in included
                ]
            },
            deep=True,
        )
        for section in creation_context.document_brief.content.structure
        if any(cue_id in included for cue_id in section.source_cue_ids)
    ]
def project_localization_creation_context_result(
    result: LocalizationCreationContextResult,
) -> dict:
    summary = result.quality_summary
    strategy = result.content.creative_strategy
    sections = [
        {
            "title": "中文创作定位",
            "items": [
                {
                    "title": strategy.content_type_zh or "沿用全文内容定位",
                    "text": strategy.expression_strategy_zh,
                    "facts": [
                        {
                            "label": "语言尺度",
                            "value": strategy.register_zh or "沿用原人物",
                        },
                        {
                            "label": "叙述声音",
                            "value": (
                                strategy.narrative_voice_zh
                                or "沿用原人物"
                            ),
                        },
                        {
                            "label": "与观众关系",
                            "value": (
                                strategy.audience_relationship_zh
                                or "沿用原人物"
                            ),
                        },
                    ],
                    "links": [],
                    "tone": "positive",
                }
            ],
        },
    ]
    if strategy.terminology:
        sections.append(
            {
                "title": "锁定的中文术语",
                "items": [
                    {
                        "title": (
                            f"{item.source_term} → "
                            f"{item.preferred_target_term}"
                        ),
                        "text": item.meaning_zh,
                        "facts": [
                            {
                                "label": "允许变体",
                                "value": (
                                    "、".join(item.allowed_variants)
                                    or "无"
                                ),
                            },
                            {
                                "label": "保留源语",
                                "value": (
                                    "是"
                                    if item.preserve_source_term
                                    else "否"
                                ),
                            },
                            {"label": "把握", "value": item.confidence},
                        ],
                        "links": [],
                        "tone": "neutral",
                    }
                    for item in strategy.terminology
                ],
            }
        )
    if strategy.semantic_attention:
        sections.append(
            {
                "title": "锁定的重点语义",
                "items": [
                    {
                        "title": item.source_meaning_zh,
                        "text": item.expression_direction_zh,
                        "facts": [
                            {
                                "label": "避免误解为",
                                "value": item.avoid_misreading_zh,
                            },
                            {"label": "把握", "value": item.confidence},
                        ],
                        "links": [],
                        "tone": (
                            "warning"
                            if item.confidence == "low"
                            else "neutral"
                        ),
                    }
                    for item in strategy.semantic_attention
                ],
            }
        )
    if result.content.verified_evidence_constraints:
        sections.append(
            {
                "title": "已核实的证据约束",
                "items": [
                    {
                        "title": f"约束 {index}",
                        "text": constraint.constraint_zh,
                        "facts": [],
                        "links": [],
                        "tone": "neutral",
                    }
                    for index, constraint in enumerate(
                        result.content.verified_evidence_constraints,
                        start=1,
                    )
                ],
            }
        )
    return {
        "label": "锁定本土化创作策略",
        "order": 55,
        "status": (
            "warning" if summary.status == "warning" else "success"
        ),
        "purpose": (
            "把全文提纲、动态创作策略和证据结论汇合成下一步唯一使用的"
            "创作上下文；不翻译，也不生成音频。"
        ),
        "summary": (
            f"已锁定 {summary.terminology_count} 个术语选择、"
            f"{summary.semantic_attention_count} 个重点语义和 "
            f"{summary.evidence_constraint_count} 条证据约束；"
            f"{summary.unresolved_evidence_count} 个证据问题保持保守。"
        ),
        "metrics": [
            {"label": "术语", "value": str(summary.terminology_count)},
            {
                "label": "重点语义",
                "value": str(summary.semantic_attention_count),
            },
            {
                "label": "证据约束",
                "value": str(summary.evidence_constraint_count),
            },
            {
                "label": "保持保守",
                "value": str(summary.unresolved_evidence_count),
            },
        ],
        "sections": sections,
        "notes": [
            "本步骤只校验并锁定上游结果，未调用模型。",
            "固定提示词内核不保存在这里；这里保存的是当前视频专属的动态创作策略。",
        ],
        "debug": {
            "description": "该节点没有模型调用。",
            "metrics": [
                {"label": "模型调用", "value": "0"},
            ],
            "sections": [],
            "notes": [],
        },
    }


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CONTEXT_VERSION",
    "LocalizationCreationContextContent",
    "LocalizationCreationContextInput",
    "LocalizationCreationContextQualitySummary",
    "LocalizationCreationContextResult",
    "LocalizationSpeechQualificationDecision",
    "LocalizationVerifiedEvidenceConstraint",
    "lock_localization_creation_context",
    "project_localization_creation_context_result",
    "project_localization_creation_sections",
    "project_localization_creation_source_lock",
    "render_verified_evidence_constraint",
]
