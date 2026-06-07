from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.models.schemas import AppSettings  # noqa: E402
from app.services import batch_queue, database, settings_store, voice_aliases  # noqa: E402


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


def test_presets_are_available_and_apply_to_main_engines(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/presets")
    assert resp.status_code == 200
    presets = resp.json()
    assert len(presets) >= 6
    ids = {preset["preset_id"] for preset in presets}
    assert "idx2_default_narration" in ids
    assert "idx2_long_text_editing" in ids
    assert {preset["engine_id"] for preset in presets} <= {"indextts-v2", "omnivoice"}
    default = next(p for p in presets if p["preset_id"] == "idx2_default_narration")
    assert default["parameters"]["emotion"] == "calm"
    assert default["parameters"]["temperature"] == 0.8


def test_voice_seed_catalog_contains_official_index_examples(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/voice-seeds")
    assert resp.status_code == 200
    seeds = resp.json()
    assert len(seeds) >= 8
    first = seeds[0]
    assert first["license_status"] == "test_only"
    assert first["recommended_engine_id"] == "indextts-v2"
    assert first["download_url"].startswith("https://media.githubusercontent.com/media/index-tts/index-tts/")


def test_seed_voice_name_normalization_keeps_user_names():
    assert (
        voice_aliases.normalized_seed_voice_name(
            "IndexTTS 官方参考音色 12",
            ["官方示例", "seed:index_voice_12"],
        )
        == "官方强情绪候选 - 强调表达"
    )
    assert (
        voice_aliases.normalized_seed_voice_name(
            "我自己改过的官方声音",
            ["官方示例", "seed:index_voice_12"],
        )
        == "我自己改过的官方声音"
    )


def test_engine_registry_exposes_only_current_main_engines(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/engines")
    assert resp.status_code == 200
    by_id = {item["manifest"]["engine_id"]: item["manifest"] for item in resp.json()}
    assert set(by_id) == {
        "indextts-v2",
        "omnivoice",
        "mimo-v2.5-tts-preset",
        "mimo-v2.5-tts-voicedesign",
        "mimo-v2.5-tts-voiceclone",
        "mimo-v2.5-asr",
    }
    assert "emotion_control" in by_id["indextts-v2"]["capabilities"]
    assert by_id["mimo-v2.5-tts-preset"]["engine_type"] == "cloud"
    assert "mimo-v2.5-tts" not in by_id


def test_mimo_secret_is_not_returned_in_settings(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.patch("/api/settings/mimo-secret", json={"api_key": "secret-token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["mimo_api_key_configured"] is True
    assert data["cloud_enabled"] is True
    assert "secret-token" not in str(data)

    settings = client.get("/api/settings").json()
    assert settings["mimo_api_key_configured"] is True
    assert "secret-token" not in str(settings)


def test_batch_endpoint_accepts_audio_segments_shape(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    monkeypatch.setattr(batch_queue, "start_worker", lambda: None)
    resp = client.post(
        "/api/batches/generate",
        json=[
            {"chapter": "intro", "step": 1, "text": "第一段。", "audio": "intro/1.mp3"},
            {"chapter": "intro", "step": 2, "text": "第二段。", "emotion": "calm"},
        ],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["segments"][0]["segment_id"] == "intro-1"
    assert data["segments"][0]["audio"] == "intro/1.mp3"
    assert data["segments"][1]["audio"] == "intro/2.mp3"

    fetched = client.get(f"/api/batches/{data['batch_task_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["batch_task_id"] == data["batch_task_id"]
