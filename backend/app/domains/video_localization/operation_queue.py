from __future__ import annotations

import asyncio
import queue
import threading
import time
from typing import Callable

from app.domains.video_localization import operation_state
from app.domains.video_localization import service
from app.domains.video_localization import source_pipeline
from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationDraft, VideoLocalizationOperation, now_iso
from app.services import project_store

OperationKind = operation_state.OperationKind
OperationStatus = operation_state.OperationStatus

_queue: queue.Queue[str | None] | None = None
_worker_thread: threading.Thread | None = None
_lock = threading.Lock()
_queued_operation_ids: set[str] = set()
_cancelled_operation_ids: set[str] = set()
_operation_commit_gates: dict[str, "_OperationCommitGate"] = {}

_ACTIVE_STATUSES = operation_state.ACTIVE_STATUSES
_TERMINAL_STATUSES = operation_state.TERMINAL_STATUSES
_KIND_LABELS = operation_state.KIND_LABELS


def _asr_stage_id(stage: str) -> str:
    normalized = str(stage or "")
    for stage_id, markers in (
        ("diarization", ("区分说话人",)),
        ("web_research", ("联网核验",)),
        ("text_review", ("校对识别", "文本校对")),
        ("alignment", ("逐词时间码", "强制对齐")),
        ("audio_boundaries", ("声学边界",)),
        ("boundary_review", ("字幕断句", "复核断句")),
        ("subtitle_track", ("生成字幕轨",)),
    ):
        if any(marker in normalized for marker in markers):
            return stage_id
    return "asr"


def _localization_stage_id(stage: str) -> str:
    normalized = str(stage or "")
    for stage_id, markers in (
        ("research", ("查证文化",)),
        ("fit_segments", ("调整字幕长度", "检查字幕长度", "精简过快字幕", "本地精简过快字幕")),
        ("localize", ("生成中文表达", "生成中文口语", "检查中文表达")),
        ("segment_timing", ("安排字幕分段", "匹配字幕分段")),
        ("quality_review", ("复核语义", "复核字幕时间线")),
        ("post_review_constraints", ("确认终审后的字幕限制",)),
        ("write_track", ("写入本土化", "正在保存")),
    ):
        if any(marker in normalized for marker in markers):
            return stage_id
    return "prepare_context"


class _StageTimer:
    """Continuously attributes operation wall time to exactly one visible step."""

    def __init__(self, stage_resolver: Callable[[str], str], *, clock=time.perf_counter):
        self._clock = clock
        self._stage_resolver = stage_resolver
        self._current_stage_id = stage_resolver("")
        self._current_started_at = clock()
        self._completed: dict[str, int] = {}

    def update(self, stage: str) -> dict[str, dict[str, int | bool]]:
        now = self._clock()
        next_stage_id = self._stage_resolver(stage)
        if next_stage_id != self._current_stage_id:
            self._completed[self._current_stage_id] = self._elapsed_ms(now)
            self._current_stage_id = next_stage_id
            self._current_started_at = now
        return self.snapshot(now=now)

    def snapshot(self, *, now: float | None = None) -> dict[str, dict[str, int | bool]]:
        observed_at = self._clock() if now is None else now
        timings: dict[str, dict[str, int | bool]] = {
            stage_id: {"duration_ms": duration_ms} for stage_id, duration_ms in self._completed.items()
        }
        timings[self._current_stage_id] = {
            "duration_ms": self._elapsed_ms(observed_at),
            "running": True,
        }
        return timings

    def finish(self) -> dict[str, dict[str, int]]:
        now = self._clock()
        self._completed[self._current_stage_id] = self._elapsed_ms(now)
        return {stage_id: {"duration_ms": duration_ms} for stage_id, duration_ms in self._completed.items()}

    def _elapsed_ms(self, now: float) -> int:
        return max(0, round((now - self._current_started_at) * 1000))


class _AsrStageTimer(_StageTimer):
    """Backward-compatible ASR timer used by focused timing tests."""

    def __init__(self, *, clock=time.perf_counter):
        super().__init__(_asr_stage_id, clock=clock)


def _finish_stage_timings(stage_timer: _StageTimer | None, summary: dict) -> dict:
    if stage_timer is None:
        return summary
    timings = stage_timer.finish()
    return {
        **summary,
        "task_stage_timings": timings,
        "task_duration_ms": sum(int(item.get("duration_ms") or 0) for item in timings.values()),
    }


