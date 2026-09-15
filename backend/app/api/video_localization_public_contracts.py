from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.video_localization import (
    asr_pipeline,
    media_health,
    public_payload,
    speaker_diarization,
    timeline_clip_timing,
    timeline_clip_identity,
    timeline_edit_receipts,
    transcription,
)
from app.schemas.voice_studio import (
    Project,
    ProjectTranscriptionImportResponse,
    VideoLocalizationDraft,
    VideoLocalizationExport,
    VideoLocalizationOperation,
    VideoLocalizationOperationSummary,
    VideoLocalizationReferenceClip,
    VideoLocalizationTranscriptionState,
)
from app.schemas.video_localization_operation_feed import (
    VideoLocalizationOperationFeedV2,
)
from app.schemas.video_localization_mutation import (
    VideoLocalizationTimelineMutationResponse,
)
from app.schemas.video_localization_timeline_edit import (
    VideoLocalizationTimelineEditPatchRequest,
    VideoLocalizationTimelineEditPatchResponse,
)


def public_timeline_edit_receipt(
    updated: VideoLocalizationDraft,
    patch: VideoLocalizationTimelineEditPatchRequest,
    revision: str,
) -> VideoLocalizationTimelineEditPatchResponse:
    """Project partial audio acknowledgements and optional complete text tracks."""
    if updated._timeline_edit_response_receipt is not None:
        receipt = updated._timeline_edit_response_receipt
        return VideoLocalizationTimelineEditPatchResponse(
            updated_at=receipt.updated_at,
            revision=str(receipt.repository_revision),
            timeline_clips=list(receipt.timeline_clips),
            dub_lane_states=dict(receipt.dub_lane_states),
            cues=receipt.cues,
            localized_subtitles=receipt.localized_subtitles,
        )
    projection = timeline_edit_receipts.project_result(updated, patch)
    return VideoLocalizationTimelineEditPatchResponse(
        updated_at=updated.updated_at,
        revision=revision,
        **projection,
    )


def _as_payload(value: Any) -> Any:
    return public_payload.as_payload(value)


def _public_operation_payload(value: Any) -> dict[str, Any]:
    return public_payload.public_operation_payload(value)


def _public_draft_payload(value: Any) -> dict[str, Any]:
    payload = dict(_as_payload(value))
    payload["timeline_clips"] = [
        timeline_clip_identity.with_generation_identity(
            dict(_as_payload(clip))
        )
        for clip in payload.get("timeline_clips", [])
    ]
    payload = _without_project_media_locators(payload)
    payload = timeline_clip_timing.normalize_draft_payload(payload)
    for clip in payload.get("timeline_clips", []):
        clip.pop("timeline_edit_gate", None)
        clip.pop("cqc_report", None)
    payload["operations"] = [
        _public_operation_payload(operation)
        for operation in payload.get("operations", [])
    ]
    return payload


def _public_timeline_clips(value: Any) -> list[dict[str, Any]]:
    payload = timeline_clip_timing.normalize_draft_payload(
        {"timeline_clips": list(_as_payload(value) or [])}
    )
    public: list[dict[str, Any]] = []
    for raw_clip in payload.get("timeline_clips", []):
        clip = timeline_clip_identity.with_generation_identity(
            dict(_as_payload(raw_clip))
        )
        if str(clip.get("track_id") or "") in {
            "original",
            "vocals",
            "background",
        }:
            clip = dict(public_payload.without_internal_locators(clip))
        public.append(clip)
    return public


def _public_timeline_projection_clips(value: Any) -> list[dict[str, Any]]:
    """Keep playback/edit fields while omitting large audit evidence."""

    clips = _public_timeline_clips(value)
    for clip in clips:
        clip.pop("timeline_edit_gate", None)
        clip.pop("cqc_report", None)
    return clips


