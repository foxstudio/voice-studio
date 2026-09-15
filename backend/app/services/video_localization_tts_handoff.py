from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from app.errors import AppException
from app.schemas.video_localization_tts_handoff import (
    TtsHandoffClaim,
    TtsResultPlacementEventV1,
    TtsTaskRegistrationEventV1,
    TtsWorkflowTerminalEventV1,
)
from app.schemas.voice_studio import (
    GenerateRequest,
    GenerationTask,
    HistoryItem,
    LongformGenerateRequest,
    LongformTask,
    TaskStatus,
)
from app.services import database
from app.services import history_store
from app.services import (
    video_localization_tts_handoff_store as handoff_store,
)


logger = logging.getLogger(__name__)
_DELIVERY_LEASE_DURATION_MS = 60_000
_DELIVERY_HEARTBEAT_INTERVAL_SECONDS = 10.0
_MAX_REPLAY_ATTEMPTS = 5
_PLACEMENT_RECOVERED_ERROR = (
    "音频已生成并保存在配音记录中，但写回视频时间线失败。"
)
_REPLAY_WORKER_LOCK = threading.Lock()
_REPLAY_WORKER_THREAD: threading.Thread | None = None


class _TtsHandoffLeaseHeartbeat:
    """Keep one fenced outbox delivery claim live during projection work."""

    def __init__(self, claim: TtsHandoffClaim) -> None:
        self._claim = claim
        self._stop_event = threading.Event()
        self._lost_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lost(self) -> bool:
        return self._lost_event.is_set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"tts-handoff-lease-{self._claim.event_id[-12:]}",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(
                timeout=max(
                    1.0,
                    _DELIVERY_HEARTBEAT_INTERVAL_SECONDS * 2,
                )
            )

    def _run(self) -> None:
        while not self._stop_event.wait(
            _DELIVERY_HEARTBEAT_INTERVAL_SECONDS
        ):
            try:
                renewed = handoff_store.heartbeat_claim(
                    self._claim,
                    observed_at_ms=(time.time_ns() // 1_000_000),
                    lease_duration_ms=_DELIVERY_LEASE_DURATION_MS,
                )
            except Exception as exc:
                logger.warning(
                    "TTS handoff lease heartbeat failed (%s)",
                    type(exc).__name__,
                )
                self._lost_event.set()
                self._stop_event.set()
                return
            if not renewed:
                self._lost_event.set()
                self._stop_event.set()
                return


def _dubbing_lineage_from_task(
    task: GenerationTask,
) -> tuple[int | None, str | None, list[str]]:
    raw_revision = task.parameters.get(
        "video_localization_dubbing_plan_revision"
    )
    revision = int(raw_revision) if raw_revision else None
    group_id = (
        str(
            task.parameters.get(
                "video_localization_dubbing_group_id"
            )
            or ""
        )
        or None
    )
    target_subtitle_ids = list(
        dict.fromkeys(
            str(value)
            for value in (
                task.parameters.get(
                    "video_localization_target_subtitle_ids"
                )
                or []
            )
            if str(value)
        )
    )
    return revision, group_id, target_subtitle_ids


@dataclass(frozen=True)
class VideoLocalizationTtsProjection:
    """Injected project-side operations used by the shared TTS workflow."""

    finalize_submission: Callable[[GenerateRequest], GenerateRequest]
    register_task: Callable[..., Any]
    sync_result: Callable[..., Any]
    mark_workflow_terminal: Callable[..., Any]
    resolve_generation_priority: Callable[[GenerationTask], str | None] | None = None


@dataclass(frozen=True)
class TtsHandoffReplayReport:
    inspected: int
    applied: int
    abandoned: int
    remaining_pending: int


class TtsHandoffApplicationService:
    """Coordinate shared TTS records with the video-localization projection."""

    def __init__(
        self,
        *,
        runner_id: str | None = None,
    ) -> None:
        self._projection: VideoLocalizationTtsProjection | None = None
        self._runner_id = runner_id or f"tts-handoff:{uuid.uuid4().hex}"

    def configure(
        self,
        projection: VideoLocalizationTtsProjection,
    ) -> None:
        self._projection = projection

    def finalize_submission(
        self,
        request: GenerateRequest,
    ) -> GenerateRequest:
        if not self._is_bound_request(request):
            return request
        return self._require_projection().finalize_submission(request)

    def resolve_generation_priority(self, task: GenerationTask) -> str | None:
        projection = self._projection
        if projection is None or projection.resolve_generation_priority is None:
            return None
        return projection.resolve_generation_priority(task)

    def persist_and_register_generation_task(
        self,
        task: GenerationTask,
        *,
        workflow_id: str | None,
    ) -> Any:
        if not self._is_bound_task(task):
            database.upsert(
                "tasks",
                task.task_id,
                task.model_dump(),
            )
            return None
        if not task.project_id or not self._project_exists(
            task.project_id
        ):
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在",
            )
        event = TtsTaskRegistrationEventV1(
            project_id=self._required_text(
                task.project_id,
                "project_id",
            ),
            source_kind="task",
            source_id=task.task_id,
            segment_id=self._required_text(
                task.segment_id,
                "segment_id",
            ),
            generation_task_id=task.task_id,
            workflow_id=workflow_id,
        )
        event_id = handoff_store.registration_event_id(
            source_kind=event.source_kind,
            source_id=event.source_id,
        )
        with database.conn() as connection:
            database.upsert_from_connection(
                connection,
                "tasks",
                task.task_id,
                task.model_dump(),
            )
            handoff_store.enqueue(
                connection,
                event_id=event_id,
                payload=event,
                created_at=task.created_at,
            )
        decision = self._claim_event(event_id)
        if decision is None:
            raise RuntimeError("TTS registration event disappeared")
        if decision.outcome == "active_lease":
            return True
        if decision.outcome == "not_pending":
            if decision.event.status == "applied":
                return True
            self._compensate_registration(
                event_id,
                source_table="tasks",
                source_key="task_id",
                source_id=task.task_id,
            )
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_HANDOFF_RETIRED",
                "该配音任务已被项目重置或删除，请重新发送。",
            )
        claim = self._required_claim(decision)
        heartbeat = _TtsHandoffLeaseHeartbeat(claim)
        heartbeat.start()
        try:
            try:
                projected = self._apply_registration(
                    event,
                    claim,
                )
            except Exception:
                self._compensate_registration(
                    event_id,
                    source_table="tasks",
                    source_key="task_id",
                    source_id=task.task_id,
                    claim=claim,
                )
                raise
            if projected is None:
                self._compensate_registration(
                    event_id,
                    source_table="tasks",
                    source_key="task_id",
                    source_id=task.task_id,
                    claim=claim,
                )
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_TTS_REGISTRATION_FAILED",
                    "配音任务未能关联到当前项目，请返回项目后重试。",
                )
            if heartbeat.lost or not handoff_store.mark_applied(
                claim,
                attempted_at=self._now(),
                observed_at_ms=self._now_ms(),
            ):
                raise handoff_store.TtsHandoffClaimLost(
                    "TTS registration claim was lost before completion"
                )
            return projected
        finally:
            heartbeat.stop()

    def persist_and_register_longform_task(
        self,
        task: LongformTask,
        request: GenerateRequest,
    ) -> Any:
        if not self._is_bound_request(request):
            database.upsert(
                "longform_tasks",
                task.longform_task_id,
                task.model_dump(),
            )
            return None
        if not request.project_id or not self._project_exists(
            request.project_id
        ):
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
                "视频本土化项目不存在",
            )
        event = TtsTaskRegistrationEventV1(
            project_id=self._required_text(
                request.project_id,
                "project_id",
            ),
            source_kind="longform_task",
            source_id=task.longform_task_id,
            segment_id=self._required_text(
                request.segment_id,
                "segment_id",
            ),
            generation_task_id=(f"longform:{task.longform_task_id}"),
            workflow_id=request.video_localization_workflow_id,
        )
        event_id = handoff_store.registration_event_id(
            source_kind=event.source_kind,
            source_id=event.source_id,
        )
        with database.conn() as connection:
            database.upsert_from_connection(
                connection,
                "longform_tasks",
                task.longform_task_id,
                task.model_dump(),
            )
            handoff_store.enqueue(
                connection,
                event_id=event_id,
                payload=event,
                created_at=task.created_at,
            )
        decision = self._claim_event(event_id)
        if decision is None:
            raise RuntimeError("TTS longform registration event disappeared")
        if decision.outcome == "active_lease":
            return True
        if decision.outcome == "not_pending":
            if decision.event.status == "applied":
                return True
            self._compensate_registration(
                event_id,
                source_table="longform_tasks",
                source_key="longform_task_id",
                source_id=task.longform_task_id,
            )
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_HANDOFF_RETIRED",
                "该长段配音任务已被项目重置或删除，请重新发送。",
            )
        claim = self._required_claim(decision)
        heartbeat = _TtsHandoffLeaseHeartbeat(claim)
        heartbeat.start()
        try:
            try:
                projected = self._apply_registration(
                    event,
                    claim,
                )
            except Exception:
                self._compensate_registration(
                    event_id,
                    source_table="longform_tasks",
                    source_key="longform_task_id",
                    source_id=task.longform_task_id,
                    claim=claim,
                )
                raise
            if projected is None:
                self._compensate_registration(
                    event_id,
                    source_table="longform_tasks",
                    source_key="longform_task_id",
                    source_id=task.longform_task_id,
                    claim=claim,
                )
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_TTS_REGISTRATION_FAILED",
                    "长段配音任务未能关联到当前项目，请返回项目后重试。",
                )
            if heartbeat.lost or not handoff_store.mark_applied(
                claim,
                attempted_at=self._now(),
                observed_at_ms=self._now_ms(),
            ):
                raise handoff_store.TtsHandoffClaimLost(
                    "TTS longform registration claim was lost before completion"
                )
            return projected
        finally:
            heartbeat.stop()

    def register_task(
        self,
        project_id: str,
        segment_id: str,
        task_id: str,
        workflow_id: str | None = None,
    ) -> Any:
        return self._require_projection().register_task(
            project_id,
            segment_id,
            task_id,
            workflow_id,
        )

    def persist_generated_history(
        self,
        task: GenerationTask,
        history: HistoryItem,
    ) -> HistoryItem:
        if (
            self._is_bound_task(task)
            and task.project_id
            and not self._project_exists(task.project_id)
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_TTS_HANDOFF_RETIRED",
                "项目已被删除，生成结果不会重新关联到本土化时间线。",
            )
        event = self._result_placement_event(task, history)
        with database.conn() as connection:
            history_store.add_from_connection(
                connection,
                history,
            )
            if event is not None:
                handoff_store.enqueue(
                    connection,
                    event_id=(handoff_store.result_placement_event_id(event.task_id)),
                    payload=event,
                    created_at=history.created_at,
                )
        return history

    def persist_terminal_longform_task(
        self,
        task: LongformTask,
    ) -> bool:
        event = self._terminal_event_from_longform(task)
        if event is None:
            return False
        if not self._project_exists(event.project_id):
            return False
        event_id = handoff_store.workflow_terminal_event_id(
            source_id=event.source_id,
            workflow_id=event.workflow_id,
        )
        with database.conn() as connection:
            database.upsert_from_connection(
                connection,
                "longform_tasks",
                task.longform_task_id,
                task.model_dump(),
            )
            handoff_store.enqueue(
                connection,
                event_id=event_id,
                payload=event,
                created_at=task.completed_at or self._now(),
            )
        return True

    def place_generated_result(
        self,
        task: GenerationTask,
        history: HistoryItem,
        *,
        handoff_claim: TtsHandoffClaim | None = None,
    ) -> bool:
        if not self._is_bound_task(task):
            return True
        generation_id = str(task.generation_id or task.parameters.get("generation_id") or "")
        target_id = (
            task.segment_id
            if str(task.segment_id or "").startswith("group_")
            else task.localized_subtitle_id or task.cue_id
        )
        if (
            not task.project_id
            or not target_id
            or not generation_id
            or generation_id != task.task_id
            or history.generation_id != generation_id
            or history.project_id != task.project_id
            or (history.localized_subtitle_id != task.localized_subtitle_id)
            or history.cue_id != task.cue_id
            or not history.bind_to_video_localization
            or not history.output_path
            or not history.result_id
        ):
            return False

        (
            dubbing_plan_revision,
            dubbing_group_id,
            dubbing_target_subtitle_ids,
        ) = _dubbing_lineage_from_task(task)
        synced = self._require_projection().sync_result(
            task.project_id,
            target_id,
            result_id=history.result_id,
            output_path=history.output_path,
            duration_ms=history.duration_ms,
            task_id=task.task_id,
            generation_id=generation_id,
            workflow_id=(
                str(task.parameters.get("video_localization_workflow_id") or "") or None
            ),
            timeline_clip_id=(str(task.parameters.get("timeline_clip_id") or "") or None),
            dubbing_plan_revision=dubbing_plan_revision,
            dubbing_group_id=dubbing_group_id,
            dubbing_target_subtitle_ids=(
                dubbing_target_subtitle_ids
            ),
            handoff_claim=handoff_claim,
        )
        if synced is None:
            return False
        workflow = next(
            (item for item in reversed(synced.tts_tasks) if item.generation_task_id == task.task_id),
            None,
        )
        if workflow is not None:
            generation = next(
                (stage for stage in workflow.stages if stage.kind == "generation"),
                None,
            )
            placement = next(
                (stage for stage in workflow.stages if stage.kind == "placement"),
                None,
            )
            managed_production = (
                generation is not None
                and generation.parameters.get(
                    "video_localization_execution_scope"
                )
                in {"single_group", "all_remaining"}
            )
            if managed_production:
                return bool(
                    generation.status == "success"
                    and workflow.result_id == history.result_id
                    and any(
                        candidate.get("task_id") == task.task_id
                        and candidate.get("result_id") == history.result_id
                        # sync_result owns media validation/adoption; adoption copies
                        # history audio to project storage. Paths are not identities.
                        and bool(candidate.get("audio_path"))
                        for candidate in synced.generated_candidates
                    )
                )
            return placement is not None and placement.status == "success"
        return any(
            item.get("track_id") == "dub" and item.get("task_id") == task.task_id and bool(item.get("audio_path"))
            for item in synced.timeline_clips
        )

    def _retire_obsolete_placement(
        self, claim: TtsHandoffClaim | None, error: Exception,
    ) -> bool:
        if claim is None or not isinstance(error, AppException) or error.code not in {
            "VIDEO_LOCALIZATION_DUBBING_TASK_STALE",
            "VIDEO_LOCALIZATION_DUBBING_GROUP_STALE",
            "VIDEO_LOCALIZATION_TTS_RESULT_DISCARDED",
        }:
            return False
        return handoff_store.abandon_claim(
            claim,
            attempted_at=self._now(),
            observed_at_ms=self._now_ms(),
            reason=f"{error.code}: {error.message}",
        )

    def place_generated_result_with_retry(
        self,
        task: GenerationTask,
        history: HistoryItem,
    ) -> bool:
        if (
            self._is_bound_task(task)
            and task.project_id
            and not self._project_exists(task.project_id)
        ):
            task.logs.append(
                "视频本土化项目已删除，放轨事件不再投递"
            )
            return False
        event = self._result_placement_event(task, history)
        event_id = handoff_store.result_placement_event_id(event.task_id) if event is not None else None
        claim: TtsHandoffClaim | None = None
        if event is not None:
            try:
                with database.conn() as connection:
                    handoff_store.enqueue(
                        connection,
                        event_id=event_id or "",
                        payload=event,
                        created_at=history.created_at,
                    )
            except Exception:
                logger.exception(
                    "Failed to persist TTS placement outbox event %s",
                    event_id,
                )
            decision = self._claim_event(event_id or "")
            if decision is None:
                task.logs.append(
                    "视频本土化放轨事件丢失，等待后台恢复"
                )
                return False
            if decision.outcome == "active_lease":
                return True
            if decision.outcome == "not_pending":
                if decision.event.status == "applied":
                    return True
                task.logs.append(
                    "视频本土化放轨事件已退役，不再自动投递"
                )
                return False
            claim = self._required_claim(decision)
        heartbeat = (
            _TtsHandoffLeaseHeartbeat(claim)
            if claim is not None
            else None
        )
        if heartbeat is not None:
            heartbeat.start()
        try:
            for attempt in range(1, 4):
                try:
                    if self.place_generated_result(
                        task,
                        history,
                        handoff_claim=claim,
                    ):
                        if claim is None:
                            return True
                        if heartbeat is not None and heartbeat.lost:
                            raise handoff_store.TtsHandoffClaimLost(
                                "TTS handoff delivery lease was lost during projection"
                            )
                        if handoff_store.mark_applied(
                            claim,
                            attempted_at=self._now(),
                            observed_at_ms=self._now_ms(),
                        ):
                            return True
                        raise handoff_store.TtsHandoffClaimLost(
                            "TTS handoff delivery lease was lost before completion"
                        )
                    task.logs.append(
                        f"视频本土化时间线未采用本次结果（{attempt}/3）"
                    )
                except Exception as exc:
                    if self._retire_obsolete_placement(claim, exc):
                        task.logs.append("本次自动放轨已结束；音频保留在配音记录中。")
                        return False
                    task.logs.append(
                        f"视频本土化 cue 回填失败（{attempt}/3）：{exc}"
                    )
                    if (
                        isinstance(exc, handoff_store.TtsHandoffClaimLost)
                        or (heartbeat is not None and heartbeat.lost)
                    ):
                        break
                if attempt < 3:
                    time.sleep(0.15 * attempt)
            if claim is not None:
                handoff_store.record_failure(
                    claim,
                    attempted_at=self._now(),
                    observed_at_ms=self._now_ms(),
                    error=(
                        task.logs[-1]
                        if task.logs
                        else "TTS result placement was not applied"
                    ),
                )
            return False
        finally:
            if heartbeat is not None:
                heartbeat.stop()

    def mark_workflow_terminal(
        self,
        request: GenerateRequest,
        *,
        status: str,
        error_message: str | None,
        source_id: str | None = None,
    ) -> Any:
        if not self._is_bound_request(request) or not request.project_id or not request.video_localization_workflow_id:
            return None
        if not self._project_exists(request.project_id):
            return None
        event = TtsWorkflowTerminalEventV1(
            project_id=request.project_id,
            source_id=(str(source_id or request.generation_id or "") or request.video_localization_workflow_id),
            workflow_id=request.video_localization_workflow_id,
            terminal_status=status,
            error_message=error_message,
        )
        return self._apply_terminal_event(event, ensure_enqueued=True)

    def replay_pending(
        self,
        *,
        limit: int = 100,
    ) -> TtsHandoffReplayReport:
        inspected = 0
        applied = 0
        abandoned = 0
        for stored in handoff_store.list_pending(limit=limit):
            inspected += 1
            payload = stored.payload
            claim: TtsHandoffClaim | None = None
            heartbeat: _TtsHandoffLeaseHeartbeat | None = None
            try:
                decision = self._claim_event(stored.event_id)
                if (
                    decision is None
                    or decision.outcome != "acquired"
                ):
                    continue
                claim = self._required_claim(decision)
                heartbeat = _TtsHandoffLeaseHeartbeat(claim)
                heartbeat.start()
                if stored.attempt_count >= _MAX_REPLAY_ATTEMPTS:
                    if handoff_store.abandon_claim(
                        claim,
                        attempted_at=self._now(),
                        observed_at_ms=self._now_ms(),
                        reason=(
                            "TTS handoff exceeded the automatic replay limit"
                        ),
                    ):
                        abandoned += 1
                    continue
                if not self._project_exists(payload.project_id):
                    if handoff_store.abandon_claim(
                        claim,
                        attempted_at=self._now(),
                        observed_at_ms=self._now_ms(),
                        reason=(
                            "video-localization project no longer exists"
                        ),
                    ):
                        abandoned += 1
                    continue
                if isinstance(
                    payload,
                    TtsTaskRegistrationEventV1,
                ):
                    if not self._registration_source_exists(payload):
                        if handoff_store.abandon_claim(
                            claim,
                            attempted_at=self._now(),
                            observed_at_ms=self._now_ms(),
                            reason=("registration source record no longer exists"),
                        ):
                            abandoned += 1
                        continue
                    projected = self._apply_registration(
                        payload,
                        claim,
                    )
                elif isinstance(
                    payload,
                    TtsResultPlacementEventV1,
                ):
                    history = history_store.get(payload.result_id)
                    if history is None:
                        if handoff_store.abandon_claim(
                            claim,
                            attempted_at=self._now(),
                            observed_at_ms=self._now_ms(),
                            reason=("placement history record no longer exists"),
                        ):
                            abandoned += 1
                        continue
                    task = self._task_from_placement_event(
                        payload,
                        history,
                    )
                    projected = self.place_generated_result(
                        task,
                        history,
                        handoff_claim=claim,
                    )
                elif isinstance(
                    payload,
                    TtsWorkflowTerminalEventV1,
                ):
                    projected = self._project_terminal_event(
                        payload,
                        claim,
                    )
                else:  # pragma: no cover - discriminated schema
                    raise TypeError("unsupported TTS handoff event kind")
                if projected is None or projected is False:
                    handoff_store.record_failure(
                        claim,
                        attempted_at=self._now(),
                        observed_at_ms=self._now_ms(),
                        error=("TTS handoff projection was not applied"),
                    )
                    continue
                if heartbeat.lost:
                    raise handoff_store.TtsHandoffClaimLost(
                        "TTS handoff delivery lease was lost during replay"
                    )
                if handoff_store.mark_applied(
                    claim,
                    attempted_at=self._now(),
                    observed_at_ms=self._now_ms(),
                ):
                    if isinstance(payload, TtsResultPlacementEventV1):
                        self._clear_recovered_task_delivery_error(
                            payload.task_id
                        )
                    applied += 1
            except Exception as exc:
                if isinstance(payload, TtsResultPlacementEventV1) and self._retire_obsolete_placement(claim, exc):
                    abandoned += 1
                    continue
                if claim is not None:
                    handoff_store.record_failure(
                        claim,
                        attempted_at=self._now(),
                        observed_at_ms=self._now_ms(),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                logger.warning(
                    "TTS handoff event %s remains pending: %s",
                    stored.event_id,
                    exc,
                )
            finally:
                if heartbeat is not None:
                    heartbeat.stop()
        return TtsHandoffReplayReport(
            inspected=inspected,
            applied=applied,
            abandoned=abandoned,
            remaining_pending=handoff_store.pending_count(),
        )

    def _result_placement_event(
        self,
        task: GenerationTask,
        history: HistoryItem,
    ) -> TtsResultPlacementEventV1 | None:
        if not self._is_bound_task(task):
            return None
        generation_id = str(task.generation_id or task.parameters.get("generation_id") or "")
        if not task.project_id or not task.segment_id or not generation_id or not history.result_id:
            return None
        (
            dubbing_plan_revision,
            dubbing_group_id,
            dubbing_target_subtitle_ids,
        ) = _dubbing_lineage_from_task(task)
        return TtsResultPlacementEventV1(
            project_id=task.project_id,
            task_id=task.task_id,
            generation_id=generation_id,
            result_id=history.result_id,
            segment_id=task.segment_id,
            localized_subtitle_id=task.localized_subtitle_id,
            cue_id=task.cue_id,
            timeline_clip_id=(str(task.parameters.get("timeline_clip_id") or "") or None),
            dubbing_plan_revision=dubbing_plan_revision,
            dubbing_group_id=dubbing_group_id,
            dubbing_target_subtitle_ids=(
                dubbing_target_subtitle_ids
            ),
        )

    def _terminal_event_from_longform(
        self,
        task: LongformTask,
    ) -> TtsWorkflowTerminalEventV1 | None:
        if task.status not in {
            TaskStatus.failed,
            TaskStatus.cancelled,
        }:
            return None
        try:
            request = LongformGenerateRequest(**task.parameters).generate_request
        except Exception:
            return None
        if not self._is_bound_request(request) or not request.project_id or not request.video_localization_workflow_id:
            return None
        return TtsWorkflowTerminalEventV1(
            project_id=request.project_id,
            source_id=task.longform_task_id,
            workflow_id=request.video_localization_workflow_id,
            terminal_status=task.status.value,
            error_message=task.error_message,
        )

    def _apply_registration(
        self,
        event: TtsTaskRegistrationEventV1,
        claim: TtsHandoffClaim,
    ) -> Any:
        return self._require_projection().register_task(
            event.project_id,
            event.segment_id,
            event.generation_task_id,
            event.workflow_id,
            handoff_claim=claim,
        )

    def _compensate_registration(
        self,
        event_id: str,
        *,
        source_table: str,
        source_key: str,
        source_id: str,
        claim: TtsHandoffClaim | None = None,
    ) -> None:
        allowed_sources = {
            ("tasks", "task_id"),
            ("longform_tasks", "longform_task_id"),
        }
        if (source_table, source_key) not in allowed_sources:
            raise ValueError("unsupported TTS registration source")
        attempted_at = self._now()
        with database.conn() as connection:
            observed_at_ms = self._now_ms()
            if claim is not None:
                handoff_store.require_active_claim(
                    connection,
                    claim,
                    target_project_id=claim.project_id,
                    observed_at_ms=observed_at_ms,
                )
            connection.execute(
                f"DELETE FROM {source_table} WHERE {source_key} = ?",
                (source_id,),
            )
            if claim is None:
                handoff_store.mark_abandoned_from_connection(
                    connection,
                    event_id,
                    attempted_at=attempted_at,
                    reason=("registration projection failed before queueing"),
                )
            elif not handoff_store.abandon_claim_from_connection(
                connection,
                claim,
                attempted_at=attempted_at,
                observed_at_ms=observed_at_ms,
                reason=("registration projection failed before queueing"),
            ):
                raise handoff_store.TtsHandoffClaimLost(
                    "TTS handoff claim was lost during registration compensation"
                )

    @staticmethod
    def _registration_source_exists(
        event: TtsTaskRegistrationEventV1,
    ) -> bool:
        if event.source_kind == "task":
            return (
                database.get_one(
                    "tasks",
                    "task_id",
                    event.source_id,
                )
                is not None
            )
        return (
            database.get_one(
                "longform_tasks",
                "longform_task_id",
                event.source_id,
            )
            is not None
        )

    @staticmethod
    def _task_from_placement_event(
        event: TtsResultPlacementEventV1,
        history: HistoryItem,
    ) -> GenerationTask:
        return GenerationTask(
            task_id=event.task_id,
            generation_id=event.generation_id,
            engine_id=history.engine_id,
            project_id=event.project_id,
            segment_id=event.segment_id,
            localized_subtitle_id=(event.localized_subtitle_id),
            cue_id=event.cue_id,
            bind_to_video_localization=True,
            input_text=history.input_text,
            status=TaskStatus.success,
            result_id=history.result_id,
            parameters={
                "source": "video_localization",
                "generation_id": event.generation_id,
                "timeline_clip_id": event.timeline_clip_id,
                "video_localization_dubbing_plan_revision": (
                    event.dubbing_plan_revision
                ),
                "video_localization_dubbing_group_id": (
                    event.dubbing_group_id
                ),
                "video_localization_target_subtitle_ids": list(
                    event.dubbing_target_subtitle_ids
                ),
            },
        )

    @staticmethod
    def _clear_recovered_task_delivery_error(task_id: str) -> None:
        row = database.get_one("tasks", "task_id", task_id)
        if row is None:
            return
        task = GenerationTask(**row)
        if task.error_message != _PLACEMENT_RECOVERED_ERROR:
            return
        recovered = task.model_copy(
            update={
                "error_message": None,
                "logs": [
                    entry
                    for entry in task.logs
                    if not entry.startswith(
                        "视频本土化 cue 回填失败"
                    )
                ]
                + ["视频本土化时间线写回已由后台恢复"],
            }
        )
        database.upsert(
            "tasks",
            recovered.task_id,
            recovered.model_dump(),
        )

    def _apply_terminal_event(
        self,
        event: TtsWorkflowTerminalEventV1,
        *,
        ensure_enqueued: bool,
    ) -> Any:
        event_id = handoff_store.workflow_terminal_event_id(
            source_id=event.source_id,
            workflow_id=event.workflow_id,
        )
        if ensure_enqueued:
            with database.conn() as connection:
                handoff_store.enqueue(
                    connection,
                    event_id=event_id,
                    payload=event,
                    created_at=self._now(),
                )
        decision = self._claim_event(event_id)
        if decision is None:
            return None
        if decision.outcome == "active_lease":
            return True
        if decision.outcome == "not_pending":
            if decision.event.status == "applied":
                return True
            return None
        claim = self._required_claim(decision)
        heartbeat = _TtsHandoffLeaseHeartbeat(claim)
        heartbeat.start()
        try:
            try:
                projected = self._project_terminal_event(
                    event,
                    claim,
                )
            except Exception as exc:
                handoff_store.record_failure(
                    claim,
                    attempted_at=self._now(),
                    observed_at_ms=self._now_ms(),
                    error=f"{type(exc).__name__}: {exc}",
                )
                logger.warning(
                    "TTS terminal event %s remains pending: %s",
                    event_id,
                    exc,
                )
                return None
            if projected is None:
                handoff_store.record_failure(
                    claim,
                    attempted_at=self._now(),
                    observed_at_ms=self._now_ms(),
                    error="TTS terminal projection was not applied",
                )
                return None
            if heartbeat.lost or not handoff_store.mark_applied(
                claim,
                attempted_at=self._now(),
                observed_at_ms=self._now_ms(),
            ):
                raise handoff_store.TtsHandoffClaimLost(
                    "TTS terminal claim was lost before completion"
                )
            return projected
        finally:
            heartbeat.stop()

    def _project_terminal_event(
        self,
        event: TtsWorkflowTerminalEventV1,
        claim: TtsHandoffClaim,
    ) -> Any:
        return self._require_projection().mark_workflow_terminal(
            event.project_id,
            event.workflow_id,
            status=event.terminal_status,
            error_message=event.error_message,
            handoff_claim=claim,
        )

    def _claim_event(
        self,
        event_id: str,
    ) -> handoff_store.TtsHandoffClaimDecision | None:
        return handoff_store.claim_pending(
            event_id,
            runner_id=self._runner_id,
            observed_at_ms=self._now_ms(),
            lease_duration_ms=_DELIVERY_LEASE_DURATION_MS,
        )

    @staticmethod
    def _required_claim(
        decision: handoff_store.TtsHandoffClaimDecision,
    ) -> TtsHandoffClaim:
        if not decision.acquired or decision.claim is None:
            raise handoff_store.TtsHandoffClaimLost(
                "TTS handoff delivery lease was not acquired"
            )
        return decision.claim

    @staticmethod
    def _project_exists(project_id: str) -> bool:
        return (
            database.get_one(
                "projects",
                "project_id",
                project_id,
            )
            is not None
        )

    @staticmethod
    def _required_text(
        value: str | None,
        field_name: str,
    ) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"video-localization TTS {field_name} is required")
        return normalized

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="microseconds")

    @staticmethod
    def _now_ms() -> int:
        return time.time_ns() // 1_000_000

    def _require_projection(self) -> VideoLocalizationTtsProjection:
        if self._projection is None:
            raise RuntimeError("video-localization TTS projection is not configured")
        return self._projection

    @staticmethod
    def _is_bound_request(request: GenerateRequest) -> bool:
        return request.source == "video_localization" and request.bind_to_video_localization

    @staticmethod
    def _is_bound_task(task: GenerationTask) -> bool:
        return task.parameters.get("source") == "video_localization" and task.bind_to_video_localization


