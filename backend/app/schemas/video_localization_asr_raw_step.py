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

from app.models.schemas import (
    VideoLocalizationTranscriptSegment,
)


ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-raw-development-workflow-v1"
)
ASR_RAW_STEP_ID = "asr"
ASR_RAW_STEP_INPUT_SCHEMA_VERSION = (
    "asr-raw-step-input-v1"
)
ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION = (
    "asr-raw-step-output-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AsrRawStepInputV1(BaseModel):
    """Actual path-free audio input locked before ASR inference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_schema_version: Literal[
        "asr-raw-step-input-v1"
    ] = ASR_RAW_STEP_INPUT_SCHEMA_VERSION
    audio_sha256: str
    source_track_id: Literal[
        "original",
        "vocals",
        "dub",
    ]
    engine_id: str = Field(min_length=1, max_length=128)
    requested_language: str = Field(
        min_length=1,
        max_length=32,
    )
    duration_ms: int | None = Field(default=None, ge=0)

    @field_validator(
        "engine_id",
        "requested_language",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("ASR raw input text must not be empty")
        return normalized

    @field_validator("audio_sha256")
    @classmethod
    def validate_audio_sha256(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "ASR raw audio fingerprint must be lowercase SHA-256"
            )
        return normalized


class AsrRawResultInputV2(BaseModel):
    """Public-compatible ASR request identity without a runtime path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["asr-raw-v2"] = "asr-raw-v2"
    audio_sha256: str
    engine_id: str = Field(min_length=1, max_length=128)
    source_track_id: Literal[
        "original",
        "vocals",
        "dub",
    ]
    requested_language: str = Field(
        min_length=1,
        max_length=32,
    )
    duration_ms: int | None = Field(default=None, ge=0)
    context_terms: list[str] = Field(default_factory=list, max_length=8)

    @field_validator(
        "engine_id",
        "requested_language",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "ASR raw result input text must not be empty"
            )
        return normalized

    @field_validator("audio_sha256")
    @classmethod
    def validate_audio_sha256(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "ASR raw result audio fingerprint must be lowercase SHA-256"
            )
        return normalized


class AsrRawIncompleteRangeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    reason: str | None = Field(
        default=None,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_range(self) -> "AsrRawIncompleteRangeV1":
        if self.end_ms < self.start_ms:
            raise ValueError(
                "ASR incomplete range end must not precede start"
            )
        return self


class AsrRawStageTimingV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    duration_ms: int = Field(ge=0)
    segment_count: int = Field(ge=0)


class AsrRawQualitySummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["passed", "warning", "failed"]
    has_text: bool
    has_segments: bool
    timestamps_monotonic: bool
    segment_count: int = Field(ge=0)
    raw_text_char_count: int = Field(ge=0)
    first_start_ms: int | None = Field(default=None, ge=0)
    last_end_ms: int | None = Field(default=None, ge=0)
    audio_duration_ms: int | None = Field(default=None, ge=0)
    trailing_gap_ms: int | None = Field(default=None, ge=0)
    incomplete_range_count: int = Field(ge=0)
    warning_codes: list[str] = Field(default_factory=list)


class AsrRawResultV2(BaseModel):
    """Complete engine output with all filesystem locators removed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["asr-raw-v2"] = "asr-raw-v2"
    input: AsrRawResultInputV2
    raw_text: str
    language: str = Field(min_length=1, max_length=32)
    segments: list[
        VideoLocalizationTranscriptSegment
    ] = Field(default_factory=list)
    incomplete_chunk_ranges: list[
        AsrRawIncompleteRangeV1
    ] = Field(default_factory=list)
    usage_seconds: float | None = Field(default=None, ge=0)
    provider_response_id: str | None = Field(
        default=None,
        max_length=256,
    )
    stage_timing: AsrRawStageTimingV1
    quality_summary: AsrRawQualitySummaryV1

    @field_validator("language")
    @classmethod
    def strip_language(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "ASR raw result language must not be empty"
            )
        return normalized

    @model_validator(mode="after")
    def validate_result_summary(
        self,
    ) -> "AsrRawResultV2":
        segments = self.segments
        quality = self.quality_summary
        first_start_ms = (
            segments[0].start_ms if segments else None
        )
        last_end_ms = (
            segments[-1].end_ms if segments else None
        )
        timestamps_monotonic = all(
            segment.end_ms >= segment.start_ms
            and (
                index == 0
                or segment.start_ms
                >= segments[index - 1].start_ms
            )
            for index, segment in enumerate(segments)
        )
        trailing_gap_ms = (
            max(0, self.input.duration_ms - last_end_ms)
            if (
                self.input.duration_ms is not None
                and last_end_ms is not None
            )
            else None
        )
        expected = {
            "segment_count": len(segments),
            "raw_text_char_count": len(self.raw_text),
            "has_text": bool(self.raw_text.strip()),
            "has_segments": bool(segments),
            "timestamps_monotonic": timestamps_monotonic,
            "first_start_ms": first_start_ms,
            "last_end_ms": last_end_ms,
            "audio_duration_ms": self.input.duration_ms,
            "trailing_gap_ms": trailing_gap_ms,
            "incomplete_range_count": len(
                self.incomplete_chunk_ranges
            ),
        }
        actual = {
            key: getattr(quality, key)
            for key in expected
        }
        if actual != expected:
            raise ValueError(
                "ASR raw quality summary does not match result"
            )
        if (
            self.stage_timing.segment_count
            != len(segments)
        ):
            raise ValueError(
                "ASR raw timing count does not match segments"
            )
        return self


class AsrRawStepOutputV1(BaseModel):
    """Canonical managed artifact for one raw-ASR development run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-raw-step-output-v1"
    ] = ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION
    input: AsrRawStepInputV1
    result: AsrRawResultV2

    @model_validator(mode="after")
    def validate_result_identity(
        self,
    ) -> "AsrRawStepOutputV1":
        result_input = self.result.input
        if (
            result_input.audio_sha256
            != self.input.audio_sha256
            or result_input.source_track_id
            != self.input.source_track_id
            or result_input.engine_id
            != self.input.engine_id
            or result_input.requested_language
            != self.input.requested_language
            or result_input.duration_ms
            != self.input.duration_ms
        ):
            raise ValueError(
                "ASR raw result identity does not match locked input"
            )
        return self


def asr_raw_step_input(
    *,
    audio_sha256: str,
    source_track_id: str,
    engine_id: str,
    requested_language: str,
    duration_ms: int | None,
) -> AsrRawStepInputV1:
    return AsrRawStepInputV1(
        audio_sha256=audio_sha256,
        source_track_id=source_track_id,
        engine_id=engine_id,
        requested_language=requested_language,
        duration_ms=duration_ms,
    )


def asr_raw_step_input_fingerprint(
    value: AsrRawStepInputV1,
) -> str:
    return hashlib.sha256(
        _canonical_json(value.model_dump(mode="json"))
    ).hexdigest()


def asr_raw_step_output_from_payload(
    step_input: AsrRawStepInputV1,
    payload: dict,
) -> AsrRawStepOutputV1:
    raw_input = dict(payload.get("input") or {})
    raw_input.pop("audio_path", None)
    raw_ranges = payload.get("incomplete_chunk_ranges") or []
    if not isinstance(raw_ranges, list) or any(
        not isinstance(item, dict)
        for item in raw_ranges
    ):
        raise ValueError(
            "ASR raw incomplete ranges must be objects"
        )
    ranges = [
        {
            "start_ms": int(item.get("start_ms") or 0),
            "end_ms": int(item.get("end_ms") or 0),
            **(
                {"reason": str(item["reason"])}
                if item.get("reason") is not None
                else {}
            ),
        }
        for item in raw_ranges
    ]
    segments = list(payload.get("segments") or [])
    raw_timing = dict(payload.get("stage_timing") or {})
    result = AsrRawResultV2(
        contract_version=payload.get(
            "contract_version",
            "asr-raw-v2",
        ),
        input=raw_input,
        raw_text=str(payload.get("raw_text") or ""),
        language=str(payload.get("language") or ""),
        segments=segments,
        incomplete_chunk_ranges=ranges,
        usage_seconds=payload.get("usage_seconds"),
        provider_response_id=payload.get(
            "provider_response_id"
        ),
        stage_timing={
            "duration_ms": int(
                raw_timing.get("duration_ms") or 0
            ),
            "segment_count": int(
                raw_timing.get("segment_count")
                if raw_timing.get("segment_count") is not None
                else len(segments)
            ),
        },
        quality_summary=payload.get("quality_summary"),
    )
    return AsrRawStepOutputV1(
        input=step_input,
        result=result,
    )


def asr_raw_step_output_bytes(
    value: AsrRawStepOutputV1,
) -> bytes:
    return _canonical_json(
        value.model_dump(mode="json")
    )


def parse_asr_raw_step_output(
    content: bytes,
) -> AsrRawStepOutputV1:
    return AsrRawStepOutputV1.model_validate_json(content)


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION",
    "ASR_RAW_STEP_ID",
    "ASR_RAW_STEP_INPUT_SCHEMA_VERSION",
    "ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION",
    "AsrRawResultV2",
    "AsrRawStepInputV1",
    "AsrRawStepOutputV1",
    "asr_raw_step_input",
    "asr_raw_step_input_fingerprint",
    "asr_raw_step_output_bytes",
    "asr_raw_step_output_from_payload",
    "parse_asr_raw_step_output",
]
