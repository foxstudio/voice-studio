"""Durable Provider-call contracts for managed ASR review decisions."""

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


ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-review-decisions-development-workflow-v1"
)
ASR_REVIEW_DECISIONS_CALL_INPUT_SCHEMA_VERSION = (
    "asr-review-decisions-call-input-v1"
)
ASR_REVIEW_DECISIONS_CALL_ARTIFACT_SCHEMA_VERSION = (
    "asr-review-decisions-call-artifact-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ModelT = TypeVar("ModelT", bound=BaseModel)
ReviewDecisionCallGroup = Literal["primary", "coverage"]


class AsrReviewDecisionsCallInputV1(BaseModel):
    """Secret-free identity of one adjudication model attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-review-decisions-call-input-v1"
    ] = ASR_REVIEW_DECISIONS_CALL_INPUT_SCHEMA_VERSION
    behavior_version: Literal[
        "asr-review-decisions-v4"
    ] = "asr-review-decisions-v4"
    behavior_fingerprint: str
    call_group: ReviewDecisionCallGroup
    attempt: int = Field(ge=1, le=2)
    round_index: int = Field(ge=1, le=2)
    issue_ids: tuple[str, ...] = Field(min_length=1)
    primary_artifact_fingerprint: str | None = None
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
        return _fingerprint(value, "review-decisions call")

    @field_validator("primary_artifact_fingerprint")
    @classmethod
    def validate_primary_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _fingerprint(value, "review-decisions primary artifact")

    @field_validator("issue_ids")
    @classmethod
    def validate_issue_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("review-decision issue IDs must be unique")
        return value

    @model_validator(mode="after")
    def validate_lineage(self) -> "AsrReviewDecisionsCallInputV1":
        if (
            self.call_group == "coverage"
        ) != bool(self.primary_artifact_fingerprint):
            raise ValueError(
                "coverage calls require exactly one primary artifact"
            )
        return self


class AsrReviewDecisionsCallArtifactV1(BaseModel):
    """Normalized response and observable record for one committed call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-review-decisions-call-artifact-v1"
    ] = ASR_REVIEW_DECISIONS_CALL_ARTIFACT_SCHEMA_VERSION
    call_input_fingerprint: str
    call_group: ReviewDecisionCallGroup
    attempt: int = Field(ge=1, le=2)
    round_index: int = Field(ge=1, le=2)
    issue_ids: tuple[str, ...] = Field(min_length=1)
    primary_artifact_fingerprint: str | None = None
    response: dict
    llm_call: VideoLocalizationLlmCallRecord

    @field_validator(
        "call_input_fingerprint",
        "primary_artifact_fingerprint",
    )
    @classmethod
    def validate_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _fingerprint(value, "review-decisions artifact")

    @model_validator(mode="after")
    def validate_call_identity(
        self,
    ) -> "AsrReviewDecisionsCallArtifactV1":
        expected_call_id = (
            f"review-decisions-r{self.round_index}-"
            f"{self.call_group}-a{self.attempt:02d}"
        )
        if (
            self.llm_call.call_id != expected_call_id
            or self.llm_call.purpose != "review_decisions"
            or self.llm_call.round_index != self.round_index
            or tuple(self.llm_call.candidate_ids) != self.issue_ids
        ):
            raise ValueError(
                "review-decisions call record identity mismatch"
            )
        if (
            self.call_group == "coverage"
        ) != bool(self.primary_artifact_fingerprint):
            raise ValueError(
                "review-decisions artifact lineage is incomplete"
            )
        return self


def review_decisions_call_input(
    *,
    behavior_version: str,
    behavior_fingerprint: str,
    call_group: ReviewDecisionCallGroup,
    attempt: int,
    round_index: int,
    issue_ids: tuple[str, ...],
    primary_artifact_fingerprint: str | None,
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
) -> AsrReviewDecisionsCallInputV1:
    return AsrReviewDecisionsCallInputV1(
        behavior_version=behavior_version,
        behavior_fingerprint=behavior_fingerprint,
        call_group=call_group,
        attempt=attempt,
        round_index=round_index,
        issue_ids=issue_ids,
        primary_artifact_fingerprint=primary_artifact_fingerprint,
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


def review_decisions_call_input_fingerprint(
    value: AsrReviewDecisionsCallInputV1,
) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def review_decisions_call_artifact_bytes(
    value: AsrReviewDecisionsCallArtifactV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_review_decisions_call_artifact(
    content: bytes,
) -> AsrReviewDecisionsCallArtifactV1:
    return _parse_model(
        content,
        AsrReviewDecisionsCallArtifactV1,
        label="review-decisions call artifact",
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
    "ASR_REVIEW_DECISIONS_CALL_ARTIFACT_SCHEMA_VERSION",
    "ASR_REVIEW_DECISIONS_CALL_INPUT_SCHEMA_VERSION",
    "ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION",
    "AsrReviewDecisionsCallArtifactV1",
    "AsrReviewDecisionsCallInputV1",
    "ReviewDecisionCallGroup",
    "parse_review_decisions_call_artifact",
    "review_decisions_call_artifact_bytes",
    "review_decisions_call_input",
    "review_decisions_call_input_fingerprint",
]
