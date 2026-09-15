"""Canonical semantic-group dubbing execution.

Single-group and whole-video runs enter here.  Whole-video execution is only
repeated single-group execution; it deliberately has no second batch policy,
model ladder, or audit loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any, Callable
from typing import Literal

from app.errors import AppException
from app.schemas.video_localization_dubbing_production import (
    DubbingProductionExecuteResponse,
    DubbingProductionGroupFailureRequest,
    DubbingProductionReviewMode,
)
from app.schemas.voice_studio import GenerateRequest, GenerationTask, TaskStatus
from app.services import task_queue, video_localization_tts_handoff


ExecutionScope = Literal["single_group", "all_remaining"]
logger = logging.getLogger(__name__)
PRIMARY_DUBBING_ENGINE_ID = "omnivoice"
DEFAULT_MAX_IN_FLIGHT_GROUPS = 2
_ACTIVE_MANAGED_WORKFLOW_STATUSES = {"prepared", "queued", "running", "generated"}
_ACTION_BY_GROUP_STAGE = {
    "ready_to_generate": "generate_candidate",
    "generating": "wait_for_generation",
    "needs_regeneration": "regenerate_candidate",
    "accepted": "complete",
    "deferred_manual_timing": "complete",
    "failed": "complete",
}


@dataclass(frozen=True)
class DubbingExecutionProjection:
    """Injected video-localization port used by the application executor."""

    read_production_run: Callable[[str], Any]
    get_video_localization: Callable[[str], Any]
    frozen_group_request: Callable[..., GenerateRequest | None]
    compatible_generated_identities: Callable[[Any, Any], list[str]]
    retry_runtime_fields: frozenset[str]
    reserve_single_tts_handoff: Callable[..., Any]
    build_single_tts_handoff: Callable[..., Any]
    finalize_generated_candidate: Callable[[str, str, str], Any]
    recover_and_finalize_generated_group: Callable[[str, str, list[str]], Any]
    find_continuous_boundary_underfill_groups: Callable[
        [str, list[str]], list[str]
    ]
    mark_workflow_placement_failed: Callable[..., Any]
    reconcile_workflow_terminal_states: Callable[[str], Any]
    record_group_failure: Callable[..., Any]
    decide_generation_speed: Callable[[Any, Any], Any]
    decide_capacity_repair_speed: Callable[..., Any] | None = None
    set_group_scheduling_priority: Callable[..., None] | None = None
    validate_recovery_decision: Callable[..., Any] | None = None
    assess_group_preflight: Callable[..., Any] | None = None
    read_completion: Callable[..., Any] | None = None
    group_evidence_context_fingerprint: Callable[[Any, Any, Any], str] | None = None


_projection: DubbingExecutionProjection | None = None
_background_submissions: set[asyncio.Task] = set()
_active_closeout_workflow_ids: set[str] = set()
_continuous_runs: dict[str, asyncio.Task] = {}
_project_execution_locks: dict[str, asyncio.Lock] = {}
_project_scheduling_locks: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class DubbingExecutionWindow:
    start_group_id: str | None = None
    end_group_id: str | None = None
    max_in_flight_groups: int = DEFAULT_MAX_IN_FLIGHT_GROUPS
    ordinary_speed_baseline: float | None = None
    resource_priority: Literal[
        "foreground_resume",
        "normal",
        "background",
    ] = "normal"


def _bounded_window(
    *,
    start_group_id: str | None = None,
    end_group_id: str | None = None,
    max_in_flight_groups: int = DEFAULT_MAX_IN_FLIGHT_GROUPS,
    ordinary_speed_baseline: float | None = None,
    resource_priority: Literal[
        "foreground_resume",
        "normal",
        "background",
    ] = "normal",
) -> DubbingExecutionWindow:
    return DubbingExecutionWindow(
        start_group_id=start_group_id,
        end_group_id=end_group_id,
        max_in_flight_groups=max(1, min(3, int(max_in_flight_groups))),
        ordinary_speed_baseline=(
            round(float(ordinary_speed_baseline), 2)
            if ordinary_speed_baseline is not None
            else None
        ),
        resource_priority=resource_priority,
    )


def _project_execution_lock(project_id: str) -> asyncio.Lock:
    lock = _project_execution_locks.get(project_id)
    if lock is None:
        lock = asyncio.Lock()
        _project_execution_locks[project_id] = lock
    return lock


def configure_projection(projection: DubbingExecutionProjection) -> None:
    global _projection
    _projection = projection


def _require_projection() -> DubbingExecutionProjection:
    if _projection is None:
        raise RuntimeError("video-localization dubbing projection is not configured")
    return _projection


async def _recover_and_finalize(
    projection: DubbingExecutionProjection,
    project_id: str,
    group_id: str,
    candidate_ids: list[str],
    review_mode: DubbingProductionReviewMode,
):
    if review_mode == "full":
        return await asyncio.to_thread(
            projection.recover_and_finalize_generated_group,
            project_id,
            group_id,
            candidate_ids,
        )
    return await asyncio.to_thread(
        projection.recover_and_finalize_generated_group,
        project_id,
        group_id,
        candidate_ids,
        review_mode=review_mode,
    )


async def _finalize_candidate(
    projection: DubbingExecutionProjection,
    project_id: str,
    candidate_id: str,
    group_id: str,
    review_mode: DubbingProductionReviewMode,
):
    if review_mode == "full":
        return await asyncio.to_thread(
            projection.finalize_generated_candidate,
            project_id,
            candidate_id,
            group_id,
        )
    return await asyncio.to_thread(
        projection.finalize_generated_candidate,
        project_id,
        candidate_id,
        group_id,
        review_mode=review_mode,
    )


def _semantic_boundary_handoff_response(
    *,
    scope: ExecutionScope,
    group_id: str | None,
) -> DubbingProductionExecuteResponse:
    """Keep a staged candidate with Agent semantic review out of failure paths."""

    return DubbingProductionExecuteResponse(
        status="needs_attention",
        scope=scope,
        group_id=group_id,
        message="当前组正在检查断句，已保留候选与证据；等待 Agent 处置后再继续。",
    )


def _capacity_recovery_handoff_response(
    *,
    scope: ExecutionScope,
    group_id: str | None,
) -> DubbingProductionExecuteResponse:
    """Expose proven physical overflow before any automatic TTS retry."""

    return DubbingProductionExecuteResponse(
        status="needs_attention",
        scope=scope,
        group_id=group_id,
        required_action="resolve_capacity",
        message=(
            "当前完整语音经过安全气口处理后仍超出实际时间窗。"
            "请先完成容量恢复决策；现有音频已保留，未自动重新生成。"
        ),
    )


async def _record_closeout_failure(
    projection: DubbingExecutionProjection,
    project_id: str,
    group_id: str,
    *,
    candidate_id: str | None,
    attempt_count: int,
    workflow_ids: list[str] | None = None,
    recovery_needed: bool = False,
    capacity_recovery_needed: bool = False,
) -> None:
    # Unrecoverable placement closes its receipt. Capacity recovery instead
    # reuses valid audio and keeps the receipt open for eventual adoption; the
    # read model exposes resolve_capacity once this closeout owner yields.
    if workflow_ids and not capacity_recovery_needed:
        await _close_unplaceable_workflow(
            projection,
            project_id,
            workflow_ids,
        )
    current = await asyncio.to_thread(
        projection.get_video_localization,
        project_id,
    )
    plan = current.dubbing_production.active_plan if current else None
    if plan is None:
        return
    await asyncio.to_thread(
        projection.record_group_failure,
        project_id,
        DubbingProductionGroupFailureRequest(
            source_revision=plan.source_revision,
            plan_revision=plan.plan_revision,
            group_id=group_id,
            candidate_id=candidate_id,
            title=(
                "配音需要容量恢复"
                if capacity_recovery_needed
                else "配音需要进一步恢复"
                if recovery_needed
                else "配音处理未完成"
            ),
            note=(
                "完整语音经过安全本地处理后仍超出实际时间窗；保留当前音频，等待容量恢复决策。"
                if capacity_recovery_needed else
                "整段恢复未能完成，或原始冻结参数不完整；保留当前音频，需确认语义分段或附近同说话人参考后继续。"
                if recovery_needed else
                "音频已经生成，但本地检查或落位尚未完成；保留当前音频，先恢复收尾，不代表需要重新生成。"
            ),
            reason_code=(
                "group_capacity_recovery_decision_required"
                if capacity_recovery_needed
                else "group_recovery_decision_required"
                if recovery_needed
                else "generated_result_closeout_unavailable"
            ),
            attempted_strategy_codes=["recover_generated_result", *(["whole_group_retry"] if recovery_needed and attempt_count > 1 else [])],
            attempt_count=max(1, int(attempt_count)),
        ),
    )


async def _close_unplaceable_workflow(
    projection: DubbingExecutionProjection,
    project_id: str,
    workflow_ids: list[str],
) -> None:
    for workflow_id in dict.fromkeys(value for value in workflow_ids if value):
        await asyncio.to_thread(
            projection.mark_workflow_placement_failed,
            project_id,
            workflow_id,
            error_code="TTS_PLACEMENT_REGENERATION_REQUIRED",
            error_message="音频已生成，本次落位未完成；请先复用现有音频检查绑定、剪辑和可用空间，不能仅凭落位失败重新生成。",
        )


def _task_review_mode(task: GenerationTask) -> DubbingProductionReviewMode:
    value = task.parameters.get("video_localization_dubbing_review_mode")
    if value in {"risk_based", "supervised"}:
        return value
    return "full"


def _task_execution_window(task: GenerationTask) -> DubbingExecutionWindow:
    return _bounded_window(
        start_group_id=task.parameters.get(
            "video_localization_execution_start_group_id"
        ),
        end_group_id=task.parameters.get(
            "video_localization_execution_end_group_id"
        ),
        max_in_flight_groups=int(
            task.parameters.get("video_localization_max_in_flight_groups")
            or DEFAULT_MAX_IN_FLIGHT_GROUPS
        ),
        ordinary_speed_baseline=task.parameters.get(
            "video_localization_ordinary_speed_baseline"
        ),
        resource_priority=task.parameters.get(
            "resource_priority",
            "normal",
        ),
    )


def _active_managed_group_ids(draft, group_ids: set[str]) -> set[str]:
    active: set[str] = set()
    if draft is None:
        return active
    for workflow in getattr(draft, "tts_tasks", []) or []:
        status = getattr(workflow, "status", None)
        status = getattr(status, "value", status)
        if status not in _ACTIVE_MANAGED_WORKFLOW_STATUSES:
            continue
        for stage in getattr(workflow, "stages", []) or []:
            parameters = getattr(stage, "parameters", {}) or {}
            if not isinstance(parameters, dict):
                continue
            group_id = str(
                parameters.get("video_localization_dubbing_group_id") or ""
            )
            if group_id in group_ids:
                active.add(group_id)
                break
    return active


def _continue_for_review_mode(
    project_id: str,
    review_mode: DubbingProductionReviewMode,
    window: DubbingExecutionWindow | None = None,
) -> None:
    execution_window = window or _bounded_window()
    if review_mode == "full":
        if execution_window == _bounded_window():
            _continue_all_remaining(project_id)
        else:
            _continue_all_remaining(project_id, window=execution_window)
    else:
        if execution_window == _bounded_window():
            _continue_all_remaining(project_id, review_mode)
        else:
            _continue_all_remaining(
                project_id,
                review_mode,
                window=execution_window,
            )


async def _queue_group_for_review_mode(
    project_id: str,
    *,
    draft,
    group,
    scope: ExecutionScope,
    attempt: int,
    review_mode: DubbingProductionReviewMode,
    workflow_id: str | None = None,
    generation_parameters: dict[str, Any] | None = None,
    window: DubbingExecutionWindow | None = None,
    frozen_retry_request: GenerateRequest | None = None,
    capacity_replaces_workflow_ids: list[str] | None = None,
):
    kwargs = {
        "draft": draft,
        "group": group,
        "scope": scope,
        "attempt": attempt,
    }
    if window is not None and window != _bounded_window():
        kwargs["window"] = window
    if workflow_id is not None:
        kwargs["workflow_id"] = workflow_id
    if generation_parameters is not None:
        kwargs["generation_parameters"] = generation_parameters
    if frozen_retry_request is not None:
        kwargs["frozen_retry_request"] = frozen_retry_request
    if capacity_replaces_workflow_ids is not None:
        kwargs["capacity_replaces_workflow_ids"] = (
            capacity_replaces_workflow_ids
        )
    if review_mode == "full":
        return await _queue_group(project_id, **kwargs)
    return await _queue_group(
        project_id,
        **kwargs,
        review_mode=review_mode,
    )


def _frozen_group_retry_request(draft, group, parameters: dict[str, Any] | None = None) -> GenerateRequest | None:
    """Read submitted controls, never infer a fresh take from current defaults."""
    return _frozen_group_retry_request_for_current_group(
        draft,
        group,
        parameters=parameters,
        allow_equivalent_plan_rebind=False,
    )


def _frozen_group_retry_request_for_current_group(
    draft,
    group,
    *,
    parameters: dict[str, Any] | None = None,
    allow_equivalent_plan_rebind: bool,
) -> GenerateRequest | None:
    """Thin executor proxy for the domain-owned frozen identity rule."""

    return _require_projection().frozen_group_request(
        draft,
        group,
        parameters=parameters,
        allow_equivalent_plan_rebind=allow_equivalent_plan_rebind,
    )


def _rebase_compatible_generation_stages(draft, group):
    """Yield immutable history stages whose frozen inputs still bind this group."""

    for workflow in getattr(draft, "tts_tasks", []) or []:
        if not getattr(workflow, "generation_task_id", None):
            continue
        for stage in getattr(workflow, "stages", []) or []:
            if getattr(stage, "kind", None) != "generation":
                continue
            parameters = getattr(stage, "parameters", {}) or {}
            if not isinstance(parameters, dict):
                continue
            if parameters.get("video_localization_dubbing_group_id") != group.group_id:
                continue
            request = _frozen_group_retry_request_for_current_group(
                draft,
                group,
                parameters=parameters,
                allow_equivalent_plan_rebind=True,
            )
            if request is not None:
                yield parameters, request


def _rebase_compatible_generated_identities(draft, group) -> list[str]:
    """Find durable outputs for an exact old-plan group after a local rebase.

    A text-only plan refresh deliberately leaves the old workflow immutable.
    When its frozen request still exactly binds the current group, its saved
    result is a close-out candidate, not a reason to submit TTS again.  The
    production facade revalidates the result/media before it can be adopted.
    """

    return _require_projection().compatible_generated_identities(draft, group)


def _same_retry_contract(expected: GenerateRequest, actual: GenerateRequest) -> bool:
    runtime_fields = _require_projection().retry_runtime_fields
    return expected.model_dump(mode="json", exclude=runtime_fields) == actual.model_dump(mode="json", exclude=runtime_fields)


def _has_current_durable_group_candidate(draft, progress) -> bool:
    """Require current frozen evidence before retrying a failed unplaced group.

    A production-run candidate ID can outlive deletion or rejection.  The
    failed-group retry is therefore allowed only when the current plan still
    owns matching frozen candidate evidence; the retry then reads the original
    submitted request instead of acquiring new defaults.
    """

    production = getattr(draft, "dubbing_production", None)
    plan = getattr(production, "active_plan", None)
    group_id = str(getattr(progress, "group_id", "") or "")
    candidate_ids = {
        str(value)
        for value in (getattr(progress, "candidate_ids", []) or [])
        if value
    }
    if plan is None or not group_id or not candidate_ids:
        return False
    return any(
        str(getattr(item, "candidate_id", "") or "") in candidate_ids
        and str(getattr(item, "group_id", "") or "") == group_id
        and str(getattr(item, "source_revision", "") or "")
        == str(getattr(plan, "source_revision", "") or "")
        and int(getattr(item, "plan_revision", 0) or 0)
        == int(getattr(plan, "plan_revision", 0) or 0)
        for item in (getattr(production, "candidate_inputs", []) or [])
    )


def _has_current_semantic_recovery_required(draft, progress) -> bool:
    """Identify a current Agent-requested whole-group replacement.

    A historical recovery report is not enough to reopen a group.  The report
    must belong to one of the run's current candidate identities and to the
    active source/plan pair; the caller separately verifies frozen candidate
    input before it can consume the single retry.
    """

    production = getattr(draft, "dubbing_production", None)
    plan = getattr(production, "active_plan", None)
    candidate_ids = {
        str(value)
        for value in (getattr(progress, "candidate_ids", []) or [])
        if value
    }
    if plan is None or not candidate_ids:
        return False

    def value(item, key: str, default=None):
        return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)

    for report in getattr(production, "candidate_reports", []) or []:
        audit = value(report, "semantic_boundary_audit")
        if (
            str(value(report, "candidate_id") or "") in candidate_ids
            and str(value(report, "group_id") or "")
            == str(getattr(progress, "group_id", "") or "")
            and str(value(report, "source_revision") or "")
            == str(getattr(plan, "source_revision", "") or "")
            and int(value(report, "plan_revision", 0) or 0)
            == int(getattr(plan, "plan_revision", 0) or 0)
            and str(value(audit, "status") or "") == "recovery_required"
        ):
            return True
    return False


def _has_current_capacity_recovery_required(draft, progress) -> bool:
    """Require the current candidate's durable capacity handoff."""

    production = getattr(draft, "dubbing_production", None)
    plan = getattr(production, "active_plan", None)
    candidate_ids = {
        str(value)
        for value in (getattr(progress, "candidate_ids", []) or [])
        if value
    }
    if plan is None or not candidate_ids:
        return False
    return any(
        str(getattr(failure, "candidate_id", "") or "") in candidate_ids
        and str(getattr(failure, "group_id", "") or "")
        == str(getattr(progress, "group_id", "") or "")
        and str(getattr(failure, "source_revision", "") or "")
        == str(getattr(plan, "source_revision", "") or "")
        and int(getattr(failure, "plan_revision", 0) or 0)
        == int(getattr(plan, "plan_revision", 0) or 0)
        and getattr(failure, "reason_code", None)
        == "group_capacity_recovery_decision_required"
        for failure in (getattr(production, "group_failures", []) or [])
    )


