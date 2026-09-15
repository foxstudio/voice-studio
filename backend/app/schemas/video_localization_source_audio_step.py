"""Versioned, path-free contracts for the source-audio operation step."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SOURCE_AUDIO_STEP_ID = "extract_source_audio"
SOURCE_AUDIO_STEP_INPUT_SCHEMA_VERSION = (
    "source-audio-step-input-v1"
)
SOURCE_AUDIO_STEP_OUTPUT_SCHEMA_VERSION = (
    "source-audio-step-output-v1"
)
SOURCE_AUDIO_WORKFLOW_VERSION = "source-audio-workflow-v1"


class SourceAudioStepInputV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "source-audio-step-input-v1"
    ] = SOURCE_AUDIO_STEP_INPUT_SCHEMA_VERSION
    source_video_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class SourceAudioStepOutputV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "source-audio-step-output-v1"
    ] = SOURCE_AUDIO_STEP_OUTPUT_SCHEMA_VERSION
    audio_extract_status: Literal["completed"]
    duration_ms: int = Field(ge=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0, le=64)
    track_count: Literal[1]
    available_track_count: Literal[1]
    media_status: Literal["available"]
    selected_source: Literal["source_media"]


def source_audio_step_input_fingerprint(
    source_video_fingerprint: str,
) -> str:
    payload = SourceAudioStepInputV1(
        source_video_fingerprint=source_video_fingerprint,
    )
    return _fingerprint(payload)


def source_audio_step_output_from_summary(
    summary: Mapping[str, Any],
) -> SourceAudioStepOutputV1:
    return SourceAudioStepOutputV1.model_validate(
        {
            "audio_extract_status": summary.get(
                "audio_extract_status"
            ),
            "duration_ms": summary.get("duration_ms"),
            "sample_rate": summary.get("sample_rate"),
            "channels": summary.get("channels"),
            "track_count": summary.get("track_count"),
            "available_track_count": summary.get(
                "available_track_count"
            ),
            "media_status": summary.get("media_status"),
            "selected_source": summary.get("selected_source"),
        }
    )


def source_audio_step_output_bytes(
    output: SourceAudioStepOutputV1,
) -> bytes:
    return _canonical_json(output)


def parse_source_audio_step_output(
    content: bytes,
) -> SourceAudioStepOutputV1:
    return SourceAudioStepOutputV1.model_validate_json(content)


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
    "SOURCE_AUDIO_STEP_ID",
    "SOURCE_AUDIO_STEP_INPUT_SCHEMA_VERSION",
    "SOURCE_AUDIO_STEP_OUTPUT_SCHEMA_VERSION",
    "SOURCE_AUDIO_WORKFLOW_VERSION",
    "SourceAudioStepInputV1",
    "SourceAudioStepOutputV1",
    "parse_source_audio_step_output",
    "source_audio_step_input_fingerprint",
    "source_audio_step_output_bytes",
    "source_audio_step_output_from_summary",
]
