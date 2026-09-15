"""Read-only, versioned capacity evidence for one planned dubbing group."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DubbingPreflightTextUnits(_StrictContract):
    """A script-neutral text measurement, never an alignment-token count."""

    granularity: Literal["unicode_letter_number_codepoints-v1"] = (
        "unicode_letter_number_codepoints-v1"
    )
    unit_count: int = Field(ge=0)
    script_profile: Literal["single_family", "mixed_or_indeterminate"]
    uncertainty_codes: list[str] = Field(default_factory=list)


class DubbingPreflightReferenceObservation(_StrictContract):
    candidate_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    engine_id: str = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    speed: float = Field(gt=0)
    speech_span_ms: int = Field(gt=0)
    text_units: DubbingPreflightTextUnits
    timeline_distance_ms: int = Field(ge=0)


class DubbingPreflightEstimateEvidence(_StrictContract):
    method: Literal[
        "frozen_same_engine_speaker_speed_text_units-v1",
        "unavailable",
    ]
    confidence: Literal["none", "low", "medium", "high"]
    reference_observations: list[DubbingPreflightReferenceObservation] = (
        Field(default_factory=list, max_length=3)
    )
    uncertainty_codes: list[str] = Field(default_factory=list)


class DubbingGroupPreflightResult(_StrictContract):
    """Pure pre-generation capacity assessment; it does not reserve or edit time."""

    schema_version: Literal["dubbing-group-preflight-v1"] = (
        "dubbing-group-preflight-v1"
    )
    group_id: str = Field(min_length=1)
    frozen_speed: float = Field(gt=0)
    target_start_ms: int = Field(ge=0)
    target_end_ms: int = Field(gt=0)
    usable_start_ms: int = Field(ge=0)
    usable_end_ms: int = Field(ge=0)
    usable_duration_ms: int = Field(ge=0)
    text_units: DubbingPreflightTextUnits
    estimated_speech_duration_ms: int | None = Field(default=None, gt=0)
    estimate_evidence: DubbingPreflightEstimateEvidence
    status: Literal["ready", "warning", "blocked"]
    reason_codes: list[str] = Field(default_factory=list)
    message: str = Field(min_length=1)
