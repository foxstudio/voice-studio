"""Managed input and final contracts for the transcript quality gate."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
)

from app.domains.video_localization import transcript_quality_gate


ASR_TRANSCRIPT_QUALITY_GATE_PREPARED_INPUT_SCHEMA_VERSION = (
    "asr-transcript-quality-gate-managed-input-v1"
)
ASR_TRANSCRIPT_QUALITY_GATE_STEP_OUTPUT_SCHEMA_VERSION = (
    "asr-transcript-quality-gate-step-output-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ModelT = TypeVar("ModelT", bound=BaseModel)


class AsrTranscriptQualityGatePreparedInputV1(BaseModel):
    """Exact whole-recheck authority consumed by the local gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-transcript-quality-gate-managed-input-v1"
    ] = ASR_TRANSCRIPT_QUALITY_GATE_PREPARED_INPUT_SCHEMA_VERSION
    whole_recheck_artifact_fingerprint: str
    behavior_fingerprint: str
    request: (
        transcript_quality_gate.AsrTranscriptQualityGateInput
    )

    @field_validator(
        "whole_recheck_artifact_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "transcript quality gate fingerprint must be lowercase SHA-256"
            )
        return normalized


class AsrTranscriptQualityGateStepOutputV1(BaseModel):
    """Deterministic local decision bound to one prepared input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-transcript-quality-gate-step-output-v1"
    ] = ASR_TRANSCRIPT_QUALITY_GATE_STEP_OUTPUT_SCHEMA_VERSION
    prepared_input_fingerprint: str
    result: (
        transcript_quality_gate.AsrTranscriptQualityGateResult
    )

    @field_validator("prepared_input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "transcript quality gate input fingerprint must be lowercase SHA-256"
            )
        return normalized


def validate_final_against_input(
    prepared: AsrTranscriptQualityGatePreparedInputV1,
    final: AsrTranscriptQualityGateStepOutputV1,
) -> None:
    """Re-run the pure gate and require the exact canonical result."""

    if (
        final.prepared_input_fingerprint
        != prepared_input_fingerprint(prepared)
        or final.result.input != prepared.request
    ):
        raise ValueError(
            "transcript quality gate final input changed"
        )
    expected = (
        transcript_quality_gate
        .DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE
        .run(prepared.request)
        .model_copy(update={"duration_ms": 0})
    )
    if final.result != expected:
        raise ValueError(
            "transcript quality gate derived result changed"
        )


def prepared_input_bytes(
    value: AsrTranscriptQualityGatePreparedInputV1,
) -> bytes:
    return _canonical_bytes(value)


def prepared_input_fingerprint(
    value: AsrTranscriptQualityGatePreparedInputV1,
) -> str:
    return hashlib.sha256(prepared_input_bytes(value)).hexdigest()


def step_output_bytes(
    value: AsrTranscriptQualityGateStepOutputV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_prepared_input(
    content: bytes,
) -> AsrTranscriptQualityGatePreparedInputV1:
    return _parse_model(
        content,
        AsrTranscriptQualityGatePreparedInputV1,
        label="transcript quality gate prepared input",
    )


def parse_step_output(
    content: bytes,
) -> AsrTranscriptQualityGateStepOutputV1:
    return _parse_model(
        content,
        AsrTranscriptQualityGateStepOutputV1,
        label="transcript quality gate step output",
    )


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
    "ASR_TRANSCRIPT_QUALITY_GATE_PREPARED_INPUT_SCHEMA_VERSION",
    "ASR_TRANSCRIPT_QUALITY_GATE_STEP_OUTPUT_SCHEMA_VERSION",
    "AsrTranscriptQualityGatePreparedInputV1",
    "AsrTranscriptQualityGateStepOutputV1",
    "parse_prepared_input",
    "parse_step_output",
    "prepared_input_bytes",
    "prepared_input_fingerprint",
    "step_output_bytes",
    "validate_final_against_input",
]
