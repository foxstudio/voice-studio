from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import model_store, qwen3_tts_paths, settings_store  # noqa: E402


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=str(tmp_path / "data"),
        model_dir=str(tmp_path / "models"),
    )


def test_managed_paths_are_rooted_in_the_configured_model_directory(tmp_path):
    settings = _settings(tmp_path)

    assert model_store.managed_model_path("indextts-v2", settings, environ={}) == (
        tmp_path / "models" / "mlx-indexTTS-2.0"
    )
    assert model_store.managed_model_path("omnivoice", settings, environ={}) == (
        tmp_path / "models" / "omnivoice"
    )
    assert model_store.managed_model_path("moss-transcribe-diarize-mlx", settings, environ={}) == (
        tmp_path / "models" / "moss-transcribe-diarize-8bit"
    )
    assert model_store.managed_model_path("campplus-modelscope", settings, environ={}) == (
        tmp_path / "models" / "campplus-speaker-verifier"
    )


def test_global_model_library_environment_overrides_persisted_setting(tmp_path):
    settings = _settings(tmp_path)
    shared = tmp_path / "shared-model-library"

    assert model_store.managed_model_root(
        settings,
        environ={"VOICE_STUDIO_MODELS_DIR": str(shared)},
    ) == shared
    assert model_store.managed_model_path(
        "indextts-v2",
        settings,
        environ={"VOICE_STUDIO_MODELS_DIR": str(shared)},
    ) == shared / "mlx-indexTTS-2.0"


def test_model_specific_environment_precedes_shared_model_library(tmp_path):
    settings = _settings(tmp_path)
    specific = tmp_path / "external-indextts"
    candidates = model_store.model_candidates(
        "indextts-v2",
        settings,
        environ={
            "VOICE_STUDIO_MODELS_DIR": str(tmp_path / "shared"),
            "VOICE_STUDIO_INDEXTTS_MODEL_DIR": str(specific),
        },
    )

    assert candidates[:2] == [
        specific,
        tmp_path / "shared" / "mlx-indexTTS-2.0",
    ]


def test_settings_store_separates_managed_write_target_from_legacy_read_path(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    legacy = tmp_path / "data" / "models" / "mlx-indexTTS-2.0"
    legacy.mkdir(parents=True)
    for name in (
        "config.yaml",
        "tokenizer.model",
        "vq2emb.safetensors",
        "gpt.safetensors",
        "s2mel.safetensors",
        "bigvgan.safetensors",
    ):
        (legacy / name).write_bytes(b"model")
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", settings.data_dir)
    monkeypatch.setenv("VOICE_STUDIO_MODELS_DIR", settings.model_dir)

    assert settings_store.managed_model_path("indextts-v2") == (
        tmp_path / "models" / "mlx-indexTTS-2.0"
    )
    assert settings_store.model_path("indextts-v2") == legacy


def test_partial_managed_model_does_not_hide_complete_legacy_model(
    tmp_path,
):
    settings = _settings(tmp_path)
    managed = tmp_path / "models" / "mlx-indexTTS-2.0"
    managed.mkdir(parents=True)
    (managed / "config.yaml").write_bytes(b"partial")
    legacy = tmp_path / "data" / "models" / "mlx-indexTTS-2.0"
    legacy.mkdir(parents=True)
    for name in (
        "config.yaml",
        "tokenizer.model",
        "vq2emb.safetensors",
        "gpt.safetensors",
        "s2mel.safetensors",
        "bigvgan.safetensors",
    ):
        (legacy / name).write_bytes(b"model")

    located = model_store.locate(
        "indextts-v2",
        settings,
        environ={"VOICE_STUDIO_DATA_DIR": settings.data_dir},
    )

    assert located.path == legacy


def test_confucius_prefers_the_configured_managed_model_directory(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.delenv("VOICE_STUDIO_CONFUCIUS4_MODEL_DIR", raising=False)

    candidates = model_store.model_candidates(
        "confucius4-mlx-int8",
        settings,
        environ={},
    )

    assert candidates[0] == tmp_path / "models" / "confucius4-mlx-int8"


def test_qwen3_tts_prefers_managed_models_and_reuses_legacy_runtime_models(
    tmp_path,
):
    managed_root = tmp_path / "managed" / "qwen3-tts-mlx-0.6b"
    runtime_root = tmp_path / "runtime"
    legacy_custom = runtime_root / "models" / qwen3_tts_paths.CUSTOM_MODEL_DIR
    legacy_custom.mkdir(parents=True)

    candidates = qwen3_tts_paths.model_candidates(
        "custom",
        managed_root=managed_root,
        runtime_root=runtime_root,
    )

    assert candidates == [
        managed_root / qwen3_tts_paths.CUSTOM_MODEL_DIR,
        legacy_custom,
    ]
    assert qwen3_tts_paths.model_dir(
        "custom",
        managed_root=managed_root,
        runtime_root=runtime_root,
    ) == legacy_custom

    managed_custom = managed_root / qwen3_tts_paths.CUSTOM_MODEL_DIR
    managed_custom.mkdir(parents=True)
    assert qwen3_tts_paths.model_dir(
        "custom",
        managed_root=managed_root,
        runtime_root=runtime_root,
    ) == managed_custom


def test_invalid_engine_id_cannot_escape_the_model_root(tmp_path):
    with pytest.raises(ValueError, match="Invalid engine id"):
        model_store.managed_model_path("../../outside", _settings(tmp_path))


def test_managed_model_wins_and_external_cache_is_described_without_copying(tmp_path):
    settings = _settings(tmp_path)
    managed = tmp_path / "models" / "omnivoice"
    managed.mkdir(parents=True)
    cache = tmp_path / "hf" / "models--k2-fsa--OmniVoice" / "snapshots" / "revision"
    cache.mkdir(parents=True)
    env = {"HF_HUB_CACHE": str(tmp_path / "hf")}

    candidates = model_store.model_candidates("omnivoice", settings, environ=env)
    locations = model_store.describe_locations("omnivoice", settings, environ=env)

    assert candidates == [managed, cache]
    assert locations[0].source == "managed"
    assert locations[0].exists is True


def test_missing_external_override_falls_back_to_managed_location(tmp_path):
    settings = _settings(tmp_path)
    location = model_store.locate("qwen3-asr-mlx", settings, environ={})

    assert location.path == tmp_path / "models" / "qwen3-asr-mlx"
    assert location.managed is True
    assert location.exists is False


def test_faster_whisper_prefers_managed_directory_then_latest_cache(tmp_path):
    settings = _settings(tmp_path)
    snapshots = (
        tmp_path
        / "hf"
        / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
        / "snapshots"
    )
    older = snapshots / "older"
    newer = snapshots / "newer"
    older.mkdir(parents=True)
    newer.mkdir()
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))

    candidates = model_store.model_candidates(
        "faster-whisper-turbo",
        settings,
        environ={"HF_HUB_CACHE": str(tmp_path / "hf")},
    )

    assert candidates == [tmp_path / "models" / "faster-whisper-turbo", newer]
