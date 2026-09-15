"""Versioned, data-only contracts for ASR document understanding."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization.llm_contracts import AsrLlmCallRecord
from app.domains.video_localization.research_limits import (
    MAX_RESEARCH_TARGET_TERMS,
)


class AsrDocumentUnderstandingSegment(BaseModel):
    """One stable joined-transcript segment supplied to document understanding."""

    model_config = ConfigDict(extra="forbid")

    ordinal: int = Field(ge=1)
    segment_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str = Field(min_length=1)
    speaker_cluster_id: str | None = None
    speaker_confidence: float | None = Field(default=None, ge=0, le=1)
    has_speaker_overlap: bool = False


class AsrDocumentEntityCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    role: str = ""
    needs_research: bool = False


class AsrDocumentResearchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    category: Literal["proper_noun", "background", "culture", "persona"] = (
        "background"
    )
    reason: str = ""
    target_terms: list[str] = Field(
        default_factory=list,
        max_length=MAX_RESEARCH_TARGET_TERMS,
    )


class AsrDocumentVisualQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    start_ordinal: int = Field(ge=1)
    end_ordinal: int = Field(ge=1)
    start_segment_id: str = Field(min_length=1)
    end_segment_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    kind: Literal[
        "visible_text",
        "chart",
        "object",
        "scene_context",
    ]
    reason: str = Field(min_length=1)
    question: str = Field(min_length=1)
    frame_strategy: Literal["nearby", "look_ahead"] = "nearby"


class AsrDocumentReviewSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1)
    start_ordinal: int = Field(ge=1)
    end_ordinal: int = Field(ge=1)
    start_segment_id: str = Field(min_length=1)
    end_segment_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    focus: list[str] = Field(min_length=1)


class AsrDocumentUnderstandingBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_kind: Literal[
        "film_or_drama",
        "interview",
        "tutorial",
        "presentation",
        "conversation",
        "other",
        "unknown",
    ] = "unknown"
    summary: str = Field(min_length=1)
    content_logic: list[str] = Field(min_length=1)
    speaker_style: str = Field(min_length=1)
    language_notes: list[str] = Field(default_factory=list, max_length=12)
    entity_candidates: list[AsrDocumentEntityCandidate] = Field(
        default_factory=list
    )
    research_candidates: list[AsrDocumentResearchCandidate] = Field(
        default_factory=list
    )
    visual_questions: list[AsrDocumentVisualQuestion] = Field(
        default_factory=list
    )
    review_sections: list[AsrDocumentReviewSection] = Field(min_length=1)


class AsrDocumentUnderstandingRawResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    response_json: str | None = Field(
        default=None,
        description=(
            "Unstable debugging payload serialized as JSON; callers must not "
            "treat it as a business contract."
        ),
    )
    error_code: str | None = None
    error: str | None = None


class AsrDocumentUnderstandingStageTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_ms: int = Field(ge=0)


class AsrDocumentUnderstandingQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning", "failed"]
    segment_count: int = Field(ge=1)
    section_count: int = Field(ge=1)
    sections_cover_all_segments: bool
    source_text_unchanged: bool


class AsrDocumentUnderstandingInput(BaseModel):
    """Versioned input for the first atomic task in transcript review."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-document-understanding-input-v1"] = (
        "asr-document-understanding-input-v1"
    )
    upstream_contract_version: Literal[
        "asr-raw-v2",
        "asr-joined-transcript-v1",
    ] = "asr-joined-transcript-v1"
    upstream_operation_id: str = Field(min_length=1)
    source_track_id: str = Field(min_length=1)
    source_audio_sha256: str = Field(min_length=1)
    language: str = Field(min_length=1)
    scene_context: str = ""
    profile_id: str | None = None
    segments: list[AsrDocumentUnderstandingSegment] = Field(min_length=1)

    @classmethod
    def from_joined_transcript(
        cls,
        joined: Any,
        *,
        upstream_operation_id: str,
        profile_id: str | None = None,
        scene_context: str = "",
    ) -> AsrDocumentUnderstandingInput:
        """Project the internal joined-transcript shape without importing its facade."""

        return cls(
            upstream_operation_id=upstream_operation_id,
            source_track_id=joined.raw_asr.input.source_track_id,
            source_audio_sha256=joined.raw_asr.input.audio_sha256,
            language=joined.raw_asr.language
            or joined.raw_asr.input.requested_language,
            scene_context=scene_context,
            profile_id=profile_id,
            segments=[
                AsrDocumentUnderstandingSegment(
                    ordinal=index,
                    segment_id=segment.segment_id,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=(segment.corrected_text or segment.raw_text).strip(),
                    speaker_cluster_id=segment.speaker_cluster_id,
                    speaker_confidence=segment.speaker_confidence,
                    has_speaker_overlap=segment.has_speaker_overlap,
                )
                for index, segment in enumerate(joined.segments, start=1)
                if (segment.corrected_text or segment.raw_text).strip()
            ],
        )


class AsrDocumentUnderstandingResult(BaseModel):
    """Document understanding only; it performs no research or text edits."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-document-understanding-v1"] = (
        "asr-document-understanding-v1"
    )
    input: AsrDocumentUnderstandingInput
    brief: AsrDocumentUnderstandingBrief
    profile_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    execution_strategy: Literal["full_document", "windowed"]
    window_count: int = Field(ge=0)
    llm_call_count: int = Field(ge=1)
    retry_count: int = Field(ge=0)
    stage_timing: AsrDocumentUnderstandingStageTiming
    quality_summary: AsrDocumentUnderstandingQualitySummary
    llm_calls: list[AsrLlmCallRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw_responses: list[AsrDocumentUnderstandingRawResponse] = Field(
        default_factory=list
    )


__all__ = [
    "AsrDocumentEntityCandidate",
    "AsrDocumentResearchCandidate",
    "AsrDocumentReviewSection",
    "AsrDocumentUnderstandingBrief",
    "AsrDocumentUnderstandingInput",
    "AsrDocumentUnderstandingQualitySummary",
    "AsrDocumentUnderstandingRawResponse",
    "AsrDocumentUnderstandingResult",
    "AsrDocumentUnderstandingSegment",
    "AsrDocumentUnderstandingStageTiming",
    "AsrDocumentVisualQuestion",
]
