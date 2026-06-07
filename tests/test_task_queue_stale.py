from __future__ import annotations

from datetime import datetime, timedelta

import pytest

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
