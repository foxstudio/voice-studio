from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, ContextManager, Literal

from fastapi import UploadFile
from pydantic import ValidationError

from app.domains.video_localization import audio_access
from app.domains.video_localization import asr_source_repair
from app.schemas.video_localization_asr_repair import AsrSourceRepairRequest
from app.schemas.video_localization_binding_repair import BindingRepairRequest, BindingRepairReceipt
from app.domains.video_localization import binding_repair
from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization import development_checkpoints
from app.domains.video_localization.development_llm_batches import DevelopmentLlmBatchReplay
from app.domains.video_localization.development_candidate_recovery import DevelopmentCandidateReceiptStore
from app.domains.video_localization import draft_store
from app.domains.video_localization import dubbing_media
from app.domains.video_localization import dubbing_generation_identity
from app.domains.video_localization import dub_subtitle_workflow
from app.domains.video_localization import dub_subtitles
from app.domains.video_localization import document_understanding_contracts
from app.domains.video_localization import exporting
from app.domains.video_localization import llm_observability
from app.domains.video_localization import localization_context_intent
from app.domains.video_localization import localization_creation_context
from app.domains.video_localization import localization_document_brief
from app.domains.video_localization import localization_document_evidence
from app.domains.video_localization import localization_display_adjudication
from app.domains.video_localization import localization_formal_retry
from app.domains.video_localization import localization_generation_chunks
from app.domains.video_localization import localization_requirements
from app.domains.video_localization import workflow_submission
from app.domains.video_localization import localization_spoken_script
from app.domains.video_localization import localization_semantic_alignment
from app.domains.video_localization import localization_alignment_adjudication
from app.domains.video_localization import localization_dual_tracks
from app.domains.video_localization import localization_tracks
from app.domains.video_localization import localization_source
from app.domains.video_localization import source_boundary_evidence
from app.domains.video_localization import localization_workflow_execution
from app.domains.video_localization import workflow_ledger
from app.domains.video_localization import workflow_graph
from app.domains.video_localization import media_assets
from app.domains.video_localization import media_health
from app.domains.video_localization import operation_state
from app.domains.video_localization import timeline_edit_receipts
from app.domains.video_localization import project_manifest
from app.domains.video_localization import project_lifecycle_cleanup
from app.domains.video_localization import project_snapshot_projection
from app.domains.video_localization import playback_proxy, preview_cache
from app.domains.video_localization import preview_media_contracts
from app.domains.video_localization import quality_gate
from app.domains.video_localization import reference_clips
from app.domains.video_localization import semantic_tts_grouping
from app.domains.video_localization import semantic_tts_grouping_execution
from app.domains.video_localization import speakers
from app.domains.video_localization import (
    asr_pipeline,
    entity_normalization,
    research_evidence,
    review_decisions,
    section_review,
    transcript_quality_gate,
    whole_recheck,
    source_pipeline,
    visual_evidence,
)
from app.domains.video_localization import timeline_clip_identity
from app.domains.video_localization import state_ownership
from app.domains.video_localization import subtitles
from app.domains.video_localization import timeline_clip_timing
from app.domains.video_localization import subtitle_mutations
from app.domains.video_localization import transcription
from app.domains.video_localization import tts_orchestration
from app.domains.video_localization import tts_history
from app.domains.video_localization import tts_parameter_pack
from app.domains.video_localization import tts_pipeline
from app.domains.video_localization import tts_placement
from app.domains.video_localization import workflow_contracts
from app.domains.video_localization import workspace_media_projection
from app.domains.video_localization.tts_selection import TtsSelectionRequest, build_selection_snapshot
from app.errors import AppException, ProjectRevisionConflict
from app.schemas.video_localization_tts_handoff import TtsHandoffClaim
from app.services import text_normalizer
from app.schemas.voice_studio import (
    GenerateRequest,
    LicenseStatus,
    Project,
    VideoLocalizationTtsTask,
    VideoLocalizationTtsTaskFeed,
    VideoLocalizationTtsTaskStage,
    VoiceSource,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationAsrVadTimingCorrectionRequest,
    VideoLocalizationCue,
    VideoLocalizationCueUpdate,
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationSpeakerCreate,
    VideoLocalizationSpeakerUpdate,
    VideoLocalizationSpokenSegmentUpdate,
    VideoLocalizationSubtitleCue,
    VideoLocalizationSubtitleCueUpdate,
    VideoLocalizationSubtitleImportRequest,
    VideoLocalizationTranscriptionState,
    now_iso,
)
from app.services import (
    audio_tools,
    batch_queue,
    custom_reference_store,
    history_store,
    llm_runtime,
    project_store,
    reference_audio_integrity,
    settings_store,
    task_queue,
    video_localization_operation_ledger_store,
    video_localization_tts_workflow_store,
    voice_store,
    waveform_cache,
    web_search,
)

from app.services import localization_ai_policy
from app.services.keyed_lock_registry import KeyedLockRegistry
from app.services.semantic_alignment_labse import LabseTextEncoder
from app.services.video_localization_execution_fence import ExecutionFence

VIDEO_LOCALIZATION_KEY = draft_store.VIDEO_LOCALIZATION_KEY
_DRAFT_WRITE_LOCK = draft_store.DRAFT_WRITE_LOCK
_TTS_PREPARED_SUBMISSION_TIMEOUT_SECONDS = 120
_PROJECT_WRITE_MAX_ATTEMPTS = 3
_TIMELINE_AUDIO_INDEX_LOCKS = KeyedLockRegistry[str]()
_PROJECT_MEDIA_INDEX_LOCKS = KeyedLockRegistry[str]()
def _no_tts_closeout_owner(_workflow_id: str) -> bool:
    return False


_TTS_CLOSEOUT_OWNER_LOOKUP: Callable[[str], bool] = _no_tts_closeout_owner
_BACKEND_OWNED_TIMELINE_CLIP_FIELDS = (
    "audio_path",
    "status",
    "candidate_id",
    "result_id",
    "generation_id",
    "task_id",
    "alignment_lead_ms",
    "speech_onset_ms",
    "cue_id",
    "subtitle_id",
    "source_cue_ids",
    "manual_history_copy",
    "cqc_status",
    "cqc_report_version",
    "cqc_report",
    "timeline_edit_gate",
    "timeline_timing_version",
)
# A browser workspace snapshot is not a timeline-edit command.  Keep these
# fields server-authoritative after initial draft creation; the bounded
# timeline-edit endpoint owns their subsequent mutations.
_FULL_SNAPSHOT_PRESERVED_TIMELINE_CLIP_FIELDS = (
    "track_id",
    "start_ms",
    "end_ms",
    "source_start_ms",
    "source_end_ms",
    "media_source_clip_id",
)
_BACKEND_OWNED_GENERATED_CANDIDATE_FIELDS = (
    "cqc_status",
    "cqc_report_version",
    "cqc_report",
    "accepted",
)
_BACKEND_OWNED_CUE_MEDIA_FIELDS = ("tts_audio_path",)
_BACKEND_OWNED_SUBTITLE_MEDIA_FIELDS = ("tts_audio_path",)
_TTS_HANDOFF_AUTHORITATIVE_FIELDS = {
    "text",
    "source",
    "project_id",
    "segment_id",
    "localized_subtitle_id",
    "cue_id",
    "timeline_clip_id",
    "generation_id",
    "bind_to_video_localization",
    "video_localization_workflow_id",
    "video_localization_submission_id",
    "video_localization_start_ms",
    "video_localization_end_ms",
    "video_localization_target_subtitle_ids",
    "video_localization_source_cue_ids",
    "video_localization_dubbing_plan_revision",
    "video_localization_dubbing_group_id",
    "video_localization_execution_scope",
    "video_localization_recovery",
    "video_localization_generation_attempt",
    "video_localization_dubbing_review_mode",
    "video_localization_execution_start_group_id",
    "video_localization_execution_end_group_id",
    "video_localization_max_in_flight_groups",
    "video_localization_ordinary_speed_baseline",
    "input_mode",
    "input_assets",
    "voice_id",
    "voice_source",
    "reference_audio_path",
    "reference_audio_license_status",
    "reference_audio_tags",
    "ref_text",
    "custom_reference_source_audio_path",
    "custom_reference_source_duration_ms",
    "custom_reference_trim_start_ms",
    "custom_reference_trim_end_ms",
    "emotion_reference_voice_id",
    "emotion_reference_audio_path",
    "emotion_reference_source_audio_path",
    "emotion_reference_source_duration_ms",
    "emotion_reference_trim_start_ms",
    "emotion_reference_trim_end_ms",
    "language",
    "idempotency_marker",
}
_TTS_HANDOFF_SUBMISSION_REFERENCE_FIELDS = {
    "reference_audio_path",
    "ref_text",
    "custom_reference_source_audio_path",
    "custom_reference_source_duration_ms",
    "custom_reference_trim_start_ms",
    "custom_reference_trim_end_ms",
}


def get_video_localization(project_id: str) -> VideoLocalizationDraft | None:
    draft = draft_store.get(project_id)
    if draft is not None:
        resolved_media = media_health.inspect_project_media(draft).paths
        media_assets.cache_project_timeline_audio_paths(
            project_id,
            draft,
            resolved_media={
                "source_audio": resolved_media.source_audio,
                "vocals": resolved_media.vocals,
                "background": resolved_media.background,
            },
        )
    return draft


# The application executor receives this domain rule through its projection;
# it must not import the video-localization domain directly.
frozen_group_request = dubbing_generation_identity.frozen_group_request
compatible_generated_identities = (
    dubbing_generation_identity.compatible_generated_identities
)
DUBBING_GENERATION_RUNTIME_FIELDS = dubbing_generation_identity.RUNTIME_FIELDS


def repair_video_localization_storage(
    project_id: str,
) -> VideoLocalizationDraft | None:
    """Run legacy storage migration and snapshot recovery as an explicit command."""

    with _DRAFT_WRITE_LOCK:
        return draft_store.repair(project_id)


def get_video_localization_workspace(project_id: str) -> dict[str, Any] | None:
    """Return the always-needed draft plus bounded workbench projections."""

    project = project_store.get_project_catalog_project(project_id)
    if not project:
        return None
    # The workspace owns the media inspection below.  Calling the general
    # reader here used to inspect every large media asset twice on each full
    # workspace refresh.
    exists, workspace_revision, workspace_payload = (
        project_store.get_video_localization_workspace_payload(project_id)
    )
    if not exists:
        return None
    if workspace_payload is not None:
        project.parameters[VIDEO_LOCALIZATION_KEY] = workspace_payload
    draft = draft_store.from_project(project)
    _, semantic_tts_groups = (
        project_store.get_video_localization_semantic_group_summaries(project_id)
    )
    package_root = _indexed_project_root(project)
    resolution = media_health.inspect_project_media(
        draft,
        package_root=package_root,
    )
    media_assets.cache_project_timeline_audio_paths(
        project_id,
        draft,
        resolved_media={
            "source_audio": resolution.paths.source_audio,
            "vocals": resolution.paths.vocals,
            "background": resolution.paths.background,
        },
    )
    cue_timing_confirmations = _workspace_cue_timing_confirmations(
        project_id,
        draft,
    )
    return {
        "revision": workspace_revision or "",
        "draft": workspace_media_projection.with_available_system_media_tracks(
            draft,
            resolution.health,
        ),
        "media_health": resolution.health,
        "cue_timing_confirmations": cue_timing_confirmations,
        "semantic_tts_groups": semantic_tts_groups,
        "omitted_sections": [
            "transcription",
            "reference_clips",
            "generated_candidates",
            "dubbing_production",
        ],
    }


def _workspace_cue_timing_confirmations(
    project_id: str,
    workspace_draft: VideoLocalizationDraft,
) -> list[dict[str, object]]:
    """Project cue confirmation freshness without returning the ASR detail."""

    needs_transcription = any(
        cue.manual_timing_confirmation_method == "asr_vad_verified"
        and cue.manual_timing_confirmation_evidence is not None
        for cue in workspace_draft.cues
    )
    draft_for_validation = workspace_draft
    if needs_transcription:
        _, transcription_payload = (
            project_store.get_video_localization_workspace_detail_payload(
                project_id,
                "transcription",
            )
        )
        try:
            transcription_state = (
                VideoLocalizationTranscriptionState.model_validate(
                    transcription_payload
                )
                if isinstance(transcription_payload, dict)
                else None
            )
        except ValidationError:
            transcription_state = None
        draft_for_validation = workspace_draft.model_copy(
            update={"transcription": transcription_state}
        )
    return [
        {
            "cue_id": cue.cue_id,
            "confirmation_current": cue_tools.timing_confirmation_is_current_for_draft(
                draft_for_validation,
                cue,
            ),
        }
        for cue in workspace_draft.cues
    ]


def get_video_localization_workspace_detail(
    project_id: str,
    section: str,
) -> dict[str, Any] | None:
    """Return one server-owned detail without reading the full project model."""

    exists, detail = project_store.get_video_localization_workspace_detail_payload(
        project_id,
        section,
    )
    if not exists:
        return None
    return {
        "section": section,
        "transcription": detail if section == "transcription" else None,
        "reference_clips": detail if section == "reference_clips" else None,
        "generated_candidates": detail if section == "generated_candidates" else None,
        "dubbing_production": detail if section == "dubbing_production" else None,
    }


def get_video_localization_timeline_projection(
    project_id: str,
) -> dict[str, Any] | None:
    """Return the bounded live timeline projection used by the workbench."""

    exists, revision, timeline_clips = (
        project_store.get_video_localization_timeline_projection(project_id)
    )
    if not exists:
        return None
    if timeline_clips is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_TIMELINE_PROJECTION_INVALID",
            "项目时间线数据需要修复，无法读取实时片段。",
        )
    return {
        "revision": revision or "",
        "timeline_clips": timeline_clips,
    }


def list_indexed_video_localization_project_summaries():
    """Project-menu summaries with current media/package health, without reconcile."""

    summaries = []
    for project in project_store.list_project_catalog_projects():
        if project_store.project_kind(project) != "video_localization":
            continue
        raw = project.parameters.get(VIDEO_LOCALIZATION_KEY)
        try:
            draft = raw if isinstance(raw, VideoLocalizationDraft) else VideoLocalizationDraft.model_validate(raw)
        except (TypeError, ValidationError):
            draft = VideoLocalizationDraft()
        health = media_health.inspect_project_media(
            draft,
            package_root=_indexed_project_root(project),
        ).health
        summaries.append(
            project_store.summarize_project(
                project,
                kind="video_localization",
                has_local_package=health.package_status == "available",
                source_media_status=health.source_video.status,
                package_status=health.package_status,
            )
        )
    return project_store.sort_project_summaries(summaries)


def _timeline_audio_index_lock(project_id: str) -> ContextManager[None]:
    return _TIMELINE_AUDIO_INDEX_LOCKS.hold(project_id)


def _project_media_index_lock(project_id: str) -> ContextManager[None]:
    return _PROJECT_MEDIA_INDEX_LOCKS.hold(project_id)


def sync_local_projects() -> list[Project]:
    """Reconcile the localization project menu with valid project packages on disk."""
    synced: list[Project] = []
    for package in project_manifest.discover_project_packages():
        project_id = package["project_id"]
        if project_lifecycle_cleanup.has_pending_delete(project_id):
            continue
        project = project_store.get_project(project_id)
        if project is None:
            # A package with absolute dependencies is valid for its existing local
            # database project, but must not be imported as an unknown project.
            if package.get("external_paths"):
                continue
            project = Project(
                project_id=project_id,
                name=package["project_name"],
                description="本地视频本土化项目",
                parameters={
                    media_assets.PROJECT_DIR_NAME_KEY: package["directory_name"],
                    VIDEO_LOCALIZATION_KEY: package["draft"].model_dump(mode="json"),
                },
                created_at=package["saved_at"],
                updated_at=package["saved_at"],
            )
            synced.append(project_store.save_project(project, touch_updated_at=False))
            continue

        next_parameters = dict(project.parameters)
        previous_directory_name = str(next_parameters.get(media_assets.PROJECT_DIR_NAME_KEY) or "").strip()
        changed = previous_directory_name != package["directory_name"]
        if changed and previous_directory_name:
            raw_draft = next_parameters.get(VIDEO_LOCALIZATION_KEY)
            if isinstance(raw_draft, dict):
                projects_root = media_assets.projects_root_dir()
                rebased = media_assets.rebase_paths_between_project_roots(
                    raw_draft,
                    previous_root=projects_root / previous_directory_name,
                    current_root=projects_root / package["directory_name"],
                )
                if rebased != raw_draft:
                    next_parameters[VIDEO_LOCALIZATION_KEY] = rebased
        next_parameters[media_assets.PROJECT_DIR_NAME_KEY] = package["directory_name"]
        if not isinstance(next_parameters.get(VIDEO_LOCALIZATION_KEY), dict):
            next_parameters[VIDEO_LOCALIZATION_KEY] = package["draft"].model_dump(mode="json")
            changed = True
        if changed:
            project.parameters = next_parameters
            project = project_store.save_project(project, touch_updated_at=False)
            media_assets.invalidate_project_directory_name(project_id)
            media_assets.invalidate_project_media_paths(project_id)
            media_assets.invalidate_project_timeline_audio_paths(project_id)
        synced.append(project)
    return synced


def sync_local_project_summaries():
    """Reconcile discoverable packages, then return every indexed project.

    A package that is temporarily unavailable is degraded catalog state, not an
    implicit delete command.  Keeping the indexed summary lets an active Web
    session retain unsaved work while reporting the package as missing.
    """
    sync_local_projects()
    return list_indexed_video_localization_project_summaries()


def _video_localization_project_summary(
    project: Project,
    *,
    package_status: media_health.ProjectPackageStatus | None = None,
):
    raw = project.parameters.get(VIDEO_LOCALIZATION_KEY)
    try:
        draft = raw if isinstance(raw, VideoLocalizationDraft) else VideoLocalizationDraft.model_validate(raw)
    except (TypeError, ValidationError):
        draft = VideoLocalizationDraft()
    health = media_health.inspect_project_media(
        draft,
        package_root=_indexed_project_root(project),
        package_status=package_status,
    ).health
    return project_store.summarize_project(
        project,
        kind="video_localization",
        has_local_package=health.package_status == "available",
        source_media_status=health.source_video.status,
        package_status=health.package_status,
    )


def _indexed_project_root(project: Project) -> Path:
    directory_name = str(project.parameters.get(media_assets.PROJECT_DIR_NAME_KEY) or "").strip()
    if not directory_name:
        directory_name = media_assets.project_dir_name(
            project.project_id,
            project.name,
        )
    return media_assets.projects_root_dir() / directory_name


def save_video_localization(
    project_id: str,
    draft: VideoLocalizationDraft,
    *,
    execution_fence: ExecutionFence | None = None,
    observed_at_ms: int | None = None,
    operation_command: (video_localization_operation_ledger_store.OperationCommand | None) = None,
) -> VideoLocalizationDraft | None:
    with _DRAFT_WRITE_LOCK:
        current = draft_store.get(project_id)
        if current and current.updated_at and draft.updated_at and current.updated_at != draft.updated_at:
            raise AppException(
                409, "VIDEO_LOCALIZATION_DRAFT_CONFLICT", "Project changed while this draft was being edited"
            )
        if current is not None and draft._repository_revision is None:
            draft = draft.model_copy()
            draft._repository_revision = current._repository_revision
        try:
            return draft_store.save(
                project_id,
                draft,
                intent="content",
                execution_fence=execution_fence,
                observed_at_ms=observed_at_ms,
                operation_command=operation_command,
            )
        except ProjectRevisionConflict as exc:
            if operation_command is not None:
                raise
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DRAFT_CONFLICT",
                "Project changed while this draft was being edited",
            ) from exc


def replace_video_localization_from_client(
    project_id: str,
    draft: VideoLocalizationDraft,
    *,
    preserve_existing_timeline_fields: bool = False,
) -> VideoLocalizationDraft | None:
    """Replace an editable draft without trusting backend-owned audit fields."""
    with _DRAFT_WRITE_LOCK:
        current = draft_store.get(project_id)
        if current and current.updated_at and current.updated_at != draft.updated_at:
            raise AppException(
                409, "VIDEO_LOCALIZATION_DRAFT_CONFLICT", "Project changed while this draft was being edited"
            )
        sanitized = cue_tools.sanitize_client_draft_timing_provenance(current, draft)
        sanitized = _without_client_dubbing_production_authority(sanitized)
        if current is not None:
            sanitized = _preserve_backend_owned_draft_state(
                current,
                sanitized,
                project_id=project_id,
                preserve_existing_timeline_fields=preserve_existing_timeline_fields,
            )
        sanitized = timeline_clip_timing.normalize_draft(sanitized)
        if current is not None and (
            sanitized.localized_subtitles != current.localized_subtitles
            or sanitized.localized_spoken_segments
            != current.localized_spoken_segments
        ):
            sanitized = localization_tracks.invalidate_formal_quality_binding(
                sanitized
            )
        try:
            return draft_store.save(
                project_id,
                sanitized,
                intent="content",
            )
        except ProjectRevisionConflict as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DRAFT_CONFLICT",
                "Project changed while this draft was being edited",
            ) from exc


def replace_video_localization_workspace_from_client(
    project_id: str,
    draft: VideoLocalizationDraft,
) -> VideoLocalizationDraft | None:
    """Save workspace-owned fields without overwriting command-owned content."""

    with _DRAFT_WRITE_LOCK:
        current = draft_store.get(project_id)
        if current is None:
            return replace_video_localization_from_client(project_id, draft)
        workspace_owned = draft.model_copy(
            update={
                # Cue/subtitle/spoken-track edits have typed commands of their
                # own. A browser workspace snapshot can lag those commands
                # while TTS is completing, so it must never make that older
                # content authoritative again.
                "cues": current.cues,
                "localized_subtitles": current.localized_subtitles,
                "localized_spoken_segments": current.localized_spoken_segments,
                "quality_gate": current.quality_gate,
                "transcription": current.transcription,
                "reference_clips": current.reference_clips,
                "generated_candidates": current.generated_candidates,
                "dubbing_production": current.dubbing_production,
            }
        )
        return replace_video_localization_from_client(
            project_id,
            workspace_owned,
            preserve_existing_timeline_fields=True,
        )


def _preserve_backend_owned_draft_state(
    current: VideoLocalizationDraft,
    incoming: VideoLocalizationDraft,
    *,
    project_id: str | None = None,
    preserve_existing_timeline_fields: bool = False,
) -> VideoLocalizationDraft:
    incoming = incoming.model_copy()
    incoming._repository_revision = current._repository_revision
    is_uninitialized = current.updated_at is None and not current.operations and not current.localization_state
    current_latest_tts_tasks = current.ui_state.get("latest_tts_task_by_segment", {})
    current_discarded_tts_task_ids = current.ui_state.get("discarded_tts_task_ids", [])
    incoming_discarded_tts_task_ids = incoming.ui_state.get("discarded_tts_task_ids", [])
    discarded_tts_task_ids = {
        str(value)
        for value in [
            *(current_discarded_tts_task_ids if isinstance(current_discarded_tts_task_ids, list) else []),
            *(incoming_discarded_tts_task_ids if isinstance(incoming_discarded_tts_task_ids, list) else []),
        ]
        if isinstance(value, str) and value
    }
    client_timeline_edit_intent = incoming.ui_state.get("client_timeline_edit_intent", {})
    allowed_dub_lane_clip_ids = {
        str(value)
        for value in (
            client_timeline_edit_intent.get("dub_lane_clip_ids", [])
            if isinstance(client_timeline_edit_intent, dict)
            else []
        )
        if isinstance(value, str) and value
    }
    explicitly_deleted_clips = {
        str(value.get("clip_id") or ""): str(
            value.get("expected_generation_identity") or ""
        )
        for value in (
            client_timeline_edit_intent.get("deleted_timeline_clips", [])
            if isinstance(client_timeline_edit_intent, dict)
            else []
        )
        if isinstance(value, dict)
        and str(value.get("clip_id") or "")
        and str(value.get("expected_generation_identity") or "")
    }
    explicitly_added_clip_ids = {
        str(value)
        for value in (
            client_timeline_edit_intent.get("added_timeline_clip_ids", [])
            if isinstance(client_timeline_edit_intent, dict)
            else []
        )
        if isinstance(value, str) and value
    }
    sanitized_incoming_ui_state = state_ownership.client_draft_ui_state(incoming.ui_state)
    current_reference_clips = {item.reference_clip_id: item for item in current.reference_clips}
    sanitized_reference_clips = []
    for item in incoming.reference_clips:
        current_item = current_reference_clips.get(item.reference_clip_id)
        sanitized_reference_clips.append(
            item.model_copy(update={"audio_path": (current_item.audio_path if current_item is not None else None)})
        )
    current_cues = {item.cue_id: item for item in current.cues}
    sanitized_cues = [
        _preserve_model_fields(
            item,
            current_cues.get(item.cue_id),
            _BACKEND_OWNED_CUE_MEDIA_FIELDS,
        )
        for item in incoming.cues
    ]
    current_subtitles = {item.subtitle_id: item for item in current.localized_subtitles}
    sanitized_subtitles = [
        _preserve_model_fields(
            item,
            current_subtitles.get(item.subtitle_id),
            _BACKEND_OWNED_SUBTITLE_MEDIA_FIELDS,
        )
        for item in incoming.localized_subtitles
    ]
    current_candidates = {str(item.get("candidate_id") or ""): dict(item) for item in current.generated_candidates}
    sanitized_candidates = []
    for item in incoming.generated_candidates:
        candidate = dict(item)
        current_candidate = current_candidates.get(str(candidate.get("candidate_id") or ""))
        if current_candidate is not None and "audio_path" in current_candidate:
            candidate["audio_path"] = current_candidate["audio_path"]
        else:
            candidate.pop("audio_path", None)
        for field in _BACKEND_OWNED_GENERATED_CANDIDATE_FIELDS:
            if current_candidate is not None and field in current_candidate:
                candidate[field] = current_candidate[field]
            else:
                candidate.pop(field, None)
        sanitized_candidates.append(candidate)
    sanitized_source_media = incoming.source_media.model_copy(
        update={
            "video_path": _server_or_initial_managed_media_path(
                project_id,
                current.source_media.video_path,
                incoming.source_media.video_path,
                allowed_directories={"source"},
                allowed_extensions=media_assets.VIDEO_EXTENSIONS,
            ),
            "audio_path": _server_or_initial_managed_media_path(
                project_id,
                current.source_media.audio_path,
                incoming.source_media.audio_path,
                allowed_directories={"audio"},
            ),
        }
    )
    sanitized_stems = incoming.stems.model_copy(
        update={
            "original_audio_path": _server_or_initial_managed_media_path(
                project_id,
                current.stems.original_audio_path,
                incoming.stems.original_audio_path,
                allowed_directories={"audio", "stems"},
            ),
            "vocals_clean_path": _server_or_initial_managed_media_path(
                project_id,
                current.stems.vocals_clean_path,
                incoming.stems.vocals_clean_path,
                allowed_directories={"stems"},
            ),
            "background_path": _server_or_initial_managed_media_path(
                project_id,
                current.stems.background_path,
                incoming.stems.background_path,
                allowed_directories={"stems"},
            ),
        }
    )
    merged_timeline_clips = _merge_client_timeline_clips(
        current.timeline_clips,
        incoming.timeline_clips,
        discarded_tts_task_ids=discarded_tts_task_ids,
        allowed_dub_lane_clip_ids=allowed_dub_lane_clip_ids,
        explicitly_added_clip_ids=explicitly_added_clip_ids,
        explicitly_deleted_clips=explicitly_deleted_clips,
        allow_untracked_incoming_dub_clips=is_uninitialized,
        preserve_existing_timeline_fields=preserve_existing_timeline_fields,
    )
    active_timeline_identities = {
        identity for clip in merged_timeline_clips if (identity := _timeline_clip_generation_identity(clip))
    }
    discarded_tts_task_ids.difference_update(active_timeline_identities)
    updates: dict[str, Any] = {
        "source_media": sanitized_source_media,
        "stems": sanitized_stems,
        "reference_clips": sanitized_reference_clips,
        "cues": sanitized_cues,
        "localized_subtitles": sanitized_subtitles,
        "dub_subtitles": (incoming.dub_subtitles if is_uninitialized else current.dub_subtitles),
        "dub_subtitle_source_revision": (
            incoming.dub_subtitle_source_revision if is_uninitialized else current.dub_subtitle_source_revision
        ),
        "dub_subtitle_dirty_scope": (
            incoming.dub_subtitle_dirty_scope
            if is_uninitialized
            else current.dub_subtitle_dirty_scope
        ),
        "dubbing_production": current.dubbing_production,
        "generated_candidates": sanitized_candidates,
        "operations": incoming.operations if is_uninitialized else current.operations,
        "tts_tasks": incoming.tts_tasks if is_uninitialized else current.tts_tasks,
        "history_placement_receipts": current.history_placement_receipts,
        "timeline_edit_receipts": current.timeline_edit_receipts,
        "source_binding_repairs": current.source_binding_repairs,
        "localization_state": incoming.localization_state if is_uninitialized else current.localization_state,
        "timeline_clips": merged_timeline_clips,
        "ui_state": {
            **sanitized_incoming_ui_state,
            **(
                {"discarded_tts_task_ids": sorted(discarded_tts_task_ids)}
                if discarded_tts_task_ids
                or "discarded_tts_task_ids" in current.ui_state
                or "discarded_tts_task_ids" in incoming.ui_state
                else {}
            ),
            **(
                {"latest_tts_task_by_segment": current_latest_tts_tasks}
                if (
                    "latest_tts_task_by_segment" in current.ui_state
                    or "latest_tts_task_by_segment" in incoming.ui_state
                )
                and isinstance(current_latest_tts_tasks, dict)
                else {}
            ),
        },
    }
    if not _localization_draft_is_active(current):
        return incoming.model_copy(update=updates)

    next_cues = []
    for cue in sanitized_cues:
        current_cue = current_cues.get(cue.cue_id)
        next_cues.append(
            cue.model_copy(
                update={
                    "zh_localized_subtitle_text": (
                        current_cue.zh_localized_subtitle_text if current_cue is not None else None
                    ),
                    "tts_recommended_text": current_cue.tts_recommended_text if current_cue is not None else None,
                }
            )
        )
    updates.update(
        {
            "cues": next_cues,
            "localized_subtitles": current.localized_subtitles,
        }
    )
    return incoming.model_copy(update=updates)


