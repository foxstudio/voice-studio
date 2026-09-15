from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.domains.video_localization import media_assets, operation_queue  # noqa: E402
from app.domains.video_localization import service as video_localization_service  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    Project,
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.services import database, project_store, settings_store  # noqa: E402


def _configure_test_storage(tmp_path: Path) -> None:
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


def _client(tmp_path: Path) -> TestClient:
    _configure_test_storage(tmp_path)
    return TestClient(app)


def _seed_projects(
    count: int,
    *,
    operation_on_oldest: VideoLocalizationOperation | None = None,
) -> list[Project]:
    started_at = datetime(2026, 1, 1)
    projects = []
    for index in range(count):
        timestamp = (started_at + timedelta(minutes=index)).isoformat(
            timespec="microseconds"
        )
        parameters = {}
        if index == 0 and operation_on_oldest is not None:
            parameters["video_localization"] = VideoLocalizationDraft(
                operations=[operation_on_oldest]
            ).model_dump(mode="json")
        project = Project(
            project_id=f"project_{index:03d}",
            name=f"项目 {index:03d}",
            parameters=parameters,
            created_at=timestamp,
            updated_at=timestamp,
        )
        projects.append(
            project_store.save_project(project, touch_updated_at=False)
        )
    return projects


def test_project_summaries_include_all_projects_beyond_default_database_page(
    tmp_path: Path,
):
    client = _client(tmp_path)
    projects = _seed_projects(150)

    response = client.get("/api/projects/summaries")

    assert response.status_code == 200
    assert len(response.json()) == 150
    assert {item["project_id"] for item in response.json()} == {
        project.project_id for project in projects
    }


def test_operation_recovery_finds_queued_operation_in_oldest_project(
    tmp_path: Path,
    monkeypatch,
):
    _configure_test_storage(tmp_path)
    operation = VideoLocalizationOperation(
        operation_id="operation_in_oldest_project",
        project_id="project_000",
        kind="source_audio",
        status="queued",
    )
    _seed_projects(150, operation_on_oldest=operation)
    enqueued: list[str] = []
    monkeypatch.setattr(operation_queue, "_enqueue", enqueued.append)

    operation_queue._recover_active_operations()

    assert enqueued == [
        ("project_000", operation.operation_id)
    ]


def test_project_summaries_are_lightweight_and_separated_by_kind(tmp_path: Path):
    client = _client(tmp_path)
    script = client.post("/api/projects", json={"name": "脚本项目"}).json()
    localization = client.post("/api/projects", json={"name": "本土化项目"}).json()
    saved = client.put(
        f"/api/projects/{localization['project_id']}/video-localization",
        json={"source_media": {"filename": "interview.mp4", "duration_ms": 1200}},
    )
    assert saved.status_code == 200

    script_response = client.get("/api/projects/summaries", params={"kind": "script"})
    localization_response = client.get(
        "/api/projects/summaries",
        params={"kind": "video_localization"},
    )

    assert script_response.status_code == 200
    assert [item["project_id"] for item in script_response.json()] == [script["project_id"]]
    assert [item["project_id"] for item in localization_response.json()] == [
        localization["project_id"]
    ]
    summary = localization_response.json()[0]
    assert summary["kind"] == "video_localization"
    assert summary["has_source_media"] is False
    assert summary["source_media_configured"] is True
    assert summary["source_media_status"] == "missing"
    assert summary["package_status"] == "available"
    assert "parameters" not in summary
    assert "roles" not in summary
    assert "segments" not in summary


