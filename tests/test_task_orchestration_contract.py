from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.errors import AppException  # noqa: E402
from app.schemas.voice_studio import AppSettings, BatchGenerateRequest, BatchSegmentInput, BatchSegmentResult, BatchTask, ExportRecord, GenerateRequest, GenerationTask, HistoryItem, LongformGenerateRequest, LongformSegmentTask, LongformTask, TTSVerificationResponse, TaskStatus  # noqa: E402
from app.services import batch_queue, database, engine_runner, longform_queue, task_queue, video_localization_tts_handoff  # noqa: E402
from app.services.paths import PROJECT_ROOT  # noqa: E402


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


def _tts_projection(
    **overrides,
) -> video_localization_tts_handoff.VideoLocalizationTtsProjection:
    callbacks = {
        "finalize_submission": lambda request: request,
        "register_task": lambda *_args, **_kwargs: None,
        "sync_result": lambda *_args, **_kwargs: None,
        "mark_workflow_terminal": lambda *_args, **_kwargs: None,
    }
    callbacks.update(overrides)
    return video_localization_tts_handoff.VideoLocalizationTtsProjection(
        **callbacks
    )


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


@pytest.fixture
def isolated_batch_db(tmp_path):
    original = database.DB_PATH
    database.set_db_path(tmp_path / "voice_studio.db")
    try:
        yield
    finally:
        database.set_db_path(original)


def test_enqueue_task_id_is_idempotent_for_single_live_queue(fake_task_db):
    # Production submission persists identity before enqueueing. A missing row
    # is not eligible work and must not be used to test duplicate admission.
    fake_task_db.upsert("tasks", "task-dup", _task_row("task-dup", TaskStatus.queued))
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


def test_missing_auto_verification_recovery_is_recent_bounded_and_skips_resolved_tasks(fake_task_db):
    fake_task_db.rows.clear()
    for index in range(4):
        row = _task_row(f"task-verify-{index}", TaskStatus.success)
        row.update(
            {
                "result_id": f"result-{index}",
                "completed_at": f"2026-07-19T00:0{index}:00",
            }
        )
        fake_task_db.upsert("tasks", row["task_id"], row)
    resolved = fake_task_db.rows["task-verify-3"]
    resolved["verification_error"] = "自动校对失败"
    segment = fake_task_db.rows["task-verify-2"]
    segment["task_type"] = "segment"

    recovered = task_queue._missing_auto_verification_task_ids(limit=2)

    assert recovered == ["task-verify-1", "task-verify-0"]


@pytest.mark.asyncio
async def test_video_localization_submit_removes_task_when_projection_registration_fails(
    fake_task_db,
    monkeypatch,
):
    async def noop_broadcast(_task):
        return None

    enqueued: list[str] = []
    monkeypatch.setattr(task_queue, "start_worker", lambda: None)
    monkeypatch.setattr(
        task_queue,
        "_enqueue_task_id",
        enqueued.append,
    )
    monkeypatch.setattr(task_queue, "_broadcast", noop_broadcast)
    monkeypatch.setattr(
        video_localization_tts_handoff,
        "persist_and_register_generation_task",
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                RuntimeError("project projection unavailable")
            )
        ),
    )
    request = GenerateRequest(
        text="待配音文本",
        engine_id="indextts-v2",
        source="video_localization",
        project_id="project-submit",
        segment_id="localized-submit",
        localized_subtitle_id="localized-submit",
        cue_id="cue-submit",
        bind_to_video_localization=True,
    )

    with pytest.raises(
        RuntimeError,
        match="project projection unavailable",
    ):
        await task_queue.submit(request)

    assert fake_task_db.rows == {}
    assert enqueued == []


@pytest.mark.asyncio
async def test_video_localization_tasks_never_enter_generic_auto_verification(
    fake_task_db,
    monkeypatch,
):
    task = GenerationTask(
        task_id="task-verification-failed",
        engine_id="omnivoice",
        project_id="project-a",
        segment_id="localized_0001",
        localized_subtitle_id="localized_0001",
        cue_id="cue_0001",
        bind_to_video_localization=True,
        input_text="所以我一次性要了几种不同的世界。",
        status=TaskStatus.success,
        result_id="result-verification-failed",
        parameters={"source": "video_localization", "language": "zh"},
    )
    fake_task_db.upsert("tasks", task.task_id, task.model_dump())
    verification_calls: list[str] = []
    placement_calls: list[str] = []
    monkeypatch.setattr(
        task_queue,
        "_verify_task_output",
        lambda candidate: verification_calls.append(candidate.task_id),
    )
    monkeypatch.setattr(
        video_localization_tts_handoff,
        "place_generated_result_with_retry",
        lambda *_: placement_calls.append("placed") or True,
    )

    await task_queue._auto_verify_task(task.task_id)

    persisted = GenerationTask(**fake_task_db.rows[task.task_id])
    assert verification_calls == []
    assert placement_calls == []
    assert persisted.verification is None
    assert persisted.error_message is None


