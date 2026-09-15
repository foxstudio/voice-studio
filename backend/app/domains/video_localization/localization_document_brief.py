"""Full-document understanding and adaptive localization brief for v3."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Compatibility exports: existing callers keep the same class objects.
from app.domains.video_localization.localization_brief_contracts import (
    BRIEF_VERSION as BRIEF_VERSION,
    PROMPT_VERSION as PROMPT_VERSION,
    LocalizationDocumentSection as LocalizationDocumentSection,
    LocalizationSpeakerProfile as LocalizationSpeakerProfile,
    LocalizationEmotionalArcItem as LocalizationEmotionalArcItem,
    LocalizationImmutableFact as LocalizationImmutableFact,
    LocalizationTermRelation as LocalizationTermRelation,
    LocalizationEvidenceQuestion as LocalizationEvidenceQuestion,
    LocalizationSpeechQualificationCandidate as LocalizationSpeechQualificationCandidate,
    LocalizationTerminologyDecision as LocalizationTerminologyDecision,
    LocalizationSemanticAttention as LocalizationSemanticAttention,
    LocalizationCreativeStrategyDraft as LocalizationCreativeStrategyDraft,
    LocalizationDocumentBriefContent as LocalizationDocumentBriefContent,
    LocalizationDocumentBriefInput as LocalizationDocumentBriefInput,
    LocalizationDocumentBriefQualitySummary as LocalizationDocumentBriefQualitySummary,
    LocalizationDocumentBriefResult as LocalizationDocumentBriefResult,
    LocalizationDocumentBriefAdaptiveRules as LocalizationDocumentBriefAdaptiveRules,
    LocalizationDocumentBriefSourceCue as LocalizationDocumentBriefSourceCue,
    LocalizationDocumentBriefSourcePayload as LocalizationDocumentBriefSourcePayload,
)

from app.domains.video_localization.development_llm_batches import DevelopmentLlmBatchReplay
from app.domains.video_localization.localization_source import project_localization_source_quality_flags
from app.domains.video_localization import localization_brief_contracts as brief_contracts
from app.domains.video_localization.localization_brief_stages import run_localization_brief_stages

from app.domains.video_localization.llm_observability import project_llm_calls
from app.services import llm_runtime as llm_runtime


MAX_CONTIGUOUS_VISUAL_CUES_PER_QUESTION = 8


class LocalizationDocumentBriefAssemblyValidation(BaseModel):
    """Local merge evidence; never a provider response or reusable node result."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["localization-brief-assembly-validation-v1"] = (
        "localization-brief-assembly-validation-v1"
    )
    source_fingerprint: str = Field(min_length=64, max_length=64)
    manifest_fingerprint: str = Field(min_length=64, max_length=64)
    outline_fingerprint: str = Field(min_length=64, max_length=64)
    candidate_fingerprint: str = Field(min_length=64, max_length=64)
    output_fingerprint: str | None = None
    status: Literal["passed", "failed"]
    error_type: str | None = None