def _without_client_dubbing_production_authority(
    draft: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Strip fields that are writable only through typed backend commands."""

    candidates = []
    for raw in draft.generated_candidates:
        candidate = dict(raw)
        for field in _BACKEND_OWNED_GENERATED_CANDIDATE_FIELDS:
            candidate.pop(field, None)
        candidates.append(candidate)
    clips = []
    for raw in draft.timeline_clips:
        clip = dict(raw)
        for field in (
            "cqc_status",
            "cqc_report_version",
            "cqc_report",
        ):
            clip.pop(field, None)
        clips.append(clip)
    return draft.model_copy(
        update={
            "dubbing_production": type(draft.dubbing_production)(),
            "generated_candidates": candidates,
            "timeline_clips": clips,
        }
    )


def _preserve_model_fields(
    incoming: Any,
    current: Any | None,
    fields: tuple[str, ...],
) -> Any:
    return incoming.model_copy(update={field: getattr(current, field, None) for field in fields})


def _server_or_initial_managed_media_path(
    project_id: str | None,
    current_value: str | None,
    incoming_value: str | None,
    *,
    allowed_directories: set[str],
    allowed_extensions: set[str] | None = None,
) -> str | None:
    """Keep server state, with a bounded compatibility path for old clients.

    Existing clients historically initialized media metadata with a full Draft
    PUT. Until the request DTO is replaced, only a real file already inside the
    expected managed package directory may initialize an empty locator.
    """

    if current_value:
        return current_value
    if not project_id or not incoming_value:
        return None
    resolved = media_assets.managed_project_file(project_id, incoming_value)
    if resolved is None:
        return None
    package_root = media_assets.project_video_localization_dir(project_id).resolve(strict=False)
    try:
        relative = resolved.relative_to(package_root)
    except ValueError:
        return None
    if not relative.parts or relative.parts[0] not in allowed_directories:
        return None
    if allowed_extensions is not None and resolved.suffix.lower() not in allowed_extensions:
        return None
    return str(resolved)


def _timeline_clip_generation_identity(clip: dict[str, Any]) -> str:
    return timeline_clip_identity.generation_identity(clip)


def _merge_client_timeline_clips(
    current_clips: list[dict[str, Any]],
    incoming_clips: list[dict[str, Any]],
    *,
    discarded_tts_task_ids: set[str] | None = None,
    allowed_dub_lane_clip_ids: set[str] | None = None,
    explicitly_added_clip_ids: set[str] | None = None,
    explicitly_deleted_clips: dict[str, str] | None = None,
    allow_untracked_incoming_dub_clips: bool = False,
    preserve_existing_timeline_fields: bool = False,
) -> list[dict[str, Any]]:
    """Merge explicit client edits without reviving omitted durable clips."""
    current_by_id = {str(clip.get("clip_id") or ""): dict(clip) for clip in current_clips}
    merged_clips: list[dict[str, Any]] = []
    discarded_ids = discarded_tts_task_ids or set()
    allowed_lane_ids = allowed_dub_lane_clip_ids or set()
    added_ids = explicitly_added_clip_ids or set()
    deleted_clips = explicitly_deleted_clips or {}
    changed_existing_timeline_clip_ids = {
        clip_id
        for incoming_clip in incoming_clips
        if (clip_id := str(incoming_clip.get("clip_id") or ""))
        and (current_item := current_by_id.get(clip_id)) is not None
        and any(
            incoming_clip.get(field) != current_item.get(field)
            for field in _FULL_SNAPSHOT_PRESERVED_TIMELINE_CLIP_FIELDS
        )
    } if preserve_existing_timeline_fields else set()
    active_incoming_identities = {
        incoming_identity
        for incoming_clip in incoming_clips
        if (
            (current_item := current_by_id.get(str(incoming_clip.get("clip_id") or ""))) is not None
            and (incoming_identity := _timeline_clip_generation_identity(incoming_clip))
            and incoming_identity == _timeline_clip_generation_identity(current_item)
        )
    }
    for incoming_clip in incoming_clips:
        incoming_item = dict(incoming_clip)
        clip_id = str(incoming_item.get("clip_id") or "")
        current_item = current_by_id.get(clip_id)
        if current_item is None:
            incoming_identity = _timeline_clip_generation_identity(incoming_item)
            media_source_clip_id = str(incoming_item.get("media_source_clip_id") or "")
            source_item = next(
                (
                    candidate
                    for candidate in current_by_id.values()
                    if media_source_clip_id
                    and (
                        str(candidate.get("clip_id") or "") == media_source_clip_id
                        or str(candidate.get("media_source_clip_id") or "") == media_source_clip_id
                    )
                    and _timeline_clip_generation_identity(candidate) == incoming_identity
                ),
                None,
            )
            if (
                str(incoming_item.get("track_id") or "") == "dub"
                and not allow_untracked_incoming_dub_clips
                and (clip_id not in added_ids or source_item is None)
            ):
                continue
            if (
                incoming_identity in discarded_ids
                and incoming_identity not in active_incoming_identities
                and source_item is None
            ):
                continue
            if (
                source_item is not None
                and str(source_item.get("clip_id") or "")
                in changed_existing_timeline_clip_ids
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_TIMELINE_EDIT_REQUIRES_PATCH",
                    "拆分片段需要连同原片段的裁切通过专用时间线编辑命令保存。",
                    {
                        "clip_id": clip_id,
                        "source_clip_id": str(source_item.get("clip_id") or ""),
                    },
                )
            if source_item is not None:
                for field in _BACKEND_OWNED_TIMELINE_CLIP_FIELDS:
                    if field in source_item:
                        incoming_item[field] = source_item[field]
                    else:
                        incoming_item.pop(field, None)
            else:
                incoming_item.pop("audio_path", None)
                incoming_item.pop("timeline_timing_version", None)
            merged_clips.append(incoming_item)
            continue

        current_identity = _timeline_clip_generation_identity(current_item)
        incoming_identity = _timeline_clip_generation_identity(incoming_item)
        if current_identity and incoming_identity and current_identity != incoming_identity:
            merged_clips.append(current_item)
            continue

        merged = dict(incoming_item)
        for field in _BACKEND_OWNED_TIMELINE_CLIP_FIELDS:
            if field in current_item:
                merged[field] = current_item[field]
            else:
                merged.pop(field, None)
        if preserve_existing_timeline_fields:
            for field in _FULL_SNAPSHOT_PRESERVED_TIMELINE_CLIP_FIELDS:
                if field in current_item:
                    merged[field] = current_item[field]
                else:
                    merged.pop(field, None)
        if clip_id not in allowed_lane_ids:
            if "dub_lane" in current_item:
                merged["dub_lane"] = current_item["dub_lane"]
            else:
                merged.pop("dub_lane", None)
        merged_clips.append(merged)
    incoming_ids = {str(clip.get("clip_id") or "") for clip in incoming_clips}
    for current_clip in current_clips:
        current_item = dict(current_clip)
        clip_id = str(current_item.get("clip_id") or "")
        if not clip_id or clip_id in incoming_ids:
            continue
        expected_deleted_identity = deleted_clips.get(clip_id)
        if expected_deleted_identity:
            if _timeline_clip_generation_identity(current_item) == expected_deleted_identity:
                continue
            # A replacement may deliberately reuse the stable timeline clip ID.
            # A delayed delete for the previous audio generation must not delete
            # the newer authoritative result.
            merged_clips.append(current_item)
            continue
        merged_clips.append(current_item)
    return merged_clips


def update_video_localization_atomic(
    project_id: str,
    updater: Callable[[VideoLocalizationDraft], VideoLocalizationDraft],
    *,
    intent: draft_store.DraftWriteIntent,
    execution_fence: ExecutionFence | None = None,
    tts_handoff_claim: TtsHandoffClaim | None = None,
    observed_at_ms: int | None = None,
    operation_command: (video_localization_operation_ledger_store.OperationCommand | None) = None,
    updated_at: str | None = None,
) -> VideoLocalizationDraft | None:
    """Merge a backend-owned patch into the latest draft under one write lock."""
    with _DRAFT_WRITE_LOCK:
        for attempt in range(_PROJECT_WRITE_MAX_ATTEMPTS):
            current = draft_store.get(
                project_id,
                normalize_for_read=intent not in {"editorial", "workspace"},
            )
            if current is None:
                return None
            updated = updater(current)
            updated._repository_revision = current._repository_revision
            if updated == current:
                return current
            try:
                save_options = {"updated_at": updated_at} if updated_at is not None else {}
                return draft_store.save(
                    project_id,
                    updated,
                    intent=intent,
                    execution_fence=execution_fence,
                    tts_handoff_claim=tts_handoff_claim,
                    observed_at_ms=observed_at_ms,
                    operation_command=operation_command,
                    **save_options,
                )
            except ProjectRevisionConflict:
                if operation_command is not None or attempt == _PROJECT_WRITE_MAX_ATTEMPTS - 1:
                    raise
        raise AssertionError("unreachable Project CAS retry state")


def update_video_localization_ui_state(project_id: str, patch: dict[str, Any]) -> VideoLocalizationDraft | None:
    with _DRAFT_WRITE_LOCK:
        for attempt in range(_PROJECT_WRITE_MAX_ATTEMPTS):
            project = project_store.get_project(project_id)
            if not project:
                return None
            draft = draft_store.from_project(project, normalize_for_read=False)
            merged_ui_state = state_ownership.merge_client_ui_state_patch(
                draft.ui_state,
                patch,
            )
            if merged_ui_state == draft.ui_state:
                return draft
            next_draft = draft.model_copy(update={"ui_state": merged_ui_state})
            if any(merged_ui_state.get(field) != draft.ui_state.get(field) for field in ("dub_lane_states", "track_states", "disabled_media_tracks")):
                next_draft = dub_subtitles.reconcile_source_lifecycle(draft, next_draft)
            try:
                return draft_store.save(
                    project_id,
                    next_draft,
                    intent="workspace",
                )
            except ProjectRevisionConflict:
                if attempt == _PROJECT_WRITE_MAX_ATTEMPTS - 1:
                    raise
        raise AssertionError("unreachable Project CAS retry state")


def update_video_localization_timeline_edit(
    project_id: str,
    patch,
) -> VideoLocalizationDraft | None:
    """Apply one bounded client timeline edit without replacing the full draft."""

    editable_clip_fields = {
        "start_ms",
        "end_ms",
        "source_start_ms",
        "source_end_ms",
        "media_source_clip_id",
        "dub_lane",
    }
    editable_lane_fields = {"muted", "solo", "volume", "label", "locked"}
    generation_task_ids_to_cancel: set[str] = set()
    request_id = str(patch.request_id or "")
    request_fingerprint = hashlib.sha256(
        json.dumps(
            patch.model_dump(mode="json", exclude={"request_id"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    committed_at = now_iso()
    system_media_track_by_clip_id = {
        "media_original": "original",
        "media_vocals": "vocals",
        "media_background": "background",
    }
    disabled_media_tracks = {
        str(track_id)
        for track_id in (
            patch.ui_state_patch.disabled_media_tracks
            if patch.ui_state_patch is not None
            and patch.ui_state_patch.disabled_media_tracks is not None
            else []
        )
    }
    referenced_system_media_clip_ids = {
        str(clip_id)
        for clip_id in [
            *(item.clip_id for item in patch.clip_patches),
            *(item.clip_id for item in patch.added_clips),
            *(item.media_source_clip_id for item in patch.added_clips),
        ]
        if str(clip_id) in system_media_track_by_clip_id
    }
    referenced_system_media_clip_ids.update(
        item.clip_id
        for item in patch.deleted_clips
        if system_media_track_by_clip_id.get(item.clip_id)
        in disabled_media_tracks
    )

    def editable_clip_state(clip: dict[str, Any]) -> dict[str, Any]:
        def integer_or_none(value: Any) -> int | None:
            return int(round(value)) if isinstance(value, (int, float)) else None

        media_source_clip_id = clip.get("media_source_clip_id")
        return {
            "start_ms": integer_or_none(clip.get("start_ms")),
            "end_ms": integer_or_none(clip.get("end_ms")),
            "source_start_ms": integer_or_none(clip.get("source_start_ms")),
            "source_end_ms": integer_or_none(clip.get("source_end_ms")),
            "media_source_clip_id": (
                str(media_source_clip_id)
                if media_source_clip_id is not None
                else None
            ),
            "dub_lane": integer_or_none(clip.get("dub_lane")),
        }

    def apply(current: VideoLocalizationDraft) -> VideoLocalizationDraft:
        from app.domains.video_localization.timeline_editor_collections import apply_collection_changes

        nonlocal generation_task_ids_to_cancel
        if request_id and (
            previous_receipt := current.timeline_edit_receipts.get(request_id)
        ) is not None:
            if previous_receipt.request_fingerprint != request_fingerprint:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_TIMELINE_REQUEST_CONFLICT",
                    "同一次时间线保存的内容发生了变化，请重新发起保存。",
                    {"request_id": request_id},
                )
            return current
        if referenced_system_media_clip_ids:
            resolution = media_health.inspect_project_media(
                current,
                package_root=media_assets.project_video_localization_dir(project_id),
            )
            current = workspace_media_projection.materialize_referenced_system_media_tracks(
                current,
                resolution,
                referenced_system_media_clip_ids,
            )
        before = current
        if patch.localized_subtitle_collection_change is not None:
            _ensure_localization_track_editable(current)
        current = apply_collection_changes(current, patch)
        previous_tasks = {item.workflow_id: item for item in before.tts_tasks}
        generation_task_ids_to_cancel = {
            item.generation_task_id for item in current.tts_tasks
            if item.generation_task_id and item.status == "cancelled"
            and (previous := previous_tasks.get(item.workflow_id)) is not None
            and previous.status not in {"success", "failed", "cancelled"}
        }
        clip_by_id = {
            str(item.get("clip_id") or ""): dict(item)
            for item in current.timeline_clips
        }
        added_clip_ids: list[str] = []

        def require_current_clip(item) -> dict[str, Any]:
            clip_id = item.clip_id
            clip = clip_by_id.get(clip_id)
            if clip is None:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_TIMELINE_CLIP_NOT_FOUND",
                    "时间线片段已变化，请刷新后重试。",
                    {"clip_id": clip_id},
                )
            clip = timeline_clip_timing.normalize_audio_clip(
                clip,
                frame_rate=current.source_media.frame_rate,
                timeline_duration_ms=current.source_media.duration_ms,
            )
            current_identity = _timeline_clip_generation_identity(clip)
            if current_identity != item.expected_generation_identity:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_TIMELINE_CLIP_CHANGED",
                    "这条声音已经被新结果替换，旧编辑没有执行。",
                    {"clip_id": clip_id},
                )
            if editable_clip_state(clip) != item.expected_editable_fields.model_dump():
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_TIMELINE_CLIP_CHANGED",
                    "这条声音刚刚又被剪过，旧的剪辑没有覆盖新结果。请刷新后重试。",
                    {"clip_id": clip_id, "editable": True},
                )
            clip_by_id[clip_id] = clip
            return clip

        for item in patch.clip_patches:
            require_current_clip(item)
        for item in patch.deleted_clips:
            require_current_clip(item)
        for item in patch.added_clips:
            if item.clip_id in clip_by_id:
                existing = clip_by_id[item.clip_id]
                if any(existing.get(field) != value for field, value in item.model_dump().items()):
                    raise AppException(
                        409, "VIDEO_LOCALIZATION_TIMELINE_CLIP_CHANGED",
                        "这个片段编号已存在，旧的新增操作没有覆盖现有片段。",
                        {"clip_id": item.clip_id},
                    )
                continue
            source = next(
                (
                    candidate
                    for candidate in clip_by_id.values()
                    if str(candidate.get("clip_id") or "") == item.media_source_clip_id
                    or str(candidate.get("media_source_clip_id") or "")
                    == item.media_source_clip_id
                ),
                None,
            )
            if source is None or not source.get("audio_path"):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_TIMELINE_MEDIA_NOT_FOUND",
                    "找不到这个片段对应的音频源。",
                    {"clip_id": item.clip_id},
                )
            added = {
                **source,
                **item.model_dump(),
                "track_id": source.get("track_id", "dub"),
                "status": "ready",
                "timeline_timing_version": "editorial-v1",
            }
            clip_by_id[item.clip_id] = added
            added_clip_ids.append(item.clip_id)
        for item in patch.clip_patches:
            clip = clip_by_id.get(item.clip_id)
            if clip is None:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_TIMELINE_CLIP_NOT_FOUND",
                    "时间线片段已变化，请刷新后重试。",
                    {"clip_id": item.clip_id},
                )
            fields = item.model_dump(exclude_unset=True)
            fields.pop("clip_id", None)
            for field, value in fields.items():
                if field in editable_clip_fields:
                    clip[field] = value
            clip["timeline_timing_version"] = "editorial-v1"
            start_ms = clip.get("start_ms")
            end_ms = clip.get("end_ms")
            if isinstance(start_ms, (int, float)) and isinstance(end_ms, (int, float)) and end_ms <= start_ms:
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_TIMELINE_RANGE_INVALID",
                    "片段出点必须晚于入点。",
                    {"clip_id": item.clip_id},
                )
            source_start_ms = clip.get("source_start_ms")
            source_end_ms = clip.get("source_end_ms")
            if (
                isinstance(source_start_ms, (int, float))
                and isinstance(source_end_ms, (int, float))
                and source_end_ms <= source_start_ms
            ):
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_TIMELINE_SOURCE_RANGE_INVALID",
                    "片段源音频出点必须晚于入点。",
                    {"clip_id": item.clip_id},
                )
        deleted_clip_ids = {item.clip_id for item in patch.deleted_clips}
        timeline_clips = [
            clip_by_id[str(item.get("clip_id") or "")]
            for item in current.timeline_clips
            if str(item.get("clip_id") or "") not in deleted_clip_ids
        ]
        timeline_clips.extend(
            clip_by_id[clip_id]
            for clip_id in added_clip_ids
            if clip_id not in deleted_clip_ids
        )

        ui_state = dict(current.ui_state)
        raw_lane_states = ui_state.get("dub_lane_states")
        lane_states = {
            str(key): dict(value)
            for key, value in (raw_lane_states.items() if isinstance(raw_lane_states, dict) else [])
            if isinstance(value, dict)
        }
        for item in patch.dub_lane_state_patches:
            lane = str(item.lane)
            if item.remove:
                lane_states.pop(lane, None)
                continue
            state = dict(lane_states.get(lane, {}))
            fields = item.model_dump(exclude_unset=True)
            for field, value in fields.items():
                if field in editable_lane_fields and value is not None:
                    state[field] = value
            lane_states[lane] = state
        if patch.dub_lane_state_patches:
            ui_state["dub_lane_states"] = lane_states

        if patch.ui_state_patch is not None:
            ui_patch = patch.ui_state_patch.model_dump(exclude_unset=True)
            if "disabled_media_tracks" in ui_patch:
                ui_state["disabled_media_tracks"] = list(dict.fromkeys(
                    str(value) for value in (ui_patch["disabled_media_tracks"] or [])
                    if str(value)
                ))
            if "discarded_tts_task_ids" in ui_patch:
                discarded = {
                    str(value)
                    for value in [
                        *ui_state.get("discarded_tts_task_ids", []),
                        *(ui_patch["discarded_tts_task_ids"] or []),
                    ]
                    if str(value)
                }
                active_identities = {
                    identity
                    for clip in timeline_clips
                    if (identity := _timeline_clip_generation_identity(clip))
                }
                discarded.difference_update(active_identities)
                ui_state["discarded_tts_task_ids"] = sorted(discarded)
        result = current.model_copy(
            update={"timeline_clips": timeline_clips, "ui_state": ui_state}
        )
        if (
            result.timeline_clips != before.timeline_clips
            or result.localized_subtitles != before.localized_subtitles
            or result.cues != before.cues
            or any(result.ui_state.get(field) != before.ui_state.get(field) for field in ("dub_lane_states", "disabled_media_tracks"))
        ):
            result = dub_subtitles.reconcile_source_lifecycle(before, result)
        if request_id:
            if current._repository_revision is None:
                raise RuntimeError(
                    "Timeline edit receipt requires a repository revision"
                )
            receipt = timeline_edit_receipts.build_receipt(
                result,
                patch,
                request_fingerprint=request_fingerprint,
                repository_revision=current._repository_revision + 1,
                updated_at=committed_at,
            )
            result = result.model_copy(
                update={
                    "timeline_edit_receipts": timeline_edit_receipts.retain_recent(
                        current.timeline_edit_receipts,
                        request_id,
                        receipt,
                    )
                }
            )
        return result

    saved = update_video_localization_atomic(
        project_id,
        apply,
        intent="editorial",
        updated_at=committed_at if request_id else None,
    )
    if saved is not None and request_id:
        saved._timeline_edit_response_receipt = (
            saved.timeline_edit_receipts.get(request_id)
        )
    if saved is not None:
        for generation_task_id in generation_task_ids_to_cancel:
            _cancel_tts_generation_task(generation_task_id)
    return saved


def _replace_tts_stage(
    task: VideoLocalizationTtsTask,
    kind: str,
    **updates: Any,
) -> VideoLocalizationTtsTask:
    stages = [stage.model_copy(update=updates) if stage.kind == kind else stage for stage in task.stages]
    if stages == task.stages:
        return task
    return task.model_copy(update={"stages": stages, "updated_at": now_iso()})


def _with_tts_task_status(task: VideoLocalizationTtsTask) -> VideoLocalizationTtsTask:
    generation, placement = task.stages
    if generation.status == "failed" or placement.status == "failed":
        status = "failed"
    elif generation.status == "cancelled" or placement.status == "cancelled":
        status = "cancelled"
    elif placement.status == "success":
        status = "success"
    elif generation.status in {"running", "success"} or placement.status == "running":
        status = "running"
    elif generation.status == "queued":
        status = "queued"
    else:
        status = "prepared"
    completed_at = (task.completed_at or now_iso()) if status in {"success", "failed", "cancelled"} else None
    if task.status == status and task.completed_at == completed_at:
        return task
    return task.model_copy(update={"status": status, "completed_at": completed_at, "updated_at": now_iso()})


def _update_tts_task(
    draft: VideoLocalizationDraft,
    workflow_id: str,
    updater: Callable[[VideoLocalizationTtsTask], VideoLocalizationTtsTask],
) -> VideoLocalizationDraft:
    changed = False
    tasks: list[VideoLocalizationTtsTask] = []
    for task in draft.tts_tasks:
        if task.workflow_id == workflow_id:
            tasks.append(_with_tts_task_status(updater(task)))
            changed = True
        else:
            tasks.append(task)
    return draft.model_copy(update={"tts_tasks": tasks}) if changed else draft


def with_completed_tts_placement(
    draft: VideoLocalizationDraft,
    *,
    generation_task_id: str | None,
    result_id: str,
    timeline_clip_id: str,
    dub_lane: int = 0,
) -> VideoLocalizationDraft:
    """Finish the durable workflow after the canonical timeline commit.

    Managed dubbing stores a generated candidate before acoustic processing,
    then marks placement successful only when that processed take atomically
    becomes the current target-owned timeline result.
    """

    workflow = next(
        (
            item
            for item in reversed(draft.tts_tasks)
            if (
                generation_task_id
                and item.generation_task_id == generation_task_id
            )
            or item.result_id == result_id
        ),
        None,
    )
    if workflow is None:
        return draft

    def complete(item: VideoLocalizationTtsTask) -> VideoLocalizationTtsTask:
        placement = next(
            stage for stage in item.stages if stage.kind == "placement"
        )
        updated = _replace_tts_stage(
            item,
            "placement",
            status="success",
            progress=1.0,
            error_code=None,
            error_message=None,
            completed_at=now_iso(),
            parameters={
                **placement.parameters,
                "result_id": result_id,
                "timeline_clip_id": timeline_clip_id,
                "dub_lane": max(0, int(dub_lane)),
            },
        )
        return updated.model_copy(
            update={
                "result_id": result_id,
                "timeline_clip_id": timeline_clip_id,
            }
        )

    return _update_tts_task(draft, workflow.workflow_id, complete)


def with_superseded_group_tts_placements_closed(
    draft: VideoLocalizationDraft,
    *,
    group_id: str,
    selected_generation_task_id: str | None,
    selected_result_id: str,
    only_earlier_workflows: bool = False,
) -> VideoLocalizationDraft:
    """Close unused generated siblings after one current-plan take is adopted.

    The generated audio remains in history. Only open placement receipts whose
    generation already succeeded for this exact current group are cancelled;
    another group's work and any generation still queued/running are untouched.
    """

    plan = draft.dubbing_production.active_plan
    group = next(
        (item for item in plan.groups if item.group_id == group_id),
        None,
    ) if plan is not None else None
    if plan is None or group is None:
        return draft
    units_by_id = {item.unit_id: item for item in plan.semantic_units}
    source_cue_ids = list(
        dict.fromkeys(
            str(cue_id)
            for unit_id in group.unit_ids
            if unit_id in units_by_id
            for cue_id in units_by_id[unit_id].source_cue_ids
            if str(cue_id)
        )
    )
    selected_ids = {
        str(value)
        for value in (selected_generation_task_id, selected_result_id)
        if value
    }
    selected_index = next(
        (index for index, item in enumerate(draft.tts_tasks)
         if selected_ids.intersection({str(item.generation_task_id or ""), str(item.result_id or "")})),
        -1,
    )
    completed_at = now_iso()
    changed = False
    tasks: list[VideoLocalizationTtsTask] = []
    for task_index, task in enumerate(draft.tts_tasks):
        generation = next(
            stage for stage in task.stages if stage.kind == "generation"
        )
        placement = next(
            stage for stage in task.stages if stage.kind == "placement"
        )
        parameters = generation.parameters
        identities = {
            str(value)
            for value in (task.generation_task_id, task.result_id)
            if value
        }
        target_ids = [
            str(value)
            for value in parameters.get(
                "video_localization_target_subtitle_ids", []
            )
            if str(value)
        ]
        task_source_cue_ids = [
            str(value)
            for value in parameters.get("video_localization_source_cue_ids", [])
            if str(value)
        ]
        if (
            identities.isdisjoint(selected_ids)
            and (not only_earlier_workflows or 0 <= task_index < selected_index)
            and generation.status == "success"
            and placement.status in {"pending", "running"}
            and parameters.get("video_localization_dubbing_group_id")
            == group.group_id
            and (
                parameters.get("video_localization_dubbing_plan_revision") == plan.plan_revision
                or frozen_group_request(
                    draft, group, parameters=parameters,
                    allow_equivalent_plan_rebind=True,
                ) is not None
            )
            and target_ids == list(group.subtitle_ids)
            and task_source_cue_ids == source_cue_ids
            and task.text == group.spoken_text
        ):
            task = _replace_tts_stage(
                task,
                "placement",
                status="cancelled",
                progress=1.0,
                error_code=None,
                error_message=None,
                completed_at=completed_at,
                parameters={
                    **placement.parameters,
                    "completion_reason": "superseded_by_formal_group_candidate",
                    "selected_result_id": selected_result_id,
                },
            )
            task = _with_tts_task_status(task)
            changed = True
        tasks.append(task)
    return draft.model_copy(update={"tts_tasks": tasks}) if changed else draft


def with_current_timeline_tts_placements_completed(
    draft: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Close generated workflows already owned by the formal timeline.

    The formal main lane is the durable placement authority.  A process
    interruption or a historical repair may leave the audio and its current
    timeline clips committed while the workflow's placement stage still says
    ``running``.  Reconcile that stale status from the existing result identity
    instead of introducing another acceptance label or review state.
    """

    formal_clip_by_result: dict[str, dict[str, Any]] = {}
    for raw in sorted(
        (dict(item) for item in draft.timeline_clips),
        key=lambda item: (
            int(item.get("start_ms") or 0),
            str(item.get("clip_id") or ""),
        ),
    ):
        if (
            raw.get("track_id") != "dub"
            or int(raw.get("dub_lane") or 0) != 0
            or raw.get("status") != "ready"
            or raw.get("manual_history_copy")
        ):
            continue
        result_id = str(raw.get("result_id") or "")
        if result_id:
            formal_clip_by_result.setdefault(result_id, raw)

    completed_at = now_iso()
    changed = False
    tasks: list[VideoLocalizationTtsTask] = []
    for task in draft.tts_tasks:
        generation = next(
            stage for stage in task.stages if stage.kind == "generation"
        )
        formal_clip = formal_clip_by_result.get(str(task.result_id or ""))
        if (
            task.status != "running"
            or generation.status != "success"
            or formal_clip is None
        ):
            tasks.append(task)
            continue
        placement = next(
            stage for stage in task.stages if stage.kind == "placement"
        )
        clip_id = str(formal_clip.get("clip_id") or "")
        updated = _replace_tts_stage(
            task,
            "placement",
            status="success",
            progress=1.0,
            error_code=None,
            error_message=None,
            completed_at=completed_at,
            parameters={
                **placement.parameters,
                "result_id": task.result_id,
                "timeline_clip_id": clip_id,
                "dub_lane": 0,
            },
        ).model_copy(update={"timeline_clip_id": clip_id})
        tasks.append(_with_tts_task_status(updated))
        changed = True
    return draft.model_copy(update={"tts_tasks": tasks}) if changed else draft


def register_single_tts_task(
    project_id: str,
    segment_id: str,
    task_id: str,
    workflow_id: str | None = None,
    *,
    handoff_claim: TtsHandoffClaim | None = None,
) -> VideoLocalizationDraft | None:
    submitted = task_queue.get_task(task_id)
    recovery_parent_id = None
    recovery_decision = None
    if submitted and submitted.task_type == "export" and submitted.parameters.get("video_localization_recovery"):
        from app.services import longform_queue
        parent = longform_queue.get_task(str(submitted.longform_task_id or ""))
        if parent and parent.status in {"queued", "running"}:
            recovery_parent_id = f"longform:{parent.longform_task_id}"
            recovery_decision = submitted.parameters["video_localization_recovery"]

    def register(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
        latest = draft.ui_state.get("latest_tts_task_by_segment", {})
        if not isinstance(latest, dict):
            latest = {}
        already_registered = next(
            (
                item
                for item in reversed(draft.tts_tasks)
                if item.segment_id == segment_id
                and item.generation_task_id == task_id
                and (not workflow_id or item.workflow_id == workflow_id)
            ),
            None,
        )
        if already_registered is not None:
            return draft
        if not workflow_id and latest.get(segment_id) == task_id:
            return draft
        prepared = next(
            (
                item
                for item in reversed(draft.tts_tasks)
                if item.segment_id == segment_id
                and (not workflow_id or item.workflow_id == workflow_id)
                and (
                    (item.generation_task_id is None and item.status == "prepared")
                    or (
                        workflow_id
                        and str(item.generation_task_id or "").startswith("longform:")
                        and (item.status in {"queued", "running"} or (
                            item.status == "failed"
                            and item.generation_task_id == recovery_parent_id
                            and any(stage.kind == "generation" and stage.parameters.get("video_localization_recovery") == recovery_decision for stage in item.stages)
                        ))
                    )
                )
            ),
            None,
        )
        if prepared is None and workflow_id:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_WORKFLOW_STALE",
                "配音任务已失效，请返回视频本土化页面重新发送",
                {"workflow_id": workflow_id, "segment_id": segment_id},
            )
        next_draft = draft
        if prepared is not None:

            def mark_queued(item: VideoLocalizationTtsTask) -> VideoLocalizationTtsTask:
                updated = _replace_tts_stage(
                    item,
                    "generation",
                    status="queued",
                    progress=0.0,
                    error_code=None,
                    error_message=None,
                )
                return updated.model_copy(update={"generation_task_id": task_id})

            next_draft = _update_tts_task(draft, prepared.workflow_id, mark_queued)
        return next_draft.model_copy(
            update={
                "ui_state": {
                    **next_draft.ui_state,
                    "latest_tts_task_by_segment": {**latest, segment_id: task_id},
                }
            }
        )

    return update_video_localization_atomic(
        project_id,
        register,
        intent="runtime",
        tts_handoff_claim=handoff_claim,
    )


def mark_prepared_tts_workflow_terminal(
    project_id: str,
    workflow_id: str,
    *,
    status: str,
    error_message: str | None = None,
    handoff_claim: TtsHandoffClaim | None = None,
) -> VideoLocalizationDraft | None:
    if status not in {"failed", "cancelled"}:
        raise ValueError("terminal TTS workflow status must be failed or cancelled")

    def mark_terminal(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
        def update(item: VideoLocalizationTtsTask) -> VideoLocalizationTtsTask:
            return _replace_tts_stage(
                item,
                "generation",
                status=status,
                progress=1.0,
                error_code="TTS_LONGFORM_CANCELLED" if status == "cancelled" else "TTS_LONGFORM_FAILED",
                error_message=error_message
                or ("长段配音任务已取消" if status == "cancelled" else "长段配音任务没有完成"),
                completed_at=now_iso(),
            )

        return _update_tts_task(draft, workflow_id, update)

    return update_video_localization_atomic(
        project_id,
        mark_terminal,
        intent="runtime",
        tts_handoff_claim=handoff_claim,
    )


def mark_tts_workflow_placement_failed(
    project_id: str,
    workflow_id: str,
    *,
    error_code: str,
    error_message: str,
) -> VideoLocalizationDraft | None:
    """Close one generated workflow whose candidate cannot be placed safely."""

    def mark_failed(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
        def update(item: VideoLocalizationTtsTask) -> VideoLocalizationTtsTask:
            generation = next(
                stage for stage in item.stages if stage.kind == "generation"
            )
            placement = next(
                stage for stage in item.stages if stage.kind == "placement"
            )
            if generation.status != "success" or placement.status not in {
                "pending",
                "running",
            }:
                return item
            return _replace_tts_stage(
                item,
                "placement",
                status="failed",
                progress=1.0,
                error_code=error_code,
                error_message=error_message,
                completed_at=now_iso(),
            )

        return _update_tts_task(draft, workflow_id, update)

    return update_video_localization_atomic(
        project_id,
        mark_failed,
        intent="runtime",
    )


def reconcile_dubbing_workflow_terminal_states(
    project_id: str,
) -> VideoLocalizationDraft | None:
    """Project formal timeline and exhausted groups into workflow terminal states."""

    def reconcile(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
        updated = with_current_timeline_tts_placements_completed(draft)
        plan = updated.dubbing_production.active_plan
        if plan is None:
            return updated
        # Recover the same sibling closeout used by adoption. A later explicit
        # take must survive replay: an older formal clip does not cancel it.
        for task in updated.tts_tasks:
            generation = next(stage for stage in task.stages if stage.kind == "generation")
            group_id = str(generation.parameters.get("video_localization_dubbing_group_id") or "")
            group = next((item for item in plan.groups if item.group_id == group_id), None)
            if (
                task.status != "success" or not task.result_id or group is None
                or task.text != group.spoken_text
            ):
                continue
            selected_clips = [clip for clip in updated.timeline_clips
                if clip.get("track_id") == "dub" and int(clip.get("dub_lane") or 0) == 0
                and clip.get("status") == "ready" and not clip.get("manual_history_copy")
                and clip.get("result_id") == task.result_id]
            covered = {target for clip in selected_clips for target in clip.get("target_subtitle_ids", [])}
            if covered != set(group.subtitle_ids):
                continue
            updated = with_superseded_group_tts_placements_closed(
                updated, group_id=group_id, selected_generation_task_id=task.generation_task_id,
                selected_result_id=task.result_id, only_earlier_workflows=True,
            )
        failed_group_ids = {
            item.group_id
            for item in updated.dubbing_production.group_failures
            if item.source_revision == plan.source_revision
            and item.plan_revision == plan.plan_revision
            and item.reason_code
            != "group_capacity_recovery_decision_required"
        }
        if not failed_group_ids:
            return updated
        completed_at = now_iso()
        tasks: list[VideoLocalizationTtsTask] = []
        changed = False
        for task in updated.tts_tasks:
            generation = next(
                stage for stage in task.stages if stage.kind == "generation"
            )
            placement = next(
                stage for stage in task.stages if stage.kind == "placement"
            )
            group_id = str(
                generation.parameters.get(
                    "video_localization_dubbing_group_id"
                )
                or ""
            )
            if (
                group_id in failed_group_ids
                and generation.status == "success"
                and placement.status in {"pending", "running"}
            ):
                task = _replace_tts_stage(
                    task,
                    "placement",
                    status="failed",
                    progress=1.0,
                    error_code="TTS_PLACEMENT_REGENERATION_EXHAUSTED",
                    error_message="当前分组的安全落位重试已结束。",
                    completed_at=completed_at,
                )
                task = _with_tts_task_status(task)
                changed = True
            tasks.append(task)
        return updated.model_copy(update={"tts_tasks": tasks}) if changed else updated

    return update_video_localization_atomic(
        project_id,
        reconcile,
        intent="runtime",
    )


def reset_video_localization(project_id: str) -> VideoLocalizationDraft | None:
    with _DRAFT_WRITE_LOCK:
        if not project_lifecycle_cleanup.flush_project(project_id):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_CLEANUP_PENDING",
                "上一次项目清理尚未完成，请稍后重试。",
            )
        project = project_store.get_project(project_id)
        if not project:
            return None
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        if any(operation.status in operation_state.ACTIVE_STATUSES for operation in draft.operations):
            raise AppException(
                409, "VIDEO_LOCALIZATION_RESET_BLOCKED", "当前仍有后台任务在运行，请先取消或等待完成后再清空"
            )
        if any(
            task.status.value not in {"success", "failed", "cancelled"}
            for task in task_queue.list_project_tasks(project_id)
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_RESET_BLOCKED",
                "当前仍有配音生成任务，请先取消或等待完成后再清空",
            )
        if any(
            batch.status.value not in {"success", "failed", "cancelled"}
            for batch in batch_queue.list_project_batches(project_id)
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_RESET_BLOCKED",
                "当前仍有批量配音任务，请先取消或等待完成后再清空",
            )
        empty_draft = draft_store.with_fresh_gate(
            VideoLocalizationDraft(),
            updated_at=now_iso(),
        )
        directory_name = str(
            project.parameters.get(media_assets.PROJECT_DIR_NAME_KEY) or ""
        ).strip() or media_assets.project_dir_name(
            project.project_id,
            project.name,
        )
        project.parameters = {
            **project.parameters,
            media_assets.PROJECT_DIR_NAME_KEY: directory_name,
            VIDEO_LOCALIZATION_KEY: empty_draft.model_dump(mode="json"),
        }
        cleanup_job = project_lifecycle_cleanup.new_job(
            project_id=project_id,
            action="reset",
            directory_name=directory_name,
        )
        project_store.reset_video_localization_project(
            project,
            operation_updated_at=str(empty_draft.updated_at or now_iso()),
            cleanup_job=cleanup_job,
        )
        project_lifecycle_cleanup.flush(cleanup_job.job_id)
        return empty_draft


def delete_project(project_id: str) -> bool:
    with _DRAFT_WRITE_LOCK:
        if not project_lifecycle_cleanup.flush_project(project_id):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_CLEANUP_PENDING",
                "上一次项目清理尚未完成，请稍后重试。",
            )
        project = project_store.get_project(project_id)
        if not project:
            return False
        draft = get_video_localization(project_id)
        if draft and any(operation.status in operation_state.ACTIVE_STATUSES for operation in draft.operations):
            raise AppException(
                409, "VIDEO_LOCALIZATION_DELETE_BLOCKED", "当前仍有后台任务，请先取消或等待完成后再删除项目"
            )
        project_tasks = task_queue.list_project_tasks(project_id)
        if any(task.status.value not in {"success", "failed", "cancelled"} for task in project_tasks):
            raise AppException(
                409, "VIDEO_LOCALIZATION_DELETE_BLOCKED", "当前仍有配音生成任务，请先取消或等待完成后再删除项目"
            )
        project_batches = batch_queue.list_project_batches(project_id)
        if any(batch.status.value not in {"success", "failed", "cancelled"} for batch in project_batches):
            raise AppException(
                409, "VIDEO_LOCALIZATION_DELETE_BLOCKED", "当前仍有批量配音任务，请先取消或等待完成后再删除项目"
            )
        project_history = history_store.list_history(
            limit=-1,
            project_id=project_id,
        )
        managed_reference_paths = custom_reference_store.managed_paths_in(project.model_dump(mode="json"))
        for task in project_tasks:
            managed_reference_paths.update(custom_reference_store.managed_paths_in(task.parameters))
        for item in project_history:
            managed_reference_paths.update(custom_reference_store.managed_paths_in(item.parameter_snapshot))
        for batch in project_batches:
            managed_reference_paths.update(custom_reference_store.managed_paths_in(batch.model_dump(mode="json")))
        directory_name = str(
            project.parameters.get(media_assets.PROJECT_DIR_NAME_KEY) or ""
        ).strip() or media_assets.project_dir_name(
            project.project_id,
            project.name,
        )
        cleanup_job = project_lifecycle_cleanup.new_job(
            project_id=project_id,
            action="delete",
            directory_name=directory_name,
            cleanup_payload={
                "result_ids": [item.result_id for item in project_history],
                "output_paths": [item.output_path for item in project_history if item.output_path],
                "managed_reference_paths": sorted(str(path) for path in managed_reference_paths),
            },
        )
        requested_at = now_iso()
        project_store.delete_project(
            project_id,
            expected_repository_revision=project._repository_revision,
            cleanup_job=cleanup_job,
            requested_at=requested_at,
        )
    project_lifecycle_cleanup.schedule(cleanup_job.job_id)
    return True


def open_project_directory(project_id: str) -> dict[str, str] | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    return media_assets.open_project_video_localization_dir(project_id)


def prepare_project_rename(project: Any, next_name: str) -> Any:
    name = next_name.strip() or project.name
    project.name = name
    return project


def save_project_metadata(project: Project) -> Project:
    """Commit metadata once, then refresh the localization snapshot mirror."""

    saved = project_store.save_project(project)
    project_snapshot_projection.flush(saved.project_id)
    return saved


def _truncate_project_naming_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"


def _project_naming_context(draft: VideoLocalizationDraft) -> dict[str, Any]:
    localized_lines = [item.text.strip() for item in draft.localized_subtitles if item.text.strip()]
    if not localized_lines:
        localized_lines = [
            cue.zh_localized_subtitle_text.strip()
            for cue in draft.cues
            if cue.zh_localized_subtitle_text and cue.zh_localized_subtitle_text.strip()
        ]

    transcription = draft.transcription
    asr_text = ""
    research_sources: list[dict[str, str]] = []
    if transcription is not None:
        asr_text = transcription.corrected_text.strip() or transcription.raw_text.strip()
        if not asr_text:
            asr_text = " ".join(segment.text.strip() for segment in transcription.segments if segment.text.strip())
        research_sources = [
            {
                "title": _truncate_project_naming_text(source.title, 120),
                "snippet": _truncate_project_naming_text(source.snippet, 240),
            }
            for source in transcription.research.sources[:6]
            if source.title.strip() or source.snippet.strip()
        ]

    if not asr_text:
        asr_text = " ".join(cue.en_subtitle_text.strip() for cue in draft.cues if cue.en_subtitle_text.strip())

    return {
        "localized_subtitles": _truncate_project_naming_text(" ".join(localized_lines), 4200),
        "asr_subtitles": _truncate_project_naming_text(asr_text, 3200),
        "scene_context": _truncate_project_naming_text(draft.scene_context, 600),
        "research_sources": research_sources,
    }


def _sanitize_project_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    name = value.strip().strip("'\"“”‘’")
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:48].rstrip(" .")


def _unique_project_name(base_name: str, projects: list[Project], current_project_id: str) -> str:
    occupied = {
        project.name.strip().casefold()
        for project in projects
        if project.project_id != current_project_id and project.name.strip()
    }
    if base_name.casefold() not in occupied:
        return base_name
    suffix = 2
    candidate = base_name
    while True:
        suffix_text = f" {suffix}"
        candidate = f"{base_name[: 48 - len(suffix_text)].rstrip()}{suffix_text}"
        if candidate.casefold() not in occupied:
            return candidate
        suffix += 1


def auto_name_video_localization_project(project_id: str) -> Project | None:
    project = project_store.get_project(project_id)
    if project is None:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    context = _project_naming_context(draft)
    if not context["localized_subtitles"] and not context["asr_subtitles"]:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_NAMING_CONTEXT_EMPTY",
            "当前没有可用于命名的 ASR 或本土化字幕，请先生成或导入字幕。",
        )

    system_prompt = """你是视频剪辑项目的命名助手。请根据字幕、场景信息和已有资料，为内容本身取一个简洁、可辨认的项目名。
要求：
1. 有中文本土化字幕时优先使用中文名称，建议 6-24 个汉字或同等长度的中英混合标题。
2. 概括视频真实主题、人物或事件，不描述制作流程。
3. 不要使用“视频本土化”“未命名项目”等泛化词，不要沿用源文件名、日期、引号或编号后缀。
4. 只返回 JSON 对象：{"name":"项目名"}。"""
    try:
        result = llm_runtime.complete_json(
            system_prompt,
            context,
            temperature=0.2,
            max_tokens=128,
            timeout=45,
            disable_reasoning=True,
        )
    except llm_runtime.LlmRuntimeError as exc:
        raise AppException(exc.status_code, exc.code, str(exc)) from exc

    suggested_name = _sanitize_project_name(result.get("name") if isinstance(result, dict) else None)
    if not suggested_name:
        raise AppException(
            502,
            "VIDEO_LOCALIZATION_NAME_INVALID",
            "语言模型没有返回有效的项目名称，请重试。",
        )

    with _DRAFT_WRITE_LOCK:
        current = project_store.get_project(project_id)
        if current is None:
            return None
        unique_name = _unique_project_name(suggested_name, project_store.list_projects(), project_id)
        renamed = prepare_project_rename(current, unique_name)
        return save_project_metadata(renamed)


