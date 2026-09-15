"""Application facade for localized-dubbing planning and gap processing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from app.domains.video_localization import (
    audio_boundaries,
    draft_store,
    dubbing_candidate_alignment,
    dubbing_gap_adjudication,
    dubbing_media,
    dub_subtitles,
    media_assets,
    timeline_clip_timing,
    tts_pipeline,
    tts_placement,
)
from app.domains.video_localization import dubbing_production as domain
from app.domains.video_localization import dubbing_production_run
from app.domains.video_localization.dubbing_plan_continuation import (
    retain_unchanged_completion_evidence,
    retain_unchanged_manual_timing_deferrals,
)
from app.domains.video_localization.dubbing_timeline_edit_gate import (
    candidate_audible_timeline_bounds,
    retained_candidate_projection_fingerprint as _normalized_candidate_projection_fingerprint,
    has_verified_retained_content as _verified_retained_projection,
    candidate_source_speech_bounds,
    candidate_protected_speech_bounds,
    first_protected_audio_overlap,
    candidate_clip_projection_fingerprint,
    first_primary_clip_overlap as _first_ready_dub_overlap,
    reconcile_gap_evidence_with_projection,
    timeline_edit_gate_matches,
)
from app.domains.video_localization import service as project_service
from app.errors import AppException
from app.schemas.video_localization_dubbing_production import (
    DubbingCandidateCqcInput,
    DubbingCandidateCqcReport,
    DubbingCandidateContentEvidenceRequest,
    DubbingCandidateContentEvidenceResponse,
    DubbingCurrentProjectionRequest,
    DubbingRetainedContentEvidence,
    DubbingCandidateReviewCommand,
    DubbingBoundaryEvidence,
    DubbingExistingFormalAcceptance,
    DubbingExistingFormalReconcileResponse,
    DubbingGenerationPlan,
    DubbingGenerationPlanInput,
    DubbingGroupSchedulingPolicy,
    DubbingManualTimingDeferral,
    DubbingManualTimingDeferralRequest,
    DubbingManualTimingDeferralResponse,
    DubbingProductionGroupFailure,
    DubbingProductionGroupFailureRequest,
    DubbingProductionSnapshot,
    DubbingProductionRunSnapshot,
    DubbingProductionReviewMode,
    DubbingSemanticUnit,
    DubbingStagedCandidateClip,
    DubbingStagedCandidateProjection,
    DubbingStagedCandidateSplitRequest,
    DubbingTimelineAuditReport,
    DubbingTimelineAlignedSlice,
    DubbingTimelineClipSplitCommand,
    DubbingTimelineEditGate,
    DubbingTimelineEditGateCommitRequest,
    DubbingTimelineGroupResetRequest,
    DubbingTimelineSplitRequest,
)
from app.schemas.voice_studio import (
    GenerationTask,
    HistoryItem,
    TTSVerificationResponse,
)
from app.domains.video_localization.schemas import now_iso
from app.services import (
    audio_tools,
    history_store,
    qwen_forced_aligner,
    task_queue,
    tts_content_verification,
)


class DubbingProductionApplicationService:
    """Single public entry used by HTTP, workers and a future thin CLI."""

    @staticmethod
    def group_evidence_context_fingerprint(draft, plan, group) -> str:
        return domain.group_evidence_context_fingerprint(draft, plan, group)

    @staticmethod
    def validate_recovery_decision(draft, decision):
        from app.domains.video_localization.dubbing_recovery import validate_recovery_decision
        return validate_recovery_decision(draft, decision)

    @staticmethod
    def assess_group_preflight(draft, group, *, speed, engine_id=None):
        from app.domains.video_localization.dubbing_preflight import assess_group_preflight
        return assess_group_preflight(draft, group, speed=speed, engine_id=engine_id)

    def read_group_preflight(self, project_id: str, group_id: str, *, speed: float):
        draft = self._require_current_project(project_id)
        plan = draft.dubbing_production.active_plan
        group = next((item for item in plan.groups if item.group_id == group_id), None) if plan else None
        if group is None:
            raise AppException(404, "DUBBING_GROUP_NOT_FOUND", "当前配音组不存在。")
        return self.assess_group_preflight(draft, group, speed=speed, engine_id="omnivoice")

    def read_snapshot(self, project_id: str) -> DubbingProductionSnapshot:
        draft = self._require_current_project(project_id)
        return domain.build_project_snapshot(draft)

    def read_completion(self, project_id: str, *, start_ms: int = 0, end_ms: int | None = None):
        from app.domains.video_localization.dubbing_completion import read_current_completion

        draft = self._require_current_project(project_id)
        try:
            return read_current_completion(project_id, draft, start_ms=start_ms, end_ms=end_ms)
        except ValueError as exc:
            raise AppException(422, "DUBBING_COMPLETION_RANGE_INVALID", str(exc)) from exc

    def read_production_run(self, project_id: str) -> DubbingProductionRunSnapshot:
        draft = self._require_current_project(project_id)
        durations = dubbing_media.current_timeline_audio_durations(project_id, draft)
        hashes = dubbing_media.current_timeline_audio_sha256s(project_id, draft)
        return self._build_production_run_snapshot(
            draft,
            playable_clip_ids={
                clip_id for clip_id, duration_ms in durations.items()
                if duration_ms is not None
            },
            audio_sha256_by_clip_id=hashes,
        )

    def resolve_generation_priority(self, task: GenerationTask) -> str | None:
        """Read the plan authority at dispatch, including after interrupted dual writes."""
        if (not task.project_id or task.parameters.get("video_localization_execution_scope")
            not in {"single_group", "all_remaining"}):
            return None
        draft = draft_store.get(task.project_id)
        plan = draft.dubbing_production.active_plan if draft else None
        if (plan is None or task.parameters.get("video_localization_dubbing_plan_revision")
            != plan.plan_revision):
            return None
        return self._group_priorities(draft).get(
            task.parameters.get("video_localization_dubbing_group_id"), "normal",
        )

    @staticmethod
    def _group_priorities(draft) -> dict[str, str]:
        plan = draft.dubbing_production.active_plan
        if plan is None:
            return {}
        return {item.group_id: item.priority
                for item in draft.dubbing_production.scheduling_policies
                if item.source_revision == plan.source_revision
                and item.plan_revision == plan.plan_revision}

    def set_group_scheduling_priority(
        self, project_id: str, *, source_revision: str, plan_revision: int,
        group_ids: list[str], priority: str,
    ) -> None:
        """Persist plan-owned policy, then refresh only its queued TTS tasks."""
        policies = [DubbingGroupSchedulingPolicy(
            source_revision=source_revision, plan_revision=plan_revision,
            group_id=group_id, priority=priority,
        ) for group_id in dict.fromkeys(group_ids)]
        selected = {item.group_id for item in policies}

        def apply(current):
            plan = current.dubbing_production.active_plan
            if (plan is None or plan.source_revision != source_revision
                or plan.plan_revision != plan_revision
                or not selected.issubset({group.group_id for group in plan.groups})):
                raise AppException(409, "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED", "配音计划已经变化，请重新读取当前分组。")
            remaining = [item for item in current.dubbing_production.scheduling_policies
                         if item.source_revision == source_revision
                         and item.plan_revision == plan_revision
                         and item.group_id not in selected]
            state = current.dubbing_production.model_copy(update={"scheduling_policies": remaining + policies})
            return current.model_copy(update={"dubbing_production": state})

        saved = project_service.update_video_localization_atomic(project_id, apply, intent="runtime")
        if saved is None:
            raise AppException(404, "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND", "视频本土化项目不存在。")
        task_queue.update_pending_production_priorities(
            project_id, plan_revision=plan_revision,
            group_priorities={item.group_id: item.priority for item in policies},
        )

    def find_continuous_boundary_underfill_groups(
        self,
        project_id: str,
        group_ids: list[str],
    ) -> list[str]:
        """Return only scoped groups whose next dub starts materially late."""

        if not group_ids:
            return []
        draft = self._require_current_project(project_id)
        plan = draft.dubbing_production.active_plan
        if plan is None:
            return []
        eligible_group_ids = set(group_ids)
        eligible_unit_ids = {
            unit_id
            for group in plan.groups
            if group.group_id in eligible_group_ids
            for unit_id in group.unit_ids
        }
        if not eligible_unit_ids:
            return []
        payload = domain.build_current_timeline_audit_input(
            draft,
            current_timeline_revision=domain.dubbing_timeline_projection_revision(
                draft
            ),
            phase="timeline",
        )
        return list(
            dict.fromkeys(
                item.left_group_id
                for item in domain.continuous_speech_underfill_assessments(
                    payload,
                    eligible_left_group_ids=eligible_group_ids,
                    eligible_left_unit_ids=eligible_unit_ids,
                    main_lane_only=True,
                )
            )
        )

    @staticmethod
    def _build_production_run_snapshot(
        draft,
        *,
        playable_clip_ids: set[str] | None = None,
        audio_sha256_by_clip_id: dict[str, str | None] | None = None,
    ) -> DubbingProductionRunSnapshot:
        snapshot = domain.build_project_snapshot(draft)
        plan = draft.dubbing_production.active_plan
        discarded_workflow_ids = {
            str(value)
            for value in draft.ui_state.get("discarded_tts_task_ids", [])
            if str(value)
        }
        workflows = [
            workflow
            for workflow in project_service.reconcile_tts_workflow_tasks(
                list(draft.tts_tasks)
            )
            if not discarded_workflow_ids.intersection(
                {
                    str(workflow.workflow_id or ""),
                    str(workflow.generation_task_id or ""),
                    str(workflow.result_id or ""),
                }
            )
        ]
        result = dubbing_production_run.build_production_run_snapshot(
            current_source_revision=snapshot.source_revision,
            active_plan=plan,
            workflows=workflows,
            recoverable_candidate_ids_by_group={
                group.group_id: project_service.compatible_generated_identities(
                    draft, group,
                )
                for group in (plan.groups if plan is not None else [])
            },
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
            playable_clip_ids=playable_clip_ids,
            audio_sha256_by_clip_id=audio_sha256_by_clip_id,
        )
        priorities = DubbingProductionApplicationService._group_priorities(draft)
        return result.model_copy(update={"groups": [
            group.model_copy(update={"resource_priority": priorities.get(group.group_id, "normal")})
            for group in result.groups
        ]})

    def reconcile_existing_formal_groups(
        self,
        project_id: str,
    ) -> DubbingExistingFormalReconcileResponse:
        """Adopt equivalent passed legacy gates without touching formal clips."""

        draft = self._require_current_project(project_id)
        plan = draft.dubbing_production.active_plan
        if plan is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_REQUIRED",
                "请先完成配音生成计划。",
            )

        exact_matches = _existing_formal_matches_by_group(draft)
        accepted: list[DubbingExistingFormalAcceptance] = []
        pending_group_ids: list[str] = []
        for group in plan.groups:
            match = exact_matches.get(group.group_id)
            if match is None:
                continue
            identity, clips = match
            acceptance = _build_existing_formal_acceptance(
                project_id=project_id,
                plan=plan,
                group=group,
                candidate_id=identity,
                clips=clips,
            )
            if acceptance is None:
                pending_group_ids.append(group.group_id)
            else:
                accepted.append(acceptance)

        existing_by_group = {
            item.group_id: item
            for item in draft.dubbing_production.existing_formal_acceptances
            if item.source_revision == plan.source_revision
            and item.plan_revision == plan.plan_revision
        }
        accepted = [
            (
                previous
                if (
                    (previous := existing_by_group.get(item.group_id)) is not None
                    and previous.model_dump(exclude={"created_at"})
                    == item.model_dump(exclude={"created_at"})
                )
                else item
            )
            for item in accepted
        ]
        unchanged_count = sum(
            existing_by_group.get(item.group_id) == item for item in accepted
        )
        expected_plan_revision = plan.plan_revision
        expected_source_revision = plan.source_revision
        expected_projection = {
            item.group_id: item.candidate_clip_projection_fingerprint
            for item in accepted
        }

        def apply(current):
            current_plan = current.dubbing_production.active_plan
            if (
                current_plan is None
                or current_plan.source_revision != expected_source_revision
                or current_plan.plan_revision != expected_plan_revision
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
                    "配音计划已经变化，请重新核对既有片段。",
                )
            current_matches = _existing_formal_matches_by_group(current)
            for group_id, fingerprint in expected_projection.items():
                match = current_matches.get(group_id)
                if (
                    match is None
                    or candidate_clip_projection_fingerprint(match[1]) != fingerprint
                ):
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_TIMELINE_CHANGED",
                        "核对期间时间线发生变化，请重新执行。",
                    )
            retained = [
                item
                for item in current.dubbing_production.existing_formal_acceptances
                if not (
                    item.source_revision == expected_source_revision
                    and item.plan_revision == expected_plan_revision
                )
            ]
            next_acceptances = [*retained, *accepted]
            if next_acceptances == list(
                current.dubbing_production.existing_formal_acceptances
            ):
                return current
            state = current.dubbing_production.model_copy(
                update={
                    "existing_formal_acceptances": next_acceptances,
                    "latest_timeline_audit": None,
                }
            )
            return current.model_copy(update={"dubbing_production": state})

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="runtime",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return DubbingExistingFormalReconcileResponse(
            exact_existing_group_count=len(exact_matches),
            accepted_group_count=len(accepted),
            pending_review_group_count=len(pending_group_ids),
            unchanged_acceptance_count=unchanged_count,
            accepted_group_ids=[item.group_id for item in accepted],
            pending_review_group_ids=pending_group_ids,
        )

    def record_group_failure(
        self,
        project_id: str,
        payload: DubbingProductionGroupFailureRequest,
    ):
        """Persist one exhausted group while leaving later groups runnable."""

        draft = self._require_current_project(project_id)
        self._assert_current_revision(draft, payload.source_revision)
        plan = draft.dubbing_production.active_plan
        if plan is None or plan.plan_revision != payload.plan_revision:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
                "配音计划已经变化，请重新读取当前分组。",
            )
        group = next(
            (item for item in plan.groups if item.group_id == payload.group_id),
            None,
        )
        if group is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_DUBBING_GROUP_NOT_FOUND",
                "配音分组不存在。",
            )
        current_progress = next(
            item
            for item in self._build_production_run_snapshot(draft).groups
            if item.group_id == group.group_id
        )
        # A replacement run keeps the previously accepted clip audible until
        # its new candidate is fully reviewed and atomically committed. If the
        # bounded replacement attempts are exhausted, the old clip is still
        # present but the requested replacement has failed. Preserve both
        # facts instead of making the old acceptance block failure recording.
        if payload.candidate_id is not None and payload.candidate_id not in {
            *current_progress.candidate_ids,
            *(
                [current_progress.passed_candidate_id]
                if current_progress.passed_candidate_id
                else []
            ),
        }:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_CHANGED",
                "指定候选不属于当前配音分组，请重新读取生产进度。",
            )

        created_at = now_iso()
        failure = DubbingProductionGroupFailure(
            source_revision=plan.source_revision,
            plan_revision=plan.plan_revision,
            group_id=group.group_id,
            candidate_id=payload.candidate_id,
            title=payload.title,
            note=payload.note,
            reason_code=payload.reason_code,
            attempted_strategy_codes=payload.attempted_strategy_codes,
            attempt_count=payload.attempt_count,
            created_at=created_at,
        )

        def apply(current):
            current_plan = current.dubbing_production.active_plan
            if (
                current_plan is None
                or current_plan.source_revision != plan.source_revision
                or current_plan.plan_revision != plan.plan_revision
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
                    "配音计划已经变化，请重新读取当前分组。",
                )
            progress = next(
                item
                for item in self._build_production_run_snapshot(current).groups
                if item.group_id == group.group_id
            )
            if payload.candidate_id is not None and payload.candidate_id not in {
                *progress.candidate_ids,
                *(
                    [progress.passed_candidate_id]
                    if progress.passed_candidate_id
                    else []
                ),
            }:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_CHANGED",
                    "指定候选不属于当前配音分组，请重新读取生产进度。",
                )
            failures = [
                item
                for item in current.dubbing_production.group_failures
                if not (
                    item.source_revision == plan.source_revision
                    and item.plan_revision == plan.plan_revision
                    and item.group_id == group.group_id
                )
            ]
            failures.append(failure)
            state = current.dubbing_production.model_copy(
                update={
                    "group_failures": failures,
                    "latest_timeline_audit": None,
                }
            )
            return current.model_copy(update={"dubbing_production": state})

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return updated

    def _resolve_current_candidate_result(
        self,
        project_id: str,
        current,
        *,
        source_revision: str,
        plan_revision: int,
        group_id: str,
        candidate_id: str,
        result_id: str,
    ):
        """Resolve one exact current candidate through its durable history result."""

        self._assert_current_revision(current, source_revision)
        plan = current.dubbing_production.active_plan
        if plan is None or plan.plan_revision != plan_revision:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
                "配音计划已经变化，请重新读取当前分组。",
            )
        group_index = next(
            (index for index, item in enumerate(plan.groups) if item.group_id == group_id),
            None,
        )
        if group_index is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_RESULT_CHANGED",
                "当前配音分组已经变化，请重新读取生产进度。",
            )
        group = plan.groups[group_index]
        frozen = next(
            (
                item
                for item in reversed(current.dubbing_production.candidate_inputs)
                if item.source_revision == plan.source_revision
                and item.plan_revision == plan.plan_revision
                and item.group_id == group.group_id
                and item.candidate_id == candidate_id
            ),
            None,
        )
        if (
            frozen is None
            or frozen.task_status != "success"
            or frozen.audio is None
            or not frozen.audio_sha256
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_RESULT_CHANGED",
                "当前成功候选或其音频边界证据已经变化，请重新读取生产进度。",
            )

        history = history_store.get(result_id)
        source_path = history_store.audio_path(result_id) if history else None
        task = task_queue.get_task(history.task_id) if history else None
        if (
            history is None
            or source_path is None
            or not Path(source_path).is_file()
            or history.project_id != project_id
            or history.parameter_snapshot.get("source") != "video_localization"
            or task is None
            or task.status.value != "success"
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_AUDIO_UNAVAILABLE",
                "当前候选没有可复用的完整生成音频。",
            )
        task_targets = {
            str(value)
            for value in task.parameters.get(
                "video_localization_target_subtitle_ids", []
            )
            if str(value)
        }
        if not task_targets and task.localized_subtitle_id:
            task_targets = {str(task.localized_subtitle_id)}
        task_text = str(task.parameters.get("text") or task.input_text or "")
        current_source_cue_ids = _group_source_cue_ids(current, plan, group)
        task_source_cue_ids = list(
            dict.fromkeys(
                str(value)
                for value in task.parameters.get(
                    "video_localization_source_cue_ids", []
                )
                if str(value)
            )
        )
        task_plan_revision = int(
            task.parameters.get("video_localization_dubbing_plan_revision") or 0
        )
        current_context_fingerprint = domain.group_evidence_context_fingerprint(
            current, plan, group
        )
        task_plan_is_current = task_plan_revision == plan.plan_revision
        task_plan_is_retained_origin = bool(
            frozen.evidence_origin_plan_revision
            and task_plan_revision == frozen.evidence_origin_plan_revision
            and frozen.source_context_fingerprint == current_context_fingerprint
        )
        task_plan_is_rebound_current = bool(
            # Earlier equivalent-plan CQC rebinds did not persist origin
            # metadata. Their current frozen evidence is still usable only
            # after the exact group/source/text checks below prove that the
            # historical task remains this current group's input.
            0 < task_plan_revision < plan.plan_revision
            and frozen.source_revision == plan.source_revision
            and frozen.plan_revision == plan.plan_revision
            and frozen.source_context_fingerprint == current_context_fingerprint
        )
        task_has_no_managed_group_binding = not str(
            task.parameters.get("video_localization_dubbing_group_id") or ""
        )
        task_has_no_managed_plan_binding = task_plan_revision == 0
        manual_formal_binding_is_current = (
            task_has_no_managed_group_binding
            and task_has_no_managed_plan_binding
            and _has_exact_current_formal_candidate_binding(
                current,
                group=group,
                candidate_id=candidate_id,
                result_id=result_id,
                source_cue_ids=current_source_cue_ids,
            )
        )
        if (
            task_targets != set(group.subtitle_ids)
            or task_source_cue_ids != current_source_cue_ids
            or not (
                str(task.parameters.get("video_localization_dubbing_group_id") or "")
                == group.group_id
                or manual_formal_binding_is_current
            )
            or not (
                task_plan_is_current
                or task_plan_is_retained_origin
                or task_plan_is_rebound_current
                or manual_formal_binding_is_current
            )
            or frozen.source_context_fingerprint != current_context_fingerprint
            or frozen.target_start_ms != group.target_start_ms
            or frozen.target_end_ms != group.target_end_ms
            or domain.transcript_pronunciation_tokens(task_text)
            != domain.transcript_pronunciation_tokens(group.spoken_text)
            or domain.transcript_pronunciation_tokens(history.input_text)
            != domain.transcript_pronunciation_tokens(group.spoken_text)
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_BINDING_CHANGED",
                "生成结果不再与当前分组、字幕或朗读文本一致。",
            )
        result_candidates = {
            str(item.get("candidate_id") or item.get("result_id") or "")
            for item in current.generated_candidates
            if str(item.get("result_id") or "") == result_id
        }
        workflow_matches = any(
            workflow.result_id == result_id
            and candidate_id
            in {
                str(value)
                for value in (
                    workflow.result_id,
                    tts_pipeline.generated_candidate_id(workflow.generation_task_id)
                    if workflow.generation_task_id
                    else None,
                )
                if value
            }
            for workflow in current.tts_tasks
        )
        if candidate_id not in result_candidates and not workflow_matches:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_RESULT_CHANGED",
                "候选身份与当前持久生成结果不一致。",
            )
        try:
            current_audio_sha256 = media_assets.file_sha256(Path(source_path))
        except OSError as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_AUDIO_UNAVAILABLE",
                "当前候选音频无法读取。",
            ) from exc
        if current_audio_sha256 != frozen.audio_sha256:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_AUDIO_CHANGED",
                "候选音频文件与冻结证据不一致。",
            )
        return (
            plan,
            group,
            group_index,
            frozen,
            history,
            task,
            Path(source_path),
            current_audio_sha256,
        )

    def defer_group_for_manual_timing(
        self,
        project_id: str,
        payload: DubbingManualTimingDeferralRequest,
    ) -> DubbingManualTimingDeferralResponse:
        """Atomically park one verified full take on lane 1 without accepting it."""

        request_fingerprint = _stable_json_fingerprint(
            payload.model_dump(mode="json")
        )

        def apply(current):
            existing = next(
                (
                    item
                    for item in current.dubbing_production.manual_timing_deferrals
                    if item.request_id == payload.request_id
                ),
                None,
            )
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_REQUEST_CONFLICT",
                        "同一次次轨停放命令的内容发生了变化，请重新发起操作。",
                    )
                # Preserve an explicit user deletion. Idempotent replay reports
                # the saved disposition but never recreates the parked clip.
                return current

            if current._repository_revision != payload.expected_repository_revision:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_REPOSITORY_CHANGED",
                    "项目内容已经变化，请重新读取当前版本后再停放声音。",
                    {
                        "expected_repository_revision": current._repository_revision,
                        "received_repository_revision": payload.expected_repository_revision,
                    },
                )
            (
                plan,
                group,
                group_index,
                frozen,
                history,
                _task,
                source_path,
                current_audio_sha256,
            ) = self._resolve_current_candidate_result(
                project_id,
                current,
                source_revision=payload.source_revision,
                plan_revision=payload.plan_revision,
                group_id=payload.group_id,
                candidate_id=payload.candidate_id,
                result_id=payload.result_id,
            )
            if (
                frozen.content_evidence is None
                or not frozen.content_evidence.matches_audio(frozen.audio_sha256)
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_EVIDENCE_INCOMPLETE",
                    "当前声音缺少成功生成、完整内容核对或音频边界证据，不能停放为完整候选。",
                )
            if (
                _content_evidence_reference(frozen.content_evidence)
                not in payload.content_verification_evidence_ids
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_EVIDENCE_REFERENCE_MISMATCH",
                    "内容核对引用与当前候选音频的独立转写证据不一致。",
                )
            if (
                frozen.audio_sha256 != payload.audio_sha256
                or frozen.audio.duration_ms != payload.candidate_duration_ms
                or domain.transcript_pronunciation_tokens(frozen.expected_spoken_text)
                != domain.transcript_pronunciation_tokens(group.spoken_text)
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_CANDIDATE_CHANGED",
                    "当前候选内容、时长或音频身份已经变化，请重新核对。",
                )
            if int(history.duration_ms or 0) != payload.candidate_duration_ms:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_AUDIO_UNAVAILABLE",
                    "当前生成结果不是可复用的完整项目音频。",
                )
            if current_audio_sha256 != payload.audio_sha256:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_AUDIO_CHANGED",
                    "音频文件在核对后发生变化，未执行次轨停放。",
                )
            try:
                actual_audio_duration_ms = audio_tools.probe_audio_duration_ceil_ms(
                    Path(source_path)
                )
            except (OSError, ValueError, RuntimeError) as exc:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_AUDIO_UNAVAILABLE",
                    "当前生成音频无法读取完整时长。",
                ) from exc
            if actual_audio_duration_ms != payload.candidate_duration_ms:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_AUDIO_DURATION_CHANGED",
                    "音频文件实际时长与冻结候选不一致，未执行次轨停放。",
                    {"actual_audio_duration_ms": actual_audio_duration_ms},
                )

            source_onset_ms, protected_speech_end_ms = (
                candidate_source_speech_bounds(frozen.audio)
            )
            if source_onset_ms is None or protected_speech_end_ms is None:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_SPEECH_BOUNDS_MISSING",
                    "当前声音缺少完整起音和尾音边界，不能安全停放。",
                )
            placement_start_ms = max(0, group.target_start_ms - source_onset_ms)
            next_group = (
                plan.groups[group_index + 1]
                if group_index + 1 < len(plan.groups)
                else None
            )
            latest_end_ms = (
                next_group.target_start_ms
                if next_group is not None
                else group.target_end_ms
            )
            actual_available_duration_ms = latest_end_ms - placement_start_ms
            protected_conflict = first_protected_audio_overlap(
                [{"clip_id": payload.parked_clip_id, "track_id": "dub", "dub_lane": 0,
                  "start_ms": placement_start_ms, "source_start_ms": 0,
                  "source_end_ms": payload.candidate_duration_ms}],
                [clip for clip in current.timeline_clips
                 if not set(_clip_target_subtitle_ids(clip)).intersection(group.subtitle_ids)],
                frozen.audio,
            )
            duration_overflow = (
                payload.candidate_duration_ms > actual_available_duration_ms
                and placement_start_ms + protected_speech_end_ms
                > latest_end_ms + frozen.frame_tolerance_ms
            )
            if (
                actual_available_duration_ms <= 0
                or payload.available_duration_ms != actual_available_duration_ms
                or not (duration_overflow or protected_conflict)
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_WINDOW_CHANGED",
                    "当前窗口证据已变化，或完整声音可以安全落位，请重新核对。",
                    {"actual_available_duration_ms": actual_available_duration_ms},
                )

            subtitle_by_id = {
                item.subtitle_id: item for item in current.localized_subtitles
            }
            if any(item not in subtitle_by_id for item in group.subtitle_ids):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_TARGET_CHANGED",
                    "当前分组引用的目标字幕已经变化。",
                )
            source_cue_ids = _group_source_cue_ids(current, plan, group)
            if not source_cue_ids:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_SOURCE_BINDING_MISSING",
                    "当前分组缺少完整来源 cue 绑定。",
                )
            existing_clips = [
                clip for clip in current.timeline_clips
                if str(clip.get("clip_id") or "") == payload.parked_clip_id
            ]
            if len(existing_clips) > 1:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_CLIP_CONFLICT",
                    "次轨片段 ID 已被其他时间线内容占用。",
                )

            existing_clip = existing_clips[0] if existing_clips else None
            adopted_path = existing_clip.get("audio_path", "") if existing_clip is not None else media_assets.adopt_tts_audio(
                project_id,
                Path(source_path),
                group.group_id,
                payload.result_id,
            )
            placed = tts_placement.place_frozen_tts_result(
                current,
                tts_placement.FrozenTtsPlacementTarget(
                    segment_id=group.subtitle_ids[0],
                    target_subtitle_ids=tuple(group.subtitle_ids),
                    source_cue_ids=tuple(source_cue_ids),
                    start_ms=group.target_start_ms,
                    end_ms=latest_end_ms,
                    text=group.spoken_text,
                ),
                tts_placement.TtsResultMetadata(
                    result_id=payload.result_id,
                    output_path=str(adopted_path),
                    duration_ms=payload.candidate_duration_ms,
                    task_id=history.task_id,
                    generation_id=history.generation_id or history.task_id,
                    timeline_clip_id=payload.parked_clip_id,
                    candidate_id=payload.candidate_id,
                    placement_start_ms=placement_start_ms,
                    source_start_ms=0,
                    source_end_ms=payload.candidate_duration_ms,
                    speech_onset_ms=source_onset_ms,
                ),
            )
            parked_clip = next(
                dict(item)
                for item in placed.draft.timeline_clips
                if str(item.get("clip_id") or "") == payload.parked_clip_id
            )
            parked_clip.update(
                {
                    "dub_lane": 1,
                    "dubbing_group_id": group.group_id,
                    "intentional_overlap": False,
                    "manual_timing_adjustment_required": True,
                }
            )
            next_clips = [
                parked_clip
                if str(item.get("clip_id") or "") == payload.parked_clip_id
                else dict(item)
                for item in placed.draft.timeline_clips
            ]
            disposition = DubbingManualTimingDeferral(
                request_id=payload.request_id,
                request_fingerprint=request_fingerprint,
                source_revision=plan.source_revision,
                plan_revision=plan.plan_revision,
                evidence_origin_source_revision=plan.source_revision,
                evidence_origin_plan_revision=plan.plan_revision,
                group_id=group.group_id,
                candidate_id=payload.candidate_id,
                result_id=payload.result_id,
                parked_clip_id=payload.parked_clip_id,
                placement_failure_reason=("duration_overflow" if duration_overflow else "protected_audio_conflict"),
                conflicting_clip_ids=([protected_conflict[1]] if protected_conflict else []),
                target_subtitle_ids=list(group.subtitle_ids),
                source_cue_ids=source_cue_ids,
                available_duration_ms=actual_available_duration_ms,
                candidate_duration_ms=payload.candidate_duration_ms,
                audio_sha256=payload.audio_sha256,
                source_context_fingerprint=domain.group_evidence_context_fingerprint(
                    current, plan, group
                ),
                candidate_spoken_text_fingerprint=_spoken_text_fingerprint(
                    group.spoken_text
                ),
                clip_projection_fingerprint=candidate_clip_projection_fingerprint(
                    [parked_clip]
                ),
                content_verification_status=payload.content_verification_status,
                content_verification_evidence_ids=(
                    payload.content_verification_evidence_ids
                ),
                semantic_boundary_review=payload.semantic_boundary_review,
                semantic_boundary_evidence_ids=payload.semantic_boundary_evidence_ids,
                naturalness_review=payload.naturalness_review,
                recovery_evidence=payload.recovery_evidence,
                created_at=now_iso(),
            )
            if existing_clip is not None:
                # Revalidation is a fresh assertion against current dependencies,
                # not an overwrite or an automatic resurrection of stale proof.
                hashes = dubbing_media.current_timeline_audio_sha256s(project_id, current)
                valid = dubbing_production_run.valid_manual_timing_deferrals(
                    active_plan=plan,
                    dispositions=[disposition],
                    timeline_clips=current.timeline_clips,
                    playable_clip_ids={key for key, value in hashes.items() if value is not None},
                    audio_sha256_by_clip_id=hashes,
                )
                if group.group_id not in valid or not existing_clip.get("manual_timing_adjustment_required"):
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_CLIP_CONFLICT",
                        "原次轨片段的音频、绑定或位置已变化，未覆盖已有编辑。",
                    )
                next_clips = current.timeline_clips
            remaining = [
                item
                for item in current.dubbing_production.manual_timing_deferrals
                if not (
                    item.source_revision == plan.source_revision
                    and item.plan_revision == plan.plan_revision
                    and item.group_id == group.group_id
                )
            ]
            state = current.dubbing_production.model_copy(
                update={
                    "manual_timing_deferrals": [*remaining, disposition],
                    "latest_timeline_audit": None,
                }
            )
            lane_states = current.ui_state.get("dub_lane_states", {})
            lane_states = dict(lane_states) if isinstance(lane_states, dict) else {}
            lane_states.setdefault(
                "1",
                {"muted": False, "solo": False, "volume": 1, "locked": False},
            )
            committed = (current if existing_clip is not None else placed.draft).model_copy(
                update={
                    # A single-target placement mirrors history into the subtitle.
                    # The lane-1 fallback does not own that primary selection.
                    "localized_subtitles": current.localized_subtitles,
                    "timeline_clips": next_clips,
                    "dubbing_production": state,
                    "ui_state": {**current.ui_state, "dub_lane_states": lane_states},
                }
            )
            return project_service.with_completed_tts_placement(
                committed,
                generation_task_id=history.task_id,
                result_id=payload.result_id,
                timeline_clip_id=payload.parked_clip_id,
                dub_lane=1,
            )

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None or updated._repository_revision is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        disposition = next(
            item
            for item in updated.dubbing_production.manual_timing_deferrals
            if item.request_id == payload.request_id
        )
        return DubbingManualTimingDeferralResponse(
            repository_revision=updated._repository_revision,
            disposition=disposition,
            production_run=self.read_production_run(project_id),
        )

    def acquire_candidate_content_evidence(
        self,
        project_id: str,
        payload: DubbingCandidateContentEvidenceRequest,
    ) -> DubbingCandidateContentEvidenceResponse:
        """Transcribe exact candidate bytes outside CAS, then persist if still current."""

        draft = self._require_current_project(project_id)
        if draft._repository_revision != payload.expected_repository_revision:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_REPOSITORY_CHANGED",
                "项目内容已经变化，请重新读取当前版本后再核对候选内容。",
            )
        (
            _plan,
            _group,
            _group_index,
            frozen,
            history,
            _task,
            source_path,
            _audio_sha256,
        ) = self._resolve_current_candidate_result(
            project_id,
            draft,
            source_revision=payload.source_revision,
            plan_revision=payload.plan_revision,
            group_id=payload.group_id,
            candidate_id=payload.candidate_id,
            result_id=payload.result_id,
        )
        retained = None
        if payload.use_current_timeline_projection:
            clips = _current_group_content_evidence_clips(
                draft,
                group=_group,
                frozen=frozen,
                candidate_id=payload.candidate_id,
            )
            if not clips:
                raise AppException(409, "DUBBING_RETAINED_PROJECTION_REQUIRED",
                                   "当前候选没有覆盖本组字幕的实际裁切片段。")
            fingerprint = _normalized_candidate_projection_fingerprint(clips, payload.candidate_id)
            cached = frozen.retained_content_evidence
            with dubbing_media.rendered_candidate_projection(source_path, clips) as rendered:
                evidence = tts_content_verification.acquire_content_evidence(
                    rendered, cached.observation if cached else None,
                )
            retained = DubbingRetainedContentEvidence(
                audio_sha256=_audio_sha256,
                candidate_clip_projection_fingerprint=fingerprint,
                observation=evidence,
            )
        else:
            evidence = tts_content_verification.acquire_content_evidence(
                source_path,
                frozen.content_evidence or getattr(history, "content_evidence", None),
            )

        def apply(current):
            if current._repository_revision != payload.expected_repository_revision:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_REPOSITORY_CHANGED",
                    "内容核对期间项目已发生变化，证据未写入；请刷新后重试。",
                )
            (
                _plan,
                _group,
                _group_index,
                current_frozen,
                _history,
                _task,
                _path,
                current_hash,
            ) = self._resolve_current_candidate_result(
                project_id,
                current,
                source_revision=payload.source_revision,
                plan_revision=payload.plan_revision,
                group_id=payload.group_id,
                candidate_id=payload.candidate_id,
                result_id=payload.result_id,
            )
            if (retained.audio_sha256 if retained else evidence.audio_sha256) != current_hash:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_CONTENT_EVIDENCE_AUDIO_CHANGED",
                    "内容核对期间候选音频已发生变化，证据未写入。",
                )
            if retained:
                current_clips = _current_group_content_evidence_clips(
                    current,
                    group=_group,
                    frozen=current_frozen,
                    candidate_id=payload.candidate_id,
                )
                if not current_clips or _normalized_candidate_projection_fingerprint(
                    current_clips, payload.candidate_id,
                ) != retained.candidate_clip_projection_fingerprint:
                    raise AppException(409, "DUBBING_RETAINED_PROJECTION_CHANGED",
                                       "实际保留范围已经变化，请重新核对裁后内容。")
            updated_frozen = current_frozen.model_copy(
                update={"retained_content_evidence": retained} if retained else {
                    "candidate_transcript": evidence.transcript,
                    "content_evidence": evidence,
                }
            )
            report = domain.build_candidate_gap_processing_report(updated_frozen)
            previous_report = next(
                (
                    item
                    for item in current.dubbing_production.candidate_reports
                    if item.candidate_id == payload.candidate_id
                ),
                None,
            )
            timeline_clips = _current_group_working_candidate_clips(
                current,
                group_id=_group.group_id,
                candidate_id=payload.candidate_id,
                target_subtitle_ids=list(_group.subtitle_ids),
            )
            if (
                retained is not None
                and previous_report is not None
                and previous_report.staged_candidate_projection is not None
                and previous_report.semantic_boundary_audit is not None
                and previous_report.source_revision == _plan.source_revision
                and previous_report.plan_revision == _plan.plan_revision
                and previous_report.evidence_fingerprint == domain.candidate_evidence_fingerprint(current_frozen)
                and previous_report.semantic_boundary_audit.audio_sha256 == current_frozen.audio_sha256
                and previous_report.semantic_boundary_audit.candidate_evidence_fingerprint
                == previous_report.evidence_fingerprint
                and previous_report.semantic_boundary_audit.candidate_clip_projection_fingerprint
                == previous_report.staged_candidate_projection.candidate_clip_projection_fingerprint
                and (
                    not timeline_clips
                    or candidate_clip_projection_fingerprint(timeline_clips)
                    == previous_report.staged_candidate_projection.candidate_clip_projection_fingerprint
                )
            ):
                # Retained-content observation changes the frozen CQC input,
                # not an unchanged staged OR adopted projection. Preserve the
                # existing decisions without inventing a new semantic review.
                report = report.model_copy(update={
                    "staged_candidate_projection": (
                        previous_report.staged_candidate_projection
                    ),
                    "semantic_boundary_audit": (
                        previous_report.semantic_boundary_audit.model_copy(update={
                            "candidate_evidence_fingerprint": report.evidence_fingerprint,
                        })
                    ),
                })
            inputs = [
                updated_frozen if item.candidate_id == payload.candidate_id else item
                for item in current.dubbing_production.candidate_inputs
            ]
            reports = [
                item
                for item in current.dubbing_production.candidate_reports
                if item.candidate_id != payload.candidate_id
            ]
            reports.append(report)
            state = current.dubbing_production.model_copy(
                update={
                    "candidate_inputs": inputs,
                    "candidate_reports": reports,
                    "latest_timeline_audit": None,
                }
            )
            return current.model_copy(update={"dubbing_production": state})

        saved = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if saved is None or saved._repository_revision is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return DubbingCandidateContentEvidenceResponse(
            repository_revision=saved._repository_revision,
            source_revision=payload.source_revision,
            plan_revision=payload.plan_revision,
            group_id=payload.group_id,
            candidate_id=payload.candidate_id,
            result_id=payload.result_id,
            evidence_id=_content_evidence_reference(evidence),
            evidence=evidence,
        )

    def create_generation_plan(
        self,
        project_id: str,
        payload: DubbingGenerationPlanInput,
    ) -> DubbingGenerationPlan:
        draft = self._require_current_project(project_id)
        self._assert_current_revision(draft, payload.source_revision)
        snapshot = domain.build_project_snapshot(draft)
        reviewed_input = _bind_plan_input_to_snapshot(snapshot, payload)
        plan = domain.build_generation_plan(reviewed_input)
        return self._persist_plan(project_id, plan)

    def refresh_generation_plan_timing(
        self,
        project_id: str,
    ) -> DubbingGenerationPlan:
        """Refresh only word-backed timing while preserving reviewed structure."""

        draft = self._require_current_project(project_id)
        plan = draft.dubbing_production.active_plan
        if plan is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_REQUIRED",
                "请先完成配音生成计划。",
            )
        snapshot = domain.build_project_snapshot(draft)
        try:
            refreshed = domain.refresh_generation_plan_timings(snapshot, plan)
        except ValueError as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_TIMING_REFRESH_UNSAFE",
                "当前字幕内容或分组成员已经变化，不能只刷新时间。",
                {"reason": str(exc)},
            ) from exc
        return self._persist_plan(project_id, refreshed)

    def clear_group_timeline_placements(
        self,
        project_id: str,
        payload: DubbingTimelineGroupResetRequest,
    ):
        """Clear exact current-plan group placements without deleting media history."""

        draft = self._require_current_project(project_id)
        self._assert_current_revision(draft, payload.source_revision)
        plan = draft.dubbing_production.active_plan
        if plan is None or plan.plan_revision != payload.plan_revision:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
                "配音计划已经变化，请重新读取当前分组。",
            )
        known_group_ids = {item.group_id for item in plan.groups}
        unknown = sorted(set(payload.group_ids) - known_group_ids)
        if unknown:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUBBING_GROUP_NOT_FOUND",
                "待清理的配音分组不属于当前计划。",
                {"group_ids": unknown},
            )
        selected = set(payload.group_ids)
        selected_subtitle_ids = {
            subtitle_id
            for group in plan.groups
            if group.group_id in selected
            for subtitle_id in group.subtitle_ids
        }

        def apply(current):
            current_plan = current.dubbing_production.active_plan
            if (
                current_plan is None
                or current_plan.source_revision != payload.source_revision
                or current_plan.plan_revision != payload.plan_revision
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
                    "配音计划已经变化，请重新读取当前分组。",
                )
            clips = [
                dict(item)
                for item in current.timeline_clips
                if not (
                    item.get("track_id") == "dub"
                    and (
                        str(item.get("dubbing_group_id") or "") in selected
                        or bool(
                            selected_subtitle_ids.intersection(
                                str(value)
                                for value in item.get(
                                    "target_subtitle_ids",
                                    [],
                                )
                                if str(value)
                            )
                        )
                    )
                )
            ]
            reviews = [
                item
                for item in current.dubbing_production.group_reviews
                if not (
                    item.source_revision == payload.source_revision
                    and item.plan_revision == payload.plan_revision
                    and item.group_id in selected
                )
            ]
            failures = [
                item
                for item in current.dubbing_production.group_failures
                if not (
                    item.source_revision == payload.source_revision
                    and item.plan_revision == payload.plan_revision
                    and item.group_id in selected
                )
            ]
            candidate_inputs = list(
                current.dubbing_production.candidate_inputs
            )
            candidate_reports = list(
                current.dubbing_production.candidate_reports
            )
            if payload.discard_current_candidates:
                candidate_inputs = [
                    item
                    for item in candidate_inputs
                    if not (
                        item.source_revision == payload.source_revision
                        and item.plan_revision == payload.plan_revision
                        and item.group_id in selected
                    )
                ]
                candidate_reports = [
                    item
                    for item in candidate_reports
                    if not (
                        item.source_revision == payload.source_revision
                        and item.plan_revision == payload.plan_revision
                        and item.group_id in selected
                    )
                ]
            ui_state = dict(current.ui_state)
            if payload.discard_current_candidates:
                discarded = {
                    str(value)
                    for value in ui_state.get("discarded_tts_task_ids", [])
                    if str(value)
                }
                for workflow in current.tts_tasks:
                    generation = next(
                        (
                            stage
                            for stage in workflow.stages
                            if stage.kind == "generation"
                        ),
                        None,
                    )
                    parameters = generation.parameters if generation else {}
                    if (
                        int(
                            parameters.get(
                                "video_localization_dubbing_plan_revision"
                            )
                            or 0
                        )
                        != payload.plan_revision
                        or str(
                            parameters.get(
                                "video_localization_dubbing_group_id"
                            )
                            or ""
                        )
                        not in selected
                    ):
                        continue
                    discarded.update(
                        str(value)
                        for value in (
                            workflow.workflow_id,
                            workflow.generation_task_id,
                            workflow.result_id,
                        )
                        if value
                    )
                latest = ui_state.get("latest_tts_task_by_segment", {})
                if isinstance(latest, dict):
                    latest = {
                        key: value
                        for key, value in latest.items()
                        if str(value or "") not in discarded
                    }
                ui_state.update(
                    {
                        "discarded_tts_task_ids": sorted(discarded),
                        "latest_tts_task_by_segment": latest,
                    }
                )
            state = current.dubbing_production.model_copy(
                update={
                    "group_failures": failures,
                    "group_reviews": reviews,
                    "manual_timing_deferrals": [
                        item
                        for item in current.dubbing_production.manual_timing_deferrals
                        if not (
                            item.source_revision == payload.source_revision
                            and item.plan_revision == payload.plan_revision
                            and item.group_id in selected
                        )
                    ],
                    "existing_formal_acceptances": [
                        item
                        for item in current.dubbing_production.existing_formal_acceptances
                        if not (
                            item.source_revision == payload.source_revision
                            and item.plan_revision == payload.plan_revision
                            and item.group_id in selected
                        )
                    ],
                    "candidate_inputs": candidate_inputs,
                    "candidate_reports": candidate_reports,
                    "latest_timeline_audit": None,
                }
            )
            return current.model_copy(
                update={
                    "timeline_clips": clips,
                    "dubbing_production": state,
                    "ui_state": ui_state,
                }
            )

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return updated

    def persist_gap_processing_report(
        self,
        project_id: str,
        candidate_id: str,
        *,
        gap_decisions: list,
    ) -> DubbingCandidateCqcReport:
        draft = self._require_current_project(project_id)
        plan = draft.dubbing_production.active_plan
        if plan is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_REQUIRED",
                "请先基于当前字幕和画面完成配音生成计划。",
            )
        frozen = next(
            (
                item
                for item in draft.dubbing_production.candidate_inputs
                if item.candidate_id == candidate_id
            ),
            None,
        )
        if frozen is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_CQC_INPUT_REQUIRED",
                "该候选缺少 worker 冻结的气口证据，不能继续处理。",
                {"candidate_id": candidate_id},
            )
        if frozen.source_revision != plan.source_revision:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_SOURCE_CHANGED",
                "候选自动证据不属于当前配音计划，请重新生成。",
            )
        if frozen.plan_revision != plan.plan_revision:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
                "候选自动证据不属于本次配音计划，请重新生成。",
            )
        if not any(group.group_id == frozen.group_id for group in plan.groups):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_GROUP_STALE",
                "候选自动证据不属于当前生成分组，请重新生成。",
            )
        group = next(
            item for item in plan.groups if item.group_id == frozen.group_id
        )
        current_group_clips = [
            dict(item)
            for item in draft.timeline_clips
            if item.get("track_id") == "dub"
            and str(item.get("candidate_id") or item.get("result_id") or "")
            == candidate_id
            and set(
                str(value)
                for value in item.get("target_subtitle_ids") or []
                if str(value)
            )
            == set(group.subtitle_ids)
        ]
        # Automatic media evidence belongs to the immutable rendered file, but
        # placement evidence belongs to the current editable projection.  A
        # candidate may first fail because its raw take overruns a scene, then
        # become valid after evidence-backed trimming.  Re-evaluate against the
        # persisted clips instead of keeping the pre-edit placement frozen.
        current_placement = {}
        if current_group_clips:
            source_onset_ms, protected_speech_end_ms = (
                _candidate_word_anchor_and_protected_end(frozen.audio)
                if frozen.audio is not None else (None, None)
            )
            audible_bounds = (
                candidate_audible_timeline_bounds(
                    current_group_clips,
                    speech_start_ms=source_onset_ms,
                    speech_end_ms=protected_speech_end_ms,
                )
                if source_onset_ms is not None
                and protected_speech_end_ms is not None
                else None
            )
            placement_start_ms, placement_end_ms = audible_bounds or (
                min(
                    int(item.get("start_ms") or 0)
                    for item in current_group_clips
                ),
                max(
                    int(item.get("end_ms") or 0)
                    for item in current_group_clips
                ),
            )
            current_placement = {
                "placement_start_ms": placement_start_ms,
                "placement_end_ms": placement_end_ms,
            }
        reviewed_audio = frozen.audio
        if gap_decisions:
            if reviewed_audio is None:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_GAP_EVIDENCE_REQUIRED",
                    "候选缺少原始音频间隙证据。",
                )
            frozen_gaps = {
                gap.gap_id: gap for gap in reviewed_audio.gap_evidence
            }
            for review in gap_decisions:
                original = frozen_gaps.get(review.gap_id)
                if original is None or (
                    review.kind,
                    review.start_ms,
                    review.end_ms,
                    review.duration_ms,
                ) != (
                    original.kind,
                    original.start_ms,
                    original.end_ms,
                    original.duration_ms,
                ):
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_GAP_EVIDENCE_CHANGED",
                        "气口处理不能改变 worker 冻结的真实音频范围。",
                        {"gap_id": review.gap_id},
                    )
                if not set(original.evidence_ids).issubset(
                    review.evidence_ids
                ):
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_GAP_EVIDENCE_CHANGED",
                        "气口处理必须保留 worker 冻结的原始证据。",
                        {"gap_id": review.gap_id},
                    )
                frozen_gaps[review.gap_id] = review
            reviewed_audio = reviewed_audio.model_copy(
                update={
                    "gap_evidence": [
                        frozen_gaps[gap.gap_id]
                        for gap in reviewed_audio.gap_evidence
                    ]
                }
            )
        reviewed_input = frozen.model_copy(
            update={
                "subjective_reviews": [],
                "audio": reviewed_audio,
                **current_placement,
            }
        )
        report = domain.build_candidate_gap_processing_report(reviewed_input)
        self._persist_candidate_report(
            project_id,
            report,
            reviewed_input,
            freeze_input=False,
        )
        return report

    def read_candidate_cqc_input(
        self,
        project_id: str,
        candidate_id: str,
    ) -> DubbingCandidateCqcInput:
        draft = self._require_current_project(project_id)
        current = next(
            (
                item
                for item in draft.dubbing_production.candidate_inputs
                if item.candidate_id == candidate_id
            ),
            None,
        )
        if current is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_DUBBING_CQC_INPUT_NOT_FOUND",
                "该候选还没有可复用的气口证据，请重新执行当前片段气口处理。",
            )
        self._assert_current_revision(draft, current.source_revision)
        plan = draft.dubbing_production.active_plan
        if plan is None or current.plan_revision != plan.plan_revision:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
                "候选证据不属于本次配音计划，请重新生成。",
            )
        return current

    def finalize_generated_candidate(
        self,
        project_id: str,
        candidate_id: str,
        group_id: str,
        *,
        review_mode: DubbingProductionReviewMode = "full",
    ) -> Literal[
        "accepted",
        "needs_semantic_review",
        "capacity_recovery_required",
        "regeneration_required",
        "retryable_failure",
    ]:
        """Trim the current take's proven gaps, place it, then finish the group."""

        del review_mode  # Supervised and automatic modes share this exact path.

        draft = self._require_current_project(project_id)
        plan = draft.dubbing_production.active_plan
        frozen = next(
            (
                item
                for item in draft.dubbing_production.candidate_inputs
                if item.candidate_id == candidate_id and item.group_id == group_id
            ),
            None,
        )
        group = next(
            (item for item in (plan.groups if plan else []) if item.group_id == group_id),
            None,
        )
        if plan is None or frozen is None or group is None:
            return "retryable_failure"
        persisted_evidence_fingerprint = domain.candidate_evidence_fingerprint(
            frozen
        )
        media_report = domain.build_candidate_gap_processing_report(frozen)
        if media_report.overall_status == "failed":
            return ("regeneration_required" if media_report.recommended_action == "regenerate"
                    else "retryable_failure")
        if frozen.audio is None or not frozen.audio.aligned_words:
            return "retryable_failure"
        frozen = frozen.model_copy(
            update={
                "audio": dubbing_candidate_alignment.with_alignment_evidence(
                    frozen.audio,
                    list(frozen.audio.aligned_words),
                )
            }
        )

        existing_candidate_clips = _current_group_working_candidate_clips(
            draft,
            group_id=group_id,
            candidate_id=candidate_id,
            target_subtitle_ids=list(group.subtitle_ids),
        )
        previous_report = next(
            (
                item
                for item in draft.dubbing_production.candidate_reports
                if item.candidate_id == candidate_id
            ),
            None,
        )
        previous_audit = (
            previous_report.semantic_boundary_audit
            if previous_report is not None
            else None
        )
        previous_stage = (
            previous_report.staged_candidate_projection
            if previous_report is not None
            else None
        )
        if (
            existing_candidate_clips
            and previous_audit is not None
            and previous_audit.status == "accepted"
            and previous_stage is not None
            and previous_stage.candidate_id == candidate_id
            and previous_audit.source_revision == plan.source_revision
            and previous_audit.plan_revision == plan.plan_revision
            and previous_audit.audio_sha256 == frozen.audio_sha256
            and previous_audit.candidate_evidence_fingerprint
            == persisted_evidence_fingerprint
            and previous_audit.candidate_clip_projection_fingerprint
            == previous_stage.candidate_clip_projection_fingerprint
            and candidate_clip_projection_fingerprint(existing_candidate_clips)
            == previous_stage.candidate_clip_projection_fingerprint
        ):
            # An accepted staged projection is already the formal group take.
            # Re-entering continuation must not run gap processing/rebalancing
            # again: that would turn a reviewed source split back into a new
            # staged candidate and can reorder equal legacy slice indexes.
            try:
                self._reconcile_accepted_projection_gap_evidence(
                    project_id,
                    group_id=group_id,
                    candidate_id=candidate_id,
                    expected_audit=previous_audit,
                    expected_stage=previous_stage,
                )
            except AppException:
                return "retryable_failure"
            return "accepted"
        verified_edge_edit = _verified_retained_projection(frozen, existing_candidate_clips)
        existing_candidate_clips = [
            {**clip, "target_start_ms": group.target_start_ms,
             "target_end_ms": group.target_end_ms,
             "dubbing_alignment_word_ids": list(clip.get("dubbing_alignment_word_ids") or [])}
            for clip in existing_candidate_clips
        ]
        # Build and edit the candidate on an in-memory projection. The live
        # timeline keeps its current take until every deterministic gas edit
        # has succeeded; final adoption is one atomic target-owned commit.
        if existing_candidate_clips:
            # A trim/split replaces its source clip.  A stale full-workspace
            # save can nevertheless leave that source beside its descendants.
            # Rebuild the target-owned projection from the current leaves so
            # close-out neither revives the source nor treats it as another
            # take competing with the user's edited result.
            working = _without_target_owned_formal_clips(
                draft,
                target_subtitle_ids=list(group.subtitle_ids),
            )
            working = working.model_copy(
                update={
                    "timeline_clips": [
                        *[dict(item) for item in working.timeline_clips],
                        *existing_candidate_clips,
                    ]
                }
            )
        else:
            # Regeneration keeps the current take visible in the live Draft,
            # but the replacement candidate must be scheduled against a
            # projection where that take no longer occupies the same target.
            # Otherwise the rebalancer treats old and new takes as consecutive
            # slices of one group and pushes the replacement past its anchor.
            working = _without_target_owned_formal_clips(
                draft,
                target_subtitle_ids=list(group.subtitle_ids),
            )
            working = tts_pipeline.with_applied_generated_candidate(
                working,
                candidate_id,
            )
            working = timeline_clip_timing.normalize_draft(working)
            working_plan = working.dubbing_production.active_plan
            if working_plan is None:
                return "retryable_failure"
            snapshot = domain.build_project_snapshot(working)
            working = working.model_copy(
                update={
                    "timeline_clips": domain.rebalance_selected_group_timeline_clips(
                        timeline_clips=[dict(item) for item in working.timeline_clips],
                        groups=working_plan.groups,
                        boundaries=snapshot.boundaries,
                        group_id=group_id,
                    )
                }
            )
        # Gas editing is deterministic and local. It uses frozen VAD/word
        # evidence only; no model review or content-quality pass is started.
        source_pauses = _source_pause_rhythm_for_group(
            draft,
            plan=plan,
            group=group,
        )
        reviewed_gaps = dubbing_gap_adjudication.process_gap_evidence(
            list(frozen.audio.gap_evidence),
            aligned_words=list(frozen.audio.aligned_words),
            speech_start_ms=frozen.audio.speech_start_ms,
            speech_end_ms=frozen.audio.speech_end_ms,
            audio_duration_ms=frozen.audio.duration_ms,
            expected_spoken_text=frozen.expected_spoken_text,
            source_pauses=source_pauses,
        )
        working = _with_trimmed_automatic_outer_gaps(
            working,
            candidate_id=candidate_id,
            frozen=frozen,
            reviewed_gaps=reviewed_gaps,
            preserve_existing_placement=bool(existing_candidate_clips),
            preserve_verified_edge_edit=verified_edge_edit,
        )
        if existing_candidate_clips and not verified_edge_edit:
            working = _with_restored_retained_internal_gaps(
                working,
                candidate_id=candidate_id,
                reviewed_gaps=reviewed_gaps,
            )
        elif not existing_candidate_clips:
            current_clips = _current_group_working_candidate_clips(
                working, group_id=group_id, candidate_id=candidate_id,
                target_subtitle_ids=list(group.subtitle_ids),
            )
            previous_end = max(
                [max(0, group.target_start_ms - 80)]
                + [int(clip.get("end_ms") or 0) for clip in draft.timeline_clips
                   if clip.get("track_id") == "dub" and int(clip.get("dub_lane") or 0) == 0
                   and clip.get("status") == "ready"
                   and int(clip.get("start_ms") or 0) < group.target_start_ms]
            )
            duration = sum(int(c.get("source_end_ms") or 0) - int(c.get("source_start_ms") or 0)
                           for c in current_clips)
            reviewed_gaps = _shorten_safe_gaps_to_fit(
                reviewed_gaps, list(frozen.audio.aligned_words),
                required_ms=max(0, duration - max(0, group.target_end_ms - previous_end)),
            )
        split_request = self._build_automatic_gap_split_request(
            working,
            candidate_id=candidate_id,
            group=group,
            frozen=frozen.model_copy(
                update={
                    "audio": frozen.audio.model_copy(
                        update={"gap_evidence": reviewed_gaps}
                    )
                }
            ),
            reviewed_gaps=reviewed_gaps,
            source_pauses=source_pauses,
        )
        if split_request is not None:
            try:
                snapshot = domain.build_project_snapshot(working)
                working = working.model_copy(
                    update={
                        "timeline_clips": domain.split_planned_timeline_clips(
                            timeline_clips=[
                                dict(item) for item in working.timeline_clips
                            ],
                            commands=list(split_request.commands),
                            groups=list(plan.groups),
                            localized_subtitles=list(working.localized_subtitles),
                            boundaries=snapshot.boundaries,
                        )
                    }
                )
            except (AppException, ValueError):
                # A requested evidence-backed edit is part of close-out. Do
                # not accept a projection that failed to apply it.
                return "retryable_failure"

        group_index = next(
            index for index, item in enumerate(plan.groups)
            if item.group_id == group.group_id
        )
        next_group = (
            plan.groups[group_index + 1]
            if group_index + 1 < len(plan.groups)
            else None
        )
        capacity_latest_end_ms = (
            next_group.target_start_ms
            if next_group is not None
            else group.target_end_ms
        )
        # The next take can start before its first word to retain safe padding.
        # Spacing, padding crop and final capacity checks must share that real
        # occupied boundary, not only the next group's audible onset.
        capacity_latest_end_ms = min(
            [int(capacity_latest_end_ms)]
            + [
                int(clip.get("start_ms") or 0)
                for clip in draft.timeline_clips
                if clip.get("track_id") == "dub"
                and int(clip.get("dub_lane") or 0) == 0
                and clip.get("status") == "ready"
                and int(clip.get("start_ms") or 0) >= group.target_end_ms
            ]
        )
        working = _with_source_rhythm_spacing(
            working,
            candidate_id=candidate_id,
            reviewed_gaps=reviewed_gaps,
            aligned_words=list(frozen.audio.aligned_words),
            source_pauses=source_pauses,
            expected_spoken_text=frozen.expected_spoken_text,
            latest_end_ms=capacity_latest_end_ms,
        )
        working = timeline_clip_timing.normalize_draft(working)
        candidate_clips = _current_group_working_candidate_clips(
            working,
            group_id=group_id,
            candidate_id=candidate_id,
            target_subtitle_ids=list(group.subtitle_ids),
        )
        if not candidate_clips:
            return "retryable_failure"
        if not existing_candidate_clips:
            source_onset_ms, protected_speech_end_ms = (
                _candidate_word_anchor_and_protected_end(frozen.audio)
            )
            # New takes inherit the plan's source onset, not a scheduler's
            # capacity preference.  A later tail conflict is recoverable; an
            # early first word is not an acceptable way to avoid it.
            working = _with_locked_candidate_source_onset(
                working,
                candidate_id=candidate_id,
                speech_start_ms=source_onset_ms,
                speech_end_ms=protected_speech_end_ms,
                target_start_ms=group.target_start_ms,
            )
            candidate_clips = _current_group_working_candidate_clips(
                working,
                group_id=group_id,
                candidate_id=candidate_id,
                target_subtitle_ids=list(group.subtitle_ids),
            )
            if not candidate_clips:
                return "retryable_failure"
        selected_start_anchor_ms = min(
            int(clip.get("start_ms") or 0) for clip in candidate_clips
        )
        # Rebuilt alignment can reveal a final word that an older projection
        # cropped away. Restore the complete source range first, then let the
        # shared scheduler use available neighboring room without touching a
        # phoneme, a measured source onset, or unrelated groups.
        working_snapshot = domain.build_project_snapshot(working)
        working = working.model_copy(
            update={
                "timeline_clips": domain.rebalance_selected_group_timeline_clips(
                    timeline_clips=[dict(item) for item in working.timeline_clips],
                    groups=plan.groups,
                    boundaries=working_snapshot.boundaries,
                    group_id=group_id,
                    selected_start_anchor_ms=selected_start_anchor_ms,
                )
            }
        )

        candidate_clips = _current_group_working_candidate_clips(
            working,
            group_id=group_id,
            candidate_id=candidate_id,
            target_subtitle_ids=list(group.subtitle_ids),
        )
        candidate_clips = _fit_trailing_safety_margin(
            candidate_clips, frozen.audio, latest_end_ms=capacity_latest_end_ms,
        )
        fitted_by_id = {clip["clip_id"]: clip for clip in candidate_clips}
        working = working.model_copy(update={"timeline_clips": [
            fitted_by_id.get(clip.get("clip_id"), clip) for clip in working.timeline_clips
        ]})
        candidate_clips = _current_group_working_candidate_clips(
            working,
            group_id=group_id,
            candidate_id=candidate_id,
            target_subtitle_ids=list(group.subtitle_ids),
        )
        candidate_ids = {
            str(clip.get("clip_id") or "") for clip in candidate_clips
        }
        other_clips = [
            dict(clip)
            for clip in working.timeline_clips
            if str(clip.get("clip_id") or "") not in candidate_ids
        ]
        conflict = _first_ready_dub_overlap(candidate_clips, other_clips)
        if conflict is not None:
            candidate_start_ms = min(
                int(clip.get("start_ms") or 0) for clip in candidate_clips
            )
            other_by_id = {
                str(clip.get("clip_id") or ""): clip for clip in other_clips
            }
            conflicting_clip = other_by_id.get(conflict[1])
            if (
                conflicting_clip is not None
                and int(conflicting_clip.get("start_ms") or 0)
                <= candidate_start_ms
            ):
                candidate_clips = _fit_leading_safety_margin(
                    candidate_clips,
                    frozen.audio,
                    earliest_start_ms=int(conflicting_clip.get("end_ms") or 0),
                )
                fitted_by_id = {
                    str(clip.get("clip_id") or ""): clip
                    for clip in candidate_clips
                }
                working = working.model_copy(update={"timeline_clips": [
                    fitted_by_id.get(str(clip.get("clip_id") or ""), clip)
                    for clip in working.timeline_clips
                ]})
                candidate_ids = set(fitted_by_id)
                other_clips = [
                    dict(clip)
                    for clip in working.timeline_clips
                    if str(clip.get("clip_id") or "") not in candidate_ids
                ]
                conflict = _first_ready_dub_overlap(
                    candidate_clips,
                    other_clips,
                )
        if conflict is not None:
            if first_protected_audio_overlap(candidate_clips, other_clips, frozen.audio):
                return "capacity_recovery_required"
            earliest_start_ms = group.target_start_ms
            latest_end_ms = capacity_latest_end_ms
            candidate_start_ms = min(
                int(clip.get("start_ms") or 0) for clip in candidate_clips
            )
            other_by_id = {
                str(clip.get("clip_id") or ""): clip for clip in other_clips
            }
            conflicting_clip = other_by_id.get(conflict[1])
            if conflicting_clip is not None:
                other_start_ms = int(conflicting_clip.get("start_ms") or 0)
                other_end_ms = int(conflicting_clip.get("end_ms") or 0)
                if other_start_ms <= candidate_start_ms:
                    earliest_start_ms = max(earliest_start_ms, other_end_ms)
                else:
                    latest_end_ms = min(latest_end_ms, other_start_ms)
            if _protected_speech_exceeds_window(
                candidate_clips,
                frozen.audio,
                earliest_start_ms=earliest_start_ms,
                latest_end_ms=latest_end_ms,
                tolerance_ms=0,
            ):
                return "capacity_recovery_required"
            return "retryable_failure"

        candidate_clips = _current_group_working_candidate_clips(
            working,
            group_id=group_id,
            candidate_id=candidate_id,
            target_subtitle_ids=list(group.subtitle_ids),
        )
        source_onset_ms, protected_speech_end_ms = (
            _candidate_word_anchor_and_protected_end(frozen.audio)
        )
        audible_bounds = candidate_audible_timeline_bounds(
            candidate_clips,
            speech_start_ms=source_onset_ms,
            speech_end_ms=protected_speech_end_ms,
        )
        if (
            audible_bounds is None
            or (
                not existing_candidate_clips
                and audible_bounds[0] != group.target_start_ms
            )
        ):
            return "regeneration_required"
        if _protected_speech_exceeds_window(
            candidate_clips,
            frozen.audio,
            earliest_start_ms=group.target_start_ms,
            latest_end_ms=capacity_latest_end_ms,
            # Admission enforces a strict occupied boundary. A sub-frame
            # protected-word overflow is still capacity pressure, not padding
            # that a local retry can remove without cutting speech.
            tolerance_ms=0,
        ):
            # The complete protected words do not physically fit after the safe
            # local edits. Capacity recovery must decide the next action before
            # another provider submission is allowed.
            return "capacity_recovery_required"
        if candidate_clips and max(
            int(clip.get("end_ms") or 0) for clip in candidate_clips
        ) > capacity_latest_end_ms:
            # Remaining overflow is unproven padding/edit state, not evidence
            # that a different generated take would solve the problem.
            return "retryable_failure"
        supporting_clips = _changed_adjacent_supporting_clips(
            draft,
            working,
            group_id=group_id,
        )
        supporting_clip_ids = {
            str(clip.get("clip_id") or "") for clip in supporting_clips
        }
        current_supporting_clips = [
            dict(clip)
            for clip in draft.timeline_clips
            if str(clip.get("clip_id") or "") in supporting_clip_ids
        ]
        try:
            dubbing_candidate_alignment.validate_candidate_clip_coverage(
                clips=candidate_clips,
                words=list(frozen.audio.aligned_words),
                speech_start_ms=None if _verified_retained_projection(frozen, candidate_clips) else frozen.audio.speech_start_ms,
                speech_end_ms=None if _verified_retained_projection(frozen, candidate_clips) else frozen.audio.speech_end_ms,
            )
            reviewed_gaps = reconcile_gap_evidence_with_projection(
                reviewed_gaps,
                candidate_clips,
            )
        except ValueError:
            return "retryable_failure"
        reviewed_audio = frozen.audio.model_copy(update={"gap_evidence": reviewed_gaps})
        source_onset_ms, protected_speech_end_ms = (
            _candidate_word_anchor_and_protected_end(reviewed_audio)
        )
        placement_start_ms, placement_end_ms = candidate_audible_timeline_bounds(
            candidate_clips,
            speech_start_ms=source_onset_ms,
            speech_end_ms=protected_speech_end_ms,
        ) or (
            min(int(item.get("start_ms") or 0) for item in candidate_clips),
            max(int(item.get("end_ms") or 0) for item in candidate_clips),
        )
        reviewed_input = frozen.model_copy(
            update={
                "subjective_reviews": [],
                "audio": reviewed_audio,
                "placement_start_ms": placement_start_ms,
                "placement_end_ms": placement_end_ms,
            }
        )
        report = domain.build_candidate_gap_processing_report(reviewed_input)
        if report.overall_status == "failed":
            return "retryable_failure"
        target_projection_fingerprint = _target_owned_projection_fingerprint(
            draft,
            target_subtitle_ids=list(group.subtitle_ids),
        )
        staged_projection = _staged_candidate_projection(
            candidate_id=candidate_id,
            target_projection_fingerprint=target_projection_fingerprint,
            clips=candidate_clips,
        )
        audit = dubbing_gap_adjudication.build_semantic_boundary_audit(
            source_revision=reviewed_input.source_revision,
            plan_revision=reviewed_input.plan_revision,
            candidate_id=reviewed_input.candidate_id,
            audio_sha256=str(reviewed_input.audio_sha256 or ""),
            candidate_evidence_fingerprint=domain.candidate_evidence_fingerprint(reviewed_input),
            candidate_clip_projection_fingerprint=(
                staged_projection.candidate_clip_projection_fingerprint
            ),
            expected_spoken_text=reviewed_input.expected_spoken_text,
            aligned_words=list(reviewed_audio.aligned_words),
            gaps=list(reviewed_gaps),
            clips=candidate_clips,
        )
        report = report.model_copy(update={
            "semantic_boundary_audit": audit,
            "staged_candidate_projection": staged_projection,
            "overall_status": "needs_review",
            "recommended_action": "listen_and_review",
        })
        previous = next((item for item in draft.dubbing_production.candidate_reports
                         if item.candidate_id == candidate_id), None)
        previous_audit = previous.semantic_boundary_audit if previous else None
        if previous_audit and previous_audit.status == "accepted" and all(
            getattr(previous_audit, field) == getattr(audit, field)
            for field in (
                "source_revision", "plan_revision", "candidate_id", "audio_sha256",
                "candidate_evidence_fingerprint", "candidate_clip_projection_fingerprint",
            )
        ):
            # A save/recovery retry is not another semantic review. Reuse the
            # exact completed judgment and atomically finish placement.
            self._commit_processed_candidate(
                project_id, group_id=group_id, candidate_id=candidate_id,
                processed_clips=candidate_clips, reviewed_input=reviewed_input,
                report=report.model_copy(update={"semantic_boundary_audit": previous_audit}),
                expected_target_projection_fingerprint=target_projection_fingerprint,
                expected_semantic_boundary_audit=previous_audit,
            )
            return "accepted"
        try:
            self._persist_staged_semantic_candidate(
                project_id,
                group_id=group_id,
                candidate_id=candidate_id,
                reviewed_input=reviewed_input,
                report=report,
                staged_projection=staged_projection,
            )
        except AppException as exc:
            if exc.code in {
                "VIDEO_LOCALIZATION_DUBBING_COMMIT_AUDIO_CHANGED",
                "VIDEO_LOCALIZATION_DUBBING_STAGE_AUDIO_CHANGED",
            }:
                return "retryable_failure"
            raise
        return "needs_semantic_review"

    def refresh_current_candidate_projection(
        self, project_id: str, payload: DubbingCurrentProjectionRequest,
    ) -> DubbingCandidateCqcReport:
        """Recheck actual editorial geometry, never regenerate or rearrange it."""
        def apply(current):
            if current._repository_revision != payload.expected_repository_revision:
                raise AppException(409, "DUBBING_CURRENT_PROJECTION_CHANGED",
                                   "项目已变化，请刷新实际片段后再核对。")
            plan, group, group_index, frozen, _, _, _, audio_hash = self._resolve_current_candidate_result(
                project_id, current, source_revision=payload.source_revision,
                plan_revision=payload.plan_revision, group_id=payload.group_id,
                candidate_id=payload.candidate_id, result_id=payload.result_id,
            )
            clips = [dict(clip) for clip in current.timeline_clips
                     if clip.get("track_id") == "dub"
                     and set(_clip_target_subtitle_ids(clip)) & set(group.subtitle_ids)]
            if (
                not clips or frozen.audio is None or not frozen.audio.aligned_words
                or any(clip.get("candidate_id") != payload.candidate_id
                       or clip.get("dubbing_group_id") not in {None, group.group_id}
                       or clip.get("status") != "ready"
                       or int(clip.get("dub_lane") or 0) != 0
                       or set(_clip_target_subtitle_ids(clip)) != set(group.subtitle_ids)
                       or int(clip["source_start_ms"]) < 0
                       or int(clip["source_end_ms"]) > frozen.audio.duration_ms
                       or int(clip["end_ms"]) - int(clip["start_ms"])
                       != int(clip["source_end_ms"]) - int(clip["source_start_ms"])
                       for clip in clips)
                or candidate_clip_projection_fingerprint(clips)
                != payload.candidate_clip_projection_fingerprint
            ):
                raise AppException(409, "DUBBING_CURRENT_PROJECTION_REQUIRED",
                                   "需要本组唯一、完整且未变化的主轨片段和词级证据；不会自动重新生成。")
            # Ordinary handoff clips need not carry managed-plan metadata.
            # Bind only this evidence copy, after exact candidate/target and
            # caller fingerprint validation; never rewrite the saved clips.
            clips = [{**clip, "dubbing_group_id": group.group_id} for clip in clips]
            hashes = dubbing_media.current_timeline_audio_sha256s(
                project_id, current.model_copy(update={"timeline_clips": clips}),
            )
            if any(hashes.get(str(clip["clip_id"])) != audio_hash for clip in clips):
                raise AppException(409, "DUBBING_CURRENT_PROJECTION_CHANGED", "实际片段的音频已变化。")
            clip_ids = {clip["clip_id"] for clip in clips}
            if _first_ready_dub_overlap(clips, [clip for clip in current.timeline_clips
                                               if clip.get("clip_id") not in clip_ids]):
                raise AppException(422, "DUBBING_CURRENT_PROJECTION_INVALID", "主轨片段发生重叠；保留现有剪辑。")
            ordered = sorted(clips, key=lambda clip: int(clip["start_ms"]))
            if any(int(left["source_end_ms"]) > int(right["source_start_ms"])
                   for left, right in zip(ordered, ordered[1:])):
                raise AppException(422, "DUBBING_CURRENT_PROJECTION_INVALID", "实际保留音频出现重复或倒序；保留现有剪辑。")
            verified_edge_edit = _verified_retained_projection(frozen, clips)
            try:
                dubbing_candidate_alignment.validate_candidate_clip_coverage(
                    clips=clips, words=list(frozen.audio.aligned_words),
                    speech_start_ms=None if verified_edge_edit else frozen.audio.speech_start_ms,
                    speech_end_ms=None if verified_edge_edit else frozen.audio.speech_end_ms,
                )
                gaps = reconcile_gap_evidence_with_projection(list(frozen.audio.gap_evidence), clips)
            except ValueError as exc:
                raise AppException(422, "DUBBING_CURRENT_PROJECTION_INVALID", str(exc)) from exc
            onset, end = candidate_source_speech_bounds(frozen.audio)
            if onset is None or end is None:
                raise AppException(422, "DUBBING_CURRENT_PROJECTION_INVALID", "缺少可核对的发声范围。")
            bounds = candidate_audible_timeline_bounds(clips, speech_start_ms=onset, speech_end_ms=end)
            if bounds is None:
                raise AppException(422, "DUBBING_CURRENT_PROJECTION_INVALID", "实际片段缺少完整发声范围。")
            latest_end = (plan.groups[group_index + 1].target_start_ms
                          if group_index + 1 < len(plan.groups) else group.target_end_ms)
            if _protected_speech_exceeds_window(
                clips, frozen.audio, earliest_start_ms=group.target_start_ms,
                latest_end_ms=latest_end, tolerance_ms=frozen.frame_tolerance_ms,
            ):
                raise AppException(422, "DUBBING_CURRENT_PROJECTION_CAPACITY",
                                   "实际字音超出本组可用窗口；保留现有片段并继续容量恢复。")
            updated = frozen.model_copy(update={
                "audio": frozen.audio.model_copy(update={"gap_evidence": gaps}),
                "placement_start_ms": bounds[0], "placement_end_ms": bounds[1],
                "retained_content_evidence": (
                    frozen.retained_content_evidence if _verified_retained_projection(frozen, clips) else None
                ),
            })
            report = domain.build_candidate_gap_processing_report(updated)
            stage = _staged_candidate_projection(
                candidate_id=payload.candidate_id, clips=clips,
                target_projection_fingerprint=_target_owned_projection_fingerprint(
                    current, target_subtitle_ids=list(group.subtitle_ids)),
            )
            audit = dubbing_gap_adjudication.build_semantic_boundary_audit(
                source_revision=plan.source_revision, plan_revision=plan.plan_revision,
                candidate_id=payload.candidate_id, audio_sha256=audio_hash,
                candidate_evidence_fingerprint=report.evidence_fingerprint,
                candidate_clip_projection_fingerprint=stage.candidate_clip_projection_fingerprint,
                expected_spoken_text=updated.expected_spoken_text,
                aligned_words=list(updated.audio.aligned_words), gaps=gaps, clips=clips,
            )
            previous = next((item for item in current.dubbing_production.candidate_reports
                             if item.candidate_id == payload.candidate_id), None)
            audit = dubbing_gap_adjudication.reuse_unchanged_boundary_reviews(
                previous.semantic_boundary_audit if previous else None, audit,
            )
            report = report.model_copy(update={
                "semantic_boundary_audit": audit, "staged_candidate_projection": stage,
            })
            return current.model_copy(update={"dubbing_production": current.dubbing_production.model_copy(update={
                "candidate_inputs": [updated if item.candidate_id == payload.candidate_id else item
                                     for item in current.dubbing_production.candidate_inputs],
                "candidate_reports": [item for item in current.dubbing_production.candidate_reports
                                      if item.candidate_id != payload.candidate_id] + [report],
            })})

        saved = project_service.update_video_localization_atomic(project_id, apply, intent="content")
        if saved is None:
            raise AppException(404, "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND", "项目不存在。")
        return next(item for item in saved.dubbing_production.candidate_reports
                    if item.candidate_id == payload.candidate_id)

    def read_candidate_semantic_boundary_audit(self, project_id: str, candidate_id: str):
        draft = self._require_current_project(project_id)
        report = next((item for item in draft.dubbing_production.candidate_reports
                       if item.candidate_id == candidate_id), None)
        if report is None or report.semantic_boundary_audit is None:
            raise AppException(404, "DUBBING_SEMANTIC_AUDIT_NOT_FOUND", "候选尚未形成最终断句检查证据。")
        self._assert_current_revision(draft, report.source_revision)
        plan = draft.dubbing_production.active_plan
        if plan is None or plan.plan_revision != report.plan_revision:
            raise AppException(
                409,
                "DUBBING_SEMANTIC_AUDIT_CHANGED",
                "配音计划已经变化，请重新生成当前候选的断句证据。",
            )
        return report.semantic_boundary_audit

    def submit_candidate_semantic_boundary_review(
        self, project_id: str, command: DubbingCandidateReviewCommand,
    ) -> DubbingCandidateCqcReport:
        """Accept one complete Agent disposition without treating it as a user review."""
        draft = self._require_current_project(project_id)
        report = next((item for item in draft.dubbing_production.candidate_reports
                       if item.candidate_id == command.candidate_id), None)
        frozen = next((item for item in draft.dubbing_production.candidate_inputs
                       if item.candidate_id == command.candidate_id), None)
        audit = report.semantic_boundary_audit if report is not None else None
        if report is None or frozen is None or audit is None:
            raise AppException(409, "DUBBING_SEMANTIC_AUDIT_REQUIRED", "候选尚未形成可提交的最终断句检查。")
        if (
            command.source_revision != audit.source_revision
            or command.plan_revision != audit.plan_revision
            or command.audio_sha256 != audit.audio_sha256
            or command.candidate_clip_projection_fingerprint
            != audit.candidate_clip_projection_fingerprint
        ):
            raise AppException(409, "DUBBING_SEMANTIC_AUDIT_CHANGED", "候选音频、版本或最终裁切已经变化，请重新读取断句检查。")
        stage = report.staged_candidate_projection if report is not None else None
        plan = draft.dubbing_production.active_plan
        group = next(
            (item for item in (plan.groups if plan is not None else [])
             if item.group_id == report.group_id),
            None,
        )
        stage_clips = (
            _staged_projection_runtime_clips(draft, stage)
            if stage is not None else []
        )
        owned_clips = [
            dict(clip) for clip in draft.timeline_clips
            if group is not None
            and clip.get("track_id") == "dub"
            and not clip.get("manual_history_copy")
            and bool(set(_clip_target_subtitle_ids(clip)) & set(group.subtitle_ids))
        ]
        already_adopted = bool(
            stage is not None
            and owned_clips
            and all(clip.get("candidate_id") == command.candidate_id
                    and clip.get("status") == "ready" for clip in owned_clips)
            and candidate_clip_projection_fingerprint(owned_clips)
            == stage.candidate_clip_projection_fingerprint
        )
        if (
            command.candidate_evidence_fingerprint
            != audit.candidate_evidence_fingerprint
            and not already_adopted
        ):
            raise AppException(
                409,
                "DUBBING_SEMANTIC_AUDIT_CHANGED",
                "候选音频、版本或最终裁切已经变化，请重新读取断句检查。",
            )
        expected_target_fingerprint = (
            _target_owned_projection_fingerprint(
                draft, target_subtitle_ids=list(group.subtitle_ids),
            ) if already_adopted else (
                stage.target_projection_fingerprint if stage is not None else None
            )
        )
        current_audio_hashes = dubbing_media.current_timeline_audio_sha256s(
            project_id,
            draft.model_copy(update={
                "timeline_clips": owned_clips if already_adopted else stage_clips,
            }),
        )
        if (
            plan is None
            or group is None
            or plan.source_revision != audit.source_revision
            or plan.plan_revision != audit.plan_revision
            or stage is None
            or stage.candidate_id != command.candidate_id
            or stage.candidate_clip_projection_fingerprint
            != audit.candidate_clip_projection_fingerprint
            or (audit.status == "accepted" and not already_adopted)
            or _target_owned_projection_fingerprint(
                draft,
                target_subtitle_ids=list(group.subtitle_ids),
            ) != expected_target_fingerprint
            or not stage_clips
            or any(
                current_audio_hashes.get(str(clip.get("clip_id") or ""))
                != audit.audio_sha256
                for clip in stage_clips
            )
        ):
            raise AppException(409, "DUBBING_SEMANTIC_AUDIT_CHANGED", "候选音频、版本或最终裁切已经变化，请重新读取断句检查。")
        decisions = {item.boundary_id: item for item in audit.agent_reviews}
        decisions.update({item.boundary_id: item for item in command.semantic_boundary_reviews})
        evidence = {item.boundary_id: item for item in audit.boundaries}
        if set(decisions) != set(evidence):
            raise AppException(422, "DUBBING_SEMANTIC_BOUNDARY_COVERAGE", "Agent 必须处置最终候选的每个相邻字词边界。")
        if (
            already_adopted
            and list(command.semantic_boundary_reviews) == list(audit.agent_reviews)
        ):
            return report
        recovery = [item for item in decisions.values() if item.disposition == "recover"]
        next_audit = audit.model_copy(update={
            "status": (
                "pending_agent" if any(item.disposition == "uncertain" for item in decisions.values())
                else "recovery_required" if recovery else "accepted"
            ),
            "agent_reviews": [decisions[item.boundary_id] for item in audit.boundaries],
        })
        next_report = report.model_copy(update={
            "semantic_boundary_audit": next_audit,
            "overall_status": "failed" if recovery else report.overall_status,
            "recommended_action": "regenerate" if recovery else report.recommended_action,
        })

        if not already_adopted and not recovery and next_audit.status == "accepted":
            try:
                self._commit_processed_candidate(
                    project_id,
                    group_id=group.group_id,
                    candidate_id=command.candidate_id,
                    processed_clips=stage_clips,
                    reviewed_input=frozen,
                    report=next_report,
                    expected_target_projection_fingerprint=(
                        stage.target_projection_fingerprint
                    ),
                    expected_semantic_boundary_audit=audit,
                )
            except AppException:
                raise
            return next_report

        def apply(current):
            current_plan = current.dubbing_production.active_plan
            current_report = next(
                (
                    item
                    for item in current.dubbing_production.candidate_reports
                    if item.candidate_id == command.candidate_id
                ),
                None,
            )
            current_stage = (
                current_report.staged_candidate_projection
                if current_report is not None else None
            )
            current_clips = (
                [dict(clip) for clip in current.timeline_clips
                 if str(clip.get("clip_id") or "")
                 in {str(owned.get("clip_id") or "") for owned in owned_clips}]
                if already_adopted else
                _staged_projection_runtime_clips(current, current_stage)
                if current_stage is not None else []
            )
            current_hashes = dubbing_media.current_timeline_audio_sha256s(
                project_id,
                current.model_copy(update={"timeline_clips": current_clips}),
            )
            if (
                current_plan is None
                or current_plan.source_revision != audit.source_revision
                or current_plan.plan_revision != audit.plan_revision
                or current_report is None
                or current_report.semantic_boundary_audit != audit
                or current_stage != stage
                or not current_clips
                or _target_owned_projection_fingerprint(
                    current,
                    target_subtitle_ids=list(group.subtitle_ids),
                ) != expected_target_fingerprint
                or any(
                    current_hashes.get(str(clip.get("clip_id") or ""))
                    != audit.audio_sha256
                    for clip in current_clips
                )
            ):
                raise AppException(
                    409,
                    "DUBBING_SEMANTIC_AUDIT_CHANGED",
                    "候选音频、版本或最终裁切已经变化，请重新读取断句检查。",
                )
            reports = [item for item in current.dubbing_production.candidate_reports
                       if item.candidate_id != command.candidate_id]
            reports.append(next_report)
            return current.model_copy(update={"dubbing_production": current.dubbing_production.model_copy(
                update={"candidate_reports": reports})})

        saved = project_service.update_video_localization_atomic(project_id, apply, intent="content")
        if saved is None:
            raise AppException(404, "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND", "视频本土化项目不存在。")
        return next_report

    @staticmethod
    def _reconcile_accepted_projection_gap_evidence(
        project_id: str,
        *,
        group_id: str,
        candidate_id: str,
        expected_audit,
        expected_stage: DubbingStagedCandidateProjection,
    ) -> None:
        """Keep accepted gas decisions bound to their accepted projection."""

        def apply(current):
            plan = current.dubbing_production.active_plan
            report = next(
                (
                    item
                    for item in current.dubbing_production.candidate_reports
                    if item.candidate_id == candidate_id
                ),
                None,
            )
            frozen = next(
                (
                    item
                    for item in current.dubbing_production.candidate_inputs
                    if item.candidate_id == candidate_id
                    and item.group_id == group_id
                ),
                None,
            )
            group = next(
                (
                    item
                    for item in (plan.groups if plan is not None else [])
                    if item.group_id == group_id
                ),
                None,
            )
            if (
                plan is None
                or group is None
                or report is None
                or frozen is None
                or frozen.audio is None
                or report.semantic_boundary_audit != expected_audit
                or report.staged_candidate_projection != expected_stage
                or expected_audit.status != "accepted"
                or plan.source_revision != expected_audit.source_revision
                or plan.plan_revision != expected_audit.plan_revision
            ):
                raise AppException(
                    409,
                    "DUBBING_SEMANTIC_AUDIT_CHANGED",
                    "已采纳候选的断句证据已经变化，请重新读取后再继续。",
                )
            clips = _current_group_working_candidate_clips(
                current,
                group_id=group_id,
                candidate_id=candidate_id,
                target_subtitle_ids=list(group.subtitle_ids),
            )
            if (
                not clips
                or candidate_clip_projection_fingerprint(clips)
                != expected_stage.candidate_clip_projection_fingerprint
            ):
                raise AppException(
                    409,
                    "DUBBING_SEMANTIC_AUDIT_CHANGED",
                    "已采纳候选的实际裁切已经变化，请重新读取后再继续。",
                )
            reconciled_gaps = reconcile_gap_evidence_with_projection(
                list(frozen.audio.gap_evidence), clips,
            )
            if reconciled_gaps == list(frozen.audio.gap_evidence):
                return current
            next_audio = frozen.audio.model_copy(update={
                "gap_evidence": reconciled_gaps,
            })
            next_frozen = frozen.model_copy(update={"audio": next_audio})
            evidence_fingerprint = domain.candidate_evidence_fingerprint(next_frozen)
            next_audit = expected_audit.model_copy(update={
                "candidate_evidence_fingerprint": evidence_fingerprint,
                "audio_gap_evidence": reconciled_gaps,
            })
            next_report = report.model_copy(update={
                "audio_evidence": next_audio,
                "evidence_fingerprint": evidence_fingerprint,
                "semantic_boundary_audit": next_audit,
            })
            return current.model_copy(update={
                "dubbing_production": current.dubbing_production.model_copy(
                    update={
                        "candidate_inputs": [
                            next_frozen if item.candidate_id == candidate_id else item
                            for item in current.dubbing_production.candidate_inputs
                        ],
                        "candidate_reports": [
                            next_report if item.candidate_id == candidate_id else item
                            for item in current.dubbing_production.candidate_reports
                        ],
                    }
                )
            })

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )

    @staticmethod
    def _persist_staged_semantic_candidate(
        project_id: str,
        *,
        group_id: str,
        candidate_id: str,
        reviewed_input: DubbingCandidateCqcInput,
        report: DubbingCandidateCqcReport,
        staged_projection: DubbingStagedCandidateProjection,
    ):
        """Persist review evidence without replacing any live timeline clip."""

        def apply(current):
            plan = current.dubbing_production.active_plan
            group = next(
                (item for item in (plan.groups if plan is not None else [])
                 if item.group_id == group_id),
                None,
            )
            if (
                plan is None
                or group is None
                or plan.source_revision != reviewed_input.source_revision
                or plan.plan_revision != reviewed_input.plan_revision
                or _target_owned_projection_fingerprint(
                    current,
                    target_subtitle_ids=list(group.subtitle_ids),
                ) != staged_projection.target_projection_fingerprint
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_STAGE_CHANGED",
                    "候选检查期间计划或当前时间线已变化，请重新生成断句证据。",
                )
            stage_clips = _staged_projection_runtime_clips(
                current,
                staged_projection,
            )
            current_hashes = dubbing_media.current_timeline_audio_sha256s(
                project_id,
                current.model_copy(update={"timeline_clips": stage_clips}),
            )
            if (
                not reviewed_input.audio_sha256
                or len(current_hashes) != len(stage_clips)
                or any(value != reviewed_input.audio_sha256 for value in current_hashes.values())
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_STAGE_AUDIO_CHANGED",
                    "候选音频在断句检查前发生变化，请重新检查。",
                )
            reports = [
                item for item in current.dubbing_production.candidate_reports
                if item.candidate_id != candidate_id
            ]
            reports.append(report)
            inputs = [
                item if item.candidate_id != candidate_id else reviewed_input
                for item in current.dubbing_production.candidate_inputs
            ]
            return current.model_copy(
                update={
                    "dubbing_production": current.dubbing_production.model_copy(
                        update={
                            "candidate_inputs": inputs,
                            "candidate_reports": reports,
                            # This staged candidate supersedes the prior
                            # close-out failure for this same plan group.  It
                            # is not accepted yet, but the runner must expose
                            # its pending Agent handoff instead of treating an
                            # obsolete failure as terminal.
                            "group_failures": [
                                failure
                                for failure in current.dubbing_production.group_failures
                                if not (
                                    failure.source_revision == plan.source_revision
                                    and failure.plan_revision == plan.plan_revision
                                    and failure.group_id == group_id
                                )
                            ],
                        }
                    )
                }
            )

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return updated

    @staticmethod
    def _commit_processed_candidate(
        project_id: str,
        *,
        group_id: str,
        candidate_id: str,
        processed_clips: list[dict],
        reviewed_input: DubbingCandidateCqcInput,
        report: DubbingCandidateCqcReport,
        expected_target_projection_fingerprint: str,
        supporting_clips: list[dict] | None = None,
        expected_supporting_projection_fingerprint: str | None = None,
        expected_semantic_boundary_audit=None,
    ):
        """Make the processed take current without a second review state machine.

        Gap analysis remains an internal input to deterministic edits. The
        timeline itself is the selected-result authority: one target set has
        one current take, while older takes remain in immutable TTS history.
        """

        def apply(current):
            plan = current.dubbing_production.active_plan
            group = next(
                (item for item in (plan.groups if plan is not None else []) if item.group_id == group_id),
                None,
            )
            if plan is None or group is None:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_COMMIT_STALE",
                    "配音分组已经变化，请重新处理当前片段。",
                )
            if expected_semantic_boundary_audit is not None:
                current_report = next(
                    (
                        item
                        for item in current.dubbing_production.candidate_reports
                        if item.candidate_id == candidate_id
                    ),
                    None,
                )
                if (
                    current_report is None
                    or current_report.semantic_boundary_audit
                    != expected_semantic_boundary_audit
                ):
                    raise AppException(
                        409,
                        "DUBBING_SEMANTIC_AUDIT_CHANGED",
                        "候选断句处置已变化，请重新读取后再提交。",
                    )
            expected_targets = set(group.subtitle_ids)
            if (
                _target_owned_projection_fingerprint(
                    current,
                    target_subtitle_ids=list(group.subtitle_ids),
                )
                != expected_target_projection_fingerprint
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_COMMIT_TARGET_CHANGED",
                    "当前片段在处理期间被修改，请基于最新时间线重新处理。",
                )
            support_replacements = {
                str(clip.get("clip_id") or ""): dict(clip)
                for clip in (supporting_clips or [])
            }
            if "" in support_replacements:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_COMMIT_SUPPORT_INVALID",
                    "相邻片段身份无效，已保留原来的时间线结果。",
                )
            current_supporting = [
                dict(clip)
                for clip in current.timeline_clips
                if str(clip.get("clip_id") or "") in support_replacements
            ]
            if support_replacements and (
                len(current_supporting) != len(support_replacements)
                or _exact_clip_projection_fingerprint(current_supporting)
                != (expected_supporting_projection_fingerprint or "")
                or any(
                    not _is_timeline_position_only_replacement(
                        original,
                        support_replacements[str(original.get("clip_id") or "")],
                    )
                    for original in current_supporting
                )
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_COMMIT_SUPPORT_CHANGED",
                    "相邻片段在处理期间发生变化，已保留原来的时间线结果。",
                )
            candidate_clips = [dict(item) for item in processed_clips]
            coverage = {subtitle_id for clip in candidate_clips for subtitle_id in _clip_target_subtitle_ids(clip)}
            if coverage != expected_targets or any(
                clip.get("status") != "ready" or not clip.get("audio_path") for clip in candidate_clips
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_COMMIT_NOT_READY",
                    "当前声音尚未形成完整、可播放的时间轴片段。",
                )
            assert reviewed_input.audio is not None
            try:
                dubbing_candidate_alignment.validate_candidate_clip_coverage(
                    clips=candidate_clips,
                    words=list(reviewed_input.audio.aligned_words),
                    speech_start_ms=None if _verified_retained_projection(reviewed_input, candidate_clips) else reviewed_input.audio.speech_start_ms,
                    speech_end_ms=None if _verified_retained_projection(reviewed_input, candidate_clips) else reviewed_input.audio.speech_end_ms,
                )
            except ValueError as exc:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_COMMIT_AUDIO_CROPPED",
                    str(exc),
                ) from exc
            stored_candidate = next(
                (
                    dict(item)
                    for item in current.generated_candidates
                    if candidate_id
                    in {
                        str(item.get("candidate_id") or ""),
                        str(item.get("result_id") or ""),
                    }
                ),
                None,
            )
            if stored_candidate is None:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_COMMIT_CANDIDATE_MISSING",
                    "生成记录已经变化，请重新处理当前片段。",
                )
            result_id = str(stored_candidate.get("result_id") or candidate_id)
            generation_task_id = str(stored_candidate.get("task_id") or "") or None
            committed_candidate_clips: list[dict] = []
            for clip in candidate_clips:
                clip.update(
                    {
                        "dub_lane": 0,
                        "dubbing_group_id": group_id,
                        "target_start_ms": group.target_start_ms,
                        "target_end_ms": group.target_end_ms,
                        "generation_identity": result_id,
                        "intentional_overlap": False,
                        "tts_target_binding_status": "current",
                    }
                )
                for legacy_field in (
                    "cqc_status",
                    "cqc_report",
                    "cqc_report_version",
                    "timeline_edit_gate",
                    "selected",
                    "accepted",
                ):
                    clip.pop(legacy_field, None)
                committed_candidate_clips.append(clip)

            committed_ids = {
                str(clip.get("clip_id") or "")
                for clip in committed_candidate_clips
            }
            changed_ids = committed_ids | set(support_replacements)
            unrelated_ids = {
                str(raw.get("clip_id") or "")
                for raw in current.timeline_clips
                if str(raw.get("clip_id") or "") not in support_replacements
                and not (
                    raw.get("track_id") == "dub"
                    and not raw.get("manual_history_copy")
                    and bool(set(_clip_target_subtitle_ids(raw)) & expected_targets)
                )
            }
            if "" in committed_ids or committed_ids & unrelated_ids:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_COMMIT_CLIP_ID_CONFLICT",
                    "当前片段身份与其他时间线内容冲突，请重新处理。",
                )
            committed: list[dict] = []
            inserted = False
            for raw in current.timeline_clips:
                clip = dict(raw)
                clip_id = str(clip.get("clip_id") or "")
                if clip_id in support_replacements:
                    committed.append(dict(support_replacements[clip_id]))
                    continue
                clip_targets = set(_clip_target_subtitle_ids(clip))
                if (
                    clip.get("track_id") == "dub"
                    and not clip.get("manual_history_copy")
                    and bool(clip_targets & expected_targets)
                ):
                    if not inserted:
                        committed.extend(committed_candidate_clips)
                        inserted = True
                    continue
                committed.append(clip)
            if not inserted:
                committed.extend(committed_candidate_clips)

            conflict = _first_ready_dub_overlap(
                [
                    clip
                    for clip in committed
                    if str(clip.get("clip_id") or "") in changed_ids
                ],
                [
                    clip
                    for clip in committed
                    if str(clip.get("clip_id") or "") not in changed_ids
                ],
            )
            if conflict is not None:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_COMMIT_TIMELINE_CONFLICT",
                    "新声音会与相邻配音冲突，已保留原来的时间线结果。",
                    {
                        "entity_ids": list(conflict),
                        "recommended_action": "move_or_trim_clip",
                    },
                )

            generation_attempt = _candidate_generation_attempt(
                current,
                generation_task_id=generation_task_id,
                result_id=result_id,
            )
            if generation_attempt > 1:
                before_payload = domain.build_current_timeline_audit_input(
                    current,
                    current_timeline_revision=domain.dubbing_timeline_projection_revision(
                        current
                    ),
                    phase="timeline",
                )
                proposed_draft = current.model_copy(
                    update={"timeline_clips": committed}
                )
                after_payload = domain.build_current_timeline_audit_input(
                    proposed_draft,
                    current_timeline_revision=domain.dubbing_timeline_projection_revision(
                        proposed_draft
                    ),
                    phase="timeline",
                )
                before_underfill = domain.continuous_speech_underfill_assessments(
                    before_payload,
                    eligible_left_group_ids={group_id},
                    eligible_left_unit_ids=set(group.unit_ids),
                    main_lane_only=True,
                )
                after_underfill = domain.continuous_speech_underfill_assessments(
                    after_payload,
                    eligible_left_group_ids={group_id},
                    eligible_left_unit_ids=set(group.unit_ids),
                    main_lane_only=True,
                )
                if _replacement_worsens_continuous_boundary(
                    before_underfill,
                    after_underfill,
                    tolerance_ms=after_payload.frame_tolerance_ms,
                ):
                    conflicted = after_underfill[0]
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_COMMIT_TIMELINE_CONFLICT",
                        "新声音让相邻空档变得更大，已保留原来的时间线结果。",
                        {
                            "entity_ids": [
                                conflicted.left_clip_id,
                                conflicted.right_clip_id,
                            ],
                            "recommended_action": "keep_previous_take",
                        },
                    )

            # Verify each unique media file once at the actual adoption boundary.
            # A stale saved report must never certify replaced or deleted bytes.
            current_hashes = dubbing_media.current_timeline_audio_sha256s(
                project_id, current.model_copy(update={"timeline_clips": committed_candidate_clips}),
            )
            if (not reviewed_input.audio_sha256 or len(current_hashes) != len(committed_candidate_clips)
                    or any(value != reviewed_input.audio_sha256 for value in current_hashes.values())):
                raise AppException(409, "VIDEO_LOCALIZATION_DUBBING_COMMIT_AUDIO_CHANGED",
                                   "音频文件在检查后发生变化或已不可读；原时间线保持不变，请重新检查。")

            reports = [
                item for item in current.dubbing_production.candidate_reports if item.candidate_id != candidate_id
            ]
            reports.append(report)
            inputs = [
                item if item.candidate_id != candidate_id else reviewed_input
                for item in current.dubbing_production.candidate_inputs
            ]

            state = current.dubbing_production.model_copy(
                update={
                    "group_reviews": [
                        review
                        for review in current.dubbing_production.group_reviews
                        if not (
                            review.source_revision == plan.source_revision
                            and review.plan_revision == plan.plan_revision
                            and review.group_id == group_id
                        )
                    ],
                    "candidate_inputs": inputs,
                    "candidate_reports": reports,
                    "group_failures": [
                        failure
                        for failure in current.dubbing_production.group_failures
                        if not (
                            failure.source_revision == plan.source_revision
                            and failure.plan_revision == plan.plan_revision
                            and failure.group_id == group_id
                        )
                    ],
                    "latest_timeline_audit": None,
                }
            )
            group_candidate_ids = {
                item.candidate_id for item in current.dubbing_production.candidate_inputs if item.group_id == group_id
            }
            candidates = []
            for raw in current.generated_candidates:
                candidate = dict(raw)
                identities = {
                    str(candidate.get("candidate_id") or ""),
                    str(candidate.get("result_id") or ""),
                }
                if identities & group_candidate_ids:
                    for legacy_field in (
                        "selected",
                        "accepted",
                        "cqc_status",
                        "cqc_report",
                        "cqc_report_version",
                    ):
                        candidate.pop(legacy_field, None)
                candidates.append(candidate)
            committed_draft = current.model_copy(
                update={
                    "timeline_clips": committed,
                    "generated_candidates": candidates,
                    "dubbing_production": state,
                }
            )
            completed_draft = project_service.with_completed_tts_placement(
                committed_draft,
                generation_task_id=generation_task_id,
                result_id=result_id,
                timeline_clip_id=str(committed_candidate_clips[0].get("clip_id") or ""),
            )
            return project_service.with_superseded_group_tts_placements_closed(
                completed_draft, group_id=group_id,
                selected_generation_task_id=generation_task_id,
                selected_result_id=result_id,
            )

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return updated

    @staticmethod
    def _trim_automatic_outer_gaps(
        project_id: str,
        *,
        candidate_id: str,
        frozen,
        reviewed_gaps,
        padding_ms: int = 80,
    ):
        """Apply evidence-backed outer trims without expanding existing edits."""

        def apply(current):
            return _with_trimmed_automatic_outer_gaps(
                current,
                candidate_id=candidate_id,
                frozen=frozen,
                reviewed_gaps=reviewed_gaps,
                padding_ms=padding_ms,
            )

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return updated

    def recover_and_finalize_generated_group(
        self,
        project_id: str,
        group_id: str,
        candidate_or_result_ids: list[str],
        *,
        review_mode: DubbingProductionReviewMode = "full",
    ) -> Literal[
        "accepted",
        "needs_semantic_review",
        "capacity_recovery_required",
        "regeneration_required",
        "retryable_failure",
    ] | None:
        """Resume a durable result through the same current group finisher."""

        draft = self._require_current_project(project_id)
        plan = draft.dubbing_production.active_plan
        if plan is None or not any(item.group_id == group_id for item in plan.groups):
            return None

        input_ids = [
            item.candidate_id
            for item in draft.dubbing_production.candidate_inputs
            if item.group_id == group_id
            and item.source_revision == plan.source_revision
            and item.plan_revision == plan.plan_revision
        ]
        existing_ids = _existing_formal_candidate_ids_for_group(
            draft,
            target_subtitle_ids=list(
                next(
                    item.subtitle_ids
                    for item in plan.groups
                    if item.group_id == group_id
                )
            ),
        )
        identities = list(
            dict.fromkeys(
                [
                    *candidate_or_result_ids,
                    *reversed(input_ids),
                    *reversed(existing_ids),
                ]
            )
        )
        # Caller-selected/current results precede older saved takes. If none
        # can be accepted, preserve the first current result's recovery need:
        # an older unusable take must not override the current result's recovery.
        preferred_disposition = None
        for identity in identities:
            try:
                report = self.resync_candidate_automatic_cqc(project_id, identity)
            except AppException as exc:
                if exc.code == "TTS_CONTENT_AUDIO_UNAVAILABLE":
                    preferred_disposition = preferred_disposition or "retryable_failure"
                    continue
                try:
                    report = self.resync_history_candidate_automatic_cqc(
                        project_id,
                        identity,
                    )
                except AppException as exc:
                    if exc.code == "TTS_CONTENT_AUDIO_UNAVAILABLE":
                        preferred_disposition = preferred_disposition or "retryable_failure"
                    # Candidate, result and task identities share this recovery
                    # list. A stale task id is not itself a group failure; keep
                    # searching until a durable candidate/history result matches.
                    continue
            if report is None or report.group_id != group_id:
                continue
            try:
                if review_mode == "full":
                    disposition = self.finalize_generated_candidate(
                        project_id,
                        report.candidate_id,
                        group_id,
                    )
                else:
                    disposition = self.finalize_generated_candidate(
                        project_id,
                        report.candidate_id,
                        group_id,
                        review_mode=review_mode,
                    )
                if disposition == "accepted":
                    return disposition
                if disposition == "needs_semantic_review":
                    return disposition
                preferred_disposition = preferred_disposition or disposition
            except AppException as exc:
                if exc.code == "VIDEO_LOCALIZATION_DUBBING_COMMIT_TIMELINE_CONFLICT":
                    preferred_disposition = preferred_disposition or "regeneration_required"
                    continue
                raise
        reports = [
            item
            for item in draft.dubbing_production.candidate_reports
            if item.group_id == group_id
            and item.source_revision == plan.source_revision
            and item.plan_revision == plan.plan_revision
        ]
        for report in reversed(reports):
            try:
                if review_mode == "full":
                    disposition = self.finalize_generated_candidate(
                        project_id,
                        report.candidate_id,
                        group_id,
                    )
                else:
                    disposition = self.finalize_generated_candidate(
                        project_id,
                        report.candidate_id,
                        group_id,
                        review_mode=review_mode,
                    )
                if disposition == "accepted":
                    return disposition
                if disposition == "needs_semantic_review":
                    return disposition
                preferred_disposition = preferred_disposition or disposition
            except AppException as exc:
                if exc.code == "VIDEO_LOCALIZATION_DUBBING_COMMIT_TIMELINE_CONFLICT":
                    preferred_disposition = preferred_disposition or "regeneration_required"
                    continue
                if exc.code != "VIDEO_LOCALIZATION_CANDIDATE_NOT_FOUND":
                    raise
                # A report can outlive a deleted/replaced candidate. It is
                # evidence to skip, not a reason to block a newer valid result.
                continue
        return preferred_disposition

    @staticmethod
    def _build_automatic_gap_split_request(
        draft,
        *,
        candidate_id: str,
        group,
        frozen,
        reviewed_gaps,
        source_pauses,
    ) -> DubbingTimelineSplitRequest | None:
        clips = [
            dict(item)
            for item in draft.timeline_clips
            if str(item.get("candidate_id") or item.get("result_id") or "")
            == candidate_id
        ]
        if len(clips) != 1 or frozen.audio is None or not frozen.audio_sha256:
            return None
        clip = clips[0]
        source_start = int(clip.get("source_start_ms") or 0)
        source_end = int(
            clip.get("source_end_ms")
            or source_start
            + int(clip.get("end_ms") or 0)
            - int(clip.get("start_ms") or 0)
        )
        words = list(frozen.audio.aligned_words)
        source_pause_by_pair = dubbing_gap_adjudication.map_source_pause_rhythm(
            frozen.expected_spoken_text,
            words,
            list(source_pauses),
        )
        boundaries: list[tuple[int, int]] = []
        for gap in reviewed_gaps:
            if gap.kind != "internal" or gap.safe_edit_boundary is not True:
                continue
            if gap.edit_decision == "remove":
                cut = dubbing_candidate_alignment.safe_internal_gap_cut(
                    gap,
                    words,
                )
                if cut is not None:
                    boundaries.append(cut)
                continue
            if gap.edit_decision != "retain" or gap.semantic_role != "semantic_boundary":
                continue
            left, right = dubbing_gap_adjudication.adjacent_words(gap, words)
            source_pause = (
                source_pause_by_pair.get((left.word_id, right.word_id))
                if left is not None and right is not None
                else None
            )
            if (
                source_pause is not None
                and source_pause.duration_ms >= gap.duration_ms + 120
            ):
                # Keep the candidate's existing breath, but split only inside
                # the alignment-proven safe core. Energy/VAD gap edges can
                # overlap a word by a few frames, so using gap.end_ms directly
                # can either drop that word or fail the 80 ms safety check.
                safe_core = dubbing_candidate_alignment.safe_internal_gap_cut(
                    gap,
                    words,
                )
                if safe_core is not None:
                    split_anchor = round(sum(safe_core) / 2)
                    boundaries.append((split_anchor, split_anchor))

        cuts = sorted(
            (
                max(source_start, cut_start),
                min(source_end, next_start),
            )
            for cut_start, next_start in boundaries
            if source_start < cut_start <= next_start < source_end
        )
        if not cuts:
            return None
        ranges = []
        cursor = source_start
        for cut_start, next_start in cuts:
            if cut_start < cursor:
                continue
            if cut_start > cursor:
                ranges.append((cursor, cut_start))
            cursor = max(cursor, next_start)
        if cursor < source_end:
            ranges.append((cursor, source_end))
        slices = []
        for range_start, range_end in ranges:
            included = [
                word
                for word in words
                if word.end_ms > word.start_ms
                and word.start_ms >= range_start
                and word.end_ms <= range_end
            ]
            if not included:
                return None
            if included[-1].end_ms <= included[0].start_ms:
                return None
            slices.append(
                DubbingTimelineAlignedSlice(
                    target_subtitle_ids=list(group.subtitle_ids),
                    source_start_ms=range_start,
                    source_end_ms=range_end,
                    speech_start_ms=included[0].start_ms,
                    speech_end_ms=included[-1].end_ms,
                    alignment_word_ids=[word.word_id for word in included],
                )
            )
        if len(slices) < 2:
            return None
        return DubbingTimelineSplitRequest(
            source_revision=frozen.source_revision,
            plan_revision=frozen.plan_revision,
            timeline_revision=domain.dubbing_timeline_projection_revision(draft),
            commands=[
                DubbingTimelineClipSplitCommand(
                    clip_id=str(clip.get("clip_id") or ""),
                    candidate_id=candidate_id,
                    audio_sha256=frozen.audio_sha256,
                    slices=slices,
                )
            ],
        )

    @staticmethod
    def _build_automatic_timeline_gate(
        *,
        plan,
        frozen,
        report,
        clips: list[dict],
        gaps,
        status: Literal["passed", "needs_review"],
    ) -> DubbingTimelineEditGate:
        audio = report.audio_evidence
        if (
            audio is None
            or audio.speaking_rate_ratio is None
            or not audio.aligned_words
            or not clips
        ):
            raise ValueError("候选缺少可提交的气口门禁证据。")
        ordered = sorted(
            clips,
            key=lambda item: (
                int(item.get("source_start_ms") or 0),
                int(item.get("start_ms") or 0),
            ),
        )
        first, last = ordered[0], ordered[-1]
        actual_start = int(first.get("start_ms") or 0) + (
            int(audio.speech_start_ms or 0)
            - int(first.get("source_start_ms") or 0)
        )
        actual_end = int(last.get("start_ms") or 0) + (
            int(audio.speech_end_ms or 0)
            - int(last.get("source_start_ms") or 0)
        )
        return DubbingTimelineEditGate(
            source_revision=plan.source_revision,
            plan_revision=plan.plan_revision,
            candidate_id=report.candidate_id,
            cqc_report_fingerprint=report.evidence_fingerprint,
            candidate_clip_projection_fingerprint=(
                candidate_clip_projection_fingerprint(clips)
            ),
            status=status,
            actual_speech_start_delta_ms=actual_start - frozen.target_start_ms,
            actual_speech_end_delta_ms=actual_end - frozen.target_end_ms,
            speaking_rate_ratio=audio.speaking_rate_ratio,
            content_speed_exception_applied=bool(
                audio.content_speed_exception_reason
                and audio.content_speed_exception_evidence_ids
            ),
            alignment_word_ids=[word.word_id for word in audio.aligned_words],
            gap_decisions=list(gaps),
        )

    def rebalance_current_timeline(self, project_id: str):
        """Persist one evidence-backed editing pass over the current dub lane."""

        draft = self._require_current_project(project_id)
        plan = draft.dubbing_production.active_plan
        if plan is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_REQUIRED",
                "请先基于当前字幕和画面完成配音生成计划。",
            )
        self._assert_current_revision(draft, plan.source_revision)
        expected_revision = plan.source_revision
        expected_plan_revision = plan.plan_revision

        def apply(current):
            current_plan = current.dubbing_production.active_plan
            if (
                current_plan is None
                or current_plan.source_revision != expected_revision
                or current_plan.plan_revision != expected_plan_revision
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
                    "配音计划已经变化，请基于当前计划重新剪辑。",
                )
            snapshot = domain.build_project_snapshot(current)
            timeline_clips = domain.rebalance_planned_timeline_clips(
                timeline_clips=[dict(item) for item in current.timeline_clips],
                groups=current_plan.groups,
                boundaries=snapshot.boundaries,
            )
            if timeline_clips == current.timeline_clips:
                return (
                    project_service
                    .with_current_timeline_tts_placements_completed(current)
                )
            before_report = _audit_candidate_timeline(project_id, current)
            state = current.dubbing_production.model_copy(
                update={"latest_timeline_audit": None}
            )
            original_clips = [dict(item) for item in current.timeline_clips]
            proposed_clips = (
                dubbing_production_run.keep_only_uniform_candidate_rebalance(
                    original_timeline_clips=original_clips,
                    proposed_timeline_clips=[dict(item) for item in timeline_clips],
                )
            )
            clips_by_id = {
                str(item.get("clip_id") or ""): item
                for item in [*original_clips, *proposed_clips]
            }
            reverted_group_ids: set[str] = set()
            latest_candidate = current
            latest_report = before_report
            new_findings = []
            for _ in range(len(current_plan.groups) + 1):
                conservative_clips = domain.restore_rebalance_groups(
                    original_timeline_clips=original_clips,
                    proposed_timeline_clips=proposed_clips,
                    group_ids=reverted_group_ids,
                )
                latest_candidate = current.model_copy(
                    update={
                        "timeline_clips": conservative_clips,
                        "dubbing_production": state.model_copy(
                            update={
                                "existing_formal_acceptances": (
                                    dubbing_production_run.rebase_terminal_existing_acceptances(
                                        acceptances=list(
                                            state.existing_formal_acceptances
                                        ),
                                        timeline_clips=conservative_clips,
                                    )
                                )
                            }
                        ),
                    }
                )
                latest_report = _audit_candidate_timeline(
                    project_id,
                    latest_candidate,
                )
                new_findings = _new_blocking_timeline_findings(
                    before_report,
                    latest_report,
                )
                if not new_findings:
                    break
                newly_rejected_groups = {
                    str(clip.get("dubbing_group_id") or "")
                    for finding in new_findings
                    for entity_id in finding.entity_ids
                    if (clip := clips_by_id.get(str(entity_id))) is not None
                    and str(clip.get("dubbing_group_id") or "")
                    not in reverted_group_ids
                }
                if not newly_rejected_groups:
                    break
                reverted_group_ids.update(newly_rejected_groups)
            if new_findings:
                finding = new_findings[0]
                raise AppException(
                    409,
                    finding.code,
                    finding.message,
                    {
                        "entity_ids": finding.entity_ids,
                        "recommended_action": finding.recommended_action,
                    },
                )
            if _blocking_timeline_finding_count(
                latest_report
            ) >= _blocking_timeline_finding_count(before_report):
                return (
                    project_service
                    .with_current_timeline_tts_placements_completed(current)
                )
            return (
                project_service
                .with_current_timeline_tts_placements_completed(
                    latest_candidate
                )
            )

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        original_by_id = {
            str(item.get("clip_id") or ""): item
            for item in draft.timeline_clips
        }
        crop_changed_candidate_ids = {
            str(item.get("candidate_id") or item.get("result_id") or "")
            for item in updated.timeline_clips
            if (
                (original := original_by_id.get(str(item.get("clip_id") or "")))
                is not None
                and (
                    int(item.get("source_start_ms") or 0)
                    != int(original.get("source_start_ms") or 0)
                    or int(item.get("source_end_ms") or 0)
                    != int(original.get("source_end_ms") or 0)
                )
            )
        }
        crop_changed_candidate_ids.discard("")
        if crop_changed_candidate_ids:
            self._refresh_candidate_gates_after_safe_crop(
                project_id,
                crop_changed_candidate_ids,
            )
            self.reconcile_existing_formal_groups(project_id)
            return self._require_current_project(project_id)
        return updated

    def _refresh_candidate_gates_after_safe_crop(
        self,
        project_id: str,
        candidate_ids: set[str],
    ) -> None:
        """Reconcile projection-owned gap durations after proven edge trims."""

        for candidate_id in sorted(candidate_ids):
            current = self._require_current_project(project_id)
            current_plan = current.dubbing_production.active_plan
            frozen = next(
                (
                    item
                    for item in current.dubbing_production.candidate_inputs
                    if item.candidate_id == candidate_id
                ),
                None,
            )
            previous_report = next(
                (
                    item
                    for item in current.dubbing_production.candidate_reports
                    if item.candidate_id == candidate_id
                ),
                None,
            )
            if frozen is None or previous_report is None:
                try:
                    self.resync_candidate_automatic_cqc(
                        project_id,
                        candidate_id,
                    )
                except AppException:
                    pass
                current = self._require_current_project(project_id)
                current_plan = current.dubbing_production.active_plan
                frozen = next(
                    (
                        item
                        for item in current.dubbing_production.candidate_inputs
                        if item.candidate_id == candidate_id
                    ),
                    None,
                )
                previous_report = next(
                    (
                        item
                        for item in current.dubbing_production.candidate_reports
                        if item.candidate_id == candidate_id
                    ),
                    None,
                )
            if (
                current_plan is None
                or frozen is None
                or frozen.audio is None
                or not frozen.audio.aligned_words
                or previous_report is None
                or previous_report.audio_evidence is None
                or not previous_report.audio_evidence.aligned_words
            ):
                continue
            group = next(
                (
                    item
                    for item in current_plan.groups
                    if item.group_id == frozen.group_id
                ),
                None,
            )
            if group is None:
                continue
            candidate_clips = _current_group_working_candidate_clips(
                current,
                group_id=group.group_id,
                candidate_id=candidate_id,
                target_subtitle_ids=list(group.subtitle_ids),
            )
            reviewed_gaps = reconcile_gap_evidence_with_projection(
                list(previous_report.audio_evidence.gap_evidence),
                candidate_clips,
            )
            report = self.persist_gap_processing_report(
                project_id,
                candidate_id,
                gap_decisions=reviewed_gaps,
            )
            if any(
                finding.severity == "blocking"
                for finding in report.findings
            ):
                continue
            gate = self._build_automatic_timeline_gate(
                plan=current_plan,
                frozen=frozen,
                report=report,
                clips=candidate_clips,
                gaps=reviewed_gaps,
                status="passed",
            )
            self.commit_timeline_edit_gate(
                project_id,
                DubbingTimelineEditGateCommitRequest(gate=gate),
            )

    def compact_current_timeline_to_terminal_selection(self, project_id: str):
        """Drop superseded dub takes after every current-plan group is terminal."""

        def apply(current):
            state_without_audit = current.dubbing_production.model_copy(
                update={"latest_timeline_audit": None}
            )
            projection = current.model_copy(
                update={"dubbing_production": state_without_audit}
            )
            run = self._build_production_run_snapshot(projection)
            try:
                timeline_clips = (
                    dubbing_production_run.compact_terminal_run_timeline_clips(
                        groups=list(run.groups),
                        timeline_clips=[dict(item) for item in current.timeline_clips],
                    )
                )
            except ValueError as exc:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_FINAL_SELECTION_INCOMPLETE",
                    "最终配音选择尚未完整，不能清理旧片段。",
                    {"reason": str(exc)},
                ) from exc
            state_without_audit = state_without_audit.model_copy(
                update={
                    "existing_formal_acceptances": (
                        dubbing_production_run.rebase_terminal_existing_acceptances(
                            acceptances=list(
                                state_without_audit.existing_formal_acceptances
                            ),
                            timeline_clips=timeline_clips,
                        )
                    )
                }
            )
            ui_state = dict(current.ui_state)
            lane_states = ui_state.get("dub_lane_states")
            if isinstance(lane_states, dict):
                ui_state["dub_lane_states"] = {
                    "0": dict(lane_states.get("0", {}))
                }
            return current.model_copy(
                update={
                    "timeline_clips": timeline_clips,
                    "ui_state": ui_state,
                    "dubbing_production": state_without_audit,
                }
            )

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return updated

    def trim_safe_alignment_padding_overlaps(self, project_id: str):
        """Trim only proven edge silence and require a fresh seam review."""

        draft = self._require_current_project(project_id)
        plan = draft.dubbing_production.active_plan
        if plan is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_REQUIRED",
                "请先基于当前字幕和画面完成配音生成计划。",
            )
        expected_source_revision = plan.source_revision
        expected_plan_revision = plan.plan_revision
        original_clips = [dict(item) for item in draft.timeline_clips]
        trimmed_clips = (
            dubbing_production_run.trim_safe_alignment_padding_overlaps(
                original_clips
            )
        )
        original_by_id = {
            str(item.get("clip_id") or ""): item for item in original_clips
        }
        changed_clip_ids = {
            str(item.get("clip_id") or "")
            for item in trimmed_clips
            if item != original_by_id.get(str(item.get("clip_id") or ""))
        }
        if not changed_clip_ids:
            return draft
        affected_candidate_ids = {
            str(item.get("candidate_id") or item.get("result_id") or "")
            for item in trimmed_clips
            if str(item.get("clip_id") or "") in changed_clip_ids
        }
        affected_candidate_ids.discard("")
        prepared_clips: list[dict] = []
        for raw in trimmed_clips:
            clip = dict(raw)
            identity = str(
                clip.get("candidate_id") or clip.get("result_id") or ""
            )
            if identity in affected_candidate_ids:
                clip.pop("timeline_edit_gate", None)
            prepared_clips.append(clip)

        def apply(current):
            current_plan = current.dubbing_production.active_plan
            if (
                current_plan is None
                or current_plan.source_revision != expected_source_revision
                or current_plan.plan_revision != expected_plan_revision
                or [dict(item) for item in current.timeline_clips]
                != original_clips
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_TIMELINE_CHANGED",
                    "时间线已经变化，请刷新后重新收口配音重叠。",
                )
            state = current.dubbing_production.model_copy(
                update={"latest_timeline_audit": None}
            )
            return current.model_copy(
                update={
                    "timeline_clips": prepared_clips,
                    "dubbing_production": state,
                }
            )

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )

        self._refresh_candidate_gates_after_safe_crop(
            project_id,
            affected_candidate_ids,
        )
        self.reconcile_existing_formal_groups(project_id)
        return self._require_current_project(project_id)

    def split_staged_candidate_projection(
        self,
        project_id: str,
        payload: DubbingStagedCandidateSplitRequest,
    ) -> DubbingCandidateCqcReport:
        """Atomically replace one staged projection with word-safe slices."""

        def apply(current):
            plan = current.dubbing_production.active_plan
            report = next(
                (item for item in current.dubbing_production.candidate_reports
                 if item.candidate_id == payload.candidate_id),
                None,
            )
            frozen = next(
                (item for item in current.dubbing_production.candidate_inputs
                 if item.candidate_id == payload.candidate_id),
                None,
            )
            stage = report.staged_candidate_projection if report is not None else None
            group = next(
                (item for item in (plan.groups if plan is not None else [])
                 if report is not None and item.group_id == report.group_id),
                None,
            )
            if (
                current._repository_revision != payload.expected_repository_revision
                or plan is None
                or report is None
                or frozen is None
                or frozen.audio is None
                or stage is None
                or group is None
                or plan.source_revision != payload.source_revision
                or plan.plan_revision != payload.plan_revision
                or report.source_revision != payload.source_revision
                or report.plan_revision != payload.plan_revision
                or stage.candidate_id != payload.candidate_id
                or stage.candidate_clip_projection_fingerprint
                != payload.candidate_clip_projection_fingerprint
                or _target_owned_projection_fingerprint(
                    current, target_subtitle_ids=list(group.subtitle_ids)
                ) != stage.target_projection_fingerprint
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_STAGE_CHANGED",
                    "候选暂存投影、计划或时间线已经变化，请重新读取后再剪辑。",
                )
            stage_clips = _staged_projection_runtime_clips(current, stage)
            stage_by_id = {str(clip.get("clip_id") or ""): clip for clip in stage_clips}
            if (
                len(stage_by_id) != len(stage_clips)
                or {command.clip_id for command in payload.commands}
                - set(stage_by_id)
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_STAGE_CHANGED",
                    "待切分的暂存片段已经变化。",
                )
            current_hashes = dubbing_media.current_timeline_audio_sha256s(
                project_id,
                current.model_copy(update={"timeline_clips": stage_clips}),
            )
            for command in payload.commands:
                clip = stage_by_id[command.clip_id]
                if (
                    command.audio_sha256 != frozen.audio_sha256
                    or current_hashes.get(command.clip_id) != frozen.audio_sha256
                ):
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_STAGE_AUDIO_CHANGED",
                        "候选音频已经变化，请重新读取后再剪辑。",
                    )
                clip_source_start_ms = int(clip.get("source_start_ms") or 0)
                clip_source_end_ms = int(clip.get("source_end_ms") or 0)
                clip_words = [
                    word
                    for word in frozen.audio.aligned_words
                    if word.end_ms > word.start_ms
                    and word.start_ms >= clip_source_start_ms
                    and word.end_ms <= clip_source_end_ms
                ]
                if len(clip_words) != sum(
                    word.end_ms > word.start_ms
                    and word.end_ms > clip_source_start_ms
                    and word.start_ms < clip_source_end_ms
                    for word in frozen.audio.aligned_words
                ):
                    raise AppException(
                        422,
                        "VIDEO_LOCALIZATION_DUBBING_SPLIT_ALIGNMENT_INVALID",
                        "暂存片段边界不能切入逐词强制对齐证据。",
                    )
                try:
                    dubbing_candidate_alignment.validate_slice_alignment(
                        slices=list(command.slices),
                        words=clip_words,
                        source_start_ms=clip_source_start_ms,
                        source_end_ms=clip_source_end_ms,
                    )
                except ValueError as exc:
                    raise AppException(
                        422,
                        "VIDEO_LOCALIZATION_DUBBING_SPLIT_ALIGNMENT_INVALID",
                        str(exc),
                    ) from exc
            snapshot = domain.build_project_snapshot(current)
            try:
                projected = domain.split_planned_timeline_clips(
                    timeline_clips=[
                        *[dict(clip) for clip in current.timeline_clips],
                        *stage_clips,
                    ],
                    commands=list(payload.commands),
                    groups=list(plan.groups),
                    localized_subtitles=list(current.localized_subtitles),
                    boundaries=snapshot.boundaries,
                )
            except ValueError as exc:
                raise AppException(
                    422, "VIDEO_LOCALIZATION_DUBBING_SPLIT_INVALID", str(exc)
                ) from exc
            source_ids = set(stage_by_id)
            staged_clips = [
                clip for clip in projected
                if str(clip.get("candidate_id") or "") == payload.candidate_id
                and (
                    str(clip.get("clip_id") or "") in source_ids
                    or str(clip.get("media_source_clip_id") or "") in source_ids
                )
            ]
            if not staged_clips:
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_DUBBING_SPLIT_INVALID",
                    "安全切分没有留下可播放的候选片段。",
                )
            removed_before_source_ms = [
                (
                    int(stage_by_id[command.clip_id].get("source_end_ms") or 0),
                    int(stage_by_id[command.clip_id].get("source_end_ms") or 0)
                    - int(stage_by_id[command.clip_id].get("source_start_ms") or 0)
                    - sum(
                        item.source_end_ms - item.source_start_ms
                        for item in command.slices
                    )
                    # A leading safe crop preserves the first word's timeline
                    # anchor and therefore must not compact another staged
                    # source clip.  Only removed interior silence closes the
                    # following staged clip's artificial gap.
                    - (
                        int(command.slices[0].source_start_ms)
                        - int(stage_by_id[command.clip_id].get("source_start_ms") or 0)
                    ),
                )
                for command in payload.commands
            ]
            # A staged candidate can already consist of several source clips.
            # Removing verified internal silence from one must not become extra
            # silence before its following staged clip.  Move later source
            # clips by exactly the removed duration, thereby preserving their
            # existing semantic pause and relative anchor.
            for clip in staged_clips:
                source_start_ms = int(clip.get("source_start_ms") or 0)
                shift_ms = sum(
                    removed_ms
                    for source_end_ms, removed_ms in removed_before_source_ms
                    if removed_ms > 0 and source_end_ms <= source_start_ms
                )
                if shift_ms:
                    clip["start_ms"] = int(clip.get("start_ms") or 0) - shift_ms
                    clip["end_ms"] = int(clip.get("end_ms") or 0) - shift_ms
            try:
                reconciled_gaps = reconcile_gap_evidence_with_projection(
                    list(frozen.audio.gap_evidence),
                    staged_clips,
                )
                dubbing_candidate_alignment.validate_candidate_clip_coverage(
                    clips=staged_clips,
                    words=list(frozen.audio.aligned_words),
                    speech_start_ms=frozen.audio.speech_start_ms,
                    speech_end_ms=frozen.audio.speech_end_ms,
                )
            except ValueError as exc:
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_DUBBING_SPLIT_ALIGNMENT_INVALID",
                    str(exc),
                ) from exc
            next_stage = _staged_candidate_projection(
                candidate_id=payload.candidate_id,
                target_projection_fingerprint=stage.target_projection_fingerprint,
                clips=staged_clips,
            )
            next_frozen = frozen.model_copy(update={
                "audio": frozen.audio.model_copy(update={
                    "gap_evidence": reconciled_gaps,
                }),
                "retained_content_evidence": None,
            })
            audio = next_frozen.audio
            audit = dubbing_gap_adjudication.build_semantic_boundary_audit(
                source_revision=next_frozen.source_revision,
                plan_revision=next_frozen.plan_revision,
                candidate_id=next_frozen.candidate_id,
                audio_sha256=str(next_frozen.audio_sha256 or ""),
                candidate_evidence_fingerprint=domain.candidate_evidence_fingerprint(next_frozen),
                candidate_clip_projection_fingerprint=(
                    next_stage.candidate_clip_projection_fingerprint
                ),
                expected_spoken_text=next_frozen.expected_spoken_text,
                aligned_words=list(audio.aligned_words),
                gaps=list(reconciled_gaps),
                clips=staged_clips,
            )
            next_report = report.model_copy(update={
                "evidence_fingerprint": domain.candidate_evidence_fingerprint(next_frozen),
                "semantic_boundary_audit": audit,
                "staged_candidate_projection": next_stage,
                "overall_status": "needs_review",
                "recommended_action": "listen_and_review",
            })
            return current.model_copy(update={
                "dubbing_production": current.dubbing_production.model_copy(update={
                    "candidate_inputs": [
                        next_frozen if item.candidate_id == payload.candidate_id else item
                        for item in current.dubbing_production.candidate_inputs
                    ],
                    "candidate_reports": [
                        next_report if item.candidate_id == payload.candidate_id else item
                        for item in current.dubbing_production.candidate_reports
                    ],
                })
            })

        updated = project_service.update_video_localization_atomic(
            project_id, apply, intent="content"
        )
        if updated is None:
            raise AppException(404, "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND", "项目不存在。")
        return next(
            item for item in updated.dubbing_production.candidate_reports
            if item.candidate_id == payload.candidate_id
        )

    def split_and_rebalance_current_timeline(
        self,
        project_id: str,
        payload: DubbingTimelineSplitRequest,
    ):
        """Persist verified source partitions, then validate the edited lane."""

        draft = self._require_current_project(project_id)
        plan = draft.dubbing_production.active_plan
        if plan is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_REQUIRED",
                "请先基于当前字幕和画面完成配音生成计划。",
            )
        self._assert_current_revision(draft, payload.source_revision)
        if payload.plan_revision != plan.plan_revision:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
                "配音计划已经变化，请基于当前计划重新剪辑。",
            )
        if payload.timeline_revision != domain.dubbing_timeline_projection_revision(
            draft
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_TIMELINE_CHANGED",
                "配音时间线已经变化，请重新读取声学切分输入。",
            )
        expected_source_revision = plan.source_revision
        expected_plan_revision = plan.plan_revision
        expected_timeline_revision = payload.timeline_revision

        def apply(current):
            current_plan = current.dubbing_production.active_plan
            if (
                current_plan is None
                or current_plan.source_revision != expected_source_revision
                or current_plan.plan_revision != expected_plan_revision
                or domain.dubbing_timeline_projection_revision(current)
                != expected_timeline_revision
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_TIMELINE_CHANGED",
                    "配音计划或时间线已经变化，请重新读取声学切分输入。",
                )
            frozen_inputs = {
                item.candidate_id: item
                for item in current.dubbing_production.candidate_inputs
            }
            current_audio_hashes = (
                dubbing_media.current_timeline_audio_sha256s(
                    project_id,
                    current,
                )
            )
            current_clips_by_id = {
                str(item.get("clip_id") or ""): dict(item)
                for item in current.timeline_clips
                if str(item.get("clip_id") or "")
            }
            for command in payload.commands:
                frozen = frozen_inputs.get(command.candidate_id)
                if (
                    frozen is None
                    or frozen.audio_sha256 != command.audio_sha256
                    or frozen.source_revision != expected_source_revision
                    or frozen.plan_revision != expected_plan_revision
                ):
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_SPLIT_EVIDENCE_STALE",
                        "切分使用的候选音频或版本已经变化。",
                        {"candidate_id": command.candidate_id},
                    )
                clip = current_clips_by_id.get(command.clip_id)
                if clip is None:
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_SPLIT_CLIP_CHANGED",
                        "待切分的正式片段已经变化。",
                        {"clip_id": command.clip_id},
                    )
                try:
                    dubbing_candidate_alignment.validate_slice_alignment(
                        slices=list(command.slices),
                        words=list(
                            frozen.audio.aligned_words
                            if frozen.audio is not None
                            else []
                        ),
                        source_start_ms=int(clip.get("source_start_ms") or 0),
                        source_end_ms=int(
                            clip.get("source_end_ms")
                            or int(clip.get("source_start_ms") or 0)
                            + int(clip.get("end_ms") or 0)
                            - int(clip.get("start_ms") or 0)
                        ),
                    )
                except ValueError as exc:
                    raise AppException(
                        422,
                        "VIDEO_LOCALIZATION_DUBBING_SPLIT_ALIGNMENT_INVALID",
                        str(exc),
                        {"candidate_id": command.candidate_id},
                    ) from exc
                if current_audio_hashes.get(command.clip_id) != command.audio_sha256:
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_SPLIT_AUDIO_CHANGED",
                        "正式片段的实际音频与声学切分证据不一致。",
                        {"clip_id": command.clip_id},
                    )
            snapshot = domain.build_project_snapshot(current)
            try:
                timeline_clips = domain.split_planned_timeline_clips(
                    timeline_clips=[dict(item) for item in current.timeline_clips],
                    commands=payload.commands,
                    groups=current_plan.groups,
                    localized_subtitles=current.localized_subtitles,
                    boundaries=snapshot.boundaries,
                )
            except ValueError as exc:
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_DUBBING_SPLIT_INVALID",
                    str(exc),
                ) from exc
            state = current.dubbing_production.model_copy(
                update={"latest_timeline_audit": None}
            )
            candidate = current.model_copy(
                update={
                    "timeline_clips": timeline_clips,
                    "dubbing_production": state,
                }
            )
            finding = _new_semantic_blocking_timeline_finding(
                current,
                _audit_candidate_timeline(project_id, current),
                candidate,
                _audit_candidate_timeline(project_id, candidate),
                ignored_codes={"TIMELINE_CLIP_CQC_NOT_PASSED"},
            )
            if finding is not None:
                raise AppException(
                    409,
                    finding.code,
                    finding.message,
                    {
                        "entity_ids": finding.entity_ids,
                        "recommended_action": finding.recommended_action,
                    },
                )
            return candidate

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return self._require_current_project(project_id)

    def commit_timeline_edit_gate(
        self,
        project_id: str,
        payload: DubbingTimelineEditGateCommitRequest,
    ):
        """Atomically persist candidate-bound, evidence-backed edit guidance."""

        expected_gate = payload.gate

        def apply(current):
            plan = current.dubbing_production.active_plan
            if (
                plan is None
                or plan.source_revision != expected_gate.source_revision
                or plan.plan_revision != expected_gate.plan_revision
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_TIMELINE_GATE_STALE",
                    "配音计划已经变化，请重新读取当前候选并处理气口。",
                )
            reports = {
                item.candidate_id: item
                for item in current.dubbing_production.candidate_reports
            }
            report = reports.get(expected_gate.candidate_id)
            frozen_inputs = {
                item.candidate_id: item
                for item in current.dubbing_production.candidate_inputs
            }
            frozen = frozen_inputs.get(expected_gate.candidate_id)
            if (
                report is None
                or frozen is None
                or report.evidence_fingerprint
                != expected_gate.cqc_report_fingerprint
                or report.audio_evidence is None
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_TIMELINE_GATE_CQC_STALE",
                    "候选的气口或真实音频证据已经变化，请重新处理。",
                )
            candidate_clips = [
                dict(clip)
                for clip in current.timeline_clips
                if str(clip.get("candidate_id") or clip.get("result_id") or "")
                == expected_gate.candidate_id
            ]
            if not candidate_clips or (
                candidate_clip_projection_fingerprint(candidate_clips)
                != expected_gate.candidate_clip_projection_fingerprint
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_TIMELINE_GATE_CLIPS_CHANGED",
                    "候选片段的位置或裁切范围已经变化，请重新处理气口。",
                )
            audio = report.audio_evidence
            exception_applied = bool(
                audio.content_speed_exception_reason
                and audio.content_speed_exception_evidence_ids
            )
            if not timeline_edit_gate_matches(
                expected_gate.model_dump(mode="json"),
                candidate_clips=candidate_clips,
                source_revision=plan.source_revision,
                plan_revision=plan.plan_revision,
                candidate_id=expected_gate.candidate_id,
                cqc_report_fingerprint=report.evidence_fingerprint,
                expected_gap_evidence=list(audio.gap_evidence),
                expected_aligned_words=list(audio.aligned_words),
                speaking_rate_ratio=audio.speaking_rate_ratio,
                content_speed_exception_applied=exception_applied,
            ):
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_DUBBING_TIMELINE_GATE_INVALID",
                    "时间线门禁与当前倍速或逐气口证据不一致。",
                )
            ordered = sorted(
                candidate_clips,
                key=lambda clip: (
                    int(clip.get("source_start_ms") or 0),
                    int(clip.get("start_ms") or 0),
                ),
            )
            first, last = ordered[0], ordered[-1]
            actual_start_ms = int(first.get("start_ms") or 0) + (
                int(audio.speech_start_ms or 0)
                - int(first.get("source_start_ms") or 0)
            )
            actual_end_ms = int(last.get("start_ms") or 0) + (
                int(audio.speech_end_ms or 0)
                - int(last.get("source_start_ms") or 0)
            )
            if (
                expected_gate.actual_speech_start_delta_ms
                != actual_start_ms - frozen.target_start_ms
                or expected_gate.actual_speech_end_delta_ms
                != actual_end_ms - frozen.target_end_ms
            ):
                raise AppException(
                    422,
                    "VIDEO_LOCALIZATION_DUBBING_TIMELINE_GATE_PLACEMENT_INVALID",
                    "门禁记录的真实发声头尾与当前时间线位置不一致。",
                )
            serialized_gate = expected_gate.model_dump(mode="json")
            timeline_clips = [
                (
                    {**dict(clip), "timeline_edit_gate": serialized_gate}
                    if str(clip.get("candidate_id") or clip.get("result_id") or "")
                    == expected_gate.candidate_id
                    else dict(clip)
                )
                for clip in current.timeline_clips
            ]
            state = current.dubbing_production.model_copy(
                update={"latest_timeline_audit": None}
            )
            return current.model_copy(
                update={
                    "timeline_clips": timeline_clips,
                    "dubbing_production": state,
                }
            )

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return updated

    @staticmethod
    def _promote_reviewed_candidate(
        project_id: str,
        *,
        group_id: str,
        candidate_id: str,
    ):
        """Atomically replace an old formal take after the new gate passed."""

        def apply(current):
            plan = current.dubbing_production.active_plan
            group = next(
                (
                    item
                    for item in (plan.groups if plan is not None else [])
                    if item.group_id == group_id
                ),
                None,
            )
            report = next(
                (
                    item
                    for item in current.dubbing_production.candidate_reports
                    if item.candidate_id == candidate_id
                ),
                None,
            )
            frozen = next(
                (
                    item
                    for item in current.dubbing_production.candidate_inputs
                    if item.candidate_id == candidate_id
                    and item.group_id == group_id
                ),
                None,
            )
            if plan is None or group is None or report is None or frozen is None:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_PROMOTION_STALE",
                    "候选或配音计划已经变化，请重新处理当前片段后再替换。",
                )
            audio = report.audio_evidence
            candidate_clips = _current_group_working_candidate_clips(
                current,
                group_id=group_id,
                candidate_id=candidate_id,
                target_subtitle_ids=list(group.subtitle_ids),
            )
            raw_gate = next(
                (
                    clip.get("timeline_edit_gate")
                    for clip in candidate_clips
                    if isinstance(clip.get("timeline_edit_gate"), dict)
                ),
                None,
            )
            if audio is None or raw_gate is None or not candidate_clips:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_PROMOTION_GATE_MISSING",
                    "新候选尚未完成逐词和气口验收，不能替换旧片段。",
                )
            exception_applied = bool(
                audio.content_speed_exception_reason
                and audio.content_speed_exception_evidence_ids
            )
            if not all(clip.get("timeline_edit_gate") == raw_gate for clip in candidate_clips) or not timeline_edit_gate_matches(
                raw_gate,
                candidate_clips=candidate_clips,
                source_revision=plan.source_revision,
                plan_revision=plan.plan_revision,
                candidate_id=candidate_id,
                cqc_report_fingerprint=report.evidence_fingerprint,
                expected_gap_evidence=list(audio.gap_evidence),
                expected_aligned_words=list(audio.aligned_words),
                speaking_rate_ratio=audio.speaking_rate_ratio,
                content_speed_exception_applied=exception_applied,
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_PROMOTION_GATE_STALE",
                    "新候选验收记录与当前时间线不一致，不能替换旧片段。",
                )

            expected_targets = set(group.subtitle_ids)

            def belongs_to_group(clip: dict) -> bool:
                targets = set(_clip_target_subtitle_ids(clip))
                if targets:
                    return targets == expected_targets
                return str(clip.get("dubbing_group_id") or "") == group_id

            promoted = []
            candidate_clip_ids = {
                str(clip.get("clip_id") or "") for clip in candidate_clips
            }
            group_candidate_ids = {candidate_id}
            for raw in current.timeline_clips:
                clip = dict(raw)
                if str(clip.get("clip_id") or "") in candidate_clip_ids:
                    promoted_clip = {
                        **clip,
                        "dub_lane": 0,
                        "intentional_overlap": False,
                    }
                    promoted_clip.pop("timeline_edit_gate", None)
                    promoted.append(promoted_clip)
                elif clip.get("track_id") == "dub" and belongs_to_group(clip):
                    replaced_identity = str(
                        clip.get("candidate_id")
                        or clip.get("result_id")
                        or ""
                    )
                    if replaced_identity:
                        group_candidate_ids.add(replaced_identity)
                    continue
                else:
                    promoted.append(clip)

            promoted_candidate_clips = [
                clip
                for clip in promoted
                if str(clip.get("clip_id") or "") in candidate_clip_ids
            ]
            promoted_gate = {
                **raw_gate,
                "candidate_clip_projection_fingerprint": (
                    candidate_clip_projection_fingerprint(
                        promoted_candidate_clips
                    )
                ),
            }
            if not timeline_edit_gate_matches(
                promoted_gate,
                candidate_clips=promoted_candidate_clips,
                source_revision=plan.source_revision,
                plan_revision=plan.plan_revision,
                candidate_id=candidate_id,
                cqc_report_fingerprint=report.evidence_fingerprint,
                expected_gap_evidence=list(audio.gap_evidence),
                expected_aligned_words=list(audio.aligned_words),
                speaking_rate_ratio=audio.speaking_rate_ratio,
                content_speed_exception_applied=exception_applied,
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_PROMOTION_INVALID",
                    "新候选移入主轨后的验收记录无效，已保留旧片段。",
                )
            promoted = [
                (
                    {**clip, "timeline_edit_gate": promoted_gate}
                    if str(clip.get("clip_id") or "") in candidate_clip_ids
                    else clip
                )
                for clip in promoted
            ]

            candidates = []
            for raw in current.generated_candidates:
                item = dict(raw)
                identity = str(
                    item.get("candidate_id") or item.get("result_id") or ""
                )
                if identity in group_candidate_ids:
                    selected = identity == candidate_id
                    item["selected"] = selected
                    item["accepted"] = selected
                candidates.append(item)
            state = current.dubbing_production.model_copy(
                update={
                    "group_reviews": [
                        review
                        for review in current.dubbing_production.group_reviews
                        if not (
                            review.source_revision == plan.source_revision
                            and review.plan_revision == plan.plan_revision
                            and review.group_id == group_id
                            and review.candidate_id == candidate_id
                        )
                    ],
                    "latest_timeline_audit": None,
                }
            )
            return current.model_copy(
                update={
                    "timeline_clips": promoted,
                    "generated_candidates": candidates,
                    "dubbing_production": state,
                }
            )

        updated = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if updated is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return updated

    def sync_generated_candidate_automatic_cqc(
        self,
        task: GenerationTask,
        history: HistoryItem,
        verification: TTSVerificationResponse | None,
        *,
        allow_equivalent_plan_rebind: bool = False,
    ) -> DubbingCandidateCqcReport | None:
        """Persist generated-file validity and word/VAD gap evidence."""

        if (
            task.parameters.get("source") != "video_localization"
            or not task.bind_to_video_localization
            or not task.project_id
            or not history.result_id
        ):
            return None
        draft = self._require_current_project(task.project_id)
        plan = draft.dubbing_production.active_plan
        if plan is None:
            return None
        self._assert_current_revision(draft, plan.source_revision)
        task_plan_revision = _integer_or_none(
            task.parameters.get(
                "video_localization_dubbing_plan_revision"
            )
        )
        task_group_id = str(
            task.parameters.get(
                "video_localization_dubbing_group_id"
            )
            or ""
        )
        if plan.plan_revision <= 0:
            return None
        target_ids = tuple(
            str(value)
            for value in task.parameters.get(
                "video_localization_target_subtitle_ids",
                [],
            )
            if str(value)
        )
        if not target_ids and task.localized_subtitle_id:
            target_ids = (task.localized_subtitle_id,)
        target_set = set(target_ids)
        group = next(
            (
                item
                for item in plan.groups
                if target_set and target_set == set(item.subtitle_ids)
            ),
            None,
        )
        if group is None:
            return None
        group_identity_changed = (
            not task_group_id or group.group_id != task_group_id
        )
        plan_identity_changed = task_plan_revision != plan.plan_revision
        equivalent_plan_rebind = False
        if group_identity_changed or plan_identity_changed:
            if not allow_equivalent_plan_rebind:
                return None
            task_text = str(
                task.parameters.get("text")
                or task.input_text
                or ""
            )
            if (
                not task_text
                or domain.transcript_pronunciation_tokens(task_text)
                != domain.transcript_pronunciation_tokens(group.spoken_text)
                or (
                    {
                    str(value)
                    for value in task.parameters.get(
                        "video_localization_source_cue_ids", []
                    )
                    if str(value)
                }
                    and {
                        str(value)
                        for value in task.parameters.get(
                            "video_localization_source_cue_ids", []
                        )
                        if str(value)
                    }
                    != set(_group_source_cue_ids(draft, plan, group))
                )
            ):
                return None
            equivalent_plan_rebind = True
        output_path = Path(str(history.output_path or ""))
        audio = None
        audio_sha256 = None
        content_evidence = None
        if output_path.is_file():
            # Optional raw-material observations never gate alignment or edits.
            current_history = history_store.get(history.result_id) or history
            content_evidence = current_history.content_evidence
            speaking_rate_ratio = _task_speaking_rate_ratio(task)
            exception_reason = str(
                task.parameters.get("content_speed_exception_reason") or ""
            ).strip() or None
            exception_evidence_ids = [
                str(value)
                for value in task.parameters.get(
                    "content_speed_exception_evidence_ids",
                    [],
                )
                if str(value)
            ]
            ordinary_project_rates = [
                float(item.audio.speaking_rate_ratio)
                for item in draft.dubbing_production.candidate_inputs
                if item.plan_revision == plan.plan_revision
                and item.audio is not None
                and item.audio.speaking_rate_ratio is not None
                and not item.audio.content_speed_exception_reason
            ]
            if exception_reason is None:
                ordinary_project_rates.append(speaking_rate_ratio)
            if not ordinary_project_rates:
                ordinary_project_rates = [1.0]
            try:
                audio = audio_boundaries.analyze_dubbing_candidate_audio(
                    output_path,
                    speaking_rate_ratio=speaking_rate_ratio,
                    project_speaking_rate_ratio_min=min(ordinary_project_rates),
                    project_speaking_rate_ratio_max=max(ordinary_project_rates),
                    content_speed_exception_reason=exception_reason,
                    content_speed_exception_evidence_ids=exception_evidence_ids,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                raise AppException(409, "TTS_CONTENT_AUDIO_UNAVAILABLE",
                                   "生成音频无法读取，检查未完成；原时间线保持不变。") from exc
            expected_spoken_text = str(
                task.parameters.get("text")
                or task.input_text
                or group.spoken_text
            )
            try:
                aligned_items = qwen_forced_aligner.align_audio(
                    audio_path=str(output_path),
                    transcript_text=expected_spoken_text,
                    language=dubbing_candidate_alignment.alignment_language(
                        draft.language_config.target_language,
                        expected_spoken_text,
                    ),
                )
                aligned_words = dubbing_candidate_alignment.normalize_aligned_words(
                    aligned_items, duration_ms=audio.duration_ms,
                )
                audio = dubbing_candidate_alignment.with_alignment_evidence(audio, aligned_words)
            except Exception:
                # Missing alignment remains unavailable for automatic editing.
                pass
            try:
                audio_sha256 = media_assets.file_sha256(output_path)
            except OSError as exc:
                raise AppException(409, "TTS_CONTENT_AUDIO_UNAVAILABLE",
                                   "生成音频在检查期间已不可读；原时间线保持不变。") from exc
        units_by_id = {item.unit_id: item for item in plan.semantic_units}
        scene_ends = [
            units_by_id[unit_id].scene_end_ms
            for unit_id in group.unit_ids
            if unit_id in units_by_id
            and units_by_id[unit_id].scene_end_ms is not None
        ]
        candidate_clips = [
            dict(raw)
            for raw in draft.timeline_clips
            if str(raw.get("result_id") or "") == history.result_id
        ]
        placement_start_ms = (
            min(int(clip.get("start_ms") or 0) for clip in candidate_clips)
            if candidate_clips
            else None
        )
        placement_end_ms = (
            max(int(clip.get("end_ms") or 0) for clip in candidate_clips)
            if candidate_clips
            else None
        )
        candidate_identity = str(
            (candidate_clips[0] if candidate_clips else {}).get(
                "candidate_id"
            )
            or ""
        )
        if not candidate_identity:
            candidate_identity = next(
                (
                    str(dict(raw).get("candidate_id") or "")
                    for raw in reversed(draft.generated_candidates)
                    if str(dict(raw).get("result_id") or "")
                    == history.result_id
                    or str(dict(raw).get("task_id") or "") == task.task_id
                ),
                "",
            )
        candidate_identity = candidate_identity or history.result_id
        payload = DubbingCandidateCqcInput(
            source_context_fingerprint=domain.group_evidence_context_fingerprint(draft, plan, group),
            evidence_origin_plan_revision=(
                task_plan_revision if equivalent_plan_rebind else None
            ),
            source_revision=plan.source_revision,
            plan_revision=plan.plan_revision,
            group_id=group.group_id,
            candidate_id=candidate_identity,
            task_status=task.status.value,
            artifact_id=history.output_audio_id or history.result_id,
            audio_sha256=audio_sha256,
            expected_spoken_text=str(
                task.parameters.get("text")
                or task.input_text
                or group.spoken_text
            ),
            reference_transcript=str(task.parameters.get("ref_text") or ""),
            candidate_transcript=(
                content_evidence.transcript if content_evidence else ""
            ),
            content_evidence=content_evidence,
            target_start_ms=group.target_start_ms,
            target_end_ms=group.target_end_ms,
            planned_scene_end_ms=(max(scene_ends) if scene_ends else None),
            placement_start_ms=placement_start_ms,
            placement_end_ms=placement_end_ms,
            audio=audio,
            subjective_reviews=[],
        )
        report = domain.build_candidate_gap_processing_report(payload)
        self._persist_candidate_report(
            task.project_id,
            report,
            payload,
            freeze_input=True,
        )
        return report

    def resync_history_candidate_automatic_cqc(
        self,
        project_id: str,
        result_id: str,
    ) -> DubbingCandidateCqcReport | None:
        """Rebuild deterministic CQC evidence after selecting a saved result."""

        history = history_store.get(result_id)
        if history is None or history.project_id != project_id:
            return None
        task = task_queue.get_task(history.task_id)
        if task is None:
            return None
        draft = self._require_current_project(project_id)
        plan = draft.dubbing_production.active_plan
        candidate_ids = {
            str(item.get("candidate_id") or "") for item in draft.generated_candidates
            if item.get("result_id") == result_id
        }
        frozen = next((item for item in draft.dubbing_production.candidate_inputs
                       if item.candidate_id in candidate_ids), None)
        report = next((item for item in draft.dubbing_production.candidate_reports
                       if frozen and item.candidate_id == frozen.candidate_id), None)
        if (
            plan and frozen and report and frozen.audio and frozen.audio.aligned_words
            and frozen.source_revision == plan.source_revision
            and frozen.plan_revision == plan.plan_revision
            and report.evidence_fingerprint == domain.candidate_evidence_fingerprint(frozen)
            and frozen.task_status == task.status.value == "success"
        ):
            path = history_store.audio_path(result_id)
            if path and Path(path).is_file() and media_assets.file_sha256(Path(path)) == frozen.audio_sha256:
                return report
        return self.sync_generated_candidate_automatic_cqc(
            task,
            history,
            history.verification or task.verification,
            allow_equivalent_plan_rebind=True,
        )

    def resync_candidate_automatic_cqc(
        self,
        project_id: str,
        candidate_id: str,
    ) -> DubbingCandidateCqcReport:
        """Refresh one persisted candidate through the canonical CQC path."""

        draft = self._require_current_project(project_id)
        candidate = next(
            (
                dict(item)
                for item in draft.generated_candidates
                if str(dict(item).get("candidate_id") or "") == candidate_id
            ),
            None,
        )
        result_id = str((candidate or {}).get("result_id") or "")
        if not result_id:
            result_id = next(
                (
                    str(dict(item).get("result_id") or "")
                    for item in draft.timeline_clips
                    if str(
                        dict(item).get("candidate_id")
                        or dict(item).get("result_id")
                        or ""
                    )
                    == candidate_id
                    and str(dict(item).get("result_id") or "")
                ),
                "",
            )
        if not result_id:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_NOT_FOUND",
                "找不到该配音候选的持久生成结果。",
            )
        report = self.resync_history_candidate_automatic_cqc(
            project_id,
            result_id,
        )
        if report is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_CQC_REFRESH_FAILED",
                "该候选已不属于当前配音计划，无法刷新质检证据。",
            )
        return report

    @staticmethod
    def _require_current_project(project_id: str):
        draft = draft_store.get(project_id)
        if draft is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return draft

    @staticmethod
    def _assert_current_revision(draft, claimed_revision: str) -> None:
        stored_revision = domain.dubbing_source_revision(draft)
        if stored_revision != claimed_revision:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_SOURCE_CHANGED",
                "原文或本土化内容已经变化，请基于当前版本重新规划或质检。",
                {
                    "expected_source_revision": stored_revision,
                    "received_source_revision": claimed_revision,
                },
            )

    @staticmethod
    def _persist_plan(
        project_id: str,
        plan: DubbingGenerationPlan,
    ) -> DubbingGenerationPlan:
        def apply(current):
            current_revision = domain.dubbing_source_revision(current)
            if current_revision != plan.source_revision:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_SOURCE_CHANGED",
                    "原文或本土化内容已经变化，请基于当前版本重新规划。",
                )
            plan_revision = (
                current.dubbing_production.plan_revision_counter + 1
            )
            committed_plan = plan.model_copy(
                update={"plan_revision": plan_revision}
            )
            audio_sha256_by_clip_id = (
                dubbing_media.current_timeline_audio_sha256s(project_id, current)
                if current.dubbing_production.candidate_reports
                or current.dubbing_production.manual_timing_deferrals
                else {}
            )
            retained_inputs, retained_reports = retain_unchanged_completion_evidence(
                current_draft=current,
                state=current.dubbing_production,
                new_plan=committed_plan,
                timeline_clips=[dict(clip) for clip in current.timeline_clips],
                audio_sha256_by_clip_id=audio_sha256_by_clip_id,
            )
            retained_deferrals = retain_unchanged_manual_timing_deferrals(
                current_draft=current,
                state=current.dubbing_production,
                new_plan=committed_plan,
                timeline_clips=[dict(clip) for clip in current.timeline_clips],
                audio_sha256_by_clip_id=audio_sha256_by_clip_id,
            )
            state = current.dubbing_production.model_copy(
                update={
                    "enforcement_mode": "planned",
                    "plan_revision_counter": plan_revision,
                    "active_plan": committed_plan,
                    "scheduling_policies": [],
                    "candidate_reports": retained_reports,
                    "candidate_inputs": retained_inputs,
                    "group_failures": [],
                    "group_reviews": [],
                    "manual_timing_deferrals": retained_deferrals,
                    "existing_formal_acceptances": [],
                    "latest_timeline_audit": None,
                }
            )
            candidates = [
                _without_legacy_candidate_review_state(raw)
                for raw in current.generated_candidates
            ]
            clips = [
                _without_legacy_timeline_review_state(raw)
                for raw in current.timeline_clips
            ]
            retired_tasks, retired_ids = _retire_inflight_tts_workflows(
                current.tts_tasks
            )
            ui_state = dict(current.ui_state)
            discarded = {
                str(value)
                for value in ui_state.get("discarded_tts_task_ids", [])
                if isinstance(value, str)
            }
            discarded.update(retired_ids)
            latest = ui_state.get("latest_tts_task_by_segment", {})
            if isinstance(latest, dict):
                latest = {
                    key: value
                    for key, value in latest.items()
                    if str(value or "") not in retired_ids
                }
            ui_state.update(
                {
                    "discarded_tts_task_ids": sorted(discarded),
                    "latest_tts_task_by_segment": latest,
                }
            )
            return current.model_copy(
                update={
                    "dubbing_production": state,
                    "generated_candidates": candidates,
                    "timeline_clips": clips,
                    "tts_tasks": retired_tasks,
                    "ui_state": ui_state,
                }
            )

        saved = project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="content",
        )
        if saved is None or saved.dubbing_production.active_plan is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        return saved.dubbing_production.active_plan

    @staticmethod
    def _persist_candidate_report(
        project_id: str,
        report: DubbingCandidateCqcReport,
        payload: DubbingCandidateCqcInput,
        *,
        freeze_input: bool,
    ) -> None:
        def apply(current):
            plan = current.dubbing_production.active_plan
            current_revision = domain.dubbing_source_revision(current)
            if (
                plan is None
                or current_revision != payload.source_revision
                or plan.source_revision != payload.source_revision
                or report.source_revision != payload.source_revision
                or plan.plan_revision != payload.plan_revision
                or report.plan_revision != payload.plan_revision
                or report.candidate_id != payload.candidate_id
                or report.group_id != payload.group_id
                or not any(
                    group.group_id == payload.group_id
                    for group in plan.groups
                )
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DUBBING_CQC_STATE_CHANGED",
                    "配音计划或候选证据已经变化，请刷新后重新质检。",
                )
            if not freeze_input:
                frozen = next(
                    (
                        item
                        for item in (
                            current.dubbing_production.candidate_inputs
                        )
                        if item.candidate_id == payload.candidate_id
                    ),
                    None,
                )
                # Gap review may change only the editorial disposition of
                # already-frozen ranges.  Compare the immutable automatic
                # evidence against the original frozen audio; otherwise every
                # valid remove/shorten decision looks like evidence drift.
                comparable_payload = (
                    payload.model_copy(
                        update={
                            "audio": frozen.audio,
                            "placement_start_ms": frozen.placement_start_ms,
                            "placement_end_ms": frozen.placement_end_ms,
                        }
                    )
                    if frozen is not None
                    else payload
                )
                if (
                    frozen is None
                    or frozen.model_dump(
                        mode="json",
                        exclude={"subjective_reviews"},
                    )
                    != comparable_payload.model_dump(
                        mode="json",
                        exclude={"subjective_reviews"},
                    )
                ):
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_CQC_STATE_CHANGED",
                        "候选自动证据已经变化，请刷新后重新质检。",
                    )
            reports = [
                item
                for item in current.dubbing_production.candidate_reports
                if item.candidate_id != report.candidate_id
            ]
            reports.append(report)
            inputs = list(current.dubbing_production.candidate_inputs)
            if freeze_input:
                inputs = [
                    item
                    for item in inputs
                    if item.candidate_id != report.candidate_id
                ]
                inputs.append(
                    payload.model_copy(update={
                        "subjective_reviews": [],
                        "evidence_origin_source_revision": None,
                        "evidence_origin_plan_revision": None,
                        "source_context_fingerprint": domain.group_evidence_context_fingerprint(
                            current, plan, next(g for g in plan.groups if g.group_id == payload.group_id)),
                    })
                )
            elif frozen is not None:
                # The rendered media evidence remains frozen, while placement
                # is an application-owned projection that legitimately changes
                # after timeline trimming or splitting.  Persist only those
                # two live coordinates so later reads and retries cannot fall
                # back to the raw pre-edit duration.
                inputs = [
                    item
                    if item.candidate_id != report.candidate_id
                    else item.model_copy(
                        update={
                            "placement_start_ms": payload.placement_start_ms,
                            "placement_end_ms": payload.placement_end_ms,
                        }
                    )
                    for item in inputs
                ]
            state = current.dubbing_production.model_copy(
                update={
                    "enforcement_mode": "planned",
                    "candidate_reports": reports,
                    "candidate_inputs": inputs,
                    "latest_timeline_audit": None,
                }
            )
            next_candidates: list[dict] = []
            for raw in current.generated_candidates:
                candidate = dict(raw)
                identities = {
                    str(candidate.get("candidate_id") or ""),
                    str(candidate.get("result_id") or ""),
                }
                if report.candidate_id in identities:
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
            for raw in current.timeline_clips:
                clip = dict(raw)
                identities = {
                    str(clip.get("candidate_id") or ""),
                    str(clip.get("result_id") or ""),
                }
                if report.candidate_id in identities:
                    for legacy_field in (
                        "cqc_status",
                        "cqc_report_version",
                        "cqc_report",
                        "timeline_edit_gate",
                    ):
                        clip.pop(legacy_field, None)
                next_clips.append(clip)
            return current.model_copy(
                update={
                    "dubbing_production": state,
                    "generated_candidates": next_candidates,
                    "timeline_clips": next_clips,
                }
            )

        project_service.update_video_localization_atomic(
            project_id,
            apply,
            intent="runtime",
        )

