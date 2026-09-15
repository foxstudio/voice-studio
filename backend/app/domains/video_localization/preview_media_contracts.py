from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VideoPlaybackRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


class VideoPlaybackProxyRequest(BaseModel):
    """Browser intent for direct playback or a prioritized proxy range."""

    model_config = ConfigDict(extra="forbid")

    source_playable: bool = False
    start_ms: int = Field(default=0, ge=0)
    end_ms: int | None = Field(default=None, ge=1)
    fill_background: bool = True


class VideoPlaybackProxyStatus(BaseModel):
    """Path-free coverage for direct or segmented browser playback."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["video-playback-proxy-status-v2"] = (
        "video-playback-proxy-status-v2"
    )
    state: Literal["idle", "building", "partial", "ready", "failed"]
    mode: Literal["source", "segmented"]
    variant: Literal["source", "segments"]
    playable: bool
    profile: str
    revision: str
    duration_ms: int = Field(ge=0)
    segment_ms: int = Field(gt=0)
    requested_range: VideoPlaybackRange
    ready_ranges: list[VideoPlaybackRange] = Field(default_factory=list)
    active_segment: int | None = Field(default=None, ge=0)
    ready_segments: int = Field(ge=0)
    total_segments: int = Field(ge=0)
    progress: float = Field(ge=0, le=1)
    updated_at: str | None = None
    retryable: bool = False
    error: str | None = None