async def import_source_media(project_id: str, file: UploadFile) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    imported = await source_pipeline.with_imported_source_media(project_id, draft, file)
    saved = save_video_localization(project_id, imported)
    playback_proxy.delete_project_cache(project_id)
    preview_cache.delete_project_cache(project_id)
    return saved


def extract_source_audio(
    project_id: str,
    *,
    commit_transform: (
        Callable[
            [VideoLocalizationDraft],
            VideoLocalizationDraft,
        ]
        | None
    ) = None,
    commit_guard: Callable[
        [Callable[[], VideoLocalizationDraft | None]],
        tuple[bool, VideoLocalizationDraft | None],
    ]
    | None = None,
) -> VideoLocalizationDraft | None:
    if project_store.get_project_repository_revision(project_id) is None:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    source_revision = source_pipeline.source_video_revision(draft)
    extracted = source_pipeline.with_extracted_source_audio(project_id, draft)
    generated_path = extracted.source_media.audio_path
    previous_paths = {draft.source_media.audio_path, draft.stems.original_audio_path}

    def commit_extracted_audio() -> VideoLocalizationDraft | None:
        with _DRAFT_WRITE_LOCK:
            latest = get_video_localization(project_id)
            if latest is None:
                return None
            if source_pipeline.source_video_revision(latest) != source_revision:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_SOURCE_CHANGED",
                    "抽取原音轨期间源视频发生了变化，因此没有写入旧音轨。请基于当前视频重新抽取。",
                )
            merged = latest.model_copy(
                update={
                    "source_media": latest.source_media.model_copy(
                        update={
                            "audio_path": extracted.source_media.audio_path,
                            "audio_sha256": extracted.source_media.audio_sha256,
                            "duration_ms": extracted.source_media.duration_ms,
                            "metadata": extracted.source_media.metadata,
                        }
                    ),
                    "stems": latest.stems.model_copy(
                        update={
                            "original_audio_path": extracted.stems.original_audio_path,
                            "original_audio_sha256": extracted.stems.original_audio_sha256,
                        }
                    ),
                }
            )
            if commit_transform is not None:
                merged = commit_transform(merged)
            return draft_store.save(project_id, merged, intent="content")

    try:
        if commit_guard is not None:
            committed, saved = commit_guard(commit_extracted_audio)
            if not committed:
                if generated_path and generated_path not in previous_paths:
                    Path(generated_path).unlink(missing_ok=True)
                return get_video_localization(project_id)
            return saved
        return commit_extracted_audio()
    except Exception:
        if generated_path and generated_path not in previous_paths:
            Path(generated_path).unlink(missing_ok=True)
        raise


def separate_source_audio(
    project_id: str,
    *,
    commit_transform: Callable[
        [
            VideoLocalizationDraft,
            reference_clips.AutomaticReferenceCandidateResult,
        ],
        VideoLocalizationDraft,
    ]
    | None = None,
    commit_guard: Callable[
        [Callable[[], VideoLocalizationDraft | None]],
        tuple[bool, VideoLocalizationDraft | None],
    ]
    | None = None,
    output_prefix: str | None = None,
    reuse_separation: (source_pipeline.ReusableStemSeparation | None) = None,
    preserve_generated_on_failure: (Callable[[], bool] | None) = None,
) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    source_revision = source_pipeline.source_audio_revision(draft)
    separated_draft = (
        source_pipeline.with_reused_separated_source_audio(
            draft,
            reuse_separation,
        )
        if reuse_separation is not None
        else (
            source_pipeline.with_separated_source_audio(
                project_id,
                draft,
                output_prefix=output_prefix,
            )
            if output_prefix is not None
            else source_pipeline.with_separated_source_audio(
                project_id,
                draft,
            )
        )
    )
    separated_stems = separated_draft.stems
    new_paths = [separated_stems.vocals_clean_path, separated_stems.background_path]
    previous_paths = {draft.stems.vocals_clean_path, draft.stems.background_path}
    try:

        def commit_separated_audio() -> VideoLocalizationDraft | None:
            # Separation can take minutes. Merge only its result into the newest draft so
            # autosaved UI/timeline changes made while stem separation was running are preserved.
            with _DRAFT_WRITE_LOCK:
                latest = get_video_localization(project_id)
                if latest is None:
                    return None
                if source_pipeline.source_audio_revision(latest) != source_revision:
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_SOURCE_AUDIO_CHANGED",
                        "分离人声期间源音轨发生了变化，因此没有写入旧的分轨结果。请基于当前音轨重新分离。",
                    )
                merged = latest.model_copy(update={"stems": separated_stems})
                if commit_transform is not None:
                    merged = commit_transform(merged)
                return draft_store.save(
                    project_id,
                    merged,
                    intent="content",
                )

        if commit_guard is not None:
            committed, saved = commit_guard(commit_separated_audio)
            if not committed:
                saved = get_video_localization(project_id)
        else:
            saved = commit_separated_audio()
    except Exception:
        preserve_generated = bool(preserve_generated_on_failure is not None and preserve_generated_on_failure())
        if not preserve_generated:
            for value in new_paths:
                if value and value not in previous_paths:
                    Path(value).unlink(missing_ok=True)
        raise
    if saved:
        media_assets.cleanup_unreferenced_stems(
            project_id, [saved.stems.vocals_clean_path, saved.stems.background_path]
        )
    return saved


def transcribe_english_source_audio(
    project_id: str,
    operation_id: str = "formal-workflow",
    engine_id: str = source_pipeline.DEFAULT_ENGLISH_ASR_ENGINE_ID,
    source_track_id: source_pipeline.EnglishAsrSourceTrackId | str = "auto",
    source_language: str = "auto",
    is_cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    on_preview: Callable[[str, list[dict]], None] | None = None,
    on_report: Callable[[str, dict], None] | None = None,
    on_atomic_snapshot: (asr_pipeline.AtomicSnapshotCallback | None) = None,
    segmentation_profile_id: str = "generic_zh",
    llm_profile_id: str | None = None,
    vision_profile_id: str | None = None,
    diarization_engine_id: str | None = "auto",
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    expected_submission_binding: dict | None = None,
    finalize_formal_commit: Callable[
        [VideoLocalizationDraft, dict, str], VideoLocalizationDraft
    ] | None = None,
    commit_guard: Callable[
        [Callable[[], VideoLocalizationDraft | None]],
        tuple[bool, VideoLocalizationDraft | None],
    ]
    | None = None,
) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    source_revision = source_pipeline.english_asr_source_revision(draft)
    if expected_submission_binding is not None:
        expected = workflow_submission.WorkflowSubmissionBinding.model_validate(expected_submission_binding)
        current = workflow_submission.capture_asr_submission_binding(draft, profile_id=llm_profile_id)
        if expected != current:
            raise AppException(
                409, "VIDEO_LOCALIZATION_SUBMISSION_CHANGED",
                "排队后音轨、原字幕或模型配置发生了变化，旧听写任务未开始执行。请基于当前内容重新提交。",
            )
    result = source_pipeline.with_english_asr(
        draft,
        engine_id,
        source_track_id,
        source_language=source_language,
        project_id=project_id,
        operation_id=operation_id,
        segmentation_profile_id=segmentation_profile_id,
        llm_profile_id=llm_profile_id,
        vision_profile_id=vision_profile_id,
        progress_callback=on_progress,
        is_cancelled=is_cancelled,
        preview_callback=on_preview,
        report_callback=on_report,
        atomic_snapshot_callback=on_atomic_snapshot,
        diarization_engine_id=diarization_engine_id,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
    )

    def commit_asr_result() -> VideoLocalizationDraft | None:
        # ASR can take minutes. Re-read and merge into the latest draft so autosaved
        # non-ASR state survives, while the source subtitle track is replaced as one
        # version and changed source media can never receive stale text.
        with _DRAFT_WRITE_LOCK:
            latest = get_video_localization(project_id)
            if latest is None:
                return None
            if is_cancelled and is_cancelled():
                return latest
            source_pipeline.ensure_english_asr_source_unchanged(
                latest,
                result,
                expected_source_revision=source_revision,
            )
            if latest.cues != draft.cues or latest.transcription != draft.transcription:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_ASR_TARGET_CHANGED",
                    "听写期间原字幕或时间被修改，已保留你的修改，没有用旧任务结果覆盖。",
                )
            merged = source_pipeline.merge_english_asr_result(latest, result)
            if is_cancelled and is_cancelled():
                return latest
            if finalize_formal_commit is not None:
                merged = finalize_formal_commit(
                    merged, operation_state.english_asr_summary(merged), now_iso(),
                )
            return draft_store.save(project_id, merged, intent="content")

    if commit_guard is not None:
        committed, saved = commit_guard(commit_asr_result)
        if not committed:
            return get_video_localization(project_id)
        return saved
    return commit_asr_result()


def generate_dub_subtitles(
    project_id: str,
    *,
    operation_id: str,
    engine_id: str = dub_subtitles.DEFAULT_ENGINE_ID,
    regeneration_mode: Literal["auto", "full"] = "auto",
    development_execution: (dub_subtitle_workflow.DubSubtitleDevelopmentExecutionConfig | None) = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    on_preview: Callable[[str, list[dict]], None] | None = None,
    on_report: Callable[[str, dict], None] | None = None,
    commit_guard: Callable[
        [Callable[[], object]],
        tuple[bool, object | None],
    ]
    | None = None,
) -> tuple[VideoLocalizationDraft | None, dict]:
    """Run the canonical six-step synthesized-dub subtitle workflow."""

    project = project_store.get_project(project_id)
    if not project:
        raise AppException(
            404,
            "PROJECT_NOT_FOUND",
            "Project not found",
        )
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    generation_text_by_task_id = _dub_generation_text_by_task_id(draft)
    dirty_scope = draft.dub_subtitle_dirty_scope
    has_pending_dirty_scope = bool(
        dirty_scope
        and (
            dirty_scope.affected_clip_ids
            or dirty_scope.affected_ranges
        )
    )
    if (
        regeneration_mode == "auto"
        and development_execution is None
        and draft.dub_subtitles
        and not has_pending_dirty_scope
    ):
        # Scope-less modern results are already committed and have a complete
        # revision fence.  Recompute that cheap fence only, then return a true
        # no-op rather than sending all audio back through ASR.  A differing
        # legacy result cannot safely identify which human captions may be
        # replaced, so it must opt into an explicit full regeneration.
        current_input = dub_subtitles.freeze_workflow_input(
            draft,
            generation_text_by_task_id=generation_text_by_task_id,
            regeneration_mode="full",
        )
        if (
            draft.dub_subtitle_source_revision
            == current_input.source_revision
        ):
            return draft, {
                "stage": "配音字幕已是当前版本，无需重新识别。",
                "stage_id": "commit",
                "execution_mode": "full",
                "regeneration_mode": "none",
                "regeneration_reason": "already_current",
                "workflow_schema_version": (
                    workflow_contracts.DUB_SUBTITLE_WORKFLOW_DEFINITION.schema_version
                ),
                "workflow_id": (
                    workflow_contracts.DUB_SUBTITLE_WORKFLOW_DEFINITION.workflow_id
                ),
                "executed_step_ids": [],
                "reused_step_ids": [],
                "recomputed_reasons": {},
                "dub_subtitle_count": len(draft.dub_subtitles),
                "review_count": sum(
                    item.needs_review for item in draft.dub_subtitles
                ),
            }
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_SCOPE_UNKNOWN",
            "这份历史配音字幕没有保存变更范围；请明确选择全量重新生成。",
        )
    workflow_input = dub_subtitles.freeze_workflow_input(
        draft,
        generation_text_by_task_id=generation_text_by_task_id,
        regeneration_mode=regeneration_mode,
    )
    audio_paths = dub_subtitles.resolve_prepare_audio_paths(
        draft,
        workflow_input,
    )
    committed_draft: VideoLocalizationDraft | None = None

    def commit_result(request):
        def save_result():
            nonlocal committed_draft
            with _DRAFT_WRITE_LOCK:
                latest = get_video_localization(project_id)
                if latest is None:
                    raise AppException(
                        404,
                        "VIDEO_LOCALIZATION_NOT_FOUND",
                        "视频本土化项目不存在。",
                    )
                if is_cancelled is not None and is_cancelled():
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_OPERATION_CANCELLED",
                        "配音字幕任务已取消。",
                    )
                dub_subtitles.ensure_prepare_source_unchanged(
                    latest,
                    workflow_input,
                    generation_text_by_task_id=(generation_text_by_task_id),
                )
                merged, output = dub_subtitles.merge_generated_subtitles(
                    latest,
                    request,
                )
                committed_draft = draft_store.save(
                    project_id,
                    merged,
                    intent="content",
                )
                return output

        if commit_guard is None:
            return save_result()
        committed, output = commit_guard(save_result)
        if not committed or output is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_OPERATION_CANCELLED",
                "配音字幕任务在保存前已取消。",
            )
        return output

    execution = dub_subtitle_workflow.DubSubtitleWorkflowExecution(
        operation_id=operation_id,
        workflow_input=workflow_input,
        engine_id=engine_id,
        audio_paths=audio_paths,
        commit_action=commit_result,
        development=development_execution,
        is_cancelled=is_cancelled,
        on_progress=on_progress,
        on_preview=on_preview,
        on_report=on_report,
    )
    result = execution.run()
    target_step_id = development_execution.target_step_id if development_execution is not None else "commit"
    summary = {
        "stage": (result.step_results.get(target_step_id, {}).get("summary") or "配音字幕子流程已完成"),
        "stage_id": target_step_id,
        "execution_mode": ("development_target" if development_execution is not None else "full"),
        "regeneration_mode": workflow_input.regeneration_scope.mode,
        "regeneration_reason": workflow_input.regeneration_scope.reason,
        "development_target_step_id": (target_step_id if development_execution is not None else None),
        "workflow_schema_version": (workflow_contracts.DUB_SUBTITLE_WORKFLOW_DEFINITION.schema_version),
        "workflow_id": (workflow_contracts.DUB_SUBTITLE_WORKFLOW_DEFINITION.workflow_id),
        "task_stage_groups": (workflow_contracts.dub_subtitle_workflow_summary()["stages"]),
        "task_step_results": result.step_results,
        "task_stage_timings": result.stage_timings,
        "executed_step_ids": result.executed_step_ids,
        "reused_step_ids": result.reused_step_ids,
        "recomputed_reasons": result.recomputed_reasons,
    }
    if result.commit is not None and committed_draft is not None:
        summary.update(
            {
                "dub_subtitle_count": len(
                    committed_draft.dub_subtitles
                ),
                "review_count": sum(
                    item.needs_review
                    for item in committed_draft.dub_subtitles
                ),
                "timing_confidence": "high",
            }
        )
    elif result.segment_subtitles is not None:
        summary.update(
            {
                "dub_subtitle_count": len(result.segment_subtitles.subtitles),
                "review_count": sum(item.needs_review for item in result.segment_subtitles.subtitles),
                "timing_confidence": "high",
            }
        )
    return (
        committed_draft if committed_draft is not None else get_video_localization(project_id),
        summary,
    )


def _dub_generation_text_by_task_id(
    draft: VideoLocalizationDraft,
) -> dict[str, str]:
    task_ids = list(
        dict.fromkeys(
            str(dict(clip).get("task_id") or dict(clip).get("generation_id") or "").strip()
            for clip in draft.timeline_clips
            if dict(clip).get("track_id") == "dub"
        )
    )
    output: dict[str, str] = {}
    for task_id in task_ids:
        if not task_id:
            continue
        task = task_queue.get_task(task_id)
        if task is not None and task.input_text.strip():
            output[task_id] = task.input_text.strip()
    return output


def transcribe_raw_english_source_audio(
    project_id: str,
    engine_id: str = source_pipeline.DEFAULT_ENGLISH_ASR_ENGINE_ID,
    source_track_id: source_pipeline.EnglishAsrSourceTrackId | str = "auto",
    source_language: str = "auto",
    is_cancelled: Callable[[], bool] | None = None,
) -> transcription.TranscribeRawOutput | None:
    """Execute only the first ASR subtask and leave the saved draft untouched."""

    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return source_pipeline.transcribe_raw(
        draft,
        engine_id=engine_id,
        source_track_id=source_track_id,
        source_language=source_language,
        is_cancelled=is_cancelled,
    )


def analyze_initial_english_source_audio(
    project_id: str,
    asr_engine_id: str = source_pipeline.DEFAULT_ENGLISH_ASR_ENGINE_ID,
    diarization_engine_id: str = "auto",
    source_track_id: source_pipeline.EnglishAsrSourceTrackId | str = "auto",
    source_language: str = "auto",
    *,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> source_pipeline.AsrInitialAnalysisSnapshot | None:
    """Run only the two parallel initial ASR branches and leave the draft untouched."""

    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return source_pipeline.analyze_initial_speech(
        draft,
        asr_engine_id=asr_engine_id,
        diarization_engine_id=diarization_engine_id,
        source_track_id=source_track_id,
        source_language=source_language,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
        is_cancelled=is_cancelled,
    )


def understand_english_transcript_snapshot(
    project_id: str,
    snapshot: asr_pipeline.AsrInitialAnalysisSnapshot,
    *,
    upstream_operation_id: str,
    profile_id: str | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> document_understanding_contracts.AsrDocumentUnderstandingResult | None:
    """Understand one fixed joined transcript without research or draft writes."""

    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    request = document_understanding_contracts.AsrDocumentUnderstandingInput.from_joined_transcript(
        snapshot.joined_transcript,
        upstream_operation_id=upstream_operation_id,
        profile_id=profile_id,
        scene_context=draft.scene_context,
    )
    return asr_pipeline.DEFAULT_ASR_PIPELINE.run_document_understanding(
        request,
        context=asr_pipeline.AsrRunContext(is_cancelled=is_cancelled),
    )


def research_english_transcript_snapshot(
    project_id: str,
    understanding: document_understanding_contracts.AsrDocumentUnderstandingResult,
    *,
    upstream_operation_id: str,
    visual_result: visual_evidence.AsrVisualEvidenceResult | None = None,
    visual_evidence_operation_id: str | None = None,
    max_rounds: int = 3,
    max_total_queries: int = 9,
    is_cancelled: Callable[[], bool] | None = None,
) -> research_evidence.AsrResearchEvidenceResult | None:
    """Gather evidence from one fixed understanding result without draft writes."""

    project = project_store.get_project(project_id)
    if not project:
        return None
    if visual_result is not None and visual_evidence_operation_id:
        request = research_evidence.AsrResearchEvidenceInputV2.from_document_and_visual_evidence(
            understanding,
            visual_result,
            upstream_operation_id=upstream_operation_id,
            visual_evidence_operation_id=(visual_evidence_operation_id),
            max_rounds=max_rounds,
            max_total_queries=max_total_queries,
        )
    else:
        request = research_evidence.AsrResearchEvidenceInput.from_document_understanding(
            understanding,
            upstream_operation_id=upstream_operation_id,
            max_rounds=max_rounds,
            max_total_queries=max_total_queries,
        )
    cache_root = settings_store.cache_dir() / "video-localization" / "research"
    return asr_pipeline.DEFAULT_ASR_PIPELINE.run_research_evidence(
        request,
        context=asr_pipeline.AsrRunContext(is_cancelled=is_cancelled),
        cache_dir=str(cache_root),
    )


def normalize_english_transcript_entities_snapshot(
    project_id: str,
    research: research_evidence.AsrResearchEvidenceResult,
    *,
    upstream_operation_id: str,
    profile_id: str | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_preview: Callable[[str, list[dict]], None] | None = None,
) -> entity_normalization.AsrEntityNormalizationResult | None:
    """Normalize one fixed research result without writing the formal draft."""

    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    request = entity_normalization.AsrEntityNormalizationInput.from_research_evidence(
        research,
        upstream_operation_id=upstream_operation_id,
        glossary=draft.glossary,
    )
    if profile_id and profile_id.strip():
        request = request.model_copy(update={"profile_id": profile_id.strip()})
    return asr_pipeline.DEFAULT_ASR_PIPELINE.run_entity_normalization(
        request,
        context=asr_pipeline.AsrRunContext(
            is_cancelled=is_cancelled,
            on_preview=on_preview,
        ),
    )


def review_english_transcript_sections_snapshot(
    project_id: str,
    normalization: entity_normalization.AsrEntityNormalizationResult,
    understanding: document_understanding_contracts.AsrDocumentUnderstandingResult,
    *,
    normalization_operation_id: str,
    understanding_operation_id: str,
    profile_id: str | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> section_review.AsrSectionReviewResult | None:
    """Review one fixed transcript snapshot without writing the formal draft."""

    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    try:
        request = section_review.build_section_review_input(
            normalization,
            understanding,
            normalization_operation_id=normalization_operation_id,
            understanding_operation_id=understanding_operation_id,
            profile_id=profile_id,
            glossary=draft.glossary,
        )
    except ValueError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SECTION_REVIEW_INPUT_MISMATCH",
            str(exc),
        ) from exc
    return asr_pipeline.DEFAULT_ASR_PIPELINE.run_section_review(
        request,
        context=asr_pipeline.AsrRunContext(
            is_cancelled=is_cancelled,
        ),
    )


def adjudicate_english_transcript_review_snapshot(
    project_id: str,
    review: section_review.AsrSectionReviewResult,
    *,
    upstream_operation_id: str,
    profile_id: str | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_preview: Callable[[str, list[dict]], None] | None = None,
) -> review_decisions.AsrReviewDecisionsResult | None:
    """Decide one fixed issue list without writing the formal draft."""

    project = project_store.get_project(project_id)
    if not project:
        return None
    ensure_english_transcript_snapshot_source_current(
        project_id,
        source_track_id=review.input.source_track_id,
        source_audio_sha256=review.input.source_audio_sha256,
    )
    try:
        request = review_decisions.build_review_decisions_input(
            review,
            upstream_operation_id=upstream_operation_id,
            profile_id=profile_id,
        )
    except ValueError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_REVIEW_DECISIONS_INPUT_MISMATCH",
            str(exc),
        ) from exc
    return asr_pipeline.DEFAULT_ASR_PIPELINE.run_review_decisions(
        request,
        context=asr_pipeline.AsrRunContext(
            is_cancelled=is_cancelled,
            on_preview=on_preview,
        ),
    )


def recheck_english_transcript_snapshot(
    project_id: str,
    decisions: review_decisions.AsrReviewDecisionsResult,
    understanding: document_understanding_contracts.AsrDocumentUnderstandingResult,
    *,
    upstream_operation_id: str,
    understanding_operation_id: str,
    profile_id: str | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> whole_recheck.AsrWholeRecheckResult | None:
    """Re-read one fixed reviewed transcript without formal draft writes."""

    project = project_store.get_project(project_id)
    if not project:
        return None
    ensure_english_transcript_snapshot_source_current(
        project_id,
        source_track_id=decisions.input.source_track_id,
        source_audio_sha256=decisions.input.source_audio_sha256,
    )
    try:
        request = whole_recheck.build_whole_recheck_input(
            decisions,
            understanding,
            upstream_operation_id=upstream_operation_id,
            understanding_operation_id=understanding_operation_id,
            profile_id=profile_id,
        )
    except ValueError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_WHOLE_RECHECK_INPUT_MISMATCH",
            str(exc),
        ) from exc
    return asr_pipeline.DEFAULT_ASR_PIPELINE.run_whole_recheck(
        request,
        context=asr_pipeline.AsrRunContext(
            is_cancelled=is_cancelled,
        ),
    )


def check_english_transcript_quality_snapshot(
    project_id: str,
    recheck: whole_recheck.AsrWholeRecheckResult,
    *,
    upstream_operation_id: str,
) -> transcript_quality_gate.AsrTranscriptQualityGateResult | None:
    """Run the local pre-alignment gate without writing the formal draft."""

    project = project_store.get_project(project_id)
    if not project:
        return None
    ensure_english_transcript_snapshot_source_current(
        project_id,
        source_track_id=recheck.input.source_track_id,
        source_audio_sha256=recheck.input.source_audio_sha256,
    )
    try:
        request = transcript_quality_gate.build_input(
            recheck,
            upstream_operation_id=upstream_operation_id,
            source_track_id=recheck.input.source_track_id,
            source_audio_sha256=recheck.input.source_audio_sha256,
            segments=recheck.input.segments,
        )
    except ValueError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_INPUT_MISMATCH",
            str(exc),
        ) from exc
    return asr_pipeline.DEFAULT_ASR_PIPELINE.run_transcript_quality_gate(request)


def ensure_english_transcript_snapshot_source_current(
    project_id: str,
    *,
    source_track_id: str,
    source_audio_sha256: str,
) -> None:
    """Reject a development transcript snapshot after its audio changed."""

    draft = get_video_localization(project_id)
    if draft is None:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    try:
        source_path, resolved_track_id = source_pipeline.resolve_english_asr_source(
            draft,
            source_track_id,
        )
        current_sha256 = media_assets.file_sha256(source_path)
    except (OSError, ValueError) as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_REVIEW_SNAPSHOT_SOURCE_UNAVAILABLE",
            "当前音轨已无法与开发快照核对。",
        ) from exc
    if resolved_track_id != source_track_id or current_sha256 != source_audio_sha256:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_REVIEW_SNAPSHOT_STALE",
            "当前音轨已经变化，不能继续使用旧的校对开发快照。",
        )


def inspect_visual_evidence_snapshot(
    project_id: str,
    understanding: document_understanding_contracts.AsrDocumentUnderstandingResult,
    *,
    upstream_operation_id: str,
    frame_dir: str | Path,
    profile_id: str | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> visual_evidence.AsrVisualEvidenceResult | None:
    """Inspect bounded video frames without naming people or changing text."""

    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    video_path = media_health.inspect_project_media(draft).paths.source_video
    video_sha256 = str(draft.source_media.content_sha256 or "").strip()
    video_duration_ms = int(draft.source_media.duration_ms or 0)
    if video_path is None or not video_sha256 or video_duration_ms <= 0:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_SOURCE_UNAVAILABLE",
            "当前项目没有可用于画面取证的源视频。",
        )
    if media_assets.file_sha256(video_path) != video_sha256:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_SOURCE_MISMATCH",
            "源视频内容已变化，请重新导入或刷新上游结果。",
        )
    request = visual_evidence.AsrVisualEvidenceInput.from_document_understanding(
        understanding,
        upstream_operation_id=upstream_operation_id,
        video_sha256=video_sha256,
        video_duration_ms=video_duration_ms,
        video_frame_rate=float(draft.source_media.frame_rate or 30.0),
        profile_id=profile_id,
    )
    return asr_pipeline.DEFAULT_ASR_PIPELINE.run_visual_evidence(
        request,
        source_video_path=str(video_path),
        frame_dir=str(frame_dir),
        context=asr_pipeline.AsrRunContext(is_cancelled=is_cancelled),
    )


