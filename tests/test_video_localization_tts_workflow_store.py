from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import service  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    GenerationTask,
    TaskStatus,
    VideoLocalizationDraft,
    VideoLocalizationTtsTask,
    VideoLocalizationTtsTaskStage,
)
from app.services import (  # noqa: E402
    database,
    project_store,
    settings_store,
    task_queue,
    video_localization_operation_store,
    video_localization_tts_workflow_store,
)


def _client(tmp_path: Path) -> TestClient:
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    return TestClient(app)


def _workflow(project_id: str, index: int) -> VideoLocalizationTtsTask:
    return VideoLocalizationTtsTask(
        workflow_id=f"workflow-{index}",
        project_id=project_id,
        segment_id=f"localized-{index}",
        subtitle_summary=f"字幕 {index}",
        text=f"配音 {index}",
        source_cue_ids=[f"cue-{index}"],
        start_ms=index * 1_000,
        end_ms=(index + 1) * 1_000,
        status="running",
        generation_task_id=f"generation-{index}",
        stages=[
            VideoLocalizationTtsTaskStage(
                kind="generation",
                status="running",
                progress=0.5,
            ),
            VideoLocalizationTtsTaskStage(
                kind="placement",
                status="pending",
                progress=0.0,
            ),
        ],
    )


def _completed_workflow(project_id: str, index: int) -> VideoLocalizationTtsTask:
    workflow = _workflow(project_id, index)
    return workflow.model_copy(
        update={
            "status": "success",
            "stages": [
                workflow.stages[0].model_copy(
                    update={"status": "success", "progress": 1.0}
                ),
                workflow.stages[1].model_copy(
                    update={"status": "success", "progress": 1.0}
                ),
            ],
        }
    )


def test_tts_workflow_projection_is_transactional_and_idempotent(tmp_path: Path):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "配音任务投影"},
    ).json()["project_id"]
    tasks = [_workflow(project_id, 1), _workflow(project_id, 2)]

    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(tts_tasks=tasks),
    )
    first = video_localization_tts_workflow_store.read_project(project_id)
    with database.conn() as connection:
        second_revision = (
            video_localization_tts_workflow_store.sync_project_from_connection(
                connection,
                project_id,
                tasks,
                projected_at="2026-09-01T00:00:00",
            )
        )
    second = video_localization_tts_workflow_store.read_project(project_id)

    assert saved is not None
    assert first.authoritative is True
    assert [task.workflow_id for task in first.tasks] == [
        "workflow-1",
        "workflow-2",
    ]
    assert second_revision == first.revision
    assert second.revision == first.revision


def test_reserving_more_than_one_hundred_workflows_preserves_durable_recovery_history(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "配音任务超过一百条仍可恢复"},
    ).json()["project_id"]
    workflows = [
        _completed_workflow(project_id, index)
        for index in range(1, 101)
    ]
    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            cues=[
                {
                    "cue_id": "cue-current",
                    "start_ms": 101_000,
                    "end_ms": 102_000,
                    "en_subtitle_text": "Current source",
                }
            ],
            localized_subtitles=[
                {
                    "subtitle_id": "localized-current",
                    "start_ms": 101_000,
                    "end_ms": 102_000,
                    "text": "当前配音",
                    "source_cue_ids": ["cue-current"],
                }
            ],
            tts_tasks=workflows,
        ),
    )
    assert saved is not None

    reserved = service.reserve_single_tts_handoff(
        project_id,
        "localized-current",
        target_subtitle_ids=["localized-current"],
        source_cue_ids=["cue-current"],
        workflow_id="workflow-101",
    )

    assert reserved is not None
    persisted = video_localization_tts_workflow_store.read_project(project_id)
    assert persisted.authoritative is True
    assert len(persisted.tasks) == 101
    assert persisted.tasks[0].workflow_id == "workflow-1"
    assert persisted.tasks[-1].workflow_id == "workflow-101"


