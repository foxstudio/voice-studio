from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import database, settings_store  # noqa: E402


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


def test_video_localization_draft_round_trips_in_project_parameters(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本土化测试", "description": ""}).json()

    draft = {
        "project_type": "video_localization",
        "schema_version": "v1",
        "source_media": {"filename": "source.mp4", "duration_ms": 3400},
        "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
        "reference_clips": [
            {
                "reference_clip_id": "ref_001",
                "speaker_id": "speaker_01",
                "source_stem": "vocals_clean",
                "asr_text": "In 1992, this changed everything.",
                "cleanliness": "clean",
            }
        ],
        "cues": [
            {
                "cue_id": "cue_0001",
                "speaker_id": "speaker_01",
                "start_ms": 1200,
                "end_ms": 3400,
                "en_subtitle_text": "In 1992, this changed everything.",
                "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                "tts_recommended_text": "一九九二年，这件事，改变了一切。",
                "reference_clip_id": "ref_001",
                "review_status": "needs_review",
            }
        ],
        "quality_gate": {"pending_issues": 1},
    }

    saved = client.put(f"/api/projects/{project['project_id']}/video-localization", json=draft)
    assert saved.status_code == 200
    assert saved.json()["updated_at"]
    assert saved.json()["cues"][0]["tts_recommended_text"].startswith("一九九二年")

    fetched = client.get(f"/api/projects/{project['project_id']}/video-localization")
    assert fetched.status_code == 200
    assert fetched.json()["reference_clips"][0]["source_stem"] == "vocals_clean"

    stored_project = client.get(f"/api/projects/{project['project_id']}").json()
    assert stored_project["parameters"]["video_localization"]["cues"][0]["cue_id"] == "cue_0001"


def test_video_localization_empty_draft_has_contract_defaults(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空草稿", "description": ""}).json()

    response = client.get(f"/api/projects/{project['project_id']}/video-localization")

    assert response.status_code == 200
    body = response.json()
    assert body["project_type"] == "video_localization"
    assert body["schema_version"] == "v1"
    assert body["source_media"]["filename"] is None
    assert body["source_media"]["metadata"] == {}
    assert body["stems"]["separation_status"] == "pending"
    assert body["quality_gate"]["status"] == "unknown"
    assert body["quality_gate"]["pending_issues"] == 0


def test_video_localization_rejects_inverted_time_ranges(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "坏时间码", "description": ""}).json()

    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_bad",
                    "start_ms": 5000,
                    "end_ms": 3000,
                    "tts_recommended_text": "这句时间码有问题。",
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_video_localization_export_adds_project_metadata(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "导出测试", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "tts_recommended_text": "你好。"}],
        },
    )

    exported = client.get(f"/api/projects/{project['project_id']}/video-localization/export")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"].endswith(f'{project["project_id"]}-video-localization.json"')
    body = exported.json()
    assert body["project_id"] == project["project_id"]
    assert body["project_name"] == "导出测试"
    assert body["cues"][0]["tts_recommended_text"] == "你好。"
