from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.models.schemas import AppSettings, VoiceAssetCreate  # noqa: E402
from app.services import database, mimo_client, settings_store, voice_store  # noqa: E402


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
            cloud_enabled=True,
        )
    )
    return TestClient(app)


def test_mimo_engines_are_split_and_legacy_id_is_hidden(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/engines")
    assert resp.status_code == 200
    by_id = {item["manifest"]["engine_id"]: item["manifest"] for item in resp.json()}

    assert "mimo-v2.5-tts" not in by_id
    assert {
        "mimo-v2.5-tts-preset",
        "mimo-v2.5-tts-voicedesign",
        "mimo-v2.5-tts-voiceclone",
        "mimo-v2.5-asr",
    } <= set(by_id)

    preset = by_id["mimo-v2.5-tts-preset"]
    preset_voice = next(p for p in preset["parameter_schema"] if p["key"] == "mimo_voice")
    assert preset_voice["type"] == "select"
    assert {x["value"] for x in preset_voice["options"]} >= {"冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"}
    assert "singing" in preset["capabilities"]

    design = by_id["mimo-v2.5-tts-voicedesign"]
    assert "voice_design" in design["capabilities"]
    assert next(p for p in design["parameter_schema"] if p["key"] == "voice_design_prompt")["required"] is True

    clone = by_id["mimo-v2.5-tts-voiceclone"]
    assert "voice_clone" in clone["capabilities"]
    assert "preset_voice" not in clone["capabilities"]


def test_settings_include_default_voice_and_cloud_upload_confirmation(tmp_path: Path):
    client = _client(tmp_path)
    settings = client.get("/api/settings").json()
    assert settings["default_voice_id"] is None
    assert settings["mimo_voiceclone_confirm_upload"] is True

    settings["default_voice_id"] = "voice-123"
    settings["mimo_voiceclone_confirm_upload"] = False
    saved = client.patch("/api/settings", json=settings).json()
    assert saved["default_voice_id"] == "voice-123"
    assert saved["mimo_voiceclone_confirm_upload"] is False


def test_mimo_tts_payloads_match_official_message_roles(tmp_path: Path):
    voice_sample = tmp_path / "voice.wav"
    voice_sample.write_bytes(b"RIFF" + b"\0" * 128)

    preset = mimo_client.build_tts_payload(
        model="mimo-v2.5-tts",
        text="测试一句话。",
        audio_format="wav",
        voice="冰糖",
        style_instruction="语速稍慢，语气温柔。",
        temperature=0.6,
        top_p=0.95,
    )
    assert preset["messages"] == [
        {"role": "user", "content": "语速稍慢，语气温柔。"},
        {"role": "assistant", "content": "测试一句话。"},
    ]
    assert preset["audio"] == {"format": "wav", "voice": "冰糖"}
    assert preset["temperature"] == 0.6
    assert preset["top_p"] == 0.95

    design = mimo_client.build_tts_payload(
        model="mimo-v2.5-tts-voicedesign",
        text="欢迎收听。",
        audio_format="wav",
        voice_design_prompt="中年男性，声音沉稳，语速缓慢。",
    )
    assert design["messages"][0]["role"] == "user"
    assert design["messages"][0]["content"] == "中年男性，声音沉稳，语速缓慢。"
    assert design["messages"][1] == {"role": "assistant", "content": "欢迎收听。"}
    assert design["audio"] == {"format": "wav"}

    clone = mimo_client.build_tts_payload(
        model="mimo-v2.5-tts-voiceclone",
        text="这是复刻测试。",
        audio_format="wav",
        reference_audio_path=str(voice_sample),
    )
    assert clone["audio"]["voice"].startswith("data:audio/wav;base64,")
    encoded = clone["audio"]["voice"].split(",", 1)[1]
    assert base64.b64decode(encoded) == voice_sample.read_bytes()


def test_mimo_validation_rejects_invalid_voiceclone_inputs(tmp_path: Path):
    unsupported = tmp_path / "voice.flac"
    unsupported.write_bytes(b"fake")
    with pytest.raises(ValueError, match="MIMO_VOICECLONE_AUDIO_FORMAT_UNSUPPORTED"):
        mimo_client.audio_file_data_url(str(unsupported))

    large = tmp_path / "large.wav"
    large.write_bytes(b"0" * (10 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="MIMO_VOICECLONE_AUDIO_TOO_LARGE"):
        mimo_client.audio_file_data_url(str(large))

    with pytest.raises(ValueError, match="MIMO_VOICE_DESIGN_PROMPT_REQUIRED"):
        mimo_client.build_tts_payload(
            model="mimo-v2.5-tts-voicedesign",
            text="缺少音色描述。",
            audio_format="wav",
        )


def test_asr_payload_accepts_wav_mp3_and_language_options(tmp_path: Path):
    audio = tmp_path / "sample.mp3"
    audio.write_bytes(b"ID3" + b"\0" * 128)
    payload = mimo_client.build_asr_payload(str(audio), language="zh")

    assert payload["model"] == "mimo-v2.5-asr"
    assert payload["asr_options"] == {"language": "zh"}
    content = payload["messages"][0]["content"][0]
    assert content["type"] == "input_audio"
    assert content["input_audio"]["data"].startswith("data:audio/mpeg;base64,")

    with pytest.raises(ValueError, match="MIMO_ASR_LANGUAGE_UNSUPPORTED"):
        mimo_client.build_asr_payload(str(audio), language="ja")


def test_voice_assets_report_engine_bindings(tmp_path: Path):
    _client(tmp_path)
    audio_path = tmp_path / "voices" / "sample.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"RIFF" + b"\0" * 128)
    database.upsert(
        "voice_files",
        "file-1",
        {
            "file_id": "file-1",
            "original_name": "sample.wav",
            "path": str(audio_path),
            "mime_type": "audio/wav",
            "duration_ms": 3000,
            "sample_rate": 24000,
            "size_bytes": audio_path.stat().st_size,
            "created_at": "2026-06-07T00:00:00",
        },
        "created_at",
    )
    voice = voice_store.create_voice(
        VoiceAssetCreate(
            name="测试参考音色",
            reference_audio_ids=["file-1"],
            license_status="authorized",
            recommended_engine_id="indextts-v2",
        )
    )

    bindings = {b.engine_id: b for b in voice_store.get_voice(voice.voice_id).engine_bindings}
    assert bindings["indextts-v2"].available is True
    assert bindings["omnivoice"].available is True
    assert bindings["mimo-v2.5-tts-voiceclone"].available is True
    assert bindings["mimo-v2.5-tts-preset"].available is False

    raw = database.get_one("voices", "voice_id", voice.voice_id)
    assert "engine_bindings" not in json.dumps(raw, ensure_ascii=False)