def import_subtitles(
    project_id: str, kind: str, request: VideoLocalizationSubtitleImportRequest
) -> VideoLocalizationDraft | None:
    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        if kind == "zh":
            _ensure_localization_track_editable(draft)
        next_draft = subtitles.import_srt(
            draft,
            kind,
            request.srt_text,
            update_timing=request.update_timing,
            overwrite_tts=request.overwrite_tts,
        )
        return draft_store.save(project_id, next_draft, intent="content")


def create_reference_clips_from_cues(project_id: str) -> VideoLocalizationDraft | None:
    saved, _result = run_automatic_reference_candidates(project_id)
    return saved


def run_automatic_reference_candidates(
    project_id: str,
    *,
    commit_transform: Callable[
        [VideoLocalizationDraft],
        VideoLocalizationDraft,
    ]
    | None = None,
    commit_guard: Callable[
        [Callable[[], VideoLocalizationDraft | None]],
        tuple[bool, VideoLocalizationDraft | None],
    ]
    | None = None,
    operation_id: str | None = None,
    reusable_media: tuple[
        reference_clips.AutomaticReferenceMedia,
        ...,
    ] = (),
    preserve_generated_on_failure: (Callable[[], bool] | None) = None,
) -> tuple[
    VideoLocalizationDraft | None,
    reference_clips.AutomaticReferenceCandidateResult | None,
]:
    project = project_store.get_project(project_id)
    if not project:
        return None, None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    source_revision = reference_clips.automatic_reference_candidate_revision(draft)
    candidate_result = reference_clips.build_automatic_reference_candidates(
        project_id,
        draft,
        operation_id=operation_id,
        reusable_media=reusable_media,
    )
    candidate_draft = speakers.reconcile_speakers(candidate_result.draft)
    generated_paths = candidate_result.generated_paths

    def commit_candidates() -> VideoLocalizationDraft | None:
        with _DRAFT_WRITE_LOCK:
            latest = get_video_localization(project_id)
            if latest is None:
                return None
            if reference_clips.automatic_reference_candidate_revision(latest) != source_revision:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_REFERENCE_INPUT_CHANGED",
                    "生成参考音期间 cue、说话人或参考音发生了变化，请重新执行。",
                )
            merged = latest.model_copy(
                update={
                    "reference_clips": (candidate_draft.reference_clips),
                    "cues": candidate_draft.cues,
                    "speakers": candidate_draft.speakers,
                }
            )
            if commit_transform is not None:
                merged = commit_transform(
                    merged,
                    candidate_result,
                )
            return draft_store.save(
                project_id,
                merged,
                intent="content",
            )

    try:
        if commit_guard is not None:
            committed, saved = commit_guard(commit_candidates)
            if not committed:
                saved = get_video_localization(project_id)
        else:
            saved = commit_candidates()
    except Exception:
        preserve_generated = bool(preserve_generated_on_failure is not None and preserve_generated_on_failure())
        if not preserve_generated:
            for path in generated_paths:
                path.unlink(missing_ok=True)
        raise
    if saved is not None:
        media_assets.cleanup_unreferenced_automatic_reference_clips(
            project_id,
            [clip.audio_path for clip in saved.reference_clips],
        )
    return saved, candidate_result


def update_cue(project_id: str, cue_id: str, patch: VideoLocalizationCueUpdate) -> VideoLocalizationDraft | None:
    target_fields = {"zh_localized_subtitle_text", "tts_recommended_text"}
    updates_target_track = bool(target_fields.intersection(patch.model_fields_set))
    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        if (
            patch.confirm_timing
            and patch.timing_confirmation_method == "asr_vad_verified"
        ):
            evidence = patch.timing_confirmation_evidence
            current_audio_sha256 = (
                quality_gate.current_transcription_alignment_audio_sha256(draft)
            )
            if (
                evidence is None
                or current_audio_sha256 is None
                or current_audio_sha256 != evidence.alignment_audio_sha256
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_CUE_TIMING_EVIDENCE_STALE",
                    "分离人声音轨已经变化或不可用，请重新执行 ASR/VAD 校核。",
                )
        if updates_target_track:
            _ensure_localization_track_editable(draft)
        next_draft = cue_tools.with_updated_cue(draft, cue_id, patch)
        return draft_store.save(
            project_id,
            speakers.reconcile_speakers(next_draft),
            intent="content",
        )


def apply_localized_binding_repair(
    project_id: str,
    request: BindingRepairRequest,
) -> VideoLocalizationDraft | None:
    """Commit a source-only correction against the current project and audio."""
    with _DRAFT_WRITE_LOCK:
        draft = get_video_localization(project_id)
        if draft is None:
            return None
        if any(op.status in {"queued", "running"} for op in draft.operations) or any(
            task.status in {"prepared", "queued", "running"} for task in draft.tts_tasks
        ):
            raise AppException(409, "VIDEO_LOCALIZATION_BINDING_REPAIR_TASK_ACTIVE", "项目仍有运行任务，暂不能修改来源绑定。")
        if quality_gate.current_transcription_alignment_audio_sha256(draft) != request.audio_sha256:
            raise AppException(409, "VIDEO_LOCALIZATION_BINDING_REPAIR_AUDIO_CHANGED", "分离人声音轨已变化或不可用，请重新核对。")
        try:
            updated = binding_repair.repair_bindings(draft, request)
        except ValueError as error:
            raise AppException(
                409, "VIDEO_LOCALIZATION_BINDING_REPAIR_UNSAFE",
                "来源绑定不满足安全修正条件，原项目未改变。",
                {"reason": str(error)},
            ) from error
        if updated == draft:
            return draft
        revision = project_store.get_project_repository_revision(project_id)
        if str(revision or "") != request.expected_project_revision:
            raise AppException(409, "VIDEO_LOCALIZATION_BINDING_REPAIR_PROJECT_CHANGED", "项目已变化，请重新核对后提交修正。")
        updated._repository_revision = draft._repository_revision
        before_by_id = {item.subtitle_id: item for item in draft.localized_subtitles}
        updated.source_binding_repairs = [*draft.source_binding_repairs, BindingRepairReceipt(
            request=request,
            changed_subtitle_ids=[item.subtitle_id for item in updated.localized_subtitles
                                  if item != before_by_id.get(item.subtitle_id)],
        )]
        try:
            return draft_store.save(project_id, updated, intent="content")
        except ProjectRevisionConflict as error:
            raise AppException(
                409, "VIDEO_LOCALIZATION_BINDING_REPAIR_PROJECT_CHANGED",
                "保存时项目已变化，原修正未写入，请重新核对后提交。",
            ) from error


def apply_asr_source_repair(
    project_id: str,
    request: AsrSourceRepairRequest,
) -> VideoLocalizationDraft | None:
    """Atomically exclude evidenced ASR errors without rewriting localization."""
    fingerprint = request.fingerprint()
    with _DRAFT_WRITE_LOCK:
        draft = get_video_localization(project_id)
        if draft is None:
            return None
        transcript = draft.transcription
        for receipt in transcript.asr_source_repairs if transcript else []:
            if receipt.request_id != request.request_id:
                continue
            if receipt.request_fingerprint != fingerprint:
                raise AppException(409, "VIDEO_LOCALIZATION_ASR_REPAIR_REQUEST_CONFLICT", "同一修正请求不能更改内容。")
            if transcript.revision_id != receipt.after_revision_id:
                raise AppException(409, "VIDEO_LOCALIZATION_ASR_REPAIR_SUPERSEDED", "ASR 已有后续修改，请读取当前版本。")
            return draft
        revision = project_store.get_project_repository_revision(project_id)
        if str(revision or "") != request.expected_project_revision:
            raise AppException(409, "VIDEO_LOCALIZATION_ASR_REPAIR_PROJECT_CHANGED", "项目已变化，请重新核对后提交修正。")
        if any(op.status in {"queued", "running"} for op in draft.operations) or any(
            task.status in {"prepared", "queued", "running"} for task in draft.tts_tasks
        ):
            raise AppException(409, "VIDEO_LOCALIZATION_ASR_REPAIR_TASK_ACTIVE", "项目仍有运行任务，请在任务结束后修正来源。")
        if quality_gate.current_transcription_alignment_audio_sha256(draft) != request.audio_sha256:
            raise AppException(409, "VIDEO_LOCALIZATION_ASR_REPAIR_AUDIO_CHANGED", "分离人声音轨已变化或不可用，请重新核对。")
        try:
            updated, receipt = asr_source_repair.repair_asr_source(
                draft, request, new_revision_id=fingerprint[:12]
            )
        except ValueError as error:
            raise AppException(
                409, "VIDEO_LOCALIZATION_ASR_REPAIR_UNSAFE",
                "当前来源或绑定不满足安全修正条件；原项目未改变。",
                {"reason": str(error)},
            ) from error
        receipt = receipt.model_copy(update={"request_fingerprint": fingerprint})
        updated.transcription.asr_source_repairs = [*transcript.asr_source_repairs, receipt]
        return draft_store.save(project_id, updated, intent="content")


def apply_asr_vad_source_timing_correction(
    project_id: str,
    request: VideoLocalizationAsrVadTimingCorrectionRequest,
) -> VideoLocalizationDraft | None:
    """Persist one all-or-nothing separated-vocals source timing correction."""

    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None
        current_project_revision = project_store.get_project_repository_revision(
            project_id
        )
        if str(current_project_revision or "") != request.expected_project_revision:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_SOURCE_TIMING_PROJECT_CHANGED",
                "项目内容已经变化，请重新读取当前字幕和词级时间后再提交校正。",
            )
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        current_audio_sha256 = quality_gate.current_transcription_alignment_audio_sha256(
            draft
        )
        if current_audio_sha256 is None or current_audio_sha256 != request.audio_sha256:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_SOURCE_TIMING_EVIDENCE_STALE",
                "分离人声音轨已经变化或不可用，请重新执行 ASR/VAD 核对。",
            )
        next_draft = cue_tools.apply_asr_vad_source_timing_correction(
            draft, request
        )
        return draft_store.save(
            project_id,
            speakers.reconcile_speakers(next_draft),
            intent="content",
        )


def update_localized_subtitle(
    project_id: str,
    subtitle_id: str,
    patch: VideoLocalizationSubtitleCueUpdate,
) -> VideoLocalizationDraft | None:
    saved, _ = _update_localized_subtitle(
        project_id,
        subtitle_id,
        patch,
        intent="editorial" if patch.model_fields_set.issubset({"start_ms", "end_ms"}) else "content",
    )
    return saved


def update_localized_subtitle_interactive(
    project_id: str,
    subtitle_id: str,
    patch: VideoLocalizationSubtitleCueUpdate,
) -> tuple[VideoLocalizationDraft | None, set[str]]:
    """Commit one editor gesture without synchronously projecting a full snapshot."""

    return _update_localized_subtitle(
        project_id,
        subtitle_id,
        patch,
        intent="editorial" if patch.model_fields_set.issubset({"start_ms", "end_ms"}) else "interactive_content",
    )


def _update_localized_subtitle(
    project_id: str,
    subtitle_id: str,
    patch: VideoLocalizationSubtitleCueUpdate,
    *,
    intent: draft_store.DraftWriteIntent,
) -> tuple[VideoLocalizationDraft | None, set[str]]:
    generation_task_ids_to_cancel: list[str] = []
    affected_clip_ids: set[str] = set()
    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None, affected_clip_ids
        draft = draft_store.from_project(project, normalize_for_read=intent != "editorial")
        _ensure_localization_track_editable(draft)
        affected_clip_ids.update(
            _timeline_clip_ids_targeting_subtitle(draft, subtitle_id)
        )
        next_draft = subtitles.with_updated_localized_subtitle(draft, subtitle_id, patch)
        if intent == "editorial" and next_draft != draft:
            next_draft = dub_subtitles.reconcile_source_lifecycle(draft, next_draft)
        affected_clip_ids.update(
            _timeline_clip_ids_targeting_subtitle(next_draft, subtitle_id)
        )
        previous_by_workflow = {item.workflow_id: item for item in draft.tts_tasks}
        generation_task_ids_to_cancel = [
            item.generation_task_id
            for item in next_draft.tts_tasks
            if (
                item.generation_task_id
                and item.status == "cancelled"
                and (
                    (previous := previous_by_workflow.get(item.workflow_id)) is not None
                    and previous.status not in {"success", "failed", "cancelled"}
                )
            )
        ]
        saved = draft_store.save(project_id, next_draft, intent=intent)
    for generation_task_id in generation_task_ids_to_cancel:
        _cancel_tts_generation_task(generation_task_id)
    return saved, affected_clip_ids


def _timeline_clip_ids_targeting_subtitle(
    draft: VideoLocalizationDraft,
    subtitle_id: str,
) -> set[str]:
    result: set[str] = set()
    for raw_clip in draft.timeline_clips:
        clip = dict(raw_clip)
        target_ids = {
            str(value)
            for value in clip.get("target_subtitle_ids") or []
            if value
        }
        if (
            subtitle_id not in target_ids
            and (
                target_ids
                or str(clip.get("subtitle_id") or "") != subtitle_id
            )
        ):
            continue
        clip_id = str(clip.get("clip_id") or "")
        if clip_id:
            result.add(clip_id)
    return result


def update_localized_spoken_segment(
    project_id: str,
    segment_id: str,
    patch: VideoLocalizationSpokenSegmentUpdate,
) -> VideoLocalizationDraft | None:
    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        _ensure_localization_track_editable(draft)
        updated = False
        segments = []
        for segment in draft.localized_spoken_segments:
            if segment.segment_id != segment_id:
                segments.append(segment)
                continue
            segments.append(segment.model_copy(update={"text": patch.text}))
            updated = True
        if not updated:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_SPOKEN_SEGMENT_NOT_FOUND",
                "本土化配音台词不存在。",
            )
        changed = draft.model_copy(
            update={"localized_spoken_segments": segments}
        )
        changed = localization_tracks.invalidate_formal_quality_binding(
            changed
        )
        return draft_store.save(
            project_id,
            changed,
            intent="content",
        )


def review_dub_subtitles(
    project_id: str,
    *,
    source_revision: str,
    cues: list[dict[str, Any]],
) -> VideoLocalizationDraft | None:
    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        reviewed = dub_subtitles.apply_reviewed_subtitles(
            draft,
            source_revision=source_revision,
            cues=cues,
        )
        return draft_store.save(project_id, reviewed, intent="content")


def normalize_dub_subtitle_display_numbers(
    project_id: str,
    *,
    source_revision: str,
) -> tuple[VideoLocalizationDraft | None, int]:
    """Apply current display-number CQC repairs without changing audio/timing."""

    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None, 0
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        normalized, changed_count = dub_subtitles.normalize_display_number_forms(
            draft,
            source_revision=source_revision,
        )
        if not changed_count:
            return draft, 0
        return (
            draft_store.save(project_id, normalized, intent="content"),
            changed_count,
        )


def _cancel_tts_generation_task(generation_task_id: str) -> None:
    """Best-effort cancellation after the draft tombstone is durable."""

    if generation_task_id.startswith("longform:"):
        from app.services import longform_queue

        try:
            longform_queue.cancel_longform(generation_task_id.removeprefix("longform:"))
        except AppException:
            pass
        return
    task_queue.cancel_task(generation_task_id)


def delete_source_cue(project_id: str, cue_id: str) -> VideoLocalizationDraft | None:
    return _commit_subtitle_mutation(
        project_id,
        lambda draft: subtitle_mutations.delete_source_cue(draft, cue_id),
        not_found_code="VIDEO_LOCALIZATION_SOURCE_CUE_NOT_FOUND",
        invalid_code="VIDEO_LOCALIZATION_SOURCE_CUE_DELETE_INVALID",
    )


def delete_localized_subtitle(project_id: str, subtitle_id: str) -> VideoLocalizationDraft | None:
    return _commit_subtitle_mutation(
        project_id,
        lambda draft: subtitle_mutations.delete_localized_subtitle(draft, subtitle_id),
        not_found_code="VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_NOT_FOUND",
        invalid_code="VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_DELETE_INVALID",
    )


def merge_source_cues(
    project_id: str,
    cue_ids: list[str],
    *,
    survivor_cue_id: str | None = None,
) -> VideoLocalizationDraft | None:
    return _commit_subtitle_mutation(
        project_id,
        lambda draft: subtitle_mutations.merge_source_cues(
            draft,
            cue_ids,
            survivor_cue_id=survivor_cue_id,
        ),
        not_found_code="VIDEO_LOCALIZATION_SOURCE_CUE_NOT_FOUND",
        invalid_code="VIDEO_LOCALIZATION_SOURCE_CUE_MERGE_INVALID",
    )


def split_source_cue(
    project_id: str,
    cue_id: str,
    replacements: list[VideoLocalizationCue],
) -> VideoLocalizationDraft | None:
    return _commit_subtitle_mutation(
        project_id,
        lambda draft: subtitle_mutations.split_source_cue(draft, cue_id, replacements),
        not_found_code="VIDEO_LOCALIZATION_SOURCE_CUE_NOT_FOUND",
        invalid_code="VIDEO_LOCALIZATION_SOURCE_CUE_SPLIT_INVALID",
    )


def split_localized_subtitle(
    project_id: str,
    subtitle_id: str,
    children: list[VideoLocalizationSubtitleCue],
    *,
    source_word_ids_by_subtitle_id: dict[str, list[str]] | None = None,
) -> VideoLocalizationDraft | None:
    return _commit_subtitle_mutation(
        project_id,
        lambda draft: subtitle_mutations.split_localized_subtitle(
            draft,
            subtitle_id,
            children,
            source_word_ids_by_subtitle_id=source_word_ids_by_subtitle_id,
        ),
        not_found_code="VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_NOT_FOUND",
        invalid_code="VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_SPLIT_INVALID",
    )


def _commit_subtitle_mutation(
    project_id: str,
    mutate: Callable[[VideoLocalizationDraft], subtitle_mutations.SubtitleDraftMutationResult],
    *,
    not_found_code: str,
    invalid_code: str,
) -> VideoLocalizationDraft | None:
    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        _ensure_localization_track_editable(draft)
        try:
            result = mutate(draft)
        except KeyError as exc:
            raise AppException(
                404,
                not_found_code,
                "要修改的字幕片段不存在，请刷新项目后重试",
                {"reason": str(exc)},
            ) from exc
        except ValueError as exc:
            raise AppException(
                400,
                invalid_code,
                str(exc),
            ) from exc
        invalidated = localization_tracks.invalidate_formal_quality_binding(
            result.draft
        )
        return draft_store.save(project_id, invalidated, intent="content")


def clear_subtitles(project_id: str, kind: str) -> VideoLocalizationDraft | None:
    def clear(current: VideoLocalizationDraft) -> VideoLocalizationDraft:
        if kind == "en":
            if any(
                operation.kind == "english_asr" and operation.status in {"queued", "running"}
                for operation in current.operations
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_SUBTITLE_CLEAR_BLOCKED",
                    "字幕听写仍在运行，请先取消或等待完成后再清空 ASR 字幕轨。",
                )
            return source_pipeline.without_english_asr(current)
        _ensure_localization_track_editable(current)
        return subtitles.without_localized_subtitle_track(current)

    return update_video_localization_atomic(project_id, clear, intent="content")


def _ensure_localization_track_editable(draft: VideoLocalizationDraft) -> None:
    if _localization_draft_is_active(draft):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_TRACK_BUSY",
            "本土化字幕正在生成，请等待完成或先取消任务。",
        )


def _localization_draft_is_active(draft: VideoLocalizationDraft) -> bool:
    return any(
        operation.kind == "localization_draft" and operation.status in {"queued", "running"}
        for operation in draft.operations
    )


def create_speaker(project_id: str, payload: VideoLocalizationSpeakerCreate) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, speakers.with_created_speaker(draft, payload))


def update_speaker(
    project_id: str, speaker_id: str, payload: VideoLocalizationSpeakerUpdate
) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, speakers.with_updated_speaker(draft, speaker_id, payload))


def lock_localization_source_snapshot(
    project_id: str,
) -> localization_source.LocalizationSourceLockResult | None:
    """Lock the current ASR artifacts without changing formal project data."""

    if project_store.get_project(project_id) is None:
        return None
    draft = get_video_localization(project_id)
    if draft is None:
        return None
    try:
        request = localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(draft)
        return localization_source.DEFAULT_LOCALIZATION_PIPELINE.lock_source(request)
    except ValueError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SOURCE_LOCK_INVALID",
            str(exc),
        ) from exc


