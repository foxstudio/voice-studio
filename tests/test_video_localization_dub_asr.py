from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import operation_queue, source_pipeline  # noqa: E402
from app.domains.video_localization.quality_gate import evaluate_quality_gate  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings, VideoLocalizationDraft  # noqa: E402
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


def _write_clip(path: Path, value: float, duration_ms: int = 1000, sample_rate: int = 1000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.full(int(sample_rate * duration_ms / 1000), value, dtype=np.float32), sample_rate)


def _dub_draft(first_path: Path, second_path: Path) -> dict:
    return {
        "project_type": "video_localization",
        "schema_version": "v1",
        "source_media": {"filename": "localized.mp4", "duration_ms": 3500},
        "timeline_clips": [
            {
                "clip_id": "dub_001",
                "track_id": "dub",
                "start_ms": 1000,
                "end_ms": 1800,
                "source_start_ms": 200,
                "source_end_ms": 700,
                "audio_path": str(first_path),
            },
            {
                "clip_id": "dub_002",
                "track_id": "dub",
                "start_ms": 2500,
                "end_ms": 3100,
                "source_start_ms": 100,
                "source_end_ms": 900,
                "audio_path": str(second_path),
            },
        ],
    }


def test_dub_asr_renders_timeline_gaps_records_metadata_and_cleans_temp_file(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "合成轨听写", "description": ""}).json()
    first_path = tmp_path / "tts" / "first.wav"
    second_path = tmp_path / "tts" / "second.wav"
    _write_clip(first_path, 0.25)
    _write_clip(second_path, 0.5)
    client.put(f"/api/projects/{project['project_id']}/video-localization", json=_dub_draft(first_path, second_path))
    captured_path: Path | None = None
    source_events: list[str] = []
    original_fingerprint = source_pipeline.dub_asr_source_state_sha256
    original_render = source_pipeline._render_dub_asr_track

    def fingerprint_before_render(draft):
        source_events.append("fingerprint")
        return original_fingerprint(draft)

    def render_after_fingerprint(draft):
        source_events.append("render")
        return original_render(draft)

    def fake_transcribe(*, engine_id: str, audio_path: str, language: str):
        nonlocal captured_path
        captured_path = Path(audio_path)
        assert captured_path.exists()
        assert captured_path.parent != tmp_path / "exports"
        audio, sample_rate = sf.read(captured_path, dtype="float32")
        assert engine_id == "qwen3-asr-mlx"
        assert language == "zh"
        assert sample_rate == 48000
        assert len(audio) == 168000
        assert abs(float(audio[int(1.1 * sample_rate)]) - 0.25) < 0.01
        assert abs(float(audio[int(1.6 * sample_rate)])) < 0.001
        assert abs(float(audio[int(2.7 * sample_rate)]) - 0.5) < 0.01
        assert abs(float(audio[int(3.2 * sample_rate)])) < 0.001
        return {"segments": [{"start_ms": 1000, "end_ms": 3100, "text": "Localized dub", "language": "en"}]}

    monkeypatch.setattr(source_pipeline.asr_service, "transcribe", fake_transcribe)
    monkeypatch.setattr(source_pipeline.transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": False})
    monkeypatch.setattr(source_pipeline, "dub_asr_source_state_sha256", fingerprint_before_render)
    monkeypatch.setattr(source_pipeline, "_render_dub_asr_track", render_after_fingerprint)

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/asr/en",
        params={"source_track_id": "dub"},
    )

    assert response.status_code == 200
    assert source_events[:2] == ["fingerprint", "render"]
    assert response.json()["source_media"]["metadata"]["english_asr_source_track_id"] == "dub"
    assert response.json()["source_media"]["metadata"]["english_asr_source_state_sha256"]
    assert response.json()["cues"][0]["en_subtitle_text"] == "Localized dub"
    assert captured_path is not None and not captured_path.exists()

    completed = VideoLocalizationDraft.model_validate(response.json())
    moved_clips = [dict(item) for item in completed.timeline_clips]
    moved_clips[0]["start_ms"] += 100
    changed = completed.model_copy(update={"timeline_clips": moved_clips})

    assert "ASR_SOURCE_CHANGED" in {issue.code for issue in evaluate_quality_gate(changed).blockers}


def test_dub_asr_cleans_temp_file_when_transcription_fails(tmp_path: Path, monkeypatch):
    first_path = tmp_path / "first.wav"
    second_path = tmp_path / "second.wav"
    _write_clip(first_path, 0.25)
    _write_clip(second_path, 0.5)
    draft = VideoLocalizationDraft(**_dub_draft(first_path, second_path))
    captured_path: Path | None = None

    def fail_transcription(**kwargs):
        nonlocal captured_path
        captured_path = Path(kwargs["audio_path"])
        assert captured_path.exists()
        assert kwargs["language"] == "zh"
        raise RuntimeError("ASR failed")

    monkeypatch.setattr(source_pipeline.asr_service, "transcribe", fail_transcription)

    with pytest.raises(RuntimeError, match="ASR failed"):
        source_pipeline.with_english_asr(draft, source_track_id="dub")

    assert captured_path is not None and not captured_path.exists()


def test_dub_asr_queue_validation_does_not_render_temp_audio(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "队列校验", "description": ""}).json()
    first_path = tmp_path / "first.wav"
    second_path = tmp_path / "second.wav"
    _write_clip(first_path, 0.25)
    _write_clip(second_path, 0.5)
    client.put(f"/api/projects/{project['project_id']}/video-localization", json=_dub_draft(first_path, second_path))
    monkeypatch.setattr(source_pipeline, "_render_dub_asr_track", lambda draft: pytest.fail("validate 不应生成临时音频"))
    monkeypatch.setattr(operation_queue, "_enqueue", lambda operation_id: None)

    operation = operation_queue.submit(
        project["project_id"],
        "english_asr",
        {"engine_id": "faster-whisper-turbo", "source_track_id": "dub"},
    )

    assert operation is not None
    assert operation.parameters["source_track_id"] == "dub"


@pytest.mark.parametrize(
    ("timeline_clips", "error_code", "message_part"),
    [
        ([], "VIDEO_LOCALIZATION_DUB_TRACK_MISSING", "没有可听写的合成配音轨"),
        (
            [{"clip_id": "dub_missing", "track_id": "dub", "audio_path": "/missing/dub.wav"}],
            "VIDEO_LOCALIZATION_DUB_CLIP_AUDIO_NOT_FOUND",
            "音频文件不存在",
        ),
    ],
)
def test_dub_asr_validation_errors_are_chinese(tmp_path: Path, timeline_clips, error_code, message_part):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "无效合成轨", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"project_type": "video_localization", "schema_version": "v1", "timeline_clips": timeline_clips},
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations",
        json={"kind": "english_asr", "parameters": {"source_track_id": "dub"}},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == error_code
    assert message_part in response.json()["error"]["message"]


def test_dub_asr_rejects_unreadable_audio_with_chinese_error(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "损坏合成轨", "description": ""}).json()
    broken_audio = tmp_path / "broken.wav"
    broken_audio.write_bytes(b"not-a-wav")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{"clip_id": "dub_broken", "track_id": "dub", "audio_path": str(broken_audio)}],
        },
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations",
        json={"kind": "english_asr", "parameters": {"source_track_id": "dub"}},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_DUB_CLIP_AUDIO_INVALID"
    assert "音频文件无法读取" in response.json()["error"]["message"]