def test_video_localization_group_result_sync_targets_the_group_segment(monkeypatch):
    calls: list[tuple[str, str]] = []
    service = (
        video_localization_tts_handoff.TtsHandoffApplicationService()
    )
    service.configure(
        _tts_projection(
            sync_result=lambda project_id, target_id, **_kwargs: (
                calls.append((project_id, target_id))
                or SimpleNamespace(
                    tts_tasks=[],
                    timeline_clips=[
                        {
                            "track_id": "dub",
                            "task_id": "task-group",
                            "audio_path": "/tmp/group.wav",
                        }
                    ],
                )
            ),
        )
    )
    segment_id = "group_localized_0001_localized_0002_2"
    task = GenerationTask(
        task_id="task-group",
        engine_id="omnivoice",
        project_id="project-group",
        segment_id=segment_id,
        localized_subtitle_id="localized_0001",
        cue_id="cue_0001",
        bind_to_video_localization=True,
        generation_id="task-group",
        input_text="组合台词",
        status=TaskStatus.success,
        parameters={"source": "video_localization", "generation_id": "task-group"},
    )
    history = HistoryItem(
        result_id="result-group",
        task_id="task-group",
        engine_id="omnivoice",
        project_id="project-group",
        segment_id=segment_id,
        localized_subtitle_id="localized_0001",
        cue_id="cue_0001",
        generation_id="task-group",
        bind_to_video_localization=True,
        input_text="组合台词",
        output_path="/tmp/group.wav",
    )

    assert service.place_generated_result(task, history) is True

    assert calls == [("project-group", segment_id)]


def test_video_localization_result_sync_reports_all_callback_failures(monkeypatch):
    service = (
        video_localization_tts_handoff.TtsHandoffApplicationService()
    )
    service.configure(
        _tts_projection(
            sync_result=lambda *_args, **_kwargs: (
                (_ for _ in ()).throw(
                    RuntimeError("draft unavailable")
                )
            )
        )
    )
    monkeypatch.setattr(
        service,
        "_project_exists",
        lambda _project_id: True,
    )
    monkeypatch.setattr(
        service,
        "_result_placement_event",
        lambda _task, _history: None,
    )
    monkeypatch.setattr(
        video_localization_tts_handoff.time,
        "sleep",
        lambda _seconds: None,
    )
    task = GenerationTask(
        task_id="task-sync",
        generation_id="task-sync",
        engine_id="indextts-v2",
        project_id="project-sync",
        segment_id="localized-sync",
        localized_subtitle_id="localized-sync",
        cue_id="cue-sync",
        bind_to_video_localization=True,
        input_text="测试",
        parameters={
            "source": "video_localization",
            "generation_id": "task-sync",
        },
    )
    history = HistoryItem(
        result_id="result-sync",
        task_id="task-sync",
        generation_id="task-sync",
        engine_id="indextts-v2",
        project_id="project-sync",
        segment_id="localized-sync",
        localized_subtitle_id="localized-sync",
        cue_id="cue-sync",
        bind_to_video_localization=True,
        input_text="测试",
        output_path="/tmp/result-sync.wav",
    )

    assert (
        service.place_generated_result_with_retry(task, history)
        is False
    )
    assert len(task.logs) == 3
    assert all("视频本土化 cue 回填失败" in item for item in task.logs)


def test_video_localization_result_sync_does_not_report_a_skipped_placement_as_success(monkeypatch):
    service = (
        video_localization_tts_handoff.TtsHandoffApplicationService()
    )
    service.configure(
        _tts_projection(
            sync_result=lambda *_args, **_kwargs: SimpleNamespace(
                tts_tasks=[],
                timeline_clips=[],
            )
        )
    )
    monkeypatch.setattr(
        service,
        "_project_exists",
        lambda _project_id: True,
    )
    monkeypatch.setattr(
        service,
        "_result_placement_event",
        lambda _task, _history: None,
    )
    monkeypatch.setattr(
        video_localization_tts_handoff.time,
        "sleep",
        lambda _seconds: None,
    )
    task = GenerationTask(
        task_id="task-skipped",
        generation_id="task-skipped",
        engine_id="indextts-v2",
        project_id="project-skipped",
        segment_id="localized-skipped",
        localized_subtitle_id="localized-skipped",
        cue_id="cue-skipped",
        bind_to_video_localization=True,
        input_text="测试",
        parameters={
            "source": "video_localization",
            "generation_id": "task-skipped",
        },
    )
    history = HistoryItem(
        result_id="result-skipped",
        task_id="task-skipped",
        generation_id="task-skipped",
        engine_id="indextts-v2",
        project_id="project-skipped",
        segment_id="localized-skipped",
        localized_subtitle_id="localized-skipped",
        cue_id="cue-skipped",
        bind_to_video_localization=True,
        input_text="测试",
        output_path="/tmp/result-skipped.wav",
    )

    assert (
        service.place_generated_result_with_retry(task, history)
        is False
    )
    assert len(task.logs) == 3
    assert all("未采用本次结果" in item for item in task.logs)