def analyze_localization_document(
    request: LocalizationDocumentBriefInput,
    *,
    batch_journal: DevelopmentLlmBatchReplay | None = None,
) -> LocalizationDocumentBriefResult:
    if request.route.phase != "document_understanding":
        raise ValueError("全文理解收到的模型路由阶段不正确。")
    if request.context_intent.source_fingerprint != request.source_lock.source_fingerprint:
        raise ValueError("全文理解的源输入与上下文不是同一版本。")
    stages = run_localization_brief_stages(
        request,
        source_payload=brief_contracts.LocalizationDocumentBriefSourcePayload.model_validate(
            _prompt_payload(request)
        ),
        adaptive_rules=build_document_brief_adaptive_rules(request),
        batch_journal=batch_journal,
    )
    candidate_fingerprint = _fingerprint(stages.content.model_dump(mode="json"))
    assembly_identity = {
        "source_fingerprint": request.source_lock.source_fingerprint,
        "manifest_fingerprint": stages.manifest.manifest_fingerprint,
        "outline_fingerprint": stages.outline_fingerprint,
        "candidate_fingerprint": candidate_fingerprint,
    }
    assembly_step_id = (
        f"{batch_journal.prefix}.assembly.{_fingerprint(assembly_identity)}"
        if batch_journal is not None else None
    )
    try:
        content = _expand_structure_to_complete_source_coverage(request, stages.content)
        content = _ensure_source_uncertainty_questions(request, content)
        content = _ensure_speech_qualification_questions(request, content)
        content = _split_noncontiguous_visual_questions(content)
        content = LocalizationDocumentBriefContent.model_validate(content.model_dump(mode="json"))
        _validate_references(request, content)
        brief_contracts.validate_localization_brief_storage_capacity(content)
    except Exception as error:
        if batch_journal is not None:
            batch_journal.writer(assembly_step_id, LocalizationDocumentBriefAssemblyValidation(
                **assembly_identity, status="failed", error_type=type(error).__name__,
            ))
        raise
    if batch_journal is not None:
        batch_journal.writer(assembly_step_id, LocalizationDocumentBriefAssemblyValidation(
            **assembly_identity, status="passed",
            output_fingerprint=_fingerprint(content.model_dump(mode="json")),
        ))
    calls = stages.llm_calls
    recovered = stages.recovered_candidates
    dynamic_rule_ids = stages.dynamic_rule_ids
    fingerprint_payload = {
        "source_fingerprint": request.source_lock.source_fingerprint,
        "context_intent_fingerprint": request.context_intent.context_intent_fingerprint,
        "prompt_version": PROMPT_VERSION,
        "dynamic_rule_ids": dynamic_rule_ids,
        "route": request.route.model_dump(mode="json"),
        "content": content.model_dump(mode="json"),
        "manifest_fingerprint": stages.manifest.manifest_fingerprint,
        "outline_fingerprint": stages.outline_fingerprint,
    }
    if recovered:
        fingerprint_payload["recovered_candidates"] = [item.model_dump(mode="json") for item in recovered]
    return LocalizationDocumentBriefResult(
        source_fingerprint=request.source_lock.source_fingerprint,
        context_intent_fingerprint=request.context_intent.context_intent_fingerprint,
        result_fingerprint=_fingerprint(fingerprint_payload),
        dynamic_rule_ids=dynamic_rule_ids,
        content=content,
        route=request.route,
        llm_calls=calls,
        recovered_candidates=recovered,
        quality_summary=LocalizationDocumentBriefQualitySummary(
            status="warning" if recovered else "passed",
            section_count=len(content.structure),
            fact_count=len(content.immutable_facts),
            term_relation_count=len(content.term_relations),
            evidence_question_count=len(content.evidence_questions),
            source_reference_complete=True,
            model_call_count=None if recovered else len(calls),
            recorded_model_call_count=len(calls) if recovered else None,
            recovered_candidate_count=len(recovered) if recovered else None,
            call_telemetry_complete=False if recovered else None,
        ),
    )






