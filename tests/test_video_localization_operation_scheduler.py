from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import operation_queue  # noqa: E402
from app.domains.video_localization.operation_runtime import (  # noqa: E402
    OperationRuntime,
)
from app.domains.video_localization.operation_scheduler import (  # noqa: E402
    DEFAULT_WORKER_COUNT,
    ProjectFairOperationScheduler,
    parse_worker_count,
)


def test_scheduler_round_robins_projects_without_same_project_overlap():
    scheduler = ProjectFairOperationScheduler(worker_count=2)
    operations = [
        ("project-1", "operation-1"),
        ("project-1", "operation-2"),
        ("project-2", "operation-1"),
        ("project-3", "operation-1"),
    ]
    for operation in operations:
        assert scheduler.put(operation) is True

    first = scheduler.get()
    second = scheduler.get()
    assert first == operations[0]
    assert second == operations[2]

    scheduler.complete(first)
    third = scheduler.get()
    assert third == operations[3]

    scheduler.complete(second)
    fourth = scheduler.get()
    assert fourth == operations[1]

    for operation in (third, fourth):
        scheduler.complete(operation)
    scheduler.close()
    assert scheduler.get() is None


def test_worker_pool_is_bounded_and_serializes_each_project(
    monkeypatch,
):
    scheduler = ProjectFairOperationScheduler(worker_count=2)
    runtime = OperationRuntime()
    release = threading.Event()
    two_projects_started = threading.Event()
    state_lock = threading.Lock()
    active_total = 0
    active_by_project: dict[str, int] = {}
    max_active_total = 0
    max_active_by_project: dict[str, int] = {}
    started: list[tuple[str, str]] = []

    def process(project_id: str, operation_id: str) -> None:
        nonlocal active_total, max_active_total
        with state_lock:
            active_total += 1
            active_by_project[project_id] = (
                active_by_project.get(project_id, 0) + 1
            )
            max_active_total = max(max_active_total, active_total)
            max_active_by_project[project_id] = max(
                max_active_by_project.get(project_id, 0),
                active_by_project[project_id],
            )
            started.append((project_id, operation_id))
            if {
                item[0]
                for item in started
            } >= {"project-1", "project-2"}:
                two_projects_started.set()
        assert release.wait(timeout=2)
        with state_lock:
            active_total -= 1
            active_by_project[project_id] -= 1

    monkeypatch.setattr(operation_queue, "_runtime", runtime)
    monkeypatch.setattr(operation_queue, "_process", process)
    operations = [
        ("project-1", "operation-1"),
        ("project-1", "operation-2"),
        ("project-2", "operation-1"),
    ]
    for operation in operations:
        assert runtime.enqueue_once(operation) is True
        assert scheduler.put(operation) is True

    workers = [
        threading.Thread(
            target=operation_queue._worker,
            args=(scheduler,),
        )
        for _index in range(2)
    ]
    for worker in workers:
        worker.start()

    assert two_projects_started.wait(timeout=2)
    with state_lock:
        assert ("project-1", "operation-2") not in started
    release.set()
    scheduler.close()
    for worker in workers:
        worker.join(timeout=2)

    assert all(not worker.is_alive() for worker in workers)
    assert set(started) == set(operations)
    assert max_active_total == 2
    assert max(max_active_by_project.values()) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DEFAULT_WORKER_COUNT),
        ("", DEFAULT_WORKER_COUNT),
        ("1", 1),
        ("4", 4),
    ],
)
def test_parse_worker_count_accepts_bounded_values(value, expected):
    assert parse_worker_count(value) == expected


@pytest.mark.parametrize("value", ["zero", "0", "5", "-1"])
def test_parse_worker_count_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_worker_count(value)


def test_start_worker_creates_configured_pool_and_shutdown_closes_it(
    monkeypatch,
):
    asyncio.run(operation_queue.shutdown())
    monkeypatch.setenv(
        "VOICE_STUDIO_VIDEO_LOCALIZATION_WORKERS",
        "2",
    )
    monkeypatch.setattr(
        operation_queue.video_localization_operation_store,
        "backfill_legacy_projects",
        lambda: None,
    )
    monkeypatch.setattr(
        operation_queue,
        "_recover_active_operations",
        lambda: None,
    )
    scheduler = None
    threads: list[threading.Thread] = []

    try:
        operation_queue.start_worker()
        scheduler = operation_queue._scheduler
        assert scheduler is not None
        assert scheduler.worker_count == 2
        assert len(operation_queue._worker_threads) == 2
        threads = list(operation_queue._worker_threads)
        assert all(
            worker.is_alive()
            for worker in threads
        )
    finally:
        asyncio.run(operation_queue.shutdown())

    assert scheduler is not None
    assert scheduler.closed is True
    assert all(not worker.is_alive() for worker in threads)