DEFAULT_TTS_HANDOFF_SERVICE = TtsHandoffApplicationService()


def configure_projection(
    projection: VideoLocalizationTtsProjection,
) -> None:
    DEFAULT_TTS_HANDOFF_SERVICE.configure(projection)


def finalize_submission(
    request: GenerateRequest,
) -> GenerateRequest:
    return DEFAULT_TTS_HANDOFF_SERVICE.finalize_submission(request)


def resolve_generation_priority(task: GenerationTask) -> str | None:
    return DEFAULT_TTS_HANDOFF_SERVICE.resolve_generation_priority(task)


def persist_and_register_generation_task(
    task: GenerationTask,
    *,
    workflow_id: str | None,
) -> Any:
    return DEFAULT_TTS_HANDOFF_SERVICE.persist_and_register_generation_task(
        task,
        workflow_id=workflow_id,
    )


def persist_and_register_longform_task(
    task: LongformTask,
    request: GenerateRequest,
) -> Any:
    return DEFAULT_TTS_HANDOFF_SERVICE.persist_and_register_longform_task(
        task,
        request,
    )


def register_task(
    project_id: str,
    segment_id: str,
    task_id: str,
    workflow_id: str | None = None,
) -> Any:
    return DEFAULT_TTS_HANDOFF_SERVICE.register_task(
        project_id,
        segment_id,
        task_id,
        workflow_id,
    )