def build_document_brief_adaptive_rules(
    request: LocalizationDocumentBriefInput,
) -> LocalizationDocumentBriefAdaptiveRules:
    """Select the existing adaptive rules without duplicating their policy."""

    if request.route.prompt_strategy == "fixed":
        return LocalizationDocumentBriefAdaptiveRules(paragraphs=[], rule_ids=[])
    rules = []
    paragraphs = []
    if request.source_lock.input.glossary:
        rules.append("respect_locked_glossary")
        paragraphs.append(
            "输入包含已审核术语表：术语关系必须优先遵守 glossary，"
            "只有存在冲突才列为证据问题。"
        )
    if not request.source_lock.input.speakers:
        rules.append("infer_textual_persona_without_voice_claims")
        paragraphs.append(
            "没有可靠说话人档案：只根据文本推断口吻，不得猜年龄、"
            "地域、性别或声音特征。"
        )
    if request.context_intent.input.document_context.speaker_style:
        rules.append("reuse_asr_speaker_style")
        paragraphs.append(
            "上游已保存说话方式摘要：用它校准人设，但仍以全文实际"
            "表达为准。"
        )
    if request.context_intent.input.document_context.source_uncertainties:
        rules.append("resolve_upstream_source_uncertainties")
        paragraphs.append(
            "上游 ASR 全文理解已附带少量罕见、待核实词及准确 cue。"
            "这些词不是已确认人名或事实，不能直接按音译解释。必须结合完整上下文"
            "决定真实含义；若画面可能有硬字幕、界面文字或可读台词，交给 visual "
            "evidence question，若画面无字则明确保留不确定性。"
        )
    if request.context_intent.input.delivery_intent.deliverables.spoken_script:
        rules.append("preserve_speakable_persona")
        paragraphs.append(
            "本次包含配音台词：简报必须记录可朗读节奏、情绪和有"
            "人设价值的口语不流畅；这里只生成台词文本，不代表生成音频。"
        )
    if any(
        "asr_unresolved_text" in cue.quality_flags
        for cue in request.source_lock.input.cues
    ):
        rules.append("prioritize_unresolved_asr_evidence")
        paragraphs.append(
            "source_cues 中标记 asr_unresolved_text 的文字已经被 ASR 复查确认仍不可靠。"
            "不要把它当成正常原文直接解释：先结合内容类型判断最合适的补证方式；"
            "影视、剧情、游戏或表演内容如果画面可能有硬字幕、界面文字或角色台词"
            "文字，必须为对应 cue 建立 visual evidence question。没有可读画面文字时，"
            "后续创作应保留不确定性，不得猜写具体含义。"
        )
    if any("asr_unresolved_scope_unknown" in cue.quality_flags for cue in request.source_lock.input.cues):
        rules.append("preserve_unlocated_asr_uncertainty")
        paragraphs.append(
            "source_cues 中的 asr_unresolved_scope_unknown 表示仍有未定位或有歧义的听写疑问，"
            "不表示该 cue 全部文字已被确认错误，也不表示问题已核实通过。"
            "仅针对有明确依据的疑点补证，不得据此否定整段原文或编造确定含义。"
        )
    rules.append("pragmatic_cultural_equivalence")
    paragraphs.append(
        "文化迁移必须按语用功能和强度建模，不能建立固定中英词表；"
        "需要最新用语或文化背景时列一个窄查询，不要为了普通口语搜索。"
    )
    return LocalizationDocumentBriefAdaptiveRules(paragraphs=paragraphs, rule_ids=rules)


