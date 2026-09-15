"""Versioned atomic contracts for synthesized-dub subtitle generation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DUB_SUBTITLE_WORKFLOW_SCHEMA_VERSION = "dub-subtitle-workflow-v1"
DUB_SUBTITLE_PREPARE_TRACK_INPUT_SCHEMA_VERSION = (
    "dub-subtitle-prepare-track-input-v1"
)
DUB_SUBTITLE_PREPARE_TRACK_OUTPUT_SCHEMA_VERSION = (
    "dub-subtitle-prepare-track-output-v1"
)
DUB_SUBTITLE_TRANSCRIBE_TRACK_INPUT_SCHEMA_VERSION = (
    "dub-subtitle-transcribe-track-input-v1"
)
DUB_SUBTITLE_TRANSCRIBE_TRACK_OUTPUT_SCHEMA_VERSION = (
    "dub-subtitle-transcribe-track-output-v1"
)
DUB_SUBTITLE_PROOFREAD_TEXT_INPUT_SCHEMA_VERSION = (
    "dub-subtitle-proofread-text-input-v1"
)
DUB_SUBTITLE_PROOFREAD_TEXT_OUTPUT_SCHEMA_VERSION = (
    "dub-subtitle-proofread-text-output-v1"
)
DUB_SUBTITLE_ALIGN_WORDS_INPUT_SCHEMA_VERSION = (
    "dub-subtitle-align-words-input-v1"
)
DUB_SUBTITLE_ALIGN_WORDS_OUTPUT_SCHEMA_VERSION = (
    "dub-subtitle-align-words-output-v1"
)
DUB_SUBTITLE_SEGMENT_SUBTITLES_INPUT_SCHEMA_VERSION = (
    "dub-subtitle-segment-subtitles-input-v1"
)
DUB_SUBTITLE_SEGMENT_SUBTITLES_OUTPUT_SCHEMA_VERSION = (
    "dub-subtitle-segment-subtitles-output-v1"
)
DUB_SUBTITLE_COMMIT_INPUT_SCHEMA_VERSION = (
    "dub-subtitle-commit-input-v1"
)
DUB_SUBTITLE_COMMIT_OUTPUT_SCHEMA_VERSION = (
    "dub-subtitle-commit-output-v1"
)


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _SubtitleEntryMapContract(_StrictContract):
    subtitle_entry_by_word_id: dict[str, int] = Field(
        default_factory=dict
    )

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


class DubSubtitleSourceClip(_StrictContract):
    """One audible clip frozen at its actual timeline position."""

    clip_id: str = Field(min_length=1)
    dub_lane: int = Field(ge=0)
    audio_sha256: str = Field(min_length=64, max_length=64)
    timeline_start_ms: int = Field(ge=0)
    timeline_end_ms: int = Field(gt=0)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    reference_ids: list[str] = Field(default_factory=list)
    speaker_id: str | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "DubSubtitleSourceClip":
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("配音片段的时间线结束时间必须晚于开始时间。")
        if self.source_end_ms <= self.source_start_ms:
            raise ValueError("配音片段的音频裁切结束时间必须晚于开始时间。")
        return self


class DubSubtitleTextReference(_StrictContract):
    """Audible generation wording used for correction, never for timing."""

    subtitle_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    display_text: str | None = None


class DubSubtitlePreparedAudio(_StrictContract):
    """Controlled full-track audio artifact without an arbitrary local path."""

    artifact_id: str = Field(min_length=1)
    audio_sha256: str = Field(min_length=64, max_length=64)
    duration_ms: int = Field(ge=1)


class DubSubtitlePrepareTrackInput(_StrictContract):
    schema_version: Literal[
        "dub-subtitle-prepare-track-input-v1"
    ] = DUB_SUBTITLE_PREPARE_TRACK_INPUT_SCHEMA_VERSION
    timeline_duration_ms: int = Field(ge=1)
    # An incremental deletion or mute can have no remaining audible clip.  It
    # still needs the canonical commit step to remove only the now-invalid
    # derived captions, so an empty render input is meaningful here.
    clips: list[DubSubtitleSourceClip] = Field(default_factory=list)


class DubSubtitleAffectedRange(_StrictContract):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "DubSubtitleAffectedRange":
        if self.end_ms <= self.start_ms:
            raise ValueError("受影响时间范围的结束必须晚于开始。")
        return self


class DubSubtitleRegenerationScope(_StrictContract):
    """Frozen replacement boundary for an incremental caption run.

    ``source_revision`` still fences the complete audible timeline.  This
    scope only says which old derived cues may be replaced by this run.
    """

    mode: Literal["full", "incremental"]
    reason: Literal[
        "explicit_full",
        "no_prior_subtitles",
        "dirty_scope",
        "legacy_scope_missing",
    ]
    affected_clip_ids: list[str] = Field(default_factory=list)
    affected_ranges: list[DubSubtitleAffectedRange] = Field(
        default_factory=list
    )
    replaceable_subtitle_fingerprints: dict[str, str] = Field(
        default_factory=dict
    )


class DubSubtitleWorkflowInput(_StrictContract):
    schema_version: Literal[
        "dub-subtitle-workflow-v1"
    ] = DUB_SUBTITLE_WORKFLOW_SCHEMA_VERSION
    source_revision: str = Field(min_length=64, max_length=64)
    video_frame_rate: float | None = Field(default=None, gt=0)
    prepare_track: DubSubtitlePrepareTrackInput
    references: list[DubSubtitleTextReference] = Field(default_factory=list)
    # v1 development snapshots predate incremental replacement boundaries.
    # They remain readable, but the workflow rejects execution rather than
    # inventing a scope from current project state.
    regeneration_scope: DubSubtitleRegenerationScope | None = None


class DubSubtitlePrepareTrackOutput(_StrictContract):
    schema_version: Literal[
        "dub-subtitle-prepare-track-output-v1"
    ] = DUB_SUBTITLE_PREPARE_TRACK_OUTPUT_SCHEMA_VERSION
    audio: DubSubtitlePreparedAudio
    clip_count: int = Field(ge=1)


class DubSubtitleTranscriptChunk(_StrictContract):
    """ASR text inside a full-audio compute window, not subtitle timing."""

    segment_id: str = Field(min_length=1)
    audio_window_start_ms: int = Field(ge=0)
    audio_window_end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)
    timing_kind: Literal["audio_chunk_window"] = "audio_chunk_window"

    @model_validator(mode="after")
    def validate_range(self) -> "DubSubtitleTranscriptChunk":
        if self.audio_window_end_ms <= self.audio_window_start_ms:
            raise ValueError("ASR 音频块结束时间必须晚于开始时间。")
        return self


class DubSubtitleTranscribeTrackInput(_StrictContract):
    schema_version: Literal[
        "dub-subtitle-transcribe-track-input-v1"
    ] = DUB_SUBTITLE_TRANSCRIBE_TRACK_INPUT_SCHEMA_VERSION
    audio: DubSubtitlePreparedAudio
    engine_id: str = Field(min_length=1)
    requested_language: str = Field(default="zh", min_length=1)
    audible_clips: list[DubSubtitleSourceClip] = Field(
        default_factory=list
    )


class DubSubtitleTranscribeTrackOutput(_StrictContract):
    schema_version: Literal[
        "dub-subtitle-transcribe-track-output-v1"
    ] = DUB_SUBTITLE_TRANSCRIBE_TRACK_OUTPUT_SCHEMA_VERSION
    audio_sha256: str = Field(min_length=64, max_length=64)
    engine_id: str = Field(min_length=1)
    language: str = Field(min_length=1)
    chunks: list[DubSubtitleTranscriptChunk] = Field(min_length=1)
    quality_status: Literal["passed", "warning", "failed"]
    quality_flags: list[str] = Field(default_factory=list)


class DubSubtitleTextCorrection(_StrictContract):
    before: str
    after: str
    kind: Literal["added", "removed", "changed"]


class DubSubtitleProofreadChunk(_StrictContract):
    """Corrected wording attached to its ASR audio compute window."""

    cue_id: str = Field(min_length=1)
    audio_window_start_ms: int = Field(ge=0)
    audio_window_end_ms: int = Field(gt=0)
    raw_text: str = Field(min_length=1)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> "DubSubtitleProofreadChunk":
        if self.audio_window_end_ms <= self.audio_window_start_ms:
            raise ValueError("校对音频块结束时间必须晚于开始时间。")
        return self


class DubSubtitleProofreadTextInput(_StrictContract):
    schema_version: Literal[
        "dub-subtitle-proofread-text-input-v1"
    ] = DUB_SUBTITLE_PROOFREAD_TEXT_INPUT_SCHEMA_VERSION
    chunks: list[DubSubtitleTranscriptChunk] = Field(min_length=1)
    references: list[DubSubtitleTextReference] = Field(min_length=1)


class DubSubtitleProofreadTextOutput(_StrictContract):
    schema_version: Literal[
        "dub-subtitle-proofread-text-output-v1"
    ] = DUB_SUBTITLE_PROOFREAD_TEXT_OUTPUT_SCHEMA_VERSION
    chunks: list[DubSubtitleProofreadChunk] = Field(min_length=1)
    corrections: list[DubSubtitleTextCorrection] = Field(
        default_factory=list
    )
    unmatched_asr_char_count: int = Field(default=0, ge=0)
    unmatched_reference_char_count: int = Field(default=0, ge=0)


class DubSubtitleAlignWordsInput(_StrictContract):
    schema_version: Literal[
        "dub-subtitle-align-words-input-v1"
    ] = DUB_SUBTITLE_ALIGN_WORDS_INPUT_SCHEMA_VERSION
    audio: DubSubtitlePreparedAudio
    language: str = Field(min_length=1)
    chunks: list[DubSubtitleProofreadChunk] = Field(min_length=1)
    video_frame_rate: float | None = Field(default=None, gt=0)


class DubSubtitleAlignedWord(_StrictContract):
    word_id: str = Field(min_length=1)
    cue_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    timing_source: Literal["forced_aligner"] = "forced_aligner"

    @model_validator(mode="after")
    def validate_range(self) -> "DubSubtitleAlignedWord":
        if self.end_ms < self.start_ms:
            raise ValueError("声学字词结束时间不得早于开始时间。")
        return self


class DubSubtitleSegmentTextChunk(_StrictContract):
    """Corrected text identity only; ASR compute windows cannot enter step 5."""

    cue_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class DubSubtitleTimelineClip(_StrictContract):
    """Only timeline provenance needed when final cues are assembled."""

    clip_id: str = Field(min_length=1)
    dub_lane: int = Field(ge=0)
    timeline_start_ms: int = Field(ge=0)
    timeline_end_ms: int = Field(gt=0)
    speaker_id: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "DubSubtitleTimelineClip":
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("配音片段的时间线结束时间必须晚于开始时间。")
        return self


class DubSubtitleAlignWordsOutput(_SubtitleEntryMapContract):
    schema_version: Literal[
        "dub-subtitle-align-words-output-v1"
    ] = DUB_SUBTITLE_ALIGN_WORDS_OUTPUT_SCHEMA_VERSION
    audio_sha256: str = Field(min_length=64, max_length=64)
    words: list[DubSubtitleAlignedWord] = Field(min_length=1)
    alignment_call_count: int = Field(ge=1)


class DubSubtitleSegmentSubtitlesInput(_SubtitleEntryMapContract):
    schema_version: Literal[
        "dub-subtitle-segment-subtitles-input-v1"
    ] = DUB_SUBTITLE_SEGMENT_SUBTITLES_INPUT_SCHEMA_VERSION
    audio_sha256: str = Field(min_length=64, max_length=64)
    asr_requires_review: bool = False
    video_duration_ms: int | None = Field(default=None, gt=0)
    video_frame_rate: float | None = Field(default=None, gt=0)
    chunks: list[DubSubtitleSegmentTextChunk] = Field(min_length=1)
    words: list[DubSubtitleAlignedWord] = Field(min_length=1)
    clips: list[DubSubtitleTimelineClip] = Field(min_length=1)


class DubSubtitleCandidateCue(_StrictContract):
    subtitle_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)
    speaker_id: str | None = None
    source_clip_ids: list[str] = Field(default_factory=list)
    dub_lanes: list[int] = Field(default_factory=list)
    source_audio_sha256: str = Field(min_length=64, max_length=64)
    needs_review: bool = False
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_range(self) -> "DubSubtitleCandidateCue":
        if self.end_ms <= self.start_ms:
            raise ValueError("配音字幕结束时间必须晚于开始时间。")
        return self


class DubSubtitleSegmentSubtitlesOutput(_StrictContract):
    schema_version: Literal[
        "dub-subtitle-segment-subtitles-output-v1"
    ] = DUB_SUBTITLE_SEGMENT_SUBTITLES_OUTPUT_SCHEMA_VERSION
    subtitles: list[DubSubtitleCandidateCue] = Field(min_length=1)


class DubSubtitleCommitInput(_StrictContract):
    schema_version: Literal[
        "dub-subtitle-commit-input-v1"
    ] = DUB_SUBTITLE_COMMIT_INPUT_SCHEMA_VERSION
    source_revision: str = Field(min_length=64, max_length=64)
    subtitles: list[DubSubtitleCandidateCue] = Field(default_factory=list)
    regeneration_scope: DubSubtitleRegenerationScope | None = None


class DubSubtitleCommitOutput(_StrictContract):
    schema_version: Literal[
        "dub-subtitle-commit-output-v1"
    ] = DUB_SUBTITLE_COMMIT_OUTPUT_SCHEMA_VERSION
    saved_source_revision: str = Field(min_length=64, max_length=64)
    saved_subtitle_count: int = Field(ge=0)
    generated_subtitle_count: int = Field(default=0, ge=0)
    preserved_subtitle_count: int = Field(default=0, ge=0)
    preserved_manual_subtitle_count: int = Field(default=0, ge=0)


__all__ = [
    "DUB_SUBTITLE_ALIGN_WORDS_INPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_ALIGN_WORDS_OUTPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_COMMIT_INPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_COMMIT_OUTPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_PREPARE_TRACK_INPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_PREPARE_TRACK_OUTPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_PROOFREAD_TEXT_INPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_PROOFREAD_TEXT_OUTPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_SEGMENT_SUBTITLES_INPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_SEGMENT_SUBTITLES_OUTPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_TRANSCRIBE_TRACK_INPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_TRANSCRIBE_TRACK_OUTPUT_SCHEMA_VERSION",
    "DUB_SUBTITLE_WORKFLOW_SCHEMA_VERSION",
    "DubSubtitleAlignedWord",
    "DubSubtitleAffectedRange",
    "DubSubtitleAlignWordsInput",
    "DubSubtitleAlignWordsOutput",
    "DubSubtitleCandidateCue",
    "DubSubtitleCommitInput",
    "DubSubtitleCommitOutput",
    "DubSubtitlePreparedAudio",
    "DubSubtitlePrepareTrackInput",
    "DubSubtitlePrepareTrackOutput",
    "DubSubtitleProofreadChunk",
    "DubSubtitleProofreadTextInput",
    "DubSubtitleProofreadTextOutput",
    "DubSubtitleRegenerationScope",
    "DubSubtitleSegmentSubtitlesInput",
    "DubSubtitleSegmentSubtitlesOutput",
    "DubSubtitleSegmentTextChunk",
    "DubSubtitleSourceClip",
    "DubSubtitleTextCorrection",
    "DubSubtitleTextReference",
    "DubSubtitleTimelineClip",
    "DubSubtitleTranscriptChunk",
    "DubSubtitleTranscribeTrackInput",
    "DubSubtitleTranscribeTrackOutput",
    "DubSubtitleWorkflowInput",
]