async def _queue_whole_group_recovery(
    project_id: str,
    *,
    group_id: str,
    review_mode: DubbingProductionReviewMode,
    window: DubbingExecutionWindow,
    attempt: int,
    scope: ExecutionScope,
    frozen_parameters: dict[str, Any] | None = None,
    capacity_replaces_workflow_ids: list[str] | None = None,
) -> DubbingProductionExecuteResponse | None:
    """Both execution scopes reuse one bounded, frozen whole-group retry."""

    if attempt != 2:
        return None
    draft = await asyncio.to_thread(
        _require_projection().get_video_localization,
        project_id,
    )
    plan = draft.dubbing_production.active_plan if draft is not None else None
    group = next(
        (item for item in (plan.groups if plan else []) if item.group_id == group_id),
        None,
    )
    if draft is None or group is None:
        return None
    if _durable_group_attempt_count(draft, group_id) >= 2:
        return None
    frozen = _frozen_group_retry_request(draft, group, frozen_parameters)
    if frozen is None or frozen.project_id != project_id:
        return None
    try:
        kwargs = {}
        if capacity_replaces_workflow_ids is not None:
            kwargs["capacity_replaces_workflow_ids"] = (
                capacity_replaces_workflow_ids
            )
        return await _queue_group_for_review_mode(
            project_id, draft=draft, group=group, scope=scope, attempt=attempt,
            generation_parameters=frozen.model_dump(mode="json"),
            review_mode=review_mode, window=window, frozen_retry_request=frozen,
            **kwargs,
        )
    except AppException as exc:
        if exc.code != "VIDEO_LOCALIZATION_DUBBING_RETRY_INPUT_CHANGED":
            raise
        return None


async def _queue_failed_group_recovery(
    projection: DubbingExecutionProjection,
    project_id: str,
    progress,
    *,
    review_mode: DubbingProductionReviewMode,
    window: DubbingExecutionWindow,
    scope: ExecutionScope,
    draft=None,
    capacity_replaces_workflow_ids: list[str] | None = None,
) -> DubbingProductionExecuteResponse | None:
    """Use the one bounded frozen retry after a failed local close-out."""

    current_draft = draft or await asyncio.to_thread(
        projection.get_video_localization,
        project_id,
    )
    previous_attempt = max(
        int(progress.attempt_count or 0),
        _durable_group_attempt_count(current_draft, progress.group_id),
    )
    if previous_attempt >= 2:
        return None
    kwargs = {}
    if capacity_replaces_workflow_ids is not None:
        kwargs["capacity_replaces_workflow_ids"] = (
            capacity_replaces_workflow_ids
        )
    return await _queue_whole_group_recovery(
        project_id,
        group_id=progress.group_id,
        review_mode=review_mode,
        window=window,
        attempt=previous_attempt + 1,
        scope=scope,
        **kwargs,
    )


def _track_background_submission(task: asyncio.Task) -> None:
    _background_submissions.add(task)

    def finish(completed: asyncio.Task) -> None:
        _background_submissions.discard(completed)
        try:
            completed.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Background dubbing submission failed")

    task.add_done_callback(finish)


def schedule_task_closeout(task: GenerationTask) -> None:
    """Run local gas editing/placement beside the next serial generation."""

    workflow_id = str(
        task.parameters.get("video_localization_workflow_id") or ""
    )
    if workflow_id and workflow_id in _active_closeout_workflow_ids:
        return
    if task.status == TaskStatus.success:
        closeout = asyncio.create_task(handle_completed_task(task))
    elif task.status == TaskStatus.failed:
        closeout = asyncio.create_task(handle_failed_task(task))
    else:
        return
    if workflow_id:
        _active_closeout_workflow_ids.add(workflow_id)

        def release_owner(_completed: asyncio.Task) -> None:
            _active_closeout_workflow_ids.discard(workflow_id)

        closeout.add_done_callback(release_owner)
    _track_background_submission(closeout)


def has_active_task_closeout(workflow_id: str) -> bool:
    return str(workflow_id or "") in _active_closeout_workflow_ids


