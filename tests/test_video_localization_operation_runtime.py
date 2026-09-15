from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.operation_runtime import (  # noqa: E402
    OperationRuntime,
    OperationRuntimeKey,
)
from app.domains.video_localization import operation_queue  # noqa: E402
from app.domains.video_localization.operation_scheduler import (  # noqa: E402
    ProjectFairOperationScheduler,
)

FIRST_KEY: OperationRuntimeKey = ("project-1", "operation-1")
SECOND_KEY: OperationRuntimeKey = ("project-2", "operation-1")


def test_failed_parent_closes_running_and_waiting_child_steps():
    summary = operation_queue._close_unfinished_task_steps_after_failure(
        {
            "error_detail": {
                "code": "QUALITY_GATE_BLOCKED",
                "message": "质量门未通过。",
            },
            "task_step_results": {
                "completed": {"status": "success", "summary": "已完成。"},
                "current": {"status": "running", "summary": "处理中。"},
                "downstream": {"status": "todo", "summary": ""},
            },
        }
    )

    assert summary["task_step_results"]["completed"]["status"] == "success"
    assert summary["task_step_results"]["current"] == {
        "status": "failed",
        "summary": "质量门未通过。",
        "error_detail": {
            "code": "QUALITY_GATE_BLOCKED",
            "message": "质量门未通过。",
        },
    }
    assert summary["task_step_results"]["downstream"] == {
        "status": "cancelled",
        "summary": "前置步骤失败，本步骤未执行。",
    }


def test_interrupted_parent_closes_steps_without_summary_error_detail():
    summary = operation_queue._close_unfinished_task_steps_after_failure(
        {
            "interrupted": True,
            "task_step_results": {
                "completed": {"status": "success", "summary": "已完成。"},
                "current": {"status": "running", "summary": "处理中。"},
                "downstream": {"status": "todo", "summary": ""},
            },
            "task_stage_timings": {
                "completed": {"duration_ms": 100},
                "current": {"duration_ms": 0, "running": True},
            },
        },
        error_code="VIDEO_LOCALIZATION_OPERATION_INTERRUPTED",
        error_message="服务停止，任务已中断。",
    )

    assert summary["task_step_results"]["completed"]["status"] == "success"
    assert summary["task_step_results"]["current"] == {
        "status": "failed",
        "summary": "服务停止，任务已中断。",
        "error_detail": {
            "code": "VIDEO_LOCALIZATION_OPERATION_INTERRUPTED",
            "message": "服务停止，任务已中断。",
        },
    }
    assert summary["task_step_results"]["downstream"] == {
        "status": "cancelled",
        "summary": "前置步骤失败，本步骤未执行。",
    }
    assert summary["task_stage_timings"]["current"] == {"duration_ms": 0}


def test_failed_operation_detail_repairs_legacy_nonterminal_child_states():
    operation = operation_queue.VideoLocalizationOperation(
        project_id="project-1",
        operation_id="operation-1",
        kind="localization_draft",
        status="failed",
        error_code="VIDEO_LOCALIZATION_OPERATION_INTERRUPTED",
        error_message="服务停止，任务已中断。",
        result_summary={
            "task_step_results": {
                "current": {"status": "running", "summary": "处理中。"},
                "downstream": {"status": "todo", "summary": ""},
            },
            "task_stage_timings": {
                "current": {"duration_ms": 0, "running": True},
            },
        },
    )

    projected = operation_queue._project_operation_detail(
        "project-1",
        operation,
    )

    assert (
        projected.result_summary["task_step_results"]["current"]["status"]
        == "failed"
    )
    assert (
        projected.result_summary["task_step_results"]["downstream"]["status"]
        == "cancelled"
    )
    assert (
        projected.result_summary["task_stage_timings"]["current"]
        == {"duration_ms": 0}
    )


def test_cancelled_operation_detail_repairs_legacy_nonterminal_child_states():
    operation = operation_queue.VideoLocalizationOperation(
        project_id="project-1",
        operation_id="operation-1",
        kind="localization_draft",
        status="cancelled",
        result_summary={
            "task_step_results": {
                "completed": {"status": "success", "summary": "已完成。"},
                "current": {"status": "running", "summary": "处理中。"},
                "downstream": {"status": "todo", "summary": ""},
            },
            "task_stage_timings": {
                "current": {"duration_ms": 0, "running": True},
            },
        },
    )

    projected = operation_queue._project_operation_detail(
        "project-1",
        operation,
    )

    assert (
        projected.result_summary["task_step_results"]["completed"]["status"]
        == "success"
    )
    assert (
        projected.result_summary["task_step_results"]["current"]["status"]
        == "cancelled"
    )
    assert (
        projected.result_summary["task_step_results"]["downstream"]["status"]
        == "cancelled"
    )
    assert (
        projected.result_summary["task_stage_timings"]["current"]
        == {"duration_ms": 0}
    )


