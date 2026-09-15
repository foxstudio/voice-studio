"""Managed input/final-output contracts for document understanding."""

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

from app.domains.video_localization.document_understanding_contracts import (
    AsrDocumentUnderstandingBrief,
    AsrDocumentUnderstandingInput,
    AsrDocumentUnderstandingQualitySummary,
)


ASR_DOCUMENT_UNDERSTANDING_PREPARED_INPUT_SCHEMA_VERSION = (
    "asr-document-understanding-input-v2"
)
ASR_DOCUMENT_UNDERSTANDING_STEP_OUTPUT_SCHEMA_VERSION = (
    "asr-document-understanding-step-output-v2"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ModelT = TypeVar("ModelT", bound=BaseModel)


class AsrDocumentUnderstandingPreparedInputV2(BaseModel):
    """Immutable input rebuilt from one managed initial-analysis snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-document-understanding-input-v2"
    ] = ASR_DOCUMENT_UNDERSTANDING_PREPARED_INPUT_SCHEMA_VERSION
    raw_artifact_fingerprint: str
    diarization_artifact_fingerprint: str
    join_artifact_fingerprint: str
    profile_configuration_fingerprint: str
    behavior_fingerprint: str
    request: AsrDocumentUnderstandingInput

    @field_validator(
        "raw_artifact_fingerprint",
        "diarization_artifact_fingerprint",
        "join_artifact_fingerprint",
        "profile_configuration_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "prepared input fingerprint must be lowercase SHA-256"
            )
        return normalized

    @model_validator(mode="after")
    def validate_profile_identity(
        self,
    ) -> "AsrDocumentUnderstandingPreparedInputV2":
        if not self.request.profile_id:
            raise ValueError(
                "prepared document understanding input requires profile ID"
            )
        return self


class AsrDocumentUnderstandingCallReferenceV1(BaseModel):
    """One exact Provider artifact consumed by finalization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=160)
    attempt: Literal[1, 2]
    step_id: str = Field(min_length=1, max_length=256)
    input_fingerprint: str
    artifact_fingerprint: str

    @field_validator(
        "input_fingerprint",
        "artifact_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "call reference fingerprint must be lowercase SHA-256"
            )
        return normalized


class AsrDocumentUnderstandingStepOutputV2(BaseModel):
    """Deterministic final result referencing, not copying, raw calls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-document-understanding-step-output-v2"
    ] = ASR_DOCUMENT_UNDERSTANDING_STEP_OUTPUT_SCHEMA_VERSION
    prepared_input_fingerprint: str
    brief: AsrDocumentUnderstandingBrief
    profile_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)
    prompt_version: str = Field(min_length=1, max_length=128)
    execution_strategy: Literal["full_document", "windowed"]
    window_count: int = Field(ge=0)
    llm_call_count: int = Field(ge=1)
    retry_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    quality_summary: AsrDocumentUnderstandingQualitySummary
    warnings: tuple[str, ...] = ()
    calls: tuple[AsrDocumentUnderstandingCallReferenceV1, ...]

    @field_validator("prepared_input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "prepared input fingerprint must be lowercase SHA-256"
            )
        return normalized

    @model_validator(mode="after")
    def validate_call_manifest(
        self,
    ) -> "AsrDocumentUnderstandingStepOutputV2":
        identities = [
            (call.call_id, call.attempt) for call in self.calls
        ]
        if (
            not self.calls
            or len(self.calls) > self.llm_call_count
            or len(set(identities)) != len(identities)
        ):
            raise ValueError(
                "document understanding call manifest is incomplete"
            )
        return self


def prepared_input_fingerprint(
    value: AsrDocumentUnderstandingPreparedInputV2,
) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def prepared_input_bytes(
    value: AsrDocumentUnderstandingPreparedInputV2,
) -> bytes:
    return _canonical_bytes(value)


def parse_prepared_input(
    content: bytes,
) -> AsrDocumentUnderstandingPreparedInputV2:
    return _parse_model(
        content,
        AsrDocumentUnderstandingPreparedInputV2,
        label="document understanding prepared input",
    )


def step_output_bytes(
    value: AsrDocumentUnderstandingStepOutputV2,
) -> bytes:
    return _canonical_bytes(value)


def parse_step_output(
    content: bytes,
) -> AsrDocumentUnderstandingStepOutputV2:
    return _parse_model(
        content,
        AsrDocumentUnderstandingStepOutputV2,
        label="document understanding step output",
    )


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
    "ASR_DOCUMENT_UNDERSTANDING_PREPARED_INPUT_SCHEMA_VERSION",
    "ASR_DOCUMENT_UNDERSTANDING_STEP_OUTPUT_SCHEMA_VERSION",
    "AsrDocumentUnderstandingCallReferenceV1",
    "AsrDocumentUnderstandingPreparedInputV2",
    "AsrDocumentUnderstandingStepOutputV2",
    "parse_prepared_input",
    "parse_step_output",
    "prepared_input_bytes",
    "prepared_input_fingerprint",
    "step_output_bytes",
]
