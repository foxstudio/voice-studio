from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    draft_store,
    media_assets,
    quality_gate,
)
from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings, VideoLocalizationDraft  # noqa: E402
from app.services import database, project_store, settings_store  # noqa: E402


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


def test_get_draft_does_not_persist_or_move_legacy_project_storage(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_data = client.post(
        "/api/projects",
        json={"name": "只读旧项目", "description": ""},
    ).json()
    project_id = project_data["project_id"]
    legacy_root = (
        tmp_path
        / "projects"
        / project_id
        / media_assets.LEGACY_DIR_NAME
    )
    legacy_video = legacy_root / "source" / "legacy.mp4"
    legacy_video.parent.mkdir(parents=True)
    legacy_video.write_bytes(b"legacy-video")

    project = project_store.get_project(project_id)
    assert project is not None
    project.parameters = {
        "video_localization": VideoLocalizationDraft(
            source_media={
                "filename": legacy_video.name,
                "video_path": str(legacy_video),
                "duration_ms": 1200,
            }
        ).model_dump(mode="json")
    }
    project_store.save_project(project, touch_updated_at=False)
    destination = (
        tmp_path
        / "projects"
        / media_assets.project_dir_name(project_id, project.name)
    )
    save_calls: list[str] = []
    manifest_calls: list[str] = []
    original_save = project_store.save_project

    def unexpected_save(value, *, touch_updated_at=True):
        save_calls.append(value.project_id)
        return original_save(value, touch_updated_at=touch_updated_at)

    monkeypatch.setattr(project_store, "save_project", unexpected_save)
    monkeypatch.setattr(
        draft_store.project_manifest,
        "write_project_snapshot",
        lambda *_args, **_kwargs: manifest_calls.append(project_id),
    )

    response = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )

    assert response.status_code == 200
    assert save_calls == []
    assert manifest_calls == []
    assert legacy_video.read_bytes() == b"legacy-video"
    assert not destination.exists()
    current = project_store.get_project(project_id)
    assert current is not None
    assert media_assets.PROJECT_DIR_NAME_KEY not in current.parameters
    assert (
        current.parameters["video_localization"]["source_media"]["video_path"]
        == str(legacy_video)
    )


def test_get_draft_normalizes_playable_audio_span_without_persisting(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_data = client.post(
        "/api/projects",
        json={"name": "音频时长归一", "description": ""},
    ).json()
    project_id = project_data["project_id"]
    project = project_store.get_project(project_id)
    assert project is not None
    raw_draft = VideoLocalizationDraft(
        source_media={"frame_rate": 30, "duration_ms": 10_000},
        timeline_clips=[
            {
                "clip_id": "dub-1",
                "track_id": "dub",
                "start_ms": 2_000,
                "end_ms": 5_500,
                "source_start_ms": 100,
                "source_end_ms": 1_600,
            }
        ],
    )
    project.parameters = {
        "video_localization": raw_draft.model_dump(mode="json")
    }
    project_store.save_project(project, touch_updated_at=False)

    loaded = draft_store.get(project_id)

    assert loaded is not None
    assert loaded.timeline_clips[0]["end_ms"] == 3_500
    stored = project_store.get_project(project_id)
    assert stored is not None
    assert (
        stored.parameters["video_localization"]["timeline_clips"][0]["end_ms"]
        == 5_500
    )


def test_save_persists_one_canonical_playable_audio_span(tmp_path: Path):
    client = _client(tmp_path)
    project_data = client.post(
        "/api/projects",
        json={"name": "音频时长持久化", "description": ""},
    ).json()
    project_id = project_data["project_id"]
    malformed = VideoLocalizationDraft(
        source_media={"frame_rate": 30, "duration_ms": 10_000},
        timeline_clips=[
            {
                "clip_id": "dub-1",
                "track_id": "dub",
                "start_ms": 2_000,
                "end_ms": 5_500,
                "source_start_ms": 100,
                "source_end_ms": 1_600,
            }
        ],
    )

    saved = draft_store.save(project_id, malformed, intent="content")

    assert saved is not None
    assert saved.timeline_clips[0]["end_ms"] == 3_500
    stored = project_store.get_project(project_id)
    assert stored is not None
    assert (
        stored.parameters["video_localization"]["timeline_clips"][0]["end_ms"]
        == 3_500
    )


def test_missing_database_draft_requires_explicit_snapshot_repair(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_data = client.post(
        "/api/projects",
        json={"name": "显式快照修复", "description": ""},
    ).json()
    project_id = project_data["project_id"]
    saved = client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "source_media": {
                "filename": "recover.mp4",
                "duration_ms": 3200,
            },
            "cues": [
                {
                    "cue_id": "cue_recovered",
                    "start_ms": 0,
                    "end_ms": 1200,
                    "en_subtitle_text": "Recovered.",
                }
            ],
        },
    )
    assert saved.status_code == 200

    project = project_store.get_project(project_id)
    assert project is not None
    original_updated_at = project.updated_at
    project.parameters.pop("video_localization")
    project_store.save_project(project, touch_updated_at=False)

    read_response = client.get(
        f"/api/projects/{project_id}/video-localization"
    )

    assert read_response.status_code == 409
    assert read_response.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_DRAFT_REPAIR_REQUIRED"
    )
    unchanged = project_store.get_project(project_id)
    assert unchanged is not None
    assert "video_localization" not in unchanged.parameters
    assert unchanged.updated_at == original_updated_at

    repaired = client.post(
        f"/api/projects/{project_id}/video-localization/repair-storage"
    )

    assert repaired.status_code == 200
    assert repaired.json()["source_media"]["filename"] == "recover.mp4"
    assert repaired.json()["cues"][0]["cue_id"] == "cue_recovered"
    current = project_store.get_project(project_id)
    assert current is not None
    assert current.updated_at == original_updated_at
    assert current.parameters["video_localization"]["cues"][0]["cue_id"] == (
        "cue_recovered"
    )
    autosave_dir = (
        media_assets.project_video_localization_dir(project_id)
        / "autosave"
    )
    autosave_count = len(list(autosave_dir.glob("*-project.json")))
    monkeypatch.setattr(
        quality_gate,
        "now_iso",
        lambda: "2099-01-01T00:00:00",
    )

    repaired_again = client.post(
        f"/api/projects/{project_id}/video-localization/repair-storage"
    )

    assert repaired_again.status_code == 200
    assert len(list(autosave_dir.glob("*-project.json"))) == autosave_count
    after_second_repair = project_store.get_project(project_id)
    assert after_second_repair is not None
    assert after_second_repair.updated_at == original_updated_at