def _continue_all_remaining(
    project_id: str,
    review_mode: DubbingProductionReviewMode = "full",
    *,
    window: DubbingExecutionWindow | None = None,
) -> None:
    """Keep one durable-looking in-process runner moving until external work starts.

    A recovered candidate can take long enough that the HTTP caller disconnects,
    and the next project write can briefly conflict with the just-finished one.
    The continuous runner therefore owns the whole recovery stretch, retries
    transient failures, and stops only when a real generation task is active or
    the plan is complete.  There is never more than one runner per project.
    """

    execution_window = window or _bounded_window()
    current = _continuous_runs.get(project_id)
    if current is not None and not current.done():
        return

    async def continue_after_current_turn() -> None:
        await asyncio.sleep(0.1)
        transient_attempt = 0
        while True:
            try:
                if review_mode == "full" and execution_window == _bounded_window():
                    response = await advance(project_id, scope="all_remaining")
                elif review_mode == "full":
                    response = await advance(
                        project_id,
                        scope="all_remaining",
                        start_group_id=execution_window.start_group_id,
                        end_group_id=execution_window.end_group_id,
                        max_in_flight_groups=execution_window.max_in_flight_groups,
                        ordinary_speed_baseline=(
                            execution_window.ordinary_speed_baseline
                        ),
                    )
                elif execution_window == _bounded_window():
                    response = await advance(
                        project_id,
                        scope="all_remaining",
                        review_mode=review_mode,
                    )
                else:
                    response = await advance(
                        project_id,
                        scope="all_remaining",
                        review_mode=review_mode,
                        start_group_id=execution_window.start_group_id,
                        end_group_id=execution_window.end_group_id,
                        max_in_flight_groups=execution_window.max_in_flight_groups,
                        ordinary_speed_baseline=(
                            execution_window.ordinary_speed_baseline
                        ),
                    )
                # A workflow/task identifier means this turn really submitted
                # external generation. Stop here and let its completion callback
                # resume the run. The persisted projection can lag briefly behind
                # task submission, so consulting it first can enqueue the same
                # group a second time.
                if response.workflow_id is not None or response.task_id is not None:
                    return
                run = await asyncio.to_thread(
                    _require_projection().read_production_run,
                    project_id,
                )
                transient_attempt = 0
                window_groups = _groups_in_window(run, execution_window)
                if all(
                    _is_terminal_in_window(item, execution_window)
                    for item in window_groups
                ):
                    return
                draft = await asyncio.to_thread(
                    _require_projection().get_video_localization,
                    project_id,
                )
                if _active_managed_group_ids(
                    draft,
                    {item.group_id for item in window_groups},
                ):
                    return
                # Candidate recovery and timeline placement are local close-out
                # work. Continue immediately instead of requiring another click.
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except Exception:
                transient_attempt += 1
                if transient_attempt >= 8:
                    logger.exception(
                        "Continuous dubbing runner stopped after bounded retries",
                        extra={"project_id": project_id},
                    )
                    return
                logger.warning(
                    "Continuous dubbing runner will retry",
                    exc_info=True,
                    extra={
                        "project_id": project_id,
                        "attempt": transient_attempt,
                    },
                )
                await asyncio.sleep(min(2.0, 0.25 * (2**transient_attempt)))

    task = asyncio.create_task(continue_after_current_turn())
    _continuous_runs[project_id] = task

    def clear_runner(completed: asyncio.Task) -> None:
        if _continuous_runs.get(project_id) is completed:
            _continuous_runs.pop(project_id, None)

    task.add_done_callback(clear_runner)
    _track_background_submission(task)


def _generation_parameters(
    draft,
    group,
    *,
    repair_timeline_capacity: bool = False,
    ordinary_speed_baseline: float | None = None,
) -> tuple[dict[str, Any], Any]:
    projection = _require_projection()
    if repair_timeline_capacity:
        if projection.decide_capacity_repair_speed is None:
            raise RuntimeError("dubbing capacity-repair speed port is not configured")
        decision = projection.decide_capacity_repair_speed(
            draft, group,
            **({"ordinary_speed_baseline": ordinary_speed_baseline}
               if ordinary_speed_baseline is not None else {}),
        )
    else:
        decision = projection.decide_generation_speed(draft, group)
    if ordinary_speed_baseline is not None and not repair_timeline_capacity:
        baseline = round(float(ordinary_speed_baseline), 2)
        decision = replace(
            decision,
            # A run-level ordinary baseline is frozen at submission time.
            # Parallel groups cannot observe each other's unfinished audio, so
            # merely clamping independent text-pressure decisions to this
            # interval can still place adjacent groups at opposite edges.
            # Keep every ordinary group at the requested baseline; the
            # evidence-labelled capacity-repair branch remains the only speed
            # exception.
            speed=baseline,
            baseline_speed=baseline,
            content_speed_exception_reason=None,
            content_speed_exception_evidence_ids=(),
        )
    parameters = {
        "engine_id": PRIMARY_DUBBING_ENGINE_ID,
        **decision.generation_parameters(),
    }
    logger.info(
        "Dubbing speed decision",
        extra={
            "group_id": group.group_id,
            "speed": decision.speed,
            "proposed_speed": decision.proposed_speed,
            "baseline_speed": decision.baseline_speed,
            "baseline_clip_id": decision.baseline_clip_id,
            "source_pace_ratio": decision.source_pace_ratio,
            "speed_exception": bool(decision.content_speed_exception_reason),
        },
    )
    return parameters, decision


def _durable_group_attempt_count(draft, group_id: str) -> int:
    """Read the highest durable attempt, including already-created candidates.

    Workflow metadata can lag candidate persistence during callback recovery.
    Counting only workflow parameters lets a stale projection submit a third
    take even though two distinct audio candidates already exist.
    """

    attempts = [0]
    production = getattr(draft, "dubbing_production", None)
    active_plan = getattr(production, "active_plan", None)
    active_plan_revision = int(getattr(active_plan, "plan_revision", 0) or 0)
    ui_state = getattr(draft, "ui_state", {}) or {}
    discarded_ids = {
        str(value)
        for value in (
            ui_state.get("discarded_tts_task_ids", [])
            if isinstance(ui_state, dict)
            else []
        )
        if value
    }
    for workflow in getattr(draft, "tts_tasks", []) or []:
        workflow_ids = {
            str(value)
            for value in (
                getattr(workflow, "workflow_id", None),
                getattr(workflow, "generation_task_id", None),
                getattr(workflow, "result_id", None),
            )
            if value
        }
        if workflow_ids & discarded_ids:
            continue
        for stage in getattr(workflow, "stages", []) or []:
            parameters = getattr(stage, "parameters", {}) or {}
            if not isinstance(parameters, dict):
                continue
            if (
                str(
                    parameters.get(
                        "video_localization_dubbing_group_id"
                    )
                    or ""
                )
                != group_id
            ):
                continue
            workflow_plan_revision = int(
                parameters.get("video_localization_dubbing_plan_revision") or 0
            )
            if (
                active_plan_revision
                and workflow_plan_revision
                and workflow_plan_revision != active_plan_revision
            ):
                continue
            attempts.append(
                int(
                    parameters.get(
                        "video_localization_generation_attempt"
                    )
                    or 0
                )
            )
    candidate_ids: set[str] = set()
    for collection_name in ("candidate_inputs", "candidate_reports"):
        for item in getattr(production, collection_name, []) or []:
            item_group_id = (
                item.get("group_id") if isinstance(item, dict) else getattr(item, "group_id", None)
            )
            candidate_id = (
                item.get("candidate_id")
                if isinstance(item, dict)
                else getattr(item, "candidate_id", None)
            )
            item_plan_revision = int(
                (
                    item.get("plan_revision")
                    if isinstance(item, dict)
                    else getattr(item, "plan_revision", 0)
                )
                or 0
            )
            if (
                active_plan_revision
                and item_plan_revision
                and item_plan_revision != active_plan_revision
            ):
                continue
            if str(item_group_id or "") == group_id and candidate_id:
                candidate_ids.add(str(candidate_id))
    for clip in getattr(draft, "timeline_clips", []) or []:
        if str(clip.get("dubbing_group_id") or "") != group_id:
            continue
        candidate_id = clip.get("candidate_id") or clip.get("result_id")
        if candidate_id:
            candidate_ids.add(str(candidate_id))
    attempts.append(len(candidate_ids))
    return max(attempts)


async def advance(
    project_id: str,
    *,
    scope: ExecutionScope,
    group_id: str | None = None,
    review_mode: DubbingProductionReviewMode = "full",
    start_group_id: str | None = None,
    end_group_id: str | None = None,
    max_in_flight_groups: int = DEFAULT_MAX_IN_FLIGHT_GROUPS,
    ordinary_speed_baseline: float | None = None,
    resource_priority: Literal[
        "foreground_resume",
        "normal",
        "background",
    ] | None = None,
    regenerate_existing: bool = False,
    repair_timeline_capacity: bool = False,
) -> DubbingProductionExecuteResponse:
    """Serialize project close-out and queue decisions across every caller."""

    window = _bounded_window(
        start_group_id=start_group_id,
        end_group_id=end_group_id,
        max_in_flight_groups=max_in_flight_groups,
        ordinary_speed_baseline=ordinary_speed_baseline,
        resource_priority=resource_priority or "normal",
    )
    async with _project_execution_lock(project_id):
        await asyncio.to_thread(
            _require_projection().reconcile_workflow_terminal_states,
            project_id,
        )
        if regenerate_existing and (
            scope != "single_group" or group_id is None
        ):
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUBBING_REGENERATION_SCOPE_INVALID",
                "重新生成已有正式片段时，必须明确指定一个语义组。",
            )
        if repair_timeline_capacity and not regenerate_existing:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUBBING_CAPACITY_REPAIR_SCOPE_INVALID",
                "时间线容量修复只能用于明确重生成的正式语义组。",
            )
        if resource_priority is not None:
            projection = _require_projection()
            if projection.set_group_scheduling_priority is None:
                raise RuntimeError("Production scheduling policy port is not configured")
            draft = await asyncio.to_thread(projection.get_video_localization, project_id)
            plan = draft.dubbing_production.active_plan
            if plan is None:
                raise AppException(409, "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED", "请先生成配音计划。")
            if scope == "single_group":
                target = group_id
                if target is None:
                    run = await asyncio.to_thread(projection.read_production_run, project_id)
                    target = run.next_group_id
                selected = [target] if target is not None else []
            else:
                selected = [item.group_id for item in _groups_in_window(plan, window)]
            async with _project_scheduling_locks.setdefault(project_id, asyncio.Lock()):
                await asyncio.to_thread(
                    projection.set_group_scheduling_priority, project_id,
                    source_revision=plan.source_revision, plan_revision=plan.plan_revision,
                    group_ids=selected, priority=resource_priority,
                )
        if scope == "all_remaining":
            response = await _advance_all_remaining_unlocked(
                project_id,
                review_mode=review_mode,
                window=window,
            )
            return await _with_completion_facts(project_id, response, window=window)
        response = await _advance_unlocked(
            project_id,
            scope=scope,
            group_id=group_id,
            review_mode=review_mode,
            window=_bounded_window(
                max_in_flight_groups=1,
                ordinary_speed_baseline=window.ordinary_speed_baseline,
                resource_priority=window.resource_priority,
            ),
            regenerate_existing=regenerate_existing,
            repair_timeline_capacity=repair_timeline_capacity,
        )
        return await _with_completion_facts(project_id, response, group_id=group_id or response.group_id)


