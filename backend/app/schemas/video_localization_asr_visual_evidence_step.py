"""Durable Provider-call contracts for ASR visual evidence."""

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


ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-visual-evidence-development-workflow-v1"
)
ASR_VISUAL_EVIDENCE_CALL_INPUT_SCHEMA_VERSION = (
    "asr-visual-evidence-call-input-v1"
)
ASR_VISUAL_EVIDENCE_CALL_ARTIFACT_SCHEMA_VERSION = (
    "asr-visual-evidence-call-artifact-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AsrVisualEvidenceImageReferenceV1(BaseModel):
    """One committed JPEG identity supplied to the Provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    frame_id: str = Field(min_length=1, max_length=128)
    artifact_fingerprint: str
    size_bytes: int = Field(ge=1, le=16 * 1024 * 1024)

    @field_validator("artifact_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "visual image fingerprint must be lowercase SHA-256"
            )
        return normalized


class AsrVisualEvidenceCallInputV1(BaseModel):
    """Secret-free identity for one exact multimodal call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-visual-evidence-call-input-v1"
    ] = ASR_VISUAL_EVIDENCE_CALL_INPUT_SCHEMA_VERSION
    behavior_fingerprint: str
    call_id: str = Field(min_length=1, max_length=160)
    question_id: str = Field(min_length=1, max_length=128)
    round_index: Literal[1, 2]
    system_prompt_fingerprint: str
    user_payload_fingerprint: str
    images: tuple[AsrVisualEvidenceImageReferenceV1, ...] = Field(
        min_length=1,
        max_length=4,
    )
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
            raise ValueError(
                "visual call fingerprint must be lowercase SHA-256"
            )
        return normalized


class AsrVisualEvidenceCallArtifactV1(BaseModel):
    """Normalized response committed before Provider-step success."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-visual-evidence-call-artifact-v1"
    ] = ASR_VISUAL_EVIDENCE_CALL_ARTIFACT_SCHEMA_VERSION
    call_input_fingerprint: str
    call_id: str = Field(min_length=1, max_length=160)
    question_id: str = Field(min_length=1, max_length=128)
    round_index: Literal[1, 2]
    response: dict[str, JsonValue]
    llm_call: VideoLocalizationLlmCallRecord

    @field_validator("call_input_fingerprint")
    @classmethod
    def validate_input_fingerprint(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "visual call input fingerprint must be lowercase SHA-256"
            )
        return normalized

    @model_validator(mode="after")
    def validate_call_identity(
        self,
    ) -> "AsrVisualEvidenceCallArtifactV1":
        if (
            self.llm_call.purpose != "visual_analysis"
            or self.llm_call.call_id
            != (
                f"visual-{self.question_id}-"
                f"r{self.round_index:02d}"
            )
            or self.llm_call.question_id != self.question_id
            or self.llm_call.round_index != self.round_index
        ):
            raise ValueError(
                "visual call record identity mismatch"
            )
        return self


def visual_call_input(
    *,
    behavior_fingerprint: str,
    call_id: str,
    question_id: str,
    round_index: int,
    system_prompt: str,
    user_payload: dict,
    images: tuple[AsrVisualEvidenceImageReferenceV1, ...],
    profile_id: str,
    model_id: str,
    provider_protocol: str,
    provider_endpoint_fingerprint: str,
    profile_configuration_fingerprint: str,
    max_tokens: int,
    timeout: float,
    disable_reasoning: bool,
) -> AsrVisualEvidenceCallInputV1:
    return AsrVisualEvidenceCallInputV1(
        behavior_fingerprint=behavior_fingerprint,
        call_id=call_id,
        question_id=question_id,
        round_index=round_index,
        system_prompt_fingerprint=_fingerprint_text(system_prompt),
        user_payload_fingerprint=_fingerprint_json(user_payload),
        images=images,
        profile_id=profile_id,
        model_id=model_id,
        provider_protocol=provider_protocol,
        provider_endpoint_fingerprint=provider_endpoint_fingerprint,
        profile_configuration_fingerprint=(
            profile_configuration_fingerprint
        ),
        max_tokens=max_tokens,
        timeout_ms=max(
            1,
            int(round(float(timeout) * 1_000)),
        ),
        disable_reasoning=disable_reasoning,
    )


def visual_call_input_fingerprint(
    value: AsrVisualEvidenceCallInputV1,
) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def visual_call_artifact_bytes(
    value: AsrVisualEvidenceCallArtifactV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_visual_call_artifact(
    content: bytes,
) -> AsrVisualEvidenceCallArtifactV1:
    return _parse_model(
        content,
        AsrVisualEvidenceCallArtifactV1,
        label="visual evidence call artifact",
    )


def _fingerprint_text(value: str) -> str:
    return hashlib.sha256(
        str(value).encode("utf-8")
    ).hexdigest()


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
        raise ValueError(
            "visual call payload must be canonical JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


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


def _canonical_bytes(value: BaseModel) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "ASR_VISUAL_EVIDENCE_CALL_ARTIFACT_SCHEMA_VERSION",
    "ASR_VISUAL_EVIDENCE_CALL_INPUT_SCHEMA_VERSION",
    "ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION",
    "AsrVisualEvidenceCallArtifactV1",
    "AsrVisualEvidenceCallInputV1",
    "AsrVisualEvidenceImageReferenceV1",
    "parse_visual_call_artifact",
    "visual_call_artifact_bytes",
    "visual_call_input",
    "visual_call_input_fingerprint",
]
