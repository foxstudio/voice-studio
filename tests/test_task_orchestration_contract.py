from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import AppSettings, BatchGenerateRequest, BatchSegmentInput, BatchSegmentResult, BatchTask, GenerateRequest, GenerationTask, LongformGenerateRequest, LongformSegmentTask, LongformTask, TaskStatus  # noqa: E402
from app.services import batch_queue, longform_queue, task_queue  # noqa: E402


class FakeDb:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def upsert(self, table: str, key: str, data: dict, *_) -> None:
        assert table in {"tasks", "longform_tasks", "batches"}
        self.rows[key] = dict(data)

    def get_one(self, table: str, key_field: str, key: str):
        assert table in {"tasks", "longform_tasks", "batches"}
        return self.rows.get(key)

    def list_all(self, table: str, *_args, **_kwargs):
        assert table in {"tasks", "longform_tasks", "batches"}
        return list(self.rows.values())

    def delete_one(self, table: str, key_field: str, key: str):
        assert table in {"tasks", "longform_tasks", "batches"}
        self.rows.pop(key, None)


def _task_row(task_id: str, status: TaskStatus, *, engine_id: str = "indextts-v2", error_message: str | None = None, progress: float = 0.0) -> dict:
    return {
        "task_id": task_id,
        "task_type": "single",
        "engine_id": engine_id,
        "voice_id": None,
        "project_id": None,
        "segment_id": None,
        "input_text": "测试文本",
        "status": status.value,
        "progress": progress,
        "parameters": {"text": "测试文本", "engine_id": engine_id},
        "created_at": "2026-06-08T00:00:00",
        "error_message": error_message,
        "started_at": None,
        "completed_at": None,
    }


