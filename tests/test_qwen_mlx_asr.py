from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import qwen_mlx_asr  # noqa: E402


def _install_fake_mlx_audio(monkeypatch, output):
    mlx_audio_module = types.ModuleType("mlx_audio")
    stt_module = types.ModuleType("mlx_audio.stt")
    generate_module = types.ModuleType("mlx_audio.stt.generate")
    generate_module.generate_transcription = lambda **kwargs: output
    monkeypatch.setitem(sys.modules, "mlx_audio", mlx_audio_module)
    monkeypatch.setitem(sys.modules, "mlx_audio.stt", stt_module)
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.generate", generate_module)


def test_transcribe_audio_does_not_leak_object_repr_when_text_is_empty(monkeypatch, tmp_path: Path):
    class STTOutput:
        text = ""
        segments = [{"text": "", "language": "None", "start": 0.0, "end": 2.0}]

        def __str__(self) -> str:
            return "STTOutput(text='', segments=[...])"

    audio_path = tmp_path / "clip.wav"
    audio_path.write_bytes(b"RIFF")
    monkeypatch.setattr(qwen_mlx_asr, "_load_model", lambda model_path: object())
    _install_fake_mlx_audio(monkeypatch, STTOutput())

    result = qwen_mlx_asr.transcribe_audio(audio_path=str(audio_path), language="en", model_path="/models/qwen3-asr-mlx")

    assert result["text"] == ""
    assert result["segments"] == []


def test_transcribe_audio_uses_text_and_non_empty_segments(monkeypatch, tmp_path: Path):
    class STTOutput:
        text = "Hello from Qwen."
        segments = [
            {"text": " Hello ", "language": "en", "start": 0.0, "end": 0.9},
            {"text": "from Qwen.", "language": "en", "start": 0.9, "end": 2.0},
            {"text": "   ", "language": "en", "start": 2.0, "end": 2.2},
        ]

    audio_path = tmp_path / "clip.wav"
    audio_path.write_bytes(b"RIFF")
    monkeypatch.setattr(qwen_mlx_asr, "_load_model", lambda model_path: object())
    _install_fake_mlx_audio(monkeypatch, STTOutput())

    result = qwen_mlx_asr.transcribe_audio(audio_path=str(audio_path), language="en", model_path="/models/qwen3-asr-mlx")

    assert result["text"] == "Hello from Qwen."
    assert result["segments"] == [
        {"start_ms": 0, "end_ms": 900, "text": "Hello", "language": "en"},
        {"start_ms": 900, "end_ms": 2000, "text": "from Qwen.", "language": "en"},
    ]