def run_localization_v3_draft(
    project_id: str,
    *,
    operation_id: str,
    expected_submission_binding: dict | None = None,
    source_language: str = "auto",
    target_language: str | None = None,
    profile_id: str | None = None,
    localization_requirements_id: str | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    on_preview: Callable[[str, list[dict]], None] | None = None,
    on_report: Callable[[str, dict], None] | None = None,
    on_atomic_result: Callable[[str, object], None] | None = None,
    formal_retry_source_operation_ids: list[str] | None = None,
    formal_retry_checkpoint_root: Path | None = None,
    development_execution: (localization_workflow_execution.LocalizationDevelopmentExecutionConfig | None) = None,
    finalize_formal_commit: (
        Callable[[VideoLocalizationDraft, dict, str], VideoLocalizationDraft]
        | None
    ) = None,
    commit_guard: Callable[
        [Callable[[], object]],
        tuple[bool, object | None],
    ]
    | None = None,
) -> tuple[VideoLocalizationDraft | None, dict]:
    """Run the document-first localization workflow and commit dual tracks."""

    if project_store.get_project(project_id) is None:
        return None, {}
    draft = get_video_localization(project_id)
    if draft is None:
        return None, {}
    resolved_target_language = str(target_language or draft.language_config.target_language).strip() or "zh-Hans"
    _ = source_language
    try:
        if expected_submission_binding is not None:
            expected = workflow_submission.WorkflowSubmissionBinding.model_validate(expected_submission_binding)
            current = workflow_submission.capture_submission_binding(
                draft, profile_id=profile_id, requirements_id=localization_requirements_id,
            )
            if expected != current:
                raise AppException(
                    409, "VIDEO_LOCALIZATION_SUBMISSION_CHANGED",
                    "排队后源字幕、模型配置或流程版本发生了变化，任务未调用模型，也没有覆盖字幕。请基于当前内容重新提交。",
                )
        requirements_profile = localization_requirements.resolve_localization_requirements(localization_requirements_id)
        if resolved_target_language != requirements_profile.target_language:
            raise ValueError(
                "本次目标语言与本土化要求配置不一致："
                f"{resolved_target_language} != "
                f"{requirements_profile.target_language}"
            )
        required_ai_phases = None
        if development_execution is not None:
            plan = workflow_graph.WorkflowGraph(workflow_contracts.LOCALIZATION_WORKFLOW_V3_DEFINITION).plan_for(
                development_execution.target_step_id
            )
            required_ai_phases = {
                phase
                for step_id in plan.required_step_ids
                if (phase := localization_ai_policy.LOCALIZATION_AI_PHASE_BY_STEP_ID.get(step_id)) is not None
            }
        policy = (
            None
            if required_ai_phases == set()
            else localization_ai_policy.resolve_localization_ai_policy(
                settings_store.get(),
                fallback_profile_id=profile_id,
                required_phases=required_ai_phases,
            )
        )
        ledger = workflow_ledger.LocalizationWorkflowLedger(
            operation_id,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
            on_report=on_report,
            on_atomic_result=on_atomic_result,
            definition=(workflow_contracts.LOCALIZATION_WORKFLOW_V3_DEFINITION),
        )
        route_by_step = {}
        if policy is not None:
            route_by_step = {
                step_id: route.model_dump(mode="json")
                for step_id, phase in (localization_ai_policy.LOCALIZATION_AI_PHASE_BY_STEP_ID.items())
                for route in policy.routes
                if route.phase == phase
            }
        source_lock_seed = (
            localization_source.DEFAULT_LOCALIZATION_PIPELINE.lock_source(
                localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(draft)
            )
            if expected_submission_binding is not None
            else lock_localization_source_snapshot(project_id)
            if (development_execution is not None or bool(formal_retry_source_operation_ids))
            else None
        )
        formal_retry = (
            localization_formal_retry.LocalizationFormalRetryRecovery(
                formal_retry_checkpoint_root,
                project_id=project_id,
                source_operation_ids=(formal_retry_source_operation_ids or []),
            )
            if (formal_retry_source_operation_ids and formal_retry_checkpoint_root is not None)
            else None
        )
        behavior_contexts = {
            **route_by_step,
            "lock_localization_source": (
                {
                    "source_fingerprint": (source_lock_seed.source_fingerprint),
                }
                if source_lock_seed is not None
                else {}
            ),
            "lock_localization_context_intent": {
                "requirements_profile_id": (requirements_profile.profile_id),
                "requirements_profile_version": (requirements_profile.profile_version),
                "requirements_fingerprint": (requirements_profile.fingerprint),
            },
            "align_localization_semantics": {
                "encoder_id": "sentence-transformers/LaBSE",
                "aligner_version": (localization_semantic_alignment.ALIGNER_VERSION),
            },
        }
        execution = localization_workflow_execution.LocalizationWorkflowExecution(
            ledger,
            project_id=project_id,
            operation_id=operation_id,
            development=development_execution,
            behavior_contexts=behavior_contexts,
        )
        operation_id_for = execution.operation_id_for
        source_lock = execution.run(
            "lock_localization_source",
            "正在锁定最终英文 ASR、逐词时间和说话人资料。",
            lambda: (
                (
                    formal_retry.recover(
                        "lock_localization_source",
                        current_result=source_lock_seed,
                    )
                    if formal_retry is not None and source_lock_seed is not None
                    else None
                )
                or source_lock_seed
                or lock_localization_source_snapshot(project_id)
            ),
            localization_source.project_localization_source_lock_step_result,
        )
        if source_lock is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_DRAFT_NOT_FOUND",
                "没有找到可用于本土化的最终 ASR 字幕。",
            )

        def build_context_intent():
            current = localization_context_intent.LocalizationContextIntentPipeline().lock(
                localization_context_intent.build_localization_context_intent_input(
                    draft,
                    source_lock=source_lock,
                    upstream_operation_id=operation_id_for("lock_localization_source"),
                    requirements_profile=requirements_profile,
                    selection_source=("request" if localization_requirements_id else "project_default"),
                )
            )
            if formal_retry is None:
                return current
            return (
                formal_retry.recover(
                    "lock_localization_context_intent",
                    expected_lineage={"source_fingerprint": (source_lock.source_fingerprint)},
                    is_reusable=lambda candidate: (
                        isinstance(
                            candidate,
                            localization_context_intent
                            .LocalizationContextIntentResult,
                        )
                        and candidate.context_intent_fingerprint
                        == current.context_intent_fingerprint
                    ),
                )
                or current
            )

        context_intent = execution.run(
            "lock_localization_context_intent",
            "正在锁定中文观众、人物表达和双轨交付目标。",
            build_context_intent,
            (localization_context_intent.project_localization_context_intent_step_result),
        )
        if context_intent is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_CONTEXT_INTENT_INVALID",
                "无法固定本次本土化要求。",
            )
        def batch_journal_for(step_id: str) -> DevelopmentLlmBatchReplay | None:
            if development_execution is None and on_atomic_result is None:
                return None
            return DevelopmentLlmBatchReplay(
                root=development_execution.snapshot_root if development_execution is not None else None,
                project_id=project_id,
                development_session_id=(development_execution.development_session_id
                                        if development_execution is not None else operation_id),
                step_id_prefix=f"{step_id}.llm_batch",
                candidate_recovery_store=(
                    DevelopmentCandidateReceiptStore(
                        root=development_execution.snapshot_root / "candidate-recovery",
                        project_id=project_id,
                        development_session_id=development_execution.development_session_id,
                        development_mode=True,
                    )
                    if step_id == "analyze_localization_document"
                    and development_execution is not None
                    and development_execution.recover_verified_candidates
                    else None
                ),
                retry_rejected_execution_id=operation_id if development_execution is not None else None,
                retry_unknown_batch_step_id=(
                    development_execution.retry_unknown_batch_step_id
                    if development_execution is not None
                    and development_execution.target_step_id == step_id else None
                ),
                # Formal diagnostics may write; only development reads candidates.
                checkpoint_writer=on_atomic_result if development_execution is None else None,
            )

        brief_batch_journal = batch_journal_for("analyze_localization_document")
        brief = execution.run(
            "analyze_localization_document",
            "正在通读全文，理解篇章、人物、情绪、事实和术语关系。",
            lambda: (
                (
                    formal_retry.recover(
                        "analyze_localization_document",
                        expected_lineage={
                            "source_fingerprint": (source_lock.source_fingerprint),
                            "context_intent_fingerprint": (context_intent.context_intent_fingerprint),
                        },
                        expected_route=(policy.route("document_understanding").model_dump(mode="json")),
                    )
                    if formal_retry is not None
                    else None
                )
                or localization_document_brief.analyze_localization_document(
                    localization_document_brief.LocalizationDocumentBriefInput(
                        source_operation_id=operation_id_for("lock_localization_source"),
                        context_operation_id=operation_id_for("lock_localization_context_intent"),
                        source_lock=source_lock,
                        context_intent=context_intent,
                        route=policy.route("document_understanding"),
                    ),
                    **({"batch_journal": brief_batch_journal} if brief_batch_journal is not None else {}),
                )
            ),
            (localization_document_brief.project_localization_document_brief_step_result),
        )
        current_draft = get_video_localization(project_id)
        source_video_path = (
            Path(current_draft.source_media.video_path)
            if current_draft is not None
            and current_draft.source_media is not None
            and current_draft.source_media.video_path
            else None
        )
        development_visual_dir = execution.development_artifact_dir_for("collect_localization_visual_evidence_v3")
        frame_dir = (
            development_visual_dir / "frames"
            if development_visual_dir is not None
            else media_assets.visual_evidence_frame_dir(
                project_id,
                operation_id,
            )
            / "localization-v3"
        )
        research, visual = execution.run_parallel(
            [
                (
                    "collect_localization_research_evidence_v3",
                    "正在只查询全文理解留下的必要问题。",
                    lambda: (
                        (
                            formal_retry.recover(
                                "collect_localization_research_evidence_v3",
                                expected_lineage={"brief_fingerprint": (brief.result_fingerprint)},
                            )
                            if formal_retry is not None
                            else None
                        )
                        or localization_document_evidence.collect_localization_document_research(brief)
                    ),
                    (localization_document_evidence.project_localization_document_research_result),
                    {"brief": brief},
                ),
                (
                    "collect_localization_visual_evidence_v3",
                    "正在只截取确实需要查看的原视频画面。",
                    lambda: (
                        (
                            formal_retry.recover(
                                "collect_localization_visual_evidence_v3",
                                expected_lineage={"brief_fingerprint": (brief.result_fingerprint)},
                            )
                            if formal_retry is not None
                            else None
                        )
                        or localization_document_evidence.collect_localization_document_visuals(
                            brief,
                            source_lock,
                            source_video_path=source_video_path,
                            frame_dir=frame_dir,
                        )
                    ),
                    (localization_document_evidence.project_localization_document_visual_result),
                    {"brief": brief},
                ),
            ],
            thread_name_prefix="localization-v3-evidence",
        )
        evidence_batch_journal = batch_journal_for("adjudicate_localization_evidence_v3")
        evidence = execution.run(
            "adjudicate_localization_evidence_v3",
            "正在把资料和画面整理成保守、可追溯的创作约束。",
            lambda: (
                (
                    formal_retry.recover(
                        "adjudicate_localization_evidence_v3",
                        expected_lineage={"brief_fingerprint": brief.result_fingerprint},
                    )
                    if formal_retry is not None
                    and {
                        "collect_localization_research_evidence_v3",
                        "collect_localization_visual_evidence_v3",
                    }.issubset(formal_retry.reused_step_ids)
                    else None
                )
                or localization_document_evidence.adjudicate_localization_document_evidence(
                    brief,
                    research,
                    visual,
                    route=policy.route("evidence_adjudication"),
                    batch_journal=evidence_batch_journal,
                )
            ),
            (localization_document_evidence.project_localization_document_evidence_result),
            brief=brief,
        )
        creation_context = execution.run(
            "lock_localization_creation_context",
            "正在锁定当前视频专属的中文创作策略、术语和重点语义。",
            lambda: (
                (
                    formal_retry.recover(
                        "lock_localization_creation_context",
                        expected_lineage={
                            "source_fingerprint": (source_lock.source_fingerprint),
                            "context_intent_fingerprint": (context_intent.context_intent_fingerprint),
                            "brief_fingerprint": brief.result_fingerprint,
                            "evidence_fingerprint": (evidence.result_fingerprint),
                        },
                    )
                    if formal_retry is not None
                    and "adjudicate_localization_evidence_v3" in formal_retry.reused_step_ids
                    else None
                )
                or localization_creation_context.lock_localization_creation_context(
                    localization_creation_context.LocalizationCreationContextInput(
                        source_operation_id=operation_id_for("lock_localization_source"),
                        context_operation_id=operation_id_for("lock_localization_context_intent"),
                        brief_operation_id=operation_id_for("analyze_localization_document"),
                        evidence_operation_id=operation_id_for("adjudicate_localization_evidence_v3"),
                        source_lock=source_lock,
                        context_intent=context_intent,
                        document_brief=brief,
                        evidence=evidence,
                    )
                )
            ),
            (localization_creation_context.project_localization_creation_context_result),
        )
        creation_source_lock = (
            localization_creation_context
            .project_localization_creation_source_lock(
                source_lock,
                creation_context,
            )
        )

        generation_chunk_count = 1
        report_generation_batch = ledger.batch_callback("generate_localization_spoken_script")

        def save_script_generation_checkpoint(
            checkpoint: (localization_spoken_script.LocalizationSpokenScriptGenerationCheckpoint),
        ) -> None:
            if checkpoint.completed_chunks:
                report_generation_batch(
                    len(checkpoint.completed_chunks),
                    generation_chunk_count,
                    checkpoint.completed_chunks[-1].chunk_id,
                )
            if on_atomic_result is None:
                return
            on_atomic_result(
                ("generate_localization_spoken_script.chunk_responses"),
                checkpoint,
            )

        script_request = localization_spoken_script.LocalizationSpokenScriptInput(
            source_operation_id=operation_id_for("lock_localization_source"),
            creation_context_operation_id=operation_id_for("lock_localization_creation_context"),
            source_lock=creation_source_lock,
            creation_context=creation_context,
            route=policy.route("spoken_script_creation"),
        )
        generation_chunk_count = len(
            localization_generation_chunks.plan_localization_generation_chunks(
                source_fingerprint=(script_request.source_lock.source_fingerprint),
                cues=list(script_request.source_lock.input.cues),
                sections=(
                    localization_creation_context
                    .project_localization_creation_sections(
                        creation_context
                    )
                ),
                pauses=list(script_request.source_lock.input.pauses),
            ).chunks
        )
        script_generation_checkpoint = None
        if development_execution is not None and execution.may_resume_atomic_checkpoint(
            "generate_localization_spoken_script",
            allow_completed_target=True,
        ):
            candidate = development_checkpoints.load_development_checkpoint(
                development_execution.snapshot_root,
                project_id=project_id,
                workflow_operation_id=(development_execution.development_session_id),
                step_id=("generate_localization_spoken_script.chunk_responses"),
                result_model=(localization_spoken_script.LocalizationSpokenScriptGenerationCheckpoint),
            )
            if isinstance(
                candidate,
                localization_spoken_script.LocalizationSpokenScriptGenerationCheckpoint,
            ) and localization_spoken_script.generation_checkpoint_matches(
                script_request,
                candidate,
            ):
                script_generation_checkpoint = candidate
        elif formal_retry is not None and "lock_localization_creation_context" in formal_retry.reused_step_ids:
            candidate = formal_retry.load_generation_checkpoint(
                localization_spoken_script.LocalizationSpokenScriptGenerationCheckpoint
            )
            if isinstance(
                candidate,
                localization_spoken_script.LocalizationSpokenScriptGenerationCheckpoint,
            ) and localization_spoken_script.generation_checkpoint_matches(
                script_request,
                candidate,
            ):
                script_generation_checkpoint = candidate

        if formal_retry is not None and script_generation_checkpoint is not None:
            save_script_generation_checkpoint(script_generation_checkpoint)

        script = execution.run(
            "generate_localization_spoken_script",
            "正在复用全文理解，按稳定连续分块生成并合并本土化初稿。",
            lambda: (
                (
                    formal_retry.recover(
                        "generate_localization_spoken_script",
                        expected_lineage={
                            "source_fingerprint": (source_lock.source_fingerprint),
                            "brief_fingerprint": brief.result_fingerprint,
                            "creation_context_fingerprint": (creation_context.result_fingerprint),
                        },
                        expected_route=(policy.route("spoken_script_creation").model_dump(mode="json")),
                    )
                    if formal_retry is not None and "lock_localization_creation_context" in formal_retry.reused_step_ids
                    else None
                )
                or localization_spoken_script.generate_localization_spoken_script(
                    script_request,
                    resume_checkpoint=script_generation_checkpoint,
                    on_generation_checkpoint=(save_script_generation_checkpoint),
                    batch_journal=batch_journal_for("generate_localization_spoken_script"),
                )
            ),
            (localization_spoken_script.project_localization_spoken_script_result),
        )
        fidelity, naturalness = execution.run_parallel(
            [
                (
                    "review_localization_fidelity",
                    "正在检查原意、事实、关键关系、否定和因果。",
                    lambda: (
                        (
                            formal_retry.recover(
                                "review_localization_fidelity",
                                expected_lineage={
                                    "source_fingerprint": (source_lock.source_fingerprint),
                                    "script_fingerprint": (script.result_fingerprint),
                                },
                                expected_route=(policy.route("fidelity_review").model_dump(mode="json")),
                            )
                            if formal_retry is not None
                            and "generate_localization_spoken_script" in formal_retry.reused_step_ids
                            else None
                        )
                        or localization_spoken_script.review_localization_spoken_script_fidelity(
                            source_lock=creation_source_lock,
                            script=script,
                            route=policy.route("fidelity_review"),
                            verified_evidence_constraints=list(creation_context.content.verified_evidence_constraints),
                            batch_journal=batch_journal_for("review_localization_fidelity"),
                        )
                    ),
                    (localization_spoken_script.project_localization_spoken_script_review_result),
                    {},
                ),
                (
                    "review_localization_naturalness",
                    "正在盲测中文是否像母语者在当前场景下自然表达。",
                    lambda: (
                        (
                            formal_retry.recover(
                                "review_localization_naturalness",
                                expected_lineage={
                                    "source_fingerprint": (source_lock.source_fingerprint),
                                    "script_fingerprint": (script.result_fingerprint),
                                },
                                expected_route=(policy.route("naturalness_review").model_dump(mode="json")),
                            )
                            if formal_retry is not None
                            and "generate_localization_spoken_script" in formal_retry.reused_step_ids
                            else None
                        )
                        or localization_spoken_script.review_localization_spoken_script_naturalness(
                            source_fingerprint=(source_lock.source_fingerprint),
                            script=script,
                            route=policy.route("naturalness_review"),
                            batch_journal=batch_journal_for("review_localization_naturalness"),
                        )
                    ),
                    (localization_spoken_script.project_localization_spoken_script_review_result),
                    {},
                ),
            ],
            thread_name_prefix="localization-v3-review",
        )

        report_finalization_section = ledger.batch_callback("finalize_localization_spoken_script")

        def save_finalization_checkpoint(
            checkpoint: (localization_spoken_script.LocalizationSpokenScriptFinalizationCheckpoint),
        ) -> None:
            total_sections = len(checkpoint.working_content.sections)
            report_finalization_section(
                checkpoint.next_section_index,
                total_sections,
                (f"第 {checkpoint.round_index} 轮/{checkpoint.review_stage}"),
            )
            if on_atomic_result is None:
                return
            on_atomic_result(
                (
                    "finalize_localization_spoken_script."
                    f"section_r{checkpoint.round_index:02d}_"
                    f"{checkpoint.next_section_index:04d}"
                ),
                checkpoint,
            )

        finalization_request = localization_spoken_script.LocalizationSpokenScriptFinalizationInput(
            script_operation_id=operation_id_for("generate_localization_spoken_script"),
            fidelity_review_operation_id=operation_id_for("review_localization_fidelity"),
            naturalness_review_operation_id=operation_id_for("review_localization_naturalness"),
            source_lock=creation_source_lock,
            document_brief=brief,
            creation_context=creation_context,
            script=script,
            fidelity_review=fidelity,
            naturalness_review=naturalness,
            verified_evidence_constraints=(
                [
                    localization_creation_context.render_verified_evidence_constraint(item)
                    for item in creation_context.content.verified_evidence_constraints
                ]
            ),
            route=policy.route("spoken_script_finalization"),
            post_fidelity_route=policy.route("fidelity_review"),
            post_naturalness_route=policy.route("naturalness_review"),
            post_naturalness_adjudication_route=(
                policy.route("spoken_script_creation").model_copy(
                    update={
                        "phase": "naturalness_review",
                        "reasoning_effort": "high",
                        "output_format": "json",
                        "may_escalate": False,
                        "escalation_reason": "",
                    }
                )
            ),
        )
        finalization_checkpoint = None
        checkpoint_candidates = []
        if development_execution is not None and execution.may_resume_atomic_checkpoint(
            "finalize_localization_spoken_script",
            allow_completed_target=True,
        ):
            checkpoint_candidates = development_checkpoints.load_development_checkpoints_by_prefix(
                development_execution.snapshot_root,
                project_id=project_id,
                workflow_operation_id=(development_execution.development_session_id),
                step_id_prefix=("finalize_localization_spoken_script.section_"),
                result_model=(localization_spoken_script.LocalizationSpokenScriptFinalizationCheckpoint),
            )
        elif formal_retry is not None and {
            "review_localization_fidelity",
            "review_localization_naturalness",
        }.issubset(formal_retry.reused_step_ids):
            checkpoint_candidates = formal_retry.load_checkpoints_by_prefix(
                "finalize_localization_spoken_script.section_",
                result_model=(localization_spoken_script.LocalizationSpokenScriptFinalizationCheckpoint),
            )

        if checkpoint_candidates:
            compatible_checkpoints = []
            route_fingerprint = localization_spoken_script._finalization_route_fingerprint(finalization_request)
            for candidate in checkpoint_candidates:
                try:
                    (
                        localization_spoken_script._validate_finalization_checkpoint(
                            finalization_request,
                            candidate,
                            route_fingerprint=route_fingerprint,
                            max_revision_rounds=(localization_spoken_script.FINALIZATION_MAX_REVISION_ROUNDS),
                        )
                    )
                except ValueError:
                    continue
                compatible_checkpoints.append(candidate)
            if compatible_checkpoints:
                finalization_checkpoint = max(
                    compatible_checkpoints,
                    key=lambda item: (
                        item.round_index,
                        item.review_stage == "post_fidelity",
                        item.next_section_index,
                    ),
                )

        finalization_batch_journal = batch_journal_for("finalize_localization_spoken_script")
        final_script = execution.run(
            "finalize_localization_spoken_script",
            "正在只修改已定位的中文句子，并做限定轮次的原意与自然度复核。",
            lambda: (
                (
                    formal_retry.recover(
                        "finalize_localization_spoken_script",
                        expected_lineage={
                            "source_fingerprint": (source_lock.source_fingerprint),
                            "input_script_fingerprint": (script.result_fingerprint),
                        },
                        expected_route=(policy.route("spoken_script_finalization").model_dump(mode="json")),
                        is_reusable=lambda candidate: (
                            isinstance(
                                candidate,
                                localization_spoken_script.LocalizationSpokenScriptFinalResult,
                            )
                        ),
                    )
                    if formal_retry is not None
                    and {
                        "review_localization_fidelity",
                        "review_localization_naturalness",
                    }.issubset(formal_retry.reused_step_ids)
                    else None
                )
                or localization_spoken_script.finalize_localization_spoken_script(
                    finalization_request,
                    resume_checkpoint=finalization_checkpoint,
                    on_section_checkpoint=save_finalization_checkpoint,
                    batch_journal=finalization_batch_journal,
                    max_revision_rounds=(localization_spoken_script.FINALIZATION_MAX_REVISION_ROUNDS),
                )
            ),
            (localization_spoken_script.project_localization_spoken_script_final_result),
        )
        script_quality_warning_count = 0
        if final_script.quality_summary.status == "warning":
            script_quality_warning_count = (
                len(final_script.post_fidelity_review.issues) if final_script.post_fidelity_review is not None else 0
            ) + (
                len(final_script.post_naturalness_review.issues)
                if final_script.post_naturalness_review is not None
                else 0
            )
        source_cues = [
            localization_semantic_alignment.LocalizationAlignmentSourceCue(
                cue_id=item.cue_id,
                speaker_id=(item.speaker_id or item.speaker_cluster_id),
                text=item.text,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                source_word_ids=list(item.source_word_ids),
            )
            for item in creation_source_lock.input.cues
        ]
        target_paragraphs = localization_semantic_alignment.build_alignment_target_paragraphs(
            [paragraph for section in final_script.content.sections for paragraph in section.paragraphs]
        )
        evidence_anchors = localization_semantic_alignment.build_alignment_evidence_anchors(
            [
                localization_semantic_alignment.LocalizationSemanticAlignmentEvidenceHint(
                    anchor_id=item.question_id,
                    target_text_zh=item.anchored_target_text_zh,
                    source_cue_ids=list(item.source_cue_ids),
                )
                for item in creation_context.content.verified_evidence_constraints
                if item.anchored_target_text_zh.strip()
            ],
            target_paragraphs,
            source_cues,
        )
        source_boundaries = source_boundary_evidence.build_source_boundary_evidence(
            creation_source_lock
        )
        alignment_encoder = LabseTextEncoder()
        alignment = execution.run(
            "align_localization_semantics",
            "正在本地把中文语义段映射回英文逐词时间。",
            lambda: (
                (
                    formal_retry.recover(
                        "align_localization_semantics",
                        expected_lineage={
                            "source_fingerprint": source_lock.source_fingerprint,
                            "spoken_script_fingerprint": (
                                final_script.result_fingerprint
                            ),
                            "embedding_model_id": alignment_encoder.model_id,
                            "embedding_model_fingerprint": (
                                alignment_encoder.model_fingerprint
                            ),
                        },
                    )
                    if formal_retry is not None
                    and "finalize_localization_spoken_script"
                    in formal_retry.reused_step_ids
                    else None
                )
                or localization_semantic_alignment.align_localized_script(
                    localization_semantic_alignment.LocalizationSemanticAlignmentInput(
                        source_fingerprint=source_lock.source_fingerprint,
                        spoken_script_fingerprint=(final_script.result_fingerprint),
                        source_cues=source_cues,
                        source_words=list(creation_source_lock.input.words),
                        source_boundaries=source_boundaries,
                        target_paragraphs=target_paragraphs,
                        evidence_anchors=evidence_anchors,
                    ),
                    encoder=alignment_encoder,
                )
            ),
            (localization_semantic_alignment.project_localization_semantic_alignment_result),
            source_cues=source_cues,
            target_paragraphs=target_paragraphs,
            source_words=list(creation_source_lock.input.words),
        )
        adjudicated_alignment = execution.run(
            "adjudicate_localization_alignment",
            "正在只复核低把握的语义时间边界。",
            lambda: (
                (
                    formal_retry.recover(
                        "adjudicate_localization_alignment",
                        expected_lineage={
                            "alignment_fingerprint": (
                                alignment.result_fingerprint
                            ),
                            "spoken_script_fingerprint": (
                                final_script.result_fingerprint
                            ),
                        },
                        expected_route=(
                            policy.route("alignment_adjudication")
                            .model_dump(mode="json")
                        ),
                    )
                    if formal_retry is not None
                    and "align_localization_semantics"
                    in formal_retry.reused_step_ids
                    else None
                )
                or localization_alignment_adjudication.adjudicate_localization_alignment(
                    localization_alignment_adjudication.LocalizationAlignmentAdjudicationInput(
                        alignment_operation_id=operation_id_for("align_localization_semantics"),
                        alignment=alignment,
                        source_cues=source_cues,
                        source_words=list(creation_source_lock.input.words),
                        source_boundaries=source_boundaries,
                        target_paragraphs=target_paragraphs,
                        route=policy.route("alignment_adjudication"),
                    ),
                    encoder=alignment_encoder,
                    batch_journal=batch_journal_for("adjudicate_localization_alignment"),
                )
            ),
            (localization_alignment_adjudication.project_localization_alignment_adjudication_result),
        )
        dual_tracks = execution.run(
            "build_localization_dual_tracks",
            "正在生成独立中文台词轨和可阅读的上屏字幕轨。",
            lambda: localization_dual_tracks.build_localization_dual_tracks(
                localization_dual_tracks.LocalizationDualTrackInput(
                    spoken_script_operation_id=operation_id_for("finalize_localization_spoken_script"),
                    alignment_operation_id=operation_id_for("adjudicate_localization_alignment"),
                    spoken_script=final_script,
                    alignment=adjudicated_alignment,
                    source_cues=source_cues,
                    source_words=list(creation_source_lock.input.words),
                    source_boundaries=source_boundaries,
                ),
                encoder=alignment_encoder,
            ),
            (localization_dual_tracks.project_localization_dual_tracks_result),
        )
        display_adjudication_request = localization_display_adjudication.LocalizationDisplayAdjudicationInput(
            dual_tracks_operation_id=operation_id_for("build_localization_dual_tracks"),
            dual_tracks=dual_tracks,
            source_cues=source_cues,
            source_words=list(creation_source_lock.input.words),
            source_boundaries=source_boundaries,
            route=policy.route("alignment_adjudication"),
        )
        display_batch_checkpoint = None
        if development_execution is not None and execution.may_resume_atomic_checkpoint(
            "adjudicate_localization_display_boundaries",
            allow_completed_target=True,
        ):
            candidate = development_checkpoints.load_development_checkpoint(
                development_execution.snapshot_root,
                project_id=project_id,
                workflow_operation_id=(development_execution.development_session_id),
                step_id=("adjudicate_localization_display_boundaries.batches"),
                result_model=(localization_display_adjudication.LocalizationDisplayAdjudicationBatchCheckpoint),
            )
            if isinstance(
                candidate,
                localization_display_adjudication.LocalizationDisplayAdjudicationBatchCheckpoint,
            ):
                display_batch_checkpoint = candidate
        elif formal_retry is not None and "build_localization_dual_tracks" in formal_retry.reused_step_ids:
            candidates = formal_retry.load_checkpoints_by_prefix(
                "adjudicate_localization_display_boundaries.batches",
                result_model=(localization_display_adjudication.LocalizationDisplayAdjudicationBatchCheckpoint),
            )
            if candidates:
                display_batch_checkpoint = candidates[-1]

        def save_display_batch_checkpoint(
            checkpoint: (localization_display_adjudication.LocalizationDisplayAdjudicationBatchCheckpoint),
        ) -> None:
            if on_atomic_result is not None:
                on_atomic_result(
                    "adjudicate_localization_display_boundaries.batches",
                    checkpoint,
                )

        display_adjudication = execution.run(
            "adjudicate_localization_display_boundaries",
            "正在只复核有歧义的上屏字幕逐词边界。",
            lambda: (
                (
                    formal_retry.recover(
                        "adjudicate_localization_display_boundaries",
                        expected_lineage={"dual_tracks_fingerprint": (dual_tracks.result_fingerprint)},
                        expected_route=(policy.route("alignment_adjudication").model_dump(mode="json")),
                    )
                    if formal_retry is not None and "build_localization_dual_tracks" in formal_retry.reused_step_ids
                    else None
                )
                or localization_display_adjudication.adjudicate_display_boundaries(
                    display_adjudication_request,
                    encoder=alignment_encoder,
                    resume_checkpoint=display_batch_checkpoint,
                    on_batch_checkpoint=save_display_batch_checkpoint,
                    batch_journal=batch_journal_for("adjudicate_localization_display_boundaries"),
                )
            ),
            (localization_display_adjudication.project_localization_display_adjudication_result),
        )
        final_dual_tracks = display_adjudication.dual_tracks
        gate = execution.run(
            "validate_localization_tracks",
            "正在检查内容覆盖、语义时间、字幕可读性和输入版本。",
            lambda: localization_tracks.validate_localization_tracks(
                localization_tracks.LocalizationTracksQualityGateInput(
                    dual_tracks_operation_id=operation_id_for("adjudicate_localization_display_boundaries"),
                    source_fingerprint=source_lock.source_fingerprint,
                    final_script=final_script,
                    dual_tracks=final_dual_tracks,
                ),
                current_source_fingerprint=(lock_localization_source_snapshot(project_id).source_fingerprint),
            ),
            localization_tracks.project_localization_tracks_quality_gate_result,
        )
        if gate.decision == "blocked":
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TRACK_QUALITY_GATE_BLOCKED",
                "本土化双轨质量门未通过，因此没有覆盖当前结果。",
            )
        result_holder = []
        saved_holder: list[VideoLocalizationDraft | None] = []
        formal_summary_holder: list[dict] = []

        def commit_tracks() -> localization_tracks.LocalizationFormalDualTrackWriteResult:
            completed_at = now_iso()

            def preview_cues_from(
                current: VideoLocalizationDraft,
            ) -> list[dict]:
                return [
                    {
                        "subtitle_id": item.subtitle_id,
                        "start_ms": item.start_ms,
                        "end_ms": item.end_ms,
                        "text": item.text,
                        "spoken_segment_id": item.spoken_segment_id,
                    }
                    for item in current.localized_subtitles
                ]

            def apply(
                latest: VideoLocalizationDraft,
            ) -> VideoLocalizationDraft:
                next_draft, write_result = localization_tracks.build_formal_localization_dual_tracks(
                    latest,
                    final_script=final_script,
                    dual_tracks=final_dual_tracks,
                    gate=gate,
                )
                result_holder.append(write_result)
                ledger_fields = ledger.summary_fields()
                commit_timing = dict(
                    ledger_fields.get("stage_timings", {}).get(
                        "commit_localization_tracks",
                        {},
                    )
                )
                commit_timing.pop("running", None)
                commit_timing["atomic"] = True
                stage_timings = {
                    **ledger_fields.get("stage_timings", {}),
                    "commit_localization_tracks": commit_timing,
                }
                preview_cues = preview_cues_from(next_draft)
                formal_summary = {
                    "stage": "本土化字幕已生成",
                    "stage_id": "commit_localization_tracks",
                    **ledger_fields,
                    "task_step_results": {
                        **ledger_fields.get("task_step_results", {}),
                        "commit_localization_tracks": (
                            localization_tracks
                            .project_localization_formal_dual_track_result(
                                write_result
                            )
                        ),
                    },
                    "stage_timings": stage_timings,
                    "localized_spoken_segment_count": len(
                        next_draft.localized_spoken_segments
                    ),
                    "localized_subtitle_count": len(preview_cues),
                    "result_count": len(preview_cues),
                    "result_unit": "条上屏字幕",
                    "preview_phase": "localization_v3_committed",
                    "preview_cues": preview_cues,
                    "target_language": resolved_target_language,
                    "quality_summary": gate.model_dump(mode="json"),
                    "content_review_warning_count": (
                        script_quality_warning_count
                    ),
                    "localization_source_fingerprint": (
                        source_lock.source_fingerprint
                    ),
                    "localization_workflow_version": "localization-v3",
                    "formal_retry_source_operation_ids": list(
                        formal_retry_source_operation_ids or []
                    ),
                    "reused_formal_retry_step_ids": (
                        list(formal_retry.reused_step_ids)
                        if formal_retry is not None
                        else []
                    ),
                }
                formal_summary_holder.append(formal_summary)
                if finalize_formal_commit is not None:
                    next_draft = finalize_formal_commit(
                        next_draft,
                        formal_summary,
                        completed_at,
                    )
                return next_draft

            def commit() -> VideoLocalizationDraft | None:
                return update_video_localization_atomic(
                    project_id,
                    apply,
                    intent="content",
                )

            if commit_guard is not None:
                committed, value = commit_guard(commit)
                if not committed:
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_OPERATION_CANCELLED",
                        "任务已取消。",
                    )
                saved = value if isinstance(value, VideoLocalizationDraft) else None
            else:
                saved = commit()
            if not result_holder:
                raise AppException(
                    404,
                    "VIDEO_LOCALIZATION_DRAFT_NOT_FOUND",
                    "没有找到要写入正式本土化双轨的项目。",
                )
            saved_holder.append(saved)
            preview_cues = preview_cues_from(saved) if saved is not None else []
            if on_preview is not None:
                on_preview("localization_v3_committed", preview_cues)
            return result_holder[-1]

        execution.run_side_effect(
            "commit_localization_tracks",
            "正在把通过质量门的中文台词和上屏字幕写入项目。",
            commit_tracks,
            localization_tracks.project_localization_formal_dual_track_result,
        )
        saved = saved_holder[-1] if saved_holder else None
        if not formal_summary_holder:
            raise AppException(
                500,
                "VIDEO_LOCALIZATION_FORMAL_SUMMARY_MISSING",
                "正式本土化字幕已执行，但没有生成可提交的任务摘要。",
            )
        return saved, formal_summary_holder[-1]
    except localization_workflow_execution.LocalizationDevelopmentTargetFailed as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_TARGET_FAILED",
            (f"{exc.step_result.get('label') or exc.step_id}没有通过，本次已停在该子流程。"),
            {"step_result": exc.step_result},
        ) from exc
    except localization_workflow_execution.LocalizationDevelopmentTargetReached:
        return get_video_localization(project_id), execution.development_summary()
    except llm_runtime.LlmRuntimeError as exc:
        raise AppException(exc.status_code, exc.code, str(exc)) from exc
    except ValueError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_V3_INVALID",
            str(exc),
        ) from exc


def run_semantic_tts_grouping(
    project_id: str,
    *,
    execution_fence: ExecutionFence,
    profile_id: str | None = None,
    expected_profile_configuration_fingerprint: str | None = None,
    target_chars: int = 120,
    max_chars: int = 180,
    on_progress: Callable[[float, str], None] | None = None,
    commit_guard: Callable[[Callable[[], object]], tuple[bool, object | None]] | None = None,
) -> tuple[VideoLocalizationDraft | None, dict]:
    draft = get_video_localization(project_id)
    if draft is None:
        return None, {}
    grouping = semantic_tts_grouping_execution.execute_semantic_tts_grouping(
        execution_fence,
        draft,
        profile_id=profile_id,
        expected_profile_configuration_fingerprint=(expected_profile_configuration_fingerprint),
        target_chars=target_chars,
        max_chars=max_chars,
        on_progress=on_progress,
    )

    def commit() -> VideoLocalizationDraft | None:
        def apply(latest: VideoLocalizationDraft) -> VideoLocalizationDraft:
            latest_items = semantic_tts_grouping.build_items(latest)
            if semantic_tts_grouping.source_fingerprint(latest_items) != grouping["source_fingerprint"]:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_SOURCE_CHANGED",
                    "分组期间字幕已被修改，请重新执行语义成组。",
                )
            return latest.model_copy(
                update={
                    "localization_state": {
                        **latest.localization_state,
                        "semantic_tts_grouping": grouping,
                    }
                }
            )

        return update_video_localization_atomic(project_id, apply, intent="content")

    def guarded_write() -> VideoLocalizationDraft | None:
        if commit_guard:
            committed, value = commit_guard(commit)
            if not committed:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_OPERATION_CANCELLED",
                    "任务已取消。",
                )
            return value if isinstance(value, VideoLocalizationDraft) else None
        return commit()

    write_input_fingerprint = semantic_tts_grouping_execution.local_step_fingerprint(
        {
            "step_id": "write",
            "source_fingerprint": grouping["source_fingerprint"],
            "groups": grouping["groups"],
        }
    )
    saved = semantic_tts_grouping_execution.run_local_step(
        execution_fence,
        step_id="write",
        input_fingerprint=write_input_fingerprint,
        output_fingerprint=write_input_fingerprint,
        action=guarded_write,
        fallback_error_code=("VIDEO_LOCALIZATION_SEMANTIC_GROUPING_WRITE_FAILED"),
    )
    return (
        saved if isinstance(saved, VideoLocalizationDraft) else None,
        grouping,
    )


def _infer_source_language_from_cues(draft: VideoLocalizationDraft) -> str:
    text = "\n".join(value for cue in draft.cues if (value := (cue.en_subtitle_text or "").strip()))
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    return "zh" if cjk_count > latin_count else "en"


def _probe_handoff_audio_duration(path_value: str | None, *, code: str, label: str) -> int:
    path = Path(path_value or "").expanduser()
    if not path_value or not path.exists() or not path.is_file():
        raise AppException(400, code, f"{label}不存在，请重新准备参考音频", {"path": path_value})
    try:
        duration_ms = int(audio_tools.probe_audio(path).get("duration_ms") or 0)
    except Exception as exc:
        raise AppException(
            400,
            f"{code}_INVALID",
            f"无法读取{label}，请重新准备可播放的音频",
            {"path": str(path)},
        ) from exc
    if duration_ms <= 0:
        raise AppException(400, f"{code}_INVALID", f"{label}没有可用时长", {"path": str(path)})
    return duration_ms


def _normalize_tts_handoff_reference(
    request: GenerateRequest,
    *,
    segment_id: str,
) -> GenerateRequest:
    source_duration_ms = _probe_handoff_audio_duration(
        request.custom_reference_source_audio_path,
        code="VIDEO_LOCALIZATION_TTS_REFERENCE_SOURCE_MISSING",
        label="参考音频源文件",
    )
    clip_duration_ms = _probe_handoff_audio_duration(
        request.reference_audio_path,
        code="VIDEO_LOCALIZATION_TTS_REFERENCE_MISSING",
        label="参考音频片段",
    )
    trim_start_ms = request.custom_reference_trim_start_ms
    trim_end_ms = request.custom_reference_trim_end_ms
    if trim_start_ms is None or trim_end_ms is None:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TTS_REFERENCE_RANGE_MISSING",
            "参考音频缺少完整的裁切入点和出点，请重新准备",
            {"segment_id": segment_id},
        )
    trim_start_ms = int(trim_start_ms)
    if trim_start_ms < 0 or trim_start_ms >= source_duration_ms:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TTS_REFERENCE_RANGE_INVALID",
            "参考音频裁切入点超出源音频范围",
            {"segment_id": segment_id, "trim_start_ms": trim_start_ms, "source_duration_ms": source_duration_ms},
        )

    # The managed clip is authoritative for materialized duration. A stale
    # source-duration or trim-end snapshot is repairable without changing which
    # part of the source was selected, so normalize it before queue submission.
    normalized_trim_end_ms = trim_start_ms + clip_duration_ms
    tolerance_ms = max(
        reference_audio_integrity.CLIP_DURATION_ABSOLUTE_TOLERANCE_MS,
        round(clip_duration_ms * reference_audio_integrity.CLIP_DURATION_RELATIVE_TOLERANCE),
    )
    if normalized_trim_end_ms > source_duration_ms + tolerance_ms:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TTS_REFERENCE_RANGE_INVALID",
            "参考音频片段时长超过源音频剩余范围，请重新裁切",
            {
                "segment_id": segment_id,
                "trim_start_ms": trim_start_ms,
                "clip_duration_ms": clip_duration_ms,
                "source_duration_ms": source_duration_ms,
            },
        )
    normalized_trim_end_ms = min(source_duration_ms, normalized_trim_end_ms)
    normalized = request.model_copy(
        update={
            "custom_reference_source_duration_ms": source_duration_ms,
            "custom_reference_trim_start_ms": trim_start_ms,
            "custom_reference_trim_end_ms": normalized_trim_end_ms,
        }
    )
    try:
        reference_audio_integrity.validate_reference_clip(
            normalized.reference_audio_path or "",
            source_duration_ms=source_duration_ms,
            trim_start_ms=trim_start_ms,
            trim_end_ms=normalized_trim_end_ms,
        )
    except reference_audio_integrity.ReferenceAudioIntegrityError as exc:
        raise AppException(
            400,
            exc.code,
            exc.message,
            {
                "segment_id": segment_id,
                "reference_audio_path": normalized.reference_audio_path,
                "trim_start_ms": trim_start_ms,
                "trim_end_ms": normalized_trim_end_ms,
            },
        ) from exc
    return normalized


def _tts_reuse_parameter_values(
    *,
    project_id: str,
    segment_id: str,
    history_result_id: str | None,
    parameters: dict[str, Any] | None,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if history_result_id:
        history = history_store.get(history_result_id)
        if history is None:
            raise AppException(404, "VIDEO_LOCALIZATION_TTS_HISTORY_NOT_FOUND", "没有找到要沿用参数的配音记录")
        belongs_to_video_localization = bool(
            history.bind_to_video_localization or history.parameter_snapshot.get("source") == "video_localization"
        )
        if history.project_id != project_id or not belongs_to_video_localization:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TTS_HISTORY_SEGMENT_MISMATCH",
                "该配音记录不属于当前项目，不能沿用参数",
                {"segment_id": segment_id, "history_result_id": history_result_id},
            )
        values.update(history.parameter_snapshot)
    if parameters:
        values.update(parameters)
    reusable = {key: value for key, value in values.items() if key not in _TTS_HANDOFF_AUTHORITATIVE_FIELDS}
    engine_parameters = reusable.get("engine_parameters")
    if isinstance(engine_parameters, dict):
        reusable["engine_parameters"] = {
            key: value for key, value in engine_parameters.items() if key not in _TTS_HANDOFF_AUTHORITATIVE_FIELDS
        }
    return reusable


def _with_voice_library_reference(
    request: GenerateRequest,
    *,
    voice_id: str,
) -> GenerateRequest:
    """Replace only the TTS reference with one validated local library voice."""

    voice = voice_store.get_voice(voice_id)
    if voice is None:
        raise AppException(
            404,
            "VIDEO_LOCALIZATION_TTS_VOICE_NOT_FOUND",
            "所选本地音色不存在，请重新选择。",
            {"voice_id": voice_id},
        )
    binding = next(
        (item for item in voice.engine_bindings if item.engine_id == request.engine_id),
        None,
    )
    if binding is None or not binding.available:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TTS_VOICE_ENGINE_UNAVAILABLE",
            "所选本地音色不能用于当前 TTS 引擎。",
            {
                "voice_id": voice_id,
                "engine_id": request.engine_id,
                "reason": binding.reason if binding is not None else "missing binding",
            },
        )
    reference_text = voice.reference_text.strip()
    if not reference_text:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TTS_VOICE_REFERENCE_TEXT_MISSING",
            "所选本地音色缺少准确参考台词，请先完善音色资料。",
            {"voice_id": voice_id},
        )
    reference_path = voice_store.reference_path(voice_id)
    duration_ms = _probe_handoff_audio_duration(
        reference_path,
        code="VIDEO_LOCALIZATION_TTS_VOICE_REFERENCE_MISSING",
        label="本地音色参考音频",
    )
    return request.model_copy(
        update={
            "voice_id": voice_id,
            "voice_source": VoiceSource.voice_library,
            "reference_audio_path": reference_path,
            "reference_audio_license_status": voice.license_status,
            "reference_audio_tags": [
                *voice.tags,
                "视频本土化",
                "本地音色库降级",
            ],
            "ref_text": reference_text,
            "custom_reference_source_audio_path": reference_path,
            "custom_reference_source_duration_ms": duration_ms,
            "custom_reference_trim_start_ms": 0,
            "custom_reference_trim_end_ms": duration_ms,
        }
    )