def test_explicit_repair_resolves_portable_project_paths_in_database_draft(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_data = client.post(
        "/api/projects",
        json={"name": "便携路径修复", "description": ""},
    ).json()
    project_id = project_data["project_id"]
    project_root = media_assets.ensure_project_video_localization_dir(project_id)
    audio_path = project_root / "tts" / "localized_cue_0001" / "result.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"RIFF")

    project = project_store.get_project(project_id)
    assert project is not None
    project.parameters["video_localization"] = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip_localized_cue_0001",
                "track_id": "dub",
                "start_ms": 0,
                "end_ms": 1000,
                "source_start_ms": 0,
                "source_end_ms": 1000,
                "status": "ready",
                "audio_path": "project://tts/localized_cue_0001/result.wav",
            }
        ]
    ).model_dump(mode="json")
    project_store.save_project(project, touch_updated_at=False)

    repaired = client.post(
        f"/api/projects/{project_id}/video-localization/repair-storage"
    )

    assert repaired.status_code == 200
    stored = project_store.get_project(project_id)
    assert stored is not None
    assert stored.parameters["video_localization"]["timeline_clips"][0][
        "audio_path"
    ] == str(audio_path.resolve())


def test_read_normalization_is_persisted_only_by_explicit_repair(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_data = client.post(
        "/api/projects",
        json={"name": "显式草稿修复", "description": ""},
    ).json()
    project_id = project_data["project_id"]
    project = project_store.get_project(project_id)
    assert project is not None
    duplicate_clips = [
        {
            "clip_id": "clip_same",
            "track_id": "dub",
            "start_ms": 0,
            "end_ms": 1000,
            "audio_path": "/tmp/old.wav",
        },
        {
            "clip_id": "clip_same",
            "track_id": "dub",
            "start_ms": 0,
            "end_ms": 1200,
            "audio_path": "/tmp/latest.wav",
        },
    ]
    project.parameters = {
        "video_localization": VideoLocalizationDraft(
            timeline_clips=duplicate_clips
        ).model_dump(mode="json")
    }
    project_store.save_project(project, touch_updated_at=False)

    read_response = client.get(
        f"/api/projects/{project_id}/video-localization"
    )

    assert read_response.status_code == 200
    assert len(read_response.json()["timeline_clips"]) == 1
    stored_after_read = project_store.get_project(project_id)
    assert stored_after_read is not None
    assert len(
        stored_after_read.parameters["video_localization"]["timeline_clips"]
    ) == 2

    repaired = client.post(
        f"/api/projects/{project_id}/video-localization/repair-storage"
    )

    assert repaired.status_code == 200
    assert len(repaired.json()["timeline_clips"]) == 1
    stored_after_repair = project_store.get_project(project_id)
    assert stored_after_repair is not None
    assert len(
        stored_after_repair.parameters["video_localization"]["timeline_clips"]
    ) == 1


def test_repair_without_a_recoverable_snapshot_refuses_to_create_empty_state(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_data = client.post(
        "/api/projects",
        json={"name": "没有快照", "description": ""},
    ).json()
    project_id = project_data["project_id"]
    project = project_store.get_project(project_id)
    assert project is not None
    original_updated_at = project.updated_at
    project.parameters = {
        media_assets.PROJECT_DIR_NAME_KEY: media_assets.project_dir_name(
            project_id,
            project.name,
        )
    }
    project_store.save_project(project, touch_updated_at=False)

    opened = client.get(
        f"/api/projects/{project_id}/video-localization"
    )
    response = client.post(
        f"/api/projects/{project_id}/video-localization/repair-storage"
    )

    assert opened.status_code == 200
    assert opened.json()["cues"] == []
    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_DRAFT_RECOVERY_NOT_FOUND"
    )
    current = project_store.get_project(project_id)
    assert current is not None
    assert "video_localization" not in current.parameters
    assert current.updated_at == original_updated_at
