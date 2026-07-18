from __future__ import annotations

from types import SimpleNamespace

import mlx.core as mx
import numpy as np
import pytest
import torch

from mlx_indextts.generate_v2 import IndexTTSv2
from mlx_indextts.models.gpt_v2 import UnifiedVoiceV2


def _as_numpy(value: mx.array) -> np.ndarray:
    mx.eval(value)
    return np.asarray(value)


@pytest.mark.parametrize(
    ("alpha", "expected"),
    [
        (-1.0, [1.0, 3.0]),
        (0.0, [1.0, 3.0]),
        (0.25, [2.0, 4.0]),
        (1.0, [5.0, 7.0]),
        (2.0, [5.0, 7.0]),
    ],
)
def test_merge_emovec_uses_official_interpolation_and_clamps_alpha(alpha, expected):
    base = mx.array([[1.0, 3.0]])
    target = mx.array([[5.0, 7.0]])
    fake_model = SimpleNamespace(get_emovec=lambda value, _lengths=None: value)

    merged = UnifiedVoiceV2.merge_emovec(fake_model, base, target, alpha=alpha)

    np.testing.assert_allclose(_as_numpy(merged), np.asarray([expected]), rtol=0, atol=1e-6)


def test_emotion_audio_processing_has_an_independent_semantic_only_cache(monkeypatch):
    from mlx_indextts import generate_v2

    model = object.__new__(IndexTTSv2)
    model.device = "cpu"
    model.cache = {"audio_path": "speaker.wav", "speaker_marker": object()}
    model.emotion_cache = {}
    ensure_calls = 0
    embedding_calls = 0

    def ensure_preprocessing():
        nonlocal ensure_calls
        ensure_calls += 1

    def semantic_embedding(_audio):
        nonlocal embedding_calls
        embedding_calls += 1
        return torch.full((1, 4, 3), float(embedding_calls))

    class IdentityResample:
        def __init__(self, _source_rate, _target_rate):
            pass

        def __call__(self, audio):
            return audio

    model._ensure_pytorch_modules = ensure_preprocessing
    model._get_semantic_embedding = semantic_embedding
    monkeypatch.setattr(generate_v2.librosa, "load", lambda _path, sr=None: (np.ones(160, dtype=np.float32), 16000))
    monkeypatch.setattr(generate_v2.torchaudio.transforms, "Resample", IdentityResample)

    first = model._process_emotion_audio("emotion-a.wav")
    second = model._process_emotion_audio("emotion-a.wav")

    assert first is second
    assert embedding_calls == 1
    assert ensure_calls == 1
    assert model.cache["audio_path"] == "speaker.wav"
    assert "speaker_marker" in model.cache
    assert set(model.emotion_cache) == {"audio_path", "spk_cond_emb"}

    third = model._process_emotion_audio("emotion-b.wav")
    assert third is not first
    assert embedding_calls == 2
    assert ensure_calls == 2
    assert model.emotion_cache["audio_path"] == "emotion-b.wav"
    assert model.cache["audio_path"] == "speaker.wav"


def test_same_emotion_and_speaker_path_reuses_speaker_embedding():
    model = object.__new__(IndexTTSv2)
    speaker_embedding = torch.ones((1, 4, 3))
    model._process_emotion_audio = lambda _path: pytest.fail("same path must not be processed twice")

    result = model._emotion_embedding_for_reference(
        "same.wav",
        "same.wav",
        {"spk_cond_emb": speaker_embedding},
    )

    assert result is speaker_embedding


def test_generate_rejects_emotion_vector_and_reference_audio_together():
    model = object.__new__(IndexTTSv2)

    with pytest.raises(ValueError, match="mutually exclusive"):
        model.generate(
            text="测试",
            reference_audio="speaker.wav",
            emotion="happy",
            emotion_reference_audio="emotion.wav",
        )