def _normalize_single_tts_submission_text(text: str, canonical_text: str) -> str:
    """Collapse accidental exact repeats of the authoritative subtitle text."""
    normalized = text_normalizer.normalize_tts_pronunciation(text)
    canonical = text_normalizer.normalize_tts_pronunciation(canonical_text)
    compact = re.sub(r"\s+", "", normalized)
    canonical_compact = re.sub(r"\s+", "", canonical)
    if compact == canonical_compact:
        return canonical_text
    if (
        canonical_compact
        and len(compact) > len(canonical_compact)
        and len(compact) % len(canonical_compact) == 0
        and compact == canonical_compact * (len(compact) // len(canonical_compact))
    ):
        return canonical_text
    return normalized


def build_tts_parameter_pack(
    project_id: str,
    *,
    target_subtitle_ids: list[str],
    source_cue_ids: list[str] | None = None,
):
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = draft_store.get(project_id) or VideoLocalizationDraft()
    return tts_parameter_pack.build_parameter_pack(
        project_id=project_id,
        draft=draft,
        selection_request=TtsSelectionRequest(
            target_subtitle_ids=target_subtitle_ids,
            source_cue_ids=source_cue_ids or [],
        ),
    )


def _prepare_single_tts_handoff(
    project_id: str,
    draft: VideoLocalizationDraft,
    segment_id: str,
    *,
    history_result_id: str | None = None,
    parameters: dict[str, Any] | None = None,
    timeline_clip_id: str | None = None,
    target_subtitle_ids: list[str],
    source_cue_ids: list[str] | None = None,
    voice_library_voice_id: str | None = None,
    workflow_id: str | None = None,
) -> tuple[GenerateRequest, VideoLocalizationTtsTask]:
    planned_group = _planned_dubbing_group_for_target(
        draft,
        target_subtitle_ids=target_subtitle_ids,
    )
    recovery = None
    reference_options = {}
    if parameters and parameters.get("video_localization_recovery"):
        from app.schemas.video_localization_dubbing_recovery import DubbingRecoveryDecision
        from app.domains.video_localization.dubbing_recovery import validate_recovery_decision
        recovery = DubbingRecoveryDecision.model_validate(parameters["video_localization_recovery"])
        recovery_group, reference_range = validate_recovery_decision(draft, recovery)
        if planned_group is None or recovery_group.group_id != planned_group.group_id:
            raise AppException(409, "DUBBING_RECOVERY_TARGET_CHANGED", "恢复方案与当前字幕选择不一致。")
        if reference_range:
            reference_options = dict(reference_start_ms=reference_range[0], reference_end_ms=reference_range[1])
    # ASR cues own default sampling and transcript, including planned groups.
    # A production plan must not silently replace the operator's selection.
    parameter_pack = tts_parameter_pack.build_parameter_pack(
        project_id=project_id,
        draft=draft,
        selection_request=TtsSelectionRequest(
            target_subtitle_ids=target_subtitle_ids,
            source_cue_ids=source_cue_ids or [],
        ),
        **reference_options,
    )
    effective_segment_id = parameter_pack.target.segment_id
    snapshot = {
        "segment_id": effective_segment_id,
        "localized_subtitle_id": parameter_pack.target.subtitle_ids[0],
        "cue_id": parameter_pack.source.cue_ids[0],
        "source_cue_ids": parameter_pack.source.cue_ids,
        "start_ms": (
            planned_group.target_start_ms if planned_group is not None else parameter_pack.target.start_ms
        ),
        "end_ms": (planned_group.target_end_ms if planned_group is not None else parameter_pack.target.end_ms),
        "text": (planned_group.spoken_text if planned_group is not None else parameter_pack.target.text),
        "subtitle_summary": re.sub(
            r"\s+",
            " ",
            (planned_group.spoken_text if planned_group is not None else parameter_pack.target.text),
        ).strip()[:60],
    }
    if timeline_clip_id:
        target_clip = next((item for item in draft.timeline_clips if item.get("clip_id") == timeline_clip_id), None)
        clip_segment_ids = {
            str((target_clip or {}).get("subtitle_id") or ""),
            str((target_clip or {}).get("cue_id") or ""),
        }
        expected_segment_ids = {
            segment_id,
            effective_segment_id,
            *(str(value) for value in (target_subtitle_ids or [])),
        }
        if (
            target_clip is None
            or target_clip.get("track_id", "dub") != "dub"
            or not (expected_segment_ids & clip_segment_ids)
        ):
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TTS_TIMELINE_TARGET_INVALID",
                "所选合成配音片段与当前字幕不匹配，请重新选择后再生成",
                {"segment_id": segment_id, "timeline_clip_id": timeline_clip_id},
            )
    canonical = parameter_pack.request
    canonical = canonical.model_copy(
        update={
            "text": snapshot["text"],
            "video_localization_recovery": recovery,
            "source": "video_localization",
            "project_id": project_id,
            "segment_id": effective_segment_id,
            "localized_subtitle_id": snapshot["localized_subtitle_id"],
            "cue_id": snapshot["cue_id"],
            "timeline_clip_id": timeline_clip_id,
            "bind_to_video_localization": True,
            "video_localization_start_ms": snapshot["start_ms"],
            "video_localization_end_ms": snapshot["end_ms"],
            "video_localization_target_subtitle_ids": parameter_pack.target.subtitle_ids,
            "video_localization_source_cue_ids": snapshot["source_cue_ids"],
            **(
                {
                    "video_localization_dubbing_plan_revision": (draft.dubbing_production.active_plan.plan_revision),
                    "video_localization_dubbing_group_id": planned_group.group_id,
                }
                if (
                    planned_group is not None
                    and draft.dubbing_production.active_plan is not None
                )
                else {}
            ),
        }
    )
    if voice_library_voice_id:
        canonical = _with_voice_library_reference(
            canonical,
            voice_id=voice_library_voice_id,
        )
    canonical = _normalize_tts_handoff_reference(canonical, segment_id=effective_segment_id)
    reusable = _tts_reuse_parameter_values(
        project_id=project_id,
        segment_id=effective_segment_id,
        history_result_id=history_result_id,
        parameters=parameters,
    )
    try:
        request = GenerateRequest.model_validate({**canonical.model_dump(), **reusable})
    except ValidationError as exc:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TTS_REUSE_PARAMETERS_INVALID",
            "沿用的生成参数已不兼容，请重置参数后再试",
            {"segment_id": effective_segment_id, "errors": exc.errors(include_url=False)},
        ) from exc
    # Re-apply and re-check all project-owned fields after parsing reusable
    # engine settings. They must never come from a history snapshot or client.
    request = request.model_copy(
        update={key: getattr(canonical, key) for key in _TTS_HANDOFF_AUTHORITATIVE_FIELDS if hasattr(canonical, key)}
    )
    request = _normalize_tts_handoff_reference(request, segment_id=effective_segment_id)
    workflow = VideoLocalizationTtsTask(
        **({"workflow_id": workflow_id} if workflow_id else {}),
        project_id=project_id,
        segment_id=effective_segment_id,
        subtitle_summary=snapshot["subtitle_summary"],
        text=snapshot["text"],
        source_cue_ids=snapshot["source_cue_ids"],
        start_ms=snapshot["start_ms"],
        end_ms=snapshot["end_ms"],
        stages=[
            VideoLocalizationTtsTaskStage(
                kind="generation",
                parameters={
                    **request.model_dump(mode="json"),
                    "handoff_mode": "reuse" if history_result_id or parameters else "default",
                    "history_result_id": history_result_id,
                    "video_localization_parameter_pack": parameter_pack.model_dump(mode="json"),
                },
            ),
            VideoLocalizationTtsTaskStage(
                kind="placement",
                parameters={
                    "target_start_ms": snapshot["start_ms"],
                    "target_end_ms": snapshot["end_ms"],
                    "source_cue_ids": snapshot["source_cue_ids"],
                    "target_snapshot": parameter_pack.target.model_dump(mode="json"),
                    "source_snapshot": parameter_pack.source.model_dump(mode="json"),
                },
            ),
        ],
    )
    request = request.model_copy(
        update={
            "video_localization_workflow_id": workflow.workflow_id,
            "video_localization_submission_id": workflow.workflow_id,
        }
    )
    generation_stage = workflow.stages[0].model_copy(
        update={"parameters": {**workflow.stages[0].parameters, "video_localization_workflow_id": workflow.workflow_id}}
    )
    workflow = workflow.model_copy(update={"stages": [generation_stage, workflow.stages[1]]})
    return request, workflow


def reserve_single_tts_handoff(
    project_id: str,
    segment_id: str,
    *,
    history_result_id: str | None = None,
    parameters: dict[str, Any] | None = None,
    timeline_clip_id: str | None = None,
    target_subtitle_ids: list[str],
    source_cue_ids: list[str] | None = None,
    voice_library_voice_id: str | None = None,
    workflow_id: str | None = None,
    capacity_replaces_workflow_ids: list[str] | None = None,
):
    """Persist one durable queue placeholder without preparing any media."""
    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None
        draft = draft_store.get(project_id) or VideoLocalizationDraft()
        selection = build_selection_snapshot(
            draft,
            TtsSelectionRequest(
                target_subtitle_ids=target_subtitle_ids,
                source_cue_ids=source_cue_ids or [],
            ),
        )
        planned_group = _planned_dubbing_group_for_target(
            draft,
            target_subtitle_ids=selection.target.subtitle_ids,
        )
        start_ms = planned_group.target_start_ms if planned_group is not None else selection.target.start_ms
        end_ms = planned_group.target_end_ms if planned_group is not None else selection.target.end_ms
        text = planned_group.spoken_text if planned_group is not None else selection.target.text
        existing = next(
            (item for item in draft.tts_tasks if workflow_id and item.workflow_id == workflow_id),
            None,
        )
        if existing is not None:
            generation_stage = next(
                (stage for stage in existing.stages if stage.kind == "generation"),
                None,
            )
            if (
                existing.status == "prepared"
                and not existing.generation_task_id
                and generation_stage is not None
                and generation_stage.parameters.get("initializing") is True
            ):
                return existing
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_WORKFLOW_ALREADY_EXISTS",
                "这条配音任务已经登记，请等待当前任务继续",
                {"workflow_id": existing.workflow_id, "status": existing.status},
            )

        requested_target_ids = set(selection.target.subtitle_ids)
        completed_segment_ids = {
            item.segment_id
            for item in draft.tts_tasks
            if item.status == "success"
        }
        requested_capacity_replacements = {
            str(value)
            for value in (capacity_replaces_workflow_ids or [])
            if str(value)
        }
        current_plan = draft.dubbing_production.active_plan
        capacity_request_matches_current_group = bool(
            requested_capacity_replacements
            and planned_group is not None
            and current_plan is not None
            and isinstance(parameters, dict)
            and parameters.get("source") == "video_localization"
            and parameters.get("video_localization_dubbing_group_id")
            == planned_group.group_id
            and parameters.get("video_localization_dubbing_plan_revision")
            == current_plan.plan_revision
            and parameters.get("video_localization_target_subtitle_ids")
            == list(planned_group.subtitle_ids)
            and parameters.get("video_localization_source_cue_ids")
            == list(selection.source.cue_ids)
            and parameters.get("text") == planned_group.spoken_text
        )
        current_capacity_failure = next(
            (
                failure
                for failure in reversed(draft.dubbing_production.group_failures)
                if planned_group is not None
                and current_plan is not None
                and failure.source_revision == current_plan.source_revision
                and failure.plan_revision == current_plan.plan_revision
                and failure.group_id == planned_group.group_id
                and failure.reason_code
                == "group_capacity_recovery_decision_required"
            ),
            None,
        )

        def is_authorized_capacity_replacement(
            item: VideoLocalizationTtsTask,
        ) -> bool:
            """Allow only the exact generated receipt named by capacity repair."""

            if (
                item.workflow_id not in requested_capacity_replacements
                or not capacity_request_matches_current_group
                or planned_group is None
                or current_capacity_failure is None
            ):
                return False
            generation = next(
                (stage for stage in item.stages if stage.kind == "generation"),
                None,
            )
            placement = next(
                (stage for stage in item.stages if stage.kind == "placement"),
                None,
            )
            task_id = str(item.generation_task_id or "")
            result_id = str(item.result_id or "")
            identities = {
                value
                for value in (
                    item.workflow_id,
                    task_id,
                    result_id,
                    f"candidate_{task_id}" if task_id else "",
                )
                if value
            }
            failure_candidate_id = str(
                current_capacity_failure.candidate_id or ""
            )
            persisted_candidate = next(
                (
                    candidate
                    for candidate in draft.generated_candidates
                    if str(candidate.get("task_id") or "") == task_id
                    and str(candidate.get("result_id") or "") == result_id
                    and bool(candidate.get("audio_path"))
                ),
                None,
            )
            if persisted_candidate is not None:
                candidate_id = str(
                    persisted_candidate.get("candidate_id") or ""
                )
                if candidate_id:
                    identities.add(candidate_id)
            placement_is_recoverable = bool(
                placement is not None
                and (
                    placement.status in {"pending", "running"}
                    or (
                        placement.status == "failed"
                        and placement.error_code in {
                            "TTS_PLACEMENT_REGENERATION_EXHAUSTED",
                            "TTS_PLACEMENT_REGENERATION_REQUIRED",
                        }
                    )
                )
            )
            return bool(
                generation is not None
                and generation.status == "success"
                and placement_is_recoverable
                and task_id
                and result_id
                and failure_candidate_id in identities
                and persisted_candidate is not None
                and frozen_group_request(
                    draft,
                    planned_group,
                    parameters=generation.parameters,
                    allow_equivalent_plan_rebind=True,
                )
                is not None
            )

        authorized_capacity_replacements = {
            item.workflow_id
            for item in draft.tts_tasks
            if is_authorized_capacity_replacement(item)
        }
        if (
            requested_capacity_replacements
            and authorized_capacity_replacements
            != requested_capacity_replacements
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_CAPACITY_REPLACEMENT_INVALID",
                "容量修复所引用的旧配音任务已变化，请重新读取当前任务后再试",
                {
                    "workflow_ids": sorted(requested_capacity_replacements),
                    "segment_id": selection.target.segment_id,
                },
            )

        def is_active_workflow(item: VideoLocalizationTtsTask) -> bool:
            if item.workflow_id in authorized_capacity_replacements:
                return False
            if item.segment_id in completed_segment_ids:
                generation = next(
                    (
                        stage
                        for stage in item.stages
                        if stage.kind == "generation"
                    ),
                    None,
                )
                placement = next(
                    (
                        stage
                        for stage in item.stages
                        if stage.kind == "placement"
                    ),
                    None,
                )
                if (
                    generation is not None
                    and generation.status == "success"
                    and placement is not None
                    and placement.status in {"pending", "running"}
                ):
                    # A generated legacy sibling is pending close-out, not an
                    # active model call. The formal adoption command now closes
                    # it durably as superseded; it must not block an explicit
                    # replacement reservation in the meantime.
                    return False
            if item.status in {"queued", "running"}:
                return True
            if item.status != "prepared":
                return False
            try:
                prepared_at = datetime.fromisoformat(
                    item.updated_at.replace("Z", "+00:00")
                )
                current_time = datetime.now(tz=prepared_at.tzinfo)
                return (current_time - prepared_at).total_seconds() <= 120
            except ValueError:
                return False

        active_for_target = next(
            (
                item
                for item in reversed(
                    reconcile_tts_workflow_tasks(list(draft.tts_tasks))
                )
                if item.workflow_id != str(workflow_id or "")
                and is_active_workflow(item)
                and (
                    item.segment_id == selection.target.segment_id
                    or bool(
                        requested_target_ids.intersection(
                            next(
                                (
                                    set(stage.parameters.get("target_subtitle_ids") or [])
                                    for stage in item.stages
                                    if stage.kind == "generation"
                                ),
                                set(),
                            )
                        )
                    )
                )
            ),
            None,
        )
        if active_for_target is not None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_SEGMENT_BUSY",
                "这段字幕已经在生成，请等待当前任务完成",
                {
                    "workflow_id": active_for_target.workflow_id,
                    "status": active_for_target.status,
                    "generation_task_id": active_for_target.generation_task_id,
                    "segment_id": active_for_target.segment_id,
                },
            )
        workflow_stub = VideoLocalizationTtsTask(
            **({"workflow_id": workflow_id} if workflow_id else {}),
            project_id=project_id,
            segment_id=selection.target.segment_id,
            subtitle_summary=re.sub(r"\s+", " ", text).strip()[:60],
            text=text,
            source_cue_ids=selection.source.cue_ids,
            start_ms=start_ms,
            end_ms=end_ms,
            stages=[
                VideoLocalizationTtsTaskStage(
                    kind="generation",
                    parameters={
                        **(parameters or {}),
                        "initializing": True,
                        "history_result_id": history_result_id,
                        "target_subtitle_ids": selection.target.subtitle_ids,
                        "source_cue_ids": selection.source.cue_ids,
                    },
                ),
                VideoLocalizationTtsTaskStage(
                    kind="placement",
                    parameters={
                        "target_start_ms": start_ms,
                        "target_end_ms": end_ms,
                        "source_cue_ids": selection.source.cue_ids,
                    },
                ),
            ],
        )
        next_tasks = [*draft.tts_tasks, workflow_stub]
        draft_store.save(
            project_id,
            draft.model_copy(update={"tts_tasks": next_tasks}),
            intent="runtime",
        )
        return workflow_stub


def build_single_tts_handoff(
    project_id: str,
    segment_id: str,
    *,
    history_result_id: str | None = None,
    parameters: dict[str, Any] | None = None,
    timeline_clip_id: str | None = None,
    target_subtitle_ids: list[str],
    source_cue_ids: list[str] | None = None,
    voice_library_voice_id: str | None = None,
    workflow_id: str | None = None,
):
    """Prepare a previously reserved workflow, or reserve one for direct callers."""
    workflow_stub = reserve_single_tts_handoff(
        project_id,
        segment_id,
        history_result_id=history_result_id,
        parameters=parameters,
        timeline_clip_id=timeline_clip_id,
        target_subtitle_ids=target_subtitle_ids,
        source_cue_ids=source_cue_ids,
        voice_library_voice_id=voice_library_voice_id,
        workflow_id=workflow_id,
    )
    if workflow_stub is None:
        return None
    draft = draft_store.get(project_id)
    if draft is None:
        return None

    try:
        request, workflow = _prepare_single_tts_handoff(
            project_id,
            draft,
            segment_id,
            history_result_id=history_result_id,
            parameters=parameters,
            timeline_clip_id=timeline_clip_id,
            target_subtitle_ids=target_subtitle_ids,
            source_cue_ids=source_cue_ids,
            voice_library_voice_id=voice_library_voice_id,
            workflow_id=workflow_stub.workflow_id,
        )
    except Exception as exc:
        error_code = exc.code if isinstance(exc, AppException) else "VIDEO_LOCALIZATION_TTS_PREPARATION_FAILED"
        error_message = exc.message if isinstance(exc, AppException) else "准备配音参考音频失败，请重试"
        with _DRAFT_WRITE_LOCK:
            latest = draft_store.get(project_id)
            if latest is not None and any(item.workflow_id == workflow_stub.workflow_id for item in latest.tts_tasks):
                def mark_failed(item: VideoLocalizationTtsTask) -> VideoLocalizationTtsTask:
                    return _replace_tts_stage(
                        item,
                        "generation",
                        status="failed",
                        error_code=error_code,
                        error_message=error_message,
                        completed_at=now_iso(),
                    )

                draft_store.save(
                    project_id,
                    _update_tts_task(latest, workflow_stub.workflow_id, mark_failed),
                    intent="runtime",
                )
        raise

    with _DRAFT_WRITE_LOCK:
        latest = draft_store.get(project_id)
        if latest is None:
            return None
        current = next(
            (item for item in latest.tts_tasks if item.workflow_id == workflow_stub.workflow_id),
            None,
        )
        if current is None or current.status == "cancelled":
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_WORKFLOW_CANCELLED",
                "配音任务已被停止或删除",
                {"workflow_id": workflow_stub.workflow_id},
            )
        next_tasks = [
            workflow if item.workflow_id == workflow_stub.workflow_id else item
            for item in latest.tts_tasks
        ]
        draft_store.save(
            project_id,
            latest.model_copy(update={"tts_tasks": next_tasks}),
            intent="runtime",
        )
    return request


def preview_single_tts_handoff(
    project_id: str,
    segment_id: str,
    *,
    history_result_id: str | None = None,
    parameters: dict[str, Any] | None = None,
    timeline_clip_id: str | None = None,
    target_subtitle_ids: list[str],
    source_cue_ids: list[str] | None = None,
    voice_library_voice_id: str | None = None,
):
    """Build an editable generate-page handoff without creating a task.

    A task starts only when the user submits generation. Keeping preview and
    submission separate prevents abandoned generate-page handoffs from showing
    up forever as active video-localization work.
    """
    project = project_store.get_project(project_id)
    if not project:
        return None
    # Preview materialization can probe/copy/crop reference audio. It is a
    # read-only handoff and must never hold the project-wide draft write lock:
    # doing so blocks autosave, subtitle edits and task polling behind media IO.
    draft = draft_store.get(project_id) or VideoLocalizationDraft()
    request, _workflow = _prepare_single_tts_handoff(
        project_id,
        draft,
        segment_id,
        history_result_id=history_result_id,
        parameters=parameters,
        timeline_clip_id=timeline_clip_id,
        target_subtitle_ids=target_subtitle_ids,
        source_cue_ids=source_cue_ids,
        voice_library_voice_id=voice_library_voice_id,
    )
    return request.model_copy(update={"video_localization_workflow_id": None})


def _dubbing_lineage_for_target(
    draft: VideoLocalizationDraft,
    target_subtitle_ids: list[str],
) -> dict[str, int | str | None]:
    plan = draft.dubbing_production.active_plan
    if plan is None or plan.plan_revision <= 0:
        return {}
    group = _planned_dubbing_group_for_target(
        draft,
        target_subtitle_ids=target_subtitle_ids,
    )
    if group is None:
        return {}
    return {
        "video_localization_dubbing_plan_revision": plan.plan_revision,
        "video_localization_dubbing_group_id": group.group_id,
    }


def _planned_dubbing_group_for_target(
    draft: VideoLocalizationDraft,
    *,
    target_subtitle_ids: list[str],
):
    """Resolve the current plan group that owns an exact subtitle selection."""

    plan = draft.dubbing_production.active_plan
    if plan is None:
        return None
    target_set = {str(value) for value in target_subtitle_ids if str(value)}
    if not target_set:
        return None
    return next(
        (item for item in plan.groups if target_set == set(item.subtitle_ids)),
        None,
    )


def _require_current_dubbing_task_lineage(
    draft: VideoLocalizationDraft,
    *,
    plan_revision: int | None,
    group_id: str | None,
    target_subtitle_ids: list[str] | None,
) -> None:
    """Reject late or legacy task results before they can adopt media."""

    plan = draft.dubbing_production.active_plan
    normalized_group_id = str(group_id or "").strip()
    normalized_targets = {str(value) for value in (target_subtitle_ids or []) if str(value)}
    has_revision = bool(plan_revision and plan_revision > 0)
    has_group = bool(normalized_group_id)
    # Manual subtitle generation is intentionally independent from the
    # mutable production plan.  Its exact target/source snapshots are frozen
    # on the workflow itself, so rebuilding a neighbouring semantic group
    # while this task waits in the queue must not invalidate its result.
    # Managed full-flow tasks always carry both lineage fields and continue to
    # use the strict checks below.
    if not has_revision and not has_group:
        return
    if plan is None or not has_revision or not has_group:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUBBING_TASK_LINEAGE_REQUIRED",
            "该配音任务没有当前计划凭证，结果不会写回时间线。",
        )
    if plan.plan_revision != plan_revision:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUBBING_TASK_STALE",
            "配音计划已经重建，旧任务结果不会写回当前时间线。",
            {
                "expected_plan_revision": plan.plan_revision,
                "received_plan_revision": plan_revision,
            },
        )
    group = next(
        (item for item in plan.groups if item.group_id == normalized_group_id),
        None,
    )
    if group is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUBBING_GROUP_STALE",
            "该生成组已经不属于当前配音计划，结果不会写回时间线。",
        )
    if normalized_targets != set(group.subtitle_ids):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUBBING_GROUP_TARGET_MISMATCH",
            "任务冻结的字幕范围与当前生成组不一致，结果不会写回时间线。",
        )


def finalize_single_tts_submission(request: GenerateRequest) -> GenerateRequest:
    """Revalidate a bound generation against the persisted project snapshot."""
    if request.source != "video_localization" or not request.bind_to_video_localization:
        return request
    if not request.project_id or not request.segment_id:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_TTS_HANDOFF_INCOMPLETE",
            "配音关联参数不完整，请返回视频本土化页面重新发送",
        )
    target_subtitle_ids = [
        str(value)
        for value in request.video_localization_target_subtitle_ids
        if str(value)
    ]
    if not target_subtitle_ids:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_TTS_TARGET_SELECTION_MISSING",
            "当前任务缺少本土化字幕选择，请返回项目重新选择字幕后生成",
        )

    with _DRAFT_WRITE_LOCK:
        workflow_is_new = not bool(request.video_localization_workflow_id)
        submission_id = str(request.video_localization_submission_id or "").strip()
        draft = draft_store.get(request.project_id)
        if draft is None:
            raise AppException(404, "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND", "视频本土化项目不存在")
        def matches_active_submission(item: VideoLocalizationTtsTask) -> bool:
            generation = next(
                (stage for stage in item.stages if stage.kind == "generation"),
                None,
            )
            parameters = generation.parameters if generation is not None else {}
            status_is_active = item.status in {"queued", "running"}
            if item.status == "prepared":
                try:
                    prepared_at = datetime.fromisoformat(
                        item.updated_at.replace("Z", "+00:00")
                    )
                    current_time = datetime.now(tz=prepared_at.tzinfo)
                    status_is_active = (
                        current_time - prepared_at
                    ).total_seconds() <= 120
                except ValueError:
                    status_is_active = False
            return (
                item.segment_id == request.segment_id
                and status_is_active
                and str(parameters.get("custom_reference_source_audio_path") or "")
                == str(request.custom_reference_source_audio_path or "")
                and item.workflow_id not in {
                    str(request.video_localization_workflow_id or ""),
                    submission_id,
                }
            )

        active_for_segment = next(
            (
                item
                for item in reversed(draft.tts_tasks)
                if matches_active_submission(item)
            ),
            None,
        ) if not request.video_localization_workflow_id else None
        if active_for_segment is not None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_SEGMENT_BUSY",
                "这段字幕已经在生成，请等待当前任务完成",
                {
                    "workflow_id": active_for_segment.workflow_id,
                    "status": active_for_segment.status,
                    "generation_task_id": active_for_segment.generation_task_id,
                    "segment_id": request.segment_id,
                },
            )
        if request.video_localization_workflow_id:
            workflow = next(
                (item for item in draft.tts_tasks if item.workflow_id == request.video_localization_workflow_id),
                None,
            )
            if workflow is None or workflow.segment_id != request.segment_id:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_TTS_WORKFLOW_STALE",
                    "配音任务已失效，请返回视频本土化页面重新发送",
                )
        elif submission_id:
            existing_submission = next(
                (item for item in draft.tts_tasks if item.workflow_id == submission_id),
                None,
            )
            if existing_submission is not None:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_TTS_WORKFLOW_ALREADY_SUBMITTED",
                    "该配音任务已经提交，请等待当前任务完成",
                    {
                        "workflow_id": existing_submission.workflow_id,
                        "status": existing_submission.status,
                        "generation_task_id": existing_submission.generation_task_id,
                    },
                )
            _prepared_request, workflow = _prepare_single_tts_handoff(
                request.project_id,
                draft,
                request.segment_id,
                parameters=request.model_dump(mode="json"),
                timeline_clip_id=request.timeline_clip_id,
                target_subtitle_ids=target_subtitle_ids,
                source_cue_ids=request.video_localization_source_cue_ids or None,
                voice_library_voice_id=(
                    request.voice_id
                    if request.voice_source == VoiceSource.voice_library
                    else None
                ),
                workflow_id=submission_id,
            )
            request = request.model_copy(update={"video_localization_workflow_id": submission_id})
        else:
            # Generate-page previews intentionally carry no workflow id. Create
            # the authoritative workflow only at the actual submit boundary.
            _prepared_request, workflow = _prepare_single_tts_handoff(
                request.project_id,
                draft,
                request.segment_id,
                parameters=request.model_dump(mode="json"),
                timeline_clip_id=request.timeline_clip_id,
                target_subtitle_ids=target_subtitle_ids,
                source_cue_ids=request.video_localization_source_cue_ids or None,
                voice_library_voice_id=(
                    request.voice_id
                    if request.voice_source == VoiceSource.voice_library
                    else None
                ),
            )
            request = request.model_copy(update={"video_localization_workflow_id": workflow.workflow_id})
        workflow = _reconcile_tts_workflow_task(workflow)
        repeat_submission = workflow.status in {"success", "failed", "cancelled"}
        if not repeat_submission and (workflow.status != "prepared" or workflow.generation_task_id):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_WORKFLOW_ALREADY_SUBMITTED",
                "该配音任务正在执行，请等待完成后再生成",
                {"workflow_id": workflow.workflow_id, "status": workflow.status},
            )

        generation_stage = next((stage for stage in workflow.stages if stage.kind == "generation"), None)
        prepared_parameters = generation_stage.parameters if generation_stage else {}
        frozen_recovery = prepared_parameters.get("video_localization_recovery")
        submitted_recovery = request.video_localization_recovery.model_dump(mode="json") if request.video_localization_recovery else None
        if frozen_recovery != submitted_recovery:
            raise AppException(409, "DUBBING_RECOVERY_DECISION_CHANGED", "恢复方案已冻结，不能在提交时替换。")
        if request.video_localization_recovery:
            from app.domains.video_localization.dubbing_recovery import validate_recovery_decision
            validate_recovery_decision(draft, request.video_localization_recovery)
        frozen_pack = prepared_parameters.get("video_localization_parameter_pack")
        if not isinstance(frozen_pack, dict):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_SELECTION_SNAPSHOT_MISSING",
                "这条任务不是由当前本土化字幕选择流程创建的，请返回项目重新选择字幕后生成",
                {"workflow_id": workflow.workflow_id, "segment_id": request.segment_id},
            )
        frozen_target = frozen_pack.get("target") if isinstance(frozen_pack.get("target"), dict) else {}
        frozen_source = frozen_pack.get("source") if isinstance(frozen_pack.get("source"), dict) else {}
        current_selection = build_selection_snapshot(
            draft,
            TtsSelectionRequest(
                target_subtitle_ids=[str(value) for value in frozen_target.get("subtitle_ids", [])],
                source_cue_ids=[str(value) for value in frozen_source.get("cue_ids", [])],
            ),
        )
        selection_changed = (
            current_selection.target.model_dump(mode="json") != frozen_target
            or current_selection.source.model_dump(mode="json") != frozen_source
        )
        planned_group = _planned_dubbing_group_for_target(
            draft,
            target_subtitle_ids=current_selection.target.subtitle_ids,
        )
        snapshot = {
            "segment_id": current_selection.target.segment_id,
            "localized_subtitle_id": current_selection.target.subtitle_ids[0],
            "cue_id": current_selection.source.cue_ids[0],
            "source_cue_ids": current_selection.source.cue_ids,
            "target_subtitle_ids": current_selection.target.subtitle_ids,
            "start_ms": (
                planned_group.target_start_ms if planned_group is not None else current_selection.target.start_ms
            ),
            "end_ms": (
                planned_group.target_end_ms if planned_group is not None else current_selection.target.end_ms
            ),
            "text": (planned_group.spoken_text if planned_group is not None else current_selection.target.text),
        }
        if (
            selection_changed
            or workflow.text != snapshot["text"]
            or workflow.start_ms != snapshot["start_ms"]
            or workflow.end_ms != snapshot["end_ms"]
            or workflow.source_cue_ids != snapshot["source_cue_ids"]
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_SUBTITLE_CHANGED",
                "字幕内容或出入点已经变化，请返回项目重新发送后再生成",
                {
                    "workflow_id": workflow.workflow_id,
                    "segment_id": request.segment_id,
                    "current_start_ms": snapshot["start_ms"],
                    "current_end_ms": snapshot["end_ms"],
                },
            )

        prepared_source_path = str(prepared_parameters.get("custom_reference_source_audio_path") or "")
        submitted_source_path = str(request.custom_reference_source_audio_path or "")
        if (
            not prepared_source_path
            or not submitted_source_path
            or Path(prepared_source_path).expanduser().resolve(strict=False)
            != Path(submitted_source_path).expanduser().resolve(strict=False)
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_REFERENCE_SOURCE_CHANGED",
                "参考音频源文件已经变化，请返回本土化项目重新发送后再调整选区",
                {"workflow_id": workflow.workflow_id, "segment_id": request.segment_id},
            )
        submitted_ref_text = (request.ref_text or "").strip()
        if not submitted_ref_text:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TTS_REFERENCE_TEXT_MISSING",
                "参考音频台词为空，请在语音合成页点击“识别并使用”或填写准确台词后再生成",
                {"workflow_id": workflow.workflow_id, "segment_id": request.segment_id},
            )
        authoritative = {
            key: value
            for key, value in prepared_parameters.items()
            if key in _TTS_HANDOFF_AUTHORITATIVE_FIELDS
            and key not in {"text", *_TTS_HANDOFF_SUBMISSION_REFERENCE_FIELDS}
        }
        if authoritative.get("voice_source") is not None:
            authoritative["voice_source"] = VoiceSource(
                authoritative["voice_source"]
            )
        if authoritative.get("reference_audio_license_status") is not None:
            authoritative["reference_audio_license_status"] = LicenseStatus(
                authoritative["reference_audio_license_status"]
            )
        managed_production_submission = request.video_localization_execution_scope in {
            "single_group",
            "all_remaining",
        }
        authoritative.update(
            {
                "source": "video_localization",
                "project_id": request.project_id,
                "segment_id": request.segment_id,
                "localized_subtitle_id": snapshot["localized_subtitle_id"],
                "cue_id": snapshot["cue_id"],
                "bind_to_video_localization": True,
                "video_localization_workflow_id": workflow.workflow_id,
                "video_localization_start_ms": snapshot["start_ms"],
                "video_localization_end_ms": snapshot["end_ms"],
                "video_localization_target_subtitle_ids": snapshot["target_subtitle_ids"],
                "video_localization_source_cue_ids": snapshot["source_cue_ids"],
                # These two values are owned by the canonical dubbing
                # executor, not by the older prepared-handoff snapshot.  If
                # the snapshot's nulls overwrite them, a successful task is
                # mistaken for a manual one and never enters the shared
                # finisher or queues the next semantic group.
                "video_localization_execution_scope": (
                    request.video_localization_execution_scope
                ),
                "video_localization_generation_attempt": (
                    request.video_localization_generation_attempt
                ),
                "video_localization_dubbing_review_mode": (
                    request.video_localization_dubbing_review_mode
                ),
                "video_localization_execution_start_group_id": (
                    request.video_localization_execution_start_group_id
                ),
                "video_localization_execution_end_group_id": (
                    request.video_localization_execution_end_group_id
                ),
                "video_localization_max_in_flight_groups": (
                    request.video_localization_max_in_flight_groups
                ),
                "video_localization_ordinary_speed_baseline": (
                    request.video_localization_ordinary_speed_baseline
                ),
                **(
                    _dubbing_lineage_for_target(
                        draft,
                        snapshot["target_subtitle_ids"],
                    )
                    if managed_production_submission
                    else {
                        "video_localization_dubbing_plan_revision": None,
                        "video_localization_dubbing_group_id": None,
                    }
                ),
            }
        )
        finalized = GenerateRequest.model_validate(
            {
                **request.model_dump(),
                **authoritative,
                "text": _normalize_single_tts_submission_text(request.text, snapshot["text"]),
                "ref_text": submitted_ref_text,
            }
        )
        finalized = _normalize_tts_handoff_reference(finalized, segment_id=request.segment_id)

        compact_summary = re.sub(r"\s+", " ", finalized.text).strip()
        generation_parameters = {
            **finalized.model_dump(mode="json"),
            "handoff_mode": "repeat" if repeat_submission else prepared_parameters.get("handoff_mode", "default"),
            "history_result_id": prepared_parameters.get("history_result_id"),
            **({"video_localization_parameter_pack": frozen_pack} if isinstance(frozen_pack, dict) else {}),
            **({"previous_workflow_id": workflow.workflow_id} if repeat_submission else {}),
        }
        if repeat_submission:
            submission_workflow = VideoLocalizationTtsTask(
                project_id=request.project_id,
                segment_id=request.segment_id,
                subtitle_summary=compact_summary[:60] + ("…" if len(compact_summary) > 60 else ""),
                text=finalized.text,
                source_cue_ids=snapshot["source_cue_ids"],
                start_ms=snapshot["start_ms"],
                end_ms=snapshot["end_ms"],
                stages=[
                    VideoLocalizationTtsTaskStage(kind="generation", parameters=generation_parameters),
                    VideoLocalizationTtsTaskStage(
                        kind="placement",
                        parameters={
                            "target_start_ms": snapshot["start_ms"],
                            "target_end_ms": snapshot["end_ms"],
                            "source_cue_ids": snapshot["source_cue_ids"],
                            **(
                                {
                                    "target_snapshot": frozen_pack.get("target", {}),
                                    "source_snapshot": frozen_pack.get("source", {}),
                                }
                                if isinstance(frozen_pack, dict)
                                else {}
                            ),
                        },
                    ),
                ],
            )
        else:
            generation_stage = workflow.stages[0].model_copy(update={"parameters": generation_parameters})
            submission_workflow = workflow.model_copy(
                update={
                    "subtitle_summary": compact_summary[:60] + ("…" if len(compact_summary) > 60 else ""),
                    "text": finalized.text,
                    "stages": [generation_stage, workflow.stages[1]],
                    "updated_at": now_iso(),
                }
            )

        finalized = finalized.model_copy(update={"video_localization_workflow_id": submission_workflow.workflow_id})
        generation_stage = submission_workflow.stages[0].model_copy(
            update={
                "parameters": {
                    **submission_workflow.stages[0].parameters,
                    "video_localization_workflow_id": submission_workflow.workflow_id,
                }
            }
        )
        submission_workflow = submission_workflow.model_copy(
            update={"stages": [generation_stage, submission_workflow.stages[1]]}
        )
        if workflow_is_new:
            next_tasks = [*draft.tts_tasks, submission_workflow]
        elif not repeat_submission:
            next_tasks = [
                submission_workflow if item.workflow_id == workflow.workflow_id else item for item in draft.tts_tasks
            ]
        else:
            reconciled_tasks = [
                workflow if item.workflow_id == workflow.workflow_id else item for item in draft.tts_tasks
            ]
            next_tasks = [*reconciled_tasks, submission_workflow]
        draft_store.save(
            request.project_id,
            draft.model_copy(update={"tts_tasks": next_tasks}),
            intent="runtime",
        )
        return finalized


