from __future__ import annotations

import threading
from datetime import datetime
from typing import Literal

from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization import dub_subtitles
from app.domains.video_localization.quality_gate import evaluate_quality_gate
from app.domains.video_localization import media_assets
from app.domains.video_localization import operation_detail_projection
from app.domains.video_localization import operation_summary_projection
from app.domains.video_localization import project_manifest
from app.domains.video_localization import project_lifecycle_cleanup
from app.domains.video_localization import project_snapshot_projection
from app.domains.video_localization import timeline_clip_timing
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.errors import AppException, ProjectRevisionConflict
from app.schemas.video_localization_tts_handoff import TtsHandoffClaim
from app.services import project_store
from app.services import video_localization_operation_ledger_store
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    resolve_execution_fence,
)

VIDEO_LOCALIZATION_KEY = "video_localization"
_USE_CURRENT_TIME = object()
DraftWriteIntent = Literal[
    "content",
    "interactive_content",
    "editorial",
    "runtime",
    "workspace",
    "repair",
]
# Draft persistence is the state owner, so cross-application mutations share
# this lock instead of defining unrelated locks in each facade.
DRAFT_WRITE_LOCK = threading.RLock()


def get(project_id: str, *, normalize_for_read: bool = True) -> VideoLocalizationDraft | None:
    """Load the database-backed draft without persistent repair side effects."""

    project = project_store.get_project(project_id)
    if not project:
        return None
    return from_project(project, normalize_for_read=normalize_for_read)


def from_project(project, *, normalize_for_read: bool = True) -> VideoLocalizationDraft:
    """Deserialize one already-locked project snapshot without another read."""

    raw_present = VIDEO_LOCALIZATION_KEY in project.parameters
    raw = project.parameters.get(VIDEO_LOCALIZATION_KEY)
    if isinstance(raw, dict) and raw:
        try:
            draft = VideoLocalizationDraft(
                **_without_removed_marker_fields(raw)
            )
            draft._repository_revision = project._repository_revision
            if not normalize_for_read:
                # Bounded editor commands preserve the stored state of every
                # unrelated entity. Legacy read repair is not a write command.
                return draft
            return _normalize_for_read(
                media_assets.recover_missing_project_media_locators(
                    project.project_id,
                    draft,
                )
            )
        except (ValueError, TypeError):
            pass
    if raw_present or (
        media_assets.PROJECT_DIR_NAME_KEY in project.parameters
        and project_manifest.has_project_snapshot(project.project_id)
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DRAFT_REPAIR_REQUIRED",
            "项目草稿缺失或损坏，请先执行项目存储修复。",
            {
                "repair_endpoint": (
                    f"/api/projects/{project.project_id}/"
                    "video-localization/repair-storage"
                )
            },
        )
    draft = VideoLocalizationDraft()
    draft._repository_revision = project._repository_revision
    return draft


def repair(project_id: str) -> VideoLocalizationDraft | None:
    """Explicitly migrate storage and persist recoverable draft normalization."""

    with DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if project is None:
            return None
        media_assets.ensure_project_video_localization_dir(project_id)
        project = project_store.get_project(project_id)
        if project is None:
            return None
        raw = project.parameters.get(VIDEO_LOCALIZATION_KEY)
        draft: VideoLocalizationDraft | None = None
        if isinstance(raw, dict) and raw:
            try:
                rebased = media_assets.rebase_project_paths(
                    project_id,
                    _without_removed_marker_fields(raw),
                )
                draft = media_assets.recover_missing_project_media_locators(
                    project_id,
                    VideoLocalizationDraft(**rebased),
                )
            except (ValueError, TypeError):
                draft = None
        if draft is None:
            draft = project_manifest.read_project_snapshot(project_id)
        if draft is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DRAFT_RECOVERY_NOT_FOUND",
                "没有找到可恢复的项目快照，未改写当前项目。",
            )
        draft._repository_revision = project._repository_revision
        normalized = with_fresh_gate(
            draft,
            updated_at=draft.updated_at,
        )
        normalized = _with_stable_gate_timestamp_if_unchanged(
            draft,
            normalized,
        )
        payload = normalized.model_dump(mode="json")
        if (
            not isinstance(raw, dict)
            or raw != payload
            or not project.parameters.get(
                media_assets.PROJECT_DIR_NAME_KEY
            )
        ):
            saved = save(
                project_id,
                normalized,
                intent="repair",
                updated_at=normalized.updated_at,
            )
            return saved
        return normalized


