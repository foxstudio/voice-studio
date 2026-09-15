"""Managed input, extraction and final contracts for visual evidence."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domains.video_localization.visual_evidence import (
    AsrVisualEvidenceInput,
    AsrVisualEvidenceObservation,
    AsrVisualEvidenceQualitySummary,
    VisualEvidenceStatus,
    VisualEvidenceStopReason,
)


ASR_VISUAL_EVIDENCE_PREPARED_INPUT_SCHEMA_VERSION = (
    "asr-visual-evidence-input-v2"
)
ASR_VISUAL_EVIDENCE_EXTRACTION_OUTPUT_SCHEMA_VERSION = (
    "asr-visual-evidence-extraction-output-v1"
)
ASR_VISUAL_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION = (
    "asr-visual-evidence-step-output-v2"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ModelT = TypeVar("ModelT", bound=BaseModel)


class AsrVisualEvidencePreparedInputV2(BaseModel):
    """Immutable visual request rebuilt from managed project authorities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-visual-evidence-input-v2"
    ] = ASR_VISUAL_EVIDENCE_PREPARED_INPUT_SCHEMA_VERSION
    upstream_artifact_fingerprint: str
    profile_configuration_fingerprint: str | None = None
    behavior_fingerprint: str
    request: AsrVisualEvidenceInput

    @field_validator(
        "upstream_artifact_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_required_fingerprint(cls, value: str) -> str:
        return _validate_fingerprint(value, label="prepared input")

    @field_validator("profile_configuration_fingerprint")
    @classmethod
    def validate_optional_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _validate_fingerprint(value, label="profile")

    @model_validator(mode="after")
    def validate_profile_identity(
        self,
    ) -> "AsrVisualEvidencePreparedInputV2":
        if bool(self.request.questions):
            if not (
                self.request.profile_id
                and self.profile_configuration_fingerprint
            ):
                raise ValueError(
                    "visual questions require a locked profile identity"
                )
        elif (
            self.request.profile_id
            or self.profile_configuration_fingerprint
        ):
            raise ValueError(
                "empty visual work must not lock a Provider profile"
            )
        return self


class AsrVisualEvidenceFrameReferenceV1(BaseModel):
    """Path-free identity for one committed JPEG."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    frame_id: str = Field(min_length=1, max_length=128)
    question_id: str = Field(min_length=1, max_length=128)
    frame_index: int = Field(ge=1)
    round_index: Literal[1, 2]
    timestamp_ms: int = Field(ge=0)
    sha256: str
    size_bytes: int = Field(ge=1, le=16 * 1024 * 1024)
    artifact_id: str = Field(min_length=1, max_length=160)
    artifact_fingerprint: str

    @field_validator("sha256", "artifact_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _validate_fingerprint(value, label="visual frame")

    @model_validator(mode="after")
    def validate_content_identity(
        self,
    ) -> "AsrVisualEvidenceFrameReferenceV1":
        if self.sha256 != self.artifact_fingerprint:
            raise ValueError(
                "visual frame digest must match artifact fingerprint"
            )
        return self


class AsrVisualEvidenceExtractionOutputV1(BaseModel):
    """One committed extraction round and its exact candidate set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-visual-evidence-extraction-output-v1"
    ] = ASR_VISUAL_EVIDENCE_EXTRACTION_OUTPUT_SCHEMA_VERSION
    question_id: str = Field(min_length=1, max_length=128)
    round_index: Literal[1, 2]
    attempted_timestamps: tuple[int, ...] = Field(
        min_length=1,
        max_length=4,
    )
    frames: tuple[AsrVisualEvidenceFrameReferenceV1, ...] = Field(
        max_length=4,
    )
    warnings: tuple[str, ...] = Field(default=(), max_length=12)

    @field_validator("attempted_timestamps")
    @classmethod
    def validate_timestamps(
        cls,
        value: tuple[int, ...],
    ) -> tuple[int, ...]:
        if any(item < 0 for item in value) or len(set(value)) != len(value):
            raise ValueError(
                "visual extraction timestamps must be unique and non-negative"
            )
        return value

    @model_validator(mode="after")
    def validate_frame_identity(
        self,
    ) -> "AsrVisualEvidenceExtractionOutputV1":
        identities = [item.frame_id for item in self.frames]
        if (
            len(set(identities)) != len(identities)
            or any(
                item.question_id != self.question_id
                or item.round_index != self.round_index
                or item.timestamp_ms not in self.attempted_timestamps
                for item in self.frames
            )
        ):
            raise ValueError(
                "visual extraction frame identity mismatch"
            )
        return self


