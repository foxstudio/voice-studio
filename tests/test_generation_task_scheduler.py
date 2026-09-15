from __future__ import annotations

import asyncio
import sys
import threading
import wave
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import generation_task_scheduler


@pytest.mark.asyncio
async def test_descriptor_io_does_not_block_admission_or_event_loop():
    entered, release = threading.Event(), threading.Event()
    def describe(task_id):
        entered.set()
        assert release.wait(2)
        return generation_task_scheduler.descriptor(task_id, project_id="p", priority="normal")
    queue = generation_task_scheduler.ProjectFairGenerationQueue(describe)
    # Admission must not perform project/storage reads.
    queue.put_nowait("one")
    pending = asyncio.create_task(queue.get())
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        queue.put_nowait("two")
        await asyncio.sleep(0)
        assert not pending.done()
    finally:
        release.set()
    assert await pending == "one"


def test_unknown_priority_projection_error_fails_task_not_worker(monkeypatch):
    from app.schemas.voice_studio import GenerationTask, TaskStatus
    from app.services import task_queue
    task = GenerationTask(engine_id="emotivoice", input_text="test", status=TaskStatus.queued)
    monkeypatch.setattr(task_queue, "get_task", lambda _: task)
    def broken(_):
        raise RuntimeError("projection broke")
    monkeypatch.setattr(task_queue.video_localization_tts_handoff, "resolve_generation_priority", broken)
    failures = []
    monkeypatch.setattr(task_queue, "_fail_queue_admission", lambda task, error: failures.append(error.code))
    assert task_queue._queued_task_descriptor(task.task_id) is None
    assert failures == ["GENERATION_QUEUE_ADMISSION_FAILED"]


@pytest.mark.asyncio
async def test_unknown_projection_failure_does_not_stop_next_project(monkeypatch):
    from app.schemas.voice_studio import GenerationTask, TaskStatus
    from app.services import task_queue
    bad = GenerationTask(engine_id="emotivoice", input_text="bad", project_id="bad", status=TaskStatus.queued)
    good = GenerationTask(engine_id="emotivoice", input_text="good", project_id="good", status=TaskStatus.queued)
    tasks = {task.task_id: task for task in (bad, good)}
    monkeypatch.setattr(task_queue, "get_task", tasks.get)
    def resolve(task):
        if task.project_id == "bad":
            raise RuntimeError("project projection failed")
        return "normal"
    monkeypatch.setattr(task_queue.video_localization_tts_handoff, "resolve_generation_priority", resolve)
    monkeypatch.setattr(task_queue, "_fail_queue_admission", lambda task, _: setattr(task, "status", TaskStatus.failed))
    completed = asyncio.Event()
    async def process(task):
        assert task.task_id == good.task_id
        task.status = TaskStatus.success
        completed.set()
    monkeypatch.setattr(task_queue, "_process", process)
    queue = generation_task_scheduler.ProjectFairGenerationQueue(task_queue._queued_task_descriptor)
    for task_id in tasks:
        queue.put_nowait(task_id)
    worker = asyncio.create_task(task_queue._worker(queue))
    try:
        await asyncio.wait_for(completed.wait(), 2)
        assert bad.status == TaskStatus.failed
        assert good.status == TaskStatus.success
        assert not worker.done()
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker


def _queue(
    descriptions: dict[str, tuple[str, str]],
    *,
    clock=lambda: 0.0,
) -> generation_task_scheduler.ProjectFairGenerationQueue:
    def describe(task_id: str):
        project_id, priority = descriptions[task_id]
        return generation_task_scheduler.descriptor(
            task_id,
            project_id=project_id,
            priority=priority,
        )

    return generation_task_scheduler.ProjectFairGenerationQueue(
        describe,
        clock=clock,
    )


def test_foreground_resume_overtakes_queued_normal_work() -> None:
    async def run() -> list[str]:
        queue = _queue(
            {
                "new-1": ("new-project", "normal"),
                "new-2": ("new-project", "normal"),
                "existing": ("existing-project", "foreground_resume"),
            }
        )
        queue.put_nowait("new-1")
        queue.put_nowait("new-2")
        queue.put_nowait("existing")
        return [await queue.get(), await queue.get(), await queue.get()]

    assert asyncio.run(run()) == ["existing", "new-1", "new-2"]


