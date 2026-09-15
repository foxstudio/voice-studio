from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.voice_studio import (
    VideoLocalizationCue,
    VideoLocalizationSubtitleCue,
)


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VideoLocalizationMutationAckResponse(_StrictContract):
    """Small acknowledgement for a mutation already applied optimistically."""

    schema_version: Literal["video-localization-mutation-ack-v1"] = (
        "video-localization-mutation-ack-v1"
    )
    updated_at: str | None = None
    revision: str = Field(description=(
        "Server revision observed while forming this partial acknowledgement. "
        "Use as a freshness hint, not as a consumed workspace/timeline snapshot revision."
    ))


class VideoLocalizationUiStatePatchResponse(
    VideoLocalizationMutationAckResponse
):
    """Only the normalized UI keys changed by the current request."""

    schema_version: Literal["video-localization-ui-state-patch-v1"] = (
        "video-localization-ui-state-patch-v1"
    )
    ui_state_patch: dict[str, Any] = Field(default_factory=dict)


class VideoLocalizationTimelineMutationResponse(
    VideoLocalizationMutationAckResponse
):
    """Bounded result for one timeline placement/replacement command."""

    schema_version: Literal["video-localization-timeline-mutation-v1"] = (
        "video-localization-timeline-mutation-v1"
    )
    affected_clip_ids: list[str] = Field(default_factory=list)
    timeline_clips: list[dict[str, Any]] = Field(default_factory=list)
    cues: list[VideoLocalizationCue] = Field(default_factory=list)
    localized_subtitles: list[VideoLocalizationSubtitleCue] = Field(
        default_factory=list
    )
    dub_lane_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    discarded_tts_task_ids: list[str] = Field(default_factory=list)
