from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.schemas.voice_studio import GenerationTask, TaskStatus  # noqa: E402
from app.services import database as db  # noqa: E402


@pytest.fixture
def isolated_db(tmp_path):
    original = db.DB_PATH
    db.set_db_path(tmp_path / "voice_studio.db")
    try:
        yield
    finally:
        db.set_db_path(original)


def _save_task(index: int, *, status: TaskStatus, created_at: datetime, engine_id: str = "indextts-v2") -> None:
    task = GenerationTask(
        task_id=f"task-{index:02d}",
        engine_id=engine_id,
        voice_id="voice-a" if index % 2 else "voice-b",
        input_text=f"第 {index} 条台词",
        status=status,
        result_id=f"result-{index:02d}" if status == TaskStatus.success else None,
        result_duration_ms=index * 1000,
        created_at=created_at.isoformat(),
    )
    db.upsert("tasks", task.task_id, task.model_dump())


def test_task_page_returns_filtered_slice_and_summary(isolated_db):
    now = datetime.now(timezone.utc)
    statuses = [TaskStatus.success, TaskStatus.failed, TaskStatus.queued, TaskStatus.success]
    for index, status in enumerate(statuses, start=1):
        _save_task(index, status=status, created_at=now - timedelta(minutes=index))

    client = TestClient(app)
    response = client.get("/api/tasks/page", params={"limit": 2, "status": "success", "sort": "oldest"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["task_id"] for item in body["items"]] == ["task-04", "task-01"]
    assert body["summary"] == {"all": 4, "active": 1, "processing": 0, "waiting": 1, "success": 2, "failed": 1}
    assert body["download_sequences"] == {"task-04": 1, "task-01": 2}


def test_task_page_filters_by_date_engine_query_and_voice(isolated_db):
    now = datetime.now(timezone.utc)
    _save_task(1, status=TaskStatus.success, created_at=now - timedelta(hours=2), engine_id="omnivoice")
    _save_task(2, status=TaskStatus.success, created_at=now - timedelta(days=2), engine_id="indextts-v2")

    client = TestClient(app)
    response = client.get(
        "/api/tasks/page",
        params={
            "created_after": (now - timedelta(days=1)).isoformat(),
            "engine_ids": "omnivoice",
            "q": "狐狸",
            "voice_ids": "voice-a",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["task_id"] == "task-01"


def test_task_summary_endpoint(isolated_db):
    now = datetime.now(timezone.utc)
    _save_task(1, status=TaskStatus.cancelled, created_at=now)
    _save_task(2, status=TaskStatus.running, created_at=now)

    response = TestClient(app).get("/api/tasks/summary")

    assert response.status_code == 200
    assert response.json() == {"all": 2, "active": 1, "processing": 1, "waiting": 0, "success": 0, "failed": 1}