_UNRESOLVED_GENERATION_TASK = object()


def _reconcile_tts_workflow_task(
    task: VideoLocalizationTtsTask,
    generation_task: object = _UNRESOLVED_GENERATION_TASK,
) -> VideoLocalizationTtsTask:
    if generation_task is _UNRESOLVED_GENERATION_TASK:
        generation_task = (
            task_queue.get_task(task.generation_task_id)
            if task.generation_task_id
            else task_queue.get_task_by_video_localization_workflow_id(
                task.workflow_id
            )
        )
    if generation_task is None:
        if task.status == "prepared":
            try:
                prepared_at = datetime.fromisoformat(task.updated_at)
                prepared_age = (datetime.now() - prepared_at).total_seconds()
            except (TypeError, ValueError):
                prepared_at = None
                prepared_age = 0
            if prepared_age >= _TTS_PREPARED_SUBMISSION_TIMEOUT_SECONDS:
                # This is recovery for a historical process interruption, not a
                # failure that happened when the task panel happened to read it.
                # Preserve the actual expiry time so an old orphan cannot jump
                # above a newly completed successful task in recency ordering.
                interrupted_at = (
                    prepared_at
                    + timedelta(seconds=_TTS_PREPARED_SUBMISSION_TIMEOUT_SECONDS)
                ).isoformat() if prepared_at is not None else task.updated_at
                interrupted = _replace_tts_stage(
                    task,
                    "generation",
                    status="failed",
                    progress=1.0,
                    error_code="VIDEO_LOCALIZATION_TTS_SUBMISSION_INTERRUPTED",
                    error_message="任务未能进入生成队列，请重新生成",
                    completed_at=interrupted_at,
                )
                reconciled = _with_tts_task_status(interrupted)
                return reconciled.model_copy(
                    update={
                        "completed_at": interrupted_at,
                        "updated_at": interrupted_at,
                    }
                )
        return task
    if not task.generation_task_id:
        task = task.model_copy(
            update={"generation_task_id": generation_task.task_id}
        )
    status_value = generation_task.status.value
    current_generation = next(stage for stage in task.stages if stage.kind == "generation")
    if current_generation.status == "success" and status_value in {
        "pending",
        "queued",
        "retrying",
        "running",
        "postprocessing",
    }:
        return task
    if status_value in {"pending", "queued", "retrying"}:
        stage_status = "queued"
    elif status_value in {"running", "postprocessing"}:
        stage_status = "running"
    else:
        stage_status = status_value
    stage_updates: dict[str, Any] = {
        "status": stage_status,
        "progress": 1.0 if stage_status == "success" else float(generation_task.progress or 0.0),
        "error_code": "TTS_GENERATION_FAILED" if stage_status == "failed" else None,
        "error_message": generation_task.error_message if stage_status == "failed" else None,
        "started_at": generation_task.started_at,
        "completed_at": generation_task.completed_at if stage_status in {"success", "failed", "cancelled"} else None,
    }
    updated = _replace_tts_stage(task, "generation", **stage_updates)
    return _with_tts_task_status(updated)


def reconcile_tts_workflow_tasks(
    tasks: list[VideoLocalizationTtsTask],
    *,
    known_generation_tasks: dict[str, object] | None = None,
) -> list[VideoLocalizationTtsTask]:
    """Project persisted workflows through the single runtime-status source."""

    generation_tasks = dict(known_generation_tasks or {})
    missing_task_ids = {
        task.generation_task_id
        for task in tasks
        if task.generation_task_id
        and task.generation_task_id not in generation_tasks
    }
    generation_tasks.update(task_queue.get_tasks_by_ids(missing_task_ids))
    return [
        _reconcile_tts_workflow_task(
            task,
            generation_tasks.get(task.generation_task_id)
            if task.generation_task_id
            else _UNRESOLVED_GENERATION_TASK,
        )
        for task in tasks
    ]


def _with_dubbing_workflow_attention(
    tasks: list[VideoLocalizationTtsTask],
    *,
    groups: list[object],
    capacity_group_ids: set[str] | None = None,
) -> list[VideoLocalizationTtsTask]:
    """Expose stopped managed close-out as attention, never fake activity.

    Generation and candidate storage may finish before the Agent can complete
    gap processing, semantic review, recovery, or timeline reconciliation. The
    persisted placement receipt remains open for the eventual atomic adopter,
    but an open receipt is not proof that a background owner is still running.
    """

    attention_by_workflow_id: dict[str, str] = {}
    capacity_groups = capacity_group_ids or set()
    for group in groups:
        stage = str(getattr(group, "stage", "") or "")
        action = str(getattr(group, "recommended_action", "") or "")
        group_id = str(getattr(group, "group_id", "") or "")
        if group_id in capacity_groups:
            action = "resolve_capacity"
        elif stage == "failed":
            action = action or "process_gaps"
        elif stage not in {
            "needs_gap_processing",
            "needs_regeneration",
            "needs_timeline_work",
            "needs_timeline_edit",
            "needs_semantic_review",
        }:
            continue
        if action not in {
            "process_gaps",
            "regenerate_candidate",
            "place_candidate",
            "edit_timeline",
            "review_semantic_boundaries",
            "resolve_capacity",
        }:
            continue
        for workflow_id in getattr(group, "workflow_ids", []) or []:
            if workflow_id:
                attention_by_workflow_id[str(workflow_id)] = action

    projected: list[VideoLocalizationTtsTask] = []
    for task in tasks:
        generation = next(
            stage for stage in task.stages if stage.kind == "generation"
        )
        placement = next(
            stage for stage in task.stages if stage.kind == "placement"
        )
        required_action = attention_by_workflow_id.get(task.workflow_id)
        if (
            required_action is not None
            and not _TTS_CLOSEOUT_OWNER_LOOKUP(task.workflow_id)
            and task.status == "running"
            and generation.status == "success"
            and placement.status in {"pending", "running"}
        ):
            task = task.model_copy(
                update={
                    "status": "needs_attention",
                    "required_action": required_action,
                }
            )
        projected.append(task)
    return projected


def configure_tts_closeout_owner_lookup(
    lookup: Callable[[str], bool],
) -> None:
    """Inject the process-local close-out ownership query at composition root."""

    global _TTS_CLOSEOUT_OWNER_LOOKUP
    _TTS_CLOSEOUT_OWNER_LOOKUP = lookup


def _project_current_dubbing_workflow_attention(
    project_id: str,
    tasks: list[VideoLocalizationTtsTask],
) -> list[VideoLocalizationTtsTask]:
    """Join the two backend read models without mutating the Project."""

    if not any(
        task.status == "running"
        and any(
            stage.kind == "generation" and stage.status == "success"
            for stage in task.stages
        )
        and any(
            stage.kind == "placement" and stage.status in {"pending", "running"}
            for stage in task.stages
        )
        for task in tasks
    ):
        return tasks

    draft = draft_store.get(project_id)
    plan = draft.dubbing_production.active_plan if draft is not None else None
    if draft is None or plan is None:
        return tasks
    from app.domains.video_localization import dubbing_production as production_domain
    from app.domains.video_localization import dubbing_production_run

    discarded = {
        str(value)
        for value in draft.ui_state.get("discarded_tts_task_ids", [])
        if str(value)
    }
    run_workflows = [
        task
        for task in tasks
        if not discarded.intersection(
            {
                str(task.workflow_id or ""),
                str(task.generation_task_id or ""),
                str(task.result_id or ""),
            }
        )
    ]
    run = dubbing_production_run.build_production_run_snapshot(
        current_source_revision=production_domain.dubbing_source_revision(draft),
        active_plan=plan,
        workflows=run_workflows,
        candidate_inputs=list(draft.dubbing_production.candidate_inputs),
        candidate_reports=list(draft.dubbing_production.candidate_reports),
        group_failures=list(draft.dubbing_production.group_failures),
        timeline_clips=[dict(item) for item in draft.timeline_clips],
        manual_timing_deferrals=list(
            draft.dubbing_production.manual_timing_deferrals
        ),
        existing_formal_acceptances=list(
            draft.dubbing_production.existing_formal_acceptances
        ),
    )
    capacity_group_ids = {
        failure.group_id
        for failure in draft.dubbing_production.group_failures
        if failure.source_revision == plan.source_revision
        and failure.plan_revision == plan.plan_revision
        and failure.reason_code == "group_capacity_recovery_decision_required"
    }
    return _with_dubbing_workflow_attention(
        tasks,
        groups=list(run.groups),
        capacity_group_ids=capacity_group_ids,
    )


def list_tts_tasks(project_id: str) -> list[VideoLocalizationTtsTask] | None:
    # Polling is a read projection. Runtime progress already belongs to the
    # generation-task store; writing it back into the full project draft on
    # every poll makes N queued clips repeatedly serialize the whole project
    # and contend with placement. Durable workflow transitions are persisted by
    # registration, terminal, placement, cancellation, and deletion commands.
    workflow_projection = video_localization_tts_workflow_store.read_project(
        project_id
    )
    if workflow_projection.authoritative:
        persisted_tasks = list(workflow_projection.tasks)
    else:
        project_exists, projected_tasks = (
            project_store.get_video_localization_tts_tasks_projection(
                project_id
            )
        )
        if not project_exists:
            return None
        if projected_tasks is None:
            draft = draft_store.get(project_id)
            if draft is None:
                return None
            persisted_tasks = draft.tts_tasks
        else:
            try:
                persisted_tasks = [VideoLocalizationTtsTask.model_validate(item) for item in projected_tasks]
            except ValidationError:
                draft = draft_store.get(project_id)
                if draft is None:
                    return None
                persisted_tasks = draft.tts_tasks
    reconciled = _project_current_dubbing_workflow_attention(
        project_id,
        reconcile_tts_workflow_tasks(persisted_tasks),
    )
    return list(reversed(reconciled))


def get_tts_task_feed(
    project_id: str,
    *,
    after_revision: str | None = None,
) -> VideoLocalizationTtsTaskFeed | None:
    """Return only workflows changed since the caller's project revision."""

    prefix = "projection:"
    previous_projection_revision = 0
    previous_attention_fingerprint: str | None = None
    if after_revision:
        match = re.fullmatch(
            r"projection:(\d+):attention:([0-9a-f]{16})",
            after_revision,
        )
        if match is not None:
            previous_projection_revision = int(match.group(1))
            previous_attention_fingerprint = match.group(2)

    # Attention can change when candidate evidence or a production disposition
    # changes without rewriting the workflow row. Build that small joined state
    # from the current project, but preserve the workflow store's row-level
    # incremental response when only generation runtime changed.
    full_projection = video_localization_tts_workflow_store.read_feed(
        project_id,
        after_revision=0,
    )
    if full_projection.authoritative:
        runtime_by_task_id = {
            runtime.task_id: runtime
            for _, runtime in full_projection.generation_runtimes
        }
        tasks = _project_current_dubbing_workflow_attention(
            project_id,
            reconcile_tts_workflow_tasks(
                list(full_projection.tasks),
                known_generation_tasks=runtime_by_task_id,
            ),
        )
        attention_fingerprint = hashlib.sha256(
            json.dumps(
                [
                    {
                        "workflow_id": task.workflow_id,
                        "required_action": (
                            task.required_action
                            if task.status == "needs_attention"
                            else None
                        ),
                    }
                    for task in tasks
                ],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        revision = (
            f"{prefix}{full_projection.revision}:attention:{attention_fingerprint}"
        )
        if after_revision == revision:
            return VideoLocalizationTtsTaskFeed(
                revision=revision,
                changed=False,
            )
        response_tasks = tasks
        if (
            previous_projection_revision > 0
            and previous_projection_revision != full_projection.revision
            and previous_attention_fingerprint == attention_fingerprint
        ):
            delta = video_localization_tts_workflow_store.read_feed(
                project_id,
                after_revision=previous_projection_revision,
            )
            changed_workflow_ids = {
                task.workflow_id for task in delta.tasks
            }
            response_tasks = [
                task
                for task in tasks
                if task.workflow_id in changed_workflow_ids
            ]
        return VideoLocalizationTtsTaskFeed(
            revision=revision,
            changed=True,
            workflow_ids=list(full_projection.workflow_ids),
            tasks=response_tasks,
        )

    tasks = list_tts_tasks(project_id)
    if tasks is None:
        return None
    payload = json.dumps(
        [task.model_dump(mode="json") for task in tasks],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    revision = "legacy:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if after_revision == revision:
        return VideoLocalizationTtsTaskFeed(
            revision=revision,
            changed=False,
        )
    return VideoLocalizationTtsTaskFeed(
        revision=revision,
        changed=True,
        workflow_ids=[task.workflow_id for task in reversed(tasks)],
        tasks=tasks,
    )


def get_tts_task(project_id: str, workflow_id: str) -> VideoLocalizationTtsTask | None:
    tasks = list_tts_tasks(project_id)
    if tasks is None:
        return None
    return next((task for task in tasks if task.workflow_id == workflow_id), None)


def cancel_tts_task(project_id: str, workflow_id: str) -> VideoLocalizationTtsTask | None:
    """Cancel a video-localization TTS workflow at its owning task boundary."""
    generation_task_id: str | None = None
    with _DRAFT_WRITE_LOCK:
        draft = draft_store.get(project_id)
        if draft is None:
            return None
        workflow = next((item for item in draft.tts_tasks if item.workflow_id == workflow_id), None)
        if workflow is None:
            return None
        if workflow.status in {"success", "failed", "cancelled"}:
            return workflow

        generation_task_id = workflow.generation_task_id
        discarded = {str(value) for value in draft.ui_state.get("discarded_tts_task_ids", []) if isinstance(value, str)}
        discarded.add(workflow_id)
        if generation_task_id:
            discarded.add(generation_task_id)
        latest = draft.ui_state.get("latest_tts_task_by_segment", {})
        next_latest = dict(latest) if isinstance(latest, dict) else {}
        if generation_task_id and str(next_latest.get(workflow.segment_id) or "") == generation_task_id:
            next_latest.pop(workflow.segment_id, None)

        def mark_cancelled(item: VideoLocalizationTtsTask) -> VideoLocalizationTtsTask:
            updated = item
            for kind in ("generation", "placement"):
                stage = next((candidate for candidate in updated.stages if candidate.kind == kind), None)
                if stage and stage.status not in {"success", "failed", "cancelled"}:
                    updated = _replace_tts_stage(
                        updated,
                        kind,
                        status="cancelled",
                        progress=stage.progress,
                        error_code="VIDEO_LOCALIZATION_TTS_CANCELLED",
                        error_message="配音任务已停止",
                        completed_at=now_iso(),
                    )
            return updated

        next_draft = _update_tts_task(draft, workflow_id, mark_cancelled)
        next_draft = next_draft.model_copy(
            update={
                "ui_state": {
                    **next_draft.ui_state,
                    "discarded_tts_task_ids": sorted(discarded),
                    "latest_tts_task_by_segment": next_latest,
                }
            }
        )
        saved = draft_store.save(project_id, next_draft, intent="content")
        cancelled = next((item for item in saved.tts_tasks if item.workflow_id == workflow_id), workflow)

    if generation_task_id:
        if generation_task_id.startswith("longform:"):
            from app.services import longform_queue

            try:
                longform_queue.cancel_longform(generation_task_id.removeprefix("longform:"))
            except AppException:
                pass
        else:
            task_queue.cancel_task(generation_task_id)
    return cancelled


def delete_tts_task(project_id: str, workflow_id: str) -> VideoLocalizationDraft | None:
    """Remove a workflow and any timeline fragment it owns, stopping it first when active."""
    cancelled = cancel_tts_task(project_id, workflow_id)
    if cancelled is None:
        return None

    with _DRAFT_WRITE_LOCK:
        draft = draft_store.get(project_id)
        if draft is None:
            return None
        workflow = next((item for item in draft.tts_tasks if item.workflow_id == workflow_id), cancelled)
        task_ids = {value for value in [workflow.generation_task_id] if value}
        discarded = {str(value) for value in draft.ui_state.get("discarded_tts_task_ids", []) if isinstance(value, str)}
        discarded.add(workflow_id)
        discarded.update(task_ids)
        target_clip_id = str(workflow.timeline_clip_id or "")

        def belongs_to_deleted_workflow(clip: dict[str, Any]) -> bool:
            # A stable timeline slot can now contain a different take. Media
            # provenance outranks its old slot binding or runtime marker.
            result_id = str(clip.get("result_id") or "")
            if result_id and workflow.result_id:
                return result_id == workflow.result_id
            identity = str(clip.get("task_id") or clip.get("generation_id") or "")
            if identity:
                return identity in task_ids
            if result_id:
                return False
            return (
                (bool(target_clip_id) and str(clip.get("clip_id") or "") == target_clip_id)
                or str(clip.get("optimistic_tts_workflow_id") or "") == workflow_id
            )

        next_draft = draft.model_copy(
            update={
                "tts_tasks": [item for item in draft.tts_tasks if item.workflow_id != workflow_id],
                "timeline_clips": [clip for clip in draft.timeline_clips if not belongs_to_deleted_workflow(clip)],
                "generated_candidates": [
                    candidate
                    for candidate in draft.generated_candidates
                    if not (
                        str(candidate.get("task_id") or "") in task_ids
                        and candidate.get("status") in {"queued", "running", "postprocessing", "retrying"}
                    )
                ],
                "ui_state": {
                    **draft.ui_state,
                    "discarded_tts_task_ids": sorted(discarded),
                },
            }
        )
        return draft_store.save(project_id, next_draft, intent="content")


def export_subtitles(
    project_id: str,
    kind: str,
    *,
    localized_variant: str = "localized",
) -> str | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    is_dub_delivery = kind == "zh" and localized_variant == "dub"
    if is_dub_delivery:
        blockers = quality_gate.dub_subtitle_export_blockers(
            draft,
            current_audio_sha256_by_clip_id=(
                dubbing_media.current_timeline_audio_sha256s(
                    project_id,
                    draft,
                )
            ),
        )
    else:
        blockers = quality_gate.subtitle_export_blockers(draft, kind)
    if blockers:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED",
            f"字幕未通过导出检查，请先处理 {len(blockers)} 个时间或内容问题。",
            {"issues": [issue.model_dump(mode="json") for issue in blockers]},
        )
    subtitle_text = exporting.export_subtitles(
        draft,
        kind,
        localized_variant=localized_variant,
    )
    if not is_dub_delivery:
        return subtitle_text

    # A standalone SRT response is a delivery artifact too.  Re-read the
    # project and managed audio under SQLite's cross-process writer boundary so
    # a worker cannot replace the accepted timeline between CQC and publish.
    with _DRAFT_WRITE_LOCK:
        with project_store.publication_guard(project_id) as locked_project:
            if locked_project is None:
                return None
            current = draft_store.from_project(locked_project)
            current_blockers = quality_gate.dub_subtitle_export_blockers(
                current,
                current_audio_sha256_by_clip_id=(
                    dubbing_media.current_timeline_audio_sha256s(
                        project_id,
                        current,
                    )
                ),
            )
            if current_blockers:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED",
                    ("配音或配音字幕在导出期间发生了变化，请重新检查后再导出。"),
                    {"issues": [issue.model_dump(mode="json") for issue in current_blockers]},
                )
            current_text = exporting.export_subtitles(
                current,
                kind,
                localized_variant=localized_variant,
            )
            if current_text != subtitle_text:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_INPUT_CHANGED",
                    "配音字幕在导出期间发生了变化，请重新导出。",
                )
            return subtitle_text


