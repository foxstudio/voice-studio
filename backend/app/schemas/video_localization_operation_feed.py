from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.voice_studio import VideoLocalizationOperationSummary


class VideoLocalizationOperationFeedV2(BaseModel):
    """Bounded operation polling head or terminal-history page."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "operation-feed-v2"
    ] = "operation-feed-v2"
    revision: int = Field(ge=0)
    history_revision: int = Field(ge=0)
    changed: bool
    active_operations: list[
        VideoLocalizationOperationSummary
    ] = Field(default_factory=list, max_length=32)
    history: list[
        VideoLocalizationOperationSummary
    ] = Field(default_factory=list, max_length=100)
    history_total: int = Field(default=0, ge=0)
    next_cursor: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "VideoLocalizationOperationFeedV2":
        if not self.changed and (
            self.active_operations
            or self.history
            or self.history_total
            or self.next_cursor is not None
        ):
            raise ValueError(
                "unchanged operation feeds must not repeat summaries"
            )
        if len(self.history) > self.history_total:
            raise ValueError(
                "operation history page exceeds its total count"
            )
        return self


__all__ = ["VideoLocalizationOperationFeedV2"]