def _editable_draft_payload(
    value: Any,
    *,
    include_tts_tasks: bool = True,
) -> dict[str, Any]:
    """Return the canonical editable state without dedicated task/read-model copies."""

    if isinstance(value, VideoLocalizationDraft):
        dubbing_production = value.dubbing_production.model_copy(
            update={"candidate_inputs": []}
        )
        generated_candidates = [
            candidate.model_copy(update={"cqc_report": None})
            if isinstance(candidate, BaseModel)
            else {
                key: item
                for key, item in dict(_as_payload(candidate)).items()
                if key != "cqc_report"
            }
            for candidate in value.generated_candidates
        ]
        updates = {
            "operations": [],
            "dubbing_production": dubbing_production,
            "generated_candidates": generated_candidates,
        }
        if not include_tts_tasks:
            updates["tts_tasks"] = []
        value = value.model_copy(update=updates)
    elif isinstance(value, dict):
        value = dict(value)
        value["operations"] = []
        if not include_tts_tasks:
            value["tts_tasks"] = []
        raw_dubbing_production = dict(
            _as_payload(value.get("dubbing_production", {}))
        )
        raw_dubbing_production["candidate_inputs"] = []
        value["dubbing_production"] = raw_dubbing_production
        value["generated_candidates"] = [
            {
                key: item
                for key, item in dict(_as_payload(candidate)).items()
                if key != "cqc_report"
            }
            for candidate in value.get("generated_candidates", [])
        ]

    draft = _public_draft_payload(value)
    draft["timeline_clips"] = _public_timeline_projection_clips(
        draft.get("timeline_clips", [])
    )
    draft["operations"] = []
    if not include_tts_tasks:
        draft["tts_tasks"] = []
    dubbing_production = dict(
        _as_payload(draft.get("dubbing_production", {}))
    )
    dubbing_production["candidate_inputs"] = []
    draft["dubbing_production"] = dubbing_production
    candidates = []
    for raw_candidate in draft.get("generated_candidates", []):
        candidate = dict(_as_payload(raw_candidate))
        candidate.pop("cqc_report", None)
        candidates.append(candidate)
    draft["generated_candidates"] = candidates
    return draft