def test_localization_summary_query_does_not_load_complete_project_models(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    localization = client.post("/api/projects", json={"name": "大时间线项目"}).json()
    saved = client.put(
        f"/api/projects/{localization['project_id']}/video-localization",
        json={
            "source_media": {"filename": "large.mp4", "duration_ms": 1200},
            "timeline_clips": [
                {
                    "clip_id": f"clip-{index}",
                    "track_id": "dub",
                    "start_ms": index * 100,
                    "end_ms": index * 100 + 80,
                }
                for index in range(2_000)
            ],
        },
    )
    assert saved.status_code == 200

    def fail_full_project_load():
        raise AssertionError("summary query loaded complete Project models")

    monkeypatch.setattr(project_store, "list_projects", fail_full_project_load)

    summaries = client.get(
        "/api/projects/summaries",
        params={"kind": "video_localization"},
    )

    assert summaries.status_code == 200
    assert summaries.json()[0]["project_id"] == localization["project_id"]


def test_localization_sync_summaries_keep_indexed_project_when_package_is_missing(
    tmp_path: Path,
):
    client = _client(tmp_path)
    localization = client.post("/api/projects", json={"name": "可移除项目"}).json()
    saved = client.put(
        f"/api/projects/{localization['project_id']}/video-localization",
        json={"source_media": {"filename": "interview.mp4", "duration_ms": 1200}},
    )
    assert saved.status_code == 200

    first = client.post("/api/projects/video-localization/sync-project-summaries")
    assert first.status_code == 200
    assert first.json() == [
        {
            "project_id": localization["project_id"],
            "name": "可移除项目",
            "description": "",
            "kind": "video_localization",
            "has_source_media": False,
            "source_media_configured": True,
            "source_media_status": "missing",
            "has_local_package": True,
            "package_status": "available",
            "created_at": localization["created_at"],
            "updated_at": project_store.get_project(localization["project_id"]).updated_at,
        }
    ]

    project = project_store.get_project(localization["project_id"])
    assert project is not None
    directory_name = project.parameters["video_localization_dir_name"]
    project_root = tmp_path / "projects" / directory_name
    temporarily_unavailable_root = tmp_path / "temporarily-unavailable-project"
    project_root.rename(temporarily_unavailable_root)
    project_updated_at = project.updated_at

    second = client.post("/api/projects/video-localization/sync-project-summaries")
    assert second.status_code == 200
    assert second.json() == [
        {
            "project_id": localization["project_id"],
            "name": "可移除项目",
            "description": "",
            "kind": "video_localization",
            "has_source_media": False,
            "source_media_configured": True,
            "source_media_status": "missing",
            "has_local_package": False,
            "package_status": "missing",
            "created_at": localization["created_at"],
            "updated_at": project_updated_at,
        }
    ]
    assert project_store.get_project(localization["project_id"]) is not None
    assert project_store.get_project(localization["project_id"]).updated_at == project_updated_at

    temporarily_unavailable_root.rename(project_root)
    recovered = client.post("/api/projects/video-localization/sync-project-summaries")

    assert recovered.status_code == 200
    assert recovered.json()[0]["project_id"] == localization["project_id"]
    assert recovered.json()[0]["has_local_package"] is True
    assert recovered.json()[0]["package_status"] == "available"
    assert recovered.json()[0]["updated_at"] == project_updated_at


def test_workspace_and_summaries_share_path_free_media_health(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "媒体健康"}).json()
    project_id = project["project_id"]
    root = media_assets.project_video_localization_dir(project_id)
    video_path = root / "source" / "source.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"fixture-video")
    saved = client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "source_media": {
                "filename": "source.mp4",
                "video_path": str(video_path),
                "duration_ms": 1200,
            }
        },
    )
    assert saved.status_code == 200

    workspace = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )
    summaries = client.get(
        "/api/projects/summaries",
        params={"kind": "video_localization"},
    )

    assert workspace.status_code == 200
    health = workspace.json()["media_health"]
    assert health["contract_version"] == "project-media-health-v1"
    assert health["package_status"] == "available"
    assert health["source_video"]["status"] == "available"
    assert health["source_video"]["resource_id"] == "source_video"
    assert health["source_video"]["revision"]
    assert "path" not in str(health).lower()
    assert str(tmp_path) not in str(health)
    assert summaries.status_code == 200
    assert summaries.json()[0]["has_source_media"] is True
    assert summaries.json()[0]["source_media_status"] == "available"
    assert summaries.json()[0]["package_status"] == "available"


def test_sync_rebases_managed_paths_after_external_package_rename(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "外部移动"}).json()
    project_id = project["project_id"]
    old_root = media_assets.project_video_localization_dir(project_id)
    video_path = old_root / "source" / "source.mp4"
    source_audio = old_root / "audio" / "source.wav"
    vocals = old_root / "stems" / "vocals.wav"
    background = old_root / "stems" / "background.wav"
    for path, payload in (
        (video_path, b"video"),
        (source_audio, b"source-audio"),
        (vocals, b"vocals"),
        (background, b"background"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    saved = client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "source_media": {
                "filename": "source.mp4",
                "video_path": str(video_path),
                "audio_path": str(source_audio),
                "duration_ms": 1200,
            },
            "stems": {
                "original_audio_path": str(source_audio),
                "vocals_clean_path": str(vocals),
                "background_path": str(background),
                "separation_status": "completed",
            },
        },
    )
    assert saved.status_code == 200

    moved_root = old_root.with_name(f"moved--{project_id}")
    old_root.rename(moved_root)
    synced = client.post(
        "/api/projects/video-localization/sync-project-summaries"
    )

    assert synced.status_code == 200
    assert synced.json()[0]["package_status"] == "available"
    stored = project_store.get_project(project_id)
    assert stored is not None
    assert stored.parameters["video_localization_dir_name"] == moved_root.name
    stored_draft = stored.parameters["video_localization"]
    assert stored_draft["source_media"]["video_path"] == str(
        moved_root / "source" / "source.mp4"
    )
    assert stored_draft["source_media"]["audio_path"] == str(
        moved_root / "audio" / "source.wav"
    )
    assert stored_draft["stems"]["vocals_clean_path"] == str(
        moved_root / "stems" / "vocals.wav"
    )
    assert not old_root.exists()

    workspace = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )
    served = client.get(
        f"/api/projects/{project_id}/video-localization/source-media/video"
    )
    assert workspace.status_code == 200
    assert workspace.json()["media_health"]["source_video"]["status"] == "available"
    assert workspace.json()["media_health"]["stems_status"] == "complete"
    assert served.status_code == 200
    assert served.content == b"video"
    assert not old_root.exists()


