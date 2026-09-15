"""Serializable contracts for the ASR timing and subtitle-track nodes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationAudioBoundaryEvidence,
    VideoLocalizationBoundaryReview,
    VideoLocalizationCue,
    VideoLocalizationSpeaker,
    VideoLocalizationTranscriptionState,
    VideoLocalizationTranscriptSegment,
)
from app.domains.video_localization.speaker_diarization import (
    SpeakerDiarizationSegment,
)


class AsrAlignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-alignment-input-v1"] = (
        "asr-alignment-input-v1"
    )
    alignment_audio_path: str = Field(min_length=1)
    alignment_audio_sha256: str = Field(min_length=1)
    alignment_source_track_id: str = Field(min_length=1)
    segments: list[VideoLocalizationTranscriptSegment]
    diarization_segments: list[SpeakerDiarizationSegment] = Field(
        default_factory=list
    )
    language: str = Field(min_length=1)
    duration_ms: int | None = Field(default=None, ge=1)


class AsrAlignmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-alignment-v1"] = "asr-alignment-v1"
    input: AsrAlignmentInput
    words: list[VideoLocalizationAlignedWord]
    metadata: dict[str, Any]


class AsrAudioBoundariesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-audio-boundaries-input-v1"] = (
        "asr-audio-boundaries-input-v1"
    )
    audio_path: str = Field(min_length=1)
    audio_sha256: str = Field(min_length=1)
    source_track_id: str = Field(min_length=1)
    words: list[VideoLocalizationAlignedWord]
    video_frame_rate: float | None = Field(default=None, gt=0)


class AsrAudioBoundariesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-audio-boundaries-v1"] = (
        "asr-audio-boundaries-v1"
    )
    input: AsrAudioBoundariesInput
    boundary_features: list[VideoLocalizationAudioBoundaryEvidence]
    subtitle_entry_by_word_id: dict[str, int]
    metadata: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_speech_onsets(cls, value):
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        legacy = migrated.pop("speech_onset_by_word_id", None)
        if (
            "subtitle_entry_by_word_id" not in migrated
            and isinstance(legacy, dict)
        ):
            migrated["subtitle_entry_by_word_id"] = legacy
        return migrated


class AsrBoundaryReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-boundary-review-input-v1"] = (
        "asr-boundary-review-input-v1"
    )
    words: list[VideoLocalizationAlignedWord]
    boundary_features: list[VideoLocalizationAudioBoundaryEvidence]
    language: str = Field(min_length=1)
    segmentation_profile_id: str = Field(min_length=1)
    audio_analysis_available: bool
    profile_id: str | None = None
    existing_reviews: list[VideoLocalizationBoundaryReview] = Field(
        default_factory=list
    )


class AsrBoundaryReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-boundary-review-v1"] = (
        "asr-boundary-review-v1"
    )
    input: AsrBoundaryReviewInput
    reviews: list[VideoLocalizationBoundaryReview]
    metadata: dict[str, Any]


class AsrSubtitleTrackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-subtitle-track-input-v1"] = (
        "asr-subtitle-track-input-v1"
    )
    transcription: VideoLocalizationTranscriptionState
    existing_speakers: list[VideoLocalizationSpeaker] = Field(
        default_factory=list
    )
    source_duration_ms: int | None = Field(default=None, gt=0)
    video_frame_rate: float | None = Field(default=None, gt=0)


class AsrSubtitleTrackResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-subtitle-track-v1"] = (
        "asr-subtitle-track-v1"
    )
    input: AsrSubtitleTrackInput
    transcription: VideoLocalizationTranscriptionState
    speakers: list[VideoLocalizationSpeaker]
    cues: list[VideoLocalizationCue]


__all__ = [
    "AsrAlignmentInput",
    "AsrAlignmentResult",
    "AsrAudioBoundariesInput",
    "AsrAudioBoundariesResult",
    "AsrBoundaryReviewInput",
    "AsrBoundaryReviewResult",
    "AsrSubtitleTrackInput",
    "AsrSubtitleTrackResult",
]
