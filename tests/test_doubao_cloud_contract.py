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
from app.services import database, doubao_client, settings_store  # noqa: E402


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.delenv("VOLCENGINE_API_KEY", raising=False)
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


def test_settings_include_doubao_defaults_and_secret_state(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    settings = client.get("/api/settings").json()
    assert settings["doubao_base_url"] == "https://openspeech.bytedance.com"
    assert settings["doubao_api_key_configured"] is False
    assert settings["doubao_default_tts_resource_id"] == "seed-tts-2.0"
    assert settings["doubao_default_icl_resource_id"] == "seed-icl-2.0"
    assert settings["doubao_upload_confirm"] is True

    saved = client.patch("/api/settings/doubao-secret", json={"api_key": "  test-doubao-key  "}).json()
    assert saved["cloud_enabled"] is True
    assert saved["doubao_api_key_configured"] is True
    assert "doubao_api_key" not in saved
    assert settings_store.doubao_api_key() == "test-doubao-key"

    cleared = client.patch("/api/settings/doubao-secret", json={"clear": True}).json()
    assert cleared["doubao_api_key_configured"] is False
    assert settings_store.doubao_api_key() is None


def test_doubao_env_key_is_detected_without_persisting_secret(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("VOLCENGINE_API_KEY", "env-doubao-key")

    settings = client.get("/api/settings").json()
    assert settings["doubao_api_key_configured"] is True
    assert settings_store.doubao_api_key() == "env-doubao-key"


def test_doubao_client_headers_masking_and_voice_summary():
    headers, request_id = doubao_client.build_headers(
        api_key="secret",
        resource_id="seed-tts-2.0",
        request_id="request-1",
    )
    assert request_id == "request-1"
    assert headers["X-Api-Key"] == "secret"
    assert headers["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert headers["X-Api-Request-Id"] == "request-1"
    assert doubao_client.masked_identifier("S_example_wX1") == "S_e***wX1"

    response = doubao_client.DoubaoResponse(
        body={
            "status": 2,
            "language": 0,
            "available_training_times": 14,
            "speaker_status": [
                {"model_type": 1, "demo_audio": "https://example.test/demo.wav"},
                {"model_type": 4},
            ],
        },
        logid="log-1",
        request_id="request-1",
    )

    summary = doubao_client.summarize_voice_status(response, speaker_id="S_example_wX1")
    assert summary == {
        "speaker_id": "S_e***wX1",
        "status": 2,
        "language": 0,
        "available_training_times": 14,
        "model_types": [1, 4],
        "has_demo_audio": True,
        "request_id": "request-1",
        "logid": "log-1",
    }


def test_doubao_tts_payload_and_chunk_parser():
    payload = doubao_client.build_tts_payload(
        text="测试一句话。",
        speaker="zh_female_vv_uranus_bigtts",
        audio_format="mp3",
        speed=1.12,
        style_instruction="自然、清晰。",
    )
    assert payload == {
        "user": {"uid": "voice-studio"},
        "req_params": {
            "text": "测试一句话。",
            "speaker": "zh_female_vv_uranus_bigtts",
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "speech_rate": 12,
            },
            "additions": '{"context_texts": ["自然、清晰。"]}',
        },
    }

    frames = doubao_client.iter_concatenated_json('{"data":"YQ=="}{"code":20000000,"message":"ok"}')
    assert frames == [{"data": "YQ=="}, {"code": 20000000, "message": "ok"}]


def test_doubao_engine_manifest_is_registered(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/api/engines")
    assert resp.status_code == 200
    by_id = {item["manifest"]["engine_id"]: item["manifest"] for item in resp.json()}

    manifest = by_id["doubao-tts-preset"]
    assert manifest["engine_type"] == "cloud"
    assert "preset_voice" in manifest["capabilities"]
    assert "natural_language_control" in manifest["capabilities"]
    speaker = next(param for param in manifest["parameter_schema"] if param["key"] == "speaker_id")
    assert speaker["default"] == "zh_female_vv_uranus_bigtts"
    assert {option["value"] for option in speaker["options"]} >= {
        "zh_female_vv_uranus_bigtts",
        "zh_female_xiaohe_uranus_bigtts",
    }