dubbing_production = DubbingProductionApplicationService()


def _retire_inflight_tts_workflows(workflows):
    """Cancel work whose frozen plan is superseded by a new revision."""

    retired_ids: set[str] = set()
    retired = []
    completed_at = now_iso()
    for workflow in workflows:
        if workflow.status in {"success", "failed", "cancelled"}:
            retired.append(workflow)
            continue
        retired_ids.add(workflow.workflow_id)
        if workflow.generation_task_id:
            retired_ids.add(workflow.generation_task_id)
        stages = []
        for stage in workflow.stages:
            if stage.status in {"success", "failed", "cancelled"}:
                stages.append(stage)
                continue
            stages.append(
                stage.model_copy(
                    update={
                        "status": "cancelled",
                        "progress": 0.0,
                        "error_code": (
                            "VIDEO_LOCALIZATION_DUBBING_PLAN_SUPERSEDED"
                        ),
                        "error_message": (
                            "配音计划已重建，旧任务结果不会写回当前时间线"
                        ),
                        "completed_at": completed_at,
                    }
                )
            )
        retired.append(
            workflow.model_copy(
                update={
                    "status": "cancelled",
                    "stages": stages,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                }
            )
        )
    return retired, retired_ids


def _without_legacy_candidate_review_state(raw: dict) -> dict:
    candidate = dict(raw)
    for legacy_field in (
        "selected",
        "accepted",
        "cqc_status",
        "cqc_report_version",
        "cqc_report",
    ):
        candidate.pop(legacy_field, None)
    return candidate


