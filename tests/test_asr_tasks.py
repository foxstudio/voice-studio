from __future__ import annotations

import queue
from pathlib import Path

import pytest

from app.models.exceptions import AppException
from app.models.schemas import TaskStatus
from app.services import asr_tasks


class FakeDb:
    def __init__(self):
        self.tables: dict[str, dict[str, dict]] = {
            "asr_tasks": {},
            "transcriptions": {},
        }

    def upsert(self, table: str, key: str, data: dict, *_args):
        self.tables.setdefault(table, {})[key] = dict(data)

    def get_one(self, table: str, key_field: str, key: str):
        return self.tables.get(table, {}).get(key)

    def delete_one(self, table: str, key_field: str, key: str):
        self.tables.get(table, {}).pop(key, None)

    def list_all(self, table: str, *_args, **kwargs):
        return list(self.tables.get(table, {}).values())


@pytest.fixture
def fake_asr_state(monkeypatch, tmp_path):
    fake_db = FakeDb()
    monkeypatch.setattr(asr_tasks, "db", fake_db)
    monkeypatch.setattr(asr_tasks, "_queue", queue.Queue())
    monkeypatch.setattr(asr_tasks, "start_worker", lambda: None)
    monkeypatch.setattr(
        asr_tasks.asr_service,
        "upload_path_for",
        lambda engine_id, record_id, suffix: tmp_path / "uploads" / engine_id / f"{record_id}{suffix}",
    )
    return fake_db, tmp_path


def test_cancel_queued_asr_task_completes_without_processing(fake_asr_state):
    fake_db, tmp_path = fake_asr_state
    upload_path = tmp_path / "source.wav"
    upload_path.write_bytes(b"audio")
    fake_db.upsert(
        "asr_tasks",
        "task-1",
        {
            "task_id": "task-1",
            "engine_id": "mimo-v2.5-asr",
            "filename": "source.wav",
            "language": "auto",
            "status": "queued",
            "upload_path": str(upload_path),
        },
    )

    result = asr_tasks.cancel_task("task-1")

    stored = fake_db.get_one("asr_tasks", "task_id", "task-1")
    assert result == {"task_id": "task-1", "status": "cancelled"}
    assert stored["status"] == TaskStatus.cancelled
    assert stored["completed_at"]
    assert stored["cancel_requested"] is True


def test_retry_asr_task_copies_available_source_audio(fake_asr_state):
    fake_db, tmp_path = fake_asr_state
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    fake_db.upsert(
        "asr_tasks",
        "task-1",
        {
            "task_id": "task-1",
            "engine_id": "mimo-v2.5-asr",
            "filename": "source.wav",
            "language": "zh",
            "status": "failed",
            "completed_at": "2026-06-08T00:00:00",
            "upload_path": str(source),
        },
    )

    retry = asr_tasks.retry_task("task-1")

    stored = fake_db.get_one("asr_tasks", "task_id", retry.task_id)
    copied = Path(stored["upload_path"])
    assert retry.status == TaskStatus.queued
    assert retry.filename == "source.wav"
    assert copied.exists()
    assert copied.read_bytes() == b"audio"


def test_retry_asr_task_reports_missing_source_audio(fake_asr_state):
    fake_db, tmp_path = fake_asr_state
    fake_db.upsert(
        "asr_tasks",
        "task-1",
        {
            "task_id": "task-1",
            "engine_id": "mimo-v2.5-asr",
            "filename": "missing.wav",
            "language": "auto",
            "status": "failed",
            "completed_at": "2026-06-08T00:00:00",
            "upload_path": str(tmp_path / "missing.wav"),
        },
    )

    with pytest.raises(AppException) as exc:
        asr_tasks.retry_task("task-1")

    assert exc.value.code == "ASR_SOURCE_AUDIO_MISSING"


def test_delete_asr_task_preserves_source_audio_used_by_transcription(fake_asr_state):
    fake_db, tmp_path = fake_asr_state
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    fake_db.upsert(
        "transcriptions",
        "record-1",
        {"transcription_id": "record-1", "source_audio_path": str(source)},
    )
    fake_db.upsert(
        "asr_tasks",
        "task-1",
        {
            "task_id": "task-1",
            "engine_id": "mimo-v2.5-asr",
            "filename": "source.wav",
            "language": "auto",
            "status": "success",
            "completed_at": "2026-06-08T00:00:00",
            "transcription_id": "record-1",
            "upload_path": str(source),
        },
    )

    result = asr_tasks.delete_task("task-1")

    assert result == {"task_id": "task-1", "status": "deleted"}
    assert source.exists()
    assert fake_db.get_one("asr_tasks", "task_id", "task-1") is None