async def _with_completion_facts(project_id, response, *, window=None, group_id=None):
    projection = _require_projection()
    if response.status != "complete" or projection.read_completion is None:
        return response
    kwargs = {}
    if group_id or (window and (window.start_group_id or window.end_group_id)):
        draft = await asyncio.to_thread(projection.get_video_localization, project_id)
        plan = draft.dubbing_production.active_plan
        groups = ([group for group in plan.groups if group.group_id == group_id]
                  if group_id else _groups_in_window(plan, window)) if plan else []
        if not groups:
            return response.model_copy(update={"status": "needs_attention", "message": "执行范围已变化，Agent 需要重新核对当前分组。"})
        kwargs = {"start_ms": min(group.target_start_ms for group in groups),
                  "end_ms": max(group.target_end_ms for group in groups)}
    completion = await asyncio.to_thread(projection.read_completion, project_id, **kwargs)
    return response.model_copy(update={
        "completion": completion,
        "status": (
            "needs_attention"
            if completion.status == "incomplete"
            and completion.automated_production_status != "resolved"
            else "complete"
        ),
        "message": (
            "自动配音已处理完；completion 中列出的次轨片段仍需手工调整，当前不能作为主轨交付。"
            if completion.deferred_manual_timing_group_ids
            and completion.automated_production_status == "resolved"
            else "本次执行已结束，但交付范围仍有缺失、冲突或残留任务；Agent 请按 completion 继续处理。"
            if completion.status == "incomplete"
            else response.message
        ),
    })


_TERMINAL_GROUP_STAGES = {"accepted", "deferred_manual_timing", "failed"}


def _violates_frozen_speed_contract(
    progress,
    window: DubbingExecutionWindow,
) -> bool:
    """Treat an old accepted take as unfinished when this run forbids its speed."""

    baseline = window.ordinary_speed_baseline
    speaking_rate = getattr(progress, "speaking_rate_ratio", None)
    if (
        baseline is None
        or speaking_rate is None
        or getattr(progress, "stage", None) != "accepted"
    ):
        return False
    return abs(float(speaking_rate) - baseline) > 0.05001


def _is_terminal_in_window(progress, window: DubbingExecutionWindow) -> bool:
    return (
        progress.stage in _TERMINAL_GROUP_STAGES
        and not _violates_frozen_speed_contract(progress, window)
    )


def _groups_in_window(run, window: DubbingExecutionWindow):
    groups = list(run.groups)
    if not groups:
        return []
    indexes = {item.group_id: index for index, item in enumerate(groups)}
    if window.start_group_id is not None and window.start_group_id not in indexes:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUBBING_RANGE_CHANGED",
            "配音起点已经变化，请重新读取当前分组。",
        )
    if window.end_group_id is not None and window.end_group_id not in indexes:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUBBING_RANGE_CHANGED",
            "配音终点已经变化，请重新读取当前分组。",
        )
    start = indexes.get(window.start_group_id, 0)
    end = indexes.get(window.end_group_id, len(groups) - 1)
    if end < start:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DUBBING_RANGE_INVALID",
            "配音终点不能早于起点。",
        )
    return groups[start : end + 1]


async def _advance_all_remaining_unlocked(
    project_id: str,
    *,
    review_mode: DubbingProductionReviewMode,
    window: DubbingExecutionWindow,
) -> DubbingProductionExecuteResponse:
    """Fill a bounded pipeline without allowing work outside its frozen range."""

    queued: list[DubbingProductionExecuteResponse] = []
    local_closeouts = 0
    while True:
        run = await asyncio.to_thread(
            _require_projection().read_production_run,
            project_id,
        )
        groups = _groups_in_window(run, window)
        nonterminal = [
            item for item in groups if not _is_terminal_in_window(item, window)
        ]
        if not nonterminal:
            underfilled_group_ids = await asyncio.to_thread(
                _require_projection().find_continuous_boundary_underfill_groups,
                project_id,
                # Only accepted main-lane groups participate in automatic
                # underfill repair. A complete lane-1 deferral is terminal for
                # automation and must never trigger another TTS request.
                [item.group_id for item in groups if item.stage == "accepted"],
            )
            if underfilled_group_ids:
                retry_group_id = next(
                    (
                        item.group_id
                        for item in groups
                        if item.group_id in underfilled_group_ids
                        and int(item.attempt_count or 0) < 2
                    ),
                    None,
                )
                if retry_group_id is not None:
                    return await _advance_unlocked(
                        project_id,
                        scope="all_remaining",
                        review_mode=review_mode,
                        window=window,
                        target_group_id=retry_group_id,
                        schedule_continuation=False,
                        run_override=run,
                        regenerate_existing=True,
                    )
                return DubbingProductionExecuteResponse(
                    status="complete",
                    scope="all_remaining",
                    group_id=window.end_group_id,
                    queued_group_ids=[item.group_id for item in queued],
                    workflow_ids=[item.workflow_id for item in queued if item.workflow_id],
                    task_ids=[item.task_id for item in queued if item.task_id],
                    message=(
                        f"指定范围已经处理完毕；仍有 {len(underfilled_group_ids)} 处"
                        "相邻衔接未达到自动合格线，已停止重复生成并保留当前正式结果。"
                    ),
                )
            is_full_plan = len(groups) == len(run.groups)
            if is_full_plan and local_closeouts:
                return DubbingProductionExecuteResponse(
                    status="queued",
                    scope="all_remaining",
                    group_id=None,
                    message="已有结果已完成收尾，正在确认最终交付状态。",
                )
            if is_full_plan:
                return await _advance_unlocked(
                    project_id,
                    scope="all_remaining",
                    review_mode=review_mode,
                    window=window,
                    run_override=run,
                )
            return DubbingProductionExecuteResponse(
                status="complete",
                scope="all_remaining",
                group_id=window.end_group_id,
                queued_group_ids=[item.group_id for item in queued],
                workflow_ids=[item.workflow_id for item in queued if item.workflow_id],
                task_ids=[item.task_id for item in queued if item.task_id],
                message="指定的未完成范围已经处理完毕。",
            )

        active = [item for item in nonterminal if item.stage == "generating"]
        # Generations may run in parallel, but adoption stays in subtitle
        # order. A later ready candidate remains durable history until every
        # preceding group in this frozen window is terminal.
        leading_nonterminal = nonterminal[0]
        recoverable_active = (
            leading_nonterminal
            if leading_nonterminal.stage == "generating"
            and bool(getattr(leading_nonterminal, "candidate_ids", []))
            else None
        )
        active_group_ids = {item.group_id for item in active}
        newly_queued_group_ids = {item.group_id for item in queued}
        capacity = window.max_in_flight_groups - len(
            active_group_ids | newly_queued_group_ids
        )
        if capacity <= 0 and recoverable_active is None:
            return DubbingProductionExecuteResponse(
                status="queued" if queued else "waiting",
                scope="all_remaining",
                group_id=(queued[0].group_id if queued else active[0].group_id),
                workflow_id=(queued[0].workflow_id if queued else None),
                task_id=(queued[0].task_id if queued else None),
                queued_group_ids=[item.group_id for item in queued],
                workflow_ids=[item.workflow_id for item in queued if item.workflow_id],
                task_ids=[item.task_id for item in queued if item.task_id],
                message=(
                    f"并行流水线已装入 {len(active)} 组，正在生成和检查。"
                    if not queued
                    else f"已补充 {len(queued)} 组，流水线正在继续。"
                ),
            )

        target = recoverable_active or next(
            (
                item
                for item in nonterminal
                if item.stage != "generating"
                and item.group_id not in newly_queued_group_ids
            ),
            None,
        )
        if target is None:
            return DubbingProductionExecuteResponse(
                status="queued" if queued else "waiting",
                scope="all_remaining",
                group_id=(queued[0].group_id if queued else active[0].group_id),
                workflow_id=(queued[0].workflow_id if queued else None),
                task_id=(queued[0].task_id if queued else None),
                queued_group_ids=[item.group_id for item in queued],
                workflow_ids=[item.workflow_id for item in queued if item.workflow_id],
                task_ids=[item.task_id for item in queued if item.task_id],
                message="当前范围内已生成的组正在后台检查。",
            )
        response = await _advance_unlocked(
            project_id,
            scope="all_remaining",
            review_mode=review_mode,
            window=window,
            target_group_id=target.group_id,
            schedule_continuation=False,
            run_override=run,
        )
        if response.status == "needs_attention":
            return response
        if response.workflow_id or response.task_id:
            queued.append(response)
            if len(queued) >= capacity:
                continue
        else:
            local_closeouts += 1
            _continue_for_review_mode(project_id, review_mode, window)
            return response