@pytest.mark.asyncio
async def test_longform_failure_is_reported_to_parent_status(fake_longform_db):
    seen_segments: list[int] = []
    task = LongformTask(
        longform_task_id="lf-1",
        engine_id="indextts-v2",
        status=TaskStatus.queued,
        input_text="第一段。第二段。第三段。",
        merge_enabled=False,
        segments=[
            LongformSegmentTask(index=1, text="第一段。", char_count=4),
            LongformSegmentTask(index=2, text="第二段。", char_count=4),
            LongformSegmentTask(index=3, text="第三段。", char_count=4),
        ],
        parameters=LongformGenerateRequest(
            generate_request=GenerateRequest(
                text="第一段。第二段。第三段。",
                engine_id="indextts-v2",
            ),
            verify_enabled=False,
            merge_enabled=False,
        ).model_dump(),
    )

    fake_longform_db.upsert("longform_tasks", task.longform_task_id, task.model_dump())
    async def fake_process_segment(_task: LongformTask, segment: LongformSegmentTask, _req: LongformGenerateRequest) -> bool:
        seen_segments.append(segment.index)
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
    assert seen_segments == [1, 2, 3]
    assert task.segments[0].status == TaskStatus.success
    assert task.segments[1].status == TaskStatus.failed
    assert task.segments[1].error_message == "段落失败"
    assert task.segments[2].status == TaskStatus.success
    assert task.progress == 1.0
    assert task.result_ids == ["result-1", "result-3"]


@pytest.mark.asyncio
async def test_longform_partial_failure_does_not_merge_when_verification_gate_requires_complete_output(fake_longform_db, monkeypatch):
    created_exports: list[list[str]] = []
    added_exports: list[str] = []
    task = LongformTask(
        longform_task_id="lf-partial-merge",
        engine_id="indextts-v2",
        status=TaskStatus.queued,
        input_text="第一段。第二段。第三段。",
        segments=[
            LongformSegmentTask(index=1, text="第一段。", char_count=4),
            LongformSegmentTask(index=2, text="第二段。", char_count=4),
            LongformSegmentTask(index=3, text="第三段。", char_count=4),
        ],
        parameters=LongformGenerateRequest(
            generate_request=GenerateRequest(
                text="第一段。第二段。第三段。",
                engine_id="indextts-v2",
                output_format="wav",
            ),
            verify_enabled=False,
            merge_enabled=True,
            stop_merge_on_verification_failed=True,
        ).model_dump(),
    )

    fake_longform_db.upsert("longform_tasks", task.longform_task_id, task.model_dump())

    async def fake_process_segment(_task: LongformTask, segment: LongformSegmentTask, _req: LongformGenerateRequest) -> bool:
        if segment.index == 2:
            segment.status = TaskStatus.failed
            segment.error_message = "段落失败"
            return False
        segment.status = TaskStatus.success
        segment.result_id = f"result-{segment.index}"
        segment.duration_ms = 1000
        return True

    def fake_create_export(req):
        created_exports.append(list(req.result_ids))
        return ExportRecord(export_id="export-partial", path="/tmp/partial.wav", format=req.format, source_count=len(req.result_ids))

    def fake_add_completed_longform_export(_task, record, **_kwargs):
        added_exports.append(record.export_id)

    monkeypatch.setattr(longform_queue, "_process_segment", fake_process_segment)
    monkeypatch.setattr(longform_queue.export_store, "create_export", fake_create_export)
    monkeypatch.setattr(longform_queue.task_queue, "add_completed_longform_export", fake_add_completed_longform_export)
    monkeypatch.setattr(longform_queue, "_notify_clients", lambda: None)

    await longform_queue._process(task)

    assert task.status == TaskStatus.failed
    assert task.progress == 1.0
    assert task.result_ids == ["result-1", "result-3"]
    assert task.export_id is None
    assert task.export_path is None
    assert created_exports == []
    assert added_exports == []
    assert "已完成 2/3 段" in (task.error_message or "")


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
async def test_recovered_task_clears_stale_notice_after_success(fake_task_db, tmp_path, monkeypatch):
    output_path = tmp_path / "recovered-success.wav"
    output_path.write_bytes(b"pcm")
    monkeypatch.setattr(task_queue.engine_registry, "ensure_loaded", lambda *_: None)
    monkeypatch.setattr(task_queue.settings_store, "ensure_directories", lambda: None)
    monkeypatch.setattr(task_queue.settings_store, "output_dir", lambda: tmp_path)
    monkeypatch.setattr(task_queue, "_kwargs", lambda _req, _output_path: {})
    monkeypatch.setattr(task_queue.engine_registry, "run_isolated", lambda *_: {"output_path": str(output_path)})
    monkeypatch.setattr(task_queue.history_store, "add", lambda item: item)
    monkeypatch.setattr(task_queue, "schedule_auto_verification", lambda _task_id: None)
    task = GenerationTask(
        task_id="recovered-success",
        engine_id="emotivoice",
        input_text="恢复后成功。",
        status=TaskStatus.queued,
        error_message="服务重启后已重新排队。",
        parameters=GenerateRequest(text="恢复后成功。", engine_id="emotivoice").model_dump(),
    )

    await task_queue._process(task)

    persisted = fake_task_db.get_one("tasks", "task_id", task.task_id)
    assert persisted is not None
    assert persisted["status"] == TaskStatus.success.value
    assert persisted["error_message"] is None


