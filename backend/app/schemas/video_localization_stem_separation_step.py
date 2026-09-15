"""Versioned, path-free contracts for managed stem separation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


STEM_SEPARATION_STEP_ID = "separate_stems"
STEM_SEPARATION_STEP_INPUT_SCHEMA_VERSION = (
    "stem-separation-step-input-v1"
)
STEM_SEPARATION_STEP_OUTPUT_SCHEMA_VERSION = (
    "stem-separation-step-output-v1"
)
STEM_SEPARATION_WORKFLOW_VERSION = (
    "stem-separation-workflow-v1"
)


class StemSeparationStepInputV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "stem-separation-step-input-v1"
    ] = STEM_SEPARATION_STEP_INPUT_SCHEMA_VERSION
    source_audio_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class StemSeparationStepOutputV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "stem-separation-step-output-v1"
    ] = STEM_SEPARATION_STEP_OUTPUT_SCHEMA_VERSION
    separation_status: Literal["completed"]
    source_audio_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    vocals_clean_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    background_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    separation_engine_id: str = Field(min_length=1, max_length=160)
    quality_flags: tuple[str, ...] = ()
    track_count: Literal[2]
    available_track_count: Literal[2]
    media_status: Literal["complete"]


def stem_separation_step_input_fingerprint(
    source_audio_sha256: str,
) -> str:
    return _fingerprint(
        StemSeparationStepInputV1(
            source_audio_sha256=source_audio_sha256,
        )
    )


def stem_separation_step_output_from_summary(
    summary: Mapping[str, Any],
    *,
    source_audio_sha256: str,
    vocals_clean_sha256: str,
    background_sha256: str,
    quality_flags: list[str] | tuple[str, ...],
) -> StemSeparationStepOutputV1:
    return StemSeparationStepOutputV1.model_validate(
        {
            "separation_status": summary.get(
                "separation_status"
            ),
            "source_audio_sha256": source_audio_sha256,
            "vocals_clean_sha256": vocals_clean_sha256,
            "background_sha256": background_sha256,
            "separation_engine_id": summary.get(
                "separation_engine_id"
            ),
            "quality_flags": tuple(quality_flags),
            "track_count": summary.get("track_count"),
            "available_track_count": summary.get(
                "available_track_count"
            ),
            "media_status": summary.get("media_status"),
        }
    )


def stem_separation_step_output_bytes(
    output: StemSeparationStepOutputV1,
) -> bytes:
    return _canonical_json(output)


def parse_stem_separation_step_output(
    content: bytes,
) -> StemSeparationStepOutputV1:
    return StemSeparationStepOutputV1.model_validate_json(content)


def _fingerprint(model: BaseModel) -> str:
    return hashlib.sha256(_canonical_json(model)).hexdigest()


def _canonical_json(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "STEM_SEPARATION_STEP_ID",
    "STEM_SEPARATION_STEP_INPUT_SCHEMA_VERSION",
    "STEM_SEPARATION_STEP_OUTPUT_SCHEMA_VERSION",
    "STEM_SEPARATION_WORKFLOW_VERSION",
    "StemSeparationStepInputV1",
    "StemSeparationStepOutputV1",
    "parse_stem_separation_step_output",
    "stem_separation_step_input_fingerprint",
    "stem_separation_step_output_bytes",
    "stem_separation_step_output_from_summary",
]