def test_localization_sync_summaries_sort_by_project_update_not_package_mtime(tmp_path: Path):
    client = _client(tmp_path)
    older = client.post("/api/projects", json={"name": "较早操作"}).json()
    newer = client.post("/api/projects", json={"name": "最近操作"}).json()
    for project in (older, newer):
        saved = client.put(
            f"/api/projects/{project['project_id']}/video-localization",
            json={"source_media": {"filename": f"{project['project_id']}.mp4", "duration_ms": 1200}},
        )
        assert saved.status_code == 200

    stored_older = project_store.get_project(older["project_id"])
    stored_newer = project_store.get_project(newer["project_id"])
    assert stored_older is not None
    assert stored_newer is not None
    stored_older.updated_at = "2026-07-29T09:00:00.000000"
    stored_newer.updated_at = "2026-07-29T10:00:00.000000"
    project_store.save_project(stored_older, touch_updated_at=False)
    project_store.save_project(stored_newer, touch_updated_at=False)

    older_manifest = (
        tmp_path
        / "projects"
        / stored_older.parameters["video_localization_dir_name"]
        / "project.json"
    )
    newer_manifest = (
        tmp_path
        / "projects"
        / stored_newer.parameters["video_localization_dir_name"]
        / "project.json"
    )
    os.utime(older_manifest, (200, 200))
    os.utime(newer_manifest, (100, 100))

    response = client.post("/api/projects/video-localization/sync-project-summaries")

    assert response.status_code == 200
    assert [item["project_id"] for item in response.json()] == [
        newer["project_id"],
        older["project_id"],
    ]
    assert [item["updated_at"] for item in response.json()] == [
        "2026-07-29T10:00:00.000000",
        "2026-07-29T09:00:00.000000",
    ]


def test_project_open_and_index_repair_do_not_touch_last_update(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "只读打开"}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"source_media": {"filename": "readonly.mp4", "duration_ms": 1200}},
    )
    assert saved.status_code == 200

    stored = project_store.get_project(project["project_id"])
    assert stored is not None
    stored.updated_at = "2026-07-29T08:00:00.000000"
    stored.parameters.pop("video_localization_dir_name")
    project_store.save_project(stored, touch_updated_at=False)

    opened = client.get(f"/api/projects/{project['project_id']}/video-localization")
    synced = client.post("/api/projects/video-localization/sync-project-summaries")

    assert opened.status_code == 200
    assert synced.status_code == 200
    current = project_store.get_project(project["project_id"])
    assert current is not None
    assert current.updated_at == "2026-07-29T08:00:00.000000"

    assert synced.json()[0]["updated_at"] == "2026-07-29T08:00:00.000000"


def test_project_edit_advances_last_update(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "可编辑项目"}).json()
    first_save = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"source_media": {"filename": "editable.mp4", "duration_ms": 1200}},
    )
    assert first_save.status_code == 200

    stored = project_store.get_project(project["project_id"])
    assert stored is not None
    stored.updated_at = "2000-01-01T00:00:00.000000"
    project_store.save_project(stored, touch_updated_at=False)

    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    draft["scene_context"] = "用户编辑后的场景说明"
    edited = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=draft,
    )

    assert edited.status_code == 200
    current = project_store.get_project(project["project_id"])
    assert current is not None
    assert current.updated_at > "2000-01-01T00:00:00.000000"


def test_ui_workspace_patch_does_not_advance_last_update(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "工作区状态"}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"source_media": {"filename": "workspace.mp4", "duration_ms": 1200}},
    )
    assert saved.status_code == 200
    draft_updated_at = saved.json()["updated_at"]

    stored = project_store.get_project(project["project_id"])
    assert stored is not None
    stored.updated_at = "2026-07-29T08:00:00.000000"
    project_store.save_project(stored, touch_updated_at=False)

    patched = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/ui-state",
        json={"playhead_ms": 800, "timeline_zoom": 1.5},
    )

    assert patched.status_code == 200
    assert patched.json()["updated_at"] > draft_updated_at
    current = project_store.get_project(project["project_id"])
    assert current is not None
    assert current.updated_at == "2026-07-29T08:00:00.000000"

    no_op = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/ui-state",
        json={"playhead_ms": 800, "timeline_zoom": 1.5},
    )
    assert no_op.status_code == 200
    assert no_op.json()["updated_at"] == patched.json()["updated_at"]
    current = project_store.get_project(project["project_id"])
    assert current is not None
    assert current.updated_at == "2026-07-29T08:00:00.000000"


