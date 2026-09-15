"""Durable step contracts for managed ASR entity normalization."""

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

from app.schemas.video_localization_llm_observability import (
    VideoLocalizationLlmCallRecord,
)


ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-entity-normalization-development-workflow-v1"
)
ASR_ENTITY_NORMALIZATION_CALL_INPUT_SCHEMA_VERSION = (
    "asr-entity-normalization-call-input-v1"
)
ASR_ENTITY_NORMALIZATION_CALL_ARTIFACT_SCHEMA_VERSION = (
    "asr-entity-normalization-call-artifact-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ModelT = TypeVar("ModelT", bound=BaseModel)
EntityCallPurpose = Literal[
    "entity_resolution",
    "entity_variant_mapping",
]


class AsrEntityNormalizationCallInputV1(BaseModel):
    """Secret-free identity of one concrete model attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-entity-normalization-call-input-v1"
    ] = ASR_ENTITY_NORMALIZATION_CALL_INPUT_SCHEMA_VERSION
    behavior_version: Literal[
        "asr-entity-normalization-v1"
    ] = "asr-entity-normalization-v1"
    behavior_fingerprint: str
    call_id: Literal[
        "entity_resolution",
        "entity_variant_mapping",
    ]
    purpose: EntityCallPurpose
    attempt: int = Field(ge=1, le=2)
    candidate_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=24,
    )
    system_prompt_fingerprint: str
    user_payload_fingerprint: str
    profile_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)
    provider_protocol: str = Field(min_length=1, max_length=64)
    provider_endpoint_fingerprint: str
    profile_configuration_fingerprint: str
    max_tokens: int = Field(ge=1, le=64_000)
    timeout_ms: int = Field(ge=1, le=600_000)
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
        return _fingerprint(value, "entity-normalization call")

    @field_validator("candidate_ids")
    @classmethod
    def validate_candidate_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("entity candidate IDs must be unique")
        return value

    @model_validator(mode="after")
    def validate_purpose(self) -> "AsrEntityNormalizationCallInputV1":
        expected: EntityCallPurpose = (
            "entity_resolution"
            if self.call_id == "entity_resolution"
            else "entity_variant_mapping"
        )
        if self.purpose != expected:
            raise ValueError("entity call purpose does not match call ID")
        return self


class AsrEntityNormalizationCallArtifactV1(BaseModel):
    """Normalized response and observable record for one committed call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-entity-normalization-call-artifact-v1"
    ] = ASR_ENTITY_NORMALIZATION_CALL_ARTIFACT_SCHEMA_VERSION
    call_input_fingerprint: str
    call_id: Literal[
        "entity_resolution",
        "entity_variant_mapping",
    ]
    purpose: EntityCallPurpose
    attempt: int = Field(ge=1, le=2)
    candidate_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=24,
    )
    response: dict
    llm_call: VideoLocalizationLlmCallRecord

    @field_validator("call_input_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "entity-normalization artifact")

    @model_validator(mode="after")
    def validate_call_identity(
        self,
    ) -> "AsrEntityNormalizationCallArtifactV1":
        if (
            self.llm_call.call_id
            != f"{self.call_id}:attempt_{self.attempt}"
            or self.llm_call.purpose != self.purpose
            or tuple(self.llm_call.candidate_ids)
            != self.candidate_ids
        ):
            raise ValueError(
                "entity-normalization call record identity mismatch"
            )
        return self


def entity_normalization_call_input(
    *,
    behavior_version: str,
    behavior_fingerprint: str,
    call_id: str,
    purpose: EntityCallPurpose,
    attempt: int,
    candidate_ids: tuple[str, ...],
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
) -> AsrEntityNormalizationCallInputV1:
    return AsrEntityNormalizationCallInputV1(
        behavior_version=behavior_version,
        behavior_fingerprint=behavior_fingerprint,
        call_id=call_id,
        purpose=purpose,
        attempt=attempt,
        candidate_ids=candidate_ids,
        system_prompt_fingerprint=_hash_text(system_prompt),
        user_payload_fingerprint=_hash_json(user_payload),
        profile_id=profile_id,
        model_id=model_id,
        provider_protocol=provider_protocol,
        provider_endpoint_fingerprint=(
            provider_endpoint_fingerprint
        ),
        profile_configuration_fingerprint=(
            profile_configuration_fingerprint
        ),
        max_tokens=max_tokens,
        timeout_ms=max(1, int(round(timeout * 1_000))),
        disable_reasoning=disable_reasoning,
    )


def entity_normalization_call_input_fingerprint(
    value: AsrEntityNormalizationCallInputV1,
) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def entity_normalization_call_artifact_bytes(
    value: AsrEntityNormalizationCallArtifactV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_entity_normalization_call_artifact(
    content: bytes,
) -> AsrEntityNormalizationCallArtifactV1:
    return _parse_model(
        content,
        AsrEntityNormalizationCallArtifactV1,
        label="entity-normalization call artifact",
    )


def _fingerprint(value: str, label: str) -> str:
    normalized = str(value).strip()
    if _SHA256.fullmatch(normalized) is None:
        raise ValueError(f"{label} fingerprint must be lowercase SHA-256")
    return normalized


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _canonical_bytes(value: BaseModel) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _parse_model(
    content: bytes,
    model: type[ModelT],
    *,
    label: str,
) -> ModelT:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    return model.model_validate(payload)


__all__ = [
    "ASR_ENTITY_NORMALIZATION_CALL_ARTIFACT_SCHEMA_VERSION",
    "ASR_ENTITY_NORMALIZATION_CALL_INPUT_SCHEMA_VERSION",
    "ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION",
    "AsrEntityNormalizationCallArtifactV1",
    "AsrEntityNormalizationCallInputV1",
    "EntityCallPurpose",
    "entity_normalization_call_artifact_bytes",
    "entity_normalization_call_input",
    "entity_normalization_call_input_fingerprint",
    "parse_entity_normalization_call_artifact",
]