def test_equal_priority_rotates_projects_without_reordering_one_project() -> None:
    async def run() -> list[str]:
        queue = _queue(
            {
                "a-1": ("project-a", "normal"),
                "a-2": ("project-a", "normal"),
                "b-1": ("project-b", "normal"),
            }
        )
        queue.put_nowait("a-1")
        queue.put_nowait("a-2")
        queue.put_nowait("b-1")
        return [await queue.get(), await queue.get(), await queue.get()]

    assert asyncio.run(run()) == ["a-1", "b-1", "a-2"]


def test_aging_eventually_promotes_background_work() -> None:
    now = [0.0]

    async def run() -> list[str]:
        queue = _queue(
            {
                "background": ("background-project", "background"),
                "normal": ("normal-project", "normal"),
            },
            clock=lambda: now[0],
        )
        queue.put_nowait("background")
        now[0] = 241.0
        queue.put_nowait("normal")
        return [await queue.get(), await queue.get()]

    assert asyncio.run(run()) == ["background", "normal"]


def test_three_projects_rotate_without_starving_the_third() -> None:
    async def run():
        descriptions = {f"{p}-{n}": (p, "normal") for p in "abc" for n in range(3)}
        queue = _queue(descriptions)
        for task_id in descriptions:
            queue.put_nowait(task_id)
        return [await queue.get() for _ in descriptions]
    assert asyncio.run(run()) == [f"{p}-{n}" for n in range(3) for p in "abc"]


def test_dispatch_reads_updated_priority_but_preserves_project_fifo() -> None:
    async def run():
        descriptions = {
            "a-first": ("a", "normal"), "a-second": ("a", "foreground_resume"),
            "b-first": ("b", "normal"),
        }
        queue = _queue(descriptions)
        for task_id in descriptions:
            queue.put_nowait(task_id)
        descriptions["b-first"] = ("b", "foreground_resume")
        return [await queue.get() for _ in descriptions]
    assert asyncio.run(run()) == ["b-first", "a-first", "a-second"]


def test_aged_project_gets_turn_among_three_foreground_projects() -> None:
    now = [0.0]
    async def run():
        descriptions = {"old": ("old", "background"), **{
            f"{p}-{n}": (p, "foreground_resume") for p in "abc" for n in range(2)
        }}
        queue = _queue(descriptions, clock=lambda: now[0])
        queue.put_nowait("old")
        now[0] = 241.0
        for task_id in descriptions:
            queue.put_nowait(task_id)
        return [await queue.get() for _ in descriptions]
    assert asyncio.run(run())[0] == "old"


def test_completed_project_bookkeeping_is_bounded() -> None:
    async def run():
        descriptions = {str(n): (str(n), "normal") for n in range(100)}
        queue = _queue(descriptions)
        for task_id in descriptions:
            queue.put_nowait(task_id)
            assert await queue.get() == task_id
            assert len(queue._last_served) <= 1
    asyncio.run(run())


@pytest.mark.asyncio
async def test_public_tts_queue_dispatches_fairly_and_never_runs_cancelled(tmp_path, monkeypatch):
    from app.schemas.voice_studio import AppSettings, GenerateRequest, TaskStatus
    from app.services import database, settings_store, task_queue

    original_db = database.DB_PATH
    await task_queue.shutdown()
    database.set_db_path(tmp_path / "config" / "voice_studio.db")
    settings_store.update(AppSettings(
        data_dir=str(tmp_path), voice_dir=str(tmp_path / "voices"),
        output_dir=str(tmp_path / "outputs"), export_dir=str(tmp_path / "exports"),
        project_dir=str(tmp_path / "projects"), cache_dir=str(tmp_path / "cache"),
        log_dir=str(tmp_path / "logs"),
    ))
    started, release = threading.Event(), threading.Event()
    calls = []

    def fixed_engine(_engine_id, kwargs, *_args):
        text = kwargs["text"]
        calls.append(text)
        if text == "holder":
            started.set()
            assert release.wait(10)
        path = Path(kwargs["output_path"])
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(b"\x00\x00" * 2400)
        return {"output_path": str(path), "duration_ms": 100, "generation_time_ms": 1}

    monkeypatch.setattr(task_queue.engine_registry, "ensure_loaded", lambda _id: None)
    monkeypatch.setattr(task_queue.engine_registry, "run_isolated", fixed_engine)
    monkeypatch.setattr(task_queue, "_recover_missing_auto_verifications", lambda: asyncio.sleep(0))
    async def submit(text, project, priority="normal"):
        return await task_queue.submit(GenerateRequest(
            text=text, engine_id="emotivoice", output_format="wav",
            project_id=project, resource_priority=priority,
        ))
    try:
        holder = await submit("holder", "holder")
        assert await asyncio.to_thread(started.wait, 3)
        ids = [holder]
        for project in "abc":
            for number in (1, 2):
                ids.append(await submit(f"{project}{number}", project))
        cancelled = await submit("cancelled", "a")
        task_queue.cancel_task(cancelled)
        ids.append(await submit("foreground", "existing", "foreground_resume"))
        release.set()
        for _ in range(500):
            if all(task_queue.get_task(task_id).status in {TaskStatus.success, TaskStatus.failed} for task_id in ids):
                break
            await asyncio.sleep(0.01)
        assert [task_queue.get_task(task_id).status for task_id in ids] == [TaskStatus.success] * len(ids)
        assert calls == ["holder", "foreground", "a1", "b1", "c1", "a2", "b2", "c2"]
        assert task_queue.get_task(cancelled).status == TaskStatus.cancelled
        assert cancelled not in task_queue._queued_task_ids
        task_queue._enqueue_task_id("missing-task")
        assert "missing-task" not in task_queue._queued_task_ids
        assert not task_queue._queued_task_ids
        assert all(task_queue.get_task(task_id).result_id for task_id in ids)
    finally:
        release.set()
        await task_queue.shutdown()
        database.set_db_path(original_db)


