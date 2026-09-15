from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


TTS_HANDOFF_EVENT_SCHEMA_VERSION = "video-localization-tts-handoff-event-v1"


class TtsHandoffClaim(BaseModel):
    """Opaque capability held by one outbox delivery runner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    runner_id: str = Field(min_length=1)
    fencing_token: int = Field(ge=1)
    lease_expires_at_ms: int = Field(ge=1)


class _TtsHandoffEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["video-localization-tts-handoff-event-v1"] = TTS_HANDOFF_EVENT_SCHEMA_VERSION
    project_id: str = Field(min_length=1)


class TtsTaskRegistrationEventV1(_TtsHandoffEventBase):
    event_kind: Literal["task_registration"] = "task_registration"
    source_kind: Literal["task", "longform_task"]
    source_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    generation_task_id: str = Field(min_length=1)
    workflow_id: str | None = None


class TtsResultPlacementEventV1(_TtsHandoffEventBase):
    event_kind: Literal["result_placement"] = "result_placement"
    task_id: str = Field(min_length=1)
    generation_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    localized_subtitle_id: str | None = None
    cue_id: str | None = None
    timeline_clip_id: str | None = None
    dubbing_plan_revision: int | None = Field(default=None, ge=1)
    dubbing_group_id: str | None = None
    dubbing_target_subtitle_ids: list[str] = Field(default_factory=list)


class TtsWorkflowTerminalEventV1(_TtsHandoffEventBase):
    event_kind: Literal["workflow_terminal"] = "workflow_terminal"
    source_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    terminal_status: Literal["failed", "cancelled"]
    error_message: str | None = None


TtsHandoffEventV1 = Annotated[
    TtsTaskRegistrationEventV1 | TtsResultPlacementEventV1 | TtsWorkflowTerminalEventV1,
    Field(discriminator="event_kind"),
]
TTS_HANDOFF_EVENT_ADAPTER = TypeAdapter(TtsHandoffEventV1)


__all__ = [
    "TTS_HANDOFF_EVENT_ADAPTER",
    "TTS_HANDOFF_EVENT_SCHEMA_VERSION",
    "TtsHandoffClaim",
    "TtsHandoffEventV1",
    "TtsResultPlacementEventV1",
    "TtsTaskRegistrationEventV1",
    "TtsWorkflowTerminalEventV1",
]
