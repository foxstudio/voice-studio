from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.models.schemas import ExportRecord, GenerateRequest, GenerationTask, HistoryItem, LongformSegmentTask, LongformTask, TaskStatus  # noqa: E402
from app.services import database as db  # noqa: E402
from app.services import longform_queue  # noqa: E402
from app.services import history_store, task_queue  # noqa: E402


@pytest.fixture
def isolated_db(tmp_path):
    original = db.DB_PATH
    db.set_db_path(tmp_path / "voice_studio.db")
    try:
        yield
    finally:
        db.set_db_path(original)


def test_longform_generate_endpoint_creates_parent_task(monkeypatch, isolated_db):
    monkeypatch.setattr(longform_queue, "_enqueue_task_id", lambda task_id: None)
    client = TestClient(app)
    response = client.post(
        "/api/longform/generate",
        json={
            "generate_request": {
                "text": "第一段内容。第二段内容。",
                "engine_id": "indextts-v2",
                "voice_id": "voice-a",
                "language": "zh",
                "output_format": "mp3",
            },
            "segments": [
                {"index": 1, "text": "第一段内容。", "char_count": 5, "segment_reason": "sentence_boundary"},
                {"index": 2, "text": "第二段内容。", "char_count": 5, "segment_reason": "sentence_boundary"},
            ],
            "verify_enabled": True,
            "merge_enabled": True,
            "max_retries": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["engine_id"] == "indextts-v2"
    assert len(body["segments"]) == 2
    assert body["segments"][0]["status"] == "pending"
    assert body["verify_enabled"] is True
    assert body["merge_enabled"] is True


def test_longform_list_endpoint_returns_tasks(monkeypatch, isolated_db):
    monkeypatch.setattr(longform_queue, "_enqueue_task_id", lambda task_id: None)
    client = TestClient(app)
    created = client.post(
        "/api/longform/generate",
        json={
            "generate_request": {
                "text": "只生成一个短段落。",
                "engine_id": "indextts-v2",
                "voice_id": "voice-a",
                "language": "zh",
                "output_format": "wav",
            },
            "segments": [{"index": 1, "text": "只生成一个短段落。", "char_count": 9, "segment_reason": "direct_text"}],
            "verify_enabled": False,
            "merge_enabled": False,
        },
    ).json()

    response = client.get("/api/longform")

    assert response.status_code == 200
    assert any(item["longform_task_id"] == created["longform_task_id"] for item in response.json())


@pytest.mark.asyncio
async def test_longform_segment_tasks_carry_parent_metadata(monkeypatch, isolated_db):
    captured: dict = {}

    async def fake_submit(req, task_type="single", project_id=None, segment_id=None, **kwargs):
        captured.update({"req": req, "task_type": task_type, **kwargs})
        return "segment-task-1"

    def fake_get_task(task_id):
        return GenerationTask(
            task_id=task_id,
            task_type="segment",
            engine_id="indextts-v2",
            voice_id="voice-a",
            input_text="第一段。",
            status=TaskStatus.success,
            result_id="result-a",
            result_duration_ms=1200,
            parameters={},
        )

    monkeypatch.setattr(task_queue, "submit", fake_submit)
    monkeypatch.setattr(task_queue, "get_task", fake_get_task)

    task = LongformTask(
        longform_task_id="longform-a",
        engine_id="indextts-v2",
        voice_id="voice-a",
        input_text="第一段。第二段。",
        segments=[
            LongformSegmentTask(index=1, text="第一段。", char_count=4),
            LongformSegmentTask(index=2, text="第二段。", char_count=4),
        ],
        parameters={
            "generate_request": GenerateRequest(
                text="第一段。第二段。",
                engine_id="indextts-v2",
                voice_id="voice-a",
            ).model_dump(),
            "segments": [],
            "verify_enabled": False,
            "merge_enabled": True,
        },
    )
    req = longform_queue.LongformGenerateRequest(**task.parameters)

    ok = await longform_queue._process_segment(task, task.segments[0], req)

    assert ok is True
    assert captured["task_type"] == "segment"
    assert captured["longform_task_id"] == "longform-a"
    assert captured["longform_segment_index"] == 1
    assert captured["longform_segment_count"] == 2


def test_completed_longform_export_creates_history_result(isolated_db, tmp_path):
    merged = tmp_path / "merged.mp3"
    merged.write_bytes(b"fake mp3")
    export = ExportRecord(export_id="export-a", path=str(merged), format="mp3", source_count=2)
    task = LongformTask(
        longform_task_id="longform-a",
        engine_id="indextts-v2",
        voice_id="voice-a",
        input_text="第一段。第二段。",
        status=TaskStatus.success,
        segments=[
            LongformSegmentTask(index=1, text="第一段。", char_count=4, status=TaskStatus.success, result_id="result-a"),
            LongformSegmentTask(index=2, text="第二段。", char_count=4, status=TaskStatus.success, result_id="result-b"),
        ],
        result_ids=["result-a", "result-b"],
        parameters={"generate_request": {"output_format": "mp3"}},
    )

    created = task_queue.add_completed_longform_export(task, export, duration_ms=2500, generation_time_ms=900)
    history = history_store.get(created.result_id or "")

    assert created.task_type == "export"
    assert created.status == TaskStatus.success
    assert created.longform_task_id == "longform-a"
    assert created.longform_segment_count == 2
    assert created.longform_export_id == "export-a"
    assert created.result_duration_ms == 2500
    assert history is not None
    assert history.output_path == str(merged)
    assert history.longform_task_id == "longform-a"
    assert history.longform_export_id == "export-a"


def test_tts_verification_endpoint_persists_report(isolated_db):
    client = TestClient(app)
    task = GenerationTask(
        task_id="task-verify",
        task_type="single",
        engine_id="indextts-v2",
        voice_id="voice-a",
        input_text="第一句。第二句。",
        status=TaskStatus.success,
        result_id="result-verify",
        parameters=GenerateRequest(text="第一句。第二句。", engine_id="indextts-v2", voice_id="voice-a").model_dump(),
    )
    db.upsert("tasks", task.task_id, task.model_dump())
    history_store.add(
        HistoryItem(
            result_id="result-verify",
            task_id=task.task_id,
            engine_id="indextts-v2",
            voice_id="voice-a",
            input_text="第一句。第二句。",
            output_audio_id="audio-a",
            output_path="/tmp/missing.wav",
            parameter_snapshot=task.parameters,
        )
    )

    response = client.post(
        "/api/evaluations/tts-verification",
        json={
            "result_id": "result-verify",
            "expected_text": "第一句。第二句。",
            "transcript_text": "第一句。第二句。",
            "asr_engine_id": "qwen3-asr-mlx",
            "language": "zh",
        },
    )
    saved_task = task_queue.get_task("task-verify")
    saved_history = history_store.get("result-verify")

    assert response.status_code == 200
    assert response.json()["status"] == "passed"
    assert saved_task is not None
    assert saved_task.verification is not None
    assert saved_task.verification.status == "passed"
    assert saved_history is not None
    assert saved_history.verification is not None
    assert saved_history.verification.status == "passed"


def test_list_longform_tasks_backfills_existing_export_result(isolated_db, tmp_path):
    merged = tmp_path / "legacy.wav"
    merged.write_bytes(b"fake wav")
    task = LongformTask(
        longform_task_id="legacy-longform",
        engine_id="indextts-v2",
        voice_id="voice-a",
        input_text="第一段。第二段。",
        status=TaskStatus.success,
        segments=[
            LongformSegmentTask(
                index=1,
                text="第一段。",
                char_count=4,
                status=TaskStatus.success,
                task_id="legacy-segment-1",
                result_id="result-a",
                duration_ms=1000,
            ),
            LongformSegmentTask(
                index=2,
                text="第二段。",
                char_count=4,
                status=TaskStatus.success,
                task_id="legacy-segment-2",
                result_id="result-b",
                duration_ms=1500,
            ),
        ],
        result_ids=["result-a", "result-b"],
        export_id="legacy-export",
        export_path=str(merged),
        parameters={
            "generate_request": GenerateRequest(
                text="第一段。第二段。",
                engine_id="indextts-v2",
                voice_id="voice-a",
            ).model_dump(),
            "segments": [],
            "verify_enabled": False,
            "merge_enabled": True,
            "silence_ms": 300,
        },
    )
    db.upsert("longform_tasks", task.longform_task_id, task.model_dump())
    db.upsert(
        "tasks",
        "legacy-segment-1",
        GenerationTask(
            task_id="legacy-segment-1",
            task_type="segment",
            engine_id="indextts-v2",
            voice_id="voice-a",
            input_text="第一段。",
            status=TaskStatus.success,
            result_id="result-a",
            parameters={},
        ).model_dump(),
    )
    history_store.add(
        HistoryItem(
            result_id="result-b",
            task_id="legacy-segment-2",
            engine_id="indextts-v2",
            voice_id="voice-a",
            input_text="第二段。",
            output_audio_id="legacy-segment-2",
            output_path=str(merged),
            duration_ms=1500,
            parameter_snapshot=GenerateRequest(
                text="第二段。",
                engine_id="indextts-v2",
                voice_id="voice-a",
            ).model_dump(),
        )
    )

    items = longform_queue.list_tasks()
    export_task = task_queue.find_longform_export_task("legacy-longform", "legacy-export")
    segment_task = task_queue.get_task("legacy-segment-1")
    restored_segment = task_queue.get_task("legacy-segment-2")

    assert items[0].longform_task_id == "legacy-longform"
    assert export_task is not None
    assert export_task.result_id
    assert export_task.result_duration_ms == 2800
    assert segment_task is not None
    assert segment_task.longform_segment_index == 1
    assert segment_task.longform_segment_count == 2
    assert restored_segment is not None
    assert restored_segment.longform_segment_index == 2
    assert restored_segment.longform_segment_count == 2