def project_localization_document_brief_step_result(
    result: LocalizationDocumentBriefResult,
) -> dict:
    calls, call_notes = project_llm_calls(
        result.llm_calls,
        calls_complete=result.quality_summary.call_telemetry_complete is not False,
    )
    content = result.content
    section_title_by_id = {
        item.section_id: item.title for item in content.structure
    }
    return {
        "label": "建立全文本土化创作提纲",
        "order": 30,
        "status": "warning" if result.recovered_candidates else "success",
        "purpose": (
            "通读完整英文内容，整理后续写中文台词需要共同遵守的"
            "篇章、人设、情绪、事实和文化表达方向；本步骤不翻译。"
        ),
        "summary": (
            f"已形成 {len(content.structure)} 个篇章的创作提纲，"
            f"锁定 {len(content.immutable_facts)} 条不能改错的事实和 "
            f"{len(content.creative_strategy.semantic_attention)} 个重点语义；"
            f"另有 {len(content.evidence_questions)} 个问题交给后续流程自动补证。"
        ),
        "metrics": [
            {"label": "篇章", "value": str(len(content.structure))},
            {"label": "事实", "value": str(len(content.immutable_facts))},
            {
                "label": "术语关系",
                "value": str(len(content.term_relations)),
            },
            {
                "label": "需要查证",
                "value": str(len(content.evidence_questions)),
            },
            {
                "label": "推荐术语",
                "value": str(len(content.creative_strategy.terminology)),
            },
        ],
        "sections": [
            {
                "title": "内容定位",
                "items": [
                    {
                        "title": "这篇内容要表达什么",
                        "text": content.purpose,
                        "facts": [
                            {
                                "label": "目标观众",
                                "value": content.audience,
                            }
                        ],
                        "links": [],
                        "tone": "positive",
                    }
                ],
            },
            {
                "title": "篇章结构",
                "items": [
                    {
                        "title": item.title,
                        "text": item.function_zh,
                        "facts": [
                            {
                                "label": "英文位置",
                                "value": _cue_range_label(
                                    item.source_cue_ids
                                ),
                            }
                        ],
                        "links": [],
                        "tone": "neutral",
                    }
                    for item in content.structure
                ],
            },
            {
                "title": "人物与表达",
                "items": [
                    {
                        "title": content.speaker_profile.identity_zh,
                        "text": content.speaker_profile.rhythm_zh,
                        "facts": [
                            {
                                "label": "专业程度",
                                "value": (
                                    content.speaker_profile.expertise_zh
                                ),
                            },
                            {
                                "label": "与观众的距离",
                                "value": (
                                    content.speaker_profile
                                    .audience_distance_zh
                                ),
                            },
                            {
                                "label": "稳定特点",
                                "value": "、".join(
                                    content.speaker_profile
                                    .stable_traits_zh
                                ),
                            },
                        ],
                        "links": [],
                        "tone": "positive",
                    }
                ],
            },
            *(
                [{
                    "title": "各篇章的情绪与说话作用",
                    "items": [
                        {
                            "title": section_title_by_id.get(
                                item.section_id,
                                item.section_id,
                            ),
                            "text": item.emotion_zh,
                            "facts": [
                                {
                                    "label": "情绪强度",
                                    "value": f"{item.intensity} / 5",
                                },
                                {
                                    "label": "说话作用",
                                    "value": "、".join(item.speech_acts),
                                },
                            ],
                            "links": [],
                            "tone": "neutral",
                        }
                        for item in content.emotional_arc
                    ],
                }]
                if content.emotional_arc
                else []
            ),
            {
                "title": "不能改错的事实",
                "items": [
                    {
                        "title": item.statement_zh,
                        "text": "",
                        "facts": [
                            {
                                "label": "英文位置",
                                "value": _cue_range_label(
                                    item.source_cue_ids
                                ),
                            },
                        ],
                        "links": [],
                        "tone": "neutral",
                    }
                    for item in content.immutable_facts
                ],
            },
            *(
                [{
                    "title": "专业词与关系",
                    "items": [
                        {
                            "title": item.term,
                            "text": item.relation_zh,
                            "facts": [
                                {
                                    "label": "英文位置",
                                    "value": _cue_range_label(
                                        item.source_cue_ids
                                    ),
                                }
                            ],
                            "links": [],
                            "tone": "neutral",
                        }
                        for item in content.term_relations
                    ],
                }]
                if content.term_relations
                else []
            ),
            {
                "title": "文化表达与口语处理",
                "items": [
                    *[
                        {
                            "title": f"表达原则 {index}",
                            "text": rule,
                            "facts": [],
                            "links": [],
                            "tone": "neutral",
                        }
                        for index, rule in enumerate(
                            content.cultural_adaptation_rules,
                            start=1,
                        )
                    ],
                    {
                        "title": "停顿、重复和自我修正怎么处理",
                        "text": content.disfluency_policy_zh,
                        "facts": [],
                        "links": [],
                        "tone": "neutral",
                    },
                ],
            },
            *(
                [{
                    "title": "创作策略草案",
                    "items": [
                        {
                            "title": (
                                content.creative_strategy.content_type_zh
                                or "按全文内容确定"
                            ),
                            "text": (
                                content.creative_strategy
                                .expression_strategy_zh
                            ),
                            "facts": [
                                {
                                    "label": "语言尺度",
                                    "value": (
                                        content.creative_strategy.register_zh
                                        or "按人物原有尺度"
                                    ),
                                },
                                {
                                    "label": "叙述声音",
                                    "value": (
                                        content.creative_strategy
                                        .narrative_voice_zh
                                        or "沿用原人物"
                                    ),
                                },
                                {
                                    "label": "与观众关系",
                                    "value": (
                                        content.creative_strategy
                                        .audience_relationship_zh
                                        or "沿用原人物"
                                    ),
                                },
                            ],
                            "links": [],
                            "tone": "positive",
                        }
                    ],
                }]
                if any(
                    (
                        content.creative_strategy.content_type_zh,
                        content.creative_strategy.register_zh,
                        content.creative_strategy.narrative_voice_zh,
                        content.creative_strategy.expression_strategy_zh,
                    )
                )
                else []
            ),
            *(
                [{
                    "title": "推荐中文术语",
                    "items": [
                        {
                            "title": (
                                f"{item.source_term} → "
                                f"{item.preferred_target_term}"
                            ),
                            "text": item.meaning_zh,
                            "facts": [
                                {
                                    "label": "保留原文",
                                    "value": (
                                        "是"
                                        if item.preserve_source_term
                                        else "否"
                                    ),
                                },
                                {
                                    "label": "把握",
                                    "value": item.confidence,
                                },
                                {
                                    "label": "英文位置",
                                    "value": _cue_range_label(
                                        item.source_cue_ids
                                    ),
                                },
                            ],
                            "links": [],
                            "tone": "neutral",
                        }
                        for item in (
                            content.creative_strategy.terminology
                        )
                    ],
                }]
                if content.creative_strategy.terminology
                else []
            ),
            *(
                [{
                    "title": "容易误解的重点语义",
                    "items": [
                        {
                            "title": item.source_meaning_zh,
                            "text": item.expression_direction_zh,
                            "facts": [
                                {
                                    "label": "避免误解为",
                                    "value": item.avoid_misreading_zh,
                                },
                                {
                                    "label": "把握",
                                    "value": item.confidence,
                                },
                                {
                                    "label": "英文位置",
                                    "value": _cue_range_label(
                                        item.source_cue_ids
                                    ),
                                },
                            ],
                            "links": [],
                            "tone": (
                                "warning"
                                if item.confidence == "low"
                                else "neutral"
                            ),
                        }
                        for item in (
                            content.creative_strategy.semantic_attention
                        )
                    ],
                }]
                if content.creative_strategy.semantic_attention
                else []
            ),
            *(
                [{
                    "title": "交给后续流程自动补证",
                    "items": [
                        {
                            "title": item.question_zh,
                            "text": item.reason_zh,
                            "facts": [
                                {"label": "方式", "value": item.kind},
                                {
                                    "label": "英文位置",
                                    "value": _cue_range_label(
                                        item.source_cue_ids
                                    ),
                                },
                            ],
                            "links": [],
                            "tone": "neutral",
                        }
                        for item in content.evidence_questions
                    ],
                }]
                if content.evidence_questions
                else []
            ),
        ],
        "notes": [
            "网络口语按语用功能、人设和强度自适应，不使用固定替换表。",
            "待补证问题会自动交给后续资料查询或画面查看，不代表需要人工审核。",
            *([f"已恢复 {len(result.recovered_candidates)} 份指纹一致的完整候选并重新校验；原调用耗时、Token 和费用未知。"] if result.recovered_candidates else []),
        ],
        "debug": {
            "description": (
                "这里记录模型轮次、耗时和 Token；不属于本土化提纲正文。"
            ),
            "metrics": call_notes,
            "sections": [
                *([{"title": "模型调用明细", "items": calls}] if calls else []),
                *([{"title": "候选恢复来源（不是模型调用记录）", "items": [
                    {"title": item.batch_id, "text": "原输入及完整候选指纹已核验；原始调用遥测不可恢复。",
                     "facts": [{"label": "恢复收据", "value": item.receipt_fingerprint},
                               {"label": "原输入指纹", "value": item.input_fingerprint},
                               {"label": "候选指纹", "value": item.candidate_fingerprint}],
                     "links": [], "tone": "warning"} for item in result.recovered_candidates
                ]}] if result.recovered_candidates else []),
            ],
            "notes": [],
        },
    }


