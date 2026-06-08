from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.models.schemas import AppSettings, EngineAudioDiagnosisRequest, Project, Role, ScriptSegment  # noqa: E402
from app.services import batch_queue, community_voice_pack_store, database, settings_store, task_queue, voice_aliases, voice_store  # noqa: E402


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
    assert default["name"] == "贴近参考音色"
    assert default["parameters"]["emotion"] is None
    assert default["parameters"]["emo_alpha"] == 0.0
    assert default["parameters"]["temperature"] == 0.8


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


def test_voice_seed_audio_preview_redirects(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/voice-seeds/index_voice_01/audio", follow_redirects=False)
    assert resp.status_code in {302, 307}
    assert "voice_01.wav" in resp.headers["location"]


def test_community_candidate_audio_preview_uses_resolved_url(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    monkeypatch.setattr(
        community_voice_pack_store,
        "candidate_preview_url",
        lambda pack_id, candidate_id: "https://example.test/sample.wav",
    )

    resp = client.get(
        "/api/community-voice-packs/csemotions_character_samples/candidates/csemotions_1200/audio",
        follow_redirects=False,
    )
    assert resp.status_code in {302, 307}
    assert resp.headers["location"] == "https://example.test/sample.wav"


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
        "qwen3-asr-mlx",
    }
    assert "emotion_control" in by_id["indextts-v2"]["capabilities"]
    assert by_id["mimo-v2.5-tts-preset"]["engine_type"] == "cloud"
    assert "mimo-v2.5-tts" not in by_id
    assert "speech_recognition" in by_id["qwen3-asr-mlx"]["capabilities"]


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
