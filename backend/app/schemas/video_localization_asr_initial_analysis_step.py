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
from app.schemas.video_localization_speaker_diarization_step import (
    SpeakerDiarizationResultV1,
    SpeakerDiarizationStepInputV1,
)


ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-initial-analysis-development-workflow-v1"
)
ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_ID = "diarization"
ASR_INITIAL_ANALYSIS_DIARIZATION_OUTPUT_SCHEMA_VERSION = (
    "asr-initial-analysis-diarization-outcome-v1"
)
ASR_INITIAL_ANALYSIS_JOIN_STEP_ID = "initial_analysis_join"
ASR_INITIAL_ANALYSIS_JOIN_OUTPUT_SCHEMA_VERSION = (
    "asr-initial-analysis-join-output-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^[A-Z0-9_]+$")


class AsrInitialAnalysisDiarizationOutcomeV1(BaseModel):
    """A durable branch outcome; ordinary diarization failure is degradable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-initial-analysis-diarization-outcome-v1"
    ] = ASR_INITIAL_ANALYSIS_DIARIZATION_OUTPUT_SCHEMA_VERSION
    input: SpeakerDiarizationStepInputV1
    status: Literal["success", "degraded"]
    result: SpeakerDiarizationResultV1 | None = None
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )

    @field_validator("error_code")
    @classmethod
    def validate_error_code(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if _ERROR_CODE.fullmatch(normalized) is None:
            raise ValueError(
                "diarization outcome error code must be stable"
            )
        return normalized

    @model_validator(mode="after")
    def validate_outcome(
        self,
    ) -> "AsrInitialAnalysisDiarizationOutcomeV1":
        if self.status == "success":
            if self.result is None or self.error_code is not None:
                raise ValueError(
                    "successful diarization outcome requires only a result"
                )
            result_input = self.result.input
            if (
                result_input.audio_sha256
                != self.input.audio_sha256
                or result_input.source_track_id
                != self.input.source_track_id
                or result_input.engine_id
                != self.input.requested_engine_id
                or result_input.duration_ms
                != self.input.duration_ms
                or result_input.min_speakers
                != self.input.min_speakers
                or result_input.max_speakers
                != self.input.max_speakers
            ):
                raise ValueError(
                    "diarization result identity does not match locked input"
                )
        elif self.result is not None or self.error_code is None:
            raise ValueError(
                "degraded diarization outcome requires only an error code"
            )
        return self


class AsrInitialAnalysisJoinOutputV1(BaseModel):
    """Small deterministic join artifact referencing both branch artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-initial-analysis-join-output-v1"
    ] = ASR_INITIAL_ANALYSIS_JOIN_OUTPUT_SCHEMA_VERSION
    audio_sha256: str
    source_track_id: str = Field(min_length=1, max_length=64)
    raw_artifact_fingerprint: str
    diarization_artifact_fingerprint: str
    segments: tuple[
        VideoLocalizationTranscriptSegment,
        ...,
    ] = ()
    warning_codes: tuple[str, ...] = ()

    @field_validator(
        "audio_sha256",
        "raw_artifact_fingerprint",
        "diarization_artifact_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "initial-analysis fingerprint must be lowercase SHA-256"
            )
        return normalized

    @field_validator("warning_codes")
    @classmethod
    def validate_warning_codes(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(
            str(value).strip().upper()
            for value in values
        )
        if any(
            not value
            or _ERROR_CODE.fullmatch(value) is None
            for value in normalized
        ):
            raise ValueError(
                "initial-analysis warnings must be stable codes"
            )
        if len(set(normalized)) != len(normalized):
            raise ValueError(
                "initial-analysis warnings must be unique"
            )
        return normalized

    @model_validator(mode="after")
    def validate_segments(
        self,
    ) -> "AsrInitialAnalysisJoinOutputV1":
        if any(
            segment.end_ms < segment.start_ms
            or (
                index > 0
                and segment.start_ms
                < self.segments[index - 1].start_ms
            )
            for index, segment in enumerate(self.segments)
        ):
            raise ValueError(
                "initial-analysis joined segments must be monotonic"
            )
        return self


def initial_analysis_join_input_fingerprint(
    *,
    raw_artifact_fingerprint: str,
    diarization_artifact_fingerprint: str,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "schema_version": (
                    ASR_INITIAL_ANALYSIS_JOIN_OUTPUT_SCHEMA_VERSION
                ),
                "raw_artifact_fingerprint": (
                    raw_artifact_fingerprint
                ),
                "diarization_artifact_fingerprint": (
                    diarization_artifact_fingerprint
                ),
            }
        )
    ).hexdigest()


def asr_initial_analysis_diarization_output_bytes(
    value: AsrInitialAnalysisDiarizationOutcomeV1,
) -> bytes:
    return _canonical_json(value.model_dump(mode="json"))


def parse_asr_initial_analysis_diarization_output(
    content: bytes,
) -> AsrInitialAnalysisDiarizationOutcomeV1:
    return (
        AsrInitialAnalysisDiarizationOutcomeV1
        .model_validate_json(content)
    )


def asr_initial_analysis_join_output_bytes(
    value: AsrInitialAnalysisJoinOutputV1,
) -> bytes:
    return _canonical_json(value.model_dump(mode="json"))


def parse_asr_initial_analysis_join_output(
    content: bytes,
) -> AsrInitialAnalysisJoinOutputV1:
    return AsrInitialAnalysisJoinOutputV1.model_validate_json(
        content
    )


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION",
    "ASR_INITIAL_ANALYSIS_DIARIZATION_OUTPUT_SCHEMA_VERSION",
    "ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_ID",
    "ASR_INITIAL_ANALYSIS_JOIN_OUTPUT_SCHEMA_VERSION",
    "ASR_INITIAL_ANALYSIS_JOIN_STEP_ID",
    "AsrInitialAnalysisDiarizationOutcomeV1",
    "AsrInitialAnalysisJoinOutputV1",
    "asr_initial_analysis_diarization_output_bytes",
    "asr_initial_analysis_join_output_bytes",
    "initial_analysis_join_input_fingerprint",
    "parse_asr_initial_analysis_diarization_output",
    "parse_asr_initial_analysis_join_output",
]