def _cue_range_label(cue_ids: list[str]) -> str:
    if not cue_ids:
        return "未记录"
    if len(cue_ids) == 1:
        return cue_ids[0]
    numeric_ids = []
    for cue_id in cue_ids:
        try:
            numeric_ids.append(int(cue_id.rsplit("_", 1)[-1]))
        except ValueError:
            numeric_ids = []
            break
    if numeric_ids and numeric_ids == list(
        range(numeric_ids[0], numeric_ids[0] + len(numeric_ids))
    ):
        return f"{cue_ids[0]} – {cue_ids[-1]}（{len(cue_ids)} 条）"
    if len(cue_ids) <= 4:
        return "、".join(cue_ids)
    return f"{cue_ids[0]}、…、{cue_ids[-1]}（共 {len(cue_ids)} 处）"


def _prompt_payload(request: LocalizationDocumentBriefInput) -> dict:
    source = request.source_lock.input
    return {
        "source_fingerprint": request.source_lock.source_fingerprint,
        "target_language": (
            request.context_intent.input.delivery_intent.target_language
        ),
        "document_context": (
            request.context_intent.input.document_context.model_dump(
                mode="json"
            )
        ),
        "delivery_intent": (
            request.context_intent.input.delivery_intent.model_dump(
                mode="json"
            )
        ),
        "source_cues": [
            {
                "cue_id": item.cue_id,
                "text": item.text,
                "speaker_id": item.speaker_id,
                "quality_flags": project_localization_source_quality_flags(item.quality_flags),
            }
            for item in source.cues
        ],
        "glossary": [
            item.model_dump(mode="json") for item in source.glossary
        ],
    }


