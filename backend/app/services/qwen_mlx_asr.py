from __future__ import annotations

import re
import time
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services import model_integrity


REQUIRED_MODEL_FILES = [
    "config.json",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "model.safetensors",
]
MODEL_REVISION = "579e237ce6ec925252973afe835d2f98a138602f"
MODEL_WEIGHTS_SIZE = 2_463_307_541
MODEL_WEIGHTS_SHA256 = "bf304b009cc7eca79283056f787b44c952d24ac22cec787b39732bba3c23c13c"
LONG_SEGMENT_SPLIT_THRESHOLD_MS = 8000
SENTENCE_PATTERN = re.compile(r".+?(?:[.!?。！？]+|$)", re.DOTALL)
ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")


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
    integrity_ok, integrity = model_integrity.verify_model_file(
        model_path,
        "model.safetensors",
        expected_size=MODEL_WEIGHTS_SIZE,
        expected_sha256=MODEL_WEIGHTS_SHA256,
        revision=MODEL_REVISION,
    )
    if not integrity_ok:
        return {
            "healthy": False,
            "status": "model_incomplete",
            "model_path": str(model_path),
            "detail": "Qwen3-ASR MLX 模型文件未通过固定 revision 的大小与 SHA-256 校验",
            "integrity": integrity,
        }
    return {
        "healthy": True,
        "status": "ready",
        "model_path": str(model_path),
        "runtime": "mlx-audio",
        "integrity": integrity,
    }


@lru_cache(maxsize=2)
def _load_model(model_path: str):
    from mlx_audio.stt.utils import load_model

    return load_model(model_path)


def transcribe_audio(*, audio_path: str, language: str, model_path: str) -> dict[str, Any]:
    from mlx_audio.stt.generate import generate_transcription

    started = time.time()
    model = _load_model(model_path)
    output_base = Path(tempfile.gettempdir()) / f"qwen3-asr-{int(started * 1000)}"
    transcription = generate_transcription(
        model=model,
        audio=audio_path,
        output_path=str(output_base),
        format="txt",
        verbose=False,
        chunk_duration=30.0,
        language=_mlx_language(language),
    )
    if hasattr(transcription, "text"):
        text = str(getattr(transcription, "text") or "").strip()
    else:
        text = str(transcription).strip()
    segments = _normalize_segments(getattr(transcription, "segments", None) or [])
    output_base.with_suffix(".txt").unlink(missing_ok=True)
    return {
        "text": text,
        "segments": segments,
        "usage_seconds": max(1, round(time.time() - started)),
        "provider_response_id": None,
    }


def _mlx_language(language: str) -> str | None:
    normalized = (language or "").strip()
    aliases = {
        "": None,
        "auto": None,
        "en": "English",
        "英文": "English",
        "zh": "Chinese",
        "中文": "Chinese",
    }
    return aliases.get(normalized.lower(), normalized)


def _normalize_segments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        segment = {
            "start_ms": int(round(float(item.get("start", 0)) * 1000)),
            "end_ms": int(round(float(item.get("end", 0)) * 1000)),
            "text": text,
            "language": item.get("language"),
        }
        normalized.extend(_split_long_segment(segment))
    return normalized


def _split_long_segment(segment: dict[str, Any]) -> list[dict[str, Any]]:
    start_ms = int(segment["start_ms"])
    end_ms = int(segment["end_ms"])
    duration_ms = end_ms - start_ms
    if duration_ms <= LONG_SEGMENT_SPLIT_THRESHOLD_MS:
        return [segment]

    parts = [match.group(0).strip() for match in SENTENCE_PATTERN.finditer(str(segment["text"])) if match.group(0).strip()]
    if len(parts) < 2:
        return [segment]

    weights = [_sentence_weight(part) for part in parts]
    total_weight = sum(weights)
    split_segments: list[dict[str, Any]] = []
    elapsed_weight = 0
    current_start_ms = start_ms
    for index, (part, weight) in enumerate(zip(parts, weights)):
        elapsed_weight += weight
        current_end_ms = end_ms if index == len(parts) - 1 else start_ms + round(duration_ms * elapsed_weight / total_weight)
        split_segments.append(
            {
                **segment,
                "start_ms": current_start_ms,
                "end_ms": current_end_ms,
                "text": part,
            }
        )
        current_start_ms = current_end_ms
    return split_segments


def _sentence_weight(text: str) -> int:
    return max(1, len(ENGLISH_WORD_PATTERN.findall(text)) + len(CJK_PATTERN.findall(text)))
