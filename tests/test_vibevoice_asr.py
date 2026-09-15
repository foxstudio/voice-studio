from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import soundfile as sf

from app.services import vibevoice_asr


class _FakeVibeVoiceModel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, audio_path: str, **_kwargs):
        self.calls.append(audio_path)
        return SimpleNamespace(
            text="structured output",
            total_time=1.25,
            segments=[
                {
                    "start": 6.0,
                    "end": 7.0,
                    "speaker_id": 0,
                    "text": "hello world",
                }
            ],
        )


def test_long_audio_is_chunked_once_for_joint_text_and_speakers(
    tmp_path,
    monkeypatch,
) -> None:
    audio_path = tmp_path / "long.wav"
    sf.write(audio_path, np.zeros(310 * 100, dtype=np.float32), 100)
    fake_model = _FakeVibeVoiceModel()
    monkeypatch.setattr(
        vibevoice_asr,
        "model_health",
        lambda _provider_id: {"healthy": True},
    )
    monkeypatch.setattr(
        vibevoice_asr.vibevoice_model,
        "require_variant",
        lambda _variant: tmp_path,
    )
    monkeypatch.setattr(vibevoice_asr, "_load_model", lambda _path: fake_model)

    asr, diarization = vibevoice_asr.transcribe_and_diarize(
        provider_id="vibevoice-asr-mlx-4bit",
        audio_path=str(audio_path),
        language="en",
        hotwords=("Ray Dalio",),
    )

    assert len(fake_model.calls) == 2
    assert [(item.start_ms, item.end_ms) for item in asr.segments] == [
        (6_000, 7_000),
        (301_000, 302_000),
    ]
    assert [item.speaker_cluster for item in diarization.segments] == [
        "chunk_001:0",
        "chunk_002:0",
    ]
    assert asr.metadata["incomplete_chunk_ranges"] == []
    assert asr.metadata["usage_seconds"] == 2.5
