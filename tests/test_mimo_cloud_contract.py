from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings, VoiceAssetCreate  # noqa: E402
from app.services import database, engine_registry, mimo_client, qwen_forced_aligner, settings_store, voice_store  # noqa: E402


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
        "qwen3-asr-mlx",
        "faster-whisper-turbo",
    } <= set(by_id)

    preset = by_id["mimo-v2.5-tts-preset"]
    preset_param_keys = {p["key"] for p in preset["parameter_schema"]}
    preset_voice = next(p for p in preset["parameter_schema"] if p["key"] == "mimo_voice")
    assert preset_voice["type"] == "select"
    assert {x["value"] for x in preset_voice["options"]} >= {"冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"}
    assert "singing" in preset["capabilities"]

    design = by_id["mimo-v2.5-tts-voicedesign"]
    design_param_keys = {p["key"] for p in design["parameter_schema"]}
    assert "voice_design" in design["capabilities"]
    assert next(p for p in design["parameter_schema"] if p["key"] == "voice_design_prompt")["required"] is True
    assert "optimize_text_preview" in design_param_keys

    clone = by_id["mimo-v2.5-tts-voiceclone"]
    clone_param_keys = {p["key"] for p in clone["parameter_schema"]}
    assert "voice_clone" in clone["capabilities"]
    assert "preset_voice" not in clone["capabilities"]
    assert clone_param_keys == {"style_instruction", "temperature", "top_p"}

    local_only_params = {
        "speed",
        "top_k",
        "max_text_tokens_per_segment",
        "interval_silence",
        "diffusion_steps",
        "cfg_rate",
        "emotion",
        "emo_alpha",
    }
    assert local_only_params.isdisjoint(preset_param_keys)
    assert local_only_params.isdisjoint(design_param_keys)
    assert local_only_params.isdisjoint(clone_param_keys)

    qwen = by_id["qwen3-asr-mlx"]
    assert qwen["engine_type"] == "local"
    assert "speech_recognition" in qwen["capabilities"]
    assert "transcription" in qwen["capabilities"]

    turbo = by_id["faster-whisper-turbo"]
    assert turbo["engine_type"] == "local"
    assert turbo["supported_languages"] == ["auto", "en"]
    assert "vad" in turbo["capabilities"]


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
        optimize_text_preview=True,
    )
    assert design["messages"][0]["role"] == "user"
    assert design["messages"][0]["content"] == "中年男性，声音沉稳，语速缓慢。"
    assert design["messages"][1] == {"role": "assistant", "content": "欢迎收听。"}
    assert design["audio"] == {"format": "wav", "optimize_text_preview": True}

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