def _validate_references(
    request: LocalizationDocumentBriefInput,
    content: LocalizationDocumentBriefContent,
) -> None:
    cue_ids = {
        item.cue_id for item in request.source_lock.input.cues
    }
    structure_references = [
        cue_id
        for item in content.structure
        for cue_id in item.source_cue_ids
    ]
    if structure_references != [
        item.cue_id for item in request.source_lock.input.cues
    ]:
        raise ValueError("全文理解的篇章没有完整、唯一、按顺序覆盖英文字幕。")
    references = list(structure_references)
    references.extend(
        cue_id
        for item in content.immutable_facts
        for cue_id in item.source_cue_ids
    )
    references.extend(
        cue_id
        for item in content.term_relations
        for cue_id in item.source_cue_ids
    )
    references.extend(
        cue_id
        for item in content.evidence_questions
        for cue_id in item.source_cue_ids
    )
    references.extend(
        cue_id
        for item in content.speech_qualification_candidates
        for cue_id in item.source_cue_ids
    )
    references.extend(
        cue_id
        for item in content.creative_strategy.terminology
        for cue_id in item.source_cue_ids
    )
    references.extend(
        cue_id
        for item in content.creative_strategy.semantic_attention
        for cue_id in item.source_cue_ids
    )
    if any(item not in cue_ids for item in references):
        raise ValueError("全文理解引用了输入中不存在的英文字幕。")
    section_ids = [item.section_id for item in content.structure]
    expected_section_ids = [
        f"section_{index:04d}"
        for index in range(1, len(section_ids) + 1)
    ]
    if section_ids != expected_section_ids:
        raise ValueError("全文理解的篇章 ID 必须唯一且连续。")
    if [
        item.section_id for item in content.emotional_arc
    ] != section_ids:
        raise ValueError("情绪曲线必须按顺序完整覆盖全部篇章。")
    if any(
        item.kind == "web" and not item.query.strip()
        for item in content.evidence_questions
    ):
        raise ValueError("资料查询问题缺少限定搜索词。")
    question_ids = {
        item.question_id for item in content.evidence_questions
    }
    if any(
        item.evidence_question_id is not None
        and item.evidence_question_id not in question_ids
        for item in content.creative_strategy.semantic_attention
    ):
        raise ValueError("重点语义关联了不存在的证据问题。")
    candidate_ids = [
        item.candidate_id
        for item in content.speech_qualification_candidates
    ]
    if candidate_ids != [
        f"speech_candidate_{index:04d}"
        for index in range(1, len(candidate_ids) + 1)
    ]:
        raise ValueError("非语言表演候选 ID 必须唯一且连续。")
    if any(
        question.purpose == "speech_qualification"
        and question.speech_candidate_id not in set(candidate_ids)
        for question in content.evidence_questions
    ):
        raise ValueError("非语言表演画面问题引用了不存在的候选。")