def persist_generated_history(
    task: GenerationTask,
    history: HistoryItem,
) -> HistoryItem:
    return DEFAULT_TTS_HANDOFF_SERVICE.persist_generated_history(
        task,
        history,
    )


def persist_terminal_longform_task(
    task: LongformTask,
) -> bool:
    return DEFAULT_TTS_HANDOFF_SERVICE.persist_terminal_longform_task(task)


def place_generated_result(
    task: GenerationTask,
    history: HistoryItem,
) -> bool:
    return DEFAULT_TTS_HANDOFF_SERVICE.place_generated_result(
        task,
        history,
    )


def place_generated_result_with_retry(
    task: GenerationTask,
    history: HistoryItem,
) -> bool:
    return DEFAULT_TTS_HANDOFF_SERVICE.place_generated_result_with_retry(
        task,
        history,
    )


def mark_workflow_terminal(
    request: GenerateRequest,
    *,
    status: str,
    error_message: str | None,
    source_id: str | None = None,
) -> Any:
    return DEFAULT_TTS_HANDOFF_SERVICE.mark_workflow_terminal(
        request,
        status=status,
        error_message=error_message,
        source_id=source_id,
    )


def replay_pending(
    *,
    limit: int = 100,
) -> TtsHandoffReplayReport:
    return DEFAULT_TTS_HANDOFF_SERVICE.replay_pending(
        limit=limit,
    )


