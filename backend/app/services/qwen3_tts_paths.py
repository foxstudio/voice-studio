from __future__ import annotations

import os
from pathlib import Path

from app.services import engine_runtime_paths
from app.services.python_runtime import engine_virtualenv_python

ENGINE_ID = "qwen3-tts-mlx-0.6b"
MODEL_ROOT_ENV = "VOICE_STUDIO_QWEN3_TTS_MODELS_DIR"

# Keep the API request builders, visible manifest, and runtime fallback on one
# contract. GenerateRequest is shared by several engines and therefore cannot
# carry Qwen-specific defaults itself.
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.9
DEFAULT_TOP_K = 50
DEFAULT_REPETITION_PENALTY = 1.1
GENERATION_DEFAULTS = {
    "temperature": DEFAULT_TEMPERATURE,
    "top_p": DEFAULT_TOP_P,
    "top_k": DEFAULT_TOP_K,
    "repetition_penalty": DEFAULT_REPETITION_PENALTY,
}

CUSTOM_MODEL_DIR = "Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
BASE_MODEL_DIR = "Qwen3-TTS-12Hz-0.6B-Base-8bit"
VOICE_DESIGN_MODEL_DIR = "Qwen3-TTS-12Hz-0.6B-VoiceDesign-8bit"

REQUIRED_MODEL_FILES = [
    "model.safetensors",
    "config.json",
    "generation_config.json",
    "speech_tokenizer/model.safetensors",
    "speech_tokenizer/config.json",
]


def root() -> Path:
    return engine_runtime_paths.resolve_engine_root(ENGINE_ID, require_existing=False)


def managed_model_root() -> Path:
    if configured := os.environ.get(MODEL_ROOT_ENV):
        return Path(configured).expanduser()
    from app.services import model_store, settings_store

    settings = settings_store.get()
    if getattr(settings, "model_dir", None):
        return model_store.managed_model_path(ENGINE_ID, settings)
    return engine_runtime_paths.data_root() / "models" / ENGINE_ID


def model_root_candidates(
    *,
    managed_root: Path | None = None,
    runtime_root: Path | None = None,
) -> list[Path]:
    preferred = managed_root or managed_model_root()
    legacy = (runtime_root or root()) / "models"
    return _dedupe([preferred, legacy])


def model_candidates(
    kind: str = "custom",
    *,
    managed_root: Path | None = None,
    runtime_root: Path | None = None,
) -> list[Path]:
    folder = _model_folder(kind)
    return [candidate / folder for candidate in model_root_candidates(
        managed_root=managed_root,
        runtime_root=runtime_root,
    )]


def model_dir(
    kind: str = "custom",
    *,
    managed_root: Path | None = None,
    runtime_root: Path | None = None,
) -> Path:
    candidates = model_candidates(
        kind,
        managed_root=managed_root,
        runtime_root=runtime_root,
    )
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def _model_folder(kind: str) -> str:
    return {
        "base": BASE_MODEL_DIR,
        "design": VOICE_DESIGN_MODEL_DIR,
    }.get(kind, CUSTOM_MODEL_DIR)


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path.expanduser())
        if key not in seen:
            seen.add(key)
            result.append(path.expanduser())
    return result


def required_root_files(*, platform: str | None = None) -> list[str]:
    python = engine_virtualenv_python(Path("."), platform=platform)
    return [python.as_posix().removeprefix("./"), "main.py"]


def missing_files() -> list[str]:
    base = root()
    missing = [item for item in required_root_files() if not (base / item).exists()]
    for kind, label in [("custom", CUSTOM_MODEL_DIR), ("base", BASE_MODEL_DIR), ("design", VOICE_DESIGN_MODEL_DIR)]:
        model = model_dir(kind)
        missing.extend(f"models/{label}/{item}" for item in REQUIRED_MODEL_FILES if not (model / item).exists())
    return missing


def missing_required_files() -> list[str]:
    base = root()
    missing = [item for item in required_root_files() if not (base / item).exists()]
    for kind, label in [("custom", CUSTOM_MODEL_DIR), ("base", BASE_MODEL_DIR)]:
        model = model_dir(kind)
        missing.extend(f"models/{label}/{item}" for item in REQUIRED_MODEL_FILES if not (model / item).exists())
    return missing


def missing_optional_files() -> list[str]:
    model = model_dir("design")
    return [f"models/{VOICE_DESIGN_MODEL_DIR}/{item}" for item in REQUIRED_MODEL_FILES if not (model / item).exists()]


def voice_design_available() -> bool:
    return not missing_optional_files()
