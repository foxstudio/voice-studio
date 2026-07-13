from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.models.exceptions import AppException  # noqa: E402
from app.schemas.voice_studio import AppSettings, GenerateRequest, VoiceAsset  # noqa: E402
from app.services import database, doubao_client, engine_request_builder, settings_store  # noqa: E402
import pytest  # noqa: E402


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.delenv("VOLCENGINE_API_KEY", raising=False)
    monkeypatch.delenv("VOLCENGINE_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("VOLCENGINE_SECRET_ACCESS_KEY", raising=False)
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


def test_volcengine_directory_credentials_are_write_only_and_can_be_cleared(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    initial = client.get("/api/settings").json()
    assert initial["volcengine_access_key_id_configured"] is False
    assert initial["volcengine_secret_access_key_configured"] is False
    assert "volcengine_access_key_id" not in initial
    assert "volcengine_secret_access_key" not in initial

    saved = client.patch(
        "/api/settings/volcengine-directory-secret",
        json={"access_key_id": "  test-ak  ", "secret_access_key": "  test-sk  "},
    ).json()
    assert saved["volcengine_access_key_id_configured"] is True
    assert saved["volcengine_secret_access_key_configured"] is True
    assert "volcengine_access_key_id" not in saved
    assert "volcengine_secret_access_key" not in saved
    assert settings_store.volcengine_access_key_id() == "test-ak"
    assert settings_store.volcengine_secret_access_key() == "test-sk"

    cleared_ak = client.patch(
        "/api/settings/volcengine-directory-secret",
        json={"clear_access_key_id": True},
    ).json()
    assert cleared_ak["volcengine_access_key_id_configured"] is False
    assert cleared_ak["volcengine_secret_access_key_configured"] is True
    assert settings_store.volcengine_access_key_id() is None
    assert settings_store.volcengine_secret_access_key() == "test-sk"

    cleared_sk = client.patch(
        "/api/settings/volcengine-directory-secret",
        json={"clear_secret_access_key": True},
    ).json()
    assert cleared_sk["volcengine_access_key_id_configured"] is False
    assert cleared_sk["volcengine_secret_access_key_configured"] is False
    assert settings_store.volcengine_secret_access_key() is None


def test_volcengine_directory_credentials_detect_standard_environment_variables(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("VOLCENGINE_ACCESS_KEY_ID", "env-ak")
    monkeypatch.setenv("VOLCENGINE_SECRET_ACCESS_KEY", "env-sk")

    settings = client.get("/api/settings").json()
    assert settings["volcengine_access_key_id_configured"] is True
    assert settings["volcengine_secret_access_key_configured"] is True
    assert "volcengine_access_key_id" not in settings
    assert "volcengine_secret_access_key" not in settings
    assert settings_store.volcengine_access_key_id() == "env-ak"
    assert settings_store.volcengine_secret_access_key() == "env-sk"


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
        sample_rate=48000,
        bit_rate=160000,
        speed=1.12,
        loudness_rate=25,
        pitch_rate=-3,
        style_instruction="自然、清晰。",
        enable_subtitle=True,
        silence_duration=600,
        aigc_watermark=True,
    )
    assert payload == {
        "user": {"uid": "voice-studio"},
        "req_params": {
            "text": "测试一句话。",
            "speaker": "zh_female_vv_uranus_bigtts",
            "audio_params": {
                "format": "mp3",
                "sample_rate": 48000,
                "bit_rate": 160000,
                "speech_rate": 12,
                "loudness_rate": 25,
                "enable_subtitle": True,
            },
            "additions": '{"context_texts": ["自然、清晰。"], "post_process": {"pitch": -3}, "silence_duration": 600, "aigc_watermark": true}',
        },
    }

    frames = doubao_client.iter_concatenated_json('{"data":"YQ=="}{"code":20000000,"message":"ok"}')
    assert frames == [{"data": "YQ=="}, {"code": 20000000, "message": "ok"}]

    with pytest.raises(doubao_client.DoubaoAPIError, match="不支持输出格式"):
        doubao_client.build_tts_payload(text="测试", speaker="speaker", audio_format="flac")


def test_doubao_voice_clone_payload(tmp_path: Path):
    audio = tmp_path / "ref.wav"
    audio.write_bytes(b"voice-bytes")

    payload = doubao_client.build_voice_clone_payload(
        speaker_id="voice_studio_demo",
        custom_speaker_id="voice_studio_demo",
        audio_path=str(audio),
        text="这是一段参考台词。",
        language="zh",
        demo_text="试听这段豆包复刻音色。",
        enable_audio_denoise=True,
        disable_volume_normalization=False,
    )

    assert payload == {
        "speaker_id": "voice_studio_demo",
        "custom_speaker_id": "voice_studio_demo",
        "audio": {"data": "dm9pY2UtYnl0ZXM=", "format": "wav"},
        "language": 0,
        "text": "这是一段参考台词。",
        "extra_params": {
            "demo_text": "试听这段豆包复刻音色。",
            "enable_audio_denoise": True,
            "disable_volume_normalization": False,
        },
    }


def test_doubao_voice_clone_language_mapping(tmp_path: Path):
    audio = tmp_path / "ref.wav"
    audio.write_bytes(b"voice-bytes")

    assert doubao_client.voice_clone_language_code("zh") == 0
    assert doubao_client.voice_clone_language_code("en") == 1
    assert doubao_client.voice_clone_language_code(8) == 8
    assert doubao_client.build_voice_clone_payload(
        speaker_id="voice_studio_demo",
        audio_path=str(audio),
        language="ko",
    )["language"] == 8


def test_doubao_voice_clone_train_requires_confirmation_and_updates_binding(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.patch("/api/settings/doubao-secret", json={"api_key": "test-doubao-key"})
    registered = client.post(
        "/api/voices/register",
        data={
            "name": "豆包训练样本",
            "reference_text": "这是一段参考台词。",
            "license_status": "self_voice",
            "tags": "豆包,云端",
        },
        files={"file": ("sample.wav", b"voice-bytes", "audio/wav")},
    ).json()

    blocked = client.post(f"/api/voices/{registered['voice_id']}/doubao/clone-train", json={"confirm_upload": False})
    assert blocked.status_code == 400
    assert blocked.json()["error"]["code"] == "DOUBAO_UPLOAD_CONFIRM_REQUIRED"

    def fake_train_voice_clone(**kwargs):
        assert kwargs["resource_id"] == "seed-icl-2.0"
        assert kwargs["api_key"] == "test-doubao-key"
        assert kwargs["text"] == "这是一段参考台词。"
        assert Path(kwargs["audio_path"]).exists()
        return doubao_client.DoubaoResponse(body={"status": "submitted"}, logid="train-log", request_id="train-request")

    def fake_get_voice(**kwargs):
        assert kwargs["speaker_id"].startswith("voice_studio_")
        return doubao_client.DoubaoResponse(
            body={
                "status": 2,
                "language": 0,
                "available_training_times": 13,
                "speaker_status": [{"model_type": 1, "demo_audio": "https://example.test/demo.wav"}],
            },
            logid="query-log",
            request_id="query-request",
        )

    monkeypatch.setattr(doubao_client, "train_voice_clone", fake_train_voice_clone)
    monkeypatch.setattr(doubao_client, "get_voice", fake_get_voice)

    trained = client.post(
        f"/api/voices/{registered['voice_id']}/doubao/clone-train",
        json={"confirm_upload": True, "demo_text": "试听这段豆包复刻音色。"},
    )
    assert trained.status_code == 200
    data = trained.json()
    voice = data["voice"]
    assert voice["external_provider"] == "doubao"
    assert voice["external_voice_id"].startswith("voice_studio_")
    assert voice["external_status"] == "2"
    assert voice["recommended_engine_id"] == "doubao-tts-voiceclone"
    doubao_binding = next(item for item in voice["engine_bindings"] if item["engine_id"] == "doubao-tts-voiceclone")
    assert doubao_binding["available"] is True
    assert doubao_binding["external_voice_id"] == voice["external_voice_id"]
    assert data["summary"]["has_demo_audio"] is True


def test_doubao_cloud_list_refresh_and_unbind(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.patch("/api/settings/doubao-secret", json={"api_key": "test-doubao-key"})
    registered = client.post(
        "/api/voices/register",
        data={
            "name": "豆包云端样本",
            "reference_text": "这是一段参考台词。",
            "license_status": "self_voice",
            "tags": "豆包,云端",
        },
        files={"file": ("sample.wav", b"voice-bytes", "audio/wav")},
    ).json()

    from app.services import voice_store  # noqa: E402

    bound = voice_store.update_external_binding(
        registered["voice_id"],
        provider="doubao",
        external_voice_id="voice_studio_ready",
        status="submitted",
        metadata={"custom_speaker_id": "voice_studio_ready"},
        recommended_engine_id="doubao-tts-voiceclone",
    )
    assert bound is not None

    cloud = client.get("/api/voices/doubao/cloud")
    assert cloud.status_code == 200
    assert cloud.json()["count"] == 1
    assert cloud.json()["management"]["cloud_delete_supported"] is False

    def fake_get_voice(**kwargs):
        assert kwargs["speaker_id"] == "voice_studio_ready"
        assert kwargs["custom_speaker_id"] == "voice_studio_ready"
        return doubao_client.DoubaoResponse(
            body={"status": 2, "language": 0, "speaker_status": [{"model_type": 1}]},
            logid="query-log",
            request_id="query-request",
        )

    monkeypatch.setattr(doubao_client, "get_voice", fake_get_voice)
    refreshed = client.post("/api/voices/doubao/cloud/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["count"] == 1
    assert refreshed.json()["voices"][0]["external_status"] == "2"

    unbound = client.delete(f"/api/voices/{registered['voice_id']}/doubao/binding")
    assert unbound.status_code == 200
    assert unbound.json()["external_provider"] is None
    assert unbound.json()["external_voice_id"] is None
    assert unbound.json()["recommended_engine_id"] is None


def test_doubao_voiceclone_generation_uses_external_speaker_and_rejects_raw_reference(tmp_path: Path, monkeypatch):
    _client(tmp_path, monkeypatch)
    settings_store.update(AppSettings(data_dir=str(tmp_path), cloud_enabled=True))
    settings_store.update_doubao_api_key("test-doubao-key")
    voice = VoiceAsset(
        name="豆包云端音色",
        external_provider="doubao",
        external_voice_id="voice_studio_ready",
        external_status="2",
    )
    req = GenerateRequest(
        text="用训练好的豆包音色合成。",
        engine_id="doubao-tts-voiceclone",
        voice_id=voice.voice_id,
        style_instruction="自然清晰。",
        output_format="mp3",
    )

    kwargs = engine_request_builder.build_doubao_tts_single_kwargs(req, str(tmp_path / "out.mp3"), voice=voice)

    assert kwargs["speaker"] == "voice_studio_ready"
    assert kwargs["resource_id"] == "seed-icl-2.0"
    assert kwargs["style_instruction"] is None

    raw_reference_req = req.model_copy(update={"reference_audio_path": str(tmp_path / "raw.wav")})
    try:
        engine_request_builder.build_doubao_tts_single_kwargs(raw_reference_req, str(tmp_path / "out.mp3"), voice=voice)
    except AppException as exc:
        assert exc.code == "DOUBAO_REFERENCE_AUDIO_NOT_SUPPORTED"
    else:
        raise AssertionError("raw reference audio must not be accepted for doubao voiceclone synthesis")

    training_voice = voice.model_copy(update={"external_status": "training"})
    try:
        engine_request_builder.build_doubao_tts_single_kwargs(req, str(tmp_path / "out.mp3"), voice=training_voice)
    except AppException as exc:
        assert exc.code == "DOUBAO_VOICE_NOT_READY"
    else:
        raise AssertionError("non-ready doubao voice must not be accepted")


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
    pitch = next(param for param in manifest["parameter_schema"] if param["key"] == "pitch_rate")
    assert (pitch["min"], pitch["max"], pitch["default"]) == (-12, 12, 0)

    clone_manifest = by_id["doubao-tts-voiceclone"]
    assert clone_manifest["engine_type"] == "cloud"
    assert "voice_clone" in clone_manifest["capabilities"]
    assert "natural_language_control" not in clone_manifest["capabilities"]
    assert not any(param["key"] == "style_instruction" for param in clone_manifest["parameter_schema"])
    assert not any(param["key"] == "speaker_id" for param in clone_manifest["parameter_schema"])
    clone_pitch = next(param for param in clone_manifest["parameter_schema"] if param["key"] == "pitch_rate")
    assert (clone_pitch["min"], clone_pitch["max"], clone_pitch["default"]) == (-12, 12, 0)

    params = {param["key"]: param for param in manifest["parameter_schema"]}
    assert [option["value"] for option in params["sample_rate"]["options"]] == [8000, 16000, 22050, 24000, 32000, 44100, 48000]
    assert (params["loudness_rate"]["min"], params["loudness_rate"]["max"]) == (-50, 100)
    assert params["enable_subtitle"]["default"] is False
    assert (params["silence_duration"]["min"], params["silence_duration"]["max"]) == (0, 30000)


def test_doubao_speaker_catalog_supports_search_gender_and_custom_ids(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    settings_store.update_doubao_api_key("test-doubao-key")

    all_items = client.get("/api/engines/doubao-tts-preset/speakers").json()
    assert len(all_items) == len(doubao_client.DOUBAO_TTS_PRESET_SPEAKERS)
    assert all(item["speaker_id"] and item["label"] for item in all_items)

    female = client.get("/api/engines/doubao-tts-preset/speakers", params={"gender": "F"}).json()
    assert female and all(item["gender"] == "F" for item in female)

    searched = client.get("/api/engines/doubao-tts-preset/speakers", params={"q": "vivi"}).json()
    assert [item["speaker_id"] for item in searched] == ["zh_female_vv_uranus_bigtts"]

    custom = GenerateRequest(
        text="测试自定义官方音色 ID",
        engine_id="doubao-tts-preset",
        speaker_id="account_authorized_voice_type",
    )
    kwargs = engine_request_builder.build_doubao_tts_single_kwargs(custom, str(tmp_path / "out.wav"))
    assert kwargs["speaker"] == "account_authorized_voice_type"