def test_operation_runtime_progress_does_not_advance_last_update(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "后台任务进度"}).json()
    project_id = project["project_id"]
    draft = video_localization_service.get_video_localization(project_id)
    assert draft is not None
    operation = VideoLocalizationOperation(
        operation_id="operation_runtime_progress",
        project_id=project_id,
        kind="english_asr",
        status="queued",
    )
    saved = video_localization_service.save_video_localization(
        project_id,
        draft.model_copy(update={"operations": [operation]}),
    )
    assert saved is not None
    draft_updated_at = saved.updated_at

    stored = project_store.get_project(project_id)
    assert stored is not None
    stored.updated_at = "2026-07-29T08:00:00.000000"
    project_store.save_project(stored, touch_updated_at=False)

    operation_queue._mark_operation(
        project_id,
        operation.operation_id,
        status="running",
        progress=0.5,
    )

    current = project_store.get_project(project_id)
    assert current is not None
    assert current.updated_at == "2026-07-29T08:00:00.000000"
    current_draft = video_localization_service.get_video_localization(project_id)
    assert current_draft is not None
    assert current_draft.updated_at is not None
    assert current_draft.updated_at > draft_updated_at
    assert current_draft.operations[0].status == "running"
    assert current_draft.operations[0].progress == 0.5


def test_interrupted_operation_recovery_does_not_advance_last_update(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "重启恢复"}).json()
    project_id = project["project_id"]
    draft = video_localization_service.get_video_localization(project_id)
    assert draft is not None
    operation = VideoLocalizationOperation(
        operation_id="operation_restart_recovery",
        project_id=project_id,
        kind="english_asr",
        status="running",
    )
    saved = video_localization_service.save_video_localization(
        project_id,
        draft.model_copy(update={"operations": [operation]}),
    )
    assert saved is not None
    draft_updated_at = saved.updated_at
    stored = project_store.get_project(project_id)
    assert stored is not None
    stored.updated_at = "2026-07-29T08:00:00.000000"
    project_store.save_project(stored, touch_updated_at=False)
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _operation_id: None)

    operation_queue._recover_active_operations()

    current = project_store.get_project(project_id)
    assert current is not None
    assert current.updated_at == "2026-07-29T08:00:00.000000"
    recovered = video_localization_service.get_video_localization(project_id)
    assert recovered is not None
    assert recovered.updated_at is not None
    assert recovered.updated_at > draft_updated_at
    assert recovered.operations[0].status == "failed"
    assert (
        recovered.operations[0].error_code
        == "VIDEO_LOCALIZATION_OPERATION_INTERRUPTED"
    )


def test_user_cancel_command_advances_last_update_once(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "用户取消任务"}).json()
    project_id = project["project_id"]
    operation = VideoLocalizationOperation(
        operation_id="operation_user_cancel",
        project_id=project_id,
        kind="english_asr",
        status="queued",
    )
    draft = video_localization_service.get_video_localization(project_id)
    assert draft is not None
    saved = video_localization_service.save_video_localization(
        project_id,
        draft.model_copy(update={"operations": [operation]}),
    )
    assert saved is not None
    stored = project_store.get_project(project_id)
    assert stored is not None
    stored.updated_at = "2000-01-01T00:00:00.000000"
    project_store.save_project(stored, touch_updated_at=False)
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _operation_id: None)

    cancelled = client.post(
        f"/api/projects/{project_id}/video-localization/operations/"
        f"{operation.operation_id}/cancel"
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    current = project_store.get_project(project_id)
    assert current is not None
    assert current.updated_at > "2000-01-01T00:00:00.000000"


def test_json_export_does_not_advance_last_update(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "只读导出"}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"scene_context": "用于导出"},
    )
    assert saved.status_code == 200

    stored = project_store.get_project(project["project_id"])
    assert stored is not None
    stored.updated_at = "2026-07-29T08:00:00.000000"
    project_store.save_project(stored, touch_updated_at=False)

    exported = client.get(
        f"/api/projects/{project['project_id']}/video-localization/export"
    )

    assert exported.status_code == 200
    current = project_store.get_project(project["project_id"])
    assert current is not None
    assert current.updated_at == "2026-07-29T08:00:00.000000"
