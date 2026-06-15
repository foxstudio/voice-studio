from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import faster_whisper_asr  # noqa: E402


def test_model_health_reports_missing_local_ct2_model(tmp_path: Path):
    health = faster_whisper_asr.model_health(tmp_path / "missing")

    assert health["healthy"] is False
    assert health["status"] == "model_missing"
    assert health["model_id"] == "dropbox-dash/faster-whisper-large-v3-turbo"
    assert health["original_model_id"] == "openai/whisper-large-v3-turbo"
    assert health["missing"] == ["model_dir"]


def test_model_health_accepts_existing_ct2_model_without_importing_runtime(tmp_path: Path, monkeypatch):
    model_dir = tmp_path / "faster-whisper-turbo"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.bin").write_bytes(b"model")
    (model_dir / "vocabulary.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(faster_whisper_asr, "runtime_available", lambda: (True, None))

    health = faster_whisper_asr.model_health(model_dir)

    assert health["healthy"] is True
    assert health["status"] == "ready"
    assert health["runtime"] == "faster-whisper"


def test_transcribe_audio_normalizes_segments(monkeypatch, tmp_path: Path):
    class Segment:
        def __init__(self, start: float, end: float, text: str):
            self.start = start
            self.end = end
            self.text = text

    class Info:
        language = "en"

    class Model:
        def transcribe(self, audio_path: str, **kwargs):
            assert audio_path.endswith("clip.wav")
            assert kwargs["language"] == "en"
            assert kwargs["beam_size"] == 1
            assert kwargs["vad_filter"] is True
            return iter([Segment(0, 1.25, " Hello "), Segment(1.25, 2.5, "world.")]), Info()

    audio_path = tmp_path / "clip.wav"
    audio_path.write_bytes(b"RIFF")
    monkeypatch.setattr(faster_whisper_asr, "_load_model", lambda model_path: Model())

    result = faster_whisper_asr.transcribe_audio(audio_path=str(audio_path), language="en", model_path="/models/faster-whisper-turbo")

    assert result["text"] == "Hello world."
    assert result["segments"] == [
        {"start_ms": 0, "end_ms": 1250, "text": "Hello", "language": "en"},
        {"start_ms": 1250, "end_ms": 2500, "text": "world.", "language": "en"},
    ]