def _ensure_speech_qualification_questions(
    request: LocalizationDocumentBriefInput,
    content: LocalizationDocumentBriefContent,
) -> LocalizationDocumentBriefContent:
    """Turn model-proposed ambiguities into bounded visual questions.

    The model decides which cues are semantically ambiguous. Program code only
    guarantees that every proposed candidate gets one traceable question.
    """

    questions = list(content.evidence_questions)
    covered = {
        question.speech_candidate_id
        for question in questions
        if question.purpose == "speech_qualification"
        and question.speech_candidate_id
    }
    cue_by_id = {
        cue.cue_id: cue for cue in request.source_lock.input.cues
    }
    for candidate in content.speech_qualification_candidates:
        if candidate.candidate_id in covered or len(questions) >= 32:
            continue
        excerpts = [
            cue_by_id[cue_id].text.strip()
            for cue_id in candidate.source_cue_ids
            if cue_id in cue_by_id and cue_by_id[cue_id].text.strip()
        ]
        source_excerpt = " / ".join(excerpts)
        questions.append(
            LocalizationEvidenceQuestion(
                question_id=f"question_{len(questions) + 1:04d}",
                kind="visual",
                purpose="speech_qualification",
                speech_candidate_id=candidate.candidate_id,
                question_zh=(
                    "结合这一时间段的连续画面，判断 ASR 文字“"
                    f"{source_excerpt}”是可理解的真实语言，还是笑声、哭声、"
                    "喘息、尖叫、欢呼、拖长感叹等非语言表演。Demo 片段本身"
                    "不是跳过理由；只要是可理解语言就必须继续本土化。"
                ),
                source_cue_ids=list(candidate.source_cue_ids),
                reason_zh=candidate.reason_zh,
            )
        )
        covered.add(candidate.candidate_id)
    return content.model_copy(
        update={"evidence_questions": questions},
        deep=True,
    )


def _ensure_source_uncertainty_questions(
    request: LocalizationDocumentBriefInput,
    content: LocalizationDocumentBriefContent,
) -> LocalizationDocumentBriefContent:
    """Make upstream rare ASR uncertainties visible to evidence collection."""

    questions = list(content.evidence_questions)
    covered_cue_ids = {
        cue_id
        for question in questions
        if question.kind == "visual"
        for cue_id in question.source_cue_ids
    }
    source_cues = request.source_lock.input.cues
    cue_index = {
        cue.cue_id: index for index, cue in enumerate(source_cues)
    }
    for hint in (
        request.context_intent.input.document_context.source_uncertainties
    ):
        hint_cue_ids = sorted(
            {
                cue_id
                for cue_id in hint.source_cue_ids
                if cue_id in cue_index
            },
            key=cue_index.__getitem__,
        )
        if not hint_cue_ids:
            continue
        uncovered_ids = [
            cue_id
            for cue_id in hint_cue_ids
            if cue_id not in covered_cue_ids
        ]
        if not uncovered_ids:
            continue
        matching_question_index = next(
            (
                index
                for index, question in enumerate(questions)
                if question.kind == "visual"
                and set(question.source_cue_ids).intersection(
                    hint.source_cue_ids
                )
            ),
            None,
        )
        if matching_question_index is not None:
            existing = questions[matching_question_index]
            merged_ids = sorted(
                {*existing.source_cue_ids, *uncovered_ids},
                key=cue_index.__getitem__,
            )
            questions[matching_question_index] = existing.model_copy(
                update={"source_cue_ids": merged_ids}
            )
            covered_cue_ids.update(merged_ids)
            continue
        if len(questions) >= 32:
            continue
        question_cue_ids = sorted(
            uncovered_ids,
            key=cue_index.__getitem__,
        )
        question = LocalizationEvidenceQuestion(
            question_id=f"question_{len(questions) + 1:04d}",
            kind="visual",
            question_zh=(
                "读取对应画面中可见的硬字幕、界面文字或台词文字，确认 ASR "
                f"待核实词“{hint.term}”所在完整台词的真实含义；"
                "若没有可读文字，明确证据不足，不得把它猜成人名或事实。"
            ),
            source_cue_ids=question_cue_ids,
            reason_zh=hint.reason,
        )
        questions.append(question)
        covered_cue_ids.update(question_cue_ids)
    return content.model_copy(
        update={"evidence_questions": questions},
        deep=True,
    )


