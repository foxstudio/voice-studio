"""Evidence-backed canonical name and terminology normalization."""

from __future__ import annotations

import time
from importlib import import_module
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization import (
    research_evidence,
    visual_name_evidence as visual_name_evidence_domain,
)
from app.domains.video_localization.review_contracts import (
    AsrLockedTranscriptChange,
)
from app.domains.video_localization.llm_observability import (
    AsrLlmCallRecord,
    AsrLlmTraceCollector,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationGlossaryEntry,
    VideoLocalizationTranscriptSegment,
)
from app.services import llm_runtime


PROMPT_VERSION = "asr-entity-normalization-v1"
GlossaryApplier = Callable[
    [
        list[VideoLocalizationTranscriptSegment],
        list[VideoLocalizationGlossaryEntry],
    ],
    tuple[list[VideoLocalizationTranscriptSegment], list[dict]],
]
ResearchedEntityNormalizer = Callable[
    ...,
    tuple[
        list[VideoLocalizationTranscriptSegment],
        list[dict],
        list[dict],
        list[dict],
    ],
]
CompletionGateway = Callable[[object], dict]


class AsrEntityNormalizationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "asr-entity-normalization-input-v1",
        "asr-entity-normalization-input-v2",
    ] = (
        "asr-entity-normalization-input-v1"
    )
    upstream_contract_version: Literal[
        "asr-research-evidence-v1",
        "asr-research-evidence-v2",
    ]
    upstream_operation_id: str = Field(min_length=1)
    source_track_id: str = Field(min_length=1)
    source_audio_sha256: str = Field(min_length=1)
    language: str = Field(min_length=1)
    profile_id: str | None = None
    document_summary: str = Field(min_length=1)
    segments: list[research_evidence.AsrResearchEvidenceSegment] = Field(
        min_length=1
    )
    candidates: list[research_evidence.AsrResearchEvidenceCandidate] = Field(
        default_factory=list
    )
    evidence: list[research_evidence.AsrResearchEvidenceItem] = Field(
        default_factory=list
    )
    visual_evidence_operation_id: str | None = None
    visual_name_evidence: list[
        visual_name_evidence_domain.AsrVisualNameEvidence
    ] = Field(
        default_factory=list,
        max_length=24,
    )
    glossary: list[VideoLocalizationGlossaryEntry] = Field(default_factory=list)
    locked_changes: list[AsrLockedTranscriptChange] = Field(
        default_factory=list
    )

    @classmethod
    def from_research_evidence(
        cls,
        result: research_evidence.AsrResearchEvidenceResult,
        *,
        upstream_operation_id: str,
        glossary: list[VideoLocalizationGlossaryEntry] | None = None,
        locked_changes: list[AsrLockedTranscriptChange] | None = None,
    ) -> AsrEntityNormalizationInput:
        visual_input = (
            result.input
            if isinstance(
                result.input,
                research_evidence.AsrResearchEvidenceInputV2,
            )
            else None
        )
        return cls(
            contract_version=(
                "asr-entity-normalization-input-v2"
                if visual_input is not None
                else "asr-entity-normalization-input-v1"
            ),
            upstream_contract_version=result.contract_version,
            upstream_operation_id=upstream_operation_id,
            source_track_id=result.input.source_track_id,
            source_audio_sha256=result.input.source_audio_sha256,
            language=result.input.language,
            profile_id=result.profile_id or result.input.profile_id,
            document_summary=result.input.document_summary,
            segments=result.input.segments,
            candidates=result.input.candidates,
            evidence=result.evidence,
            visual_evidence_operation_id=(
                visual_input.visual_evidence_operation_id
                if visual_input is not None
                else None
            ),
            visual_name_evidence=(
                visual_name_evidence_domain.build_visual_name_evidence(
                    segments=result.input.segments,
                    candidates=result.input.candidates,
                    visual_hints=visual_input.visual_hints,
                )
                if visual_input is not None
                else []
            ),
            glossary=list(glossary or []),
            locked_changes=list(locked_changes or []),
        )


class AsrEntityResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_name: str = Field(min_length=1)
    variants: list[str] = Field(default_factory=list)
    role: str = ""
    confidence: float = Field(ge=0, le=1)
    evidence_source_ids: list[str] = Field(default_factory=list)


class AsrEntityNormalizationChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1)
    before: str
    after: str
    reason: str
    confidence: float = Field(ge=0, le=1)
    evidence_source_ids: list[str] = Field(default_factory=list)
    replacement: str | None = None


def _normalization_change(
    item: dict,
) -> AsrEntityNormalizationChange:
    """Project shared review output onto this task's strict public contract."""

    allowed_fields = {
        "segment_id",
        "before",
        "after",
        "reason",
        "confidence",
        "evidence_source_ids",
        "replacement",
    }
    return AsrEntityNormalizationChange.model_validate(
        {
            key: value
            for key, value in item.items()
            if key in allowed_fields
        }
    )


class AsrEntityNormalizationWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["needs_confirmation"] = "needs_confirmation"
    segment_id: str = ""
    excerpt: str = ""
    message: str = Field(min_length=1)


class AsrEntityNormalizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-entity-normalization-v1"] = (
        "asr-entity-normalization-v1"
    )
    input: AsrEntityNormalizationInput
    status: Literal["not_needed", "completed", "partial", "failed"]
    profile_id: str | None = None
    model_id: str | None = None
    prompt_version: Literal["asr-entity-normalization-v1"] = PROMPT_VERSION
    resolutions: list[AsrEntityResolution] = Field(default_factory=list)
    updated_segments: list[VideoLocalizationTranscriptSegment] = Field(
        default_factory=list
    )
    changes: list[AsrEntityNormalizationChange] = Field(default_factory=list)
    warnings: list[AsrEntityNormalizationWarning] = Field(default_factory=list)
    llm_calls: list[AsrLlmCallRecord] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)


