from __future__ import annotations

import builtins
import sys
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings, EngineAudioDiagnosisRequest, GenerateRequest, HistoryItem, Project, Role, ScriptSegment  # noqa: E402
from app.services import audio_tools, batch_queue, community_voice_pack_store, database, engine_registry, history_store, qwen3_tts_paths, ser_service, settings_store, task_queue, voice_aliases, voice_store  # noqa: E402


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


def test_presets_are_available_and_apply_to_main_engines(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(qwen3_tts_paths, "voice_design_available", lambda: True)
    client = _client(tmp_path)
    resp = client.get("/api/presets")
    assert resp.status_code == 200
    presets = resp.json()
    assert len(presets) >= 6
    ids = {preset["preset_id"] for preset in presets}
    assert "idx2_default_narration" in ids
    assert "idx2_long_text_editing" in ids
    assert {
        "indextts-v2",
        "omnivoice",
        "emotivoice",
        "confucius4-mlx-int8",
        "qwen3-tts-mlx-0.6b",
        "f5-tts",
        "cosyvoice-sft",
        "cosyvoice-zero-shot",
    } <= {preset["engine_id"] for preset in presets}
    assert "f5_official_default_clone" in ids
    assert "cosy_zero_reference_default" in ids
    assert "qwen3_tutorial_slow" in ids
    assert "qwen3_voice_design_warm" in ids
    default = next(p for p in presets if p["preset_id"] == "idx2_default_narration")
    assert default["name"] == "贴近参考音色"
    assert default["parameters"]["emotion"] is None
    assert default["parameters"]["emo_alpha"] == 0.0
    assert default["parameters"]["temperature"] == 0.8
    qwen3_design = next(p for p in presets if p["preset_id"] == "qwen3_voice_design_warm")
    assert qwen3_design["recommended_voice_type"] == "voice_design"
    assert qwen3_design["parameters"]["voice_design_prompt"].startswith("温柔的中文女声")
    assert qwen3_design["parameters"]["temperature"] == 0.65
    assert qwen3_design["parameters"]["repetition_penalty"] == 1.15
    assert "speaker_id" not in qwen3_design["parameters"]


def test_qwen3_voice_design_is_hidden_when_optional_model_is_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(qwen3_tts_paths, "voice_design_available", lambda: False)
    client = _client(tmp_path)

    presets = client.get("/api/presets").json()
    ids = {preset["preset_id"] for preset in presets}
    assert "qwen3_voice_design_warm" not in ids
    assert client.get("/api/presets/qwen3_voice_design_warm").status_code == 404

    engines = client.get("/api/engines").json()
    qwen3 = next(item["manifest"] for item in engines if item["manifest"]["engine_id"] == qwen3_tts_paths.ENGINE_ID)
    assert "voice_design" not in qwen3["capabilities"]
    assert not any(param["key"] == "voice_design_prompt" for param in qwen3["parameter_schema"])


def test_emotivoice_speaker_catalog_can_be_filtered(tmp_path: Path, monkeypatch):
    root = tmp_path / "EmotiVoice"
    readme = root / "data" / "youdao" / "text" / "README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text(
        "\n".join(
            [
                "| ID | Voice Name | Gender | Description |",
                "|----|-------|--------|-------------|",
                "| 8051 | Maria Kasper | F | Clear, soothing, expressive |",
                "| 9017 | John Van Stan | M | Rich, resonant, engaging |",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(engine_registry, "_external_engine_root", lambda engine_id: root)
    engine_registry._emotivoice_speaker_catalog.cache_clear()
    try:
        client = _client(tmp_path)
        resp = client.get("/api/engines/emotivoice/speakers?q=maria&gender=F&limit=10")

        assert resp.status_code == 200
        speakers = resp.json()
        assert len(speakers) == 1
        assert speakers[0]["speaker_id"] == "8051"
        assert speakers[0]["name"] == "Maria Kasper"
        assert "Clear" in speakers[0]["label"]
    finally:
        engine_registry._emotivoice_speaker_catalog.cache_clear()


def test_f5_run_isolated_uses_persistent_worker_by_default(tmp_path: Path, monkeypatch):
    captured: dict = {}

    def fake_run(kwargs, *, root, python, timeout, cancel_check=None, on_tick=None):
        captured.update({"kwargs": kwargs, "root": root, "python": python, "timeout": timeout, "cancel_check": cancel_check, "on_tick": on_tick})
        return {"output_path": kwargs["output_path"], "duration_ms": 1000, "generation_time_ms": 42}

    root = tmp_path / "F5-TTS"
    monkeypatch.setenv("VOICE_STUDIO_F5_TTS_ROOT", str(root))
    monkeypatch.delenv("VOICE_STUDIO_F5_PERSISTENT_WORKER", raising=False)
    monkeypatch.setattr(engine_registry.f5_worker, "run", fake_run)

    result = engine_registry.run_isolated("f5-tts", {"output_path": "/tmp/f5.wav"}, timeout=123)

    assert result["output_path"] == "/tmp/f5.wav"
    assert captured["timeout"] == 123
    assert captured["root"].name == "F5-TTS"
    assert captured["python"].endswith("/.venv/bin/python")


def test_cosyvoice_run_isolated_uses_persistent_worker_by_default(tmp_path: Path, monkeypatch):
    captured: dict = {}

    def fake_run(engine_id, kwargs, *, root, python, timeout, cancel_check=None, on_tick=None):
        captured.update(
            {
                "engine_id": engine_id,
                "kwargs": kwargs,
                "root": root,
                "python": python,
                "timeout": timeout,
                "cancel_check": cancel_check,
                "on_tick": on_tick,
            }
        )
        return {"output_path": kwargs["output_path"], "duration_ms": 1000, "generation_time_ms": 42}

    root = tmp_path / "CosyVoice"
    monkeypatch.setenv("VOICE_STUDIO_COSYVOICE_ROOT", str(root))
    monkeypatch.delenv("VOICE_STUDIO_COSYVOICE_PERSISTENT_WORKER", raising=False)
    monkeypatch.setattr(engine_registry.cosyvoice_worker, "run", fake_run)

    result = engine_registry.run_isolated("cosyvoice-zero-shot", {"output_path": "/tmp/cosy.wav"}, timeout=456)

    assert result["output_path"] == "/tmp/cosy.wav"
    assert captured["engine_id"] == "cosyvoice-zero-shot"
    assert captured["timeout"] == 456
    assert captured["root"].name == "CosyVoice"
    assert captured["python"].endswith("/.venv/bin/python")


def test_custom_presets_can_be_created_updated_and_deleted(tmp_path: Path):
    client = _client(tmp_path)
    payload = {
        "name": "我的 MiMo 慢讲",
        "scene": "课程旁白",
        "description": "语速稍慢，适合长句。",
        "engine_id": "mimo-v2.5-tts-voiceclone",
        "sample_text": "这是一段自定义预设测试文本。",
        "parameters": {
            "style_instruction": "语速稍慢，停顿自然。",
            "temperature": 0.6,
            "top_p": 0.95,
            "output_format": "wav",
        },
        "tags": ["自定义", "慢讲"],
    }

    created = client.post("/api/presets", json=payload)
    assert created.status_code == 200
    preset = created.json()
    assert preset["preset_id"].startswith("custom_")
    assert preset["engine_id"] == "mimo-v2.5-tts-voiceclone"

    updated = client.patch(
        f"/api/presets/{preset['preset_id']}",
        json={**payload, "name": "我的 MiMo 慢讲 v2"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "我的 MiMo 慢讲 v2"

    listed = client.get("/api/presets").json()
    assert any(item["preset_id"] == preset["preset_id"] for item in listed)

    deleted = client.delete(f"/api/presets/{preset['preset_id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/presets/{preset['preset_id']}").status_code == 404


def test_seed_audio_custom_preset_round_trips_mode_assets_and_parameters(tmp_path: Path):
    client = _client(tmp_path)
    payload = {
        "name": "我的文字声音场景",
        "scene": "Seed Audio / 文本模式",
        "description": "保存当前文本模式的声音场景。",
        "engine_id": "doubao-seed-audio-1.0",
        "input_mode": "text",
        "input_assets": [],
        "sample_text": "雨声中，两个人用压低的声音交谈。",
        "parameters": {"format": "wav", "sample_rate": 24000, "speech_rate": 0},
        "recommended_voice_type": "generated_audio",
        "tags": ["Seed Audio", "文本"],
    }

    created = client.post("/api/presets", json=payload)

    assert created.status_code == 200
    preset = created.json()
    assert preset["input_mode"] == "text"
    assert preset["input_assets"] == []
    assert preset["parameters"] == payload["parameters"]
    restored = client.get(f"/api/presets/{preset['preset_id']}")
    assert restored.status_code == 200
    assert restored.json()["input_mode"] == "text"


def test_agent_can_register_voice_with_license_and_tags(tmp_path: Path):
    client = _client(tmp_path)

    resp = client.post(
        "/api/voices/register",
        data={
            "name": "外部 Agent 授权音色",
            "reference_text": "这是一段经过授权的参考声音。",
            "license_status": "authorized",
            "tags": '["agent:demo", "授权", "参考声音"]',
            "recommended_engine_id": "indextts-v2",
        },
        files={"file": ("agent.wav", b"RIFF\x24\x00\x00\x00WAVEfmt ", "audio/wav")},
    )

    assert resp.status_code == 200
    voice = resp.json()
    assert voice["name"] == "外部 Agent 授权音色"
    assert voice["license_status"] == "authorized"
    assert voice["reference_text"] == "这是一段经过授权的参考声音。"
    assert voice["reference_audio_ids"]
    stored_file = voice_store.get_file(voice["reference_audio_ids"][0])
    assert stored_file is not None
    assert Path(stored_file.path).exists()
    assert "agent:demo" in voice["tags"]
    assert any(binding["engine_id"] == "indextts-v2" and binding["available"] for binding in voice["engine_bindings"])


def test_voice_upload_returns_reference_audio_path(tmp_path: Path):
    client = _client(tmp_path)

    resp = client.post(
        "/api/voices/upload",
        files={"file": ("custom.wav", b"RIFF\x24\x00\x00\x00WAVEfmt ", "audio/wav")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["file_id"]
    assert data["filename"] == "custom.wav"
    assert data["path"]
    assert Path(data["path"]).exists()


def test_predict_file_emotion_uses_uploaded_voice_file(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)

    uploaded = client.post(
        "/api/voices/upload",
        files={"file": ("custom.wav", b"RIFF\x24\x00\x00\x00WAVEfmt ", "audio/wav")},
    ).json()

    captured: dict[str, str] = {}

    def fake_predict(path: str):
        captured["path"] = path
        return {"top_emotion": "calm", "emotion_scores": {"calm": 0.9, "happy": 0.2}}

    monkeypatch.setattr(ser_service, "predict_emotion", fake_predict)

    resp = client.post("/api/ser/predict-file", json={"file_id": uploaded["file_id"]})

    assert resp.status_code == 200
    assert captured["path"] == uploaded["path"]
    assert resp.json()["voice_id"] == uploaded["file_id"]
    assert resp.json()["top_emotion"] == "calm"
    assert resp.json()["emotion_scores"]["happy"] == 0.2


def test_builtin_presets_are_readonly(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.delete("/api/presets/idx2_default_narration")
    assert resp.status_code == 409


def test_engine_audio_diagnosis_defaults_follow_reference_emotion():
    req = EngineAudioDiagnosisRequest()
    assert req.emotion is None


def test_engine_diagnostic_audio_endpoint_serves_latest_file(tmp_path: Path):
    client = _client(tmp_path)
    diagnostics = tmp_path / "outputs" / "diagnostics"
    diagnostics.mkdir(parents=True)
    wav = diagnostics / "indextts-v2-diagnosis.wav"
    wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")

    resp = client.get("/api/engines/indextts-v2/diagnostic-audio")
    assert resp.status_code == 200
    assert resp.content.startswith(b"RIFF")

    missing = client.get("/api/engines/omnivoice/diagnostic-audio")
    assert missing.status_code == 404


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


def test_engine_registry_exposes_only_current_main_engines(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(qwen3_tts_paths, "voice_design_available", lambda: True)
    client = _client(tmp_path)
    resp = client.get("/api/engines")
    assert resp.status_code == 200
    by_id = {item["manifest"]["engine_id"]: item["manifest"] for item in resp.json()}
    assert set(by_id) == {
        "indextts-v2",
        "omnivoice",
        "emotivoice",
        "confucius4-mlx-int8",
        "qwen3-tts-mlx-0.6b",
        "f5-tts",
        "cosyvoice-sft",
        "cosyvoice-zero-shot",
        "mimo-v2.5-tts-preset",
        "mimo-v2.5-tts-voicedesign",
        "mimo-v2.5-tts-voiceclone",
        "mimo-v2.5-asr",
        "doubao-tts-preset",
        "doubao-tts-voiceclone",
        "doubao-seed-audio-1.0",
        "qwen3-asr-mlx",
        "faster-whisper-turbo",
    }
    assert "emotion_control" in by_id["indextts-v2"]["capabilities"]
    assert by_id["mimo-v2.5-tts-preset"]["engine_type"] == "cloud"
    assert by_id["doubao-seed-audio-1.0"]["input_modes"] == ["text", "audio", "image"]
    assert by_id["doubao-seed-audio-1.0"]["max_reference_audio"] == 3
    assert "mimo-v2.5-tts" not in by_id
    assert "speech_recognition" in by_id["qwen3-asr-mlx"]["capabilities"]
    assert "vad" in by_id["faster-whisper-turbo"]["capabilities"]
    assert by_id["emotivoice"]["sample_rate"] == 16000
    assert by_id["confucius4-mlx-int8"]["sample_rate"] == 22050
    assert "emotion_transfer" in by_id["confucius4-mlx-int8"]["capabilities"]
    assert any(param["key"] == "seed" for param in by_id["confucius4-mlx-int8"]["parameter_schema"])
    assert by_id["qwen3-tts-mlx-0.6b"]["sample_rate"] == 24000
    assert "preset_voice" in by_id["qwen3-tts-mlx-0.6b"]["capabilities"]
    assert "voice_design" in by_id["qwen3-tts-mlx-0.6b"]["capabilities"]
    assert "voice_clone" in by_id["qwen3-tts-mlx-0.6b"]["capabilities"]
    assert any(param["key"] == "style_instruction" for param in by_id["qwen3-tts-mlx-0.6b"]["parameter_schema"])
    assert any(param["key"] == "voice_design_prompt" for param in by_id["qwen3-tts-mlx-0.6b"]["parameter_schema"])
    assert any(param["key"] == "speed" for param in by_id["qwen3-tts-mlx-0.6b"]["parameter_schema"])
    assert any(param["key"] == "top_p" for param in by_id["qwen3-tts-mlx-0.6b"]["parameter_schema"])
    assert any(param["key"] == "top_k" for param in by_id["qwen3-tts-mlx-0.6b"]["parameter_schema"])
    assert any(param["key"] == "repetition_penalty" for param in by_id["qwen3-tts-mlx-0.6b"]["parameter_schema"])
    assert any(param["key"] == "max_tokens" for param in by_id["qwen3-tts-mlx-0.6b"]["parameter_schema"])
    assert any(param["key"] == "cfg_scale" for param in by_id["qwen3-tts-mlx-0.6b"]["parameter_schema"])
    assert any(param["key"] == "ddpm_steps" for param in by_id["qwen3-tts-mlx-0.6b"]["parameter_schema"])
    assert "preset_voice" in by_id["cosyvoice-sft"]["capabilities"]
    assert "voice_clone" in by_id["f5-tts"]["capabilities"]
    assert "voice_clone" in by_id["cosyvoice-zero-shot"]["capabilities"]


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


def test_batch_segment_parameters_are_passed_to_runner(tmp_path: Path):
    client = _client(tmp_path)
    req = batch_queue.normalize_payload(
        {
            "engine_id": "indextts-v2",
            "voice_id": "voice-1",
            "parameters": {"temperature": 0.6, "top_p": 0.7},
            "segments": [
                {
                    "segment_id": "seg-1",
                    "text": "第一段。",
                    "speed": 1.1,
                    "parameters": {"temperature": 0.42, "top_k": 12, "max_text_tokens_per_segment": 80},
                }
            ],
        }
    )
    batch = client.post(
        "/api/batches/generate",
        json={
            "engine_id": "indextts-v2",
            "voice_id": "voice-1",
            "segments": [{"segment_id": "seg-1", "text": "第一段。"}],
        },
    ).json()
    runner_segments = batch_queue._runner_segments(req, batch_queue.BatchTask(**batch), tmp_path)

    params = runner_segments[0]["parameters"]
    assert params["temperature"] == 0.42
    assert "top_p" not in params
    assert params["top_k"] == 12
    assert params["max_text_tokens_per_segment"] == 80
    assert params["speed"] == 1.1


def test_project_segments_can_store_imported_transcript_timestamps(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕项目", "description": ""}).json()
    resp = client.put(
        f"/api/projects/{project['project_id']}/segments",
        json=[
            {
                "segment_id": "seg-1",
                "index": 0,
                "text": "第一句。",
                "source_start_ms": 0,
                "source_end_ms": 1800,
                "role_id": None,
                "voice_id": None,
                "engine_id": "indextts-v2",
                "language": "zh",
                "emotion": "calm",
                "speed": 1,
                "status": "ready",
                "result_audio_id": None,
                "result_id": None,
                "error_message": None,
                "locked": False,
            }
        ],
    )
    assert resp.status_code == 200
    saved = resp.json()["segments"][0]
    assert saved["source_start_ms"] == 0
    assert saved["source_end_ms"] == 1800


def test_project_segment_parameters_override_role_defaults(tmp_path: Path, monkeypatch):
    captured = []

    async def fake_submit(req, task_type="single", project_id=None, segment_id=None):
        captured.append(req)
        return "task-1"

    monkeypatch.setattr("app.services.task_queue.submit", fake_submit)
    project = Project(
        project_id="project-1",
        name="参数项目",
        default_engine_id="indextts-v2",
        parameters={"temperature": 0.6, "top_p": 0.7, "max_text_tokens_per_segment": 120},
        roles=[
            Role(
                role_id="role-1",
                name="旁白",
                default_engine_id="indextts-v2",
                default_voice_id="voice-1",
                default_emotion="calm",
                default_speed=0.95,
                default_parameters={"temperature": 0.5, "emo_alpha": 0.4},
            )
        ],
        segments=[
            ScriptSegment(
                segment_id="seg-1",
                index=0,
                text="需要生成的段落。",
                role_id="role-1",
                parameters={"temperature": 0.33, "style_instruction": "更有故事感", "output_format": "mp3"},
            )
        ],
    )

    import asyncio

    task_ids = asyncio.run(task_queue.submit_project(project))

    assert task_ids == ["task-1"]
    assert len(captured) == 1
    req = captured[0]
    assert req.temperature == 0.33
    assert req.top_p == 0.7
    assert req.emo_alpha == 0.4
    assert req.style_instruction == "更有故事感"
    assert req.output_format == "mp3"
    assert req.voice_id == "voice-1"
    assert req.speed == 0.95


def test_project_imports_transcription_segments_and_plain_text(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "转写导入项目", "description": ""}).json()

    timed_id = "timed-asr"
    plain_id = "plain-asr"
    database.upsert(
        "transcriptions",
        timed_id,
        {
            "transcription_id": timed_id,
            "engine_id": "qwen3-asr-mlx",
            "filename": "timed.wav",
            "language": "zh",
            "text": "第一句。第二句。",
            "segments": [
                {"start_ms": 0, "end_ms": 1200, "text": "第一句。", "language": "Chinese"},
                {"start_ms": 1200, "end_ms": 2400, "text": "第二句。", "language": "Chinese"},
            ],
            "has_source_audio": True,
            "timestamp_mode": "native",
            "timestamp_source_engine_id": "qwen3-asr-mlx",
            "duration_ms": 2400,
            "size_bytes": 10,
            "usage_seconds": None,
            "provider_response_id": None,
            "created_at": "2026-06-08T00:00:00",
        },
        "created_at",
    )
    database.upsert(
        "transcriptions",
        plain_id,
        {
            "transcription_id": plain_id,
            "engine_id": "mimo-v2.5-asr",
            "filename": "plain.wav",
            "language": "auto",
            "text": "第三句。第四句！",
            "segments": [],
            "has_source_audio": True,
            "timestamp_mode": "none",
            "timestamp_source_engine_id": None,
            "duration_ms": 1800,
            "size_bytes": 10,
            "usage_seconds": None,
            "provider_response_id": None,
            "created_at": "2026-06-08T00:01:00",
        },
        "created_at",
    )

    resp = client.post(
        f"/api/projects/{project['project_id']}/transcriptions/import",
        json={"transcription_ids": [timed_id, plain_id]},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["imported_count"] == 4
    assert data["skipped_count"] == 0
    segments = data["project"]["segments"]
    assert [segment["text"] for segment in segments] == ["第一句。", "第二句。", "第三句。", "第四句！"]
    assert segments[0]["source_start_ms"] == 0
    assert segments[1]["source_end_ms"] == 2400
    assert segments[2]["source_start_ms"] is None
    assert segments[2]["language"] == "zh"
    assert [segment["index"] for segment in segments] == [0, 1, 2, 3]


def test_community_voice_pack_can_import_single_candidate_once(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)

    def fake_download(download_url: str, path: Path) -> None:
        path.write_bytes(b"RIFF" + b"\0" * 128)

    monkeypatch.setattr(community_voice_pack_store, "_download_audio", fake_download)
    monkeypatch.setattr(
        "app.services.audio_tools.probe_audio",
        lambda path: {"duration_ms": 3200, "sample_rate": 24000},
    )
    monkeypatch.setattr(
        "app.services.audio_tools.quality_metrics",
        lambda path: {
            "duration_ms": 3200,
            "sample_rate": 24000,
            "peak": 0.4,
            "rms": 0.12,
            "silence_ratio": 0.05,
            "size_bytes": Path(path).stat().st_size,
            "passed": True,
            "warnings": [],
        },
    )

    packs = client.get("/api/community-voice-packs")
    assert packs.status_code == 200
    pack = packs.json()[0]
    candidate_id = pack["candidates"][0]["candidate_id"]

    imported = client.post(
        "/api/community-voice-packs/import",
        json={"pack_id": pack["pack_id"], "candidate_ids": [candidate_id]},
    )
    assert imported.status_code == 200
    imported_candidate = next(item for item in imported.json()["candidates"] if item["candidate_id"] == candidate_id)
    assert imported_candidate["imported_voice_id"]
    assert imported.json()["imported_count"] == 1

    voices = [voice for voice in voice_store.list_voices() if f"community:{candidate_id}" in voice.tags]
    assert len(voices) == 1
    assert voices[0].license_status == "authorized"
    assert voices[0].reference_audio_ids

    reimported = client.post(
        "/api/community-voice-packs/import",
        json={"pack_id": pack["pack_id"], "candidate_ids": [candidate_id]},
    )
    assert reimported.status_code == 200
    voices_after = [voice for voice in voice_store.list_voices() if f"community:{candidate_id}" in voice.tags]
    assert len(voices_after) == 1


def test_omnivoice_reference_audio_without_text_skips_auto_asr(tmp_path: Path):
    _client(tmp_path)
    ref = tmp_path / "voices" / "ref.wav"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")

    req = GenerateRequest(
        text="测试一句。",
        engine_id="omnivoice",
        reference_audio_path=str(ref),
        ref_text=None,
    )

    kwargs = task_queue._kwargs(req, str(tmp_path / "out.wav"))

    assert kwargs["reference_audio"] == str(ref)
    assert kwargs["ref_text"] == ""


def test_f5_requires_reference_text_to_avoid_auto_asr(tmp_path: Path):
    _client(tmp_path)
    ref = tmp_path / "voices" / "ref.wav"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")

    req = GenerateRequest(
        text="测试一句。",
        engine_id="f5-tts",
        reference_audio_path=str(ref),
        ref_text=None,
    )

    try:
        task_queue._kwargs(req, str(tmp_path / "out.wav"))
    except Exception as exc:
        assert getattr(exc, 'code', None) == 'REFERENCE_TEXT_REQUIRED' or str(exc) == "REFERENCE_TEXT_REQUIRED"
    else:
        raise AssertionError("F5-TTS should require reference text")


def test_cosyvoice_zero_shot_requires_reference_text(tmp_path: Path):
    _client(tmp_path)
    ref = tmp_path / "voices" / "ref.wav"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")

    req = GenerateRequest(
        text="测试一句。",
        engine_id="cosyvoice-zero-shot",
        reference_audio_path=str(ref),
        ref_text=None,
    )

    try:
        task_queue._kwargs(req, str(tmp_path / "out.wav"))
    except Exception as exc:
        assert getattr(exc, 'code', None) == 'REFERENCE_TEXT_REQUIRED' or str(exc) == "REFERENCE_TEXT_REQUIRED"
    else:
        raise AssertionError("CosyVoice Zero-Shot should require reference text")


def test_mp3_export_fallback_returns_actual_wav_path(tmp_path: Path, monkeypatch):
    real_import = builtins.__import__

    def block_pydub(name, *args, **kwargs):
        if name == "pydub" or name.startswith("pydub."):
            raise ImportError("pydub unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_pydub)
    requested = tmp_path / "outputs" / "sample.mp3"

    actual = audio_tools.write_audio(requested, np.zeros(2205, dtype=np.float32), 22050, "mp3")

    assert actual == requested.with_suffix(".wav")
    assert actual.exists()
    assert not requested.exists()


def test_history_audio_path_falls_back_to_existing_sibling_file(tmp_path: Path):
    _client(tmp_path)
    actual = tmp_path / "outputs" / "result.wav"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    history_store.add(
        HistoryItem(
            result_id="result-with-fallback",
            task_id="task-with-fallback",
            engine_id="cosyvoice-zero-shot",
            input_text="测试一句。",
            output_audio_id="result",
            output_path=str(actual.with_suffix(".mp3")),
        )
    )

    assert history_store.audio_path("result-with-fallback") == actual