def test_terminal_operation_update_closes_children_before_persistence():
    failed = operation_queue._normalize_terminal_operation_updates(
        {
            "status": "failed",
            "error_code": "STEP_FAILED",
            "error_message": "当前步骤失败。",
            "result_summary": {
                "task_step_results": {
                    "current": {"status": "running"},
                    "next": {"status": "todo"},
                }
            },
        }
    )
    cancelled = operation_queue._normalize_terminal_operation_updates(
        {
            "status": "cancelled",
            "result_summary": {
                "task_step_results": {
                    "current": {"status": "running"},
                    "next": {"status": "todo"},
                }
            },
        }
    )

    assert failed["completed_at"]
    assert failed["result_summary"]["task_step_results"]["current"][
        "status"
    ] == "failed"
    assert failed["result_summary"]["task_step_results"]["next"][
        "status"
    ] == "cancelled"
    assert cancelled["completed_at"]
    assert cancelled["result_summary"]["task_step_results"]["current"][
        "status"
    ] == "cancelled"
    assert cancelled["result_summary"]["task_step_results"]["next"][
        "status"
    ] == "cancelled"


def test_success_parent_is_rejected_when_a_child_failed():
    normalized = operation_queue._normalize_terminal_operation_updates(
        {
            "status": "success",
            "result_summary": {
                "task_step_results": {
                    "completed": {"status": "success"},
                    "quality_gate": {
                        "label": "检查本土化结果",
                        "status": "failed",
                    },
                }
            },
        }
    )

    assert normalized["status"] == "failed"
    assert normalized["error_code"] == (
        "VIDEO_LOCALIZATION_CHILD_STEP_FAILED"
    )
    assert "检查本土化结果" in normalized["error_message"]


def test_runtime_deduplicates_pending_and_active_process_work():
    runtime = OperationRuntime()

    assert runtime.enqueue_once(FIRST_KEY) is True
    assert runtime.enqueue_once(FIRST_KEY) is False
    assert runtime.enqueue_once(SECOND_KEY) is True

    runtime.mark_dequeued(FIRST_KEY)

    assert runtime.enqueue_once(FIRST_KEY) is False
    runtime.complete(FIRST_KEY)
    assert runtime.enqueue_once(FIRST_KEY) is True


def test_worker_routes_same_operation_id_by_project(monkeypatch):
    runtime = OperationRuntime()
    task_queue = ProjectFairOperationScheduler(worker_count=1)
    processed: list[OperationRuntimeKey] = []
    monkeypatch.setattr(operation_queue, "_runtime", runtime)
    monkeypatch.setattr(
        operation_queue,
        "_process",
        lambda project_id, operation_id: processed.append(
            (project_id, operation_id)
        ),
    )
    for operation_key in (FIRST_KEY, SECOND_KEY):
        runtime.enqueue_once(operation_key)
        task_queue.put(operation_key)
    task_queue.close()

    operation_queue._worker(task_queue)

    assert processed == [FIRST_KEY, SECOND_KEY]


def test_cancel_token_remains_visible_when_gate_lifecycle_changes():
    runtime = OperationRuntime()
    gate = runtime.commit_gate(FIRST_KEY)

    assert gate.request_cancel() is True
    runtime.mark_cancelled(FIRST_KEY)
    assert runtime.cancellation_requested(FIRST_KEY) is True
    assert runtime.cancellation_requested(SECOND_KEY) is False

    runtime.complete(FIRST_KEY)

    assert runtime.cancellation_requested(FIRST_KEY) is False


