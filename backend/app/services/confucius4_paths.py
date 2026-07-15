from __future__ import annotations

import os
from pathlib import Path

from app.services.paths import PROJECT_ROOT, expand_path

ENGINE_ID = "confucius4-mlx-int8"
MODEL_ENV = "VOICE_STUDIO_CONFUCIUS4_MODEL_DIR"
RUNTIME_ENV = "VOICE_STUDIO_CONFUCIUS4_MLX_AUDIO_ROOT"
DEFAULT_DATA_ROOT = Path(os.environ.get("VOICE_STUDIO_DATA_DIR", "~/VoiceStudio")).expanduser()
DEFAULT_MODEL_DIR = DEFAULT_DATA_ROOT / "models" / ENGINE_ID
DEFAULT_RUNTIME_ROOT = DEFAULT_DATA_ROOT / "engines" / "mlx-audio-confucius4"

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

# This is the language-token map shipped in the pinned local MLX runtime.
# Keep the UI and request validation tied to it: unsupported values used to
# silently fall back to an English prompt inside the runtime.
SUPPORTED_LANGUAGE_CODES = ("zh", "en", "vi", "ja", "ko", "th")


def require_supported_language(value: str | None) -> str:
    language = str(value or "zh").strip().lower()
    if language not in SUPPORTED_LANGUAGE_CODES:
        raise ValueError(
            "CONFUCIUS4_LANGUAGE_UNSUPPORTED: 当前本机 Confucius4 MLX 仅支持中文、英文、越南语、日语、韩语、泰语"
        )
    return language


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