@pytest.fixture
def fake_task_db(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(task_queue, "db", db)
    return db


@pytest.fixture
def fake_longform_db(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(longform_queue, "db", db)
    return db


@pytest.fixture
def fake_batch_db(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(batch_queue, "db", db)
    return db


def test_enqueue_task_id_is_idempotent_for_single_live_queue():
    queue = asyncio.Queue[str]()
    original_queue = task_queue._queue
    original_inflight = set(task_queue._queued_task_ids)
    task_queue._queue = queue
    task_queue._queued_task_ids = set()
    try:
        task_queue._enqueue_task_id("task-dup")
        task_queue._enqueue_task_id("task-dup")

        assert queue.qsize() == 1
        assert queue.get_nowait() == "task-dup"
        assert task_queue._queued_task_ids == {"task-dup"}
    finally:
        task_queue._queue = original_queue
        task_queue._queued_task_ids = original_inflight


@pytest.mark.parametrize("status", [TaskStatus.success, TaskStatus.failed, TaskStatus.cancelled])
def test_recover_incomplete_tasks_skips_terminal_task_rows(fake_task_db, status):
    fake_task_db.rows.clear()
    fake_task_db.upsert("tasks", "task-closed", _task_row("task-closed", status, progress=0.8, error_message="keep-me"))

    recovered = task_queue._recover_incomplete_tasks()

    persisted = fake_task_db.get_one("tasks", "task_id", "task-closed")
    assert recovered == []
    assert persisted is not None
    assert persisted["status"] == status.value
    assert persisted["error_message"] == "keep-me"


@pytest.mark.asyncio
async def test_longform_failure_is_reported_to_parent_status(fake_longform_db):
    task = LongformTask(
        longform_task_id="lf-1",
        engine_id="indextts-v2",
        status=TaskStatus.queued,
        input_text="第一段。第二段。",
        segments=[
            LongformSegmentTask(index=1, text="第一段。", char_count=4),
            LongformSegmentTask(index=2, text="第二段。", char_count=4),
        ],
        parameters=LongformGenerateRequest(
            generate_request=GenerateRequest(
                text="第一段。第二段。",
                engine_id="indextts-v2",
            ),
            verify_enabled=False,
            merge_enabled=False,
        ).model_dump(),
    )

    fake_longform_db.upsert("longform_tasks", task.longform_task_id, task.model_dump())
    async def fake_process_segment(_task: LongformTask, segment: LongformSegmentTask, _req: LongformGenerateRequest) -> bool:
        if segment.index == 2:
            segment.status = TaskStatus.failed
            segment.error_message = "段落失败"
            return False
        segment.result_id = f"result-{segment.index}"
        segment.status = TaskStatus.success
        return True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(longform_queue, "_process_segment", fake_process_segment)
    monkeypatch.setattr(longform_queue, "_notify_clients", lambda: None)
    try:
        await longform_queue._process(task)
    finally:
        monkeypatch.undo()

    assert task.status == TaskStatus.failed
    assert "段落生成或校对失败" in (task.error_message or "")
    assert task.segments[0].status == TaskStatus.success
    assert task.segments[1].status == TaskStatus.failed
    assert task.segments[1].error_message == "段落失败"


@pytest.mark.asyncio
async def test_longform_retry_failed_keeps_success_segment_result(fake_longform_db, monkeypatch):
    enqueued_ids = []

    monkeypatch.setattr(longform_queue, "start_worker", lambda: None)
    monkeypatch.setattr(longform_queue, "_enqueue_task_id", lambda task_id: enqueued_ids.append(task_id))
    task = LongformTask(
        longform_task_id="lf-retry-1",
        engine_id="indextts-v2",
        status=TaskStatus.failed,
        input_text="第一段。第二段。",
        segments=[
            LongformSegmentTask(index=1, text="第一段。", char_count=4, status=TaskStatus.success, result_id="result-1"),
            LongformSegmentTask(index=2, text="第二段。", char_count=4, status=TaskStatus.failed, error_message="生成失败"),
        ],
        parameters=LongformGenerateRequest(
            generate_request=GenerateRequest(
                text="第一段。第二段。",
                engine_id="indextts-v2",
            ),
            verify_enabled=False,
            merge_enabled=False,
        ).model_dump(),
    )
    fake_longform_db.upsert("longform_tasks", task.longform_task_id, task.model_dump())

    retried = await longform_queue.retry_failed(task.longform_task_id)

    assert retried.status == TaskStatus.queued
    assert retried.error_message is None
    assert retried.segments[0].status == TaskStatus.success
    assert retried.segments[0].result_id == "result-1"
    assert retried.segments[1].status == TaskStatus.queued
    assert retried.segments[1].error_message is None
    assert retried.segments[1].task_id is None
    assert enqueued_ids == ["lf-retry-1"]


@pytest.mark.asyncio
async def test_longform_cancel_does_not_overwrite_unfinished_segments(fake_longform_db):
    task = LongformTask(
        longform_task_id="lf-cancel-stale-race",
        engine_id="indextts-v2",
        status=TaskStatus.queued,
        input_text="第一段。第二段。",
        segments=[
            LongformSegmentTask(index=1, text="第一段。", char_count=4),
            LongformSegmentTask(index=2, text="第二段。", char_count=4),
        ],
        parameters=LongformGenerateRequest(
            generate_request=GenerateRequest(
                text="第一段。第二段。",
                engine_id="indextts-v2",
            ),
            verify_enabled=False,
            merge_enabled=False,
        ).model_dump(),
    )
    fake_longform_db.upsert("longform_tasks", task.longform_task_id, task.model_dump())

    segment_started = asyncio.Event()
    allow_continue = asyncio.Event()

    async def fake_process_segment(_task: LongformTask, segment: LongformSegmentTask, _req: LongformGenerateRequest) -> bool:
        if segment.index != 1:
            raise AssertionError("second segment should not be started after cancellation")
        segment.status = TaskStatus.success
        segment.result_id = "result-1"
        segment.error_message = None
        segment_started.set()
        await allow_continue.wait()
        return True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(longform_queue, "_process_segment", fake_process_segment)
    monkeypatch.setattr(longform_queue, "_notify_clients", lambda: None)
    process_task = asyncio.create_task(longform_queue._process(task))

    await segment_started.wait()
    canceled = longform_queue.cancel_longform(task.longform_task_id)
    assert canceled["status"] == "cancelled"
    allow_continue.set()

    try:
        await process_task
    finally:
        monkeypatch.undo()

    persisted = longform_queue.get_task("lf-cancel-stale-race")
    assert persisted is not None
    assert persisted.status == TaskStatus.cancelled
    assert persisted.segments[0].status == TaskStatus.success
    assert persisted.segments[0].result_id == "result-1"
    assert persisted.segments[1].status == TaskStatus.cancelled


@pytest.mark.asyncio
async def test_cancelled_task_status_not_overwritten_by_late_success(fake_task_db, tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(task_queue.engine_registry, "ensure_loaded", lambda *_: None)
    monkeypatch.setattr(task_queue.audio_tools, "copy_or_convert", lambda source, target, *_: source)
    monkeypatch.setattr(task_queue.history_store, "add", lambda item: item)
    monkeypatch.setattr(task_queue, "settings_store", task_queue.settings_store)
    monkeypatch.setattr(task_queue.settings_store, "output_dir", lambda: tmp_path)

    output_path = tmp_path / "cancel-late.wav"
    output_path.write_bytes(b"pcm")
    monkeypatch.setattr(task_queue.engine_registry, "run_isolated", lambda *_: {"output_path": str(output_path)})

    task = GenerationTask(
        task_id="task-late-success",
        task_type="single",
        engine_id="indextts-v2",
        input_text="演示文本",
        status=TaskStatus.cancelled,
        parameters=GenerateRequest(text="演示文本", engine_id="indextts-v2").model_dump(),
    )

    try:
        await task_queue._process(task)
    finally:
        monkeypatch.undo()

    assert task.status == TaskStatus.cancelled


@pytest.mark.asyncio
async def test_running_task_cancelled_during_runner_does_not_write_success_history(fake_task_db, tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    history_items = []
    monkeypatch.setattr(task_queue.engine_registry, "ensure_loaded", lambda *_: None)
    monkeypatch.setattr(task_queue.settings_store, "ensure_directories", lambda: None)
    monkeypatch.setattr(task_queue.settings_store, "output_dir", lambda: tmp_path)
    monkeypatch.setattr(task_queue, "_kwargs", lambda _req, _output_path: {})
    monkeypatch.setattr(task_queue.audio_tools, "copy_or_convert", lambda source, target, *_: source)
    monkeypatch.setattr(task_queue.history_store, "add", lambda item: history_items.append(item) or item)

    output_path = tmp_path / "late-success.wav"
    output_path.write_bytes(b"pcm")

    def fake_run_isolated(*_args):
        task_queue.cancel_task("task-cancel-during-runner")
        return {"output_path": str(output_path)}

    monkeypatch.setattr(task_queue.engine_registry, "run_isolated", fake_run_isolated)

    task = GenerationTask(
        task_id="task-cancel-during-runner",
        task_type="single",
        engine_id="indextts-v2",
        input_text="演示文本",
        status=TaskStatus.queued,
        parameters=GenerateRequest(text="演示文本", engine_id="indextts-v2").model_dump(),
    )

    try:
        await task_queue._process(task)
    finally:
        monkeypatch.undo()
        task_queue._cancelled.discard("task-cancel-during-runner")

    persisted = fake_task_db.get_one("tasks", "task_id", "task-cancel-during-runner")
    assert persisted is not None
    assert persisted["status"] == TaskStatus.cancelled.value
    assert task.status == TaskStatus.cancelled
    assert task.result_id is None
    assert history_items == []


@pytest.mark.asyncio
async def test_batch_partial_success_policy_is_honored(fake_batch_db):
    def fake_run_batch(_req: BatchGenerateRequest, _batch: BatchTask) -> dict[str, list[dict[str, str]]]:
        return {
            "results": [
                {"segment_id": "seg-1", "status": "success", "output_path": "a.mp3"},
                {"segment_id": "seg-2", "status": "failed", "error_message": "段落失败"},
            ]
        }

    req_payload = {
        "engine_id": "indextts-v2",
        "segments": [
            BatchSegmentInput(text="第一段", segment_id="seg-1").model_dump(),
            BatchSegmentInput(text="第二段", segment_id="seg-2").model_dump(),
        ],
        "partial_success": True,
    }
    batch = BatchTask(
        engine_id="indextts-v2",
        status=TaskStatus.queued,
        segments=[
            BatchSegmentResult(segment_id="seg-1", text="第一段", status=TaskStatus.pending),
            BatchSegmentResult(segment_id="seg-2", text="第二段", status=TaskStatus.pending),
        ],
        parameters=req_payload,
    )
    fake_batch_db.upsert("batches", batch.batch_task_id, batch.model_dump())
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(batch_queue, "run_batch", fake_run_batch)
    try:
        await batch_queue._process(batch)
    finally:
        monkeypatch.undo()

    assert batch.status == TaskStatus.success
    assert batch.error_message is None


@pytest.mark.asyncio
async def test_batch_failure_records_success_and_error_context(fake_batch_db):
    def fake_run_batch(_req: BatchGenerateRequest, _batch: BatchTask) -> dict[str, list[dict[str, str]]]:
        return {
            "results": [
                {
                    "segment_id": "seg-1",
                    "status": "success",
                    "output_path": "/tmp/seg-1.mp3",
                    "duration_ms": 120,
                },
                {"segment_id": "seg-2", "status": "failed", "error_message": "seg-2 failed"},
            ]
        }

    batch = BatchTask(
        engine_id="indextts-v2",
        status=TaskStatus.queued,
        segments=[
            BatchSegmentResult(segment_id="seg-1", text="第一段", status=TaskStatus.pending),
            BatchSegmentResult(segment_id="seg-2", text="第二段", status=TaskStatus.pending),
            BatchSegmentResult(segment_id="seg-3", text="第三段", status=TaskStatus.pending),
        ],
        parameters={
            "engine_id": "indextts-v2",
            "segments": [
                BatchSegmentInput(text="第一段", segment_id="seg-1").model_dump(),
                BatchSegmentInput(text="第二段", segment_id="seg-2").model_dump(),
                BatchSegmentInput(text="第三段", segment_id="seg-3").model_dump(),
            ],
        },
    )
    fake_batch_db.upsert("batches", batch.batch_task_id, batch.model_dump())
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(batch_queue, "run_batch", fake_run_batch)
    try:
        await batch_queue._process(batch)
    finally:
        monkeypatch.undo()

    assert batch.status == TaskStatus.failed
    assert batch.segments[0].status == TaskStatus.success
    assert batch.segments[0].output_path == "/tmp/seg-1.mp3"
    assert batch.segments[0].duration_ms == 120
    assert batch.segments[1].status == TaskStatus.failed
    assert batch.segments[1].error_message == "seg-2 failed"
    assert batch.segments[2].status == TaskStatus.failed
    assert batch.segments[2].error_message == "批处理段落生成失败"
    assert batch.error_message == "批处理段落生成失败: 成功 1 个，失败 2 个。"


@pytest.mark.asyncio
async def test_batch_runner_exception_preserves_prior_success_segment(fake_batch_db):
    def fake_run_batch(_req: BatchGenerateRequest, _batch: BatchTask):
        raise RuntimeError("runner crashed")

    batch = BatchTask(
        engine_id="indextts-v2",
        status=TaskStatus.queued,
        segments=[
            BatchSegmentResult(
                segment_id="seg-1",
                text="第一段",
                status=TaskStatus.success,
                output_path="/tmp/seg-1.mp3",
            ),
            BatchSegmentResult(segment_id="seg-2", text="第二段", status=TaskStatus.pending),
        ],
        parameters={
            "engine_id": "indextts-v2",
            "segments": [
                BatchSegmentInput(text="第一段", segment_id="seg-1").model_dump(),
                BatchSegmentInput(text="第二段", segment_id="seg-2").model_dump(),
            ],
        },
    )
    fake_batch_db.upsert("batches", batch.batch_task_id, batch.model_dump())
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(batch_queue, "run_batch", fake_run_batch)
    try:
        await batch_queue._process(batch)
    finally:
        monkeypatch.undo()

    assert batch.status == TaskStatus.failed
    assert batch.segments[0].status == TaskStatus.success
    assert batch.segments[0].output_path == "/tmp/seg-1.mp3"
    assert batch.segments[1].status == TaskStatus.failed
    assert batch.segments[1].error_message == "runner crashed"
    assert batch.error_message == "批处理段落处理异常: 成功 1 个，失败 1 个。runner crashed"


def test_batch_retry_should_not_recompute_success_segments_without_retry_entry(fake_batch_db):
    batch = BatchTask(
        engine_id="indextts-v2",
        status=TaskStatus.failed,
        segments=[
            BatchSegmentResult(
                segment_id="seg-1",
                text="第一段",
                status=TaskStatus.success,
                output_path="/tmp/seg-1.mp3",
            ),
            BatchSegmentResult(segment_id="seg-2", text="第二段", status=TaskStatus.failed, error_message="前次失败"),
        ],
        parameters={"engine_id": "indextts-v2"},
    )
    fake_batch_db.upsert("batches", batch.batch_task_id, batch.model_dump())
    retried = batch_queue.retry_batch(batch.batch_task_id)  # type: ignore[attr-defined]
    assert retried is not None
    assert retried.segments[0].status == TaskStatus.success
    assert retried.segments[0].output_path == "/tmp/seg-1.mp3"
    assert retried.segments[1].status != TaskStatus.success


def test_batch_cancel_should_not_overwrite_success_segments_without_cancel_entry(fake_batch_db):
    batch = BatchTask(
        engine_id="indextts-v2",
        status=TaskStatus.running,
        segments=[
            BatchSegmentResult(
                segment_id="seg-1",
                text="第一段",
                status=TaskStatus.success,
                output_path="/tmp/seg-1.mp3",
            ),
            BatchSegmentResult(segment_id="seg-2", text="第二段", status=TaskStatus.running),
        ],
        parameters={"engine_id": "indextts-v2"},
    )
    fake_batch_db.upsert("batches", batch.batch_task_id, batch.model_dump())
    result = batch_queue.cancel_batch(batch.batch_task_id)  # type: ignore[attr-defined]
    assert result["status"] == "cancelled"
    persisted = batch_queue.get_batch(batch.batch_task_id)
    assert persisted is not None
    assert persisted.segments[0].status == TaskStatus.success


def test_mimo_restart_recovery_requires_idempotency_marker(monkeypatch):
    settings = AppSettings(cloud_enabled=True, mimo_api_key_configured=True, mimo_default_voice="mimo_default")
    monkeypatch.setattr(task_queue.settings_store, "get", lambda: settings)
    monkeypatch.setattr(task_queue.settings_store, "mimo_api_key", lambda: "test-secret")
    req = GenerateRequest(
        text="演示文本",
        engine_id="mimo-v2.5-tts",
        voice_id="voice-a",
        style_instruction="沉稳",
    )
    args = task_queue._kwargs(req, "/tmp/mimo.wav")
    same_args = task_queue._kwargs(req, "/tmp/other-output.wav")

    assert args["idempotency_marker"].startswith("mimo:")
    assert same_args["idempotency_marker"] == args["idempotency_marker"]
    assert "test-secret" not in args["idempotency_marker"]


def test_mimo_running_task_without_idempotency_marker_is_not_auto_recovered(fake_task_db):
    row = _task_row("mimo-old-running", TaskStatus.running, engine_id="mimo-v2.5-tts-preset", progress=0.4)
    row["started_at"] = task_queue.now_iso()
    fake_task_db.upsert("tasks", "mimo-old-running", row)

    recovered = task_queue._recover_incomplete_tasks()

    persisted = fake_task_db.get_one("tasks", "task_id", "mimo-old-running")
    assert recovered == []
    assert persisted is not None
    assert persisted["status"] == TaskStatus.failed.value
    assert "缺少幂等标记" in persisted["error_message"]


def test_mimo_submit_persists_idempotency_marker(fake_task_db, monkeypatch):
    async def noop_broadcast(_task):
        return None

    monkeypatch.setattr(task_queue, "start_worker", lambda: None)
    monkeypatch.setattr(task_queue, "_enqueue_task_id", lambda _task_id: None)
    monkeypatch.setattr(task_queue, "_broadcast", noop_broadcast)

    req = GenerateRequest(text="演示文本", engine_id="mimo-v2.5-tts-preset", mimo_voice="voice-a")

    task_id = asyncio.run(task_queue.submit(req))

    persisted = fake_task_db.get_one("tasks", "task_id", task_id)
    assert persisted is not None
    assert persisted["parameters"]["idempotency_marker"].startswith("mimo:")