def test_asr_transcribe_endpoint_returns_transcript_and_stores_history(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.patch("/api/settings/mimo-secret", json={"api_key": "secret-token"})

    def fake_transcribe_audio(**kwargs):
        assert kwargs["language"] == "zh"
        assert kwargs["audio_path"].endswith(".wav")
        return {
            "text": "今天下午三点开会。",
            "segments": [],
            "usage_seconds": 4,
            "provider_response_id": "mimo-asr-123",
        }

    monkeypatch.setattr(mimo_client, "transcribe_audio", fake_transcribe_audio)

    resp = client.post(
        "/api/asr/transcribe",
        data={"language": "zh"},
        files={"file": ("meeting.wav", b"RIFF" + b"\0" * 128, "audio/wav")},
    )
    assert resp.status_code == 200
    record = resp.json()
    assert record["engine_id"] == "mimo-v2.5-asr"
    assert record["filename"] == "meeting.wav"
    assert record["language"] == "zh"
    assert record["text"] == "今天下午三点开会。"
    assert record["has_source_audio"] is True
    assert record["timestamp_mode"] == "none"
    assert record["provider_response_id"] == "mimo-asr-123"

    stored = database.get_one("transcriptions", "transcription_id", record["transcription_id"])
    assert stored is not None
    assert Path(stored["source_audio_path"]).exists()

    history = client.get("/api/asr/history")
    assert history.status_code == 200
    entries = history.json()
    assert len(entries) == 1
    assert entries[0]["transcription_id"] == record["transcription_id"]
    assert entries[0]["text"] == "今天下午三点开会。"


def test_asr_transcribe_endpoint_dispatches_selected_engine(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)

    monkeypatch.setattr(
        engine_registry,
        "health_check",
        lambda engine_id: {"healthy": True, "status": "ready", "engine_id": engine_id},
    )

    import app.services.qwen_mlx_asr as qwen_mlx_asr  # noqa: E402

    def fake_local_transcribe(*, audio_path: str, language: str, model_path: str):
        assert audio_path.endswith(".wav")
        assert language == "zh"
        assert Path(model_path).name in {"qwen3-asr-mlx", "mlx-community_Qwen3-ASR-1.7B-8bit"}
        return {
            "text": "这是本地 Qwen3-ASR 结果。",
            "segments": [
                {"start_ms": 0, "end_ms": 1350, "text": "这是本地", "language": "Chinese"},
                {"start_ms": 1350, "end_ms": 2600, "text": "Qwen3-ASR 结果。", "language": "Chinese"},
            ],
            "usage_seconds": None,
            "provider_response_id": None,
        }

    monkeypatch.setattr(qwen_mlx_asr, "transcribe_audio", fake_local_transcribe)

    resp = client.post(
        "/api/asr/transcribe",
        data={"language": "zh", "engine_id": "qwen3-asr-mlx"},
        files={"file": ("meeting.wav", b"RIFF" + b"\0" * 128, "audio/wav")},
    )
    assert resp.status_code == 200
    record = resp.json()
    assert record["engine_id"] == "qwen3-asr-mlx"
    assert record["text"] == "这是本地 Qwen3-ASR 结果。"
    assert len(record["segments"]) == 2
    assert record["timestamp_mode"] == "native"
    assert record["timestamp_source_engine_id"] == "qwen3-asr-mlx"

    srt = client.get(f"/api/asr/{record['transcription_id']}/export", params={"format": "srt"})
    assert srt.status_code == 200
    assert "00:00:00,000 --> 00:00:01,350" in srt.text
    assert "Qwen3-ASR 结果。" in srt.text


def test_asr_transcribe_endpoint_dispatches_faster_whisper_turbo(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)

    monkeypatch.setattr(
        engine_registry,
        "health_check",
        lambda engine_id: {"healthy": True, "status": "ready", "engine_id": engine_id},
    )

    import app.services.faster_whisper_asr as faster_whisper_asr  # noqa: E402

    def fake_turbo_transcribe(*, audio_path: str, language: str, model_path: str):
        assert audio_path.endswith(".wav")
        assert language == "en"
        assert Path(model_path).name == "faster-whisper-turbo"
        return {
            "text": "We shipped the first localization pass.",
            "segments": [
                {"start_ms": 0, "end_ms": 1850, "text": "We shipped", "language": "en"},
                {"start_ms": 1850, "end_ms": 4200, "text": "the first localization pass.", "language": "en"},
            ],
            "usage_seconds": 2,
            "provider_response_id": None,
        }

    monkeypatch.setattr(faster_whisper_asr, "transcribe_audio", fake_turbo_transcribe)

    resp = client.post(
        "/api/asr/transcribe",
        data={"language": "en", "engine_id": "faster-whisper-turbo"},
        files={"file": ("clip.wav", b"RIFF" + b"\0" * 128, "audio/wav")},
    )
    assert resp.status_code == 200
    record = resp.json()
    assert record["engine_id"] == "faster-whisper-turbo"
    assert record["text"] == "We shipped the first localization pass."
    assert len(record["segments"]) == 2
    assert record["timestamp_mode"] == "native"
    assert record["timestamp_source_engine_id"] == "faster-whisper-turbo"


def test_mimo_transcription_srt_export_is_rejected_without_segments(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.patch("/api/settings/mimo-secret", json={"api_key": "secret-token"})

    monkeypatch.setattr(
        mimo_client,
        "transcribe_audio",
        lambda **kwargs: {
            "text": "只有纯文本。",
            "segments": [],
            "usage_seconds": 1,
            "provider_response_id": "mimo-asr-no-srt",
        },
    )

    resp = client.post(
        "/api/asr/transcribe",
        data={"language": "zh"},
        files={"file": ("meeting.wav", b"RIFF" + b"\0" * 128, "audio/wav")},
    )
    assert resp.status_code == 200
    record = resp.json()

    srt = client.get(f"/api/asr/{record['transcription_id']}/export", params={"format": "srt"})
    assert srt.status_code == 400
    assert srt.json()["error"]["code"] == "ASR_SRT_UNAVAILABLE"


def test_mimo_transcription_can_supplement_timestamps_with_forced_aligner_first(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.patch("/api/settings/mimo-secret", json={"api_key": "secret-token"})

    monkeypatch.setattr(
        mimo_client,
        "transcribe_audio",
        lambda **kwargs: {
            "text": "第一句。第二句更长一点。",
            "segments": [],
            "usage_seconds": 1,
            "provider_response_id": "mimo-asr-need-align",
        },
    )
    monkeypatch.setattr(
        qwen_forced_aligner,
        "align_audio",
        lambda **kwargs: [
            {"text": "第", "start_time": 0.0, "end_time": 0.3},
            {"text": "一", "start_time": 0.3, "end_time": 0.6},
            {"text": "句", "start_time": 0.6, "end_time": 1.0},
            {"text": "第", "start_time": 1.2, "end_time": 1.5},
            {"text": "二", "start_time": 1.5, "end_time": 1.8},
            {"text": "句", "start_time": 1.8, "end_time": 2.1},
            {"text": "更", "start_time": 2.1, "end_time": 2.4},
            {"text": "长", "start_time": 2.4, "end_time": 2.7},
            {"text": "一", "start_time": 2.7, "end_time": 3.0},
            {"text": "点", "start_time": 3.0, "end_time": 3.3},
        ],
    )

    resp = client.post(
        "/api/asr/transcribe",
        data={"language": "zh"},
        files={"file": ("meeting.wav", b"RIFF" + b"\0" * 128, "audio/wav")},
    )
    assert resp.status_code == 200
    record = resp.json()
    assert record["segments"] == []

    aligned = client.post(f"/api/asr/{record['transcription_id']}/timestamps", json={})
    assert aligned.status_code == 200
    updated = aligned.json()
    assert updated["timestamp_mode"] == "supplemented"
    assert updated["timestamp_source_engine_id"] == "qwen3-forced-aligner-0.6B"
    assert len(updated["segments"]) == 2
    assert updated["segments"][0]["text"] == "第一句。"
    assert updated["segments"][1]["text"] == "第二句更长一点。"
    assert updated["segments"][0]["end_ms"] == 1000
    assert updated["segments"][1]["start_ms"] == 1200

    srt = client.get(f"/api/asr/{record['transcription_id']}/export", params={"format": "srt"})
    assert srt.status_code == 200
    assert "00:00:00,000 --> 00:00:01,000" in srt.text
    assert "第二句更长一点。" in srt.text


def test_timestamp_supplement_falls_back_to_coarse_qwen_when_forced_aligner_unavailable(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.patch("/api/settings/mimo-secret", json={"api_key": "secret-token"})

    monkeypatch.setattr(
        mimo_client,
        "transcribe_audio",
        lambda **kwargs: {
            "text": "第一句。第二句更长一点。",
            "segments": [],
            "usage_seconds": 1,
            "provider_response_id": "mimo-asr-need-align-fallback",
        },
    )
    monkeypatch.setattr(
        qwen_forced_aligner,
        "align_audio",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("forced align unavailable")),
    )
    monkeypatch.setattr(
        engine_registry,
        "health_check",
        lambda engine_id: {"healthy": True, "status": "ready", "engine_id": engine_id},
    )

    import app.services.qwen_mlx_asr as qwen_mlx_asr  # noqa: E402

    monkeypatch.setattr(
        qwen_mlx_asr,
        "transcribe_audio",
        lambda **kwargs: {
            "text": "本地转写参考文本",
            "segments": [
                {"start_ms": 0, "end_ms": 1200, "text": "本地第一段", "language": "Chinese"},
                {"start_ms": 1200, "end_ms": 3100, "text": "本地第二段", "language": "Chinese"},
            ],
            "usage_seconds": None,
            "provider_response_id": None,
        },
    )

    resp = client.post(
        "/api/asr/transcribe",
        data={"language": "zh"},
        files={"file": ("meeting.wav", b"RIFF" + b"\0" * 128, "audio/wav")},
    )
    assert resp.status_code == 200
    record = resp.json()

    aligned = client.post(f"/api/asr/{record['transcription_id']}/timestamps", json={})
    assert aligned.status_code == 200
    updated = aligned.json()
    assert updated["timestamp_source_engine_id"] == "qwen3-asr-mlx"
    assert len(updated["segments"]) == 2


def test_batch_timestamp_supplement_and_batch_delete_work_for_transcriptions(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.patch("/api/settings/mimo-secret", json={"api_key": "secret-token"})

    monkeypatch.setattr(
        mimo_client,
        "transcribe_audio",
        lambda **kwargs: {
            "text": "第一句。第二句。",
            "segments": [],
            "usage_seconds": 1,
            "provider_response_id": "mimo-asr-batch",
        },
    )
    monkeypatch.setattr(
        qwen_forced_aligner,
        "align_audio",
        lambda **kwargs: [
            {"text": "第", "start_time": 0.0, "end_time": 0.2},
            {"text": "一", "start_time": 0.2, "end_time": 0.4},
            {"text": "句", "start_time": 0.4, "end_time": 0.8},
            {"text": "第", "start_time": 1.0, "end_time": 1.2},
            {"text": "二", "start_time": 1.2, "end_time": 1.4},
            {"text": "句", "start_time": 1.4, "end_time": 1.8},
        ],
    )

    created: list[dict] = []
    for index in range(2):
        resp = client.post(
            "/api/asr/transcribe",
            data={"language": "zh"},
            files={"file": (f"meeting-{index}.wav", b"RIFF" + b"\0" * 128, "audio/wav")},
        )
        assert resp.status_code == 200
        created.append(resp.json())

    source_paths = [
        Path(database.get_one("transcriptions", "transcription_id", item["transcription_id"])["source_audio_path"])
        for item in created
    ]
    assert all(path.exists() for path in source_paths)

    batch_aligned = client.post(
        "/api/asr/timestamps/batch",
        json={"transcription_ids": [item["transcription_id"] for item in created], "strategy": "auto"},
    )
    assert batch_aligned.status_code == 200
    aligned_items = batch_aligned.json()
    assert len(aligned_items) == 2
    assert all(item["timestamp_source_engine_id"] == "qwen3-forced-aligner-0.6B" for item in aligned_items)
    assert all(len(item["segments"]) == 2 for item in aligned_items)

    batch_deleted = client.post(
        "/api/asr/batch-delete",
        json={"transcription_ids": [item["transcription_id"] for item in created]},
    )
    assert batch_deleted.status_code == 200
    assert set(batch_deleted.json()["deleted_ids"]) == {item["transcription_id"] for item in created}
    assert all(database.get_one("transcriptions", "transcription_id", item["transcription_id"]) is None for item in created)
    assert all(not path.exists() for path in source_paths)


def test_async_asr_task_endpoint_completes_and_persists_history(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.patch("/api/settings/mimo-secret", json={"api_key": "secret-token"})

    monkeypatch.setattr(
        mimo_client,
        "transcribe_audio",
        lambda **kwargs: {
            "text": "这是异步转写结果。",
            "segments": [],
            "usage_seconds": 2,
            "provider_response_id": "mimo-asr-task-1",
        },
    )

    resp = client.post(
        "/api/asr/tasks",
        data={"language": "zh"},
        files={"file": ("clip.wav", b"RIFF" + b"\0" * 128, "audio/wav")},
    )
    assert resp.status_code == 200
    task = resp.json()
    assert task["status"] == "queued"

    deadline = time.time() + 3
    final = None
    while time.time() < deadline:
        poll = client.get(f"/api/asr/tasks/{task['task_id']}")
        assert poll.status_code == 200
        final = poll.json()
        if final["status"] in {"success", "failed"}:
            break
        time.sleep(0.05)

    assert final is not None
    assert final["status"] == "success"
    assert final["text"] == "这是异步转写结果。"
    assert final["provider_response_id"] == "mimo-asr-task-1"
    assert final["transcription_id"] is not None

    history = client.get("/api/asr/history")
    assert history.status_code == 200
    assert any(item["transcription_id"] == final["transcription_id"] for item in history.json())


def test_qwen3_asr_health_reports_runtime_missing_when_model_exists(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    model_dir = tmp_path / "qwen3-asr-mlx"
    model_dir.mkdir(parents=True, exist_ok=True)
    for name in [
        "config.json",
        "preprocessor_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "model.safetensors",
    ]:
        (model_dir / name).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(settings_store, "model_path", lambda engine_id: model_dir)
    import app.services.qwen_mlx_asr as qwen_mlx_asr  # noqa: E402

    monkeypatch.setattr(qwen_mlx_asr, "runtime_available", lambda: (False, "mlx-audio is not installed"))

    resp = client.post("/api/engines/qwen3-asr-mlx/health-check")
    assert resp.status_code == 200
    data = resp.json()
    assert data["healthy"] is False
    assert data["status"] == "runtime_missing"
    assert data["model_path"] == str(model_dir)
    assert "mlx-audio" in data["detail"]


def test_faster_whisper_turbo_health_reports_runtime_missing_when_model_exists(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    model_dir = tmp_path / "faster-whisper-turbo"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.bin").write_bytes(b"model")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(settings_store, "model_path", lambda engine_id: model_dir)
    import app.services.faster_whisper_asr as faster_whisper_asr  # noqa: E402

    monkeypatch.setattr(faster_whisper_asr, "runtime_available", lambda: (False, "faster-whisper is not installed"))

    resp = client.post("/api/engines/faster-whisper-turbo/health-check")
    assert resp.status_code == 200
    data = resp.json()
    assert data["healthy"] is False
    assert data["status"] == "runtime_missing"
    assert data["model_path"] == str(model_dir)
    assert data["model_id"] == "dropbox-dash/faster-whisper-large-v3-turbo"
    assert data["original_model_id"] == "openai/whisper-large-v3-turbo"
    assert "faster-whisper" in data["detail"]


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