def start_replay_worker(*, limit: int = 100) -> bool:
    """Replay durable handoff events without blocking API startup."""

    global _REPLAY_WORKER_THREAD
    with _REPLAY_WORKER_LOCK:
        if (
            _REPLAY_WORKER_THREAD is not None
            and _REPLAY_WORKER_THREAD.is_alive()
        ):
            return False

        def run() -> None:
            try:
                report = replay_pending(limit=limit)
                logger.info(
                    "Startup TTS handoff replay inspected=%s applied=%s "
                    "abandoned=%s remaining=%s",
                    report.inspected,
                    report.applied,
                    report.abandoned,
                    report.remaining_pending,
                )
            except Exception:
                logger.exception(
                    "Background video-localization TTS handoff replay failed"
                )

        worker = threading.Thread(
            target=run,
            daemon=True,
            name="video-localization-tts-handoff-replay",
        )
        worker.start()
        _REPLAY_WORKER_THREAD = worker
        return True


__all__ = [
    "DEFAULT_TTS_HANDOFF_SERVICE",
    "TtsHandoffApplicationService",
    "TtsHandoffReplayReport",
    "VideoLocalizationTtsProjection",
    "configure_projection",
    "finalize_submission",
    "mark_workflow_terminal",
    "persist_and_register_generation_task",
    "persist_and_register_longform_task",
    "persist_generated_history",
    "persist_terminal_longform_task",
    "place_generated_result",
    "place_generated_result_with_retry",
    "replay_pending",
    "start_replay_worker",
    "register_task",
]