def _normalize_for_read(
    draft: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    # Timeline placement, collision checks, public rendering and export must
    # all observe the same playable audio span. Older/internal writers could
    # leave an audio clip whose timeline duration exceeded its source crop;
    # normalizing only at the HTTP boundary made the page and application
    # service disagree about whether neighboring room existed.
    normalized = timeline_clip_timing.normalize_draft(draft)
    normalized = _without_duplicate_timeline_clip_ids(normalized)
    normalized = _with_planned_dubbing_writes(normalized)
    normalized = cue_tools.with_normalized_quality_flags(normalized)
    review_normalized = cue_tools.without_stale_generated_asr_review_status(
        normalized
    )
    if review_normalized is not normalized:
        return with_fresh_gate(
            review_normalized,
            updated_at=review_normalized.updated_at,
        )
    return normalized


def _with_planned_dubbing_writes(
    draft: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Keep historical media readable while disabling legacy timeline writes.

    The legacy value described a writer, not a different stored-media format.
    Moving it to ``planned`` therefore preserves every existing clip and
    candidate.  A later write must build a current semantic plan before it can
    adopt another candidate onto the formal timeline.
    """

    state = draft.dubbing_production
    if state.enforcement_mode == "planned":
        return draft
    return draft.model_copy(
        update={
            "dubbing_production": state.model_copy(
                update={"enforcement_mode": "planned"}
            )
        }
    )


def save(
    project_id: str,
    draft: VideoLocalizationDraft,
    *,
    intent: DraftWriteIntent,
    updated_at: str | None | object = _USE_CURRENT_TIME,
    execution_fence: ExecutionFence | None = None,
    tts_handoff_claim: TtsHandoffClaim | None = None,
    observed_at_ms: int | None = None,
    operation_command: (
        video_localization_operation_ledger_store.OperationCommand | None
    ) = None,
) -> VideoLocalizationDraft | None:
    if (
        project_lifecycle_cleanup.has_pending_reset(project_id)
        and not project_lifecycle_cleanup.flush_project(project_id)
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_CLEANUP_PENDING",
            "项目媒体清理尚未完成，请稍后重试。",
        )
    project = project_store.get_project(project_id)
    if not project:
        return None
    if (
        draft._repository_revision is not None
        and draft._repository_revision != project._repository_revision
    ):
        raise ProjectRevisionConflict("Project changed since the draft was read")
    previous_raw = project.parameters.get(VIDEO_LOCALIZATION_KEY)
    bounded_edit = intent in {"editorial", "workspace"}
    if not bounded_edit and isinstance(previous_raw, dict) and previous_raw:
        try:
            previous_draft = VideoLocalizationDraft.model_validate(
                _without_removed_marker_fields(previous_raw)
            )
        except (TypeError, ValueError):
            previous_draft = None
        if previous_draft is not None:
            from app.domains.video_localization.dubbing_text_rebase import rebase_text_edit

            draft = rebase_text_edit(previous_draft, draft, project_id=project_id)
            draft = dub_subtitles.reconcile_source_lifecycle(
                previous_draft,
                draft,
            )
    if not bounded_edit:
        draft = timeline_clip_timing.normalize_draft(draft)
    previous_media_paths = _media_path_signature(project.parameters.get(VIDEO_LOCALIZATION_KEY))
    actual_updated_at = (
        datetime.now().isoformat(timespec="microseconds")
        if updated_at is _USE_CURRENT_TIME and intent != "repair"
        else draft.updated_at
        if updated_at is _USE_CURRENT_TIME
        else updated_at
    )
    next_draft = (
        draft.model_copy(update={"updated_at": actual_updated_at})
        if bounded_edit
        else with_fresh_gate(draft, updated_at=actual_updated_at, project_id=project_id)
    )
    directory_name = str(
        project.parameters.get(media_assets.PROJECT_DIR_NAME_KEY)
        or ""
    ).strip() or media_assets.project_dir_name(
        project.project_id,
        project.name,
    )
    project.parameters = {
        **project.parameters,
        media_assets.PROJECT_DIR_NAME_KEY: directory_name,
        VIDEO_LOCALIZATION_KEY: next_draft.model_dump(),
    }
    operation_summary_cores = tuple(
        operation_summary_projection
        .operation_summary_core_from_draft_operation(
            next_draft,
            operation,
        )
        for operation in next_draft.operations
    )
    effective_execution_fence = execution_fence
    detail_operation_id: str | None = None
    detail_workflow_version: str | None = None
    if operation_command is not None:
        if operation_command.command_type in {"submit", "retry"}:
            detail_operation_id = operation_command.operation_id
            detail_workflow_version = (
                operation_command.workflow_version
            )
    else:
        effective_execution_fence = resolve_execution_fence(
            execution_fence
        )
        if effective_execution_fence is not None:
            detail_operation_id = (
                effective_execution_fence.operation_id
            )
    operation_detail_core = (
        operation_detail_projection
        .detail_core_from_draft_operation(
            next_draft,
            detail_operation_id,
            workflow_version=detail_workflow_version,
        )
        if detail_operation_id is not None
        else None
    )
    project_store.save_project(
        project,
        touch_updated_at=intent in {"content", "interactive_content", "editorial"},
        operation_updated_at=next_draft.updated_at,
        execution_fence=effective_execution_fence,
        tts_handoff_claim=tts_handoff_claim,
        observed_at_ms=observed_at_ms,
        operation_command=operation_command,
        operation_summary_cores=operation_summary_cores,
        operation_detail_core=operation_detail_core,
        tts_workflows=tuple(next_draft.tts_tasks),
        snapshot_create_autosave=intent in {
            "content",
            "interactive_content",
            "editorial",
            "repair",
        },
    )
    next_draft._repository_revision = project._repository_revision
    # Runtime/workspace writes are already durable once the Project row and
    # coalesced projection request commit in SQLite.  Rewriting a large
    # project.json here can keep a completed TTS result off the timeline for
    # tens of seconds.  Content/repair commands still refresh the recovery
    # mirror before returning; runtime projections are coalesced and replayed
    # by the next foreground snapshot write or bounded startup replay.
    if intent in {"content", "repair"}:
        project_snapshot_projection.flush(project_id)
    media_assets.cache_project_directory_name(project_id, directory_name)
    next_media_paths = _media_path_signature(next_draft)
    timeline_media_changed = (
        _timeline_media_signature(previous_raw)
        != _timeline_media_signature(next_draft)
    )
    if previous_media_paths != next_media_paths:
        media_assets.invalidate_project_media_paths(project_id)
    if previous_media_paths != next_media_paths or timeline_media_changed:
        # Writes invalidate derived indexes; the next media reader rebuilds
        # them once. Eagerly inspecting every project asset here made a single
        # subtitle trim wait on unrelated media and waveform work.
        media_assets.invalidate_project_timeline_audio_paths(project_id)
    return next_draft


def _media_path_signature(value: object) -> tuple[str, str, str, str, str]:
    if isinstance(value, VideoLocalizationDraft):
        return (
            value.source_media.video_path or "",
            value.source_media.audio_path or "",
            value.stems.original_audio_path or "",
            value.stems.vocals_clean_path or "",
            value.stems.background_path or "",
        )
    if isinstance(value, dict):
        source = value.get("source_media") if isinstance(value.get("source_media"), dict) else {}
        stems = value.get("stems") if isinstance(value.get("stems"), dict) else {}
        return (
            str(source.get("video_path") or ""),
            str(source.get("audio_path") or ""),
            str(stems.get("original_audio_path") or ""),
            str(stems.get("vocals_clean_path") or ""),
            str(stems.get("background_path") or ""),
        )
    return ("", "", "", "", "")


def _timeline_media_signature(value: object) -> tuple[tuple[str, str, str], ...]:
    if isinstance(value, VideoLocalizationDraft):
        clips = value.timeline_clips
    elif isinstance(value, dict):
        raw = value.get("timeline_clips", [])
        clips = raw if isinstance(raw, list) else []
    else:
        clips = []
    result: list[tuple[str, str, str]] = []
    for raw_clip in clips:
        if not isinstance(raw_clip, dict):
            continue
        result.append((
            str(raw_clip.get("clip_id") or ""),
            str(raw_clip.get("audio_path") or ""),
            str(raw_clip.get("media_source_clip_id") or ""),
        ))
    return tuple(result)


def with_fresh_gate(draft: VideoLocalizationDraft, updated_at: str | None, *, project_id: str | None = None) -> VideoLocalizationDraft:
    draft = _without_duplicate_timeline_clip_ids(draft)
    draft = _with_planned_dubbing_writes(draft)
    draft = cue_tools.without_stale_generated_asr_review_status(draft)
    draft = _without_stale_semantic_tts_grouping(draft)
    draft = _without_stale_dubbing_production(draft, project_id=project_id)
    gate = evaluate_quality_gate(draft)
    status = _status_for_gate(draft, gate.status)
    return draft.model_copy(update={"quality_gate": gate, "status": status, "updated_at": updated_at})


def _with_stable_gate_timestamp_if_unchanged(
    source: VideoLocalizationDraft,
    normalized: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Keep an explicit repair idempotent when only the check clock changed."""

    source_gate = source.quality_gate
    normalized_gate = normalized.quality_gate
    if source_gate.model_dump(
        mode="json",
        exclude={"checked_at"},
    ) != normalized_gate.model_dump(
        mode="json",
        exclude={"checked_at"},
    ):
        return normalized
    if source_gate.checked_at == normalized_gate.checked_at:
        return normalized
    return normalized.model_copy(
        update={
            "quality_gate": normalized_gate.model_copy(
                update={"checked_at": source_gate.checked_at}
            )
        }
    )


def _without_removed_marker_fields(raw: dict) -> dict:
    """Drop the retired timeline-marker feature while loading stored drafts."""

    cleaned = dict(raw)
    cleaned.pop("timeline_markers", None)
    production = cleaned.get("dubbing_production")
    if isinstance(production, dict):
        production = dict(production)
        production["schema_version"] = "dubbing-production-state-v2"
        failures = []
        for item in production.get("group_failures", []) or []:
            if isinstance(item, dict):
                item = dict(item)
                item.pop("marker_id", None)
                item["schema_version"] = "dubbing-production-group-failure-v2"
            failures.append(item)
        production["group_failures"] = failures
        cleaned["dubbing_production"] = production
    clips = []
    for item in cleaned.get("timeline_clips", []) or []:
        if isinstance(item, dict):
            item = dict(item)
            item.pop("manual_review_marker_id", None)
        clips.append(item)
    cleaned["timeline_clips"] = clips
    return cleaned


def _without_duplicate_timeline_clip_ids(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    """Keep the most recently written clip for each stable timeline identity."""

    seen: set[str] = set()
    retained_reversed: list[dict] = []
    for clip in reversed(draft.timeline_clips):
        clip_id = str(clip.get("clip_id") or "")
        if clip_id and clip_id in seen:
            continue
        if clip_id:
            seen.add(clip_id)
        retained_reversed.append(clip)
    if len(retained_reversed) == len(draft.timeline_clips):
        return draft
    return draft.model_copy(update={"timeline_clips": list(reversed(retained_reversed))})


def _without_stale_semantic_tts_grouping(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    grouping = draft.localization_state.get("semantic_tts_grouping")
    if not isinstance(grouping, dict):
        return draft
    from app.domains.video_localization import semantic_tts_grouping

    expected = str(grouping.get("source_fingerprint") or "")
    actual = semantic_tts_grouping.source_fingerprint(semantic_tts_grouping.build_items(draft))
    if expected and expected == actual:
        return draft
    next_state = dict(draft.localization_state)
    next_state.pop("semantic_tts_grouping", None)
    return draft.model_copy(update={"localization_state": next_state})


def _without_stale_dubbing_production(
    draft: VideoLocalizationDraft,
    *, project_id: str | None = None,
) -> VideoLocalizationDraft:
    state = draft.dubbing_production
    expected_revisions = {
        value
        for value in [
            state.active_plan.source_revision
            if state.active_plan is not None
            else None,
            *(
                report.source_revision
                for report in state.candidate_reports
            ),
            *(
                item.source_revision
                for item in state.candidate_inputs
            ),
            state.latest_timeline_audit.current_source_revision
            if state.latest_timeline_audit is not None
            else None,
        ]
        if value
    }
    if not expected_revisions:
        return draft
    from app.domains.video_localization import dubbing_production

    snapshot = dubbing_production.build_project_snapshot(draft)
    actual = snapshot.source_revision
    if expected_revisions == {actual}:
        return draft
    next_candidates = []
    for raw in draft.generated_candidates:
        candidate = dict(raw)
        for legacy_field in (
            "selected",
            "accepted",
            "cqc_status",
            "cqc_report_version",
            "cqc_report",
        ):
            candidate.pop(legacy_field, None)
        next_candidates.append(candidate)
    next_clips = []
    for raw in draft.timeline_clips:
        clip = dict(raw)
        if str(clip.get("track_id") or "dub") == "dub":
            for legacy_field in (
                "cqc_status",
                "cqc_report_version",
                "cqc_report",
                "timeline_edit_gate",
            ):
                clip.pop(legacy_field, None)
        next_clips.append(clip)
    rebased_plan = (
        dubbing_production.rebase_compatible_generation_plan(
            snapshot,
            state.active_plan,
        )
        if state.active_plan is not None
        else None
    )
    next_plan_revision = state.plan_revision_counter
    if rebased_plan is not None:
        next_plan_revision += 1
        rebased_plan = rebased_plan.model_copy(
            update={"plan_revision": next_plan_revision}
        )
    retained_inputs, retained_reports = [], []
    if rebased_plan is not None and project_id and state.candidate_reports:
        from app.domains.video_localization import dubbing_media
        from app.domains.video_localization.dubbing_plan_continuation import retain_unchanged_completion_evidence

        retained_inputs, retained_reports = retain_unchanged_completion_evidence(
            state=state, new_plan=rebased_plan, current_draft=draft,
            timeline_clips=[dict(clip) for clip in draft.timeline_clips],
            audio_sha256_by_clip_id=dubbing_media.current_timeline_audio_sha256s(project_id, draft),
        )
    next_state = type(state)(
        candidate_inputs=retained_inputs,
        candidate_reports=retained_reports,
        enforcement_mode="planned",
        plan_revision_counter=next_plan_revision,
        active_plan=rebased_plan,
    )
    return draft.model_copy(
        update={
            "dubbing_production": next_state,
            "generated_candidates": next_candidates,
            "timeline_clips": next_clips,
        }
    )


def _status_for_gate(draft: VideoLocalizationDraft, gate_status: str) -> str:
    if gate_status == "blocked":
        return "blocked"
    if draft.status in {"tts_running", "candidate"}:
        return draft.status
    if gate_status == "pass" and draft.cues:
        return "ready_for_tts"
    if draft.cues:
        return "reviewing"
    return "draft"
