from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import qwen_mlx_asr  # noqa: E402


@pytest.mark.parametrize('tail_has_audio', [False, True])
def test_chunking_keeps_short_final_remainder_with_previous_audio(tmp_path, tail_has_audio):
    sample_rate = 16000
    audio = np.full(round(32.4 * sample_rate), 0.1, dtype=np.float32)
    audio[32 * sample_rate:] = 0
    if tail_has_audio:
        audio[round(32.2 * sample_rate):] = 0.1
    path = tmp_path / 'short-tail.wav'
    sf.write(path, audio, sample_rate)
    with sf.SoundFile(path) as source:
        ranges = qwen_mlx_asr._soundfile_chunk_ranges(source)
    assert ranges == [(0, len(audio))]


def _write_model_files(path: Path, *, omit: str | None = None):
    path.mkdir()
    for name in qwen_mlx_asr.REQUIRED_MODEL_FILES:
        if name != omit:
            (path / name).write_bytes(b"model")


def test_model_health_matches_published_mlx_checkpoint_layout(monkeypatch, tmp_path: Path):
    model_path = tmp_path / "qwen3-asr-mlx"
    _write_model_files(model_path)
    monkeypatch.setattr(qwen_mlx_asr, "runtime_available", lambda: (True, None))
    monkeypatch.setattr(
        qwen_mlx_asr.model_integrity, "verify_model_file", lambda *args, **kwargs: (True, {"status": "sha256_verified"})
    )

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

    result = qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path), language="en", model_path="/models/qwen3-asr-mlx"
    )

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

    result = qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path), language="en", model_path="/models/qwen3-asr-mlx"
    )

    assert result["text"] == "Hello from Qwen."
    assert result["segments"] == [
        {"start_ms": 0, "end_ms": 900, "text": "Hello", "language": "en"},
        {"start_ms": 900, "end_ms": 2000, "text": "from Qwen.", "language": "en"},
    ]


def test_transcribe_audio_passes_context_terms_as_one_short_plain_prompt(
    monkeypatch,
    tmp_path: Path,
):
    class STTOutput:
        text = "Seedance 2.0"
        segments = [
            {
                "text": "Seedance 2.0",
                "language": "en",
                "start": 0.0,
                "end": 1.0,
            }
        ]

    audio_path = tmp_path / "clip.wav"
    audio_path.write_bytes(b"RIFF")
    monkeypatch.setattr(qwen_mlx_asr, "_load_model", lambda model_path: object())
    calls = _install_fake_mlx_audio(monkeypatch, STTOutput())

    qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path),
        language="en",
        model_path="/models/qwen3-asr-mlx",
        context_terms=("Seedance 2.0", "Dreamina"),
    )

    assert calls[0]["system_prompt"] == (
        "Context terms; use these spellings only when supported by the audio: Seedance 2.0; Dreamina"
    )


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
def test_transcribe_audio_maps_language(monkeypatch, tmp_path: Path, requested_language, mlx_language):
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
    assert calls[0]["chunk_duration"] == 120.0
    assert calls[0]["language"] == mlx_language


def test_transcribe_audio_processes_long_audio_as_independent_chunks(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "long.wav"
    sample_rate = 1000
    sf.write(audio_path, np.full(sample_rate * 65, 0.1, dtype=np.float32), sample_rate)
    model = object()
    monkeypatch.setattr(qwen_mlx_asr, "_load_model", lambda model_path: model)
    calls = []

    def fake_generate_transcription(**kwargs):
        calls.append(kwargs)
        duration = sf.info(kwargs["audio"]).duration
        index = len(calls)
        return types.SimpleNamespace(
            text=f"Chunk {index}.",
            segments=[{"text": f"Chunk {index}.", "language": "en", "start": 0.0, "end": duration}],
        )

    generate_module = types.ModuleType("mlx_audio.stt.generate")
    generate_module.generate_transcription = fake_generate_transcription
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.generate", generate_module)

    result = qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path),
        language="en",
        model_path="/models/qwen3-asr-mlx",
    )

    assert len(calls) == 3
    assert all(call["model"] is model for call in calls)
    assert all(call["chunk_duration"] == 120.0 for call in calls)
    assert all(call["max_tokens"] == qwen_mlx_asr.MAX_CHUNK_GENERATION_TOKENS for call in calls)
    assert all(call["repetition_penalty"] == qwen_mlx_asr.CHUNK_REPETITION_PENALTY for call in calls)
    assert result["text"] == "Chunk 1. Chunk 2. Chunk 3."
    assert [item["text"] for item in result["segments"]] == ["Chunk 1.", "Chunk 2.", "Chunk 3."]
    assert result["segments"][0]["start_ms"] == 0
    assert result["segments"][-1]["end_ms"] == 65000
    assert all(left["end_ms"] == right["start_ms"] for left, right in zip(result["segments"], result["segments"][1:]))


