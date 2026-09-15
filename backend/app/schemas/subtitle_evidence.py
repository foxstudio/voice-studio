"""Path-free public contracts for reusable subtitle timing evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.voice_studio import (
    VideoLocalizationAlignedWord,
    VideoLocalizationAudioBoundaryEvidence,
)


class SubtitleEvidenceSegmentInput(BaseModel):
    """One caller-owned transcript window to align against retained audio."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    segment_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class SubtitleEvidenceRequest(BaseModel):
    """Request strict timing evidence for one retained ASR upload.

    When ``segments`` is empty, the transcript windows saved by the ASR task
    are used. Supplying segments lets a downstream subtitle workflow align its
    already locked wording without importing Voice Studio internals.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["subtitle-evidence-request-v1"] = (
        "subtitle-evidence-request-v1"
    )
    language: Literal["auto", "zh", "en"] | None = None
    video_frame_rate: float | None = Field(default=None, gt=0)
    segments: list[SubtitleEvidenceSegmentInput] = Field(default_factory=list)


class SubtitleEvidenceResult(BaseModel):
    """Strict, path-free evidence; final subtitle wording remains caller-owned."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["subtitle-evidence-v1"] = "subtitle-evidence-v1"
    transcription_id: str
    engine_id: str
    filename: str
    language: Literal["auto", "zh", "en"]
    text: str
    audio_sha256: str
    duration_ms: int = Field(gt=0)
    segments: list[SubtitleEvidenceSegmentInput]
    aligned_words: list[VideoLocalizationAlignedWord]
    boundary_features: list[VideoLocalizationAudioBoundaryEvidence]
    subtitle_entry_by_word_id: dict[str, int]
    alignment_status: Literal["completed"]
    alignment_engine_id: str
    timing_confidence: Literal["high"]
    quality_flags: list[str] = Field(default_factory=list)
    alignment_call_count: int = Field(ge=1)
    audio_analysis_status: Literal["completed", "partial", "failed"]


__all__ = [
    "SubtitleEvidenceRequest",
    "SubtitleEvidenceResult",
    "SubtitleEvidenceSegmentInput",
]
