"""Provider-neutral, privacy-safe LLM call observability contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


VideoLocalizationLlmCallPurpose = Literal[
    "document_understanding",
    "visual_analysis",
    "query_rewrite",
    "evidence_assessment",
    "entity_resolution",
    "entity_variant_mapping",
    "section_review",
    "review_decisions",
    "whole_recheck",
    "localization_document_brief",
    "localization_document_evidence_adjudication",
    "localization_spoken_script_generation",
    "localization_fidelity_review",
    "localization_naturalness_review",
    "localization_spoken_script_finalization",
    "localization_fidelity_closure_review",
    "localization_naturalness_closure_review",
    "localization_fidelity_finalization_regression",
    "localization_naturalness_finalization_regression",
    "localization_alignment_adjudication",
    "semantic_tts_grouping",
]


class VideoLocalizationLlmCallRecord(BaseModel):
    """One model call without prompts, outputs, credentials, or reasoning."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    purpose: VideoLocalizationLlmCallPurpose
    round_index: int = Field(ge=1)
    candidate_ids: list[str] = Field(default_factory=list)
    question_id: str | None = None
    profile_id: str
    model_id: str
    provider_host: str
    request_chars: int = Field(ge=0)
    request_body_bytes: int = Field(ge=0)
    max_tokens: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)
    reasoning_effort_requested: Literal["low", "high", "max"] | None
    reasoning_control_applied: bool
    duration_ms: int = Field(ge=0)
    finish_reason: str | None = None
    native_finish_reason: str | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    content_chars: int = Field(ge=0)
    reasoning_chars: int = Field(ge=0)
    response_id: str | None = None
    error_code: str | None = None

    @classmethod
    def from_runtime(
        cls,
        trace: Any,
        *,
        call_id: str,
        purpose: VideoLocalizationLlmCallPurpose,
        round_index: int,
        candidate_ids: list[str] | None = None,
        question_id: str | None = None,
    ) -> "VideoLocalizationLlmCallRecord":
        return cls(
            call_id=call_id,
            purpose=purpose,
            round_index=round_index,
            candidate_ids=list(candidate_ids or []),
            question_id=question_id,
            **trace.__dict__,
        )


__all__ = [
    "VideoLocalizationLlmCallPurpose",
    "VideoLocalizationLlmCallRecord",
]
