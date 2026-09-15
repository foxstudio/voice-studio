from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.voice_studio import VideoLocalizationCue, VideoLocalizationSubtitleCue


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VideoLocalizationTimelineClipExpectedEditableFields(_StrictContract):
    """Client-observed clip state used to reject stale same-media edits."""

    start_ms: int | None = Field(ge=0)
    end_ms: int | None = Field(ge=0)
    source_start_ms: int | None = Field(ge=0)
    source_end_ms: int | None = Field(ge=0)
    media_source_clip_id: str | None
    dub_lane: int | None = Field(ge=0)


class VideoLocalizationTimelineClipEditPatch(_StrictContract):
    clip_id: str = Field(min_length=1)
    expected_generation_identity: str = Field(min_length=1)
    expected_editable_fields: VideoLocalizationTimelineClipExpectedEditableFields
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    source_start_ms: int | None = Field(default=None, ge=0)
    source_end_ms: int | None = Field(default=None, ge=0)
    media_source_clip_id: str | None = None
    dub_lane: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_explicit_timing(self):
        if any(getattr(self, field) is None for field in self.model_fields_set.intersection({
            "start_ms", "end_ms", "source_start_ms", "source_end_ms",
        })):
            raise ValueError("explicit clip timing cannot be null")
        return self


class VideoLocalizationTimelineClipAdd(_StrictContract):
    """Create one editable timeline slice from an existing playable source."""

    clip_id: str = Field(min_length=1)
    media_source_clip_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=1)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(ge=1)
    dub_lane: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.end_ms <= self.start_ms or self.source_end_ms <= self.source_start_ms:
            raise ValueError("timeline and source ranges must have positive duration")
        return self


class VideoLocalizationTimelineClipDelete(_StrictContract):
    clip_id: str = Field(min_length=1)
    expected_generation_identity: str = Field(min_length=1)
    expected_editable_fields: VideoLocalizationTimelineClipExpectedEditableFields


class VideoLocalizationDubLaneStateEditPatch(_StrictContract):
    lane: int = Field(ge=0)
    muted: bool | None = None
    solo: bool | None = None
    volume: float | None = Field(default=None, ge=0, le=4)
    label: str | None = Field(default=None, max_length=120)
    locked: bool | None = None
    remove: bool = False


class VideoLocalizationTimelineUiStatePatch(_StrictContract):
    """Timeline-owned state that must commit with the clip edit."""

    disabled_media_tracks: list[str] | None = Field(default=None, max_length=20)
    discarded_tts_task_ids: list[str] | None = Field(default=None, max_length=5000)


class VideoLocalizationCueCollectionChange(_StrictContract):
    expected: list[VideoLocalizationCue] = Field(max_length=10000)
    desired: list[VideoLocalizationCue] = Field(max_length=10000)

    @model_validator(mode="before")
    @classmethod
    def discard_client_confirmation_provenance(cls, value):
        if not isinstance(value, dict):
            return value
        cleaned = dict(value)
        for field in ("expected", "desired"):
            if isinstance(cleaned.get(field), list):
                cleaned[field] = [
                    {key: item for key, item in row.items() if not key.startswith("manual_timing_")}
                    if isinstance(row, dict) else row for row in cleaned[field]
                ]
        return cleaned


class VideoLocalizationSubtitleCollectionChange(_StrictContract):
    expected: list[VideoLocalizationSubtitleCue] = Field(max_length=10000)
    desired: list[VideoLocalizationSubtitleCue] = Field(max_length=10000)


class VideoLocalizationTimelineEditPatchRequest(_StrictContract):
    schema_version: Literal["timeline-edit-patch-v2"] = "timeline-edit-patch-v2"
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    clip_patches: list[VideoLocalizationTimelineClipEditPatch] = Field(
        default_factory=list,
        max_length=500,
    )
    added_clips: list[VideoLocalizationTimelineClipAdd] = Field(
        default_factory=list,
        max_length=500,
    )
    dub_lane_state_patches: list[VideoLocalizationDubLaneStateEditPatch] = Field(
        default_factory=list,
        max_length=100,
    )
    deleted_clips: list[VideoLocalizationTimelineClipDelete] = Field(
        default_factory=list,
        max_length=500,
    )
    ui_state_patch: VideoLocalizationTimelineUiStatePatch | None = None
    cue_collection_change: VideoLocalizationCueCollectionChange | None = None
    localized_subtitle_collection_change: VideoLocalizationSubtitleCollectionChange | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if (
            not self.clip_patches
            and not self.added_clips
            and not self.dub_lane_state_patches
            and not self.deleted_clips
            and self.ui_state_patch is None
            and self.cue_collection_change is None
            and self.localized_subtitle_collection_change is None
        ):
            raise ValueError("timeline edit patch must contain at least one change")
        clip_ids = [item.clip_id for item in self.clip_patches]
        if len(clip_ids) != len(set(clip_ids)):
            raise ValueError("timeline edit patch contains duplicate clip ids")
        added_ids = [item.clip_id for item in self.added_clips]
        if len(added_ids) != len(set(added_ids)):
            raise ValueError("timeline edit patch contains duplicate added clip ids")
        lanes = [item.lane for item in self.dub_lane_state_patches]
        if len(lanes) != len(set(lanes)):
            raise ValueError("timeline edit patch contains duplicate lanes")
        deleted_ids = [item.clip_id for item in self.deleted_clips]
        if len(deleted_ids) != len(set(deleted_ids)):
            raise ValueError("timeline edit patch contains duplicate deleted clip ids")
        if set(clip_ids) & set(deleted_ids):
            raise ValueError("timeline edit patch cannot update and delete the same clip")
        if set(added_ids) & (set(clip_ids) | set(deleted_ids)):
            raise ValueError("timeline edit patch cannot add and modify/delete the same clip")
        for change, key in (
            (self.cue_collection_change, "cue_id"),
            (self.localized_subtitle_collection_change, "subtitle_id"),
        ):
            if change is None:
                continue
            for collection in (change.expected, change.desired):
                ids = [getattr(item, key) for item in collection]
                if any(not item.strip() for item in ids) or len(ids) != len(set(ids)):
                    raise ValueError("timeline collection must contain unique nonempty ids")
        return self


class VideoLocalizationTimelineEditPatchResponse(_StrictContract):
    schema_version: Literal["timeline-edit-patch-v2"] = "timeline-edit-patch-v2"
    updated_at: str | None = None
    revision: str = Field(description=(
        "Repository revision of the original committed timeline edit; an idempotent replay returns the same revision. "
        "Use as a freshness hint, not as a consumed workspace/timeline snapshot revision."
    ))
    timeline_clips: list[dict[str, Any]] = Field(default_factory=list)
    dub_lane_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    cues: list[VideoLocalizationCue] | None = None
    localized_subtitles: list[VideoLocalizationSubtitleCue] | None = None
