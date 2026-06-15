from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path
from typing import Any


ENGINE_ID = "faster-whisper-turbo"
RUNTIME_PACKAGE = "faster-whisper"
ORIGINAL_MODEL_ID = "openai/whisper-large-v3-turbo"
DEFAULT_CT2_MODEL_ID = "dropbox-dash/faster-whisper-large-v3-turbo"

REQUIRED_MODEL_FILES = ["config.json", "model.bin"]
TOKENIZER_FILES = ["tokenizer.json", "vocabulary.json"]


def runtime_available() -> tuple[bool, str | None]:
    try:
        import faster_whisper  # noqa: F401
    except Exception as exc:
        return False, str(exc)
    return True, None


def model_health(model_path: Path) -> dict[str, Any]:
    if not model_path.exists():
        return {
            "healthy": False,
            "status": "model_missing",
            "model_path": str(model_path),
            "model_id": DEFAULT_CT2_MODEL_ID,
            "original_model_id": ORIGINAL_MODEL_ID,
            "missing": ["model_dir"],
            "detail": "faster-whisper turbo requires a local CTranslate2 model directory",
        }

    missing = [name for name in REQUIRED_MODEL_FILES if not (model_path / name).exists()]
    if not any((model_path / name).exists() for name in TOKENIZER_FILES):
        missing.append("tokenizer.json|vocabulary.json")
    if missing:
        return {
            "healthy": False,
            "status": "model_missing",
            "model_path": str(model_path),
            "model_id": DEFAULT_CT2_MODEL_ID,
            "original_model_id": ORIGINAL_MODEL_ID,
            "missing": missing,
        }

    ok, detail = runtime_available()
    if not ok:
        return {
            "healthy": False,
            "status": "runtime_missing",
            "model_path": str(model_path),
            "model_id": DEFAULT_CT2_MODEL_ID,
            "original_model_id": ORIGINAL_MODEL_ID,
            "detail": f"faster-whisper runtime is unavailable: {detail}",
        }

    return {
        "healthy": True,
        "status": "ready",
        "model_path": str(model_path),
        "model_id": DEFAULT_CT2_MODEL_ID,
        "original_model_id": ORIGINAL_MODEL_ID,
        "runtime": RUNTIME_PACKAGE,
    }


@lru_cache(maxsize=2)
def _load_model(model_path: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_path, device="auto", compute_type="auto")


def transcribe_audio(*, audio_path: str, language: str, model_path: str) -> dict[str, Any]:
    started = time.time()
    model = _load_model(model_path)
    language_arg = None if language == "auto" else language
    segments_iter, info = model.transcribe(
        audio_path,
        language=language_arg,
        task="transcribe",
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    segments = [
        {
            "start_ms": int(round(float(item.start) * 1000)),
            "end_ms": int(round(float(item.end) * 1000)),
            "text": item.text.strip(),
            "language": getattr(info, "language", None),
        }
        for item in segments_iter
        if item.text and item.text.strip()
    ]
    return {
        "text": " ".join(item["text"] for item in segments).strip(),
        "segments": segments,
        "usage_seconds": max(1, round(time.time() - started)),
        "provider_response_id": None,
    }
