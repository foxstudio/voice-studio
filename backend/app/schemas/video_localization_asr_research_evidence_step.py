"""Durable call/search contracts for managed ASR research evidence."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

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


ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-research-evidence-development-workflow-v1"
)
ASR_RESEARCH_SEARCH_INPUT_SCHEMA_VERSION = (
    "asr-research-search-input-v1"
)
ASR_RESEARCH_SEARCH_ARTIFACT_SCHEMA_VERSION = (
    "asr-research-search-artifact-v1"
)
ASR_RESEARCH_CALL_INPUT_SCHEMA_VERSION = (
    "asr-research-call-input-v1"
)
ASR_RESEARCH_CALL_ARTIFACT_SCHEMA_VERSION = (
    "asr-research-call-artifact-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AsrResearchSearchInputV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-research-search-input-v1"
    ] = ASR_RESEARCH_SEARCH_INPUT_SCHEMA_VERSION
    request_id: str = Field(min_length=1, max_length=180)
    round_index: int = Field(ge=1, le=3)
    candidate_id: str = Field(min_length=1, max_length=128)
    search_kind: Literal["primary", "general_fallback"]
    attempt: int = Field(ge=1, le=2)
    provider: Literal[
        "wikipedia",
        "tavily",
        "searxng",
        "duckduckgo",
    ]
    query: str = Field(min_length=1, max_length=240)
    limit: int = Field(ge=1, le=8)
    provider_endpoint_fingerprint: str
    search_configuration_fingerprint: str
    behavior_fingerprint: str

    @field_validator(
        "provider_endpoint_fingerprint",
        "search_configuration_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "search input")

    @model_validator(mode="after")
    def validate_kind_provider(self) -> "AsrResearchSearchInputV1":
        if (
            self.search_kind == "general_fallback"
            and self.provider != "duckduckgo"
        ):
            raise ValueError(
                "general fallback must use duckduckgo"
            )
        if (
            self.search_kind == "primary"
            and self.provider == "duckduckgo"
        ):
            raise ValueError(
                "duckduckgo is only a general fallback"
            )
        return self


class AsrResearchSearchResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2_000)
    snippet: str = Field(default="", max_length=1_200)
    retrieved_at: str = Field(min_length=1, max_length=64)


class AsrResearchSearchArtifactV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-research-search-artifact-v1"
    ] = ASR_RESEARCH_SEARCH_ARTIFACT_SCHEMA_VERSION
    search_input_fingerprint: str
    request_id: str = Field(min_length=1, max_length=180)
    round_index: int = Field(ge=1, le=3)
    candidate_id: str = Field(min_length=1, max_length=128)
    search_kind: Literal["primary", "general_fallback"]
    attempt: int = Field(ge=1, le=2)
    provider: Literal[
        "wikipedia",
        "tavily",
        "searxng",
        "duckduckgo",
    ]
    duration_ms: int = Field(ge=0)
    results: tuple[AsrResearchSearchResultV1, ...] = Field(
        default=(),
        max_length=8,
    )

    @field_validator("search_input_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "search artifact")


class AsrResearchCallInputV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-research-call-input-v1"
    ] = ASR_RESEARCH_CALL_INPUT_SCHEMA_VERSION
    behavior_fingerprint: str
    call_id: str = Field(min_length=1, max_length=180)
    purpose: Literal["query_rewrite", "evidence_assessment"]
    round_index: int = Field(ge=1, le=3)
    candidate_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=12,
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
        return _fingerprint(value, "research call")

    @field_validator("candidate_ids")
    @classmethod
    def validate_candidate_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("candidate IDs must be unique")
        return value


class AsrResearchCallArtifactV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-research-call-artifact-v1"
    ] = ASR_RESEARCH_CALL_ARTIFACT_SCHEMA_VERSION
    call_input_fingerprint: str
    call_id: str = Field(min_length=1, max_length=180)
    purpose: Literal["query_rewrite", "evidence_assessment"]
    round_index: int = Field(ge=1, le=3)
    candidate_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=12,
    )
    response: dict
    llm_call: VideoLocalizationLlmCallRecord

    @field_validator("call_input_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "research call artifact")

    @model_validator(mode="after")
    def validate_call_identity(self) -> "AsrResearchCallArtifactV1":
        if (
            self.llm_call.call_id != self.call_id
            or self.llm_call.purpose != self.purpose
            or self.llm_call.round_index != self.round_index
            or tuple(self.llm_call.candidate_ids)
            != self.candidate_ids
        ):
            raise ValueError("research call record identity mismatch")
        return self


def research_search_input_fingerprint(
    value: AsrResearchSearchInputV1,
) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def research_search_artifact_bytes(
    value: AsrResearchSearchArtifactV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_research_search_artifact(
    content: bytes,
) -> AsrResearchSearchArtifactV1:
    return _parse_model(
        content,
        AsrResearchSearchArtifactV1,
        label="research search artifact",
    )


def research_call_input(
    *,
    behavior_fingerprint: str,
    call_id: str,
    purpose: Literal["query_rewrite", "evidence_assessment"],
    round_index: int,
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
) -> AsrResearchCallInputV1:
    return AsrResearchCallInputV1(
        behavior_fingerprint=behavior_fingerprint,
        call_id=call_id,
        purpose=purpose,
        round_index=round_index,
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


def research_call_input_fingerprint(
    value: AsrResearchCallInputV1,
) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def research_call_artifact_bytes(
    value: AsrResearchCallArtifactV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_research_call_artifact(
    content: bytes,
) -> AsrResearchCallArtifactV1:
    return _parse_model(
        content,
        AsrResearchCallArtifactV1,
        label="research call artifact",
    )


def _fingerprint(value: str, label: str) -> str:
    normalized = str(value).strip()
    if _SHA256.fullmatch(normalized) is None:
        raise ValueError(f"{label} fingerprint must be lowercase SHA-256")
    return normalized


def _hash_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


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
    "ASR_RESEARCH_CALL_ARTIFACT_SCHEMA_VERSION",
    "ASR_RESEARCH_CALL_INPUT_SCHEMA_VERSION",
    "ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION",
    "ASR_RESEARCH_SEARCH_ARTIFACT_SCHEMA_VERSION",
    "ASR_RESEARCH_SEARCH_INPUT_SCHEMA_VERSION",
    "AsrResearchCallArtifactV1",
    "AsrResearchCallInputV1",
    "AsrResearchSearchArtifactV1",
    "AsrResearchSearchInputV1",
    "AsrResearchSearchResultV1",
    "parse_research_call_artifact",
    "parse_research_search_artifact",
    "research_call_artifact_bytes",
    "research_call_input",
    "research_call_input_fingerprint",
    "research_search_artifact_bytes",
    "research_search_input_fingerprint",
]