async def _advance_unlocked(
    project_id: str,
    *,
    scope: ExecutionScope,
    group_id: str | None = None,
    review_mode: DubbingProductionReviewMode = "full",
    window: DubbingExecutionWindow | None = None,
    target_group_id: str | None = None,
    schedule_continuation: bool = True,
    run_override=None,
    regenerate_existing: bool = False,
    repair_timeline_capacity: bool = False,
) -> DubbingProductionExecuteResponse:
    """Queue at most one semantic group through the shared TTS worker."""

    projection = _require_projection()
    forced_attempt: int | None = None
    capacity_replaces_workflow_ids: list[str] | None = None
    run = run_override or await asyncio.to_thread(
        projection.read_production_run,
        project_id,
    )
    execution_window = window or _bounded_window(
        max_in_flight_groups=(
            DEFAULT_MAX_IN_FLIGHT_GROUPS if scope == "all_remaining" else 1
        )
    )
    if scope == "all_remaining" and group_id is not None:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DUBBING_GROUP_SCOPE_INVALID",
            "连续生成不能同时指定单个语义组。",
        )
    requested_group_id = target_group_id or group_id
    selected_progress = (
        next((item for item in run.groups if item.group_id == requested_group_id), None)
        if requested_group_id is not None
        else None
    )
    if requested_group_id is not None and selected_progress is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUBBING_GROUP_CHANGED",
            "当前选择的配音语义组已经变化，请重新选择。",
        )
    next_action = (
        getattr(selected_progress, "recommended_action", None)
        or _ACTION_BY_GROUP_STAGE.get(selected_progress.stage, "place_candidate")
        if selected_progress is not None
        else run.next_action
    )
    selected_group_id = requested_group_id or run.next_group_id
    action_progress = selected_progress or next(
        (
            item
            for item in run.groups
            if item.group_id == selected_group_id
        ),
        None,
    )
    if (
        selected_progress is not None
        and _violates_frozen_speed_contract(selected_progress, execution_window)
    ):
        recovered = await _recover_and_finalize(
            projection,
            project_id,
            selected_progress.group_id,
            list(getattr(selected_progress, "candidate_ids", [])),
            review_mode,
        )
        if recovered == "accepted":
            refreshed_run = await asyncio.to_thread(
                projection.read_production_run,
                project_id,
            )
            refreshed_progress = next(
                (
                    item
                    for item in refreshed_run.groups
                    if item.group_id == selected_progress.group_id
                ),
                None,
            )
            if (
                refreshed_progress is not None
                and not _violates_frozen_speed_contract(
                    refreshed_progress,
                    execution_window,
                )
            ):
                if scope == "all_remaining" and schedule_continuation:
                    _continue_for_review_mode(
                        project_id,
                        review_mode,
                        execution_window,
                    )
                return DubbingProductionExecuteResponse(
                    status=("queued" if scope == "all_remaining" else "complete"),
                    scope=scope,
                    group_id=selected_progress.group_id,
                    message="已恢复符合本次语速基线的新声音并继续下一组。",
                )
        if recovered == "needs_semantic_review":
            return _semantic_boundary_handoff_response(
                scope=scope,
                group_id=selected_progress.group_id,
            )
        if recovered == "capacity_recovery_required":
            await _record_closeout_failure(
                projection,
                project_id,
                selected_progress.group_id,
                candidate_id=(
                    selected_progress.candidate_ids[0]
                    if selected_progress.candidate_ids
                    else None
                ),
                attempt_count=max(1, int(selected_progress.attempt_count or 0)),
                workflow_ids=list(getattr(selected_progress, "workflow_ids", [])),
                capacity_recovery_needed=True,
            )
            return _capacity_recovery_handoff_response(
                scope=scope,
                group_id=selected_progress.group_id,
            )
        await _close_unplaceable_workflow(
            projection,
            project_id,
            list(getattr(selected_progress, "workflow_ids", [])),
        )
        # The old take remains visible until the new baseline-compliant take
        # finishes alignment, gap editing and one atomic target-owned commit.
        next_action = "regenerate_candidate"
        forced_attempt = 1
    unresolved_single_boundary = False
    if (
        not regenerate_existing
        and scope == "single_group"
        and next_action == "complete"
        and selected_progress is not None
        and selected_progress.stage == "accepted"
    ):
        underfilled_group_ids = await asyncio.to_thread(
            projection.find_continuous_boundary_underfill_groups,
            project_id,
            [selected_progress.group_id],
        )
        if selected_progress.group_id in underfilled_group_ids:
            if int(selected_progress.attempt_count or 0) < 2:
                regenerate_existing = True
            else:
                unresolved_single_boundary = True
    if regenerate_existing:
        regeneration_draft = await asyncio.to_thread(
            projection.get_video_localization,
            project_id,
        )
        expected_targets = set(
            getattr(selected_progress, "target_subtitle_ids", [])
            if selected_progress is not None
            else []
        )
        has_formal_projection = bool(
            regeneration_draft is not None
            and expected_targets
            and any(
                clip.get("track_id") == "dub"
                and int(clip.get("dub_lane") or 0) == 0
                and clip.get("status") == "ready"
                and set(
                    str(value)
                    for value in (
                        clip.get("target_subtitle_ids")
                        or ([clip.get("subtitle_id")] if clip.get("subtitle_id") else [])
                    )
                    if str(value)
                )
                == expected_targets
                for clip in regeneration_draft.timeline_clips
            )
        )
        failed_group_retry = bool(
            selected_progress is not None
            and selected_progress.stage == "failed"
            and regeneration_draft is not None
            and _has_current_durable_group_candidate(
                regeneration_draft,
                selected_progress,
            )
        )
        frozen_capacity_group_retry = bool(
            not repair_timeline_capacity
            and selected_progress is not None
            and selected_progress.stage == "needs_gap_processing"
            and regeneration_draft is not None
            and getattr(selected_progress, "workflow_ids", [])
            and _has_current_durable_group_candidate(
                regeneration_draft,
                selected_progress,
            )
            and _has_current_capacity_recovery_required(
                regeneration_draft,
                selected_progress,
            )
        )
        capacity_group_retry = bool(
            repair_timeline_capacity
            and selected_progress is not None
            and selected_progress.stage in {"failed", "needs_gap_processing"}
            and not has_formal_projection
            and regeneration_draft is not None
            and getattr(selected_progress, "workflow_ids", [])
            and _has_current_durable_group_candidate(regeneration_draft, selected_progress)
        )
        if selected_progress is None or not (
            selected_progress.stage == "accepted"
            or has_formal_projection
            or failed_group_retry
            or capacity_group_retry
            or frozen_capacity_group_retry
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_ACCEPTED_GROUP_REQUIRED",
                "只有主轨正式片段，或保留了当前冻结候选的失败语义组，可以安全重新生成。",
            )
        if capacity_group_retry:
            # This is a deliberate new speed attempt, not a replay of the same
            # frozen quality-retry request. The reservation owner validates the
            # current capacity evidence and exact old receipts under its lock.
            capacity_replaces_workflow_ids = list(selected_progress.workflow_ids)
        if (
            (failed_group_retry or frozen_capacity_group_retry)
            and not capacity_group_retry
        ):
            # A failed group has no formal clip to replace. Its only safe retry
            # is the same bounded recovery route used after close-out reports
            # regeneration_required: it reuses the frozen request, observes the
            # durable attempt budget, and lets the reservation reject a still
            # active generation. Do not recreate deleted/rejected candidates.
            retry_kwargs = {}
            if frozen_capacity_group_retry:
                retry_kwargs["capacity_replaces_workflow_ids"] = list(
                    selected_progress.workflow_ids
                )
            queued_retry = await _queue_failed_group_recovery(
                projection,
                project_id,
                selected_progress,
                review_mode=review_mode,
                window=execution_window,
                scope=scope,
                draft=regeneration_draft,
                **retry_kwargs,
            )
            if queued_retry is not None:
                return queued_retry
            return DubbingProductionExecuteResponse(
                status="needs_attention",
                scope=scope,
                group_id=selected_progress.group_id,
                message="当前失败组缺少可用的冻结重试条件，已保留失败记录和现有音频。",
            )
        # Keep the current formal clip visible and playable. The canonical
        # adopter replaces it atomically only after the new media and all
        # evidence-backed close-out steps have completed.
        next_action = "regenerate_candidate"
        # Replacement uses the same durable plan/group attempt sequence as
        # recovery. Resetting it would bypass the adopter's replacement guard
        # without resetting the persisted candidate history or retry budget.
        forced_attempt = None
    if (
        scope == "single_group"
        and requested_group_id is not None
        and selected_progress is not None
        and selected_progress.stage == "failed"
        and not regenerate_existing
    ):
        # A durable failure is terminal for unattended batch progress, but an
        # explicit retry after a repair must be able to replay the local
        # close-out from persisted media.  This does not submit TTS again.
        recovered = await _recover_and_finalize(
            projection,
            project_id,
            selected_progress.group_id,
            list(getattr(selected_progress, "candidate_ids", [])),
            review_mode,
        )
        if recovered == "accepted":
            return DubbingProductionExecuteResponse(
                status="complete",
                scope=scope,
                group_id=selected_progress.group_id,
                message="已用现有音频重新完成当前片段收尾。",
            )
        if recovered == "needs_semantic_review":
            return _semantic_boundary_handoff_response(
                scope=scope,
                group_id=selected_progress.group_id,
            )
        if recovered == "capacity_recovery_required":
            await _record_closeout_failure(
                projection,
                project_id,
                selected_progress.group_id,
                candidate_id=(
                    selected_progress.candidate_ids[0]
                    if selected_progress.candidate_ids
                    else None
                ),
                attempt_count=max(1, int(selected_progress.attempt_count or 0)),
                workflow_ids=list(getattr(selected_progress, "workflow_ids", [])),
                capacity_recovery_needed=True,
            )
            return _capacity_recovery_handoff_response(
                scope=scope,
                group_id=selected_progress.group_id,
            )
        if recovered == "regeneration_required":
            # Keep a regular retry conservative: first reuse the durable take;
            # only an explicit close-out result may consume the single frozen
            # whole-group retry. This shares the same attempt budget and
            # single-flight reservation as worker-triggered recovery.
            await _close_unplaceable_workflow(
                projection,
                project_id,
                list(getattr(selected_progress, "workflow_ids", [])),
            )
            queued_retry = await _queue_failed_group_recovery(
                projection,
                project_id,
                selected_progress,
                review_mode=review_mode,
                window=execution_window,
                scope=scope,
            )
            if queued_retry is not None:
                return queued_retry
        return DubbingProductionExecuteResponse(
            status="needs_attention",
            scope=scope,
            group_id=selected_progress.group_id,
            message="现有音频仍无法安全收尾；失败记录和音频均已保留。",
        )
    if next_action == "complete":
        return DubbingProductionExecuteResponse(
            status="complete",
            scope=scope,
            group_id=selected_group_id,
            message=(
                "当前片段已完成；相邻衔接仍未达到自动合格线，"
                "已停止重复生成并保留当前正式结果。"
                if unresolved_single_boundary
                else "剩余语义组已经完成。"
            ),
        )
    if next_action == "wait_for_generation":
        active = selected_progress or next(
            (item for item in run.groups if item.stage == "generating"), None
        )
        # A process restart or an earlier callback failure can leave the
        # workflow badge at "generating" after a durable audio result already
        # exists.  Prefer that result over waiting forever or submitting a
        # duplicate generation.
        active_candidate_ids = list(
            getattr(active, "candidate_ids", []) if active else []
        )
        if active is not None and active_candidate_ids:
            recovered = await _recover_and_finalize(
                projection,
                project_id,
                active.group_id,
                active_candidate_ids,
                review_mode,
            )
            if recovered == "accepted":
                if scope == "all_remaining" and schedule_continuation:
                    _continue_for_review_mode(project_id, review_mode, execution_window)
                return DubbingProductionExecuteResponse(
                    status=(
                        "queued" if scope == "all_remaining" else "complete"
                    ),
                    scope=scope,
                    group_id=active.group_id,
                    message=(
                        "已恢复生成完成的音频并继续下一组。"
                        if scope == "all_remaining"
                        else "已恢复生成完成的音频并完成收尾。"
                    ),
                )
            if recovered == "needs_semantic_review":
                return _semantic_boundary_handoff_response(
                    scope=scope,
                    group_id=active.group_id,
                )
            if recovered == "capacity_recovery_required":
                await _record_closeout_failure(
                    projection,
                    project_id,
                    active.group_id,
                    candidate_id=(active_candidate_ids[0] if active_candidate_ids else None),
                    attempt_count=max(1, int(active.attempt_count or 0)),
                    workflow_ids=list(getattr(active, "workflow_ids", [])),
                    capacity_recovery_needed=True,
                )
                return _capacity_recovery_handoff_response(
                    scope=scope,
                    group_id=active.group_id,
                )
            if recovered == "regeneration_required":
                await _close_unplaceable_workflow(
                    projection,
                    project_id,
                    list(getattr(active, "workflow_ids", [])),
                )
                previous_attempt = max(
                    int(active.attempt_count or 0),
                    _durable_group_attempt_count(
                        await asyncio.to_thread(
                            projection.get_video_localization,
                            project_id,
                        ),
                        active.group_id,
                    ),
                )
                queued_retry = (
                    await _queue_whole_group_recovery(
                        project_id,
                        group_id=active.group_id,
                        review_mode=review_mode,
                        window=execution_window,
                        attempt=previous_attempt + 1,
                        scope=scope,
                    )
                    if previous_attempt < 2
                    else None
                )
                if queued_retry is not None:
                    return queued_retry
                await _record_closeout_failure(
                    projection,
                    project_id,
                    active.group_id,
                    candidate_id=(active_candidate_ids[0] if active_candidate_ids else None),
                    attempt_count=max(1, previous_attempt),
                    recovery_needed=True,
                )
                if scope == "all_remaining" and schedule_continuation:
                    _continue_for_review_mode(project_id, review_mode, execution_window)
                return DubbingProductionExecuteResponse(
                    status=("queued" if scope == "all_remaining" else "complete"),
                    scope=scope,
                    group_id=active.group_id,
                    message="当前组需确认语义分段或附近参考后继续恢复；现有音频保留，问题已记录。",
                )
            if recovered == "retryable_failure":
                await _record_closeout_failure(
                    projection,
                    project_id,
                    active.group_id,
                    candidate_id=(active_candidate_ids[0] if active_candidate_ids else None),
                    attempt_count=active.attempt_count,
                    workflow_ids=list(getattr(active, "workflow_ids", [])),
                )
                if scope == "all_remaining" and schedule_continuation:
                    _continue_for_review_mode(project_id, review_mode, execution_window)
                return DubbingProductionExecuteResponse(
                    status=("queued" if scope == "all_remaining" else "complete"),
                    scope=scope,
                    group_id=active.group_id,
                    message="当前音频无法完成气口收口，已记录失败并继续下一组。",
                )
        return DubbingProductionExecuteResponse(
            status="waiting",
            scope=scope,
            group_id=active.group_id if active else selected_group_id,
            message="当前语义组正在生成，不会重复提交。",
        )
    if (
        next_action == "edit_timeline"
        and action_progress is not None
        and getattr(action_progress, "timeline_requires_reconciliation", False)
        and not regenerate_existing
    ):
        return DubbingProductionExecuteResponse(
            status="needs_attention",
            scope=scope,
            group_id=selected_group_id,
            message="当前字幕已关联手动拆分或部分配音。Agent 应先核对现有片段与未覆盖字幕，再调整受管范围；已保留现有音频，未重复生成。",
        )
    if next_action in {
        "generate_candidate",
        "process_gaps",
        "place_candidate",
        "edit_timeline",
    }:
        current_draft = await asyncio.to_thread(
            projection.get_video_localization,
            project_id,
        )
        current_plan = getattr(
            getattr(current_draft, "dubbing_production", None),
            "active_plan",
            None,
        )
        current_group = next(
            (
                item
                for item in (getattr(current_plan, "groups", []) or [])
                if item.group_id == selected_group_id
            ),
            None,
        )
        durable_identities = (
            _rebase_compatible_generated_identities(current_draft, current_group)
            if current_draft is not None and current_group is not None
            else []
        )
        recovered = await _recover_and_finalize(
            projection,
            project_id,
            str(selected_group_id or ""),
            list(dict.fromkeys(
                [
                    *(
                        getattr(action_progress, "candidate_ids", [])
                        if action_progress
                        else []
                    ),
                    *durable_identities,
                ]
            )),
            review_mode,
        )
        if recovered == "needs_semantic_review":
            return _semantic_boundary_handoff_response(
                scope=scope,
                group_id=selected_group_id,
            )
        if recovered == "capacity_recovery_required":
            await _record_closeout_failure(
                projection,
                project_id,
                str(selected_group_id or ""),
                candidate_id=(
                    action_progress.candidate_ids[0]
                    if action_progress and action_progress.candidate_ids
                    else None
                ),
                attempt_count=(action_progress.attempt_count if action_progress else 1),
                workflow_ids=(
                    list(getattr(action_progress, "workflow_ids", []))
                    if action_progress
                    else []
                ),
                capacity_recovery_needed=True,
            )
            if scope == "all_remaining" and schedule_continuation:
                _continue_for_review_mode(project_id, review_mode, execution_window)
            return _capacity_recovery_handoff_response(
                scope=scope,
                group_id=selected_group_id,
            )
        if recovered == "retryable_failure":
            await _record_closeout_failure(
                projection,
                project_id,
                str(selected_group_id or ""),
                candidate_id=(
                    action_progress.candidate_ids[0]
                    if action_progress and action_progress.candidate_ids
                    else None
                ),
                attempt_count=(action_progress.attempt_count if action_progress else 1),
                workflow_ids=(
                    list(getattr(action_progress, "workflow_ids", []))
                    if action_progress
                    else []
                ),
            )
            if scope == "all_remaining" and schedule_continuation:
                _continue_for_review_mode(project_id, review_mode, execution_window)
            return DubbingProductionExecuteResponse(
                status=("queued" if scope == "all_remaining" else "complete"),
                scope=scope,
                group_id=selected_group_id,
                message="当前音频无法完成气口收口，已记录失败并继续下一组。",
            )
        if recovered == "regeneration_required":
            await _close_unplaceable_workflow(
                projection,
                project_id,
                (
                    list(getattr(action_progress, "workflow_ids", []))
                    if action_progress
                    else []
                ),
            )
            current_draft = await asyncio.to_thread(
                projection.get_video_localization,
                project_id,
            )
            previous_attempt = max(
                int(action_progress.attempt_count if action_progress else 0),
                _durable_group_attempt_count(
                    current_draft,
                    str(selected_group_id or ""),
                ),
            )
            queued_retry = (
                await _queue_whole_group_recovery(
                    project_id,
                    group_id=str(selected_group_id or ""),
                    review_mode=review_mode,
                    window=execution_window,
                    attempt=previous_attempt + 1,
                    scope=scope,
                )
                if previous_attempt < 2
                else None
            )
            if queued_retry is not None:
                return queued_retry
            await _record_closeout_failure(
                projection,
                project_id,
                str(selected_group_id or ""),
                candidate_id=(
                    action_progress.candidate_ids[0]
                    if action_progress and action_progress.candidate_ids
                    else None
                ),
                attempt_count=max(1, previous_attempt),
                recovery_needed=True,
            )
            if scope == "all_remaining" and schedule_continuation:
                _continue_for_review_mode(project_id, review_mode, execution_window)
            return DubbingProductionExecuteResponse(
                status=("queued" if scope == "all_remaining" else "complete"),
                scope=scope,
                group_id=selected_group_id,
                message="当前组需确认语义分段或附近参考后继续恢复；现有音频保留，问题已记录。",
            )
        if recovered == "accepted":
            if scope == "all_remaining" and schedule_continuation:
                _continue_for_review_mode(project_id, review_mode, execution_window)
            return DubbingProductionExecuteResponse(
                status=("queued" if scope == "all_remaining" else "complete"),
                scope=scope,
                group_id=selected_group_id,
                message=(
                    "已有正式片段已完成气口收口，正在继续下一组。"
                    if scope == "all_remaining"
                    else "已有正式片段已完成气口收口。"
                ),
            )
    if next_action not in {"generate_candidate", "regenerate_candidate"}:
        message = (
            "当前组正在检查断句，已保留候选与证据；等待 Agent 处置后再继续。"
            if next_action == "review_semantic_boundaries"
            else "当前语义组已有结果，等待轻量收口，不会启动另一套生成流程。"
        )
        return DubbingProductionExecuteResponse(
            status="needs_attention",
            scope=scope,
            group_id=selected_group_id,
            message=message,
        )

    draft = await asyncio.to_thread(
        projection.get_video_localization,
        project_id,
    )
    if draft is None:
        raise AppException(
            404,
            "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
            "视频本土化项目不存在。",
        )
    plan = draft.dubbing_production.active_plan
    group = next(
        (
            item
            for item in (plan.groups if plan else [])
            if item.group_id == selected_group_id
        ),
        None,
    )
    if plan is None or group is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
            "配音分组已经变化，请重新读取当前进度。",
        )
    progress = selected_progress or next(
        item for item in run.groups if item.group_id == group.group_id
    )
    if (
        next_action == "regenerate_candidate"
        and _has_current_durable_group_candidate(draft, progress)
        and _has_current_semantic_recovery_required(draft, progress)
    ):
        # Agent recovery is a decision about an already generated waveform.
        # Its workflow may still display ``running`` only because placement is
        # pending.  Close that successful placement before reserving the one
        # frozen whole-group retry.  The service leaves actual queued/running
        # generation untouched, so a real active inference still wins the
        # single-flight reservation instead of being cancelled.
        await _close_unplaceable_workflow(
            projection,
            project_id,
            list(getattr(progress, "workflow_ids", [])),
        )
        queued_retry = await _queue_failed_group_recovery(
            projection,
            project_id,
            progress,
            review_mode=review_mode,
            window=execution_window,
            scope=scope,
            draft=draft,
        )
        if queued_retry is not None:
            return queued_retry
        return DubbingProductionExecuteResponse(
            status="needs_attention",
            scope=scope,
            group_id=group.group_id,
            message="当前候选已要求整组恢复，但冻结重试条件不足或重试次数已用完；现有音频和语义处置均已保留。",
        )
    durable_attempts = _durable_group_attempt_count(draft, group.group_id)
    attempt = forced_attempt or (max(progress.attempt_count, durable_attempts) + 1)
    source_cue_ids = _source_cue_ids(draft, group)
    generation_parameters, _speed_decision = _generation_parameters(
        draft,
        group,
        repair_timeline_capacity=repair_timeline_capacity,
        ordinary_speed_baseline=execution_window.ordinary_speed_baseline,
    )
    preflight = projection.assess_group_preflight(draft, group, speed=generation_parameters["speed"], engine_id=generation_parameters["engine_id"]) if projection.assess_group_preflight else None
    if preflight is not None and preflight.status == "blocked":
        return DubbingProductionExecuteResponse(status="needs_attention", scope=scope, group_id=group.group_id,
            preflight=preflight, message=preflight.message)
    if capacity_replaces_workflow_ids is not None:
        generation_parameters = {
            **generation_parameters,
            "source": "video_localization",
            "text": group.spoken_text,
            "video_localization_dubbing_group_id": group.group_id,
            "video_localization_dubbing_plan_revision": plan.plan_revision,
            "video_localization_target_subtitle_ids": list(group.subtitle_ids),
            "video_localization_source_cue_ids": source_cue_ids,
        }
    try:
        workflow = await asyncio.to_thread(
            projection.reserve_single_tts_handoff,
            project_id,
            group.group_id,
            parameters=generation_parameters,
            target_subtitle_ids=list(group.subtitle_ids),
            source_cue_ids=source_cue_ids,
            **({"capacity_replaces_workflow_ids": capacity_replaces_workflow_ids}
               if capacity_replaces_workflow_ids is not None else {}),
        )
    except AppException as exc:
        if exc.code != "VIDEO_LOCALIZATION_TTS_SEGMENT_BUSY":
            raise
        return DubbingProductionExecuteResponse(
            status="waiting",
            scope=scope,
            group_id=group.group_id,
            message="这组已经在生成，不会重复排队。",
        )
    if workflow is None:
        raise AppException(
            404,
            "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
            "视频本土化项目不存在。",
        )
    background = asyncio.create_task(
        _queue_group_for_review_mode(
            project_id,
            draft=draft,
            group=group,
            scope=scope,
            attempt=attempt,
            workflow_id=workflow.workflow_id,
            generation_parameters=generation_parameters,
            review_mode=review_mode,
            window=execution_window,
        )
    )
    _track_background_submission(background)
    return DubbingProductionExecuteResponse(
        status="queued",
        scope=scope,
        group_id=group.group_id,
        workflow_id=workflow.workflow_id,
        preflight=preflight,
        message="已排队，参考音频会在后台准备并继续生成。" + (" " + preflight.message if preflight and preflight.status == "warning" else ""),
    )