class EntityNormalizationService:
    """Single atomic entry point shared by formal and development workflows."""

    def __init__(
        self,
        *,
        glossary_applier: GlossaryApplier | None = None,
        researched_entity_normalizer: (
            ResearchedEntityNormalizer | None
        ) = None,
    ) -> None:
        if (glossary_applier is None) != (
            researched_entity_normalizer is None
        ):
            raise ValueError(
                "entity normalization ports must be supplied together"
            )
        self._glossary_applier = glossary_applier
        self._researched_entity_normalizer = (
            researched_entity_normalizer
        )

    def run(
        self,
        request: AsrEntityNormalizationInput,
        *,
        is_cancelled: Callable[[], bool] | None = None,
        completion_gateway: CompletionGateway | None = None,
    ) -> AsrEntityNormalizationResult:
        if self._glossary_applier is None:
            runtime = import_module(
                "app.domains.video_localization."
                "entity_normalization_runtime"
            )
            return runtime.DEFAULT_ENTITY_NORMALIZATION_SERVICE.run(
                request,
                is_cancelled=is_cancelled,
                completion_gateway=completion_gateway,
            )
        assert self._researched_entity_normalizer is not None
        started_at = time.perf_counter()
        trace_collector = AsrLlmTraceCollector()
        if is_cancelled and is_cancelled():
            raise llm_runtime.LlmRuntimeError(
                "名称与术语统一已取消",
                code="llm_cancelled",
                status_code=409,
            )
        segments = [
            VideoLocalizationTranscriptSegment(
                segment_id=item.segment_id,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                raw_text=item.text,
                speaker_cluster_id=item.speaker_cluster_id,
            )
            for item in request.segments
        ]
        segments, glossary_changes = self._glossary_applier(
            segments,
            request.glossary,
        )
        locked_changes = [
            item.model_dump(mode="json") for item in request.locked_changes
        ]
        locked_changes.extend(glossary_changes)
        visual_evidence = _qualified_visual_evidence(request)
        if not requires_model_call(request) or not request.profile_id:
            return AsrEntityNormalizationResult(
                input=request.model_copy(deep=True),
                status="completed" if glossary_changes else "not_needed",
                profile_id=request.profile_id,
                updated_segments=segments,
                changes=[
                    _normalization_change(item)
                    for item in glossary_changes
                ],
                llm_calls=[],
                duration_ms=_elapsed_ms(started_at),
            )
        evidence = [
            {
                "source_id": item.evidence_id,
                "source_type": "web",
                "candidate_id": item.candidate_id,
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
            }
            for item in request.evidence
        ]
        evidence.extend(visual_evidence)
        updated, resolutions, entity_changes, warnings = (
            self._researched_entity_normalizer(
                segments=segments,
                document_summary=request.document_summary,
                candidates=[
                    item.model_dump(mode="json")
                    for item in request.candidates
                ],
                evidence=evidence,
                profile_id=request.profile_id,
                locked_changes=locked_changes,
                resolution_trace_sink_factory=lambda attempt: (
                    trace_collector.sink(
                        call_id=f"entity-resolution-a{attempt:02d}",
                        purpose="entity_resolution",
                        round_index=attempt,
                        candidate_ids=[
                            item.candidate_id for item in request.candidates
                        ],
                    )
                ),
                variant_trace_sink_factory=lambda attempt: (
                    trace_collector.sink(
                        call_id=f"entity-variant-mapping-a{attempt:02d}",
                        purpose="entity_variant_mapping",
                        round_index=attempt,
                        candidate_ids=[
                            item.candidate_id for item in request.candidates
                        ],
                    )
                ),
                completion_gateway=completion_gateway,
            )
        )
        changes = [*glossary_changes, *entity_changes]
        llm_calls = trace_collector.records()
        return AsrEntityNormalizationResult(
            input=request.model_copy(deep=True),
            status="partial" if warnings else "completed",
            profile_id=request.profile_id,
            model_id=llm_calls[0].model_id if llm_calls else None,
            resolutions=[
                AsrEntityResolution.model_validate(item)
                for item in resolutions
            ],
            updated_segments=updated,
            changes=[
                _normalization_change(item)
                for item in changes
            ],
            warnings=[
                AsrEntityNormalizationWarning.model_validate(item)
                for item in warnings
            ],
            llm_calls=llm_calls,
            duration_ms=_elapsed_ms(started_at),
        )


def _qualified_visual_evidence(
    request: AsrEntityNormalizationInput,
) -> list[dict]:
    """Project only strong, candidate-bound screen text into review evidence."""

    if request.contract_version != "asr-entity-normalization-input-v2":
        return []
    if not (request.visual_evidence_operation_id or "").strip():
        return []
    output: list[dict] = []
    for item in request.visual_name_evidence:
        frame_ids = [
            value.strip()
            for value in item.frame_ids
            if value.strip()
        ]
        if not frame_ids:
            continue
        output.append(
            {
                "source_id": item.evidence_id,
                "source_type": "visual_text",
                "candidate_id": item.candidate_id,
                "candidate_target_terms": [
                    item.transcript_variant
                ],
                "title": "画面中逐字可见的姓名或专名候选",
                "snippet": item.visible_name,
                "canonical_text": item.visible_name,
                "confidence": item.confidence,
                "visual_evidence_operation_id": (
                    request.visual_evidence_operation_id
                ),
                "frame_ids": frame_ids,
                "source_segment_ids": item.source_segment_ids,
                "match_basis": item.match_basis,
            }
        )
    return output


def _elapsed_ms(started_at: float) -> int:
    return max(0, int(round((time.perf_counter() - started_at) * 1000)))


DEFAULT_ENTITY_NORMALIZATION_SERVICE = EntityNormalizationService()


def requires_model_call(
    request: AsrEntityNormalizationInput,
) -> bool:
    """Return whether evidence-backed canonicalization needs a model."""

    return bool(
        request.candidates
        and (request.evidence or _qualified_visual_evidence(request))
    )