def _split_noncontiguous_visual_questions(
    content: LocalizationDocumentBriefContent,
) -> LocalizationDocumentBriefContent:
    expanded: list[LocalizationEvidenceQuestion] = []
    first_id_by_original: dict[str, str] = {}
    next_ordinal = 1
    for question in content.evidence_questions:
        groups = (
            [
                group[
                    start : start + MAX_CONTIGUOUS_VISUAL_CUES_PER_QUESTION
                ]
                for group in _contiguous_cue_groups(
                    question.source_cue_ids
                )
                for start in range(
                    0,
                    len(group),
                    MAX_CONTIGUOUS_VISUAL_CUES_PER_QUESTION,
                )
            ]
            if question.kind == "visual"
            else [list(question.source_cue_ids)]
        )
        for group in groups:
            question_id = f"question_{next_ordinal:04d}"
            next_ordinal += 1
            first_id_by_original.setdefault(
                question.question_id,
                question_id,
            )
            expanded.append(
                question.model_copy(
                    update={
                        "question_id": question_id,
                        "source_cue_ids": group,
                        "question_zh": (
                            question.question_zh
                            if len(groups) == 1
                            else (
                                f"只查看 {_cue_range_label(group)} 对应画面，"
                                "读取可见字幕、界面文字和该镜头能直接确认的含义；"
                                "不要用其他时间点补全。"
                            )
                        ),
                    },
                    deep=True,
                )
            )
    semantic_attention = [
        item.model_copy(
            update={
                "evidence_question_id": first_id_by_original.get(
                    item.evidence_question_id,
                    item.evidence_question_id,
                )
            }
        )
        for item in content.creative_strategy.semantic_attention
    ]
    return content.model_copy(
        update={
            "evidence_questions": expanded,
            "creative_strategy": (
                content.creative_strategy.model_copy(
                    update={"semantic_attention": semantic_attention}
                )
            ),
        },
        deep=True,
    )


def _contiguous_cue_groups(cue_ids: list[str]) -> list[list[str]]:
    if not cue_ids:
        return []
    groups = [[cue_ids[0]]]
    for cue_id in cue_ids[1:]:
        if _consecutive_cue_ids(groups[-1][-1], cue_id):
            groups[-1].append(cue_id)
        else:
            groups.append([cue_id])
    return groups


def _consecutive_cue_ids(left: str, right: str) -> bool:
    left_prefix, left_separator, left_ordinal = left.rpartition("_")
    right_prefix, right_separator, right_ordinal = right.rpartition("_")
    if (
        not left_separator
        or not right_separator
        or left_prefix != right_prefix
    ):
        return False
    try:
        return int(right_ordinal) == int(left_ordinal) + 1
    except ValueError:
        return False


def _expand_structure_to_complete_source_coverage(
    request: LocalizationDocumentBriefInput,
    content: LocalizationDocumentBriefContent,
) -> LocalizationDocumentBriefContent:
    """Turn LLM chapter anchors into exhaustive deterministic cue ranges."""

    ordered_cue_ids = [
        item.cue_id for item in request.source_lock.input.cues
    ]
    cue_index = {
        cue_id: index for index, cue_id in enumerate(ordered_cue_ids)
    }
    starts: list[int] = []
    for section in content.structure:
        anchor = section.source_cue_ids[0]
        if anchor not in cue_index:
            raise ValueError("全文理解引用了输入中不存在的英文字幕。")
        starts.append(cue_index[anchor])
    if not starts or starts[0] != 0:
        raise ValueError("全文理解的第一章没有从全文第一个英文 cue 开始。")
    if starts != sorted(set(starts)):
        raise ValueError("全文理解的章节起点存在重复或倒序。")

    expanded = []
    for index, section in enumerate(content.structure):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else len(
            ordered_cue_ids
        )
        expanded.append(
            section.model_copy(
                update={"source_cue_ids": ordered_cue_ids[start:end]}
            )
        )
    return content.model_copy(update={"structure": expanded})


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
    "BRIEF_VERSION",
    "LocalizationCreativeStrategyDraft",
    "LocalizationDocumentBriefContent",
    "LocalizationDocumentBriefInput",
    "LocalizationDocumentBriefResult",
    "LocalizationSemanticAttention",
    "LocalizationSpeechQualificationCandidate",
    "LocalizationTerminologyDecision",
    "analyze_localization_document",
    "build_document_brief_adaptive_rules",
    "project_localization_document_brief_step_result",
]
