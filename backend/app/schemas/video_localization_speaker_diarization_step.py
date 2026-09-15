"""Versioned, path-free contracts for standalone speaker diarization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


SPEAKER_DIARIZATION_STEP_ID = "diarization"
SPEAKER_DIARIZATION_STEP_INPUT_SCHEMA_VERSION = (
    "speaker-diarization-step-input-v1"
)
SPEAKER_DIARIZATION_STEP_OUTPUT_SCHEMA_VERSION = (
    "speaker-diarization-step-output-v1"
)
SPEAKER_DIARIZATION_WORKFLOW_VERSION = (
    "speaker-diarization-workflow-v1"
)
SPEAKER_DIARIZATION_RESULT_CONTRACT_VERSION = (
    "speaker-diarization-v1"
)
MAX_SPEAKER_COUNT_GUIDANCE = 50


class SpeakerDiarizationStepInputV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "speaker-diarization-step-input-v1"
    ] = SPEAKER_DIARIZATION_STEP_INPUT_SCHEMA_VERSION
    audio_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    source_track_id: str = Field(min_length=1, max_length=64)
    requested_engine_id: str = Field(
        min_length=1,
        max_length=128,
    )
    duration_ms: int | None = Field(default=None, ge=0)
    min_speakers: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SPEAKER_COUNT_GUIDANCE,
    )
    max_speakers: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SPEAKER_COUNT_GUIDANCE,
    )

    @model_validator(mode="after")
    def validate_speaker_range(
        self,
    ) -> "SpeakerDiarizationStepInputV1":
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError(
                "min_speakers must not exceed max_speakers"
            )
        return self


class SpeakerDiarizationPublicInputV1(BaseModel):
    """The durable input identity, intentionally excluding audio_path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal[
        "speaker-diarization-v1"
    ] = SPEAKER_DIARIZATION_RESULT_CONTRACT_VERSION
    audio_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    source_track_id: str = Field(min_length=1, max_length=64)
    engine_id: str = Field(min_length=1, max_length=128)
    duration_ms: int | None = Field(default=None, ge=0)
    min_speakers: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SPEAKER_COUNT_GUIDANCE,
    )
    max_speakers: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SPEAKER_COUNT_GUIDANCE,
    )

    @model_validator(mode="after")
    def validate_speaker_range(
        self,
    ) -> "SpeakerDiarizationPublicInputV1":
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError(
                "min_speakers must not exceed max_speakers"
            )
        return self


class SpeakerDiarizationSegmentV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    speaker_cluster_id: str = Field(min_length=1, max_length=256)
    source_speaker_label: str = Field(
        min_length=1,
        max_length=256,
    )
    confidence: float | None = Field(default=None, ge=0, le=1)
    has_speaker_overlap: bool = False

    @model_validator(mode="after")
    def validate_range(
        self,
    ) -> "SpeakerDiarizationSegmentV1":
        if self.end_ms <= self.start_ms:
            raise ValueError(
                "speaker segment end must follow start"
            )
        return self


class SpeakerDiarizationTimeRangeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    source: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_range(
        self,
    ) -> "SpeakerDiarizationTimeRangeV1":
        if (
            self.start_ms is not None
            and self.end_ms is not None
            and self.end_ms < self.start_ms
        ):
            raise ValueError(
                "speaker range end must not precede start"
            )
        return self


class SpeakerDiarizationClusterV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster_id: str = Field(min_length=1, max_length=256)
    source_label: str = Field(min_length=1, max_length=256)
    source_engine_id: str = Field(min_length=1, max_length=128)
    business_speaker_id: str | None = Field(
        default=None,
        max_length=256,
    )
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    segment_count: int = Field(ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    merge_status: Literal[
        "original",
        "auto_merged",
        "needs_review",
    ] = "original"
    merged_source_labels: tuple[str, ...] = ()
    time_ranges: tuple[SpeakerDiarizationTimeRangeV1, ...] = ()

    @model_validator(mode="after")
    def validate_range(
        self,
    ) -> "SpeakerDiarizationClusterV1":
        if self.end_ms < self.start_ms:
            raise ValueError(
                "speaker cluster end must not precede start"
            )
        if self.duration_ms != self.end_ms - self.start_ms:
            raise ValueError(
                "speaker cluster duration must match its range"
            )
        return self


class SpeakerCountGuidanceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_min_speakers: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SPEAKER_COUNT_GUIDANCE,
    )
    requested_max_speakers: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SPEAKER_COUNT_GUIDANCE,
    )
    detected_speaker_count: int = Field(ge=0)
    engine_applied: bool
    usage: Literal[
        "automatic",
        "engine_constraint",
        "quality_check_only",
    ]
    evaluation: Literal[
        "automatic",
        "within_range",
        "below_minimum",
        "above_maximum",
    ]


class SpeakerDiarizationQualitySummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["passed", "warning", "failed"]
    segment_count: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    overlap_segment_count: int = Field(ge=0)
    covered_duration_ms: int = Field(ge=0)
    audio_duration_ms: int | None = Field(default=None, ge=0)
    coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    warning_codes: tuple[str, ...] = ()


class SpeakerDiarizationStageTimingV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    duration_ms: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    status: Literal["completed", "partial"]


class SpeakerVerificationPairV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left: str = Field(min_length=1, max_length=256)
    right: str = Field(min_length=1, max_length=256)
    cosine: float = Field(ge=-1, le=1)


class SpeakerVerificationThresholdsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    auto_merge: float = Field(ge=-1, le=1)
    review: float = Field(ge=-1, le=1)