def _without_legacy_timeline_review_state(raw: dict) -> dict:
    clip = dict(raw)
    if str(clip.get("track_id") or "dub") == "dub":
        for legacy_field in (
            "cqc_status",
            "cqc_report_version",
            "cqc_report",
            "timeline_edit_gate",
        ):
            clip.pop(legacy_field, None)
    return clip


def _integer_or_none(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _task_speaking_rate_ratio(task: GenerationTask) -> float:
    """Resolve the actual normalized speed from current and legacy task shapes."""

    engine_parameters = task.parameters.get("engine_parameters")
    engine_speed = (
        _float_or_none(engine_parameters.get("speed"))
        if isinstance(engine_parameters, dict)
        else None
    )
    top_level_speed = _float_or_none(task.parameters.get("speed"))
    return max(0.01, engine_speed or top_level_speed or 1.0)


def _clip_target_subtitle_ids(clip: dict) -> list[str]:
    return [
        str(value)
        for value in (
            clip.get("target_subtitle_ids")
            or ([clip.get("subtitle_id")] if clip.get("subtitle_id") else [])
        )
        if str(value)
    ]


def _protected_speech_exceeds_window(
    candidate_clips: list[dict],
    audio,
    *,
    earliest_start_ms: int,
    latest_end_ms: int,
    tolerance_ms: int,
) -> bool:
    """Classify only actual protected-word overflow after local safe edits."""

    source_onset_ms, protected_speech_end_ms = (
        _candidate_word_anchor_and_protected_end(audio)
    )
    audible_bounds = candidate_audible_timeline_bounds(
        candidate_clips,
        speech_start_ms=source_onset_ms,
        speech_end_ms=protected_speech_end_ms,
    )
    if audible_bounds is None:
        return False
    return bool(
        audible_bounds[0] < earliest_start_ms - tolerance_ms
        or audible_bounds[1] > latest_end_ms + tolerance_ms
    )


def _current_group_candidate_clips(
    draft,
    *,
    group_id: str,
    candidate_id: str,
    target_subtitle_ids: list[str],
) -> list[dict]:
    """Return an exact, already-formal projection for one current group."""

    expected = set(target_subtitle_ids)
    clips = [
        dict(raw)
        for raw in draft.timeline_clips
        if raw.get("track_id") == "dub"
        and int(raw.get("dub_lane") or 0) == 0
        and str(raw.get("candidate_id") or raw.get("result_id") or "")
        == candidate_id
        and (
            not raw.get("dubbing_group_id")
            or str(raw.get("dubbing_group_id") or "") == group_id
        )
    ]
    coverage = {
        subtitle_id
        for clip in clips
        for subtitle_id in _clip_target_subtitle_ids(clip)
    }
    return clips if clips and coverage == expected else []


def _target_owned_projection_fingerprint(
    draft,
    *,
    target_subtitle_ids: list[str],
) -> str:
    """Fingerprint only the current clips that an atomic adoption may replace."""

    targets = set(target_subtitle_ids)
    owned = [
        dict(raw)
        for raw in draft.timeline_clips
        if raw.get("track_id") == "dub"
        and not raw.get("manual_history_copy")
        and bool(set(_clip_target_subtitle_ids(raw)) & targets)
    ]
    return hashlib.sha256(
        json.dumps(
            owned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _exact_clip_projection_fingerprint(clips: list[dict]) -> str:
    """Bind an atomic supporting edit to the exact clips it observed."""

    return hashlib.sha256(
        json.dumps(
            sorted(
                (dict(clip) for clip in clips),
                key=lambda clip: str(clip.get("clip_id") or ""),
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _staged_candidate_projection(
    *,
    candidate_id: str,
    target_projection_fingerprint: str,
    clips: list[dict],
) -> DubbingStagedCandidateProjection:
    """Serialize the final candidate projection without retaining live Draft data."""

    fields = {
        "clip_id", "candidate_id", "track_id", "status",
        "start_ms", "end_ms", "source_start_ms", "source_end_ms",
        "target_subtitle_ids", "subtitle_id", "dubbing_group_id", "result_id",
        "task_id", "generation_id", "cue_id", "target_start_ms", "target_end_ms",
        "tts_target_text", "source_cue_ids", "dub_lane", "intentional_overlap",
        "media_source_clip_id", "dubbing_slice_index", "dubbing_slice_count",
        "dubbing_alignment_word_ids", "dubbing_timeline_gap_before_ms", "audio_sha256",
    }
    staged = [
        DubbingStagedCandidateClip.model_validate(
            {key: value for key, value in clip.items() if key in fields}
        )
        for clip in clips
    ]
    serialized_clips = [
        clip.model_dump(exclude_none=True)
        for clip in staged
    ]
    return DubbingStagedCandidateProjection(
        candidate_id=candidate_id,
        target_projection_fingerprint=target_projection_fingerprint,
        candidate_clip_projection_fingerprint=candidate_clip_projection_fingerprint(
            serialized_clips
        ),
        clips=staged,
    )


def _staged_projection_runtime_clips(
    draft,
    projection: DubbingStagedCandidateProjection,
) -> list[dict]:
    """Resolve a managed audio path only while validating or adopting a stage."""

    path = tts_pipeline.generated_candidate_audio_path(
        draft,
        projection.candidate_id,
    )
    if path is None:
        return []
    return [
        {**clip.model_dump(exclude_none=True), "audio_path": str(path)}
        for clip in projection.clips
    ]


def _current_group_content_evidence_clips(
    draft,
    *,
    group,
    frozen: DubbingCandidateCqcInput,
    candidate_id: str,
) -> list[dict]:
    """Return the current playback projection eligible for retained evidence.

    A staged candidate is not in the timeline yet. It is still a valid
    playback source when its semantic handoff, serialized clips, and target
    projection are all current.
    """

    timeline_clips = _current_group_working_candidate_clips(
        draft,
        group_id=group.group_id,
        candidate_id=candidate_id,
        target_subtitle_ids=list(group.subtitle_ids),
    )
    if timeline_clips:
        return timeline_clips

    report = next(
        (
            item
            for item in draft.dubbing_production.candidate_reports
            if item.candidate_id == candidate_id
        ),
        None,
    )
    stage = report.staged_candidate_projection if report is not None else None
    audit = report.semantic_boundary_audit if report is not None else None
    serialized_clips = (
        [clip.model_dump(exclude_none=True) for clip in stage.clips]
        if stage is not None else []
    )
    if (
        report is None
        or stage is None
        or audit is None
        or report.source_revision != frozen.source_revision
        or report.plan_revision != frozen.plan_revision
        or report.group_id != group.group_id
        or stage.candidate_id != candidate_id
        or audit.source_revision != frozen.source_revision
        or audit.plan_revision != frozen.plan_revision
        or audit.candidate_id != candidate_id
        or audit.audio_sha256 != frozen.audio_sha256
        or report.evidence_fingerprint != domain.candidate_evidence_fingerprint(frozen)
        or audit.candidate_evidence_fingerprint != report.evidence_fingerprint
        or stage.candidate_clip_projection_fingerprint
        != candidate_clip_projection_fingerprint(serialized_clips)
        or audit.candidate_clip_projection_fingerprint
        != stage.candidate_clip_projection_fingerprint
        or _target_owned_projection_fingerprint(
            draft,
            target_subtitle_ids=list(group.subtitle_ids),
        ) != stage.target_projection_fingerprint
    ):
        return []
    staged_clips = _staged_projection_runtime_clips(draft, stage)
    coverage = {
        subtitle_id
        for clip in staged_clips
        for subtitle_id in _clip_target_subtitle_ids(clip)
    }
    if (
        not staged_clips
        or coverage != set(group.subtitle_ids)
        or any(
            clip.get("track_id") != "dub"
            or clip.get("status") != "ready"
            or clip.get("candidate_id") != candidate_id
            or clip.get("dubbing_group_id") != group.group_id
            or not clip.get("audio_path")
            for clip in staged_clips
        )
    ):
        return []
    return staged_clips


def _is_timeline_position_only_replacement(
    original: dict,
    replacement: dict,
) -> bool:
    """A supporting clip may move, but its media and ownership stay immutable."""

    mutable_fields = {"start_ms", "end_ms", "dub_lane"}
    return {
        key: value for key, value in original.items() if key not in mutable_fields
    } == {
        key: value for key, value in replacement.items() if key not in mutable_fields
    }


def _changed_adjacent_supporting_clips(
    original_draft,
    working_draft,
    *,
    group_id: str,
) -> list[dict]:
    """Return only neighboring current clips moved by bounded reflow."""

    original_by_id = {
        str(clip.get("clip_id") or ""): dict(clip)
        for clip in original_draft.timeline_clips
    }
    supporting: list[dict] = []
    for raw in working_draft.timeline_clips:
        clip = dict(raw)
        clip_id = str(clip.get("clip_id") or "")
        original = original_by_id.get(clip_id)
        if (
            not clip_id
            or original is None
            or str(clip.get("dubbing_group_id") or "") == group_id
            or (
                int(original.get("start_ms") or 0)
                == int(clip.get("start_ms") or 0)
                and int(original.get("end_ms") or 0)
                == int(clip.get("end_ms") or 0)
                and int(original.get("dub_lane") or 0)
                == int(clip.get("dub_lane") or 0)
            )
        ):
            continue
        if not _is_timeline_position_only_replacement(original, clip):
            return []
        supporting.append(clip)
    return supporting


def _without_target_owned_formal_clips(
    draft,
    *,
    target_subtitle_ids: list[str],
):
    """Build an in-memory replacement projection without the current take.

    The live Draft is unchanged until the later atomic commit. Manual history
    copies are audition material rather than the current formal result and are
    deliberately retained.
    """

    targets = set(target_subtitle_ids)
    timeline_clips = [
        dict(raw)
        for raw in draft.timeline_clips
        if not (
            raw.get("track_id") == "dub"
            and not raw.get("manual_history_copy")
            and bool(set(_clip_target_subtitle_ids(raw)) & targets)
        )
    ]
    return draft.model_copy(update={"timeline_clips": timeline_clips})


def _source_pause_rhythm_for_group(
    draft,
    *,
    plan,
    group,
) -> list[dubbing_gap_adjudication.SourcePauseRhythmEvidence]:
    """Return meaningful source-word pauses for the group's original speech."""

    transcription = getattr(draft, "transcription", None)
    if transcription is None:
        return []
    units_by_id = {item.unit_id: item for item in plan.semantic_units}
    source_word_ids = {
        word_id
        for unit_id in group.unit_ids
        for word_id in (units_by_id[unit_id].source_word_ids if unit_id in units_by_id else [])
    }
    words = sorted(
        (word for word in transcription.words if word.word_id in source_word_ids),
        key=lambda word: (word.start_ms, word.end_ms, word.word_id),
    )
    return [
        dubbing_gap_adjudication.SourcePauseRhythmEvidence(
            duration_ms=right.start_ms - left.end_ms,
            right_word_start_ms=right.start_ms,
        )
        for left, right in zip(words, words[1:])
        if right.start_ms - left.end_ms >= 200
    ]


def _with_trimmed_automatic_outer_gaps(
    draft,
    *,
    candidate_id: str,
    frozen,
    reviewed_gaps,
    padding_ms: int = 80,
    preserve_existing_placement: bool = False,
    preserve_verified_edge_edit: bool = False,
):
    """Return a projection with only proven leading/trailing silence removed."""

    leading = next(
        (
            gap
            for gap in reviewed_gaps
            if gap.kind == "leading" and gap.edit_decision in {"remove", "shorten"} and gap.safe_edit_boundary is True
        ),
        None,
    )
    trailing = next(
        (
            gap
            for gap in reviewed_gaps
            if gap.kind == "trailing" and gap.edit_decision in {"remove", "shorten"} and gap.safe_edit_boundary is True
        ),
        None,
    )
    clips = [dict(item) for item in draft.timeline_clips]
    indexes = [
        index
        for index, clip in enumerate(clips)
        if clip.get("track_id") == "dub"
        and str(clip.get("candidate_id") or clip.get("result_id") or "") == candidate_id
    ]
    if not indexes:
        return draft
    ordered = sorted(
        indexes,
        key=lambda index: (
            int(clips[index].get("source_start_ms") or 0),
            int(clips[index].get("start_ms") or 0),
        ),
    )
    padding = max(0, int(padding_ms))
    changed = False
    aligned_words = list(getattr(frozen.audio, "aligned_words", []) or [])

    index = ordered[0]
    clip = clips[index]
    current_source_start = int(clip.get("source_start_ms") or 0)
    current_source_end = int(
        clip.get("source_end_ms")
        or current_source_start
        + max(
            1,
            int(clip.get("end_ms") or 0)
            - int(clip.get("start_ms") or 0),
        )
    )
    speech_start = int(
        frozen.audio.speech_start_ms
        if frozen.audio.speech_start_ms is not None
        else leading.end_ms
        if leading is not None
        else current_source_start
    )
    if aligned_words:
        speech_start = min(
            speech_start,
            min(word.start_ms for word in aligned_words),
        )
    desired_source_start = max(0, speech_start - padding)
    if (
        preserve_verified_edge_edit
        and aligned_words
        and speech_start < current_source_start
        <= min(word.start_ms for word in aligned_words)
    ):
        # The edited take still contains every target word. Earlier VAD may
        # be a deliberately removed filler; padding repair must not revive it.
        desired_source_start = current_source_start
    if (
        (leading is not None and desired_source_start > current_source_start)
        or desired_source_start < current_source_start
    ):
        timeline_start_ms = max(
            0,
            (
                int(clip.get("start_ms") or 0)
                + desired_source_start
                - current_source_start
                if preserve_existing_placement
                else int(frozen.target_start_ms) - speech_start + desired_source_start
            ),
        )
        clip["source_start_ms"] = desired_source_start
        clip["start_ms"] = timeline_start_ms
        # A source-edge change must retain the same source tail. Otherwise a
        # later generic normalizer resolves the inconsistent durations by
        # silently cutting final words.
        clip["end_ms"] = timeline_start_ms + max(
            1,
            current_source_end - desired_source_start,
        )
        clip["alignment_lead_ms"] = speech_start - desired_source_start
        changed = True

    index = ordered[-1]
    clip = clips[index]
    source_start = int(clip.get("source_start_ms") or 0)
    current_source_end = int(
        clip.get("source_end_ms") or source_start + int(clip.get("end_ms") or 0) - int(clip.get("start_ms") or 0)
    )
    speech_end = int(
        frozen.audio.speech_end_ms
        if frozen.audio.speech_end_ms is not None
        else trailing.start_ms
        if trailing is not None
        else current_source_end
    )
    if aligned_words:
        speech_end = max(
            speech_end,
            max(word.end_ms for word in aligned_words),
        )
    desired_source_end = max(
        source_start + 1,
        min(
            int(
                getattr(
                    frozen.audio,
                    "duration_ms",
                    max(current_source_end, speech_end + padding),
                )
            ),
            speech_end + padding,
        ),
    )
    if (
        preserve_verified_edge_edit
        and aligned_words
        and max(word.end_ms for word in aligned_words)
        <= current_source_end < speech_end
    ):
        desired_source_end = current_source_end
    should_shrink = trailing is not None and desired_source_end < current_source_end
    should_expand = desired_source_end > current_source_end
    if should_shrink or should_expand:
        clip["source_end_ms"] = desired_source_end
        clip["end_ms"] = int(clip.get("start_ms") or 0) + desired_source_end - source_start
        clip["alignment_trail_ms"] = max(
            0,
            desired_source_end - speech_end,
        )
        changed = True

    if not changed:
        return draft
    for index in ordered:
        clips[index].pop("timeline_edit_gate", None)
    return draft.model_copy(update={"timeline_clips": clips})


def _candidate_word_anchor_and_protected_end(audio) -> tuple[int | None, int | None]:
    """Choose a placement onset without weakening the trim safety envelope.

    A positive-duration forced-alignment word is the finest available source
    onset, so it wins when VAD and alignment disagree.  VAD still contributes
    to the protected end and outer-gap trimming continues to retain the union
    of both evidence sources.
    """

    return candidate_source_speech_bounds(audio)


def _with_locked_candidate_source_onset(
    draft,
    *,
    candidate_id: str,
    speech_start_ms: int | None,
    speech_end_ms: int | None,
    target_start_ms: int,
):
    """Translate a new candidate so measured first speech starts at its target.

    This is deliberately a whole-candidate translation.  It never trims a
    word or edits a neighbour to compensate for an overlong rendered take.
    Callers use it only for a fresh replacement; an existing formal or manual
    placement keeps its current audible anchor.
    """

    if speech_start_ms is None or speech_end_ms is None:
        return draft
    clips = [dict(item) for item in draft.timeline_clips]
    indexes = [
        index
        for index, clip in enumerate(clips)
        if clip.get("track_id") == "dub"
        and str(clip.get("candidate_id") or clip.get("result_id") or "")
        == candidate_id
    ]
    candidate_clips = [clips[index] for index in indexes]
    audible_bounds = candidate_audible_timeline_bounds(
        candidate_clips,
        speech_start_ms=int(speech_start_ms),
        speech_end_ms=int(speech_end_ms),
    )
    if audible_bounds is None:
        return draft
    shift_ms = int(target_start_ms) - audible_bounds[0]
    if not shift_ms:
        return draft
    for index in indexes:
        clips[index]["start_ms"] = int(clips[index].get("start_ms") or 0) + shift_ms
        clips[index]["end_ms"] = int(clips[index].get("end_ms") or 0) + shift_ms
        clips[index].pop("timeline_edit_gate", None)
    return draft.model_copy(update={"timeline_clips": clips})


def _with_restored_retained_internal_gaps(
    draft,
    *,
    candidate_id: str,
    reviewed_gaps,
):
    """Restore candidate pauses that an older edit incorrectly removed."""

    clips = [dict(item) for item in draft.timeline_clips]
    indexes = sorted(
        (
            index
            for index, clip in enumerate(clips)
            if clip.get("track_id") == "dub"
            and str(clip.get("candidate_id") or clip.get("result_id") or "") == candidate_id
        ),
        key=lambda index: (
            int(clips[index].get("source_start_ms") or 0),
            int(clips[index].get("start_ms") or 0),
        ),
    )
    if len(indexes) < 2:
        return draft
    retained = [
        gap
        for gap in reviewed_gaps
        if gap.kind == "internal" and gap.edit_decision == "retain" and gap.safe_edit_boundary is True
    ]
    changed = False
    for position, (left_index, right_index) in enumerate(zip(indexes, indexes[1:])):
        left = clips[left_index]
        right = clips[right_index]
        left_source_end_ms = int(left.get("source_end_ms") or 0)
        right_source_start_ms = int(right.get("source_start_ms") or 0)
        missing_ms = right_source_start_ms - left_source_end_ms
        if missing_ms <= 0 or not any(
            gap.start_ms < right_source_start_ms and gap.end_ms > left_source_end_ms for gap in retained
        ):
            continue

        old_left_end_ms = int(left.get("end_ms") or 0)
        right_start_ms = int(right.get("start_ms") or 0)
        available_timeline_gap_ms = max(0, right_start_ms - old_left_end_ms)
        left["source_end_ms"] = right_source_start_ms
        left["end_ms"] = old_left_end_ms + missing_ms
        left["alignment_trail_ms"] = max(
            0,
            int(left.get("alignment_trail_ms") or 0) + missing_ms,
        )
        shift_ms = max(0, missing_ms - available_timeline_gap_ms)
        if shift_ms:
            for later_index in indexes[position + 1 :]:
                later = clips[later_index]
                later["start_ms"] = int(later.get("start_ms") or 0) + shift_ms
                later["end_ms"] = int(later.get("end_ms") or 0) + shift_ms
        right["dubbing_timeline_gap_before_ms"] = max(
            0,
            int(right.get("start_ms") or 0) - int(left.get("end_ms") or 0),
        )
        changed = True

    if not changed:
        return draft
    for index in indexes:
        clips[index].pop("timeline_edit_gate", None)
    return draft.model_copy(update={"timeline_clips": clips})


def _fit_trailing_safety_margin(clips, audio, *, latest_end_ms: int):
    """Reduce only proven trailing silence when the next group limits padding."""
    result = [dict(clip) for clip in clips]
    if not result or audio.speech_end_ms is None:
        return result
    words = [word for word in audio.aligned_words if word.end_ms > word.start_ms]
    if not words:
        return result
    tail = max(result, key=lambda clip: int(clip.get("end_ms") or 0))
    excess = max(0, int(tail.get("end_ms") or 0) - latest_end_ms)
    protected_end = max(audio.speech_end_ms, max(word.end_ms for word in words))
    source_end = int(tail.get("source_end_ms") or 0)
    if excess and source_end - excess >= protected_end:
        tail["source_end_ms"] = source_end - excess
        tail["end_ms"] = int(tail["end_ms"]) - excess
        tail["alignment_trail_ms"] = source_end - excess - protected_end
    return result


def _fit_leading_safety_margin(clips, audio, *, earliest_start_ms: int):
    """Crop only a new candidate's proven leading silence before a neighbour."""

    result = [dict(clip) for clip in clips]
    if not result:
        return result
    protected_start, _protected_end = candidate_protected_speech_bounds(audio)
    if protected_start is None:
        return result
    head = min(result, key=lambda clip: int(clip.get("start_ms") or 0))
    excess = max(0, int(earliest_start_ms) - int(head.get("start_ms") or 0))
    source_start = int(head.get("source_start_ms") or 0)
    if excess and source_start + excess <= protected_start:
        head["source_start_ms"] = source_start + excess
        head["start_ms"] = int(head.get("start_ms") or 0) + excess
        head["alignment_lead_ms"] = protected_start - (source_start + excess)
    return result


def _shorten_safe_gaps_to_fit(gaps, words, *, required_ms: int):
    """Use only proven removable cores; keep phoneme padding on both sides."""
    remaining = max(0, required_ms)
    result = []
    for gap in gaps:
        cut = dubbing_candidate_alignment.safe_internal_gap_cut(gap, words)
        if (remaining and gap.safe_edit_boundary is True and gap.edit_decision == "retain"
                and gap.semantic_role in {"continuous_phrase", "semantic_boundary"}
                and cut is not None):
            removed = cut[1] - cut[0]
            gap = gap.model_copy(update={
                "edit_decision": "remove",
                "retained_duration_ms": max(0, gap.duration_ms - removed),
                "decision_reason": "当前组时间不足，只压缩词级证据允许的停顿核心，两侧保留字音安全余量。",
                "review_evidence_ids": [*gap.review_evidence_ids, "candidate-window-capacity-v1"],
            })
            remaining = max(0, remaining - removed)
        result.append(gap)
    return result


def _with_source_rhythm_spacing(
    draft,
    *,
    candidate_id: str,
    reviewed_gaps,
    aligned_words,
    source_pauses,
    expected_spoken_text: str,
    latest_end_ms: int | None = None,
):
    """Place later phrases on matching source-speech pause anchors.

    The candidate's own retained pause stays intact.  When that pause is
    shorter than the source speaker's pause, the missing room is inserted at
    an existing safe slice boundary instead of accumulating at sentence end.
    """

    source_pause_by_pair = dubbing_gap_adjudication.map_source_pause_rhythm(
        expected_spoken_text,
        list(aligned_words),
        list(source_pauses),
    )
    if not source_pause_by_pair:
        return draft

    clips = [dict(item) for item in draft.timeline_clips]
    indexes = sorted(
        (
            index
            for index, clip in enumerate(clips)
            if clip.get("track_id") == "dub"
            and str(clip.get("candidate_id") or clip.get("result_id") or "")
            == candidate_id
        ),
        key=lambda index: (
            int(clips[index].get("source_start_ms") or 0),
            int(clips[index].get("start_ms") or 0),
        ),
    )
    if len(indexes) < 2:
        return draft

    retained = [
        gap
        for gap in reviewed_gaps
        if gap.kind == "internal"
        and gap.edit_decision == "retain"
        and gap.safe_edit_boundary is True
    ]
    changed = False
    for gap in retained:
        left_word, right_word = dubbing_gap_adjudication.adjacent_words(
            gap,
            list(aligned_words),
        )
        if left_word is None or right_word is None:
            continue
        source_pause = source_pause_by_pair.get(
            (left_word.word_id, right_word.word_id)
        )
        if source_pause is None:
            continue

        left_position = next(
            (
                position
                for position, index in enumerate(indexes)
                if int(clips[index].get("source_start_ms") or 0)
                <= left_word.start_ms
                < int(clips[index].get("source_end_ms") or 0)
            ),
            None,
        )
        right_position = next(
            (
                position
                for position, index in enumerate(indexes)
                if int(clips[index].get("source_start_ms") or 0)
                <= right_word.start_ms
                < int(clips[index].get("source_end_ms") or 0)
            ),
            None,
        )
        if (
            left_position is None
            or right_position is None
            or right_position <= left_position
        ):
            continue

        right_index = indexes[right_position]
        right_clip = clips[right_index]
        current_right_word_start_ms = (
            int(right_clip.get("start_ms") or 0)
            + right_word.start_ms
            - int(right_clip.get("source_start_ms") or 0)
        )
        shift_ms = max(
            0,
            source_pause.right_word_start_ms - current_right_word_start_ms,
        )
        if latest_end_ms is not None:
            # Source pauses guide rhythm; they cannot enlarge this group's
            # delivery window or push the following phrase out of its slot.
            remaining_room = max(
                0,
                latest_end_ms - max(int(clips[i].get("end_ms") or 0) for i in indexes),
            )
            shift_ms = min(shift_ms, remaining_room)
        if not shift_ms:
            continue
        right_clip["dubbing_timeline_gap_before_ms"] = (
            max(0, int(right_clip.get("dubbing_timeline_gap_before_ms") or 0))
            + shift_ms
        )
        for later_index in indexes[right_position:]:
            later = clips[later_index]
            later["start_ms"] = int(later.get("start_ms") or 0) + shift_ms
            later["end_ms"] = int(later.get("end_ms") or 0) + shift_ms
        changed = True

    if not changed:
        return draft
    for index in indexes:
        clips[index].pop("timeline_edit_gate", None)
    return draft.model_copy(update={"timeline_clips": clips})


def _current_group_working_candidate_clips(
    draft,
    *,
    group_id: str,
    candidate_id: str,
    target_subtitle_ids: list[str],
) -> list[dict]:
    """Return one exact candidate projection from any audition lane."""

    expected = set(target_subtitle_ids)
    clips = [
        dict(raw)
        for raw in draft.timeline_clips
        if raw.get("track_id") == "dub"
        and str(raw.get("candidate_id") or raw.get("result_id") or "")
        == candidate_id
        and (
            not raw.get("dubbing_group_id")
            or str(raw.get("dubbing_group_id") or "") == group_id
        )
    ]
    # media_source_clip_id describes media provenance, not deletion. A razor
    # edit keeps the first playable slice under the original ID; later slices
    # can reference it without removing its speech from the listening sequence.
    clips = [clip for clip in clips if not any(
        child.get("media_source_clip_id") == clip.get("clip_id")
        and child.get("clip_id") != clip.get("clip_id")
        and int(child.get("source_start_ms") or 0) < int(clip.get("source_end_ms") or 0)
        and int(clip.get("source_start_ms") or 0) < int(child.get("source_end_ms") or 0)
        and not (int(clip.get("dubbing_slice_index") or 0) > 0
                 and int(clip.get("dubbing_slice_count") or 0) > 1)
        for child in clips
    )]
    coverage = {
        subtitle_id
        for clip in clips
        for subtitle_id in _clip_target_subtitle_ids(clip)
    }
    if not clips or coverage != expected:
        return []
    # History adoption binds the exact candidate and subtitle set, but need
    # not carry a managed group ID. Resolve it only after that exact coverage
    # check so the same edited take can enter the typed semantic-review stage.
    return [{**clip, "dubbing_group_id": group_id} for clip in clips]


def _has_exact_current_formal_candidate_binding(
    draft,
    *,
    group,
    candidate_id: str,
    result_id: str,
    source_cue_ids: list[str],
) -> bool:
    """Allow legacy manual history only after exact current formal placement."""

    clips = [
        dict(raw)
        for raw in draft.timeline_clips
        if raw.get("track_id") == "dub"
        and int(raw.get("dub_lane") or 0) == 0
        and raw.get("status") == "ready"
        and str(raw.get("candidate_id") or "") == candidate_id
        and str(raw.get("result_id") or "") == result_id
    ]
    if not clips:
        return False
    targets = {
        subtitle_id
        for clip in clips
        for subtitle_id in _clip_target_subtitle_ids(clip)
    }
    sources = {
        str(cue_id)
        for clip in clips
        for cue_id in (clip.get("source_cue_ids") or [])
        if str(cue_id)
    }
    return targets == set(group.subtitle_ids) and sources == set(source_cue_ids)


def _existing_formal_candidate_ids_for_group(
    draft,
    *,
    target_subtitle_ids: list[str],
) -> list[str]:
    """Find durable main-lane candidates that exactly cover one plan group."""

    expected = set(target_subtitle_ids)
    clips_by_identity: dict[str, list[dict]] = {}
    for raw in draft.timeline_clips:
        if raw.get("track_id") != "dub" or int(raw.get("dub_lane") or 0) != 0:
            continue
        identity = str(raw.get("candidate_id") or raw.get("result_id") or "")
        if not identity:
            continue
        clips_by_identity.setdefault(identity, []).append(dict(raw))
    matches = []
    for identity, clips in clips_by_identity.items():
        coverage = {
            subtitle_id
            for clip in clips
            for subtitle_id in _clip_target_subtitle_ids(clip)
        }
        if coverage == expected:
            matches.append(identity)
    return matches


def _existing_formal_matches_by_group(draft) -> dict[str, tuple[str, list[dict]]]:
    """Return only unambiguous exact main-lane coverage for current groups."""

    plan = draft.dubbing_production.active_plan
    if plan is None:
        return {}
    clips_by_identity: dict[str, list[dict]] = {}
    for raw in draft.timeline_clips:
        if raw.get("track_id") != "dub" or int(raw.get("dub_lane") or 0) != 0:
            continue
        identity = str(raw.get("candidate_id") or raw.get("result_id") or "")
        if identity:
            clips_by_identity.setdefault(identity, []).append(dict(raw))
    result: dict[str, tuple[str, list[dict]]] = {}
    for group in plan.groups:
        expected = set(group.subtitle_ids)
        matches = [
            (identity, clips)
            for identity, clips in clips_by_identity.items()
            if {
                subtitle_id
                for clip in clips
                for subtitle_id in _clip_target_subtitle_ids(clip)
            }
            == expected
        ]
        if len(matches) == 1:
            result[group.group_id] = matches[0]
    return result


def _stable_json_fingerprint(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _group_source_cue_ids(draft, plan, group) -> list[str]:
    """Return the ordered source binding shared by generation and parking."""

    units_by_id = {item.unit_id: item for item in plan.semantic_units}
    source_cue_ids = list(
        dict.fromkeys(
            str(cue_id)
            for unit_id in group.unit_ids
            for cue_id in (
                units_by_id[unit_id].source_cue_ids
                if unit_id in units_by_id
                else []
            )
            if str(cue_id)
        )
    )
    if source_cue_ids:
        return source_cue_ids
    subtitles_by_id = {
        item.subtitle_id: item for item in draft.localized_subtitles
    }
    return list(
        dict.fromkeys(
            str(cue_id)
            for subtitle_id in group.subtitle_ids
            if subtitle_id in subtitles_by_id
            for cue_id in subtitles_by_id[subtitle_id].source_cue_ids
            if str(cue_id)
        )
    )


def _spoken_text_fingerprint(value: str) -> str:
    return _stable_json_fingerprint(domain.transcript_pronunciation_tokens(value))


def _content_evidence_reference(evidence) -> str:
    """Stable public reference to one exact-byte independent ASR observation."""

    return ":".join(
        (
            "tts-content-evidence-v1",
            evidence.audio_sha256,
            evidence.engine_id,
            evidence.protocol,
        )
    )


def _build_existing_formal_acceptance(
    *,
    project_id: str,
    plan: DubbingGenerationPlan,
    group,
    candidate_id: str,
    clips: list[dict],
) -> DubbingExistingFormalAcceptance | None:
    """Validate immutable history + a passed gate before current-plan adoption."""

    gates = [clip.get("timeline_edit_gate") for clip in clips]
    if not gates or any(not isinstance(gate, dict) for gate in gates):
        return None
    raw_gate = gates[0]
    if any(item != raw_gate for item in gates[1:]):
        return None
    try:
        gate = DubbingTimelineEditGate.model_validate(raw_gate)
    except ValueError:
        return None
    projection_fingerprint = candidate_clip_projection_fingerprint(clips)
    if (
        gate.status != "passed"
        or gate.candidate_id != candidate_id
        or gate.candidate_clip_projection_fingerprint
        != projection_fingerprint
    ):
        return None

    result_ids = {
        str(clip.get("result_id") or "")
        for clip in clips
        if str(clip.get("result_id") or "")
    }
    if len(result_ids) != 1:
        return None
    result_id = next(iter(result_ids))
    history = history_store.get(result_id)
    if history is None or history.project_id != project_id:
        return None
    if not Path(str(history.output_path or "")).is_file():
        return None
    task = task_queue.get_task(history.task_id)
    if task is None or task.project_id != project_id:
        return None
    if str(getattr(task.status, "value", task.status)) != "success":
        return None
    task_target_ids = {
        str(value)
        for value in task.parameters.get(
            "video_localization_target_subtitle_ids",
            [],
        )
        if str(value)
    }
    if not task_target_ids and task.localized_subtitle_id:
        task_target_ids = {str(task.localized_subtitle_id)}
    if task_target_ids != set(group.subtitle_ids):
        return None
    task_text = str(task.parameters.get("text") or task.input_text or "")
    if (
        not task_text
        or domain.transcript_pronunciation_tokens(task_text)
        != domain.transcript_pronunciation_tokens(group.spoken_text)
    ):
        return None
    return DubbingExistingFormalAcceptance(
        source_revision=plan.source_revision,
        plan_revision=plan.plan_revision,
        group_id=group.group_id,
        candidate_id=candidate_id,
        result_id=result_id,
        target_subtitle_ids=list(group.subtitle_ids),
        spoken_text_fingerprint=_spoken_text_fingerprint(group.spoken_text),
        inherited_gate_fingerprint=_stable_json_fingerprint(
            gate.model_dump(mode="json")
        ),
        candidate_clip_projection_fingerprint=projection_fingerprint,
        created_at=now_iso(),
    )


def _bind_plan_input_to_snapshot(
    snapshot: DubbingProductionSnapshot,
    payload: DubbingGenerationPlanInput,
) -> DubbingGenerationPlanInput:
    snapshot_unit_ids = [item.unit_id for item in snapshot.semantic_units]
    incoming_unit_ids = [item.unit_id for item in payload.semantic_units]
    snapshot_boundary_ids = [item.boundary_id for item in snapshot.boundaries]
    incoming_boundary_ids = [item.boundary_id for item in payload.boundaries]
    if (
        snapshot_unit_ids != incoming_unit_ids
        or snapshot_boundary_ids != incoming_boundary_ids
    ):
        _raise_plan_snapshot_mismatch()

    reviewed_units = [
        _bind_semantic_unit(snapshot_unit, incoming_unit)
        for snapshot_unit, incoming_unit in zip(
            snapshot.semantic_units,
            payload.semantic_units,
        )
    ]
    reviewed_boundaries = [
        _bind_boundary(snapshot_boundary, incoming_boundary)
        for snapshot_boundary, incoming_boundary in zip(
            snapshot.boundaries,
            payload.boundaries,
        )
    ]
    return DubbingGenerationPlanInput(
        source_revision=snapshot.source_revision,
        semantic_units=reviewed_units,
        boundaries=reviewed_boundaries,
        policy=payload.policy,
    )


def _bind_semantic_unit(
    snapshot: DubbingSemanticUnit,
    incoming: DubbingSemanticUnit,
) -> DubbingSemanticUnit:
    editable = {
        "scene_id",
        "scene_end_ms",
        "speech_policy",
        "decision_reason_codes",
    }
    if snapshot.model_dump(exclude=editable) != incoming.model_dump(
        exclude=editable
    ):
        _raise_plan_snapshot_mismatch()
    return snapshot.model_copy(
        update={
            **{
                field: getattr(incoming, field)
                for field in editable
                if field != "decision_reason_codes"
            },
            "decision_reason_codes": list(
                dict.fromkeys(
                    [
                        *snapshot.decision_reason_codes,
                        *incoming.decision_reason_codes,
                    ]
                )
            ),
        }
    )


def _bind_boundary(
    snapshot: DubbingBoundaryEvidence,
    incoming: DubbingBoundaryEvidence,
) -> DubbingBoundaryEvidence:
    editable = {"same_scene", "semantic_relation", "no_break_with_next"}
    if snapshot.model_dump(exclude=editable) != incoming.model_dump(
        exclude=editable
    ):
        _raise_plan_snapshot_mismatch()
    return snapshot.model_copy(
        update={field: getattr(incoming, field) for field in editable}
    )


def _audit_candidate_timeline(
    project_id: str,
    draft,
) -> DubbingTimelineAuditReport:
    audio_sha256_by_clip_id = dubbing_media.current_timeline_audio_sha256s(
        project_id,
        draft,
    )
    payload = domain.build_current_timeline_audit_input(
        draft,
        current_timeline_revision=domain.dubbing_timeline_projection_revision(
            draft
        ),
        phase="timeline",
        current_audio_sha256_by_clip_id=audio_sha256_by_clip_id,
    )
    return domain.audit_timeline(payload)


def _candidate_generation_attempt(
    draft,
    *,
    generation_task_id: str | None,
    result_id: str,
) -> int:
    """Read the durable workflow attempt for one generated result."""

    for workflow in draft.tts_tasks:
        if not (
            (
                generation_task_id
                and workflow.generation_task_id == generation_task_id
            )
            or workflow.result_id == result_id
        ):
            continue
        generation_stage = next(
            (stage for stage in workflow.stages if stage.kind == "generation"),
            None,
        )
        if generation_stage is None:
            continue
        return max(
            1,
            int(
                generation_stage.parameters.get(
                    "video_localization_generation_attempt"
                )
                or 1
            ),
        )
    return 1


def _replacement_worsens_continuous_boundary(
    before: list[domain.DubbingContinuousBoundaryUnderfill],
    after: list[domain.DubbingContinuousBoundaryUnderfill],
    *,
    tolerance_ms: int,
) -> bool:
    """Reject only a retry that materially enlarges the same excess gap."""

    if not after:
        return False
    before_excess_ms = max((item.excess_gap_ms for item in before), default=0)
    after_excess_ms = max(item.excess_gap_ms for item in after)
    return after_excess_ms > before_excess_ms + max(0, int(tolerance_ms))


def _new_blocking_timeline_finding(
    before: DubbingTimelineAuditReport,
    after: DubbingTimelineAuditReport,
    *,
    ignored_codes: set[str] | None = None,
):
    findings = _new_blocking_timeline_findings(
        before,
        after,
        ignored_codes=ignored_codes,
    )
    return findings[0] if findings else None


def _new_blocking_timeline_findings(
    before: DubbingTimelineAuditReport,
    after: DubbingTimelineAuditReport,
    *,
    ignored_codes: set[str] | None = None,
):
    ignored = ignored_codes or set()
    existing = {
        (item.code, tuple(item.entity_ids))
        for item in before.findings
        if item.severity == "blocking"
        and item.code != "TIMELINE_DUB_COVERAGE_MISSING"
        and item.code not in ignored
    }
    return [
        item
        for item in after.findings
        if item.severity == "blocking"
        and item.code != "TIMELINE_DUB_COVERAGE_MISSING"
        and item.code not in ignored
        and (item.code, tuple(item.entity_ids)) not in existing
    ]


def _new_semantic_blocking_timeline_finding(
    before_draft,
    before: DubbingTimelineAuditReport,
    after_draft,
    after: DubbingTimelineAuditReport,
    *,
    ignored_codes: set[str] | None = None,
):
    """Compare blockers by stable plan groups, not replaceable slice IDs."""

    ignored = ignored_codes or set()

    def finding_key(draft, finding):
        plan = draft.dubbing_production.active_plan
        groups = list(plan.groups if plan is not None else [])
        group_by_id = {group.group_id: group for group in groups}
        group_by_targets = {
            frozenset(group.subtitle_ids): group.group_id
            for group in groups
        }
        clips_by_id = {
            str(clip.get("clip_id") or ""): clip
            for clip in draft.timeline_clips
        }
        stable_entities: list[str] = []
        for entity_id in finding.entity_ids:
            clip = clips_by_id.get(str(entity_id))
            if clip is None:
                stable_entities.append(str(entity_id))
                continue
            group_id = str(clip.get("dubbing_group_id") or "")
            if group_id not in group_by_id:
                targets = frozenset(
                    str(value)
                    for value in (
                        clip.get("target_subtitle_ids")
                        or ([clip.get("subtitle_id")] if clip.get("subtitle_id") else [])
                    )
                    if str(value)
                )
                group_id = group_by_targets.get(targets, "")
            stable_entities.append(
                f"group:{group_id}" if group_id else str(entity_id)
            )
        if finding.code == "TIMELINE_UNEXPLAINED_DUB_OVERLAP":
            stable_entities.sort()
        return finding.code, tuple(stable_entities)

    existing = {
        finding_key(before_draft, item)
        for item in before.findings
        if item.severity == "blocking"
        and item.code != "TIMELINE_DUB_COVERAGE_MISSING"
        and item.code not in ignored
    }
    return next(
        (
            item
            for item in after.findings
            if item.severity == "blocking"
            and item.code != "TIMELINE_DUB_COVERAGE_MISSING"
            and item.code not in ignored
            and finding_key(after_draft, item) not in existing
        ),
        None,
    )


def _blocking_timeline_finding_count(
    report: DubbingTimelineAuditReport,
) -> int:
    return sum(
        item.severity == "blocking"
        and item.code != "TIMELINE_DUB_COVERAGE_MISSING"
        for item in report.findings
    )


def _raise_plan_snapshot_mismatch() -> None:
    raise AppException(
        409,
        "VIDEO_LOCALIZATION_DUBBING_PLAN_SNAPSHOT_MISMATCH",
        "配音计划包含不属于当前服务器快照的单元或客观证据，请刷新快照后重试。",
    )


__all__ = [
    "DubbingProductionApplicationService",
    "dubbing_production",
]
