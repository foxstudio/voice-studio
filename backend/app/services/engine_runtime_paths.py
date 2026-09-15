from __future__ import annotations

import os
from pathlib import Path

from app.services.paths import PROJECT_ROOT


ENGINE_LAYOUT = {
    "emotivoice": ("VOICE_STUDIO_EMOTIVOICE_ROOT", "emotivoice", "EmotiVoice"),
    "f5-tts": ("VOICE_STUDIO_F5_TTS_ROOT", "f5-tts", "F5-TTS"),
    "cosyvoice-sft": ("VOICE_STUDIO_COSYVOICE_ROOT", "cosyvoice", "CosyVoice"),
    "cosyvoice-zero-shot": ("VOICE_STUDIO_COSYVOICE_ROOT", "cosyvoice", "CosyVoice"),
    "qwen3-tts-mlx-0.6b": (
        "VOICE_STUDIO_QWEN3_TTS_ROOT",
        "qwen3-tts-mlx",
        "qwen3-tts-apple-silicon",
    ),
}


def data_root() -> Path:
    return Path(os.environ.get("VOICE_STUDIO_DATA_DIR", "~/VoiceStudio")).expanduser()


def managed_engine_root(engine_id: str) -> Path:
    _, managed_name, _ = ENGINE_LAYOUT[engine_id]
    return data_root() / "engines" / managed_name


def engine_root_candidates(engine_id: str) -> list[Path]:
    env_name, _, sibling_name = ENGINE_LAYOUT[engine_id]
    candidates: list[Path] = []
    if value := os.environ.get(env_name):
        candidates.append(Path(value).expanduser())
    candidates.extend(
        [
            managed_engine_root(engine_id),
            PROJECT_ROOT.parent / "tts-engine-lab" / sibling_name,
        ]
    )
    return _dedupe(candidates)


def resolve_engine_root(engine_id: str, *, require_existing: bool = True) -> Path:
    candidates = engine_root_candidates(engine_id)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if require_existing:
        env_name = ENGINE_LAYOUT[engine_id][0]
        raise RuntimeError(
            f"{engine_id} runtime is not installed. Put it at {managed_engine_root(engine_id)} "
            f"or set {env_name}."
        )
    return candidates[0]


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path.expanduser())
        if key not in seen:
            seen.add(key)
            result.append(path.expanduser())
    return result