def _source_cue_ids(draft, group) -> list[str]:
    plan = draft.dubbing_production.active_plan
    if plan is None:
        return []
    units_by_id = {item.unit_id: item for item in plan.semantic_units}
    return list(
        dict.fromkeys(
            cue_id
            for unit_id in group.unit_ids
            if unit_id in units_by_id
            for cue_id in units_by_id[unit_id].source_cue_ids
        )
    )


def _current_equivalent_group_after_plan_rebind(
    *,
    draft,
    plan,
    group,
    source_cue_ids: list[str],
    current,
    current_plan,
    request: GenerateRequest,
    group_evidence_context_fingerprint: Callable[[Any, Any, Any], str] | None,
):
    """Return a current group only when a runtime plan rebind kept its input exact."""

    current_group = next(
        (
            item
            for item in current_plan.groups
            if item.group_id == group.group_id
        ),
        None,
    )
    if current_group is None:
        return None
    binding_fields = (
        "group_id",
        "unit_ids",
        "subtitle_ids",
        "speaker_id",
        "scene_id",
        "spoken_text",
        "target_start_ms",
        "target_end_ms",
        "source_reference_start_ms",
        "source_reference_end_ms",
    )
    if any(
        getattr(current_group, field, None) != getattr(group, field, None)
        for field in binding_fields
    ):
        return None
    current_source_cue_ids = _source_cue_ids(current, current_group)
    if current_source_cue_ids != source_cue_ids:
        return None
    if (
        request.video_localization_dubbing_plan_revision != current_plan.plan_revision
        or request.video_localization_dubbing_group_id != current_group.group_id
        or request.text != current_group.spoken_text
        or request.video_localization_target_subtitle_ids
        != list(current_group.subtitle_ids)
        or request.video_localization_source_cue_ids != current_source_cue_ids
    ):
        return None
    if group_evidence_context_fingerprint is None:
        return None
    try:
        return (
            current_group
            if group_evidence_context_fingerprint(
                draft, plan, group
            )
            == group_evidence_context_fingerprint(
                current, current_plan, current_group
            )
            else None
        )
    except (AttributeError, TypeError, ValueError):
        # Missing or malformed source evidence must never turn a plan change
        # into a generation submission.
        return None


