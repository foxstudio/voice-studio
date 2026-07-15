from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import qwen_mlx_asr  # noqa: E402


def _write_model_files(path: Path, *, omit: str | None = None):
    path.mkdir()
    for name in qwen_mlx_asr.REQUIRED_MODEL_FILES:
        if name != omit:
            (path / name).write_bytes(b"model")


def test_model_health_matches_published_mlx_checkpoint_layout(monkeypatch, tmp_path: Path):
    model_path = tmp_path / "qwen3-asr-mlx"
    _write_model_files(model_path)
    monkeypatch.setattr(qwen_mlx_asr, "runtime_available", lambda: (True, None))
    monkeypatch.setattr(qwen_mlx_asr.model_integrity, "verify_model_file", lambda *args, **kwargs: (True, {"status": "sha256_verified"}))

    assert qwen_mlx_asr.model_health(model_path)["healthy"] is True
    assert "tokenizer.json" not in qwen_mlx_asr.REQUIRED_MODEL_FILES
    assert {"vocab.json", "merges.txt"} <= set(qwen_mlx_asr.REQUIRED_MODEL_FILES)


def test_model_health_rejects_incomplete_tokenizer_files(monkeypatch, tmp_path: Path):
    model_path = tmp_path / "qwen3-asr-mlx"
    _write_model_files(model_path, omit="vocab.json")
    monkeypatch.setattr(qwen_mlx_asr, "runtime_available", lambda: (True, None))

    result = qwen_mlx_asr.model_health(model_path)

    assert result["healthy"] is False
    assert "vocab.json" in result["missing"]


def _install_fake_mlx_audio(monkeypatch, output):
    calls = []

    def fake_generate_transcription(**kwargs):
        calls.append(kwargs)
        return output

    mlx_audio_module = types.ModuleType("mlx_audio")
    stt_module = types.ModuleType("mlx_audio.stt")
    generate_module = types.ModuleType("mlx_audio.stt.generate")
    generate_module.generate_transcription = fake_generate_transcription
    monkeypatch.setitem(sys.modules, "mlx_audio", mlx_audio_module)
    monkeypatch.setitem(sys.modules, "mlx_audio.stt", stt_module)
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.generate", generate_module)
    return calls


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


@pytest.mark.parametrize(
    ("requested_language", "mlx_language"),
    [
        ("en", "English"),
        ("英文", "English"),
        ("zh", "Chinese"),
        ("中文", "Chinese"),
        ("auto", None),
        ("", None),
    ],
)
def test_transcribe_audio_chunks_long_audio_and_maps_language(monkeypatch, tmp_path: Path, requested_language, mlx_language):
    class STTOutput:
        text = "Transcribed text."
        segments = []

    audio_path = tmp_path / "long.wav"
    audio_path.write_bytes(b"RIFF")
    model = object()
    monkeypatch.setattr(qwen_mlx_asr, "_load_model", lambda model_path: model)
    calls = _install_fake_mlx_audio(monkeypatch, STTOutput())

    qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path),
        language=requested_language,
        model_path="/models/qwen3-asr-mlx",
    )

    assert len(calls) == 1
    assert calls[0]["model"] is model
    assert calls[0]["audio"] == str(audio_path)
    assert calls[0]["chunk_duration"] == 30.0
    assert calls[0]["language"] == mlx_language


def test_normalize_segments_splits_long_english_sentences_by_word_count():
    segments = qwen_mlx_asr._normalize_segments(
        [{"start": 2.0, "end": 20.0, "text": "One two. Three four five six.", "language": "en"}]
    )

    assert segments == [
        {"start_ms": 2000, "end_ms": 8000, "text": "One two.", "language": "en"},
        {"start_ms": 8000, "end_ms": 20000, "text": "Three four five six.", "language": "en"},
    ]


def test_normalize_segments_splits_long_chinese_sentences_by_character_count():
    segments = qwen_mlx_asr._normalize_segments(
        [{"start": 0.0, "end": 16.0, "text": "你好。我们一起出发。", "language": "zh"}]
    )

    assert segments == [
        {"start_ms": 0, "end_ms": 4000, "text": "你好。", "language": "zh"},
        {"start_ms": 4000, "end_ms": 16000, "text": "我们一起出发。", "language": "zh"},
    ]


@pytest.mark.parametrize(
    "item",
    [
        {"start": 0.0, "end": 8.0, "text": "Short first. Short second.", "language": "en"},
        {"start": 0.0, "end": 30.0, "text": "long segment without sentence punctuation", "language": "en"},
    ],
)
def test_normalize_segments_does_not_split_short_or_unpunctuated_segments(item):
    assert qwen_mlx_asr._normalize_segments([item]) == [
        {
            "start_ms": int(item["start"] * 1000),
            "end_ms": int(item["end"] * 1000),
            "text": item["text"],
            "language": item["language"],
        }
    ]
