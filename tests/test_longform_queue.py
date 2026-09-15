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
from app.schemas.voice_studio import ExportRecord, GenerateRequest, GenerationTask, HistoryItem, LongformSegmentTask, LongformTask, TTSVerificationResponse, TaskStatus  # noqa: E402
from app.services import database as db  # noqa: E402
from app.services import longform_queue  # noqa: E402
from app.services import history_store, task_queue, video_localization_tts_handoff  # noqa: E402


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


@pytest.mark.asyncio
async def test_bound_longform_submit_removes_parent_when_projection_registration_fails(
    monkeypatch,
    isolated_db,
):
    enqueued: list[str] = []
    monkeypatch.setattr(longform_queue, "start_worker", lambda: None)
    monkeypatch.setattr(
        longform_queue,
        "_enqueue_task_id",
        enqueued.append,
    )
    monkeypatch.setattr(
        video_localization_tts_handoff,
        "finalize_submission",
        lambda request: request,
    )
    monkeypatch.setattr(
        video_localization_tts_handoff,
        "persist_and_register_longform_task",
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                RuntimeError("project projection unavailable")
            )
        ),
    )
    request = longform_queue.LongformGenerateRequest(
        generate_request=GenerateRequest(
            text="第一段。第二段。",
            engine_id="indextts-v2",
            source="video_localization",
            project_id="project-longform-submit",
            segment_id="localized-longform-submit",
            localized_subtitle_id="localized-longform-submit",
            cue_id="cue-longform-submit",
            bind_to_video_localization=True,
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="project projection unavailable",
    ):
        await longform_queue.submit(request)

    assert longform_queue.list_tasks() == []
    assert enqueued == []


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


def test_uncertain_cloud_longform_retry_requires_explicit_confirmation(
    monkeypatch,
    isolated_db,
):
    monkeypatch.setattr(longform_queue, "start_worker", lambda: None)
    monkeypatch.setattr(
        longform_queue,
        "_enqueue_task_id",
        lambda _task_id: None,
    )
    child = GenerationTask(
        task_id="cloud-child-uncertain",
        task_type="segment",
        engine_id="doubao-tts-preset",
        input_text="云端分段。",
        status=TaskStatus.failed,
        provider_state_uncertain=True,
        parameters={},
    )
    db.upsert("tasks", child.task_id, child.model_dump())
    parent = LongformTask(
        longform_task_id="longform-cloud-retry",
        engine_id="doubao-tts-preset",
        input_text="云端分段。",
        status=TaskStatus.failed,
        segments=[
            LongformSegmentTask(
                index=1,
                text="云端分段。",
                char_count=5,
                status=TaskStatus.failed,
                task_id=child.task_id,
                error_message=task_queue.CLOUD_RESULT_UNKNOWN_MESSAGE,
            )
        ],
        parameters={
            "generate_request": {
                "text": "云端分段。",
                "engine_id": "doubao-tts-preset",
            }
        },
    )
    db.upsert(
        "longform_tasks",
        parent.longform_task_id,
        parent.model_dump(),
    )

    client = TestClient(app)
    rejected = client.post(
        f"/api/longform/{parent.longform_task_id}/retry-failed"
    )
    confirmed = client.post(
        f"/api/longform/{parent.longform_task_id}/retry-failed",
        json={"confirm_cloud_replay": True},
    )

    assert rejected.status_code == 409
    assert (
        rejected.json()["error"]["code"]
        == "CLOUD_REPLAY_CONFIRM_REQUIRED"
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == TaskStatus.queued.value


def test_failed_longform_with_long_input_can_be_dismissed(isolated_db):
    long_text = "包括为啥各种灾情都集中在这个时间段？这是一段正文，不是文件路径。" * 80
    task = LongformTask(
        longform_task_id="longform-long-input",
        engine_id="indextts-v2",
        voice_id="voice-a",
        input_text=long_text,
        status=TaskStatus.failed,
        progress=1.0,
        segments=[
            LongformSegmentTask(
                index=1,
                text=long_text,
                char_count=len(long_text),
                status=TaskStatus.failed,
                error_message="校对失败：检测到缺句或漏段",
            )
        ],
        error_message="1 个段落生成或校对失败",
        parameters={"generate_request": {"text": long_text}},
    )
    db.upsert("longform_tasks", task.longform_task_id, task.model_dump())

    with TestClient(app) as client:
        response = client.delete(f"/api/longform/{task.longform_task_id}")

    assert response.status_code == 200
    assert response.json() == {"longform_task_id": task.longform_task_id, "status": "dismissed"}
    assert db.get_one("longform_tasks", "longform_task_id", task.longform_task_id) is None


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
                source="video_localization",
                project_id="project-a",
                segment_id="group-a",
                localized_subtitle_id="localized-a",
                timeline_clip_id="clip-a",
                bind_to_video_localization=True,
                video_localization_workflow_id="workflow-a",
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
    assert captured["req"].bind_to_video_localization is False
    assert captured["req"].project_id is None
    assert captured["req"].segment_id is None
    assert captured["req"].video_localization_workflow_id is None


@pytest.mark.asyncio
async def test_video_localization_longform_verification_is_advisory(
    monkeypatch,
    isolated_db,
):
    async def fake_submit(*_args, **_kwargs):
        return "segment-low-coverage"

    async def fake_wait(*_args, **_kwargs):
        return GenerationTask(
            task_id="segment-low-coverage",
            task_type="segment",
            engine_id="indextts-v2",
            input_text="较长的本土化台词。",
            status=TaskStatus.success,
            result_id="result-low-coverage",
            result_duration_ms=1200,
            parameters={},
        )

    async def fake_verify(*_args, **_kwargs):
        return TTSVerificationResponse(
            status="failed",
            coverage=0.0,
            similarity=0.0,
            expected_text="较长的本土化台词。",
            transcript_text="",
            normalized_expected="较长的本土化台词",
            normalized_transcript="",
        )

    monkeypatch.setattr(task_queue, "submit", fake_submit)
    monkeypatch.setattr(longform_queue, "_wait_for_generation", fake_wait)
    monkeypatch.setattr(longform_queue, "_verify_segment", fake_verify)
    monkeypatch.setattr(task_queue, "attach_verification", lambda *_args, **_kwargs: None)

    request = GenerateRequest(
        text="较长的本土化台词。",
        engine_id="indextts-v2",
        source="video_localization",
        project_id="project-a",
        segment_id="group-a",
        localized_subtitle_id="localized-a",
        bind_to_video_localization=True,
        video_localization_workflow_id="workflow-a",
    )
    task = LongformTask(
        longform_task_id="longform-low-coverage",
        engine_id=request.engine_id,
        input_text=request.text,
        verify_enabled=True,
        max_retries=0,
        segments=[
            LongformSegmentTask(index=1, text=request.text, char_count=len(request.text))
        ],
        parameters=longform_queue.LongformGenerateRequest(
            generate_request=request,
            verify_enabled=True,
            max_retries=0,
        ).model_dump(),
    )

    ok = await longform_queue._process_segment(
        task,
        task.segments[0],
        longform_queue.LongformGenerateRequest(**task.parameters),
    )

    assert ok is True
    assert task.segments[0].status == TaskStatus.success
    assert task.segments[0].verification is not None
    assert task.segments[0].verification.status == "failed"
    assert task.segments[0].error_message is None


@pytest.mark.asyncio
async def test_cloud_longform_timeout_does_not_submit_paid_segment_again(
    monkeypatch,
    isolated_db,
):
    submitted: list[str] = []

    async def fake_submit(*_args, **_kwargs):
        task_id = f"cloud-timeout-{len(submitted) + 1}"
        submitted.append(task_id)
        return task_id

    async def fake_wait(*_args, **_kwargs):
        raise TimeoutError("provider wait timed out")

    monkeypatch.setattr(task_queue, "submit", fake_submit)
    monkeypatch.setattr(longform_queue, "_wait_for_generation", fake_wait)

    task = LongformTask(
        longform_task_id="longform-cloud-timeout",
        engine_id="doubao-tts-preset",
        input_text="云端长句。",
        max_retries=2,
        segments=[
            LongformSegmentTask(
                index=1,
                text="云端长句。",
                char_count=5,
            )
        ],
        parameters=longform_queue.LongformGenerateRequest(
            generate_request=GenerateRequest(
                text="云端长句。",
                engine_id="doubao-tts-preset",
            ),
            max_retries=2,
            verify_enabled=False,
        ).model_dump(),
    )

    ok = await longform_queue._process_segment(
        task,
        task.segments[0],
        longform_queue.LongformGenerateRequest(**task.parameters),
    )

    assert ok is False
    assert submitted == ["cloud-timeout-1"]
    assert task.segments[0].attempts == 1
    assert (
        task.segments[0].error_message
        == task_queue.CLOUD_RESULT_UNKNOWN_MESSAGE
    )


@pytest.mark.asyncio
async def test_uncertain_cloud_longform_failure_is_not_replayed(
    monkeypatch,
    isolated_db,
):
    submitted: list[str] = []

    async def fake_submit(*_args, **_kwargs):
        task_id = f"cloud-uncertain-{len(submitted) + 1}"
        submitted.append(task_id)
        return task_id

    async def fake_wait(task_id, **_kwargs):
        return GenerationTask(
            task_id=task_id,
            task_type="segment",
            engine_id="mimo-v2.5-tts-preset",
            input_text="云端长句。",
            status=TaskStatus.failed,
            provider_state_uncertain=True,
            error_message="connection closed",
            parameters={},
        )

    monkeypatch.setattr(task_queue, "submit", fake_submit)
    monkeypatch.setattr(longform_queue, "_wait_for_generation", fake_wait)

    task = LongformTask(
        longform_task_id="longform-cloud-uncertain",
        engine_id="mimo-v2.5-tts-preset",
        input_text="云端长句。",
        max_retries=2,
        segments=[
            LongformSegmentTask(
                index=1,
                text="云端长句。",
                char_count=5,
            )
        ],
        parameters=longform_queue.LongformGenerateRequest(
            generate_request=GenerateRequest(
                text="云端长句。",
                engine_id="mimo-v2.5-tts-preset",
            ),
            max_retries=2,
            verify_enabled=False,
        ).model_dump(),
    )

    ok = await longform_queue._process_segment(
        task,
        task.segments[0],
        longform_queue.LongformGenerateRequest(**task.parameters),
    )

    assert ok is False
    assert submitted == ["cloud-uncertain-1"]
    assert task.segments[0].attempts == 1
    assert (
        task.segments[0].error_message
        == task_queue.CLOUD_RESULT_UNKNOWN_MESSAGE
    )


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


def test_video_localization_longform_export_binds_and_syncs_only_the_merged_result(monkeypatch, isolated_db, tmp_path):
    merged = tmp_path / "localized-merged.wav"
    merged.write_bytes(b"fake wav")
    export = ExportRecord(export_id="export-localized", path=str(merged), format="wav", source_count=2)
    request = GenerateRequest(
        text="第一段。第二段。",
        engine_id="indextts-v2",
        source="video_localization",
        project_id="project-localized",
        segment_id="group-localized",
        localized_subtitle_id="group-localized",
        timeline_clip_id="clip-localized",
        bind_to_video_localization=True,
        video_localization_workflow_id="workflow-localized",
    )
    task = LongformTask(
        longform_task_id="longform-localized",
        engine_id="indextts-v2",
        input_text=request.text,
        status=TaskStatus.success,
        segments=[
            LongformSegmentTask(index=1, text="第一段。", char_count=4, status=TaskStatus.success, result_id="result-a"),
            LongformSegmentTask(index=2, text="第二段。", char_count=4, status=TaskStatus.success, result_id="result-b"),
        ],
        result_ids=["result-a", "result-b"],
        parameters=longform_queue.LongformGenerateRequest(generate_request=request).model_dump(),
    )
    registered: list[tuple] = []
    synced: list[tuple[str, str]] = []
    monkeypatch.setattr(
        video_localization_tts_handoff,
        "persist_and_register_generation_task",
        lambda task, workflow_id=None: registered.append(
            (
                task.project_id,
                task.segment_id,
                task.task_id,
                workflow_id,
            )
        ),
    )
    monkeypatch.setattr(
        video_localization_tts_handoff,
        "place_generated_result_with_retry",
        lambda generated, history: synced.append((generated.task_id, history.result_id)) or True,
    )
    monkeypatch.setattr(task_queue, "schedule_auto_verification", lambda _task_id: None)
    with db.conn() as connection:
        connection.execute(
            """
            INSERT INTO projects (
                project_id,
                data,
                updated_at,
                repository_revision
            )
            VALUES (?, ?, ?, 0)
            """,
            (
                "project-localized",
                "{}",
                "2026-08-02T00:00:00",
            ),
        )

    created = task_queue.add_completed_longform_export(task, export)
    history = history_store.get(created.result_id or "")

    assert created.bind_to_video_localization is True
    assert created.project_id == "project-localized"
    assert created.segment_id == "group-localized"
    assert created.generation_id == created.task_id
    assert history is not None
    assert history.generation_id == created.task_id
    assert history.bind_to_video_localization is True
    assert registered == [("project-localized", "group-localized", created.task_id, "workflow-localized")]
    assert synced == [(created.task_id, history.result_id)]


@pytest.mark.asyncio
async def test_longform_segment_uses_engine_runtime_timeout_without_counting_queue(monkeypatch, isolated_db):
    captured_timeouts: list[float] = []

    async def fake_submit(*_args, **_kwargs):
        return "segment-timeout-task"

    async def fake_wait(_task_id: str, runtime_timeout: float):
        captured_timeouts.append(runtime_timeout)
        return GenerationTask(
            task_id="segment-timeout-task",
            task_type="segment",
            engine_id="cosyvoice-zero-shot",
            input_text="长句。",
            status=TaskStatus.success,
            result_id="result-timeout",
            parameters={},
        )

    monkeypatch.setattr(task_queue, "submit", fake_submit)
    monkeypatch.setattr(longform_queue, "_wait_for_generation", fake_wait)
    monkeypatch.setattr(longform_queue.engine_policy, "timeout_seconds_for", lambda _engine_id: 900)
    task = LongformTask(
        longform_task_id="longform-timeout",
        engine_id="cosyvoice-zero-shot",
        input_text="长句。",
        verify_enabled=False,
        segments=[LongformSegmentTask(index=1, text="长句。", char_count=3)],
        parameters=longform_queue.LongformGenerateRequest(
            generate_request=GenerateRequest(text="长句。", engine_id="cosyvoice-zero-shot"),
            verify_enabled=False,
        ).model_dump(),
    )

    assert await longform_queue._process_segment(task, task.segments[0], longform_queue.LongformGenerateRequest(**task.parameters)) is True
    assert captured_timeouts == [960.0]


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


def _recovery_request():
    from app.schemas.video_localization_dubbing_recovery import DubbingRecoveryDecision
    return longform_queue.LongformGenerateRequest(generate_request=GenerateRequest(text='第一句。第二句。', engine_id='omnivoice',
        video_localization_recovery=DubbingRecoveryDecision(recovery_id='a12345678901',source_revision='a'*64,
            plan_revision=1,group_id='g',stage='semantic_phrases',phrases=['第一句。','第二句。'],reason='完整句间断开')),
        verify_enabled=False, max_retries=2)


@pytest.mark.asyncio
@pytest.mark.parametrize('lost_parent_task_id', [False, True])
async def test_recovery_reuses_completed_child_after_interruption(monkeypatch, isolated_db, lost_parent_task_id):
    req = _recovery_request()
    task = LongformTask(engine_id='omnivoice',input_text=req.generate_request.text,parameters=req.model_dump(),verify_enabled=False,
        segments=[LongformSegmentTask(index=0,text='第一句。',char_count=4,status=TaskStatus.running,attempts=1,
            task_id=None if lost_parent_task_id else 'saved-child')])
    child = GenerationTask(task_id='saved-child',engine_id='omnivoice',input_text='第一句。',status=TaskStatus.success,result_id='saved-result',result_duration_ms=900)
    monkeypatch.setattr(task_queue,'get_task',lambda _:child)
    monkeypatch.setattr(task_queue,'find_longform_segment_task',lambda *args:child)
    async def forbid(*a,**k): raise AssertionError('completed child must not regenerate')
    monkeypatch.setattr(task_queue,'submit',forbid)
    assert await longform_queue._process_segment(task,task.segments[0],req)
    assert task.segments[0].result_id == 'saved-result'
    assert task.segments[0].attempts == 1


@pytest.mark.asyncio
async def test_recovery_attempt_budget_survives_parent_resume(monkeypatch, isolated_db):
    req = _recovery_request()
    task = LongformTask(engine_id='omnivoice',input_text=req.generate_request.text,parameters=req.model_dump(),verify_enabled=False,
        segments=[LongformSegmentTask(index=0,text='第一句。',char_count=4,status=TaskStatus.failed,attempts=3,task_id='failed-child')])
    child = GenerationTask(task_id='failed-child',engine_id='omnivoice',input_text='第一句。',status=TaskStatus.failed)
    monkeypatch.setattr(task_queue,'get_task',lambda _:child)
    async def forbid(*a,**k): raise AssertionError('retry budget must not reset')
    monkeypatch.setattr(task_queue,'submit',forbid)
    assert not await longform_queue._process_segment(task,task.segments[0],req)
    assert task.segments[0].attempts == 3