def sync_single_tts_result(
    project_id: str,
    cue_id: str,
    *,
    result_id: str,
    output_path: str,
    duration_ms: int | None,
    task_id: str | None = None,
    generation_id: str | None = None,
    workflow_id: str | None = None,
    timeline_clip_id: str | None = None,
    dubbing_plan_revision: int | None = None,
    dubbing_group_id: str | None = None,
    dubbing_target_subtitle_ids: list[str] | None = None,
    handoff_claim: TtsHandoffClaim | None = None,
) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None

    if not workflow_id and task_id:
        current = draft_store.get(project_id)
        if current is not None:
            workflow = next(
                (item for item in reversed(current.tts_tasks) if item.generation_task_id == task_id),
                None,
            )
            workflow_id = workflow.workflow_id if workflow else None

    def apply_result(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
        placement_workflow = next(
            (item for item in draft.tts_tasks if workflow_id and item.workflow_id == workflow_id),
            None,
        )
        placement_stage = (
            next((stage for stage in placement_workflow.stages if stage.kind == "placement"), None)
            if placement_workflow is not None else None
        )
        # Completion belongs to the command, not to the editable clip. A late
        # delivery must not recreate deleted pieces or reset a user's crop.
        if (
            placement_workflow is not None
            and placement_stage is not None
            and placement_stage.status == "success"
            and placement_workflow.result_id == result_id
            and placement_workflow.generation_task_id == task_id
        ):
            return draft
        discarded_task_ids = {
            str(value) for value in draft.ui_state.get("discarded_tts_task_ids", []) if isinstance(value, str)
        }
        if task_id and task_id in discarded_task_ids:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_RESULT_DISCARDED",
                "该配音已被用户移除，不再自动放回时间线；历史音频仍可手动采用。",
            )
        latest_tasks = draft.ui_state.get("latest_tts_task_by_segment", {})
        latest_task_id = str(latest_tasks.get(cue_id) or "") if isinstance(latest_tasks, dict) else ""
        if task_id and latest_task_id and latest_task_id != task_id:
            return draft

        _require_current_dubbing_task_lineage(
            draft,
            plan_revision=dubbing_plan_revision,
            group_id=dubbing_group_id,
            target_subtitle_ids=dubbing_target_subtitle_ids,
        )

        managed_output_path = output_path
        source_path = Path(managed_output_path)
        if not source_path.is_file():
            raise AppException(400, "VIDEO_LOCALIZATION_TTS_AUDIO_NOT_FOUND", "TTS audio file not found")
        adopted_path = media_assets.adopt_tts_audio(project_id, source_path, cue_id, task_id or result_id)
        managed_output_path = str(adopted_path)
        target_snapshot = (
            placement_stage.parameters.get("target_snapshot")
            if placement_stage is not None and isinstance(placement_stage.parameters.get("target_snapshot"), dict)
            else None
        )
        source_snapshot = (
            placement_stage.parameters.get("source_snapshot")
            if placement_stage is not None and isinstance(placement_stage.parameters.get("source_snapshot"), dict)
            else None
        )
        if target_snapshot is None or source_snapshot is None or placement_workflow is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_SELECTION_SNAPSHOT_MISSING",
                "配音任务缺少当前字幕选择快照，结果不会写入时间线",
                {"workflow_id": workflow_id, "task_id": task_id},
            )
        candidate_draft = draft
        candidate_id: str | None = None
        if task_id:
            frozen_source_ids = [str(value) for value in source_snapshot.get("cue_ids", []) if str(value)]
            candidate_draft, candidate_id = tts_pipeline.with_synced_generated_candidate(
                draft,
                cue_id=(frozen_source_ids[0] if frozen_source_ids else cue_id),
                task_id=task_id,
                result_id=result_id,
                output_path=managed_output_path,
                duration_ms=duration_ms,
                subtitle_id=placement_workflow.segment_id,
            )
        generation_stage = next(
            stage
            for stage in placement_workflow.stages
            if stage.kind == "generation"
        )
        managed_production = generation_stage.parameters.get(
            "video_localization_execution_scope"
        ) in {"single_group", "all_remaining"}

        if managed_production:
            # A managed run has one later, evidence-backed commit point. Keep
            # the playable artifact in immutable history/candidate storage,
            # but do not expose a second working take on the timeline while
            # gas processing is still underway.
            managed_candidates = []
            for raw in candidate_draft.generated_candidates:
                candidate = dict(raw)
                if task_id and candidate.get("task_id") == task_id:
                    for legacy_field in (
                        "selected",
                        "accepted",
                        "cqc_status",
                        "cqc_report",
                        "cqc_report_version",
                    ):
                        candidate.pop(legacy_field, None)
                managed_candidates.append(candidate)
            candidate_draft = candidate_draft.model_copy(
                update={"generated_candidates": managed_candidates}
            )

            current_formal_clip = next(
                (
                    dict(item)
                    for item in candidate_draft.timeline_clips
                    if item.get("track_id") == "dub"
                    and int(item.get("dub_lane") or 0) == 0
                    and item.get("status") == "ready"
                    and str(item.get("result_id") or "") == result_id
                ),
                None,
            )
            if current_formal_clip is not None:
                # A recovered generation completion can be replayed after a
                # process restart. If this exact immutable result already owns
                # the formal lane, the replay must close the workflow instead
                # of resetting placement to a fake running state.
                def close_replayed_generation(
                    item: VideoLocalizationTtsTask,
                ) -> VideoLocalizationTtsTask:
                    generated = _replace_tts_stage(
                        item,
                        "generation",
                        status="success",
                        progress=1.0,
                        error_code=None,
                        error_message=None,
                        completed_at=now_iso(),
                    )
                    return generated.model_copy(update={"result_id": result_id})

                candidate_draft = _update_tts_task(
                    candidate_draft,
                    workflow_id,
                    close_replayed_generation,
                )
                return with_completed_tts_placement(
                    candidate_draft,
                    generation_task_id=task_id,
                    result_id=result_id,
                    timeline_clip_id=str(
                        current_formal_clip.get("clip_id") or ""
                    ),
                )

            if placement_stage.status in {"failed", "cancelled"}:
                # Startup/outbox replay may revisit a generation result after
                # the gas-edit finisher already made a terminal placement
                # decision. Replaying immutable media must not resurrect that
                # closed workflow as a fake running task.
                def preserve_terminal_placement(
                    item: VideoLocalizationTtsTask,
                ) -> VideoLocalizationTtsTask:
                    generated = _replace_tts_stage(
                        item,
                        "generation",
                        status="success",
                        progress=1.0,
                        error_code=None,
                        error_message=None,
                        completed_at=now_iso(),
                    )
                    return generated.model_copy(update={"result_id": result_id})

                return _update_tts_task(
                    candidate_draft,
                    workflow_id,
                    preserve_terminal_placement,
                )

            def mark_generated(item: VideoLocalizationTtsTask) -> VideoLocalizationTtsTask:
                generated = _replace_tts_stage(
                    item,
                    "generation",
                    status="success",
                    progress=1.0,
                    error_code=None,
                    error_message=None,
                    completed_at=now_iso(),
                )
                waiting = _replace_tts_stage(
                    generated,
                    "placement",
                    status="running",
                    progress=0.0,
                    error_code=None,
                    error_message=None,
                    started_at=now_iso(),
                    parameters={
                        **next(
                            stage.parameters
                            for stage in item.stages
                            if stage.kind == "placement"
                        ),
                        "result_id": result_id,
                        "duration_ms": duration_ms,
                    },
                )
                return waiting.model_copy(
                    update={
                        "result_id": result_id,
                        "timeline_clip_id": None,
                    }
                )

            return _update_tts_task(
                candidate_draft,
                workflow_id,
                mark_generated,
            )

        synced = tts_placement.with_explicit_single_tts_result(
            candidate_draft,
            segment_id=placement_workflow.segment_id,
            target_subtitle_ids=[str(value) for value in target_snapshot.get("subtitle_ids", [])],
            source_cue_ids=[str(value) for value in source_snapshot.get("cue_ids", [])],
            target_start_ms=int(target_snapshot.get("start_ms", placement_workflow.start_ms)),
            target_end_ms=int(target_snapshot.get("end_ms", placement_workflow.end_ms)),
            target_text=str(target_snapshot.get("text") or placement_workflow.text),
            result_id=result_id,
            output_path=managed_output_path,
            duration_ms=duration_ms,
            task_id=task_id,
            generation_id=generation_id,
            timeline_clip_id=timeline_clip_id,
            candidate_id=candidate_id,
        )
        synced_clip = next(
            (
                dict(item)
                for item in synced.timeline_clips
                if item.get("track_id", "dub") == "dub"
                and (
                    (task_id and item.get("task_id") == task_id)
                    or (task_id and str(item.get("candidate_id") or "").endswith(task_id))
                    or (timeline_clip_id and item.get("clip_id") == timeline_clip_id)
                )
            ),
            None,
        )
        placed = (
            _place_dub_clip_on_free_lane(
                synced,
                str(synced_clip["clip_id"]),
                int(synced_clip.get("dub_lane") or 0),
            )
            if task_id and synced_clip
            else synced
        )
        placed_clip = next(
            (
                dict(item)
                for item in placed.timeline_clips
                if synced_clip and item.get("clip_id") == synced_clip.get("clip_id")
            ),
            synced_clip,
        )
        if placed_clip is None:
            raise AppException(
                500,
                "VIDEO_LOCALIZATION_TTS_PLACEMENT_MISSING",
                "声音已经生成，但没有形成可持久化的配音轨片段。",
                {
                    "workflow_id": workflow_id,
                    "task_id": task_id,
                    "result_id": result_id,
                },
            )
        if not workflow_id:
            return placed

        def mark_success(item: VideoLocalizationTtsTask) -> VideoLocalizationTtsTask:
            generated = _replace_tts_stage(
                item,
                "generation",
                status="success",
                progress=1.0,
                error_code=None,
                error_message=None,
                completed_at=now_iso(),
            )
            updated = _replace_tts_stage(
                generated,
                "placement",
                status="success",
                progress=1.0,
                error_code=None,
                error_message=None,
                completed_at=now_iso(),
                parameters={
                    **next(stage.parameters for stage in item.stages if stage.kind == "placement"),
                    "result_id": result_id,
                    "duration_ms": duration_ms,
                    "timeline_clip_id": str((placed_clip or {}).get("clip_id") or timeline_clip_id or "") or None,
                    "dub_lane": (placed_clip or {}).get("dub_lane"),
                },
            )
            return updated.model_copy(
                update={
                    "result_id": result_id,
                    "timeline_clip_id": str((placed_clip or {}).get("clip_id") or timeline_clip_id or "") or None,
                }
            )

        return _update_tts_task(placed, workflow_id, mark_success)

    try:
        return update_video_localization_atomic(
            project_id,
            apply_result,
            intent="runtime",
            tts_handoff_claim=handoff_claim,
        )
    except Exception as exc:
        if isinstance(exc, AppException) and exc.code == "VIDEO_LOCALIZATION_TTS_RESULT_DISCARDED":
            raise
        if workflow_id:
            error_code = getattr(exc, "code", "VIDEO_LOCALIZATION_TTS_PLACEMENT_FAILED")
            error_message = getattr(exc, "message", str(exc))

            def mark_failed(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
                return _update_tts_task(
                    draft,
                    workflow_id,
                    lambda item: _replace_tts_stage(
                        item,
                        "placement",
                        status="failed",
                        progress=0.0,
                        error_code=error_code,
                        error_message=error_message,
                        completed_at=now_iso(),
                    ),
                )

            update_video_localization_atomic(
                project_id,
                mark_failed,
                intent="runtime",
                tts_handoff_claim=handoff_claim,
            )
        raise


def apply_tts_history_to_timeline_clip(
    project_id: str,
    clip_id: str,
    result_id: str,
    *,
    request_id: str,
) -> VideoLocalizationDraft | None:
    return apply_tts_history_to_timeline(
        project_id,
        result_id,
        request_id=request_id,
        segment_id="",
        clip_id=clip_id,
    )


def _place_dub_clip_on_free_lane(
    draft: VideoLocalizationDraft,
    clip_id: str,
    preferred_lane: int,
) -> VideoLocalizationDraft:
    target = next((dict(item) for item in draft.timeline_clips if item.get("clip_id") == clip_id), None)
    if not target:
        return draft
    start_ms = int(target.get("start_ms") or 0)
    end_ms = max(start_ms + 1, int(target.get("end_ms") or start_ms + 1))
    occupied = [
        dict(item) for item in draft.timeline_clips if item.get("clip_id") != clip_id and item.get("track_id") == "dub"
    ]
    max_lane = max((int(item.get("dub_lane") or 0) for item in occupied), default=0)
    requested = max(0, int(preferred_lane))
    candidates = [requested, *(lane for lane in range(max_lane + 2) if lane != requested)]
    selected_lane = requested
    lane_states = draft.ui_state.get("dub_lane_states", {})
    lane_states = dict(lane_states) if isinstance(lane_states, dict) else {}
    track_states = draft.ui_state.get("track_states", {})
    track_states = dict(track_states) if isinstance(track_states, dict) else {}

    def lane_is_locked(lane: int) -> bool:
        state = lane_states.get(str(lane))
        if not isinstance(state, dict) and lane == 0:
            state = track_states.get("dub")
        return isinstance(state, dict) and state.get("locked") is True

    for lane in candidates:
        if lane_is_locked(lane):
            continue
        if not any(
            int(item.get("dub_lane") or 0) == lane
            and start_ms < int(item.get("end_ms") or 0)
            and end_ms > int(item.get("start_ms") or 0)
            for item in occupied
        ):
            selected_lane = lane
            break
    updated_clips = [
        {**dict(item), "dub_lane": selected_lane} if item.get("clip_id") == clip_id else item
        for item in draft.timeline_clips
    ]
    lane_states.setdefault(
        str(selected_lane),
        {"muted": False, "solo": False, "volume": 1, "locked": False},
    )
    placed = draft.model_copy(
        update={
            "timeline_clips": updated_clips,
            "ui_state": {**draft.ui_state, "dub_lane_states": lane_states},
        }
    )
    return _compact_empty_secondary_dub_lanes(placed)


def _compact_empty_secondary_dub_lanes(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    occupied_lanes = sorted(
        {max(0, int(item.get("dub_lane") or 0)) for item in draft.timeline_clips if item.get("track_id") == "dub"}
    )
    active_lanes = [0, *(lane for lane in occupied_lanes if lane != 0)]
    lane_map = {lane: index for index, lane in enumerate(active_lanes)}
    lane_states = draft.ui_state.get("dub_lane_states", {})
    lane_states = dict(lane_states) if isinstance(lane_states, dict) else {}
    track_states = draft.ui_state.get("track_states", {})
    track_states = dict(track_states) if isinstance(track_states, dict) else {}
    default_state = {"muted": False, "solo": False, "volume": 1, "locked": False}
    compacted_states: dict[str, Any] = {}
    for old_lane, new_lane in lane_map.items():
        state = lane_states.get(str(old_lane))
        if not isinstance(state, dict) and old_lane == 0:
            state = track_states.get("dub")
        compacted_states[str(new_lane)] = dict(state) if isinstance(state, dict) else dict(default_state)
    compacted_clips = [
        {
            **dict(item),
            "dub_lane": lane_map.get(max(0, int(item.get("dub_lane") or 0)), 0),
        }
        if item.get("track_id") == "dub"
        else item
        for item in draft.timeline_clips
    ]
    track_states["dub"] = {**compacted_states["0"]}
    return draft.model_copy(
        update={
            "timeline_clips": compacted_clips,
            "ui_state": {
                **draft.ui_state,
                "track_states": track_states,
                "dub_lane_states": compacted_states,
            },
        }
    )


def apply_tts_history_to_timeline(
    project_id: str,
    result_id: str,
    *,
    request_id: str,
    segment_id: str,
    clip_id: str | None = None,
    new_clip_id: str | None = None,
    start_ms: int | None = None,
    dub_lane: int | None = None,
    force_new: bool = False,
) -> VideoLocalizationDraft | None:
    fingerprint = hashlib.sha256(json.dumps(
        [result_id, segment_id, clip_id, new_clip_id, start_ms, dub_lane, force_new],
        ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()

    def apply_once(current: VideoLocalizationDraft) -> VideoLocalizationDraft:
        previous = current.history_placement_receipts.get(request_id)
        if previous is not None:
            if previous != fingerprint:
                raise AppException(
                    409, "VIDEO_LOCALIZATION_PLACEMENT_REQUEST_CONFLICT",
                    "同一次放入操作的内容发生了变化，请重新发起操作。",
                )
            return current
        history = history_store.get(result_id)
        source_path = history_store.audio_path(result_id) if history else None
        if not history or not source_path:
            raise AppException(404, "VIDEO_LOCALIZATION_TTS_HISTORY_NOT_FOUND", "TTS history audio is not available")
        if history.project_id != project_id or history.parameter_snapshot.get("source") != "video_localization":
            raise AppException(400, "VIDEO_LOCALIZATION_TTS_HISTORY_PROJECT_MISMATCH", "该历史记录不属于当前视频本土化项目")
        adopted_path = media_assets.adopt_tts_audio(
            project_id, source_path,
            history.localized_subtitle_id or history.cue_id or segment_id or clip_id or "history",
            history.task_id or result_id,
        )
        placed = _apply_tts_history_to_draft(
            current,
            history=history,
            result_id=result_id,
            segment_id=segment_id,
            clip_id=clip_id,
            new_clip_id=new_clip_id,
            start_ms=start_ms,
            dub_lane=dub_lane,
            force_new=force_new,
            adopted_path=adopted_path,
        )
        return placed.model_copy(update={"history_placement_receipts": {
            **current.history_placement_receipts, request_id: fingerprint,
        }})

    return update_video_localization_atomic(
        project_id,
        apply_once,
        intent="content",
    )


def commit_local_phrase_repair(
    project_id: str,
    *,
    candidate_clip_id: str,
    replace_clip_ids: list[str],
    target_start_ms: int,
    target_end_ms: int,
    source_start_ms: int,
    source_end_ms: int,
    target_text: str,
) -> VideoLocalizationDraft | None:
    """Adopt one generated repair candidate without exposing a delete gap."""

    def apply(current: VideoLocalizationDraft) -> VideoLocalizationDraft:
        try:
            return tts_placement.replace_with_local_phrase(
                current,
                candidate_clip_id=candidate_clip_id,
                replace_clip_ids=replace_clip_ids,
                target_start_ms=target_start_ms,
                target_end_ms=target_end_ms,
                source_start_ms=source_start_ms,
                source_end_ms=source_end_ms,
                target_text=target_text,
            )
        except KeyError as exc:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_LOCAL_PHRASE_CLIP_NOT_FOUND",
                "局部修补引用的配音片段不存在，请刷新时间线后重试",
                {"reason": str(exc)},
            ) from exc
        except ValueError as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_LOCAL_PHRASE_NOT_READY",
                "局部修补候选尚未可播放，原片段保持不变",
                {"reason": str(exc)},
            ) from exc

    return update_video_localization_atomic(project_id, apply, intent="content")


def _apply_tts_history_to_draft(
    draft: VideoLocalizationDraft,
    *,
    history: Any,
    result_id: str,
    segment_id: str,
    clip_id: str | None,
    new_clip_id: str | None,
    start_ms: int | None,
    dub_lane: int | None,
    force_new: bool,
    adopted_path: Path,
) -> VideoLocalizationDraft:
    if clip_id and new_clip_id:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TIMELINE_CLIP_ID_AMBIGUOUS",
            "一次配音采用命令只能指定一个时间线片段身份",
        )
    if force_new and not new_clip_id:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TIMELINE_CLIP_ID_REQUIRED",
            "新增时间线片段必须提供稳定的片段 ID",
        )
    clip = (
        next((dict(item) for item in draft.timeline_clips if item.get("clip_id") == clip_id), None)
        if clip_id
        else None
    )
    if clip_id and not clip:
        raise AppException(404, "VIDEO_LOCALIZATION_TIMELINE_CLIP_NOT_FOUND", "Timeline clip not found")
    if clip and clip.get("track_id", "dub") != "dub":
        raise AppException(400, "VIDEO_LOCALIZATION_TIMELINE_CLIP_NOT_DUB", "Only dub clips can use TTS history")

    requested_segment_id = str(segment_id or (clip or {}).get("subtitle_id") or (clip or {}).get("cue_id") or "")
    target_segment_ids = list(
        dict.fromkeys(value for value in [requested_segment_id, history.localized_subtitle_id, history.cue_id] if value)
    )
    subtitles_by_id = {item.subtitle_id: item for item in draft.localized_subtitles}
    cues_by_id = {item.cue_id: item for item in draft.cues}
    subtitle = next((subtitles_by_id[item_id] for item_id in target_segment_ids if item_id in subtitles_by_id), None)
    cue = next((cues_by_id[item_id] for item_id in target_segment_ids if item_id in cues_by_id), None)
    if not subtitle and not cue and not clip:
        raise AppException(404, "VIDEO_LOCALIZATION_TTS_SEGMENT_NOT_FOUND", "找不到要采用配音的字幕片段")

    subtitle_id = str((clip or {}).get("subtitle_id") or (subtitle.subtitle_id if subtitle else ""))
    cue_id = str((clip or {}).get("cue_id") or (cue.cue_id if cue else ""))
    if subtitle_id:
        matches_clip = history.localized_subtitle_id == subtitle_id or history.segment_id == subtitle_id
    else:
        matches_clip = history.cue_id == cue_id or history.segment_id == cue_id
    if not matches_clip:
        raise AppException(
            400, "VIDEO_LOCALIZATION_TTS_HISTORY_SEGMENT_MISMATCH", "该历史声音与当前字幕片段不匹配，不能直接替换"
        )

    existing_new_clip = (
        next(
            (dict(item) for item in draft.timeline_clips if item.get("clip_id") == new_clip_id),
            None,
        )
        if new_clip_id
        else None
    )
    if existing_new_clip:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_TIMELINE_CLIP_ID_CONFLICT",
            "这个时间线片段 ID 已被其他片段占用",
        )

    working_draft = draft
    if not clip:
        used_ids = {str(item.get("clip_id") or "") for item in draft.timeline_clips}
        base_clip_id = f"clip_{subtitle_id or cue_id}"
        next_clip_id = new_clip_id or base_clip_id
        if not new_clip_id:
            suffix = 2
            while next_clip_id in used_ids:
                next_clip_id = f"{base_clip_id}_{suffix}"
                suffix += 1
        source_cue_ids = list(dict.fromkeys(subtitle.source_cue_ids)) if subtitle else []
        primary_cue_id = next(iter(source_cue_ids), subtitle.linked_cue_id if subtitle else cue_id)
        natural_start_ms = subtitle.start_ms if subtitle else cue.start_ms
        natural_end_ms = subtitle.end_ms if subtitle else cue.end_ms
        placed_start_ms = max(0, int(start_ms)) if start_ms is not None else natural_start_ms
        placed_end_ms = placed_start_ms + max(1, int(history.duration_ms)) if history.duration_ms else natural_end_ms
        clip = {
            "clip_id": next_clip_id,
            "track_id": "dub",
            "subtitle_id": subtitle_id or None,
            "cue_id": primary_cue_id,
            "source_cue_ids": source_cue_ids,
            "start_ms": placed_start_ms,
            "end_ms": placed_end_ms,
            "status": "ready",
            "manual_history_copy": bool(force_new),
        }
        if dub_lane is not None:
            clip["dub_lane"] = max(0, int(dub_lane))
        working_draft = draft.model_copy(update={"timeline_clips": [*draft.timeline_clips, clip]})

    # A history item may represent several subtitles. Restore its immutable
    # submission binding, rather than narrowing it to the primary subtitle.
    matching_workflow = next((
        item for item in draft.tts_tasks
        if item.generation_task_id == history.task_id and history.task_id
        and item.text == history.input_text
    ), None)
    placement_stage = next((
        stage for stage in matching_workflow.stages if stage.kind == "placement"
    ), None) if matching_workflow else None
    frozen_target = placement_stage.parameters.get("target_snapshot") if placement_stage else None
    frozen_source = placement_stage.parameters.get("source_snapshot") if placement_stage else None
    if (isinstance(frozen_target, dict) and isinstance(frozen_source, dict)
            and frozen_target.get("subtitle_ids")
            and requested_segment_id in {history.segment_id, matching_workflow.segment_id}
            and all(value in subtitles_by_id for value in frozen_target["subtitle_ids"])
            and (not clip_id or clip.get("generation_id") == (history.generation_id or history.task_id))):
        clip = {**clip,
            "target_subtitle_ids": list(frozen_target["subtitle_ids"]),
            "source_cue_ids": list(frozen_source.get("cue_ids", [])),
            "target_start_ms": int(frozen_target["start_ms"]),
            "target_end_ms": int(frozen_target["end_ms"]),
        }
    explicit_target_ids = [str(value) for value in clip.get("target_subtitle_ids") or []]
    if not explicit_target_ids and not force_new and subtitle_id in subtitles_by_id:
        explicit_target_ids = [subtitle_id]
    if explicit_target_ids:
        explicit_source_ids = [
            str(value) for value in clip.get("source_cue_ids") or [] if str(value)
        ]
        if not explicit_source_ids and subtitle is not None:
            explicit_source_ids = [
                str(value) for value in subtitle.source_cue_ids if str(value)
            ]
        explicit_result = tts_placement.place_frozen_tts_result(
            working_draft,
            tts_placement.FrozenTtsPlacementTarget(
                segment_id=str(clip.get("subtitle_id") or requested_segment_id),
                target_subtitle_ids=tuple(explicit_target_ids),
                source_cue_ids=tuple(explicit_source_ids),
                start_ms=int(clip.get("target_start_ms", clip.get("start_ms", 0))),
                end_ms=int(clip.get("target_end_ms", clip.get("end_ms", 0))),
                text=history.input_text,
            ),
            tts_placement.TtsResultMetadata(
                result_id=result_id,
                output_path=str(adopted_path),
                duration_ms=history.duration_ms,
                task_id=history.task_id,
                generation_id=history.generation_id or history.task_id,
                timeline_clip_id=str(clip["clip_id"]),
                candidate_id=(
                    tts_pipeline.generated_candidate_id(history.task_id)
                    if history.task_id
                    else None
                ),
                placement_start_ms=start_ms,
            ),
        )
        applied_draft = explicit_result.draft
        if dub_lane is not None:
            applied_draft = applied_draft.model_copy(
                update={
                    "timeline_clips": [
                        {**dict(item), "dub_lane": max(0, int(dub_lane))}
                        if item.get("clip_id") == explicit_result.clip_id
                        else item
                        for item in applied_draft.timeline_clips
                    ]
                }
            )
    else:
        applied_draft = tts_pipeline.with_applied_history_result(
            working_draft,
            str(clip["clip_id"]),
            result_id=result_id,
            output_path=str(adopted_path),
            duration_ms=history.duration_ms,
            generation_id=history.generation_id or history.task_id,
            placement_start_ms=start_ms,
            dub_lane=dub_lane,
        )
    # Added history clips always occupy the first lane with enough room. Replacing
    # an existing clip keeps its lane and does not disturb sibling clips.
    if not clip_id or force_new:
        applied_draft = _place_dub_clip_on_free_lane(applied_draft, str(clip["clip_id"]), dub_lane or 0)
    if force_new:
        applied_draft = applied_draft.model_copy(
            update={
                "timeline_clips": [
                    {
                        key: value
                        for key, value in dict(item).items()
                        if key
                        not in {
                            "cqc_status",
                            "cqc_report_version",
                            "cqc_report",
                            "timeline_edit_gate",
                        }
                    }
                    if item.get("clip_id") == clip["clip_id"]
                    else item
                    for item in applied_draft.timeline_clips
                ]
            }
        )
    reactivated_ids = {
        str(value)
        for value in (
            result_id,
            history.task_id,
            history.generation_id,
            history.longform_task_id,
        )
        if value
    }
    discarded = [
        str(value)
        for value in applied_draft.ui_state.get("discarded_tts_task_ids", [])
        if isinstance(value, str) and value and value not in reactivated_ids
    ]
    return applied_draft.model_copy(
        update={
            "ui_state": {
                **applied_draft.ui_state,
                "discarded_tts_task_ids": list(dict.fromkeys(discarded)),
            }
        }
    )


def cleanup_unused_tts_history(project_id: str, segment_id: str | None = None) -> dict[str, int] | None:
    project = project_store.get_project(project_id)
    if not project:
        return None

    with _DRAFT_WRITE_LOCK:
        draft = draft_store.get(project_id)
        if draft is None:
            return None
        active_statuses = {"pending", "queued", "running", "postprocessing", "retrying"}

        def belongs_to_scope(item: Any) -> bool:
            return tts_history.belongs_to_segment(item, segment_id)

        active_tasks = [
            task
            for task in task_queue.list_project_tasks(project_id)
            if str(getattr(task.status, "value", task.status)) in active_statuses and belongs_to_scope(task)
        ]
        active_workflows = [
            workflow
            for workflow in (_reconcile_tts_workflow_task(item) for item in draft.tts_tasks)
            if workflow.status in {"prepared", "queued", "running"} and belongs_to_scope(workflow)
        ]
        if active_tasks or active_workflows:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_CLEANUP_ACTIVE",
                "当前范围仍有配音生成任务，请等待完成或取消后再清理",
                {
                    "task_ids": [task.task_id for task in active_tasks],
                    "workflow_ids": [workflow.workflow_id for workflow in active_workflows],
                },
            )

        history_items = history_store.list_history(
            limit=-1,
            project_id=project_id,
            source="video_localization",
        )

        identity_keys = (
            "result_id",
            "task_id",
            "generation_id",
            "candidate_id",
            "audio_id",
            "result_audio_id",
            "output_audio_id",
        )

        def identities(item: Any) -> set[str]:
            if isinstance(item, dict):
                values = [item.get(key) for key in identity_keys]
            else:
                values = [getattr(item, key, None) for key in identity_keys]
            return {str(value) for value in values if value}

        def resolved_path(value: Any) -> str:
            return str(Path(str(value)).resolve()) if value else ""

        protected_ids: set[str] = set()
        protected_paths: set[str] = set()
        for clip in draft.timeline_clips:
            if clip.get("track_id", "dub") != "dub":
                continue
            protected_ids.update(identities(clip))
            path = resolved_path(clip.get("audio_path"))
            if path:
                protected_paths.add(path)

        # A timeline clip can retain only a candidate/task/path identity. Expand
        # that reference to the complete candidate identity before classifying history.
        protected_candidate_ids: set[str] = set()
        changed = True
        while changed:
            changed = False
            for candidate in draft.generated_candidates:
                candidate_ids = identities(candidate)
                candidate_path = resolved_path(candidate.get("audio_path"))
                candidate_key = str(candidate.get("candidate_id") or "")
                if not (
                    candidate_ids & protected_ids
                    or (candidate_path and candidate_path in protected_paths)
                    or (candidate_key and candidate_key in protected_candidate_ids)
                ):
                    continue
                before = len(protected_ids)
                protected_ids.update(candidate_ids)
                if candidate_key:
                    protected_candidate_ids.add(candidate_key)
                if candidate_path:
                    protected_paths.add(candidate_path)
                changed = changed or len(protected_ids) != before

        def history_is_used(item: Any) -> bool:
            return bool(
                identities(item) & protected_ids
                or (item.output_path and resolved_path(item.output_path) in protected_paths)
            )

        removable = [item for item in history_items if belongs_to_scope(item) and not history_is_used(item)]
        removed_result_ids = {item.result_id for item in removable}
        removed_task_ids = {str(item.task_id or "") for item in removable if item.task_id}
        removed_generation_ids = {str(item.generation_id or "") for item in removable if item.generation_id}
        removed_ids = removed_result_ids | removed_task_ids | removed_generation_ids

        removable_candidates = [
            dict(item)
            for item in draft.generated_candidates
            if belongs_to_scope(item)
            and not identities(item) & protected_ids
            and (
                bool(identities(item) & removed_ids)
                or not identities(item)
                or not any(identities(item) & identities(history) for history in history_items)
            )
        ]
        removed_candidate_ids = {
            str(item.get("candidate_id") or "") for item in removable_candidates if item.get("candidate_id")
        }
        removed_ids.update(removed_candidate_ids)
        selected_project_paths = [str(item.get("audio_path") or "") for item in removable_candidates]
        selected_project_path_set = {resolved_path(value) for value in selected_project_paths if value}

        next_candidates = [
            dict(item)
            for item in draft.generated_candidates
            if str(item.get("candidate_id") or "") not in removed_candidate_ids
        ]

        def clear_cue_result(item):
            if not belongs_to_scope(item):
                return item
            cue_ids = {str(value) for value in (item.tts_result_id, item.tts_generation_id) if value}
            cue_path = resolved_path(item.tts_audio_path)
            if not cue_ids & removed_ids and not (cue_path and cue_path in selected_project_path_set):
                return item
            if item.tts_audio_path:
                selected_project_paths.append(item.tts_audio_path)
                selected_project_path_set.add(cue_path)
            return item.model_copy(
                update={
                    "tts_result_id": None,
                    "tts_generation_id": None,
                    "tts_audio_path": None,
                    "generated_duration_ms": None,
                }
            )

        latest = draft.ui_state.get("latest_tts_task_by_segment", {})
        next_latest = {
            key: value
            for key, value in (latest.items() if isinstance(latest, dict) else [])
            if str(value or "") not in removed_task_ids
        }
        discarded = {
            str(value) for value in draft.ui_state.get("discarded_tts_task_ids", []) if isinstance(value, str) and value
        }
        discarded.update(removed_task_ids)
        removed_at = now_iso()

        def clear_workflow_artifacts(item: VideoLocalizationTtsTask) -> VideoLocalizationTtsTask:
            if not belongs_to_scope(item):
                return item
            workflow_ids = {str(value) for value in (item.result_id, item.generation_task_id) if value}
            if not workflow_ids & removed_ids:
                return item
            generation_stage = item.stages[0].model_copy(
                update={
                    "parameters": {
                        **item.stages[0].parameters,
                        "artifacts_removed_at": removed_at,
                    }
                }
            )
            return item.model_copy(
                update={
                    "result_id": None,
                    "timeline_clip_id": None,
                    "stages": [generation_stage, item.stages[1]],
                    "updated_at": removed_at,
                }
            )

        next_draft = draft.model_copy(
            update={
                "cues": [clear_cue_result(item) for item in draft.cues],
                "localized_subtitles": [clear_cue_result(item) for item in draft.localized_subtitles],
                "generated_candidates": next_candidates,
                "tts_tasks": [clear_workflow_artifacts(item) for item in draft.tts_tasks],
                "ui_state": {
                    **draft.ui_state,
                    "latest_tts_task_by_segment": next_latest,
                    "discarded_tts_task_ids": sorted(discarded),
                },
            }
        )
        saved = draft_store.save(project_id, next_draft, intent="content")

    history_store.delete_many(removed_result_ids)
    task_queue.mark_artifacts_removed(removed_task_ids, removed_result_ids, removed_at=removed_at)

    referenced_paths = [
        *[str(item.get("audio_path") or "") for item in saved.timeline_clips],
        *[str(item.get("audio_path") or "") for item in saved.generated_candidates],
        *[str(item.tts_audio_path or "") for item in saved.cues],
        *[str(item.tts_audio_path or "") for item in saved.localized_subtitles],
    ]
    removed_files = media_assets.cleanup_unreferenced_tts(
        project_id,
        referenced_paths,
        candidate_paths=selected_project_paths if segment_id else None,
    )
    media_assets.invalidate_project_timeline_audio_paths(project_id)
    return {
        "removed_records": len(removable),
        "removed_files": len(removed_files),
        "kept_used_records": sum(1 for item in history_items if belongs_to_scope(item) and history_is_used(item)),
    }


def tts_audio_file(project_id: str, cue_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return media_assets.managed_project_file(
        project_id,
        audio_access.tts_audio_path(draft, cue_id),
    )


def generated_candidate_audio_file(project_id: str, candidate_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return media_assets.managed_project_file(
        project_id,
        tts_pipeline.generated_candidate_audio_path(draft, candidate_id),
    )


def timeline_clip_audio_file(project_id: str, clip_id: str) -> Path | None:
    cached = media_assets.cached_project_timeline_audio_paths(project_id)
    if cached is not None:
        raw_path = cached.get(clip_id)
        if raw_path is not None:
            path = media_assets.managed_project_file(project_id, raw_path)
            if path is not None:
                return path
            media_assets.invalidate_project_timeline_audio_paths(project_id)
        recovered = _timeline_clip_history_audio_file(project_id, clip_id)
        if recovered is not None:
            return recovered
        if raw_path is None:
            return None
    with _timeline_audio_index_lock(project_id):
        cached = media_assets.cached_project_timeline_audio_paths(project_id)
        if cached is None:
            draft = draft_store.get(project_id)
            if not draft:
                return None
            resolved_media = media_health.inspect_project_media(draft).paths
            cached = media_assets.cache_project_timeline_audio_paths(
                project_id,
                draft,
                resolved_media={
                    "source_audio": resolved_media.source_audio,
                    "vocals": resolved_media.vocals,
                    "background": resolved_media.background,
                },
            )
        raw_path = cached.get(clip_id)
        path = media_assets.managed_project_file(project_id, raw_path)
        if raw_path is not None and path is None:
            media_assets.invalidate_project_timeline_audio_paths(project_id)
        return path or _timeline_clip_history_audio_file(project_id, clip_id)


def _timeline_clip_history_audio_file(project_id: str, clip_id: str) -> Path | None:
    """Recover a persisted split clip whose managed audio locator was lost."""

    draft = draft_store.get(project_id)
    if draft is None:
        return None
    exact_matches: list[dict[str, Any]] = []
    source_matches: list[dict[str, Any]] = []
    for item in draft.timeline_clips:
        clip = dict(item)
        if str(clip.get("clip_id") or "") == clip_id:
            exact_matches.append(clip)
        elif str(clip.get("media_source_clip_id") or "") == clip_id:
            source_matches.append(clip)
    for clip in [*exact_matches, *source_matches]:
        result_id = str(clip.get("result_id") or "")
        if not result_id:
            continue
        history = history_store.get(result_id)
        if history is None or history.project_id != project_id:
            continue
        parameters = history.parameter_snapshot
        if not isinstance(parameters, dict) or parameters.get("source") != "video_localization":
            continue
        path = history_store.audio_path(result_id)
        if path is not None and path.is_file():
            return path.resolve()
    return None


def timeline_clip_audio_preview_file(project_id: str, clip_id: str) -> Path | None:
    source_path = timeline_clip_audio_file(project_id, clip_id)
    if not source_path:
        return None
    try:
        return media_assets.ensure_audio_preview_proxy(project_id, source_path)
    except AppException:
        # Playback remains usable on installations without an ffmpeg encoder.
        return source_path


def source_video_file(project_id: str) -> Path | None:
    return _project_media_paths(project_id).get("source_video")


def _project_media_paths(project_id: str) -> dict[str, Path | None]:
    cached = media_assets.cached_project_media_paths(project_id)
    if cached is not None:
        resolved = {key: media_assets.managed_project_file(project_id, path) for key, path in cached.items()}
        if all(original is None or resolved.get(key) is not None for key, original in cached.items()):
            return resolved
        media_assets.invalidate_project_media_paths(project_id)
    with _project_media_index_lock(project_id):
        cached = media_assets.cached_project_media_paths(project_id)
        if cached is not None:
            resolved = {key: media_assets.managed_project_file(project_id, path) for key, path in cached.items()}
            if all(original is None or resolved.get(key) is not None for key, original in cached.items()):
                return resolved
        draft = get_video_localization(project_id)
        if not draft:
            return {}
        candidate_paths = {
            "source_video": audio_access.source_video_path(draft),
            "source_audio": audio_access.source_audio_path(draft),
            "vocals": audio_access.stem_audio_path(draft, "vocals"),
            "background": audio_access.stem_audio_path(draft, "background"),
        }
        return media_assets.cache_project_media_paths(
            project_id,
            {key: media_assets.managed_project_file(project_id, path) for key, path in candidate_paths.items()},
        )


def source_preview_video_file(
    project_id: str,
    *,
    variant: Literal["auto", "source", "preview"] = "auto",
) -> Path | None:
    """Serve the immutable source; segmented compatibility media has its own endpoint."""

    del variant  # retained in the public route as a thin legacy query adapter
    return source_video_file(project_id)


def request_source_playback_proxy(
    project_id: str,
    *,
    source_playable: bool = False,
    start_ms: int = 0,
    end_ms: int | None = None,
    fill_background: bool = True,
) -> preview_media_contracts.VideoPlaybackProxyStatus | None:
    """Resolve direct playback or prioritize a segmented proxy range."""

    source_path = source_video_file(project_id)
    if not source_path:
        return None
    return preview_media_contracts.VideoPlaybackProxyStatus.model_validate(
        playback_proxy.request_playback(
            project_id,
            source_path,
            source_playable=source_playable,
            start_ms=start_ms,
            end_ms=end_ms,
            fill_background=fill_background,
        )
    )


def source_playback_proxy_segment_file(
    project_id: str,
    segment_index: int,
    revision: str,
) -> Path | None:
    source_path = source_video_file(project_id)
    if not source_path:
        return None
    return playback_proxy.segment_file(
        project_id,
        source_path,
        segment_index,
        revision=revision,
    )


def source_preview_cache_status(project_id: str) -> dict[str, Any] | None:
    source_path = source_video_file(project_id)
    if not source_path:
        return None
    return preview_cache.cache_status(project_id, source_path)


def request_source_preview_cache(
    project_id: str,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    full: bool = False,
) -> dict[str, Any] | None:
    source_path = source_video_file(project_id)
    if not source_path:
        return None
    return preview_cache.request_cache(project_id, source_path, start_ms=start_ms, end_ms=end_ms, full=full)


def refresh_source_preview_cache(project_id: str) -> dict[str, Any] | None:
    source_path = source_video_file(project_id)
    if not source_path:
        return None
    return preview_cache.refresh_cache(project_id, source_path)


def source_preview_cached_sprite(project_id: str, chunk_index: int) -> Path | None:
    source_path = source_video_file(project_id)
    if not source_path:
        return None
    return preview_cache.cached_sprite(project_id, source_path, chunk_index)


def source_audio_file(project_id: str) -> Path | None:
    return _project_media_paths(project_id).get("source_audio")


def source_preview_audio_file(
    project_id: str,
    *,
    variant: Literal["auto", "source", "preview"] = "auto",
) -> Path | None:
    source_path = source_audio_file(project_id)
    if not source_path:
        return None
    if variant == "source":
        return source_path
    proxy_path = media_assets.existing_audio_preview_proxy(project_id, source_path)
    if variant == "preview":
        return proxy_path
    return proxy_path or source_path


def ensure_source_preview_audio(project_id: str) -> Path | None:
    source_path = source_audio_file(project_id)
    if not source_path:
        return None
    return media_assets.ensure_audio_preview_proxy(project_id, source_path)


def stem_audio_file(project_id: str, kind: str) -> Path | None:
    if kind not in {"vocals", "background"}:
        return None
    return _project_media_paths(project_id).get(kind)


def stem_preview_audio_file(
    project_id: str,
    kind: str,
    *,
    variant: Literal["auto", "source", "preview"] = "auto",
) -> Path | None:
    source_path = stem_audio_file(project_id, kind)
    if not source_path:
        return None
    if variant == "source":
        return source_path
    proxy_path = media_assets.existing_audio_preview_proxy(project_id, source_path)
    if variant == "preview":
        return proxy_path
    return proxy_path or source_path


def ensure_stem_preview_audio(project_id: str, kind: str) -> Path | None:
    source_path = stem_audio_file(project_id, kind)
    if not source_path:
        return None
    return media_assets.ensure_audio_preview_proxy(project_id, source_path)


def reference_clip_audio_file(project_id: str, reference_clip_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return media_assets.managed_project_file(
        project_id,
        audio_access.reference_clip_audio_path(draft, reference_clip_id),
    )


def source_cue_audio_file(project_id: str, cue_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.source_cue_audio_path(project_id, draft, cue_id)


def production_readiness_audit(project_id: str) -> dict | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return exporting.production_readiness(project.project_id, project.name, draft)
