from __future__ import annotations

import os
from pathlib import Path

from app.services.paths import PROJECT_ROOT, expand_path

ENGINE_ID = "confucius4-mlx-int8"
MODEL_ENV = "VOICE_STUDIO_CONFUCIUS4_MODEL_DIR"
RUNTIME_ENV = "VOICE_STUDIO_CONFUCIUS4_MLX_AUDIO_ROOT"
DEFAULT_MODEL_DIR = Path("/Users/foxmacstudio/VoiceStudio/models/confucius4-mlx-int8")
DEFAULT_RUNTIME_ROOT = Path("/Users/foxmacstudio/VoiceStudio/engines/mlx-audio-confucius4")

REQUIRED_MODEL_FILES = [
    "config.json",
    "t2s_model.safetensors",
    "s2a_mlx.safetensors",
    "w2vbert_mlx.safetensors",
    "bigvgan_mlx.safetensors",
    "campplus.safetensors",
    "w2v_stats.npz",
    "fbank_filters.npz",
    "checkpoints/tokenizer.json",
]
REQUIRED_RUNTIME_FILES = [
    "mlx_audio/tts/models/confucius4/confucius4.py",
    "mlx_audio/tts/utils.py",
]


def model_candidates(settings_base: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    env_value = os.environ.get(MODEL_ENV)
    if env_value:
        candidates.append(Path(env_value).expanduser())
    candidates.append(DEFAULT_MODEL_DIR)
    if settings_base is not None:
        candidates.append(settings_base / ENGINE_ID)
    candidates.append(expand_path("models", PROJECT_ROOT) / ENGINE_ID)
    return _dedupe(candidates)


def model_dir(model_dir: str | Path | None = None) -> Path:
    if model_dir:
        return Path(model_dir).expanduser()
    for candidate in model_candidates():
        if candidate.exists():
            return candidate
    return model_candidates()[0]


def runtime_root() -> Path:
    env_value = os.environ.get(RUNTIME_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_RUNTIME_ROOT


def missing_model_files(path: Path) -> list[str]:
    return [name for name in REQUIRED_MODEL_FILES if not (path / name).exists()]


def missing_runtime_files(path: Path) -> list[str]:
    return [name for name in REQUIRED_RUNTIME_FILES if not (path / name).exists()]


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        result.append(path.expanduser())
    return result
