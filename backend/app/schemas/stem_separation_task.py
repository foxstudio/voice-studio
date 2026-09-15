from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.voice_studio import TaskStatus, new_id, now_iso


class StemSeparationTask(BaseModel):
    task_id: str = Field(default_factory=new_id)
    filename: str
    status: TaskStatus = TaskStatus.queued
    engine_id: str
    size_bytes: int = 0
    duration_ms: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    vocals_ready: bool = False
    background_ready: bool = False
    error_message: str | None = None
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class StemSeparationDeleteResult(BaseModel):
    task_id: str
    status: Literal["deleted"]


class StemSeparationCancelResult(BaseModel):
    task_id: str
    status: TaskStatus