def test_priority_projection_cannot_resurrect_concurrent_cancellation(tmp_path, monkeypatch):
    from app.schemas.voice_studio import GenerationTask, TaskStatus
    from app.services import database, task_queue
    original_db = database.DB_PATH
    database.set_db_path(tmp_path / "config" / "voice_studio.db")
    task = GenerationTask(engine_id="emotivoice", project_id="priority-project",
                          input_text="fixed", status=TaskStatus.queued,
                          parameters={"text": "fixed", "engine_id": "emotivoice",
                                      "resource_priority": "normal",
                                      "video_localization_execution_scope": "all_remaining",
                                      "video_localization_dubbing_plan_revision": 1,
                                      "video_localization_dubbing_group_id": "group"})
    database.upsert("tasks", task.task_id, task.model_dump())
    real_conn = database.conn
    injected = []
    class Connection:
        def __init__(self, connection):
            self.connection = connection
        def execute(self, sql, parameters=()):
            if sql.startswith("UPDATE tasks SET data = json_set") and not injected:
                injected.append(True)
                task_queue.cancel_task(task.task_id)
            return self.connection.execute(sql, parameters)
    @contextmanager
    def racing_conn():
        with real_conn() as connection:
            yield Connection(connection)
    try:
        monkeypatch.setattr(database, "conn", racing_conn)
        updated = task_queue.update_pending_production_priorities(
            "priority-project", plan_revision=1, group_priorities={"group": "foreground_resume"},
        )
        assert injected == [True]
        assert updated == []
        assert task_queue.get_task(task.task_id).status == TaskStatus.cancelled
        assert task_queue.get_task(task.task_id).parameters["resource_priority"] == "normal"
    finally:
        task_queue._cancelled.discard(task.task_id)
        database.set_db_path(original_db)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage_before_enqueue", [True, False])
