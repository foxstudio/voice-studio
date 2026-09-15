from __future__ import annotations

from types import SimpleNamespace

import mlx.core as mx
import numpy as np
import pytest
import torch

from mlx_indextts.generate_v2 import (
    IndexTTSv2,
    _resolve_preprocessing_artifact,
    _resolve_preprocessing_bundle,
)
from mlx_indextts.model_artifacts import (
    INDEXTTS_W2V_BERT_ARTIFACTS,
    INDEXTTS_WAV_PREPROCESSING_BY_ID,
)
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


def test_semantic_codec_initialization_fails_closed_when_weights_are_unavailable(
    monkeypatch,
    tmp_path,
):
    from mlx_indextts.indextts.utils import maskgct_utils

    model = object.__new__(IndexTTSv2)
    model.cfg = SimpleNamespace(semantic_codec=object())
    model.device = "cpu"
    model.mlx_model_dir = tmp_path
    model.model_dir = tmp_path
    fake_codec = SimpleNamespace()

    monkeypatch.setattr(maskgct_utils, "build_semantic_codec", lambda _cfg: fake_codec)
    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="semantic codec weights could not be loaded"):
        model._init_semantic_codec()

    assert model.semantic_codec is None


def test_campplus_initialization_fails_closed_when_weights_are_unavailable(
    monkeypatch,
    tmp_path,
):
    from mlx_indextts.indextts.s2mel.modules.campplus import DTDNN
    from mlx_indextts.indextts.utils import maskgct_utils

    class FakeSemanticModel:
        def to(self, _device):
            return self

    model = object.__new__(IndexTTSv2)
    model.cfg = SimpleNamespace(w2v_stat="wav2vec2bert_stats.pt")
    model.device = "cpu"
    model.mlx_model_dir = tmp_path
    model.model_dir = tmp_path
    (tmp_path / model.cfg.w2v_stat).touch()
    w2v_dir = tmp_path / "preprocessing" / "facebook-w2v-bert-2.0"
    w2v_dir.mkdir(parents=True)
    for artifact in INDEXTTS_W2V_BERT_ARTIFACTS:
        (w2v_dir / artifact.filename).write_bytes(b"fixture")

    monkeypatch.setattr(
        maskgct_utils,
        "build_semantic_model",
        lambda path_, model_name_or_path: (
            FakeSemanticModel(),
            torch.tensor(0.0),
            torch.tensor(1.0),
        ),
    )
    monkeypatch.setattr(
        "transformers.AutoFeatureExtractor.from_pretrained",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        DTDNN,
        "CAMPPlus",
        lambda *_args, **_kwargs: pytest.fail("random CAMPPlus fallback must not be created"),
    )

    with pytest.raises(RuntimeError, match="CAMPPlus weights could not be loaded"):
        model._init_pytorch_modules()

    assert model.campplus is None


def test_preprocessing_dependency_prefers_managed_model_directory(tmp_path, monkeypatch):
    managed = (
        tmp_path
        / "preprocessing"
        / "funasr-campplus"
        / "campplus_cn_common.bin"
    )
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"weights")
    monkeypatch.setattr(
        "huggingface_hub.try_to_load_from_cache",
        lambda *_args, **_kwargs: pytest.fail("managed files must win over shared cache"),
    )

    resolved = _resolve_preprocessing_artifact(
        (tmp_path,),
        INDEXTTS_WAV_PREPROCESSING_BY_ID["campplus-speaker-encoder"],
    )

    assert resolved == managed


def test_preprocessing_dependency_reuses_cache_without_downloading(tmp_path, monkeypatch):
    cached = tmp_path / "hf-cache" / "campplus_cn_common.bin"
    cached.parent.mkdir()
    cached.write_bytes(b"weights")
    captured = {}

    def cached_file(repo_id, filename, revision):
        captured.update(
            repo_id=repo_id,
            filename=filename,
            revision=revision,
        )
        return str(cached)

    monkeypatch.setattr(
        "huggingface_hub.try_to_load_from_cache",
        cached_file,
    )

    resolved = _resolve_preprocessing_artifact(
        (tmp_path / "managed",),
        INDEXTTS_WAV_PREPROCESSING_BY_ID["campplus-speaker-encoder"],
    )

    assert resolved == cached
    assert captured == {
        "repo_id": "funasr/campplus",
        "filename": "campplus_cn_common.bin",
        "revision": "e4b6ede7ce16997aff4ae69fbca1f0175e2afede",
    }


def test_preprocessing_dependency_reports_offline_install_path(tmp_path, monkeypatch):
    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", lambda *_args, **_kwargs: None)

    with pytest.raises(FileNotFoundError, match="does not download model files") as exc_info:
        _resolve_preprocessing_artifact(
            (tmp_path,),
            INDEXTTS_WAV_PREPROCESSING_BY_ID["maskgct-semantic-codec"],
        )

    assert str(tmp_path / "preprocessing" / "amphion-maskgct") in str(exc_info.value)


def test_preprocessing_bundle_does_not_mix_partial_managed_and_cached_files(
    tmp_path,
    monkeypatch,
):
    managed_config = (
        tmp_path
        / INDEXTTS_WAV_PREPROCESSING_BY_ID["w2v-bert-config"].managed_relative_path
    )
    managed_config.parent.mkdir(parents=True)
    managed_config.write_text("{}", encoding="utf-8")
    cache_dir = tmp_path / "hf-cache" / "snapshot"
    cache_dir.mkdir(parents=True)
    for artifact in INDEXTTS_W2V_BERT_ARTIFACTS:
        (cache_dir / artifact.filename).write_bytes(b"cached")
    monkeypatch.setattr(
        "huggingface_hub.try_to_load_from_cache",
        lambda _repo, filename, revision: str(cache_dir / filename),
    )

    resolved = _resolve_preprocessing_bundle(
        (tmp_path,),
        INDEXTTS_W2V_BERT_ARTIFACTS,
    )

    assert resolved == cache_dir


def test_generate_rejects_emotion_vector_and_reference_audio_together():
    model = object.__new__(IndexTTSv2)

    with pytest.raises(ValueError, match="mutually exclusive"):
        model.generate(
            text="测试",
            reference_audio="speaker.wav",
            emotion="happy",
            emotion_reference_audio="emotion.wav",
        )