def test_transcribe_audio_retries_an_empty_long_audio_chunk(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "long.wav"
    sample_rate = 1000
    sf.write(audio_path, np.full(sample_rate * 50, 0.1, dtype=np.float32), sample_rate)
    monkeypatch.setattr(qwen_mlx_asr, "_load_model", lambda model_path: object())
    calls = []

    def fake_generate_transcription(**kwargs):
        calls.append(kwargs)
        text = "" if len(calls) == 1 else f"Recovered {len(calls)}."
        duration = sf.info(kwargs["audio"]).duration
        segments = [{"text": text, "start": 0.0, "end": duration}] if text else []
        return types.SimpleNamespace(text=text, segments=segments)

    generate_module = types.ModuleType("mlx_audio.stt.generate")
    generate_module.generate_transcription = fake_generate_transcription
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.generate", generate_module)

    result = qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path),
        language="en",
        model_path="/models/qwen3-asr-mlx",
    )

    assert len(calls) == 3
    assert result["text"] == "Recovered 2. Recovered 3."
    assert len(result["segments"]) == 2


def test_transcribe_audio_recovers_empty_auto_language_chunks_with_english(
    monkeypatch,
    tmp_path: Path,
):
    audio_path = tmp_path / "long.wav"
    sample_rate = 1000
    sf.write(
        audio_path,
        np.full(sample_rate * 50, 0.1, dtype=np.float32),
        sample_rate,
    )
    monkeypatch.setattr(
        qwen_mlx_asr,
        "_load_model",
        lambda model_path: object(),
    )
    calls = []

    def fake_generate_transcription(**kwargs):
        calls.append(kwargs)
        duration = sf.info(kwargs["audio"]).duration
        text = "Recovered English." if kwargs["language"] == "English" else ""
        return types.SimpleNamespace(
            text=text,
            segments=([{"text": text, "start": 0.0, "end": duration}] if text else []),
        )

    generate_module = types.ModuleType("mlx_audio.stt.generate")
    generate_module.generate_transcription = fake_generate_transcription
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.generate", generate_module)

    result = qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path),
        language="auto",
        model_path="/models/qwen3-asr-mlx",
    )

    assert [call["language"] for call in calls] == [
        None,
        "English",
        None,
        "English",
    ]
    assert result["text"] == "Recovered English. Recovered English."
    assert result["incomplete_chunk_ranges"] == []


def test_transcribe_audio_reports_long_audio_chunks_after_retries_are_exhausted(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "long.wav"
    sample_rate = 1000
    sf.write(audio_path, np.full(sample_rate * 50, 0.1, dtype=np.float32), sample_rate)
    monkeypatch.setattr(qwen_mlx_asr, "_load_model", lambda model_path: object())
    calls = []

    def fake_generate_transcription(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(text="", segments=[])

    generate_module = types.ModuleType("mlx_audio.stt.generate")
    generate_module.generate_transcription = fake_generate_transcription
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.generate", generate_module)

    result = qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path),
        language="en",
        model_path="/models/qwen3-asr-mlx",
    )

    assert len(calls) == 4
    incomplete = result["incomplete_chunk_ranges"]
    assert len(incomplete) == 2
    assert incomplete[0]["start_ms"] == 0
    assert incomplete[0]["end_ms"] == incomplete[1]["start_ms"]
    assert incomplete[1]["end_ms"] == 50000
    assert {item["reason"] for item in incomplete} == {"missing_text"}


def test_transcribe_audio_rejects_runaway_decoder_output(
    monkeypatch,
    tmp_path: Path,
):
    audio_path = tmp_path / "long.wav"
    sample_rate = 1000
    sf.write(
        audio_path,
        np.full(sample_rate * 50, 0.1, dtype=np.float32),
        sample_rate,
    )
    monkeypatch.setattr(
        qwen_mlx_asr,
        "_load_model",
        lambda model_path: object(),
    )

    def fake_generate_transcription(**kwargs):
        duration = sf.info(kwargs["audio"]).duration
        text = "Hands up! " * 3000
        return types.SimpleNamespace(
            text=text,
            segments=[{"text": text, "start": 0.0, "end": duration}],
        )

    generate_module = types.ModuleType("mlx_audio.stt.generate")
    generate_module.generate_transcription = fake_generate_transcription
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.generate", generate_module)

    result = qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path),
        language="auto",
        model_path="/models/qwen3-asr-mlx",
    )

    assert result["text"] == ""
    assert result["segments"] == []
    assert len(result["incomplete_chunk_ranges"]) >= 2
    assert {item["reason"] for item in result["incomplete_chunk_ranges"]} == {"implausible_output"}
    assert result["incomplete_chunk_ranges"][0]["start_ms"] == 0
    assert result["incomplete_chunk_ranges"][-1]["end_ms"] == 50_000
    assert all(
        left["end_ms"] == right["start_ms"]
        for left, right in zip(
            result["incomplete_chunk_ranges"],
            result["incomplete_chunk_ranges"][1:],
        )
    )


