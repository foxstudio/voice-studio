"""Durable Provider-call contracts for managed ASR whole recheck."""

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


ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-whole-recheck-development-workflow-v1"
)
ASR_WHOLE_RECHECK_CALL_INPUT_SCHEMA_VERSION = (
    "asr-whole-recheck-call-input-v1"
)
ASR_WHOLE_RECHECK_CALL_ARTIFACT_SCHEMA_VERSION = (
    "asr-whole-recheck-call-artifact-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ModelT = TypeVar("ModelT", bound=BaseModel)


class AsrWholeRecheckCallInputV1(BaseModel):
    """Secret-free identity of one whole-recheck model attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-whole-recheck-call-input-v1"
    ] = ASR_WHOLE_RECHECK_CALL_INPUT_SCHEMA_VERSION
    behavior_version: Literal[
        "asr-whole-recheck-v3"
    ] = "asr-whole-recheck-v3"
    behavior_fingerprint: str
    attempt: int = Field(ge=1, le=2)
    round_index: int = Field(ge=1, le=2)
    decision_ids: tuple[str, ...] = ()
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
        return _fingerprint(value, "whole-recheck call")

    @field_validator("decision_ids")
    @classmethod
    def validate_decision_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("whole-recheck decision IDs must be unique")
        return value


class AsrWholeRecheckCallArtifactV1(BaseModel):
    """Normalized response and observable record for one committed call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-whole-recheck-call-artifact-v1"
    ] = ASR_WHOLE_RECHECK_CALL_ARTIFACT_SCHEMA_VERSION
    call_input_fingerprint: str
    attempt: int = Field(ge=1, le=2)
    round_index: int = Field(ge=1, le=2)
    decision_ids: tuple[str, ...] = ()
    response: dict
    llm_call: VideoLocalizationLlmCallRecord

    @field_validator("call_input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "whole-recheck artifact")

    @model_validator(mode="after")
    def validate_call_identity(
        self,
    ) -> "AsrWholeRecheckCallArtifactV1":
        expected_call_id = (
            f"whole-recheck-r{self.round_index}-a{self.attempt:02d}"
        )
        if (
            self.llm_call.call_id != expected_call_id
            or self.llm_call.purpose != "whole_recheck"
            or self.llm_call.round_index != self.round_index
            or tuple(self.llm_call.candidate_ids)
            != self.decision_ids
        ):
            raise ValueError("whole-recheck call record identity mismatch")
        return self


def whole_recheck_call_input(
    *,
    behavior_version: str,
    behavior_fingerprint: str,
    attempt: int,
    round_index: int,
    decision_ids: tuple[str, ...],
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
) -> AsrWholeRecheckCallInputV1:
    return AsrWholeRecheckCallInputV1(
        behavior_version=behavior_version,
        behavior_fingerprint=behavior_fingerprint,
        attempt=attempt,
        round_index=round_index,
        decision_ids=decision_ids,
        system_prompt_fingerprint=_hash_text(system_prompt),
        user_payload_fingerprint=_hash_json(user_payload),
        profile_id=profile_id,
        model_id=model_id,
        provider_protocol=provider_protocol,
        provider_endpoint_fingerprint=provider_endpoint_fingerprint,
        profile_configuration_fingerprint=(
            profile_configuration_fingerprint
        ),
        max_tokens=max_tokens,
        timeout_ms=max(1, int(round(timeout * 1_000))),
        disable_reasoning=disable_reasoning,
    )


def whole_recheck_call_input_fingerprint(
    value: AsrWholeRecheckCallInputV1,
) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def whole_recheck_call_artifact_bytes(
    value: AsrWholeRecheckCallArtifactV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_whole_recheck_call_artifact(
    content: bytes,
) -> AsrWholeRecheckCallArtifactV1:
    return _parse_model(
        content,
        AsrWholeRecheckCallArtifactV1,
        label="whole-recheck call artifact",
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
    "ASR_WHOLE_RECHECK_CALL_ARTIFACT_SCHEMA_VERSION",
    "ASR_WHOLE_RECHECK_CALL_INPUT_SCHEMA_VERSION",
    "ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION",
    "AsrWholeRecheckCallArtifactV1",
    "AsrWholeRecheckCallInputV1",
    "parse_whole_recheck_call_artifact",
    "whole_recheck_call_artifact_bytes",
    "whole_recheck_call_input",
    "whole_recheck_call_input_fingerprint",
]