async def test_damaged_project_fails_only_its_task_and_preserves_terminal_outbox(tmp_path, monkeypatch, damage_before_enqueue):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.domains.video_localization import draft_store, service as project_service
    from app.domains.video_localization.schemas import VideoLocalizationDraft
    from app.domains.video_localization.dubbing_production_service import dubbing_production
    from app.schemas.voice_studio import AppSettings, GenerateRequest, GenerationTask, ProjectCreate, TaskStatus, VideoLocalizationTtsTask, VideoLocalizationTtsTaskStage
    from app.services import database, project_store, settings_store, task_queue, video_localization_tts_handoff as handoff, video_localization_tts_handoff_store as outbox

    original_db = database.DB_PATH
    await task_queue.shutdown()
    database.set_db_path(tmp_path / "config" / "voice_studio.db")
    settings_store.update(AppSettings(data_dir=str(tmp_path), voice_dir=str(tmp_path / "voices"),
        output_dir=str(tmp_path / "outputs"), export_dir=str(tmp_path / "exports"),
        project_dir=str(tmp_path / "projects"), cache_dir=str(tmp_path / "cache"), log_dir=str(tmp_path / "logs")))
    local_handoff = handoff.TtsHandoffApplicationService()
    local_handoff.configure(handoff.VideoLocalizationTtsProjection(
        finalize_submission=project_service.finalize_single_tts_submission,
        register_task=project_service.register_single_tts_task,
        sync_result=project_service.sync_single_tts_result,
        mark_workflow_terminal=project_service.mark_prepared_tts_workflow_terminal,
        resolve_generation_priority=dubbing_production.resolve_generation_priority,
    ))
    monkeypatch.setattr(handoff, "DEFAULT_TTS_HANDOFF_SERVICE", local_handoff)
    broken = project_store.create_project(ProjectCreate(name="Damaged project"))
    good = project_store.create_project(ProjectCreate(name="Good project"))
    request = GenerateRequest(text="must not run", engine_id="emotivoice", project_id=broken.project_id,
        segment_id="group", source="video_localization", bind_to_video_localization=True,
        video_localization_workflow_id="damaged-workflow", video_localization_execution_scope="all_remaining",
        video_localization_dubbing_plan_revision=1, video_localization_dubbing_group_id="group")
    bad_task = GenerationTask(engine_id="emotivoice", project_id=broken.project_id, segment_id="group",
        input_text=request.text, bind_to_video_localization=True, status=TaskStatus.queued, parameters=request.model_dump())
    workflow = VideoLocalizationTtsTask(workflow_id="damaged-workflow", project_id=broken.project_id,
        segment_id="group", generation_task_id=bad_task.task_id, subtitle_summary=request.text, text=request.text,
        start_ms=0, end_ms=1000, status="queued", stages=[
            VideoLocalizationTtsTaskStage(kind="generation", status="queued", parameters=request.model_dump()),
            VideoLocalizationTtsTaskStage(kind="placement", status="pending"),
        ])
    valid_draft = VideoLocalizationDraft(tts_tasks=[workflow]).model_dump(mode="json")
    def write_draft(value):
        project = project_store.get_project(broken.project_id)
        project_store.save_project(project.model_copy(update={"parameters": {**project.parameters, "video_localization": value}}))
    write_draft(valid_draft)
    database.upsert("tasks", bad_task.task_id, bad_task.model_dump())
    calls = []
    def fixed_engine(_engine_id, kwargs, *_args):
        calls.append(kwargs["text"])
        with wave.open(kwargs["output_path"], "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(b"\x00\x00" * 2400)
        return {"output_path": kwargs["output_path"], "duration_ms": 100}
    monkeypatch.setattr(task_queue.engine_registry, "ensure_loaded", lambda _id: None)
    monkeypatch.setattr(task_queue.engine_registry, "run_isolated", fixed_engine)
    monkeypatch.setattr(task_queue, "_recover_missing_auto_verifications", lambda: asyncio.sleep(0))
    try:
        if damage_before_enqueue:
            write_draft({"tts_tasks": "corrupt"})
        task_queue.start_worker()
        if not damage_before_enqueue:
            assert bad_task.task_id in task_queue._queued_task_ids
            write_draft({"tts_tasks": "corrupt"})
        good_id = await task_queue.submit(GenerateRequest(text="good", engine_id="emotivoice", project_id=good.project_id))
        for _ in range(300):
            if task_queue.get_task(good_id).status in {TaskStatus.success, TaskStatus.failed}:
                break
            await asyncio.sleep(0.01)
        assert task_queue.get_task(good_id).status == TaskStatus.success
        assert calls == ["good"]
        assert task_queue._worker_task is not None and not task_queue._worker_task.done()
        assert bad_task.task_id not in task_queue._queued_task_ids
        payload = TestClient(app).get(f"/api/tasks/{bad_task.task_id}").json()
        assert payload["status"] == "failed"
        assert "VIDEO_LOCALIZATION_DRAFT_REPAIR_REQUIRED" in payload["error_message"]
        assert payload["completed_at"] and payload["logs"]
        event_id = outbox.workflow_terminal_event_id(source_id=bad_task.task_id, workflow_id="damaged-workflow")
        assert outbox.get(event_id).status == "pending"
        write_draft(valid_draft)
        replay = handoff.replay_pending()
        assert replay.applied == 1
        assert outbox.get(event_id).status == "applied"
        assert draft_store.get(broken.project_id).tts_tasks[0].status == "failed"
        assert calls == ["good"]
    finally:
        await task_queue.shutdown()
        database.set_db_path(original_db)
