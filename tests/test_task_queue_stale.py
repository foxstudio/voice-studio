from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import (
    GenerationTask,
    Project,
    ScriptSegment,
    SegmentStatus,
    TaskStatus,
)
from app.services import project_store, task_queue


class FakeDb:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def upsert(self, table: str, key: str, data: dict, *_args):
        assert table == "tasks"
        self.rows[key] = dict(data)

    def get_one(self, table: str, key_field: str, key: str):
        assert table == "tasks"
        return self.rows.get(key)

    def list_all(self, table: str, *_args, **kwargs):
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


def test_get_task_marks_stale_cloud_result_uncertain(
    fake_task_db,
):
    row = task_row("cloud-stale", timedelta(minutes=30))
    row["engine_id"] = "doubao-tts-preset"
    row["parameters"]["engine_id"] = "doubao-tts-preset"
    fake_task_db.upsert("tasks", "cloud-stale", row)

    task = task_queue.get_task("cloud-stale")

    assert task is not None
    assert task.status == TaskStatus.failed
    assert task.provider_state_uncertain is True
    assert (
        task.error_message
        == task_queue.CLOUD_RESULT_UNKNOWN_MESSAGE
    )


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


def test_update_segment_result_skips_unknown_and_unchanged_segments(
    monkeypatch,
):
    segment = ScriptSegment(
        segment_id="segment-1",
        index=0,
        text="测试文本",
        status=SegmentStatus.completed,
        result_audio_id="audio-1",
        result_id="result-1",
    )
    project = Project(
        project_id="project-1",
        name="脚本项目",
        segments=[segment],
    )
    saves: list[Project] = []
    monkeypatch.setattr(
        project_store,
        "get_project",
        lambda _project_id: project,
    )
    monkeypatch.setattr(
        project_store,
        "save_project",
        lambda saved: saves.append(saved) or saved,
    )

    assert (
        project_store.update_segment_result(
            project.project_id,
            "missing-segment",
            "audio-1",
            "result-1",
            SegmentStatus.completed,
        )
        is False
    )
    assert (
        project_store.update_segment_result(
            project.project_id,
            segment.segment_id,
            "audio-1",
            "result-1",
            SegmentStatus.completed,
        )
        is False
    )
    assert saves == []


def test_update_segment_result_saves_one_real_change(monkeypatch):
    segment = ScriptSegment(
        segment_id="segment-1",
        index=0,
        text="测试文本",
        status=SegmentStatus.ready,
    )
    project = Project(
        project_id="project-1",
        name="脚本项目",
        segments=[segment],
    )
    saves: list[Project] = []
    monkeypatch.setattr(
        project_store,
        "get_project",
        lambda _project_id: project,
    )
    monkeypatch.setattr(
        project_store,
        "save_project",
        lambda saved: saves.append(saved) or saved,
    )

    assert (
        project_store.update_segment_result(
            project.project_id,
            segment.segment_id,
            "audio-1",
            "result-1",
            SegmentStatus.completed,
        )
        is True
    )
    assert len(saves) == 1
    assert segment.result_audio_id == "audio-1"
    assert segment.result_id == "result-1"
    assert segment.status == SegmentStatus.completed


def test_video_localization_tts_skips_legacy_script_segment_projection(
    monkeypatch,
):
    calls: list[tuple] = []
    monkeypatch.setattr(
        project_store,
        "update_segment_result",
        lambda *args: calls.append(args),
    )
    task = GenerationTask(
        task_id="task-1",
        generation_id="task-1",
        engine_id="omnivoice",
        project_id="project-1",
        segment_id="localized_0001",
        localized_subtitle_id="localized_0001",
        bind_to_video_localization=True,
        input_text="测试台词",
        parameters={"source": "video_localization"},
    )

    task_queue._update_project_segment(
        task,
        "audio-1",
        "result-1",
        SegmentStatus.completed,
    )

    assert calls == []
