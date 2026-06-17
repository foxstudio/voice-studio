from __future__ import annotations

import time
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any


REQUIRED_MODEL_FILES = [
    "config.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "model.safetensors",
]


def runtime_available() -> tuple[bool, str | None]:
    try:
        import mlx_audio  # noqa: F401
    except Exception as exc:
        return False, str(exc)
    return True, None


def model_health(model_path: Path) -> dict[str, Any]:
    missing = [name for name in REQUIRED_MODEL_FILES if not (model_path / name).exists()]
    if missing:
        return {
            "healthy": False,
            "status": "model_missing",
            "model_path": str(model_path),
            "missing": missing,
        }
    ok, detail = runtime_available()
    if not ok:
        return {
            "healthy": False,
            "status": "runtime_missing",
            "model_path": str(model_path),
            "detail": f"mlx-audio runtime is unavailable: {detail}",
        }
    return {
        "healthy": True,
        "status": "ready",
        "model_path": str(model_path),
        "runtime": "mlx-audio",
    }


@lru_cache(maxsize=2)
def _load_model(model_path: str):
    from mlx_audio.stt.utils import load_model

    return load_model(model_path)


def transcribe_audio(*, audio_path: str, language: str, model_path: str) -> dict[str, Any]:
    from mlx_audio.stt.generate import generate_transcription

    started = time.time()
    model = _load_model(model_path)
    # mlx-audio's published examples rely on model-side language detection.
    # We keep the requested language in the record, but do not force an
    # unverified runtime argument here.
    output_base = Path(tempfile.gettempdir()) / f"qwen3-asr-{int(started * 1000)}"
    transcription = generate_transcription(model=model, audio=audio_path, output_path=str(output_base), format="txt", verbose=False)
    if hasattr(transcription, "text"):
        text = str(getattr(transcription, "text") or "").strip()
    else:
        text = str(transcription).strip()
    segments = [
        {
            "start_ms": int(round(float(item.get("start", 0)) * 1000)),
            "end_ms": int(round(float(item.get("end", 0)) * 1000)),
            "text": str(item.get("text", "")).strip(),
            "language": item.get("language"),
        }
        for item in (getattr(transcription, "segments", None) or [])
        if str(item.get("text", "")).strip()
    ]
    output_base.with_suffix(".txt").unlink(missing_ok=True)
    return {
        "text": text,
        "segments": segments,
        "usage_seconds": max(1, round(time.time() - started)),
        "provider_response_id": None,
    }
