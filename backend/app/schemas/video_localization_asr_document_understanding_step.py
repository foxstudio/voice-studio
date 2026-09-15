"""Durable call contracts for ASR document understanding."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from app.schemas.video_localization_llm_observability import (
    VideoLocalizationLlmCallRecord,
)


ASR_DOCUMENT_UNDERSTANDING_CALL_INPUT_SCHEMA_VERSION = "asr-document-understanding-call-input-v1"
ASR_DOCUMENT_UNDERSTANDING_CALL_ARTIFACT_SCHEMA_VERSION = "asr-document-understanding-call-artifact-v1"
ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION = "asr-document-understanding-development-workflow-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AsrDocumentUnderstandingCallInputV1(BaseModel):
    """Secret-free identity for one exact Provider attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["asr-document-understanding-call-input-v1"] = (
        ASR_DOCUMENT_UNDERSTANDING_CALL_INPUT_SCHEMA_VERSION
    )
    behavior_version: str = Field(min_length=1, max_length=128)
    behavior_fingerprint: str
    call_id: str = Field(min_length=1, max_length=160)
    attempt: Literal[1, 2]
    system_prompt_fingerprint: str
    user_payload_fingerprint: str
    profile_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)
    provider_protocol: Literal["openai_compatible", "codex_cli"]
    provider_endpoint_fingerprint: str
    profile_configuration_fingerprint: str
    max_tokens: int = Field(ge=1, le=1_000_000)
    timeout_ms: int = Field(ge=1, le=3_600_000)
    disable_reasoning: bool

    @field_validator(
        "behavior_fingerprint",
        "system_prompt_fingerprint",
        "user_payload_fingerprint",
        "provider_endpoint_fingerprint",
        "profile_configuration_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError("document understanding fingerprint must be lowercase SHA-256")
        return normalized


class AsrDocumentUnderstandingCallArtifactV1(BaseModel):
    """Path-free normalized response committed before Provider success."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["asr-document-understanding-call-artifact-v1"] = (
        ASR_DOCUMENT_UNDERSTANDING_CALL_ARTIFACT_SCHEMA_VERSION
    )
    call_input_fingerprint: str
    call_id: str = Field(min_length=1, max_length=160)
    attempt: Literal[1, 2]
    response: dict[str, JsonValue]
    llm_call: VideoLocalizationLlmCallRecord

    @field_validator("call_input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError("call input fingerprint must be lowercase SHA-256")
        return normalized

    @model_validator(mode="after")
    def validate_call_identity(
        self,
    ) -> "AsrDocumentUnderstandingCallArtifactV1":
        if (
            self.llm_call.purpose != "document_understanding"
            or self.llm_call.call_id != f"{self.call_id}:attempt_{self.attempt}"
        ):
            raise ValueError("document understanding call record identity mismatch")
        return self


def asr_document_understanding_call_input(
    *,
    behavior_version: str,
    behavior_fingerprint: str,
    call_id: str,
    attempt: int,
    system_prompt: str,
    user_payload: dict,
    profile_id: str,
    model_id: str,
    provider_protocol: str,
    provider_endpoint_fingerprint: str,
    profile_configuration_fingerprint: str,
    max_tokens: int,
    timeout: float,
    disable_reasoning: bool,
) -> AsrDocumentUnderstandingCallInputV1:
    return AsrDocumentUnderstandingCallInputV1(
        behavior_version=behavior_version,
        behavior_fingerprint=behavior_fingerprint,
        call_id=call_id,
        attempt=attempt,
        system_prompt_fingerprint=_fingerprint_text(system_prompt),
        user_payload_fingerprint=_fingerprint_json(user_payload),
        profile_id=profile_id,
        model_id=model_id,
        provider_protocol=provider_protocol,
        provider_endpoint_fingerprint=(provider_endpoint_fingerprint),
        profile_configuration_fingerprint=(profile_configuration_fingerprint),
        max_tokens=max_tokens,
        timeout_ms=max(1, int(round(float(timeout) * 1_000))),
        disable_reasoning=disable_reasoning,
    )


def asr_document_understanding_call_input_fingerprint(
    value: AsrDocumentUnderstandingCallInputV1,
) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def asr_document_understanding_call_artifact_bytes(
    value: AsrDocumentUnderstandingCallArtifactV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_asr_document_understanding_call_artifact(
    content: bytes,
) -> AsrDocumentUnderstandingCallArtifactV1:
    return _parse_model(
        content,
        AsrDocumentUnderstandingCallArtifactV1,
        label="document understanding call artifact",
    )


def _parse_model(content: bytes, model_type, *, label: str):
    if not isinstance(content, bytes) or not content:
        raise ValueError(f"{label} must be non-empty bytes")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return model_type.model_validate(payload)


def _fingerprint_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _fingerprint_json(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("document understanding payload must be canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _canonical_bytes(value: BaseModel) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "ASR_DOCUMENT_UNDERSTANDING_CALL_ARTIFACT_SCHEMA_VERSION",
    "ASR_DOCUMENT_UNDERSTANDING_CALL_INPUT_SCHEMA_VERSION",
    "ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION",
    "AsrDocumentUnderstandingCallArtifactV1",
    "AsrDocumentUnderstandingCallInputV1",
    "asr_document_understanding_call_artifact_bytes",
    "asr_document_understanding_call_input",
    "asr_document_understanding_call_input_fingerprint",
    "parse_asr_document_understanding_call_artifact",
]