def test_transcribe_audio_recovers_runaway_chunk_by_splitting_only_that_chunk(
    monkeypatch,
    tmp_path: Path,
):
    audio_path = tmp_path / "long.wav"
    sample_rate = 1000
    sf.write(
        audio_path,
        np.full(sample_rate * 50, 0.1, dtype=np.float32),
        sample_rate,
    )
    monkeypatch.setattr(
        qwen_mlx_asr,
        "_load_model",
        lambda model_path: object(),
    )
    calls: list[float] = []

    def fake_generate_transcription(**kwargs):
        duration = sf.info(kwargs["audio"]).duration
        calls.append(duration)
        if duration > 15:
            text = "Hands up! " * 3000
        else:
            text = f"Recovered {duration:.1f}."
        return types.SimpleNamespace(
            text=text,
            segments=[
                {
                    "text": text,
                    "start": 0.0,
                    "end": duration,
                }
            ],
        )

    generate_module = types.ModuleType("mlx_audio.stt.generate")
    generate_module.generate_transcription = fake_generate_transcription
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.generate", generate_module)

    result = qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path),
        language="en",
        model_path="/models/qwen3-asr-mlx",
    )

    assert "Recovered" in result["text"]
    assert len(result["segments"]) >= 4
    assert result["incomplete_chunk_ranges"] == []
    assert any(duration > 15 for duration in calls)
    assert any(duration <= 15 for duration in calls)


def test_transcribe_audio_allows_a_silent_long_audio_chunk_to_stay_empty(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "silence.wav"
    sample_rate = 1000
    sf.write(audio_path, np.zeros(sample_rate * 50, dtype=np.float32), sample_rate)
    monkeypatch.setattr(qwen_mlx_asr, "_load_model", lambda model_path: object())

    def fake_generate_transcription(**kwargs):
        return types.SimpleNamespace(text="", segments=[])

    generate_module = types.ModuleType("mlx_audio.stt.generate")
    generate_module.generate_transcription = fake_generate_transcription
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.generate", generate_module)

    result = qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path),
        language="en",
        model_path="/models/qwen3-asr-mlx",
    )

    assert result["text"] == ""
    assert result["segments"] == []
    assert len(result["incomplete_chunk_ranges"]) == 2


def test_transcribe_audio_reports_long_audio_text_without_native_timestamps(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "long.wav"
    sample_rate = 1000
    sf.write(audio_path, np.full(sample_rate * 50, 0.1, dtype=np.float32), sample_rate)
    monkeypatch.setattr(qwen_mlx_asr, "_load_model", lambda model_path: object())

    def fake_generate_transcription(**kwargs):
        return types.SimpleNamespace(text="Only a little text.", segments=[])

    generate_module = types.ModuleType("mlx_audio.stt.generate")
    generate_module.generate_transcription = fake_generate_transcription
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.generate", generate_module)

    result = qwen_mlx_asr.transcribe_audio(
        audio_path=str(audio_path),
        language="en",
        model_path="/models/qwen3-asr-mlx",
    )

    assert result["segments"] == []
    assert {item["reason"] for item in result["incomplete_chunk_ranges"]} == {"missing_valid_timestamps"}


def test_validated_chunk_segments_reject_invalid_native_timestamps():
    result = qwen_mlx_asr._validated_chunk_segments(
        [
            {"text": "", "start": 0.0, "end": 1.0},
            {"text": "zero", "start": 1.0, "end": 1.0},
            {"text": "nan", "start": 0.0, "end": float("nan")},
            {"text": "outside", "start": 40.0, "end": 41.0},
            {"text": "valid", "start": -0.1, "end": 2.0},
        ],
        chunk_duration=30.0,
    )

    assert result == [{"text": "valid", "start": 0.0, "end": 2.0}]


def test_normalize_segments_keeps_long_english_native_range_unchanged():
    segments = qwen_mlx_asr._normalize_segments(
        [{"start": 2.0, "end": 20.0, "text": "One two. Three four five six.", "language": "en"}]
    )

    assert segments == [
        {
            "start_ms": 2000,
            "end_ms": 20000,
            "text": "One two. Three four five six.",
            "language": "en",
        },
    ]


def test_normalize_segments_never_invents_chinese_sentence_times():
    segments = qwen_mlx_asr._normalize_segments(
        [{"start": 0.0, "end": 16.0, "text": "你好。我们一起出发。", "language": "zh"}]
    )

    assert segments == [
        {
            "start_ms": 0,
            "end_ms": 16000,
            "text": "你好。我们一起出发。",
            "language": "zh",
        },
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
