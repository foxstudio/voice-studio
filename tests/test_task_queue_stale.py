from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models.schemas import TaskStatus
from app.services import task_queue


class FakeDb:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def upsert(self, table: str, key: str, data: dict, *_args):
        assert table == "tasks"
        self.rows[key] = dict(data)

    def get_one(self, table: str, key_field: str, key: str):
        assert table == "tasks"
        return self.rows.get(key)

    def list_all(self, table: str, *_args):
        assert table == "tasks"
        return list(self.rows.values())

    def delete_one(self, table: str, key_field: str, key: str):
        assert table == "tasks"
        self.rows.pop(key, None)


@pytest.fixture
def fake_task_db(monkeypatch):
    fake_db = FakeDb()
    monkeypatch.setattr(task_queue, "db", fake_db)
    return fake_db


def task_row(task_id: str, started_delta: timedelta):
    return {
        "task_id": task_id,
        "task_type": "single",
        "engine_id": "indextts-v2",
        "voice_id": None,
        "project_id": None,
        "segment_id": None,
        "input_text": "测试文本",
        "status": "running",
        "progress": 0.3,
        "parameters": {"text": "测试文本", "engine_id": "indextts-v2"},
        "created_at": "2026-06-08T00:00:00",
        "started_at": (datetime.now() - started_delta).isoformat(timespec="seconds"),
    }


def queued_task_row(task_id: str):
    row = task_row(task_id, timedelta(minutes=0))
    row.update({"status": "queued", "progress": 0.0, "started_at": None})
    return row


def test_get_task_marks_stale_running_task_failed(fake_task_db):
    fake_task_db.upsert("tasks", "task-1", task_row("task-1", timedelta(minutes=30)))

    task = task_queue.get_task("task-1")

    assert task is not None
    assert task.status == TaskStatus.failed
    assert task.completed_at
    assert "常规超时窗口" in (task.error_message or "")


def test_get_task_keeps_fresh_running_task_active(fake_task_db):
    fake_task_db.upsert("tasks", "task-1", task_row("task-1", timedelta(minutes=2)))

    task = task_queue.get_task("task-1")

    assert task is not None
    assert task.status == TaskStatus.running
    assert task.completed_at is None


def test_recover_incomplete_tasks_requeues_waiting_and_fresh_running_tasks(fake_task_db):
    fake_task_db.upsert("tasks", "queued-1", queued_task_row("queued-1"))
    fake_task_db.upsert("tasks", "running-1", task_row("running-1", timedelta(minutes=2)))

    recovered = task_queue._recover_incomplete_tasks()

    assert recovered == ["queued-1", "running-1"]
    queued = task_queue.get_task("queued-1")
    running = task_queue.get_task("running-1")
    assert queued is not None
    assert queued.status == TaskStatus.queued
    assert running is not None
    assert running.status == TaskStatus.queued
    assert running.progress == 0
    assert running.started_at is None
    assert "重新排队" in (running.error_message or "")


def test_recover_incomplete_tasks_marks_stale_running_failed(fake_task_db):
    fake_task_db.upsert("tasks", "stale-1", task_row("stale-1", timedelta(minutes=30)))

    recovered = task_queue._recover_incomplete_tasks()

    stale = task_queue.get_task("stale-1")
    assert recovered == []
    assert stale is not None
    assert stale.status == TaskStatus.failed


def test_cancel_running_task_marks_it_cancelled(fake_task_db):
    fake_task_db.upsert("tasks", "task-1", task_row("task-1", timedelta(minutes=2)))

    result = task_queue.cancel_task("task-1")

    task = task_queue.get_task("task-1")
    assert result == {"task_id": "task-1", "status": "cancelled"}
    assert task is not None
    assert task.status == TaskStatus.cancelled
    assert task.completed_at