def test_cancel_and_final_commit_are_linearized():
    runtime = OperationRuntime()
    gate = runtime.commit_gate(FIRST_KEY)
    action_started = threading.Event()
    release_action = threading.Event()
    result: list[tuple[bool, object | None]] = []

    def commit_action():
        action_started.set()
        release_action.wait(timeout=1)
        return "saved"

    commit_thread = threading.Thread(
        target=lambda: result.append(gate.commit(commit_action))
    )
    commit_thread.start()
    assert action_started.wait(timeout=1)

    cancel_result: list[bool] = []
    cancel_thread = threading.Thread(
        target=lambda: cancel_result.append(gate.request_cancel())
    )
    cancel_thread.start()
    release_action.set()
    commit_thread.join(timeout=1)
    cancel_thread.join(timeout=1)

    assert result == [(True, "saved")]
    assert cancel_result == [False]


def test_cancel_before_commit_rejects_late_content_write():
    runtime = OperationRuntime()
    gate = runtime.commit_gate(FIRST_KEY)

    assert gate.request_cancel() is True
    assert gate.commit(lambda: "late write") == (False, None)


def test_durable_cancel_is_exposed_only_after_persistence_succeeds():
    runtime = OperationRuntime()
    gate = runtime.commit_gate(FIRST_KEY)

    assert gate.commit_cancel(lambda: "saved") == (True, "saved")
    assert gate.is_cancel_requested() is True
    assert gate.commit(lambda: "late write") == (False, None)


def test_failed_cancel_persistence_does_not_poison_local_gate():
    runtime = OperationRuntime()
    gate = runtime.commit_gate(FIRST_KEY)

    with pytest.raises(RuntimeError, match="write failed"):
        gate.commit_cancel(
            lambda: (_ for _ in ()).throw(
                RuntimeError("write failed")
            )
        )

    assert gate.is_cancel_requested() is False
    assert gate.commit(lambda: "saved") == (True, "saved")


def test_cancel_persistence_does_not_hold_gate_or_publish_early():
    runtime = OperationRuntime()
    gate = runtime.commit_gate(FIRST_KEY)
    persistence_started = threading.Event()
    release_persistence = threading.Event()
    cancellation_finished = threading.Event()

    def persist() -> str:
        persistence_started.set()
        assert release_persistence.wait(timeout=1)
        return "persisted"

    def cancel() -> None:
        gate.commit_cancel(persist)
        cancellation_finished.set()

    thread = threading.Thread(target=cancel)
    thread.start()
    assert persistence_started.wait(timeout=1)
    commit_result: list[tuple[bool, object | None]] = []
    commit_finished = threading.Event()

    def commit() -> None:
        commit_result.append(gate.commit(lambda: "late"))
        commit_finished.set()

    commit_thread = threading.Thread(target=commit)
    commit_thread.start()

    assert gate.is_cancel_requested() is False
    assert cancellation_finished.is_set() is False
    assert commit_finished.wait(timeout=0.05) is False

    release_persistence.set()
    thread.join(timeout=1)
    commit_thread.join(timeout=1)

    assert cancellation_finished.is_set()
    assert commit_finished.is_set()
    assert gate.is_cancel_requested() is True
    assert commit_result == [(False, None)]


def test_recovery_timer_survives_queue_completion_and_fires_once():
    runtime = OperationRuntime()
    fired: list[OperationRuntimeKey] = []
    now_ms = time.time_ns() // 1_000_000

    runtime.schedule_recovery(
        FIRST_KEY,
        deadline_ms=now_ms + 20,
        callback=fired.append,
    )
    runtime.complete(FIRST_KEY)
    deadline = time.monotonic() + 1
    while not fired and time.monotonic() < deadline:
        time.sleep(0.01)

    assert fired == [FIRST_KEY]


def test_recovery_timer_keeps_earlier_wake_and_reset_cancels():
    runtime = OperationRuntime()
    fired: list[OperationRuntimeKey | str] = []
    now_ms = time.time_ns() // 1_000_000

    runtime.schedule_recovery(
        FIRST_KEY,
        deadline_ms=now_ms + 20,
        callback=fired.append,
    )
    runtime.schedule_recovery(
        FIRST_KEY,
        deadline_ms=now_ms + 5_000,
        callback=lambda operation_id: fired.append(
            f"late-{operation_id}"
        ),
    )
    deadline = time.monotonic() + 1
    while not fired and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fired == [FIRST_KEY]

    runtime.schedule_recovery(
        SECOND_KEY,
        deadline_ms=now_ms + 5_000,
        callback=fired.append,
    )
    runtime.reset()
    time.sleep(0.1)
    assert fired == [FIRST_KEY]