@pytest.mark.asyncio
async def test_video_localization_generation_places_immediately_without_scheduling_verification(
    fake_task_db,
    tmp_path,
    monkeypatch,
):
    output_path = tmp_path / "video-localization-success.wav"
    output_path.write_bytes(b"pcm")
    placements: list[str] = []
    scheduled: list[str] = []
    monkeypatch.setattr(task_queue.engine_registry, "ensure_loaded", lambda *_: None)
    monkeypatch.setattr(task_queue.settings_store, "ensure_directories", lambda: None)
    monkeypatch.setattr(task_queue.settings_store, "output_dir", lambda: tmp_path)
    monkeypatch.setattr(task_queue, "_kwargs", lambda _req, _output_path: {})
    monkeypatch.setattr(task_queue.engine_registry, "run_isolated", lambda *_: {"output_path": str(output_path)})
    monkeypatch.setattr(task_queue.history_store, "add", lambda item: item)
    monkeypatch.setattr(
        video_localization_tts_handoff,
        "persist_generated_history",
        lambda _task, history: history,
    )
    monkeypatch.setattr(
        video_localization_tts_handoff,
        "place_generated_result_with_retry",
        lambda task, _history: placements.append(task.task_id) or True,
    )
    monkeypatch.setattr(task_queue, "schedule_auto_verification", scheduled.append)
    request = GenerateRequest(
        text="生成后立即放轨。",
        engine_id="emotivoice",
        source="video_localization",
        project_id="project-immediate-placement",
        segment_id="localized_0001",
        localized_subtitle_id="localized_0001",
        cue_id="cue_0001",
        bind_to_video_localization=True,
    )
    task = GenerationTask(
        task_id="video-localization-immediate-placement",
        engine_id=request.engine_id,
        project_id=request.project_id,
        segment_id=request.segment_id,
        localized_subtitle_id=request.localized_subtitle_id,
        cue_id=request.cue_id,
        bind_to_video_localization=True,
        input_text=request.text,
        status=TaskStatus.queued,
        parameters=request.model_dump(),
    )

    await task_queue._process(task)

    assert placements == [task.task_id]
    assert scheduled == []
    persisted = fake_task_db.get_one("tasks", "task_id", task.task_id)
    assert persisted["status"] == TaskStatus.success.value


@pytest.mark.asyncio
async def test_recovered_task_keeps_real_terminal_failure(fake_task_db, tmp_path, monkeypatch):
    monkeypatch.setattr(task_queue.engine_registry, "ensure_loaded", lambda *_: None)
    monkeypatch.setattr(task_queue.settings_store, "ensure_directories", lambda: None)
    monkeypatch.setattr(task_queue.settings_store, "output_dir", lambda: tmp_path)
    monkeypatch.setattr(task_queue, "_kwargs", lambda _req, _output_path: {})
    monkeypatch.setattr(
        task_queue.engine_registry,
        "run_isolated",
        lambda *_: (_ for _ in ()).throw(RuntimeError("真实推理失败")),
    )
    task = GenerationTask(
        task_id="recovered-failure",
        engine_id="emotivoice",
        input_text="恢复后失败。",
        status=TaskStatus.queued,
        error_message="服务重启后已重新排队。",
        parameters=GenerateRequest(text="恢复后失败。", engine_id="emotivoice").model_dump(),
    )

    await task_queue._process(task)

    persisted = fake_task_db.get_one("tasks", "task_id", task.task_id)
    assert persisted is not None
    assert persisted["status"] == TaskStatus.failed.value
    assert "真实推理失败" in persisted["error_message"]


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


def test_batch_run_batch_preserves_stdout_results_when_runner_exits_nonzero(tmp_path, monkeypatch):
    class FakeCompletedProcess:
        returncode = 1
        stdout = (
            "loading model\n"
            + __import__("json").dumps(
                {
                    "results": [
                        {
                            "segment_id": "seg-1",
                            "status": "success",
                            "output_path": str(tmp_path / "seg-1.wav"),
                            "duration_ms": 1234,
                        },
                        {"segment_id": "seg-2", "status": "failed", "error_message": "段落失败"},
                    ]
                },
                ensure_ascii=False,
            )
        )
        stderr = "torchaudio warning on shutdown"

    req = BatchGenerateRequest(
        engine_id="indextts-v2",
        output_dir=str(tmp_path),
        segments=[
            BatchSegmentInput(text="第一段", segment_id="seg-1"),
            BatchSegmentInput(text="第二段", segment_id="seg-2"),
        ],
    )
    batch = BatchTask(
        engine_id="indextts-v2",
        status=TaskStatus.running,
        output_dir=str(tmp_path),
        segments=[
            BatchSegmentResult(segment_id="seg-1", text="第一段", status=TaskStatus.pending),
            BatchSegmentResult(segment_id="seg-2", text="第二段", status=TaskStatus.pending),
        ],
        parameters=req.model_dump(),
    )
    monkeypatch.setattr(batch_queue.engine_registry, "ensure_loaded", lambda _engine_id: None)
    monkeypatch.setattr(batch_queue, "_common_kwargs", lambda _req: {})
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return FakeCompletedProcess()

    monkeypatch.setenv("PYTHONPATH", ".")
    monkeypatch.setattr(batch_queue.subprocess, "run", fake_run)

    result = batch_queue.run_batch(req, batch)

    assert captured["env"]["PYTHONPATH"].split(os.pathsep)[:2] == [
        str(PROJECT_ROOT / "backend"),
        str(PROJECT_ROOT),
    ]
    assert result["results"][0]["status"] == "success"
    assert result["results"][0]["duration_ms"] == 1234
    assert result["results"][1]["status"] == "failed"
    assert result["_runner_error"] == "torchaudio warning on shutdown"