class AsrVisualEvidenceExtractionReferenceV1(BaseModel):
    """One exact extraction output consumed by finalization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str = Field(min_length=1, max_length=128)
    round_index: Literal[1, 2]
    step_id: str = Field(min_length=1, max_length=256)
    input_fingerprint: str
    artifact_fingerprint: str

    @field_validator("input_fingerprint", "artifact_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _validate_fingerprint(value, label="extraction reference")


class AsrVisualEvidenceCallReferenceV1(BaseModel):
    """One exact multimodal Provider artifact consumed by finalization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=160)
    question_id: str = Field(min_length=1, max_length=128)
    round_index: Literal[1, 2]
    step_id: str = Field(min_length=1, max_length=256)
    input_fingerprint: str
    artifact_fingerprint: str

    @field_validator("input_fingerprint", "artifact_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _validate_fingerprint(value, label="call reference")


class AsrVisualEvidenceStepOutputV2(BaseModel):
    """Deterministic result referencing committed frames and Provider calls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-visual-evidence-step-output-v2"
    ] = ASR_VISUAL_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION
    prepared_input_fingerprint: str
    status: VisualEvidenceStatus
    stop_reason: VisualEvidenceStopReason
    profile_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    model_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    frames: tuple[AsrVisualEvidenceFrameReferenceV1, ...] = ()
    observations: tuple[AsrVisualEvidenceObservation, ...] = ()
    warnings: tuple[str, ...] = ()
    duration_ms: int = Field(ge=0)
    quality_summary: AsrVisualEvidenceQualitySummary
    extractions: tuple[AsrVisualEvidenceExtractionReferenceV1, ...] = ()
    calls: tuple[AsrVisualEvidenceCallReferenceV1, ...] = ()

    @field_validator("prepared_input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        return _validate_fingerprint(value, label="prepared input")

    @model_validator(mode="after")
    def validate_manifests(
        self,
    ) -> "AsrVisualEvidenceStepOutputV2":
        frame_ids = [item.frame_id for item in self.frames]
        extraction_ids = [
            (item.question_id, item.round_index)
            for item in self.extractions
        ]
        call_ids = [
            (item.question_id, item.round_index)
            for item in self.calls
        ]
        if (
            len(set(frame_ids)) != len(frame_ids)
            or len(set(extraction_ids)) != len(extraction_ids)
            or len(set(call_ids)) != len(call_ids)
            or self.quality_summary.frame_count != len(self.frames)
            or self.quality_summary.model_call_count != len(self.calls)
        ):
            raise ValueError(
                "visual evidence final manifest is inconsistent"
            )
        if bool(self.profile_id) != bool(self.model_id):
            raise ValueError(
                "visual evidence Provider identity is incomplete"
            )
        return self


def prepared_input_fingerprint(
    value: AsrVisualEvidencePreparedInputV2,
) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def prepared_input_bytes(
    value: AsrVisualEvidencePreparedInputV2,
) -> bytes:
    return _canonical_bytes(value)


def parse_prepared_input(
    content: bytes,
) -> AsrVisualEvidencePreparedInputV2:
    return _parse_model(
        content,
        AsrVisualEvidencePreparedInputV2,
        label="visual evidence prepared input",
    )


def extraction_output_bytes(
    value: AsrVisualEvidenceExtractionOutputV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_extraction_output(
    content: bytes,
) -> AsrVisualEvidenceExtractionOutputV1:
    return _parse_model(
        content,
        AsrVisualEvidenceExtractionOutputV1,
        label="visual evidence extraction output",
    )


def step_output_bytes(
    value: AsrVisualEvidenceStepOutputV2,
) -> bytes:
    return _canonical_bytes(value)


def parse_step_output(
    content: bytes,
) -> AsrVisualEvidenceStepOutputV2:
    return _parse_model(
        content,
        AsrVisualEvidenceStepOutputV2,
        label="visual evidence step output",
    )


def _validate_fingerprint(value: str, *, label: str) -> str:
    normalized = value.strip()
    if _SHA256.fullmatch(normalized) is None:
        raise ValueError(f"{label} fingerprint must be lowercase SHA-256")
    return normalized


def _parse_model(
    content: bytes,
    model_type: type[ModelT],
    *,
    label: str,
) -> ModelT:
    if not isinstance(content, bytes) or not content:
        raise ValueError(f"{label} must be non-empty bytes")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return model_type.model_validate(payload)


def _canonical_bytes(value: BaseModel) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "ASR_VISUAL_EVIDENCE_EXTRACTION_OUTPUT_SCHEMA_VERSION",
    "ASR_VISUAL_EVIDENCE_PREPARED_INPUT_SCHEMA_VERSION",
    "ASR_VISUAL_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION",
    "AsrVisualEvidenceCallReferenceV1",
    "AsrVisualEvidenceExtractionOutputV1",
    "AsrVisualEvidenceExtractionReferenceV1",
    "AsrVisualEvidenceFrameReferenceV1",
    "AsrVisualEvidencePreparedInputV2",
    "AsrVisualEvidenceStepOutputV2",
    "extraction_output_bytes",
    "parse_extraction_output",
    "parse_prepared_input",
    "parse_step_output",
    "prepared_input_bytes",
    "prepared_input_fingerprint",
    "step_output_bytes",
]
