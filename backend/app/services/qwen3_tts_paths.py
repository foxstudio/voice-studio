from __future__ import annotations

import os
from pathlib import Path

from app.services import engine_runtime_paths

ENGINE_ID = "qwen3-tts-mlx-0.6b"

DEFAULT_ROOT = engine_runtime_paths.managed_engine_root(ENGINE_ID)
CUSTOM_MODEL_DIR = "Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
BASE_MODEL_DIR = "Qwen3-TTS-12Hz-0.6B-Base-8bit"
VOICE_DESIGN_MODEL_DIR = "Qwen3-TTS-12Hz-0.6B-VoiceDesign-8bit"

REQUIRED_ROOT_FILES = [
    ".venv/bin/python",
    "main.py",
]

REQUIRED_MODEL_FILES = [
    "model.safetensors",
    "config.json",
    "generation_config.json",
    "speech_tokenizer/model.safetensors",
    "speech_tokenizer/config.json",
]


def root() -> Path:
    return engine_runtime_paths.resolve_engine_root(ENGINE_ID, require_existing=False)


def model_dir(kind: str = "custom") -> Path:
    folder = {"base": BASE_MODEL_DIR, "design": VOICE_DESIGN_MODEL_DIR}.get(kind, CUSTOM_MODEL_DIR)
    return root() / "models" / folder


def missing_files() -> list[str]:
    base = root()
    missing = [item for item in REQUIRED_ROOT_FILES if not (base / item).exists()]
    for kind, label in [("custom", CUSTOM_MODEL_DIR), ("base", BASE_MODEL_DIR), ("design", VOICE_DESIGN_MODEL_DIR)]:
        model = model_dir(kind)
        missing.extend(f"models/{label}/{item}" for item in REQUIRED_MODEL_FILES if not (model / item).exists())
    return missing


def missing_required_files() -> list[str]:
    base = root()
    missing = [item for item in REQUIRED_ROOT_FILES if not (base / item).exists()]
    for kind, label in [("custom", CUSTOM_MODEL_DIR), ("base", BASE_MODEL_DIR)]:
        model = model_dir(kind)
        missing.extend(f"models/{label}/{item}" for item in REQUIRED_MODEL_FILES if not (model / item).exists())
    return missing


def missing_optional_files() -> list[str]:
    model = model_dir("design")
    return [f"models/{VOICE_DESIGN_MODEL_DIR}/{item}" for item in REQUIRED_MODEL_FILES if not (model / item).exists()]


def voice_design_available() -> bool:
    return not missing_optional_files()
