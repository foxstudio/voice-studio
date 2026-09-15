"""Immutable document context and delivery requirements for localization v3.

The source lock deliberately owns only ASR text and timing evidence.  This
companion contract keeps the already-reviewed document brief and the chosen
delivery goal immutable without invalidating existing source-lock snapshots.
It performs no model calls.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization import localization_requirements
from app.domains.video_localization.localization_source import (
    LocalizationSourceLockResult,
)
from app.domains.video_localization.schemas import VideoLocalizationDraft


ContextProvenance = Literal[
    "asr_document_brief",
    "project_scene_context",
    "not_available",
]
ContextIntentWarningCode = Literal["document_context_missing"]


class LocalizationSourceUncertainty(BaseModel):
    """Rare ASR term that upstream understanding could not verify."""

    model_config = ConfigDict(extra="forbid")

    term: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=500)
    source_cue_ids: list[str] = Field(min_length=1, max_length=3)


class LocalizationDocumentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    content_logic: list[str] = Field(default_factory=list)
    speaker_style: str = ""
    source_uncertainties: list[LocalizationSourceUncertainty] = Field(
        default_factory=list,
        max_length=8,
    )
    provenance: ContextProvenance = "not_available"


class LocalizationDeliveryIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements_profile_id: str = Field(min_length=1)
    requirements_profile_version: str = Field(min_length=1)
    requirements_fingerprint: str = Field(min_length=64, max_length=64)
    selection_source: Literal["project_default", "request"]
    target_language: str = Field(min_length=1)
    target_locale: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    deliverables: localization_requirements.LocalizationDeliverables
    expression: (
        localization_requirements.LocalizationExpressionRequirements
    )
    timing: localization_requirements.LocalizationTimingRequirements
    source_reference: (
        localization_requirements.LocalizationSourceReferenceRequirements
    )
    subtitle: localization_requirements.LocalizationSubtitleRequirements


class LocalizationContextIntentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-context-intent-input-v2"
    ] = "localization-context-intent-input-v2"
    upstream_contract_version: Literal["localization-source-lock-v1"] = (
        "localization-source-lock-v1"
    )
    upstream_operation_id: str = Field(min_length=1)
    source_lock: LocalizationSourceLockResult
    document_context: LocalizationDocumentContext
    delivery_intent: LocalizationDeliveryIntent


class LocalizationContextIntentWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ContextIntentWarningCode
    message: str = Field(min_length=1)


class LocalizationContextIntentQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning"]
    has_document_summary: bool
    has_content_logic: bool
    has_speaker_style: bool
    expression_rule_count: int = Field(ge=0)
    model_call_count: Literal[0] = 0


class LocalizationContextIntentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["localization-context-intent-v2"] = (
        "localization-context-intent-v2"
    )
    input: LocalizationContextIntentInput
    source_fingerprint: str = Field(min_length=64, max_length=64)
    context_intent_fingerprint: str = Field(min_length=64, max_length=64)
    warnings: list[LocalizationContextIntentWarning] = Field(
        default_factory=list
    )
    quality_summary: LocalizationContextIntentQualitySummary


def build_localization_context_intent_input(
    draft: VideoLocalizationDraft,
    *,
    source_lock: LocalizationSourceLockResult,
    upstream_operation_id: str,
    requirements_profile: (
        localization_requirements.LocalizationRequirementsProfile
    ),
    selection_source: Literal["project_default", "request"],
) -> LocalizationContextIntentInput:
    """Adapt the saved ASR brief into the narrow localization context."""

    quality_cycle = (
        draft.transcription.transcript_quality_cycle
        if draft.transcription is not None
        and isinstance(
            draft.transcription.transcript_quality_cycle,
            dict,
        )
        else {}
    )
    raw_brief = quality_cycle.get("document_brief")
    raw_brief = raw_brief if isinstance(raw_brief, dict) else {}
    summary = _bounded_text(raw_brief.get("summary"), 1_000)
    content_logic = _bounded_text_list(
        raw_brief.get("logic"),
        item_limit=300,
        count_limit=12,
    )
    speaker_style = _bounded_text(
        raw_brief.get("speaker_style"),
        500,
    )
    source_uncertainties = _source_uncertainties(
        raw_brief.get("entities"),
        source_lock=source_lock,
    )
    provenance: ContextProvenance = (
        "asr_document_brief"
        if summary or content_logic or speaker_style
        else "project_scene_context"
        if str(source_lock.input.scene_context or "").strip()
        else "not_available"
    )
    if not summary and provenance == "project_scene_context":
        summary = _bounded_text(source_lock.input.scene_context, 1_000)
    return LocalizationContextIntentInput(
        upstream_operation_id=upstream_operation_id,
        source_lock=source_lock,
        document_context=LocalizationDocumentContext(
            summary=summary,
            content_logic=content_logic,
            speaker_style=speaker_style,
            source_uncertainties=source_uncertainties,
            provenance=provenance,
        ),
        delivery_intent=LocalizationDeliveryIntent(
            requirements_profile_id=requirements_profile.profile_id,
            requirements_profile_version=(
                requirements_profile.profile_version
            ),
            requirements_fingerprint=requirements_profile.fingerprint,
            selection_source=selection_source,
            target_language=requirements_profile.target_language,
            target_locale=requirements_profile.target_locale,
            audience=requirements_profile.audience,
            deliverables=requirements_profile.deliverables,
            expression=requirements_profile.expression,
            timing=requirements_profile.timing,
            source_reference=requirements_profile.source_reference,
            subtitle=requirements_profile.subtitle,
        ),
    )


def _source_uncertainties(
    raw_entities: object,
    *,
    source_lock: LocalizationSourceLockResult,
) -> list[LocalizationSourceUncertainty]:
    """Carry only rare, source-locatable ASR uncertainties downstream."""

    if not isinstance(raw_entities, list):
        return []
    resolved: list[LocalizationSourceUncertainty] = []
    for raw in raw_entities:
        if not isinstance(raw, dict) or not raw.get("needs_research"):
            continue
        raw_name = _bounded_text(raw.get("name"), 160)
        terms = [
            item.strip()
            for item in re.split(r"\s*/\s*|\s*\|\s*", raw_name)
            if item.strip()
        ]
        if not terms:
            continue
        patterns = [
            re.compile(
                rf"(?<![\w]){re.escape(term)}(?![\w])",
                re.IGNORECASE,
            )
            for term in terms
        ]
        source_cue_ids = [
            cue.cue_id
            for cue in source_lock.input.cues
            if any(pattern.search(cue.text) for pattern in patterns)
        ]
        if not 1 <= len(source_cue_ids) <= 3:
            continue
        resolved.append(
            LocalizationSourceUncertainty(
                term=raw_name,
                reason=(
                    _bounded_text(raw.get("role"), 500)
                    or "上游 ASR 全文理解标记为待核实词。"
                ),
                source_cue_ids=source_cue_ids,
            )
        )
    cue_order = {
        cue.cue_id: index
        for index, cue in enumerate(source_lock.input.cues)
    }
    return sorted(
        resolved,
        key=lambda item: cue_order[item.source_cue_ids[0]],
    )[:8]


class LocalizationContextIntentPipeline:
    """Public deterministic facade for locking context and intent."""

    def lock(
        self,
        request: LocalizationContextIntentInput,
    ) -> LocalizationContextIntentResult:
        if (
            request.source_lock.contract_version
            != request.upstream_contract_version
        ):
            raise ValueError("本土化上下文收到的源输入契约版本不一致。")
        warnings = []
        context = request.document_context
        if not (
            context.summary
            or context.content_logic
            or context.speaker_style
        ):
            warnings.append(
                LocalizationContextIntentWarning(
                    code="document_context_missing",
                    message=(
                        "上游没有保存可用的全文背景；后续仍可继续，"
                        "但翻译只能依赖连续语义单元和局部上下文。"
                    ),
                )
            )
        payload = {
            "source_fingerprint": request.source_lock.source_fingerprint,
            "document_context": context.model_dump(mode="json"),
            "delivery_intent": request.delivery_intent.model_dump(
                mode="json"
            ),
        }
        return LocalizationContextIntentResult(
            input=request,
            source_fingerprint=request.source_lock.source_fingerprint,
            context_intent_fingerprint=_fingerprint(payload),
            warnings=warnings,
            quality_summary=LocalizationContextIntentQualitySummary(
                status="warning" if warnings else "passed",
                has_document_summary=bool(context.summary),
                has_content_logic=bool(context.content_logic),
                has_speaker_style=bool(context.speaker_style),
                expression_rule_count=sum(
                    len(values)
                    for values in (
                        request.delivery_intent.expression.preserve,
                        request.delivery_intent.expression.adapt,
                        request.delivery_intent.expression.avoid,
                    )
                ),
            ),
        )


def project_localization_context_intent_step_result(
    result: LocalizationContextIntentResult,
) -> dict:
    context = result.input.document_context
    intent = result.input.delivery_intent
    context_items = [
        {
            "title": "内容概述",
            "text": context.summary or "上游未保存全文概述。",
            "facts": [
                {
                    "label": "内容逻辑",
                    "value": (
                        "；".join(context.content_logic)
                        if context.content_logic
                        else "未保存"
                    ),
                },
                {
                    "label": "说话方式",
                    "value": context.speaker_style or "未保存",
                },
            ],
            "links": [],
            "tone": "warning" if result.warnings else "neutral",
        }
    ]
    return {
        "label": "固定本次本土化要求",
        "order": 15,
        "status": "warning" if result.warnings else "success",
        "purpose": (
            "把本次要交付什么、中文要怎么表达、时间怎么对应以及"
            "ASR 概述如何使用固定下来，供后续所有步骤共同读取。"
        ),
        "summary": (
            f"已采用“{intent.requirements_profile_id}”"
            f"（{_selection_source_label(intent.selection_source)}），"
            f"交付内容为 {intent.deliverables.label}。"
        ),
        "metrics": [
            {
                "label": "目标语言",
                "value": intent.target_language,
            },
            {
                "label": "交付方式",
                "value": intent.deliverables.label,
            },
            {
                "label": "时间对应方式",
                "value": intent.timing.label,
            },
            {
                "label": "配置来源",
                "value": _selection_source_label(
                    intent.selection_source
                ),
            },
        ],
        "sections": [
            {"title": "锁定内容", "items": context_items},
            {
                "title": "交付目标",
                "items": [
                    {
                        "title": "中文表达目标",
                        "text": intent.expression.goal,
                        "facts": [
                            {
                                "label": "配置",
                                "value": (
                                    f"{intent.requirements_profile_id} "
                                    f"v{intent.requirements_profile_version}"
                                ),
                            },
                            {
                                "label": "字幕规范",
                                "value": intent.subtitle.label,
                            },
                        ],
                        "links": [],
                        "tone": "neutral",
                    }
                ],
            },
            {
                "title": "时间与上游资料",
                "items": [
                    {
                        "title": intent.timing.label,
                        "text": intent.timing.description,
                        "facts": [
                            {
                                "label": "英文字幕逐条对齐",
                                "value": (
                                    "需要"
                                    if intent.timing
                                    .source_cue_alignment_required
                                    else "不需要"
                                ),
                            },
                            {
                                "label": "ASR 全文概述",
                                "value": "只作参考，本土化仍重新通读全文",
                            },
                        ],
                        "links": [],
                        "tone": "neutral",
                    }
                ],
            },
        ],
        "review_targets": [
            {"title": "上下文提醒", "detail": item.message}
            for item in result.warnings
        ],
        "coverage": {
            "mode": "complete",
            "reason": "本步骤完整展示锁定的全文背景与交付目标。",
            "shown_count": 1,
            "total_count": 1,
            "unit": "份本土化目标",
        },
        "debug": {
            "description": (
                "用于确认上下文来源、交付目标和指纹；本步骤不调用模型。"
            ),
            "metrics": [
                {
                    "label": "输入契约",
                    "value": result.input.contract_version,
                },
                {
                    "label": "上游契约",
                    "value": result.input.upstream_contract_version,
                },
                {
                    "label": "输出契约",
                    "value": result.contract_version,
                },
                {
                    "label": "要求配置指纹",
                    "value": intent.requirements_fingerprint,
                },
                {
                    "label": "上下文来源",
                    "value": _provenance_label(context.provenance),
                },
                {"label": "模型调用", "value": "0 次"},
            ],
            "items": [
                {
                    "title": "版本指纹",
                    "text": (
                        f"源输入 {result.source_fingerprint[:12]}…；"
                        f"上下文与目标 "
                        f"{result.context_intent_fingerprint[:12]}…"
                    ),
                }
            ],
        },
    }


def _bounded_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_text_list(
    value: object,
    *,
    item_limit: int,
    count_limit: int,
) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item
        for raw in value[:count_limit]
        if (item := _bounded_text(raw, item_limit))
    ]


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _selection_source_label(
    value: Literal["project_default", "request"],
) -> str:
    return {
        "project_default": "项目默认配置",
        "request": "本次任务指定",
    }[value]


def _provenance_label(value: ContextProvenance) -> str:
    return {
        "asr_document_brief": "ASR 全文理解",
        "project_scene_context": "项目场景说明",
        "not_available": "未保存",
    }[value]


DEFAULT_LOCALIZATION_CONTEXT_INTENT_PIPELINE = (
    LocalizationContextIntentPipeline()
)


__all__ = [
    "DEFAULT_LOCALIZATION_CONTEXT_INTENT_PIPELINE",
    "LocalizationContextIntentInput",
    "LocalizationContextIntentPipeline",
    "LocalizationContextIntentQualitySummary",
    "LocalizationContextIntentResult",
    "LocalizationContextIntentWarning",
    "LocalizationDeliveryIntent",
    "LocalizationDocumentContext",
    "build_localization_context_intent_input",
    "project_localization_context_intent_step_result",
]
