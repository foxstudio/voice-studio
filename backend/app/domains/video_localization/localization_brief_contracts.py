"""Neutral typed contracts shared by brief orchestration and cue planning."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from app.domains.video_localization.llm_candidate_provenance import RecoveredLlmCandidateProvenance

from app.domains.video_localization.llm_observability import VideoLocalizationLlmCallRecord
from app.domains.video_localization.localization_context_intent import (
    LocalizationContextIntentResult,
    LocalizationDocumentContext,
    LocalizationDeliveryIntent,
)
from app.domains.video_localization.localization_source import (
    LocalizationSourceLockResult,
    LocalizationSourceGlossaryEntry,
)
from app.services.localization_ai_policy import LocalizationAiPhaseRoute


BRIEF_VERSION = "localization-document-brief-v3"
PROMPT_VERSION = "localization-document-brief-prompt-v14"
# Engineering storage budget for serialized content, not a model token limit.
MAX_BRIEF_CONTENT_UTF8_BYTES = 1024 * 1024


class LocalizationDocumentSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(pattern=r"^section_\d{4}$")
    title: str = Field(min_length=1, max_length=120)
    function_zh: str = Field(min_length=1, max_length=500)
    source_cue_ids: list[str] = Field(min_length=1)


class LocalizationSpeakerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_zh: str = Field(min_length=1, max_length=500)
    expertise_zh: str = Field(min_length=1, max_length=500)
    audience_distance_zh: str = Field(min_length=1, max_length=500)
    rhythm_zh: str = Field(min_length=1, max_length=500)
    stable_traits_zh: list[str] = Field(min_length=1, max_length=20)


class LocalizationEmotionalArcItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(pattern=r"^section_\d{4}$")
    emotion_zh: str = Field(min_length=1, max_length=300)
    intensity: int = Field(ge=1, le=5)
    speech_acts: list[str] = Field(min_length=1, max_length=12)


class LocalizationImmutableFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(pattern=r"^fact_\d{4}$")
    statement_zh: str = Field(min_length=1, max_length=500)
    source_cue_ids: list[str] = Field(min_length=1)


class LocalizationTermRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str = Field(min_length=1, max_length=120)
    relation_zh: str = Field(min_length=1, max_length=500)
    source_cue_ids: list[str] = Field(min_length=1)


class LocalizationEvidenceQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^question_\d{4}$")
    kind: Literal["web", "visual"]
    question_zh: str = Field(min_length=1, max_length=500)
    query: str = Field(default="", max_length=240)
    source_cue_ids: list[str] = Field(min_length=1)
    reason_zh: str = Field(min_length=1, max_length=500)
    purpose: Literal["general", "speech_qualification"] = "general"
    speech_candidate_id: str | None = Field(
        default=None,
        pattern=r"^speech_candidate_\d{4}$",
    )


class LocalizationSpeechQualificationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^speech_candidate_\d{4}$")
    source_cue_ids: list[str] = Field(min_length=1, max_length=16)
    reason_zh: str = Field(min_length=1, max_length=500)


class LocalizationTerminologyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_term: str = Field(min_length=1, max_length=160)
    meaning_zh: str = Field(min_length=1, max_length=500)
    preferred_target_term: str = Field(min_length=1, max_length=160)
    allowed_variants: list[str] = Field(default_factory=list, max_length=12)
    preserve_source_term: bool = False
    source_cue_ids: list[str] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]


class LocalizationSemanticAttention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attention_id: str = Field(pattern=r"^attention_\d{4}$")
    source_meaning_zh: str = Field(min_length=1, max_length=800)
    expression_direction_zh: str = Field(min_length=1, max_length=800)
    avoid_misreading_zh: str = Field(min_length=1, max_length=800)
    source_cue_ids: list[str] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]
    evidence_question_id: str | None = Field(
        default=None,
        pattern=r"^question_\d{4}$",
    )


class LocalizationCreativeStrategyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type_zh: str = Field(default="", max_length=300)
    register_zh: str = Field(default="", max_length=300)
    audience_relationship_zh: str = Field(default="", max_length=300)
    narrative_voice_zh: str = Field(default="", max_length=500)
    expression_strategy_zh: str = Field(default="", max_length=1_000)
    terminology: list[LocalizationTerminologyDecision] = Field(
        default_factory=list,
    )
    semantic_attention: list[LocalizationSemanticAttention] = Field(
        default_factory=list,
    )


class LocalizationDocumentBriefContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1, max_length=1_000)
    audience: str = Field(min_length=1, max_length=500)
    structure: list[LocalizationDocumentSection] = Field(
        min_length=1,
        max_length=40,
    )
    speaker_profile: LocalizationSpeakerProfile
    emotional_arc: list[LocalizationEmotionalArcItem] = Field(
        min_length=1,
        max_length=40,
    )
    immutable_facts: list[LocalizationImmutableFact] = Field(
        min_length=1,
    )
    term_relations: list[LocalizationTermRelation] = Field(
        default_factory=list,
    )
    cultural_adaptation_rules: list[str] = Field(
        min_length=1,
        max_length=30,
    )
    disfluency_policy_zh: str = Field(min_length=1, max_length=1_000)
    speech_qualification_candidates: list[
        LocalizationSpeechQualificationCandidate
    ] = Field(default_factory=list, max_length=32)
    creative_strategy: LocalizationCreativeStrategyDraft = Field(
        default_factory=LocalizationCreativeStrategyDraft,
    )
    evidence_questions: list[LocalizationEvidenceQuestion] = Field(
        default_factory=list,
        # The model proposes at most 32 questions. One visual question may be
        # deterministically split into several non-contiguous cue ranges, so
        # the persisted post-processed contract must allow those expansions.
        max_length=512,
    )


def validate_localization_brief_storage_capacity(content: LocalizationDocumentBriefContent) -> int:
    """Check complete v3 content without truncation; return its UTF-8 size.

    This canonical compact JSON is a storage engineering boundary. It is not
    the size of a result envelope, provider request, or model token window.
    Historical v2 reads do not retroactively acquire this policy.
    """
    size = len(json.dumps(content.model_dump(mode="json"), ensure_ascii=False,
                          allow_nan=False, separators=(",", ":")).encode("utf-8"))
    if size > MAX_BRIEF_CONTENT_UTF8_BYTES:
        raise ValueError(f"完整简报存储超出 UTF-8 工程预算：{size} > {MAX_BRIEF_CONTENT_UTF8_BYTES} 字节；未截断。")
    return size


class LocalizationDocumentBriefInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-document-brief-input-v1"
    ] = "localization-document-brief-input-v1"
    source_operation_id: str = Field(min_length=1)
    context_operation_id: str = Field(min_length=1)
    source_lock: LocalizationSourceLockResult
    context_intent: LocalizationContextIntentResult
    route: LocalizationAiPhaseRoute


class LocalizationDocumentBriefQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning"]
    section_count: int = Field(ge=1)
    fact_count: int = Field(ge=1)
    term_relation_count: int = Field(ge=0)
    evidence_question_count: int = Field(ge=0)
    source_reference_complete: bool
    # Unknown when original telemetry was lost; observed records stay separate.
    model_call_count: int | None = Field(ge=1)
    recorded_model_call_count: int | None = Field(default=None, ge=0)
    recovered_candidate_count: int | None = Field(default=None, ge=0)
    call_telemetry_complete: bool | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, serialize):
        result = serialize(self)
        for key in ("recorded_model_call_count", "recovered_candidate_count", "call_telemetry_complete"):
            if getattr(self, key) is None:
                result.pop(key, None)
        return result


class LocalizationDocumentBriefResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-document-brief-v2", "localization-document-brief-v3"
    ] = BRIEF_VERSION
    prompt_version: Literal[
        "localization-document-brief-prompt-v13",
        "localization-document-brief-prompt-v14",
    ] = PROMPT_VERSION
    source_fingerprint: str = Field(min_length=64, max_length=64)
    context_intent_fingerprint: str = Field(min_length=64, max_length=64)
    result_fingerprint: str = Field(min_length=64, max_length=64)
    dynamic_rule_ids: list[str] = Field(default_factory=list)
    content: LocalizationDocumentBriefContent
    route: LocalizationAiPhaseRoute
    llm_calls: list[VideoLocalizationLlmCallRecord]
    recovered_candidates: list[RecoveredLlmCandidateProvenance] = Field(default_factory=list)
    quality_summary: LocalizationDocumentBriefQualitySummary

    @model_validator(mode="before")
    @classmethod
    def require_call_or_recovered_evidence(cls, value):
        if isinstance(value, dict) and value.get("llm_calls") == [] and not value.get("recovered_candidates"):
            raise ValueError("没有真实调用记录或已核验恢复来源的简报不能完成。")
        return value

    @model_validator(mode="after")
    def validate_storage_capacity(self) -> "LocalizationDocumentBriefResult":
        summary = self.quality_summary
        if self.recovered_candidates:
            if self.contract_version != "localization-document-brief-v3":
                raise ValueError("恢复候选来源只能由 v3 简报明确表示，不能伪装成历史 v2。")
            if (summary.status != "warning" or summary.model_call_count is not None
                    or summary.recorded_model_call_count != len(self.llm_calls)
                    or summary.recovered_candidate_count != len(self.recovered_candidates)
                    or summary.call_telemetry_complete is not False):
                raise ValueError("恢复候选缺失原始遥测，必须如实标记未知调用总数及恢复来源。")
            identities = {(item.batch_id, item.attempt) for item in self.recovered_candidates}
            if len(identities) != len(self.recovered_candidates):
                raise ValueError("恢复候选来源批次重复。")
        elif summary.model_call_count is None:
            raise ValueError("没有真实调用记录或已核验恢复来源的简报不能完成。")
        if self.contract_version == "localization-document-brief-v3":
            validate_localization_brief_storage_capacity(self.content)
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, serialize):
        result = serialize(self)
        if not self.recovered_candidates:
            result.pop("recovered_candidates", None)
        return result


class LocalizationDocumentBriefAdaptiveRules(BaseModel):
    """Ordered rule paragraphs and their matching IDs, without an output schema.

    The fixed strategy returns both lists empty. The builder does not alter
    wording or source-field names for another caller's payload projection.
    """

    model_config = ConfigDict(extra="forbid")

    paragraphs: list[str]
    rule_ids: list[str]


class LocalizationDocumentBriefSourceCue(BaseModel):
    """Meaning-facing cue projection; processing metadata is filtered upstream."""

    model_config = ConfigDict(extra="forbid")

    cue_id: str = Field(pattern=r"^cue_\d{4}$")
    text: str
    speaker_id: str | None
    quality_flags: list[str]


class LocalizationDocumentBriefSourcePayload(BaseModel):
    """Typed existing brief payload, without source-lock processing internals."""

    model_config = ConfigDict(extra="forbid")

    source_fingerprint: str = Field(min_length=64, max_length=64)
    target_language: str = Field(min_length=1)
    document_context: LocalizationDocumentContext
    delivery_intent: LocalizationDeliveryIntent
    source_cues: list[LocalizationDocumentBriefSourceCue] = Field(min_length=1)
    glossary: list[LocalizationSourceGlossaryEntry]