def test_single_runner_prepends_project_paths_when_parent_pythonpath_is_relative(
    monkeypatch,
):
    captured: dict = {}

    class FakeStdin:
        def write(self, _payload: str) -> None:
            return None

        def close(self) -> None:
            return None

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.returncode = 0

        def poll(self):
            return 0

        def communicate(self):
            return ('{"output_path": "/tmp/test.wav"}', "")

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setenv("PYTHONPATH", ".")
    monkeypatch.setenv("VOICE_STUDIO_LOCAL_PERSISTENT_WORKER", "0")
    monkeypatch.setattr(engine_runner.subprocess, "Popen", fake_popen)

    result = engine_runner.run_isolated(
        "omnivoice",
        {"text": "环境测试"},
    )

    assert result["output_path"] == "/tmp/test.wav"
    assert captured["env"]["PYTHONPATH"].split(os.pathsep)[:2] == [
        str(PROJECT_ROOT / "backend"),
        str(PROJECT_ROOT),
    ]


@pytest.mark.asyncio
async def test_batch_nonzero_runner_with_results_keeps_success_and_marks_batch_failed(fake_batch_db):
    def fake_run_batch(_req: BatchGenerateRequest, _batch: BatchTask) -> dict:
        return {
            "_runner_error": "runner shutdown warning",
            "results": [
                {
                    "segment_id": "seg-1",
                    "status": "success",
                    "output_path": "/tmp/seg-1.wav",
                    "duration_ms": 1234,
                },
                {"segment_id": "seg-2", "status": "failed", "error_message": "段落失败"},
            ],
        }

    batch = BatchTask(
        engine_id="indextts-v2",
        status=TaskStatus.queued,
        segments=[
            BatchSegmentResult(segment_id="seg-1", text="第一段", status=TaskStatus.pending),
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
    assert batch.segments[0].output_path == "/tmp/seg-1.wav"
    assert batch.segments[0].duration_ms == 1234
    assert batch.segments[1].status == TaskStatus.failed
    assert batch.segments[1].error_message == "段落失败"
    assert batch.error_message == "批处理段落处理异常: 成功 1 个，失败 1 个。runner shutdown warning"


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


def test_batch_runner_payload_excludes_already_successful_segments(tmp_path):
    request = BatchGenerateRequest(
        engine_id="doubao-tts-preset",
        output_format="wav",
        segments=[
            BatchSegmentInput(text="已完成", segment_id="seg-done"),
            BatchSegmentInput(text="需要重试", segment_id="seg-retry"),
        ],
    )
    batch = BatchTask(
        engine_id=request.engine_id,
        output_format=request.output_format,
        segments=[
            BatchSegmentResult(
                segment_id="seg-done",
                text="已完成",
                status=TaskStatus.success,
                output_path="/tmp/seg-done.wav",
            ),
            BatchSegmentResult(
                segment_id="seg-retry",
                text="需要重试",
                status=TaskStatus.queued,
            ),
        ],
        parameters=request.model_dump(),
    )

    segments = batch_queue._runner_segments(
        request,
        batch,
        tmp_path,
    )

    assert [segment["segment_id"] for segment in segments] == [
        "seg-retry"
    ]


def test_batch_recovery_requeues_local_work_but_stops_uncertain_cloud_work(
    fake_batch_db,
):
    local = BatchTask(
        batch_task_id="batch-local-running",
        engine_id="indextts-v2",
        status=TaskStatus.running,
        progress=0.4,
        started_at="2026-07-30T10:00:00",
        segments=[
            BatchSegmentResult(
                segment_id="local-done",
                text="已完成",
                status=TaskStatus.success,
                output_path="/tmp/local-done.wav",
            ),
            BatchSegmentResult(
                segment_id="local-active",
                text="处理中",
                status=TaskStatus.running,
            ),
        ],
        parameters={"engine_id": "indextts-v2", "segments": []},
    )
    cloud = BatchTask(
        batch_task_id="batch-cloud-running",
        engine_id="doubao-tts-preset",
        status=TaskStatus.running,
        progress=0.4,
        started_at="2026-07-30T10:00:00",
        segments=[
            BatchSegmentResult(
                segment_id="cloud-done",
                text="已完成",
                status=TaskStatus.success,
                output_path="/tmp/cloud-done.wav",
            ),
            BatchSegmentResult(
                segment_id="cloud-active",
                text="结果未知",
                status=TaskStatus.running,
            ),
        ],
        parameters={"engine_id": "doubao-tts-preset", "segments": []},
    )
    cloud_queued_uncertain = BatchTask(
        batch_task_id="batch-cloud-queued-uncertain",
        engine_id="mimo-v2.5-tts-preset",
        status=TaskStatus.queued,
        provider_state_uncertain=True,
        segments=[
            BatchSegmentResult(
                segment_id="cloud-queued",
                text="结果未知",
                status=TaskStatus.queued,
            )
        ],
        parameters={"engine_id": "mimo-v2.5-tts-preset", "segments": []},
    )
    for batch in (local, cloud, cloud_queued_uncertain):
        fake_batch_db.upsert(
            "batches",
            batch.batch_task_id,
            batch.model_dump(),
        )

    recovered = batch_queue._recover_incomplete_batches()

    assert recovered == ["batch-local-running"]
    recovered_local = batch_queue.get_batch(local.batch_task_id)
    assert recovered_local is not None
    assert recovered_local.status == TaskStatus.queued
    assert recovered_local.progress == 0.0
    assert recovered_local.started_at is None
    assert recovered_local.segments[0].status == TaskStatus.success
    assert recovered_local.segments[1].status == TaskStatus.queued
    assert recovered_local.error_message == "服务重启后已重新排队。"

    for batch_task_id in (
        cloud.batch_task_id,
        cloud_queued_uncertain.batch_task_id,
    ):
        stopped = batch_queue.get_batch(batch_task_id)
        assert stopped is not None
        assert stopped.status == TaskStatus.failed
        assert stopped.provider_state_uncertain is True
        assert (
            stopped.error_message
            == batch_queue.engine_policy.CLOUD_RESULT_UNKNOWN_MESSAGE
        )
        assert stopped.segments[-1].status == TaskStatus.failed
        assert (
            stopped.segments[-1].error_message
            == batch_queue.engine_policy.CLOUD_RESULT_UNKNOWN_MESSAGE
        )


def test_uncertain_cloud_batch_retry_requires_confirmation(fake_batch_db):
    batch = BatchTask(
        batch_task_id="batch-cloud-uncertain",
        engine_id="doubao-tts-preset",
        status=TaskStatus.failed,
        provider_state_uncertain=True,
        segments=[
            BatchSegmentResult(
                segment_id="cloud-done",
                text="已完成",
                status=TaskStatus.success,
                output_path="/tmp/cloud-done.wav",
            ),
            BatchSegmentResult(
                segment_id="cloud-unknown",
                text="结果未知",
                status=TaskStatus.failed,
                error_message=batch_queue.engine_policy.CLOUD_RESULT_UNKNOWN_MESSAGE,
            ),
        ],
        parameters={"engine_id": "doubao-tts-preset", "segments": []},
    )
    fake_batch_db.upsert(
        "batches",
        batch.batch_task_id,
        batch.model_dump(),
    )
    original_queue = batch_queue._queue
    original_queued_ids = set(batch_queue._queued_batch_ids)
    batch_queue._queue = asyncio.Queue()
    batch_queue._queued_batch_ids = set()
    try:
        with pytest.raises(AppException) as exc_info:
            batch_queue.retry_batch(batch.batch_task_id)
        assert exc_info.value.code == "CLOUD_REPLAY_CONFIRM_REQUIRED"
        unchanged = batch_queue.get_batch(batch.batch_task_id)
        assert unchanged is not None
        assert unchanged.status == TaskStatus.failed
        assert unchanged.provider_state_uncertain is True

        retried = batch_queue.retry_batch(
            batch.batch_task_id,
            confirm_cloud_replay=True,
        )
        assert retried.status == TaskStatus.queued
        assert retried.provider_state_uncertain is False
        assert retried.segments[0].status == TaskStatus.success
        assert retried.segments[1].status == TaskStatus.queued
        assert batch_queue._queue.get_nowait() == batch.batch_task_id
    finally:
        batch_queue._queue = original_queue
        batch_queue._queued_batch_ids = original_queued_ids


@pytest.mark.asyncio
async def test_cloud_batch_marks_provider_uncertain_before_call_and_clears_after_success(
    isolated_batch_db,
    monkeypatch,
):
    request = BatchGenerateRequest(
        engine_id="doubao-tts-preset",
        output_format="wav",
        segments=[
            BatchSegmentInput(text="第一段", segment_id="cloud-seg-1"),
        ],
    )
    batch = BatchTask(
        batch_task_id="batch-cloud-provider-state",
        engine_id=request.engine_id,
        output_format=request.output_format,
        status=TaskStatus.queued,
        segments=[
            BatchSegmentResult(
                segment_id="cloud-seg-1",
                text="第一段",
                status=TaskStatus.queued,
            )
        ],
        parameters=request.model_dump(),
    )
    database.upsert(
        "batches",
        batch.batch_task_id,
        batch.model_dump(),
    )
    run_plan = batch_queue._BatchRunPlan(
        payload_json=json.dumps(
            {
                "engine_id": request.engine_id,
                "common": {},
                "segments": [
                    {
                        "segment_id": "cloud-seg-1",
                        "text": "第一段",
                        "output_path": "/tmp/cloud-seg-1.wav",
                        "parameters": {},
                    }
                ],
            }
        ),
        env={},
    )
    prepared: list[str] = []
    monkeypatch.setattr(
        batch_queue,
        "_prepare_batch_run",
        lambda prepared_request, _batch: (
            prepared.append(prepared_request.engine_id)
            or run_plan
        ),
    )

    def fake_execute_batch_run(prepared_plan):
        assert json.loads(prepared_plan.payload_json)["segments"][0]["segment_id"] == "cloud-seg-1"
        persisted = batch_queue.get_batch(batch.batch_task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.running
        assert persisted.provider_state_uncertain is True
        return {
            "results": [
                {
                    "segment_id": "cloud-seg-1",
                    "status": "success",
                    "output_path": "/tmp/cloud-seg-1.wav",
                    "duration_ms": 123,
                }
            ]
        }

    monkeypatch.setattr(
        batch_queue,
        "_execute_batch_run",
        fake_execute_batch_run,
    )

    await batch_queue._process(batch)

    persisted = batch_queue.get_batch(batch.batch_task_id)
    assert prepared == [request.engine_id]
    assert persisted is not None
    assert persisted.status == TaskStatus.success
    assert persisted.provider_state_uncertain is False
    with database.conn() as connection:
        attempt = connection.execute(
            "SELECT status, provider_request_id FROM batch_segment_attempts"
        ).fetchone()
    assert attempt["status"] == "success"
    assert attempt["provider_request_id"]


@pytest.mark.asyncio
async def test_cloud_batch_keeps_provider_uncertain_when_provider_call_fails(
    isolated_batch_db,
    monkeypatch,
):
    request = BatchGenerateRequest(
        engine_id="mimo-v2.5-tts-preset",
        output_format="wav",
        segments=[
            BatchSegmentInput(text="第一段", segment_id="cloud-seg-1"),
        ],
    )
    batch = BatchTask(
        batch_task_id="batch-cloud-provider-failed",
        engine_id=request.engine_id,
        output_format=request.output_format,
        status=TaskStatus.queued,
        segments=[
            BatchSegmentResult(
                segment_id="cloud-seg-1",
                text="第一段",
                status=TaskStatus.queued,
            )
        ],
        parameters=request.model_dump(),
    )
    database.upsert(
        "batches",
        batch.batch_task_id,
        batch.model_dump(),
    )
    run_plan = batch_queue._BatchRunPlan(
        payload_json=json.dumps(
            {
                "engine_id": request.engine_id,
                "common": {},
                "segments": [
                    {
                        "segment_id": "cloud-seg-1",
                        "text": "第一段",
                        "output_path": "/tmp/cloud-seg-1.wav",
                        "parameters": {},
                    }
                ],
            }
        ),
        env={},
    )
    monkeypatch.setattr(
        batch_queue,
        "_prepare_batch_run",
        lambda *_args: run_plan,
    )

    def fail_provider(prepared_plan):
        assert json.loads(prepared_plan.payload_json)["segments"][0]["segment_id"] == "cloud-seg-1"
        raise RuntimeError("provider connection dropped")

    monkeypatch.setattr(
        batch_queue,
        "_execute_batch_run",
        fail_provider,
    )

    await batch_queue._process(batch)

    persisted = batch_queue.get_batch(batch.batch_task_id)
    assert persisted is not None
    assert persisted.status == TaskStatus.failed
    assert persisted.provider_state_uncertain is True
    assert persisted.segments[0].status == TaskStatus.failed
    assert persisted.segments[0].provider_state_uncertain is True
    with database.conn() as connection:
        attempt = connection.execute(
            "SELECT status FROM batch_segment_attempts"
        ).fetchone()
    assert attempt["status"] == "uncertain"


@pytest.mark.asyncio
async def test_cloud_batch_checkpoints_each_segment_with_unique_request_identity(
    isolated_batch_db,
    monkeypatch,
):
    request = BatchGenerateRequest(
        engine_id="doubao-tts-preset",
        output_format="wav",
        partial_success=True,
        segments=[
            BatchSegmentInput(text="第一段", segment_id="cloud-seg-1"),
            BatchSegmentInput(text="第二段", segment_id="cloud-seg-2"),
        ],
    )
    batch = BatchTask(
        batch_task_id="batch-cloud-checkpoints",
        engine_id=request.engine_id,
        output_format=request.output_format,
        status=TaskStatus.queued,
        segments=[
            BatchSegmentResult(segment_id="cloud-seg-1", text="第一段", status=TaskStatus.queued),
            BatchSegmentResult(segment_id="cloud-seg-2", text="第二段", status=TaskStatus.queued),
        ],
        parameters=request.model_dump(),
    )
    database.upsert("batches", batch.batch_task_id, batch.model_dump())
    plan = batch_queue._BatchRunPlan(
        payload_json=json.dumps(
            {
                "engine_id": request.engine_id,
                "common": {"speaker": "voice"},
                "segments": [
                    {"segment_id": "cloud-seg-1", "text": "第一段", "output_path": "/tmp/1.wav", "parameters": {}},
                    {"segment_id": "cloud-seg-2", "text": "第二段", "output_path": "/tmp/2.wav", "parameters": {}},
                ],
            }
        ),
        env={},
    )
    monkeypatch.setattr(batch_queue, "_prepare_batch_run", lambda *_args: plan)
    request_ids: list[str] = []

    def execute(single_plan):
        segment = json.loads(single_plan.payload_json)["segments"][0]
        request_id = segment["parameters"]["provider_request_id"]
        request_ids.append(request_id)
        return {
            "results": [
                {
                    "segment_id": segment["segment_id"],
                    "status": "success",
                    "output_path": segment["output_path"],
                    "duration_ms": 100,
                    "provider_request_id": request_id,
                    "provider_state_uncertain": False,
                }
            ]
        }

    monkeypatch.setattr(batch_queue, "_execute_batch_run", execute)

    await batch_queue._process(batch)

    assert len(request_ids) == 2
    assert len(set(request_ids)) == 2
    persisted = batch_queue.get_batch(batch.batch_task_id)
    assert persisted is not None
    assert persisted.status == TaskStatus.success
    assert all(segment.status == TaskStatus.success for segment in persisted.segments)
    with database.conn() as connection:
        attempts = connection.execute(
            """
            SELECT segment_id, attempt_number, status, provider_request_id
            FROM batch_segment_attempts
            ORDER BY segment_id
            """
        ).fetchall()
    assert [row["segment_id"] for row in attempts] == ["cloud-seg-1", "cloud-seg-2"]
    assert all(row["attempt_number"] == 1 for row in attempts)
    assert all(row["status"] == "success" for row in attempts)


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


@pytest.mark.parametrize(
    "engine_id",
    ["mimo-v2.5-tts-preset", "doubao-tts-preset"],
)
def test_running_cloud_task_is_not_auto_recovered(
    fake_task_db,
    engine_id,
):
    row = _task_row(
        "cloud-old-running",
        TaskStatus.running,
        engine_id=engine_id,
        progress=0.4,
    )
    row["started_at"] = task_queue.now_iso()
    fake_task_db.upsert("tasks", "cloud-old-running", row)

    recovered = task_queue._recover_incomplete_tasks()

    persisted = fake_task_db.get_one("tasks", "task_id", "cloud-old-running")
    assert recovered == []
    assert persisted is not None
    assert persisted["status"] == TaskStatus.failed.value
    assert persisted["provider_state_uncertain"] is True
    assert persisted["error_message"] == task_queue.CLOUD_RESULT_UNKNOWN_MESSAGE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_id",
    ["mimo-v2.5-tts-preset", "doubao-tts-preset"],
)
async def test_uncertain_cloud_task_retry_requires_confirmation(
    fake_task_db,
    monkeypatch,
    engine_id,
):
    task = GenerationTask(
        task_id="cloud-uncertain",
        engine_id=engine_id,
        input_text="测试文本",
        status=TaskStatus.failed,
        provider_state_uncertain=True,
        parameters=GenerateRequest(
            text="测试文本",
            engine_id=engine_id,
        ).model_dump(),
    )
    fake_task_db.upsert("tasks", task.task_id, task.model_dump())

    with pytest.raises(AppException) as exc_info:
        await task_queue.retry_task(task.task_id)
    assert exc_info.value.code == "CLOUD_REPLAY_CONFIRM_REQUIRED"

    async def fake_submit(*_args, **_kwargs):
        return "confirmed-cloud-retry"

    monkeypatch.setattr(task_queue, "submit", fake_submit)
    assert (
        await task_queue.retry_task(
            task.task_id,
            confirm_cloud_replay=True,
        )
        == "confirmed-cloud-retry"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_id", "expects_request_id"),
    [
        ("mimo-v2.5-tts-preset", False),
        ("doubao-tts-preset", True),
    ],
)
async def test_cloud_engine_marks_result_uncertain_before_provider_call(
    fake_task_db,
    monkeypatch,
    engine_id,
    expects_request_id,
):
    task = GenerationTask(
        task_id="cloud-start",
        engine_id=engine_id,
        input_text="测试文本",
        status=TaskStatus.running,
        parameters=GenerateRequest(
            text="测试文本",
            engine_id=engine_id,
        ).model_dump(),
    )
    fake_task_db.upsert("tasks", task.task_id, task.model_dump())
    captured: dict = {}

    monkeypatch.setattr(task_queue, "_kwargs", lambda *_args: {})

    def fake_run_isolated(_engine_id, kwargs, *_args, **_kwargs):
        persisted = task_queue.get_task(task.task_id)
        assert persisted is not None
        assert persisted.provider_state_uncertain is True
        captured.update(kwargs)
        raise RuntimeError("provider disconnected")

    monkeypatch.setattr(
        task_queue.engine_registry,
        "run_isolated",
        fake_run_isolated,
    )

    with pytest.raises(RuntimeError, match="provider disconnected"):
        await task_queue._execute_engine(
            task,
            GenerateRequest(text="测试文本", engine_id=engine_id),
            Path("/tmp/cloud-start.wav"),
        )

    if expects_request_id:
        assert task.provider_request_id
        assert captured["request_id"] == task.provider_request_id
    else:
        assert task.provider_request_id is None
        assert "request_id" not in captured


@pytest.mark.asyncio
async def test_local_engine_progress_persistence_failure_does_not_abort_generation(
    fake_task_db,
    monkeypatch,
    caplog,
):
    task = GenerationTask(
        task_id="progress-write-failure",
        engine_id="omnivoice",
        input_text="测试文本",
        status=TaskStatus.running,
        parameters=GenerateRequest(
            text="测试文本",
            engine_id="omnivoice",
        ).model_dump(),
    )
    fake_task_db.upsert("tasks", task.task_id, task.model_dump())
    monkeypatch.setattr(task_queue, "_kwargs", lambda *_args: {})
    monkeypatch.setattr(
        task_queue,
        "_update_status_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("database is locked")
        ),
    )

    def fake_run_isolated(_engine_id, _kwargs, _timeout, _cancel_check, on_tick):
        on_tick(1.0)
        return {"output_path": "/tmp/generated.wav"}

    monkeypatch.setattr(
        task_queue.engine_registry,
        "run_isolated",
        fake_run_isolated,
    )

    result, _progress = await task_queue._execute_engine(
        task,
        GenerateRequest(text="测试文本", engine_id="omnivoice"),
        Path("/tmp/generated.wav"),
    )

    assert result["output_path"] == "/tmp/generated.wav"
    assert "database is locked" in caplog.text


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