def test_tts_task_reader_uses_projection_and_one_bulk_generation_query(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "配音任务批量读取"},
    ).json()["project_id"]
    workflows = [_workflow(project_id, index) for index in range(1, 21)]
    assert service.save_video_localization(
        project_id,
        VideoLocalizationDraft(tts_tasks=workflows),
    ) is not None
    for index in range(1, 21):
        task_queue._save(
            GenerationTask(
                task_id=f"generation-{index}",
                engine_id="omnivoice",
                input_text=f"配音 {index}",
                status=TaskStatus.running,
                progress=0.7,
            )
        )

    monkeypatch.setattr(
        project_store,
        "get_video_localization_tts_tasks_projection",
        lambda _project_id: (_ for _ in ()).throw(
            AssertionError("authoritative TTS reader must not parse Project JSON")
        ),
    )
    statements: list[str] = []
    original_conn = database.conn

    @contextmanager
    def traced_conn():
        with original_conn() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(database, "conn", traced_conn)

    response = client.get(
        f"/api/projects/{project_id}/video-localization/tts/tasks"
    )

    assert response.status_code == 200
    assert len(response.json()) == 20
    task_selects = [
        statement
        for statement in statements
        if "SELECT data FROM tasks WHERE task_id IN" in statement
    ]
    assert len(task_selects) == 1


def test_tts_task_feed_returns_only_changed_workflow_runtime(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "配音任务增量读取"},
    ).json()["project_id"]
    workflows = [_workflow(project_id, index) for index in range(1, 21)]
    assert service.save_video_localization(
        project_id,
        VideoLocalizationDraft(tts_tasks=workflows),
    ) is not None

    initial = client.get(
        f"/api/projects/{project_id}/video-localization/tts/tasks/feed"
    )
    assert initial.status_code == 200
    initial_payload = initial.json()
    assert initial_payload["changed"] is True
    assert len(initial_payload["tasks"]) == 20

    unchanged = client.get(
        f"/api/projects/{project_id}/video-localization/tts/tasks/feed",
        params={"after_revision": initial_payload["revision"]},
    )
    assert unchanged.status_code == 200
    assert unchanged.json() == {
        "schema_version": "video-localization-tts-task-feed-v1",
        "revision": initial_payload["revision"],
        "changed": False,
        "workflow_ids": [],
        "tasks": [],
    }

    task_queue._save(
        GenerationTask(
            task_id="generation-7",
            engine_id="omnivoice",
            input_text="配音 7",
            status=TaskStatus.running,
            progress=0.85,
        )
    )
    changed = client.get(
        f"/api/projects/{project_id}/video-localization/tts/tasks/feed",
        params={"after_revision": initial_payload["revision"]},
    )
    assert changed.status_code == 200
    changed_payload = changed.json()
    assert changed_payload["changed"] is True
    assert changed_payload["revision"] != initial_payload["revision"]
    assert [task["workflow_id"] for task in changed_payload["tasks"]] == [
        "workflow-7"
    ]
    assert changed_payload["tasks"][0]["stages"][0]["progress"] == 0.85
    assert len(changed.content) < len(initial.content) / 4


def test_task_and_tts_runtime_projection_share_one_transaction(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "任务状态原子投影"},
    ).json()["project_id"]
    assert service.save_video_localization(
        project_id,
        VideoLocalizationDraft(tts_tasks=[_workflow(project_id, 1)]),
    ) is not None

    def fail_projection(*_args, **_kwargs):
        raise RuntimeError("projection failed")

    monkeypatch.setattr(
        video_localization_tts_workflow_store,
        "sync_generation_task_from_connection",
        fail_projection,
    )
    task = GenerationTask(
        task_id="generation-1",
        engine_id="omnivoice",
        input_text="配音 1",
        status=TaskStatus.running,
        progress=0.5,
    )

    try:
        task_queue._save(task)
    except RuntimeError as exc:
        assert str(exc) == "projection failed"
    else:
        raise AssertionError("projection failure must abort the task transaction")

    assert database.get_one("tasks", "task_id", task.task_id) is None


def test_legacy_startup_backfill_publishes_tts_workflow_projection(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "旧项目配音任务迁移"},
    ).json()["project_id"]
    workflow = _workflow(project_id, 1)
    assert service.save_video_localization(
        project_id,
        VideoLocalizationDraft(tts_tasks=[workflow]),
    ) is not None
    with database.conn() as connection:
        video_localization_tts_workflow_store.delete_project_from_connection(
            connection,
            project_id,
        )
    path_key = str(database.DB_PATH.resolve())
    video_localization_operation_store._backfilled_paths.discard(path_key)

    video_localization_operation_store.backfill_legacy_projects()

    projection = video_localization_tts_workflow_store.read_project(project_id)
    assert projection.authoritative is True
    assert [task.workflow_id for task in projection.tasks] == [workflow.workflow_id]