async def _queue_group(
    project_id: str,
    *,
    draft,
    group,
    scope: ExecutionScope,
    attempt: int,
    workflow_id: str | None = None,
    generation_parameters: dict[str, Any] | None = None,
    review_mode: DubbingProductionReviewMode = "full",
    window: DubbingExecutionWindow | None = None,
    frozen_retry_request: GenerateRequest | None = None,
    capacity_replaces_workflow_ids: list[str] | None = None,
) -> DubbingProductionExecuteResponse:
    execution_window = window or _bounded_window(max_in_flight_groups=1)
    plan = draft.dubbing_production.active_plan
    if plan is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
            "配音分组已经变化，请重新读取当前进度。",
        )
    source_cue_ids = _source_cue_ids(draft, group)
    if generation_parameters is None:
        generation_parameters, _speed_decision = _generation_parameters(
            draft,
            group,
            ordinary_speed_baseline=execution_window.ordinary_speed_baseline,
        )
    projection = _require_projection()
    preflight = projection.assess_group_preflight(draft, group, speed=generation_parameters["speed"], engine_id=generation_parameters["engine_id"]) if projection.assess_group_preflight else None
    if preflight is not None and preflight.status == "blocked":
        if workflow_id:
            await _close_unplaceable_workflow(projection, project_id, [workflow_id])
        return DubbingProductionExecuteResponse(status="needs_attention", scope=scope, group_id=group.group_id,
            preflight=preflight, message=preflight.message)
    if workflow_id is None:
        try:
            reservation_kwargs = {}
            if capacity_replaces_workflow_ids is not None:
                reservation_kwargs["capacity_replaces_workflow_ids"] = (
                    capacity_replaces_workflow_ids
                )
            reserved = await asyncio.to_thread(
                _require_projection().reserve_single_tts_handoff,
                project_id,
                group.group_id,
                parameters=generation_parameters,
                target_subtitle_ids=list(group.subtitle_ids),
                source_cue_ids=source_cue_ids,
                **reservation_kwargs,
            )
        except AppException as exc:
            if exc.code != "VIDEO_LOCALIZATION_TTS_SEGMENT_BUSY":
                raise
            return DubbingProductionExecuteResponse(
                status="waiting",
                scope=scope,
                group_id=group.group_id,
                message="这组已经在生成，不会重复排队。",
            )
        if reserved is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在。",
            )
        workflow_id = reserved.workflow_id
    request = await asyncio.to_thread(
        _require_projection().build_single_tts_handoff,
        project_id,
        group.group_id,
        parameters=generation_parameters,
        target_subtitle_ids=list(group.subtitle_ids),
        source_cue_ids=source_cue_ids,
        workflow_id=workflow_id,
    )
    if request is None:
        raise AppException(
            404,
            "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
            "视频本土化项目不存在。",
        )
    request = request.model_copy(
        update={
            "engine_id": PRIMARY_DUBBING_ENGINE_ID,
            "video_localization_preflight": preflight,
            "video_localization_execution_scope": scope,
            "video_localization_generation_attempt": attempt,
            "video_localization_dubbing_review_mode": review_mode,
            "video_localization_execution_start_group_id": (
                execution_window.start_group_id
            ),
            "video_localization_execution_end_group_id": (
                execution_window.end_group_id
            ),
            "video_localization_max_in_flight_groups": (
                execution_window.max_in_flight_groups
            ),
            "video_localization_ordinary_speed_baseline": (
                execution_window.ordinary_speed_baseline
            ),
            "resource_priority": execution_window.resource_priority,
        }
    )
    request = await asyncio.to_thread(
        video_localization_tts_handoff.finalize_submission,
        request,
    )
    if frozen_retry_request is not None and not _same_retry_contract(frozen_retry_request, request):
        await asyncio.to_thread(
            video_localization_tts_handoff.mark_workflow_terminal,
            request, status="failed",
            error_message="原始台词、参考音或生成参数已变化，未提交重生成；请确认恢复方案。",
            source_id=request.video_localization_workflow_id,
        )
        raise AppException(
            409, "VIDEO_LOCALIZATION_DUBBING_RETRY_INPUT_CHANGED",
            "原始台词、参考音或生成参数已变化，请确认恢复方案。",
        )
    try:
        # Background reference preparation can outlive an explicit priority
        # change. Resolve durable policy at submission, serialized with updates.
        async with _project_scheduling_locks.setdefault(project_id, asyncio.Lock()):
            current = await asyncio.to_thread(
                _require_projection().get_video_localization, project_id,
            )
            current_plan = current.dubbing_production.active_plan if current else None
            if (current_plan is None or current_plan.plan_revision != plan.plan_revision
                or current_plan.source_revision != plan.source_revision):
                current_group = (
                    _current_equivalent_group_after_plan_rebind(
                        draft=draft,
                        plan=plan,
                        group=group,
                        source_cue_ids=source_cue_ids,
                        current=current,
                        current_plan=current_plan,
                        request=request,
                        group_evidence_context_fingerprint=(
                            projection.group_evidence_context_fingerprint
                        ),
                    )
                    if current is not None and current_plan is not None
                    else None
                )
                if current_group is None:
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED",
                        "配音计划已经变化，请重新读取当前分组。",
                    )
                plan = current_plan
                group = current_group
            policy = next((item for item in getattr(current.dubbing_production, "scheduling_policies", [])
                           if item.plan_revision == plan.plan_revision
                           and item.source_revision == plan.source_revision
                           and item.group_id == group.group_id), None)
            request = request.model_copy(update={
                "resource_priority": policy.priority if policy else "normal",
            })
            if request.video_localization_recovery is not None:
                from app.services import longform_queue
                from app.schemas.voice_studio import LongformGenerateRequest, PlannedTextSegment
                recovery = request.video_localization_recovery
                parent = await longform_queue.submit(LongformGenerateRequest(
                    generate_request=request,
                    segments=[PlannedTextSegment(index=i, text=text, char_count=len(text), segment_reason="explicit_semantic_recovery")
                              for i, text in enumerate(recovery.phrases)],
                    verify_enabled=True, merge_enabled=True, max_retries=2,
                    stop_merge_on_verification_failed=True, silence_ms=0,
                ))
                task_id = parent.longform_task_id
            else:
                task_id = await task_queue.submit(
                    request,
                    project_id=project_id,
                    segment_id=request.segment_id,
                )
    except Exception as exc:
        if request.video_localization_workflow_id:
            try:
                video_localization_tts_handoff.mark_workflow_terminal(
                    request,
                    status="failed",
                    error_message=(
                        exc.message
                        if isinstance(exc, AppException)
                        else "配音任务未能进入生成队列，请重试"
                    ),
                    source_id=request.video_localization_workflow_id,
                )
            except Exception:
                logger.exception(
                    "Failed to persist dubbing queue submission failure",
                    extra={
                        "project_id": project_id,
                        "workflow_id": request.video_localization_workflow_id,
                    },
                )
        raise
    return DubbingProductionExecuteResponse(
        status="queued",
        scope=scope,
        group_id=group.group_id,
        task_id=task_id,
        workflow_id=request.video_localization_workflow_id,
        preflight=preflight,
        message="已按统一语义组流程提交 OmniVoice 生成。" + (" " + preflight.message if preflight and preflight.status == "warning" else ""),
    )


async def handle_completed_task(task: GenerationTask) -> str:
    """Serialize task callbacks with manual and continuous advancement."""

    project_id = str(task.project_id or "")
    if not project_id:
        return await _handle_completed_task_unlocked(task)
    async with _project_execution_lock(project_id):
        return await _handle_completed_task_unlocked(task)


