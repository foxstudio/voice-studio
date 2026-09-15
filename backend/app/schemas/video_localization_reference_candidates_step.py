"""Versioned, path-free contracts for automatic reference candidates."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


REFERENCE_CANDIDATES_STEP_ID = (
    "generate_reference_candidates"
)
REFERENCE_CANDIDATES_STEP_INPUT_SCHEMA_VERSION = (
    "reference-candidates-step-input-v1"
)
REFERENCE_CANDIDATES_STEP_OUTPUT_SCHEMA_VERSION = (
    "reference-candidates-step-output-v1"
)
REFERENCE_CANDIDATES_WORKFLOW_VERSION = (
    "reference-candidates-workflow-v1"
)


class ReferenceCandidateCueIdentityV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cue_id: str = Field(min_length=1, max_length=256)
    speaker_id: str = Field(min_length=1, max_length=256)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_text_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("candidate end must follow start")
        return self


class ReferenceCandidatesStepInputV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "reference-candidates-step-input-v1"
    ] = REFERENCE_CANDIDATES_STEP_INPUT_SCHEMA_VERSION
    clean_vocals_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    candidate_revision_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    candidates: tuple[ReferenceCandidateCueIdentityV1, ...]


class ReferenceCandidateMediaV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_clip_id: str = Field(
        min_length=1,
        max_length=512,
    )
    cue_id: str = Field(min_length=1, max_length=256)
    speaker_id: str = Field(min_length=1, max_length=256)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    duration_ms: int = Field(ge=0)
    audio_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("candidate media end must follow start")
        return self


class ReferenceCandidatesStepOutputV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "reference-candidates-step-output-v1"
    ] = REFERENCE_CANDIDATES_STEP_OUTPUT_SCHEMA_VERSION
    candidate_clip_count: int = Field(ge=0)
    generated_clip_count: int = Field(ge=0)
    linked_cue_count: int = Field(ge=0)
    media_status: Literal["available"]
    generated_media: tuple[ReferenceCandidateMediaV1, ...]

    @model_validator(mode="after")
    def validate_counts(self):
        if self.generated_clip_count != len(
            self.generated_media
        ):
            raise ValueError(
                "generated clip count must match media records"
            )
        if (
            self.candidate_clip_count < self.generated_clip_count
            or self.linked_cue_count < self.candidate_clip_count
        ):
            raise ValueError(
                "reference candidate counts are inconsistent"
            )
        return self


def reference_candidates_step_input_fingerprint(
    *,
    clean_vocals_sha256: str,
    candidate_revision_sha256: str,
    candidates: tuple[dict[str, object], ...],
) -> str:
    payload = ReferenceCandidatesStepInputV1(
        clean_vocals_sha256=clean_vocals_sha256,
        candidate_revision_sha256=(
            candidate_revision_sha256
        ),
        candidates=tuple(
            ReferenceCandidateCueIdentityV1.model_validate(item)
            for item in candidates
        ),
    )
    return _fingerprint(payload)


def reference_candidates_step_output_bytes(
    output: ReferenceCandidatesStepOutputV1,
) -> bytes:
    return _canonical_json(output)


def parse_reference_candidates_step_output(
    content: bytes,
) -> ReferenceCandidatesStepOutputV1:
    return ReferenceCandidatesStepOutputV1.model_validate_json(
        content
    )


def _fingerprint(model: BaseModel) -> str:
    return hashlib.sha256(_canonical_json(model)).hexdigest()


def _canonical_json(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "REFERENCE_CANDIDATES_STEP_ID",
    "REFERENCE_CANDIDATES_STEP_INPUT_SCHEMA_VERSION",
    "REFERENCE_CANDIDATES_STEP_OUTPUT_SCHEMA_VERSION",
    "REFERENCE_CANDIDATES_WORKFLOW_VERSION",
    "ReferenceCandidateCueIdentityV1",
    "ReferenceCandidateMediaV1",
    "ReferenceCandidatesStepInputV1",
    "ReferenceCandidatesStepOutputV1",
    "parse_reference_candidates_step_output",
    "reference_candidates_step_input_fingerprint",
    "reference_candidates_step_output_bytes",
]
