from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


PREVIEW_CACHE_STATUS_CONTRACT_VERSION = "video-preview-cache-status-v1"


class PreviewCacheRange(BaseModel):
    start_ms: int
    end_ms: int
    status: Literal["empty", "rendering", "ready", "failed"]
    frame_count: int = 0
    sprite_rows: int = 0


class PreviewCacheStatus(BaseModel):
    """Stable internal/OpenAPI projection for timeline preview-frame cache state."""

    contract_version: Literal["video-preview-cache-status-v1"] = PREVIEW_CACHE_STATUS_CONTRACT_VERSION
    state: Literal["empty", "building", "partial", "ready", "failed"]
    phase: Literal["idle", "queued", "rendering", "failed", "cancelled"]
    active_chunk: int | None = None
    started_at: str | None = None
    updated_at: str | None = None
    retryable: bool = False
    profile: str
    revision: str
    mode: Literal["auto", "compact", "quality"]
    duration_ms: int
    chunk_ms: int
    frame_interval_ms: int
    frame_width: int
    frame_height: int
    sprite_columns: int
    sprite_rows: int
    ready_chunks: int
    total_chunks: int
    progress: float
    cached_bytes: int
    capacity_bytes: int
    ranges: list[PreviewCacheRange]
    error: str | None = None
