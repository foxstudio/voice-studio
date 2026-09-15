"""Versioned contracts for durable semantic TTS grouping Provider calls."""

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


SEMANTIC_TTS_GROUPING_INPUT_SCHEMA_VERSION = (
    "semantic-tts-grouping-input-v2"
)
SEMANTIC_TTS_GROUPING_PROMPT_VERSION = "semantic-tts-grouping-v1"
SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION = (
    "semantic-tts-grouping-round-v1"
)
SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION = (
    "semantic-tts-grouping-workflow-v2"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SemanticTtsGroupingItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subtitle_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=20_000)
    speaker_id: str = Field(min_length=1, max_length=256)

    @field_validator("subtitle_id", "text", "speaker_id")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("semantic grouping item text must not be empty")
        return normalized


class SemanticTtsGroupingInputV2(BaseModel):
    """Immutable workflow input, including the resolved model identity."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "semantic-tts-grouping-input-v2"
    ] = SEMANTIC_TTS_GROUPING_INPUT_SCHEMA_VERSION
    prompt_version: Literal[
        "semantic-tts-grouping-v1"
    ] = SEMANTIC_TTS_GROUPING_PROMPT_VERSION
    profile_id: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=256)
    provider_protocol: Literal["openai_compatible", "codex_cli"]
    provider_endpoint_fingerprint: str
    target_chars: int = Field(ge=20, le=1_000)
    max_chars: int = Field(ge=20, le=2_000)
    subtitles: list[SemanticTtsGroupingItemV1] = Field(
        min_length=1,
        max_length=20_000,
    )

    @field_validator("provider_endpoint_fingerprint")
    @classmethod
    def validate_endpoint_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "provider_endpoint_fingerprint must be lowercase SHA-256"
            )
        return normalized

    @model_validator(mode="after")
    def validate_limits_and_identity(
        self,
    ) -> "SemanticTtsGroupingInputV2":
        if self.max_chars < self.target_chars:
            raise ValueError(
                "max_chars must be greater than or equal to target_chars"
            )
        subtitle_ids = [
            item.subtitle_id for item in self.subtitles
        ]
        if len(set(subtitle_ids)) != len(subtitle_ids):
            raise ValueError("subtitle_id values must be unique")
        return self


class SemanticTtsGroupingRoundInputV1(BaseModel):
    """Exact input to one externally submitted grouping round."""

    model_config = ConfigDict(extra="forbid")

    workflow_input: SemanticTtsGroupingInputV2
    round_index: Literal[1, 2]
    previous_validation_error: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )

    @field_validator("previous_validation_error")
    @classmethod
    def strip_previous_error(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "previous_validation_error must not be empty"
            )
        return normalized

    @model_validator(mode="after")
    def validate_round_context(
        self,
    ) -> "SemanticTtsGroupingRoundInputV1":
        if self.round_index == 1 and self.previous_validation_error:
            raise ValueError(
                "previous_validation_error is only valid for round two"
            )
        if self.round_index == 2 and not self.previous_validation_error:
            raise ValueError(
                "round two requires previous_validation_error"
            )
        return self


class SemanticTtsGroupingRoundArtifactV1(BaseModel):
    """Small, path-free result persisted before a durable step succeeds."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "semantic-tts-grouping-round-v1"
    ] = SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION
    round_index: Literal[1, 2]
    response_shape: Literal[
        "groups_array",
        "groups_missing",
        "groups_not_array",
        "group_not_array",
        "subtitle_id_not_string",
    ] = "groups_array"
    groups: list[list[str]] = Field(max_length=20_000)
    llm_call: VideoLocalizationLlmCallRecord

    @field_validator("groups")
    @classmethod
    def validate_groups_shape(
        cls,
        groups: list[list[str]],
    ) -> list[list[str]]:
        normalized: list[list[str]] = []
        for group in groups:
            normalized_group = [
                str(value).strip() for value in group
            ]
            normalized.append(normalized_group)
        return normalized

    @model_validator(mode="after")
    def validate_call_identity(
        self,
    ) -> "SemanticTtsGroupingRoundArtifactV1":
        if (
            self.llm_call.purpose != "semantic_tts_grouping"
            or self.llm_call.round_index != self.round_index
        ):
            raise ValueError(
                "semantic grouping artifact call identity differs from round"
            )
        if self.response_shape != "groups_array" and self.groups:
            raise ValueError(
                "invalid Provider response shape cannot contain groups"
            )
        return self


def semantic_tts_grouping_round_input_fingerprint(
    value: SemanticTtsGroupingRoundInputV1,
) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def semantic_tts_grouping_round_artifact_bytes(
    value: SemanticTtsGroupingRoundArtifactV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_semantic_tts_grouping_round_artifact(
    content: bytes,
) -> SemanticTtsGroupingRoundArtifactV1:
    if not isinstance(content, bytes) or not content:
        raise ValueError(
            "semantic grouping round artifact must be non-empty bytes"
        )
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "semantic grouping round artifact is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            "semantic grouping round artifact must be a JSON object"
        )
    return SemanticTtsGroupingRoundArtifactV1.model_validate(payload)


def _canonical_bytes(value: BaseModel) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "SEMANTIC_TTS_GROUPING_INPUT_SCHEMA_VERSION",
    "SEMANTIC_TTS_GROUPING_PROMPT_VERSION",
    "SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION",
    "SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION",
    "SemanticTtsGroupingInputV2",
    "SemanticTtsGroupingItemV1",
    "SemanticTtsGroupingRoundArtifactV1",
    "SemanticTtsGroupingRoundInputV1",
    "parse_semantic_tts_grouping_round_artifact",
    "semantic_tts_grouping_round_artifact_bytes",
    "semantic_tts_grouping_round_input_fingerprint",
]