class _OperationCommitGate:
    """Linearizes cancellation against one operation's final draft commit."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cancel_requested = False
        self._committed = False

    def request_cancel(self) -> bool:
        with self._lock:
            if self._committed:
                return False
            self._cancel_requested = True
            return True

    def is_cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel_requested

    def commit(self, action: Callable[[], object]) -> tuple[bool, object | None]:
        with self._lock:
            if self._cancel_requested:
                return False, None
            result = action()
            self._committed = True
            return True, result


def _operation_commit_gate(operation_id: str) -> _OperationCommitGate:
    with _lock:
        gate = _operation_commit_gates.get(operation_id)
        if gate is None:
            gate = _OperationCommitGate()
            _operation_commit_gates[operation_id] = gate
        return gate


def start_worker() -> None:
    global _queue, _worker_thread
    with _lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _queue = queue.Queue()
        _worker_thread = threading.Thread(
            target=_worker, args=(_queue,), daemon=True, name="video-localization-operation-worker"
        )
        _worker_thread.start()
    _recover_active_operations()


async def shutdown() -> None:
    global _queue, _worker_thread
    with _lock:
        q = _queue
        thread = _worker_thread
        _queue = None
        _worker_thread = None
        _queued_operation_ids.clear()
        _cancelled_operation_ids.clear()
        _operation_commit_gates.clear()
    if q:
        q.put(None)
    if thread and thread.is_alive():
        await asyncio.to_thread(thread.join, 2)


def list_operations(project_id: str) -> list[VideoLocalizationOperation] | None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return None
    return sorted(draft.operations, key=lambda item: item.created_at, reverse=True)


def list_operation_summaries(project_id: str) -> list[VideoLocalizationOperation] | None:
    operations = list_operations(project_id)
    if operations is None:
        return None
    common_keys = {
        "stage",
        "stage_id",
        "task_stage_timings",
        "task_duration_ms",
        "preview_phase",
        "cue_count",
        "segment_count",
        "localized_subtitle_count",
        "engine_id",
        "language",
        "llm_profile_id",
        "llm_model_id",
        "source_track_id",
    }
    summarized: list[VideoLocalizationOperation] = []
    for operation in operations:
        summary = {
            key: value
            for key, value in operation.result_summary.items()
            if key in common_keys
        }
        if operation.status in _ACTIVE_STATUSES and "preview_cues" in operation.result_summary:
            summary["preview_cues"] = operation.result_summary["preview_cues"]
        summarized.append(operation.model_copy(update={"result_summary": summary}))
    return summarized


def get_operation(project_id: str, operation_id: str) -> VideoLocalizationOperation | None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return None
    return next((operation for operation in draft.operations if operation.operation_id == operation_id), None)


def cancel(project_id: str, operation_id: str) -> VideoLocalizationOperation | None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return None
    operation = _operation_from_draft(draft, operation_id)
    if not operation:
        raise AppException(404, "VIDEO_LOCALIZATION_OPERATION_NOT_FOUND", "Operation not found")
    if operation.status not in _ACTIVE_STATUSES:
        return operation

    gate = _operation_commit_gate(operation_id)
    if not gate.request_cancel():
        return get_operation(project_id, operation_id) or operation

    with _lock:
        _cancelled_operation_ids.add(operation_id)

    completed_at = now_iso() if operation.status == "queued" else None
    updates = {
        "status": "cancelled",
        "cancel_requested": True,
        "completed_at": completed_at,
        "error_message": "已取消" if operation.status == "queued" else "已请求取消；当前处理结束后会丢弃任务结果。",
        "result_summary": {"stage": "已取消" if operation.status == "queued" else "正在取消", "preview_cues": []},
    }
    _mark_operation(project_id, operation_id, kind=operation.kind, **updates)
    updated = get_operation(project_id, operation_id)
    return updated or operation.model_copy(update=updates)


def _cancel_requested(project_id: str, operation_id: str) -> bool:
    gate = _operation_commit_gate(operation_id)
    if gate.is_cancel_requested():
        return True
    with _lock:
        if operation_id in _cancelled_operation_ids:
            return True
    return operation_state.operation_was_cancelled(get_operation(project_id, operation_id))


def retry(project_id: str, operation_id: str) -> VideoLocalizationOperation | None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return None
    operation = _operation_from_draft(draft, operation_id)
    if not operation:
        raise AppException(404, "VIDEO_LOCALIZATION_OPERATION_NOT_FOUND", "Operation not found")
    if operation.status in _ACTIVE_STATUSES:
        raise AppException(409, "VIDEO_LOCALIZATION_OPERATION_ACTIVE", "Operation is still active")
    service.save_video_localization(project_id, operation_state.with_kind_status(draft, operation.kind, "queued"))
    return submit(project_id, operation.kind, operation.parameters)


def _normalized_operation_parameters(
    kind: OperationKind,
    parameters: dict | None,
    draft: VideoLocalizationDraft,
) -> dict:
    normalized = {key: value for key, value in (parameters or {}).items() if key != "scope"}
    if kind == "english_asr":
        normalized.update(
            {
                "engine_id": str(normalized.get("engine_id") or source_pipeline.DEFAULT_ENGLISH_ASR_ENGINE_ID),
                "source_track_id": str(normalized.get("source_track_id") or "auto"),
                "source_language": source_pipeline.normalize_source_language(
                    str(normalized.get("source_language") or draft.language_config.source_language)
                ),
                "segmentation_profile_id": str(normalized.get("segmentation_profile_id") or "generic_zh"),
                "diarization_engine_id": str(normalized.get("diarization_engine_id") or "auto"),
            }
        )
    elif kind == "localization_draft":
        normalized.update(
            {
                "source_language": source_pipeline.normalize_source_language(
                    str(normalized.get("source_language") or draft.language_config.source_language)
                ),
                "target_language": str(
                    normalized.get("target_language") or draft.language_config.target_language
                ),
                "profile_id": str(normalized.get("profile_id") or ""),
                "localization_level": str(normalized.get("localization_level") or "L1"),
                "worldview_permeability": str(normalized.get("worldview_permeability") or "W0"),
            }
        )
    normalized["scope"] = operation_state.operation_scope(kind, normalized)
    return normalized


def submit(project_id: str, kind: OperationKind, parameters: dict | None = None) -> VideoLocalizationOperation | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = service.get_video_localization(project_id) or VideoLocalizationDraft()
    operation_state.validate_prerequisites(kind, draft, parameters)

    operation_parameters = _normalized_operation_parameters(kind, parameters, draft)

    active = operation_state.active_operation_for_kind(draft, kind)
    if active:
        active_parameters = _normalized_operation_parameters(kind, active.parameters, draft)
        if active_parameters != operation_parameters:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_OPERATION_PARAMETERS_CONFLICT",
                "同类任务正在使用另一组参数处理，请等待当前任务结束后再试。",
            )
        _enqueue(active.operation_id)
        return active

    operation = VideoLocalizationOperation(
        project_id=project_id,
        kind=kind,
        label=_KIND_LABELS[kind],
        parameters=operation_parameters,
    )
    draft = operation_state.with_operation(draft, operation)
    draft = operation_state.with_kind_status(draft, kind, "queued")
    service.save_video_localization(project_id, draft)
    _enqueue(operation.operation_id)
    return operation


def _worker(task_queue: queue.Queue[str | None]) -> None:
    while True:
        operation_id = task_queue.get()
        if operation_id is None:
            task_queue.task_done()
            return
        with _lock:
            _queued_operation_ids.discard(operation_id)
        try:
            _process(operation_id)
        finally:
            with _lock:
                _cancelled_operation_ids.discard(operation_id)
                _operation_commit_gates.pop(operation_id, None)
            task_queue.task_done()


def _process(operation_id: str) -> None:
    project_id, operation = _find_operation(operation_id)
    if not project_id or not operation:
        return
    if operation.status == "cancelled" or operation.cancel_requested:
        _mark_operation(
            project_id,
            operation_id,
            kind=operation.kind,
            status="cancelled",
            completed_at=operation.completed_at or now_iso(),
        )
        return

    commit_gate = _operation_commit_gate(operation_id)

    _mark_operation(
        project_id,
        operation_id,
        kind=operation.kind,
        status="running",
        progress=0.05,
        started_at=now_iso(),
        result_summary={"stage": "准备处理"},
    )
    stage_timer: _StageTimer | None = None
    try:
        if operation.kind == "source_audio":
            updated = service.extract_source_audio(project_id, commit_guard=commit_gate.commit)
            summary = operation_state.source_audio_summary(updated)
        elif operation.kind == "stems":
            updated = service.separate_source_audio(project_id, commit_guard=commit_gate.commit)
            summary = operation_state.stems_summary(updated)
        elif operation.kind == "english_asr":
            engine_id = str(operation.parameters.get("engine_id") or source_pipeline.DEFAULT_ENGLISH_ASR_ENGINE_ID)
            source_track_id = str(operation.parameters.get("source_track_id") or "auto")
            source_language = source_pipeline.normalize_source_language(
                str(operation.parameters.get("source_language") or "auto")
            )
            segmentation_profile_id = str(operation.parameters.get("segmentation_profile_id") or "generic_zh")
            diarization_engine_id = str(operation.parameters.get("diarization_engine_id") or "auto")
            stage_timer = _StageTimer(_asr_stage_id)

            def report_asr_progress(progress: float, stage: str) -> None:
                assert stage_timer is not None
                _mark_operation(
                    project_id,
                    operation_id,
                    kind=operation.kind,
                    status="running",
                    progress=progress,
                    result_summary={
                        "stage": stage,
                        "stage_id": _asr_stage_id(stage),
                        "task_stage_timings": stage_timer.update(stage),
                    },
                )

            updated = service.transcribe_english_source_audio(
                project_id,
                engine_id=engine_id,
                source_track_id=source_track_id,
                source_language=source_language,
                is_cancelled=lambda: _cancel_requested(project_id, operation_id),
                on_progress=report_asr_progress,
                on_preview=lambda phase, cues: _mark_operation(
                    project_id,
                    operation_id,
                    kind=operation.kind,
                    status="running",
                    result_summary={"preview_phase": phase, "preview_cues": cues},
                ),
                segmentation_profile_id=segmentation_profile_id,
                diarization_engine_id=diarization_engine_id,
                commit_guard=commit_gate.commit,
            )
            summary = operation_state.english_asr_summary(updated)
            summary = _finish_stage_timings(stage_timer, summary)
        elif operation.kind == "localization_draft":
            stage_timer = _StageTimer(_localization_stage_id)

            def report_localization_progress(progress: float, stage: str) -> None:
                assert stage_timer is not None
                _mark_operation(
                    project_id,
                    operation_id,
                    kind=operation.kind,
                    status="running",
                    progress=progress,
                    result_summary={
                        "stage": stage,
                        "stage_id": _localization_stage_id(stage),
                        "task_stage_timings": stage_timer.update(stage),
                    },
                )

            updated, summary = service.run_localization_draft(
                project_id,
                source_language=str(operation.parameters.get("source_language") or "auto"),
                target_language=str(operation.parameters.get("target_language") or "") or None,
                profile_id=str(operation.parameters.get("profile_id") or "") or None,
                localization_level=str(operation.parameters.get("localization_level") or "L1"),
                worldview_permeability=str(operation.parameters.get("worldview_permeability") or "W0"),
                is_cancelled=lambda: _cancel_requested(project_id, operation_id),
                on_progress=report_localization_progress,
                on_preview=lambda phase, cues: _mark_operation(
                    project_id,
                    operation_id,
                    kind=operation.kind,
                    status="running",
                    result_summary={"preview_phase": phase, "preview_cues": cues},
                ),
                commit_guard=commit_gate.commit,
            )
            summary = _finish_stage_timings(stage_timer, summary)
        elif operation.kind == "reference_clips":
            updated = service.create_reference_clips_from_cues(project_id)
            summary = operation_state.reference_clips_summary(updated)
        else:
            raise AppException(
                400, "VIDEO_LOCALIZATION_OPERATION_UNSUPPORTED", f"Unsupported operation: {operation.kind}"
            )
        if updated is None:
            raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
        latest = get_operation(project_id, operation_id)
        if operation_state.operation_was_cancelled(latest):
            cancellation_summary = _finish_stage_timings(
                stage_timer,
                dict(latest.result_summary if latest is not None else {}),
            )
            _mark_operation(
                project_id,
                operation_id,
                kind=operation.kind,
                status="cancelled",
                progress=1.0,
                completed_at=now_iso(),
                error_message="已取消，任务结果未作为成功状态保留。",
                result_summary=cancellation_summary,
            )
            return
        _mark_operation(
            project_id,
            operation_id,
            kind=operation.kind,
            status="success",
            progress=1.0,
            completed_at=now_iso(),
            result_summary=summary,
        )
    except AppException as exc:
        latest = get_operation(project_id, operation_id)
        if operation_state.operation_was_cancelled(latest):
            cancellation_summary = _finish_stage_timings(
                stage_timer,
                dict(latest.result_summary if latest is not None else {}),
            )
            _mark_operation(
                project_id,
                operation_id,
                kind=operation.kind,
                status="cancelled",
                progress=1.0,
                completed_at=now_iso(),
                error_message="已取消，失败结果未保留。",
                result_summary=cancellation_summary,
            )
            return
        failure_summary = _finish_stage_timings(
            stage_timer,
            dict(latest.result_summary if latest is not None else {}),
        )
        if exc.detail_dict:
            failure_summary["error_detail"] = exc.detail_dict
        _mark_kind_failed(project_id, operation.kind, exc.code, exc.message)
        _mark_operation(
            project_id,
            operation_id,
            kind=operation.kind,
            status="failed",
            progress=1.0,
            completed_at=now_iso(),
            error_code=exc.code,
            error_message=exc.message,
            result_summary=failure_summary,
        )
    except Exception as exc:
        latest = get_operation(project_id, operation_id)
        if operation_state.operation_was_cancelled(latest):
            cancellation_summary = _finish_stage_timings(
                stage_timer,
                dict(latest.result_summary if latest is not None else {}),
            )
            _mark_operation(
                project_id,
                operation_id,
                kind=operation.kind,
                status="cancelled",
                progress=1.0,
                completed_at=now_iso(),
                error_message="已取消，失败结果未保留。",
                result_summary=cancellation_summary,
            )
            return
        failure_summary = _finish_stage_timings(
            stage_timer,
            dict(latest.result_summary if latest is not None else {}),
        )
        _mark_kind_failed(project_id, operation.kind, "VIDEO_LOCALIZATION_OPERATION_FAILED", str(exc))
        _mark_operation(
            project_id,
            operation_id,
            kind=operation.kind,
            status="failed",
            progress=1.0,
            completed_at=now_iso(),
            error_code="VIDEO_LOCALIZATION_OPERATION_FAILED",
            error_message=str(exc),
            result_summary=failure_summary,
        )


def _enqueue(operation_id: str) -> None:
    start_worker()
    with _lock:
        if operation_id in _queued_operation_ids:
            return
        _queued_operation_ids.add(operation_id)
        q = _queue
    if q is not None:
        q.put(operation_id)


def _recover_active_operations() -> None:
    for project in project_store.list_projects():
        raw = project.parameters.get(service.VIDEO_LOCALIZATION_KEY) or {}
        draft = VideoLocalizationDraft(**raw)
        changed = False
        for operation in draft.operations:
            if operation.cancel_requested:
                operation.status = "cancelled"
                operation.completed_at = operation.completed_at or now_iso()
                changed = True
            elif operation.status in _ACTIVE_STATUSES:
                operation.status = "queued"
                operation.started_at = None
                operation.error_message = None
                changed = True
        if changed:
            service.save_video_localization(project.project_id, draft)
        for operation in draft.operations:
            if operation.status == "queued":
                _enqueue(operation.operation_id)


def _find_operation(operation_id: str) -> tuple[str | None, VideoLocalizationOperation | None]:
    for project in project_store.list_projects():
        raw = project.parameters.get(service.VIDEO_LOCALIZATION_KEY) or {}
        draft = VideoLocalizationDraft(**raw)
        for operation in draft.operations:
            if operation.operation_id == operation_id:
                return project.project_id, operation
    return None, None


def _operation_from_draft(draft: VideoLocalizationDraft, operation_id: str) -> VideoLocalizationOperation | None:
    return operation_state.operation_from_draft(draft, operation_id)


def _mark_operation(project_id: str, operation_id: str, *, kind: OperationKind | None = None, **updates) -> None:
    def apply(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
        next_operations = []
        applied = False
        for operation in draft.operations:
            if operation.operation_id == operation_id:
                incoming_status = updates.get("status")
                if (
                    operation.cancel_requested or operation.status in _TERMINAL_STATUSES
                ) and incoming_status in _ACTIVE_STATUSES:
                    next_operations.append(operation)
                else:
                    operation_updates = dict(updates)
                    if incoming_status in _ACTIVE_STATUSES and isinstance(
                        operation_updates.get("result_summary"), dict
                    ):
                        operation_updates["result_summary"] = {
                            **operation.result_summary,
                            **operation_updates["result_summary"],
                        }
                    next_operations.append(operation.model_copy(update=operation_updates))
                    applied = True
            else:
                next_operations.append(operation)
        next_draft = draft.model_copy(update={"operations": next_operations})
        status = updates.get("status")
        if applied and kind and status in _TERMINAL_STATUSES.union(_ACTIVE_STATUSES):
            next_draft = operation_state.with_kind_status(next_draft, kind, status)
        return next_draft

    service.update_video_localization_atomic(project_id, apply)


def _mark_kind_failed(project_id: str, kind: OperationKind, code: str, message: str) -> None:
    service.update_video_localization_atomic(
        project_id,
        lambda draft: operation_state.with_kind_status(
            draft,
            kind,
            "failed",
            error_code=code,
            error_message=message,
        ),
    )