def _without_project_media_locators(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Project source/stem files stay internal; public clips keep identity."""

    public = dict(payload)
    source_media = dict(
        public_payload.without_internal_locators(
            public.get("source_media", {})
        )
    )
    source_media.update(
        {
            "video_path": None,
            "audio_path": None,
        }
    )
    public["source_media"] = source_media

    stems = dict(
        public_payload.without_internal_locators(
            public.get("stems", {})
        )
    )
    stems.update(
        {
            "original_audio_path": None,
            "vocals_clean_path": None,
            "background_path": None,
        }
    )
    public["stems"] = stems

    timeline_clips: list[Any] = []
    for raw_clip in public.get("timeline_clips", []):
        clip = dict(_as_payload(raw_clip))
        if str(clip.get("track_id") or "") in {
            "original",
            "vocals",
            "background",
        }:
            clip = dict(
                public_payload.without_internal_locators(clip)
            )
            media_source_clip_id = str(
                clip.get("media_source_clip_id")
                or clip.get("clip_id")
                or ""
            )
            if media_source_clip_id:
                clip["media_source_clip_id"] = media_source_clip_id
        timeline_clips.append(clip)
    public["timeline_clips"] = timeline_clips
    return public


def _without_audio_path(value: Any) -> dict[str, Any]:
    payload = dict(_as_payload(value))
    payload.pop("audio_path", None)
    return payload


class PublicVideoLocalizationOperation(VideoLocalizationOperation):
    """Operation response without internal filesystem locators."""

    @model_validator(mode="before")
    @classmethod
    def remove_internal_locators(cls, value: Any):
        return _public_operation_payload(value)


class PublicVideoLocalizationOperationSummary(VideoLocalizationOperationSummary):
    """Compact polling response without internal filesystem locators."""

    @model_validator(mode="before")
    @classmethod
    def remove_internal_locators(cls, value: Any):
        return _public_operation_payload(value)


class PublicVideoLocalizationOperationFeedV2(
    VideoLocalizationOperationFeedV2
):
    """Bounded operation feed without internal filesystem locators."""

    active_operations: list[
        PublicVideoLocalizationOperationSummary
    ]
    history: list[PublicVideoLocalizationOperationSummary]


class PublicVideoLocalizationDraft(VideoLocalizationDraft):
    """Draft response without source/stem locators or operation artifacts."""

    @model_validator(mode="before")
    @classmethod
    def remove_internal_operation_locators(cls, value: Any):
        return _public_draft_payload(value)


class PublicVideoLocalizationEditableDraft(VideoLocalizationDraft):
    """Editable save response without histories already owned by dedicated readers."""

    @model_validator(mode="before")
    @classmethod
    def omit_dedicated_task_histories(cls, value: Any):
        return _editable_draft_payload(value)


class PublicVideoLocalizationTimelineProjection(BaseModel):
    """Bounded live timeline state without the rest of the project document."""

    model_config = ConfigDict(extra="forbid")

    revision: str
    timeline_clips: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def remove_internal_media_locators(cls, value: Any):
        payload = dict(_as_payload(value))
        payload["timeline_clips"] = _public_timeline_projection_clips(
            payload.get("timeline_clips", [])
        )
        return payload


class PublicVideoLocalizationTimelineMutation(
    VideoLocalizationTimelineMutationResponse
):
    """Bounded timeline command result without audit evidence or locators."""

    @model_validator(mode="before")
    @classmethod
    def remove_heavy_timeline_evidence(cls, value: Any):
        payload = dict(_as_payload(value))
        payload["timeline_clips"] = _public_timeline_projection_clips(
            payload.get("timeline_clips", [])
        )
        return payload


class PublicVideoLocalizationCueTimingConfirmation(BaseModel):
    """Current timing-confirmation state without exposing ASR word evidence."""

    model_config = ConfigDict(extra="forbid")

    cue_id: str
    confirmation_current: bool


class PublicVideoLocalizationWorkspace(BaseModel):
    """Always-needed workbench state plus bounded picker projections."""

    model_config = ConfigDict(extra="forbid")

    revision: str = Field(
        description=(
            "Repository revision captured atomically with the editable workspace "
            "payload. Clients must not replace it with an older projection."
        )
    )
    draft: PublicVideoLocalizationDraft = Field(
        description=(
            "Editable project state. The operations and tts_tasks arrays are empty; "
            "clients load those server-owned histories from their dedicated readers. "
            "ASR evidence, references, generated candidates and the full production "
            "plan are omitted and loaded from workspace-details only when needed."
        )
    )
    media_health: media_health.ProjectMediaHealth
    cue_timing_confirmations: list[PublicVideoLocalizationCueTimingConfirmation] = Field(
        default_factory=list,
        description=(
            "Read-only current-state projection for each cue timing confirmation. "
            "It is calculated from server-held evidence without returning the "
            "transcription or its words."
        ),
    )
    semantic_tts_groups: list[dict[str, Any]] = Field(default_factory=list)
    omitted_sections: list[
        Literal[
            "transcription",
            "reference_clips",
            "generated_candidates",
            "dubbing_production",
        ]
    ] = Field(
        default_factory=lambda: [
            "transcription",
            "reference_clips",
            "generated_candidates",
            "dubbing_production",
        ]
    )

    @model_validator(mode="before")
    @classmethod
    def omit_dedicated_task_histories(cls, value: Any):
        payload = dict(_as_payload(value))
        payload["draft"] = _editable_draft_payload(
            payload.get("draft", {}),
            include_tts_tasks=False,
        )
        return payload


class PublicVideoLocalizationWorkspaceDetail(BaseModel):
    """One lazily loaded, server-owned workspace detail section."""

    model_config = ConfigDict(extra="forbid")

    section: Literal[
        "transcription",
        "reference_clips",
        "generated_candidates",
        "dubbing_production",
    ]
    transcription: VideoLocalizationTranscriptionState | None = None
    reference_clips: list[VideoLocalizationReferenceClip] | None = None
    generated_candidates: list[dict[str, Any]] | None = None
    dubbing_production: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def remove_internal_detail_locators(cls, value: Any):
        payload = dict(_as_payload(value))
        if isinstance(payload.get("reference_clips"), list):
            payload["reference_clips"] = [
                _without_audio_path(item)
                for item in payload["reference_clips"]
            ]
        if isinstance(payload.get("generated_candidates"), list):
            candidates = []
            for raw_candidate in payload["generated_candidates"]:
                candidate = _without_audio_path(raw_candidate)
                candidate.pop("cqc_report", None)
                candidates.append(candidate)
            payload["generated_candidates"] = candidates
        if isinstance(payload.get("dubbing_production"), dict):
            production = dict(payload["dubbing_production"])
            production["candidate_inputs"] = []
            payload["dubbing_production"] = production
        return payload


class PublicVideoLocalizationExport(VideoLocalizationExport):
    """Download contract without operation artifact locators."""

    @model_validator(mode="before")
    @classmethod
    def remove_internal_operation_locators(cls, value: Any):
        return _public_draft_payload(value)


class PublicTranscribeRawInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-raw-v2"] = "asr-raw-v2"
    audio_sha256: str
    engine_id: str
    source_track_id: str
    requested_language: str
    duration_ms: int | None = None
    context_terms: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def remove_audio_path(cls, value: Any):
        return _without_audio_path(value)


class PublicTranscribeRawOutput(transcription.TranscribeRawOutput):
    input: PublicTranscribeRawInput

    @model_validator(mode="before")
    @classmethod
    def read_internal_model(cls, value: Any):
        return _as_payload(value)


class PublicDiarizeSpeakersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["speaker-diarization-v1"] = "speaker-diarization-v1"
    audio_sha256: str = Field(min_length=1)
    source_track_id: str = Field(min_length=1)
    engine_id: str = Field(min_length=1)
    duration_ms: int | None = Field(default=None, ge=0)
    min_speakers: int | None = Field(
        default=None,
        ge=1,
        le=speaker_diarization.MAX_SPEAKER_COUNT_GUIDANCE,
    )
    max_speakers: int | None = Field(
        default=None,
        ge=1,
        le=speaker_diarization.MAX_SPEAKER_COUNT_GUIDANCE,
    )

    @model_validator(mode="before")
    @classmethod
    def remove_audio_path(cls, value: Any):
        return _without_audio_path(value)


class PublicDiarizeSpeakersOutput(speaker_diarization.DiarizeSpeakersOutput):
    input: PublicDiarizeSpeakersInput

    @model_validator(mode="before")
    @classmethod
    def read_internal_model(cls, value: Any):
        return _as_payload(value)


class PublicAsrInitialAnalysisResult(asr_pipeline.AsrInitialAnalysisResult):
    raw_asr: PublicTranscribeRawOutput
    diarization: PublicDiarizeSpeakersOutput | None = None

    @model_validator(mode="before")
    @classmethod
    def read_internal_model(cls, value: Any):
        return _as_payload(value)


class PublicAsrJoinedTranscript(asr_pipeline.AsrJoinedTranscript):
    raw_asr: PublicTranscribeRawOutput
    diarization: PublicDiarizeSpeakersOutput | None = None

    @model_validator(mode="before")
    @classmethod
    def read_internal_model(cls, value: Any):
        return _as_payload(value)


class PublicAsrInitialAnalysisSnapshot(asr_pipeline.AsrInitialAnalysisSnapshot):
    analysis: PublicAsrInitialAnalysisResult
    joined_transcript: PublicAsrJoinedTranscript

    @model_validator(mode="before")
    @classmethod
    def read_internal_model(cls, value: Any):
        return _as_payload(value)


class PublicProject(Project):
    """Project response with a sanitized embedded localization draft."""

    @model_validator(mode="before")
    @classmethod
    def remove_internal_operation_locators(cls, value: Any):
        payload = dict(_as_payload(value))
        parameters = dict(payload.get("parameters") or {})
        draft = parameters.get("video_localization")
        if isinstance(draft, (dict, VideoLocalizationDraft)):
            parameters["video_localization"] = _public_draft_payload(draft)
        payload["parameters"] = parameters
        return payload


class PublicProjectTranscriptionImportResponse(ProjectTranscriptionImportResponse):
    project: PublicProject


def public_video_localization_export(
    value: VideoLocalizationExport,
) -> PublicVideoLocalizationExport:
    return PublicVideoLocalizationExport.model_validate(value)