async def _handle_completed_task_unlocked(task: GenerationTask) -> str:
    """Close one successful managed task through the canonical finisher.

    Generation success and gas-edit placement are separate durable facts. The
    queue calls this only after the audio history result has been saved, so
    recovery can reuse that result when the first placement attempt failed.
    """

    project_id = str(task.project_id or "")
    group_id = str(
        task.parameters.get("video_localization_dubbing_group_id")
        or task.segment_id
        or ""
    )
    scope = task.parameters.get("video_localization_execution_scope")
    if scope not in {"single_group", "all_remaining"} or not project_id or not group_id:
        return "not_managed"
    review_mode = _task_review_mode(task)
    execution_window = _task_execution_window(task)

    if scope == "all_remaining":
        run = await asyncio.to_thread(
            _require_projection().read_production_run,
            project_id,
        )
        window_groups = (
            _groups_in_window(run, execution_window)
            if run is not None
            else []
        )
        first_nonterminal = next(
            (
                item
                for item in window_groups
                if item.stage not in _TERMINAL_GROUP_STAGES
            ),
            None,
        )
        if (
            first_nonterminal is not None
            and first_nonterminal.group_id != group_id
        ):
            # The candidate is already safe in history. The preceding group's
            # completion will re-enter the shared runner and adopt this one.
            return "waiting_for_preceding_group"

    identities = list(
        dict.fromkeys(
            value
            for value in (
                task.result_id,
                task.task_id,
                f"candidate_{task.task_id}" if task.task_id else None,
            )
            if value
        )
    )
    projection = _require_projection()
    disposition = await _recover_and_finalize(
        projection,
        project_id,
        group_id,
        identities,
        review_mode,
    )
    attempt = int(
        task.parameters.get("video_localization_generation_attempt") or 1
    )
    if disposition == "capacity_recovery_required":
        await _record_closeout_failure(
            projection,
            project_id,
            group_id,
            candidate_id=(identities[0] if identities else None),
            attempt_count=attempt,
            workflow_ids=[
                str(task.parameters.get("video_localization_workflow_id") or "")
            ],
            capacity_recovery_needed=True,
        )
        if scope == "all_remaining":
            _continue_for_review_mode(project_id, review_mode, execution_window)
        return disposition
    if disposition == "regeneration_required":
        await _close_unplaceable_workflow(
            projection,
            project_id,
            [str(task.parameters.get("video_localization_workflow_id") or "")],
        )
        queued = (
            await _queue_whole_group_recovery(
                project_id,
                group_id=group_id,
                review_mode=review_mode,
                window=execution_window,
                attempt=attempt + 1,
                scope=scope,
                frozen_parameters={**task.parameters, "text": task.input_text, "engine_id": task.engine_id},
            )
            if attempt < 2
            else None
        )
        if queued is not None and queued.status in {"queued", "waiting"}:
            return "regeneration_queued"
    if disposition == "needs_semantic_review":
        # This is an Agent handoff, not a failed take.  In particular, an
        # all_remaining window must not queue its successor while the rendered
        # boundary evidence is still awaiting a disposition.
        return disposition
    if disposition in {None, "retryable_failure"}:
        await _record_closeout_failure(
            projection,
            project_id,
            group_id,
            candidate_id=(identities[0] if identities else None),
            attempt_count=attempt,
            workflow_ids=[
                str(task.parameters.get("video_localization_workflow_id") or "")
            ],
        )
        disposition = "failed"
    elif disposition == "regeneration_required":
        await _record_closeout_failure(
            projection,
            project_id,
            group_id,
            candidate_id=(identities[0] if identities else None),
            attempt_count=attempt,
            recovery_needed=True,
        )
        disposition = "failed"
    if scope == "all_remaining":
        _continue_for_review_mode(project_id, review_mode, execution_window)
    return disposition


async def handle_failed_task(task: GenerationTask) -> str:
    """Record one failed generation and continue without automatic retry."""

    project_id = str(task.project_id or "")
    group_id = str(
        task.parameters.get("video_localization_dubbing_group_id")
        or task.segment_id
        or ""
    )
    scope = task.parameters.get("video_localization_execution_scope")
    if scope not in {"single_group", "all_remaining"} or not project_id or not group_id:
        return "not_managed"
    attempt = int(
        task.parameters.get("video_localization_generation_attempt") or 1
    )
    review_mode = _task_review_mode(task)
    execution_window = _task_execution_window(task)
    projection = _require_projection()
    current = projection.get_video_localization(project_id)
    plan = current.dubbing_production.active_plan if current else None
    group = next(
        (item for item in (plan.groups if plan else []) if item.group_id == group_id),
        None,
    )
    if current is None or plan is None or group is None:
        return "stale"
    projection.record_group_failure(
        project_id,
        DubbingProductionGroupFailureRequest(
            source_revision=plan.source_revision,
            plan_revision=plan.plan_revision,
            group_id=group_id,
            title="配音生成失败",
            note=task.error_message or "OmniVoice 没有生成可用音频。",
            reason_code="tts_generation_failed",
            attempted_strategy_codes=["omnivoice"],
            attempt_count=attempt,
        ),
    )
    if scope == "all_remaining":
        _continue_for_review_mode(project_id, review_mode, execution_window)
    return "failed"


__all__ = [
    "PRIMARY_DUBBING_ENGINE_ID",
    "advance",
    "handle_completed_task",
    "handle_failed_task",
    "schedule_task_closeout",
    "has_active_task_closeout",
]


async def advance_recovery(project_id: str, decision) -> DubbingProductionExecuteResponse:
    """Persist an explicit stage through the shared durable segmented queue."""
    from app.services import longform_queue
    async with _project_execution_lock(project_id):
        projection = _require_projection()
        draft = await asyncio.to_thread(projection.get_video_localization, project_id)
        if draft is None:
            raise AppException(404, "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND", "项目不存在。")
        plan = draft.dubbing_production.active_plan
        group, _ = projection.validate_recovery_decision(draft, decision)
        run = await asyncio.to_thread(projection.read_production_run, project_id)
        progress = next((g for g in run.groups if g.group_id == group.group_id), None)
        if progress is None or progress.stage in {"accepted", "deferred_manual_timing"}:
            raise AppException(409, "DUBBING_RECOVERY_NOT_REQUIRED", "当前组已有正式结果；替换成品请使用明确的重新生成入口。")
        discarded = set((draft.ui_state or {}).get("discarded_tts_task_ids", []))
        if decision.recovery_id in discarded:
            raise AppException(409, "DUBBING_RECOVERY_CANCELLED", "该恢复结果已删除，不会自动恢复。")
        existing = await asyncio.to_thread(longform_queue.get_task, decision.recovery_id)
        if existing is not None:
            saved = GenerateRequest.model_validate(existing.parameters["generate_request"])
            if saved.project_id != project_id or saved.video_localization_recovery != decision:
                raise AppException(409, "DUBBING_RECOVERY_ID_CONFLICT", "恢复标识已用于另一份方案。")
            if existing.status == TaskStatus.cancelled:
                raise AppException(409, "DUBBING_RECOVERY_CANCELLED", "该恢复任务已取消，不会自动恢复已删除的结果。")
            if existing.status == TaskStatus.failed:
                if any(part.status == TaskStatus.cancelled for part in existing.segments):
                    raise AppException(409, "DUBBING_RECOVERY_CANCELLED", "恢复短语已被取消，不会合并或自动补回。")
                reusable_children = []
                for part in existing.segments:
                    child = await asyncio.to_thread(task_queue.get_task, part.task_id) if part.task_id else None
                    if child and child.status not in {TaskStatus.failed, TaskStatus.cancelled}:
                        reusable_children.append(part.index)
                if any(part.status == TaskStatus.failed for part in existing.segments) and all(part.status == TaskStatus.success or (part.attempts >= 3 and part.index not in reusable_children) for part in existing.segments):
                    return DubbingProductionExecuteResponse(status="needs_attention", scope="single_group", group_id=group.group_id,
                        task_id=existing.longform_task_id, message="失败短语的重试次数已用完，请选择下一恢复方案。")
                await longform_queue.retry_failed(existing.longform_task_id)
            elif existing.status == TaskStatus.success:
                merged = await asyncio.to_thread(task_queue.find_longform_export_task, existing.longform_task_id, existing.export_id)
                if merged is not None:
                    outcome = await _handle_completed_task_unlocked(merged)
                    return DubbingProductionExecuteResponse(status="complete" if outcome == "accepted" else "needs_attention",
                        scope="single_group", group_id=group.group_id, task_id=existing.longform_task_id,
                        message="已恢复现有分段音频的安全收尾。" if outcome == "accepted" else "分段音频已保留，仍需处理当前组的落轨问题。")
            return DubbingProductionExecuteResponse(status="waiting", scope="single_group", group_id=group.group_id,
                task_id=existing.longform_task_id, message="已接回原恢复任务，成功短语不会重复生成。")
        run = await asyncio.to_thread(projection.read_production_run, project_id)
        progress = next((g for g in run.groups if g.group_id == group.group_id), None)
        if progress is None or progress.stage in {"accepted", "deferred_manual_timing"}:
            raise AppException(409, "DUBBING_RECOVERY_NOT_REQUIRED", "当前组已有正式结果；替换成品请使用明确的重新生成入口。")
        if progress.stage == "generating":
            return DubbingProductionExecuteResponse(status="waiting", scope="single_group", group_id=group.group_id, message="当前组仍在生成，请等待原任务。")
        compatible_stages = list(
            _rebase_compatible_generation_stages(draft, group)
        )
        attempts = max(
            _durable_group_attempt_count(draft, group.group_id),
            *[
                int(parameters.get("video_localization_generation_attempt") or 0)
                for parameters, _request in compatible_stages
            ],
        )
        prior_decisions = []
        for parameters, _request in compatible_stages:
            value = parameters.get("video_localization_recovery")
            if not isinstance(value, dict):
                continue
            try:
                from app.schemas.video_localization_dubbing_recovery import (
                    DubbingRecoveryDecision,
                )

                historical = DubbingRecoveryDecision.model_validate(value)
                # The stored stage remains historical. Revalidate an in-memory
                # current-plan view before it can establish recovery order.
                projection.validate_recovery_decision(
                    draft,
                    historical.model_copy(update={
                        "source_revision": plan.source_revision,
                        "plan_revision": plan.plan_revision,
                    }),
                )
            except (TypeError, ValueError, AppException):
                continue
            prior_decisions.append(value)
        stage_ids = {value["recovery_id"] for value in prior_decisions if value.get("stage") == decision.stage}
        if len(stage_ids) >= 3:
            raise AppException(409, "DUBBING_RECOVERY_BUDGET_EXHAUSTED", "当前恢复级别已尝试三次，请保留结果并处理下一步。")
        if attempts < 2 or (decision.stage == "nearby_reference" and not any(value.get("stage") == "semantic_phrases" for value in prior_decisions)):
            raise AppException(409, "DUBBING_RECOVERY_STAGE_ORDER", "请先完成整组重试，再按语义分段，最后才更换参考。")
        frozen = _frozen_group_retry_request_for_current_group(
            draft,
            group,
            allow_equivalent_plan_rebind=True,
        )
        if frozen is None:
            raise AppException(409, "DUBBING_RECOVERY_FROZEN_INPUT_MISSING", "缺少完整冻结参数，未提交新生成。")
        parameters = frozen.model_dump(mode="json")
        parameters["video_localization_recovery"] = decision.model_dump(mode="json")
        await _close_unplaceable_workflow(projection, project_id, list(progress.workflow_ids))
        return await _queue_group(project_id, draft=draft, group=group, scope="single_group", attempt=attempts + 1,
            generation_parameters=parameters,
            frozen_retry_request=(frozen.model_copy(update={"video_localization_recovery": decision})
                                  if decision.stage == "semantic_phrases" else None),
            window=_bounded_window(max_in_flight_groups=1, ordinary_speed_baseline=frozen.video_localization_ordinary_speed_baseline))