class SpeakerVerificationSummaryV1(BaseModel):
    """Bounded verifier facts; runtime paths and raw errors are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal[
        "completed",
        "partial",
        "failed",
        "skipped",
        "unknown",
    ] = "unknown"
    reason: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_.:-]+$",
    )
    engine_id: str | None = Field(default=None, max_length=128)
    model_id: str | None = Field(default=None, max_length=512)
    mapping: dict[str, str] = Field(default_factory=dict)
    auto_merged: tuple[SpeakerVerificationPairV1, ...] = ()
    needs_review: tuple[SpeakerVerificationPairV1, ...] = ()
    thresholds: SpeakerVerificationThresholdsV1 | None = None


class SpeakerDiarizationResultV1(BaseModel):
    """Path-free public result stored inside the managed artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal[
        "speaker-diarization-v1"
    ] = SPEAKER_DIARIZATION_RESULT_CONTRACT_VERSION
    input: SpeakerDiarizationPublicInputV1
    status: Literal["completed", "partial"]
    engine_id: str = Field(min_length=1, max_length=128)
    model_id: str | None = Field(default=None, max_length=512)
    segments: tuple[SpeakerDiarizationSegmentV1, ...] = ()
    clusters: tuple[SpeakerDiarizationClusterV1, ...] = ()
    count_guidance: SpeakerCountGuidanceV1
    quality_summary: SpeakerDiarizationQualitySummaryV1
    verification: SpeakerVerificationSummaryV1 = Field(
        default_factory=SpeakerVerificationSummaryV1
    )
    quality_flags: tuple[str, ...] = ()
    error: str | None = None
    stage_timing: SpeakerDiarizationStageTimingV1

    @model_validator(mode="after")
    def validate_counts(
        self,
    ) -> "SpeakerDiarizationResultV1":
        if self.quality_summary.segment_count != len(
            self.segments
        ):
            raise ValueError(
                "speaker segment count does not match result"
            )
        if self.quality_summary.cluster_count != len(
            self.clusters
        ):
            raise ValueError(
                "speaker cluster count does not match result"
            )
        if (
            self.count_guidance.detected_speaker_count
            != len(self.clusters)
        ):
            raise ValueError(
                "detected speaker count does not match result"
            )
        if (
            self.stage_timing.segment_count
            != len(self.segments)
            or self.stage_timing.cluster_count
            != len(self.clusters)
            or self.stage_timing.status != self.status
        ):
            raise ValueError(
                "speaker timing summary does not match result"
            )
        return self


class SpeakerDiarizationStepOutputV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "speaker-diarization-step-output-v1"
    ] = SPEAKER_DIARIZATION_STEP_OUTPUT_SCHEMA_VERSION
    result: SpeakerDiarizationResultV1


def speaker_diarization_step_input(
    *,
    audio_sha256: str,
    source_track_id: str,
    requested_engine_id: str,
    duration_ms: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
) -> SpeakerDiarizationStepInputV1:
    return SpeakerDiarizationStepInputV1(
        audio_sha256=audio_sha256,
        source_track_id=source_track_id,
        requested_engine_id=requested_engine_id,
        duration_ms=duration_ms,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
    )


def speaker_diarization_step_input_fingerprint(
    value: SpeakerDiarizationStepInputV1,
) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def speaker_diarization_step_output_from_payload(
    payload: Mapping[str, Any],
) -> SpeakerDiarizationStepOutputV1:
    normalized = dict(payload)
    raw_input = normalized.get("input")
    if not isinstance(raw_input, Mapping):
        raise ValueError(
            "speaker diarization result input is missing"
        )
    public_input = {
        key: value
        for key, value in raw_input.items()
        if key != "audio_path"
    }
    normalized["input"] = public_input
    normalized["verification"] = _verification_summary(
        normalized.get("verification")
    )
    normalized["error"] = (
        "speaker_verification_failed"
        if normalized.get("error")
        else None
    )
    return SpeakerDiarizationStepOutputV1(
        result=SpeakerDiarizationResultV1.model_validate(
            normalized
        )
    )


def _verification_summary(
    value: object,
) -> SpeakerVerificationSummaryV1:
    if not isinstance(value, Mapping):
        return SpeakerVerificationSummaryV1()
    return SpeakerVerificationSummaryV1.model_validate(
        {
            key: value[key]
            for key in {
                "status",
                "reason",
                "engine_id",
                "model_id",
                "mapping",
                "auto_merged",
                "needs_review",
                "thresholds",
            }
            if key in value
        }
    )


def speaker_diarization_step_output_bytes(
    output: SpeakerDiarizationStepOutputV1,
) -> bytes:
    return _canonical_json(output)


def parse_speaker_diarization_step_output(
    content: bytes,
) -> SpeakerDiarizationStepOutputV1:
    return SpeakerDiarizationStepOutputV1.model_validate_json(
        content
    )


def _canonical_json(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "MAX_SPEAKER_COUNT_GUIDANCE",
    "SPEAKER_DIARIZATION_RESULT_CONTRACT_VERSION",
    "SPEAKER_DIARIZATION_STEP_ID",
    "SPEAKER_DIARIZATION_STEP_INPUT_SCHEMA_VERSION",
    "SPEAKER_DIARIZATION_STEP_OUTPUT_SCHEMA_VERSION",
    "SPEAKER_DIARIZATION_WORKFLOW_VERSION",
    "SpeakerDiarizationPublicInputV1",
    "SpeakerDiarizationResultV1",
    "SpeakerDiarizationStepInputV1",
    "SpeakerDiarizationStepOutputV1",
    "parse_speaker_diarization_step_output",
    "speaker_diarization_step_input",
    "speaker_diarization_step_input_fingerprint",
    "speaker_diarization_step_output_bytes",
    "speaker_diarization_step_output_from_payload",
]
