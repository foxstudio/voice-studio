from __future__ import annotations

import math
import time
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import soundfile as sf

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
LONG_AUDIO_THRESHOLD_SECONDS = 45.0
CHUNK_TARGET_SECONDS = 30.0
CHUNK_BOUNDARY_SEARCH_SECONDS = 3.0
CHUNK_ENERGY_WINDOW_MS = 80
CHUNK_RETRY_COUNT = 1
MAX_FAILED_CHUNK_RECOVERY_DEPTH = 2
MIN_FAILED_CHUNK_RECOVERY_SECONDS = 6.0
FAILED_CHUNK_BOUNDARY_SEARCH_SECONDS = 1.5
AUTO_LANGUAGE_RECOVERY = "English"
MAX_CHUNK_GENERATION_TOKENS = 512
CHUNK_REPETITION_PENALTY = 1.1
MAX_PLAUSIBLE_CHARACTERS_PER_SECOND = 30.0
MIN_RUNAWAY_OUTPUT_CHARACTERS = 1000
_MODEL_GENERATION_LOCK = threading.RLock()


def model_files_available(model_path: Path) -> bool:
    return all((model_path / name).is_file() for name in REQUIRED_MODEL_FILES)


def runtime_available() -> tuple[bool, str | None]:
    try:
        import mlx_audio  # noqa: F401
    except Exception as exc:
        return False, str(exc)
    return True, None


def model_health(model_path: Path) -> dict[str, Any]:
    missing = [name for name in REQUIRED_MODEL_FILES if not (model_path / name).is_file()]
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


def transcribe_audio(
    *,
    audio_path: str,
    language: str,
    model_path: str,
    context_terms: Sequence[str] = (),
) -> dict[str, Any]:
    from mlx_audio.stt.generate import generate_transcription

    started = time.time()
    with _MODEL_GENERATION_LOCK:
        model = _load_model(model_path)
        if _audio_duration_seconds(audio_path) > LONG_AUDIO_THRESHOLD_SECONDS:
            text, segments, incomplete_chunk_ranges = _transcribe_long_audio(
                model=model,
                audio_path=audio_path,
                language=_mlx_language(language),
                generate_transcription=generate_transcription,
                context_terms=context_terms,
            )
        else:
            output_base = Path(tempfile.gettempdir()) / f"qwen3-asr-{int(started * 1000)}"
            transcription = _generate(
                generate_transcription,
                model=model,
                audio_path=audio_path,
                output_base=output_base,
                language=_mlx_language(language),
                system_prompt=_context_prompt(context_terms),
            )
            text = _transcription_text(transcription)
            segments = _normalize_segments(getattr(transcription, "segments", None) or [])
            incomplete_chunk_ranges = []
    return {
        "text": text,
        "segments": segments,
        "usage_seconds": max(1, round(time.time() - started)),
        "provider_response_id": None,
        "incomplete_chunk_ranges": incomplete_chunk_ranges,
    }


def _generate(
    generate_transcription,
    *,
    model,
    audio_path: str,
    output_base: Path,
    language: str | None,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
    repetition_penalty: float | None = None,
):
    generation_options: dict[str, Any] = {}
    if max_tokens is not None:
        generation_options["max_tokens"] = max_tokens
    if repetition_penalty is not None:
        generation_options["repetition_penalty"] = repetition_penalty
    try:
        return generate_transcription(
            model=model,
            audio=audio_path,
            output_path=str(output_base),
            format="txt",
            verbose=False,
            chunk_duration=120.0,
            language=language,
            system_prompt=system_prompt,
            **generation_options,
        )
    finally:
        output_base.with_suffix(".txt").unlink(missing_ok=True)


def _transcribe_long_audio(
    *,
    model,
    audio_path: str,
    language: str | None,
    generate_transcription,
    context_terms: Sequence[str] = (),
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    chunk_texts: list[str] = []
    raw_segments: list[dict[str, Any]] = []
    incomplete_chunk_ranges: list[dict[str, Any]] = []
    with sf.SoundFile(audio_path) as source, tempfile.TemporaryDirectory(prefix="qwen3-asr-chunks-") as temp_dir:
        sample_rate = source.samplerate
        ranges = _soundfile_chunk_ranges(source)
        for index, (start_frame, end_frame) in enumerate(ranges):
            source.seek(start_frame)
            chunk_audio = source.read(
                end_frame - start_frame,
                dtype="float32",
                always_2d=True,
            )
            texts, segments, incomplete = _transcribe_chunk_with_recovery(
                model=model,
                chunk_audio=chunk_audio,
                sample_rate=sample_rate,
                absolute_start_frame=start_frame,
                chunk_id=f"{index:04d}",
                temp_dir=Path(temp_dir),
                language=language,
                generate_transcription=generate_transcription,
                context_terms=context_terms,
            )
            chunk_texts.extend(texts)
            raw_segments.extend(segments)
            incomplete_chunk_ranges.extend(incomplete)
    return " ".join(chunk_texts).strip(), _normalize_segments(raw_segments), incomplete_chunk_ranges


def _transcribe_chunk_with_recovery(
    *,
    model,
    chunk_audio: np.ndarray,
    sample_rate: int,
    absolute_start_frame: int,
    chunk_id: str,
    temp_dir: Path,
    language: str | None,
    generate_transcription,
    context_terms: Sequence[str],
    recovery_depth: int = 0,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    chunk_path = temp_dir / f"chunk-{chunk_id}.wav"
    sf.write(chunk_path, chunk_audio, sample_rate, subtype="PCM_16")
    chunk_duration = len(chunk_audio) / sample_rate
    text = ""
    chunk_segments: list[dict[str, Any]] = []
    failure_reason = "missing_text"
    for attempt, attempt_language in enumerate(_chunk_attempt_languages(language)):
        output_base = temp_dir / f"result-{chunk_id}-{attempt}"
        transcription = _generate(
            generate_transcription,
            model=model,
            audio_path=str(chunk_path),
            output_base=output_base,
            language=attempt_language,
            system_prompt=_context_prompt(context_terms),
            max_tokens=MAX_CHUNK_GENERATION_TOKENS,
            repetition_penalty=CHUNK_REPETITION_PENALTY,
        )
        text = _transcription_text(transcription)
        chunk_segments = _validated_chunk_segments(
            getattr(transcription, "segments", None) or [],
            chunk_duration=chunk_duration,
        )
        if not text and chunk_segments:
            text = " ".join(str(item["text"]) for item in chunk_segments).strip()
        if _is_implausible_chunk_output(
            text,
            chunk_duration=chunk_duration,
        ):
            failure_reason = "implausible_output"
            text = ""
            chunk_segments = []
            continue
        failure_reason = "missing_text" if not text else "missing_valid_timestamps"
        if text and chunk_segments:
            offset_seconds = absolute_start_frame / sample_rate
            return (
                [text],
                [
                    {
                        **item,
                        "start": float(item.get("start", 0.0)) + offset_seconds,
                        "end": float(item.get("end", 0.0)) + offset_seconds,
                    }
                    for item in chunk_segments
                ],
                [],
            )

    split_frame = _failed_chunk_recovery_split_frame(
        chunk_audio,
        sample_rate=sample_rate,
    )
    if (
        failure_reason == "implausible_output"
        and recovery_depth < MAX_FAILED_CHUNK_RECOVERY_DEPTH
        and split_frame is not None
    ):
        recovered_texts: list[str] = []
        recovered_segments: list[dict[str, Any]] = []
        recovered_incomplete: list[dict[str, Any]] = []
        for suffix, child_audio, child_offset in (
            ("a", chunk_audio[:split_frame], 0),
            ("b", chunk_audio[split_frame:], split_frame),
        ):
            child_texts, child_segments, child_incomplete = _transcribe_chunk_with_recovery(
                model=model,
                chunk_audio=child_audio,
                sample_rate=sample_rate,
                absolute_start_frame=(absolute_start_frame + child_offset),
                chunk_id=f"{chunk_id}-{suffix}",
                temp_dir=temp_dir,
                language=language,
                generate_transcription=generate_transcription,
                context_terms=context_terms,
                recovery_depth=recovery_depth + 1,
            )
            recovered_texts.extend(child_texts)
            recovered_segments.extend(child_segments)
            recovered_incomplete.extend(child_incomplete)
        return (
            recovered_texts,
            recovered_segments,
            recovered_incomplete,
        )

    return (
        [],
        [],
        [
            {
                "start_ms": round(absolute_start_frame * 1000 / sample_rate),
                "end_ms": round((absolute_start_frame + len(chunk_audio)) * 1000 / sample_rate),
                "reason": failure_reason,
            }
        ],
    )


def _failed_chunk_recovery_split_frame(
    chunk_audio: np.ndarray,
    *,
    sample_rate: int,
) -> int | None:
    minimum_frames = max(
        sample_rate,
        int(MIN_FAILED_CHUNK_RECOVERY_SECONDS * sample_rate),
    )
    total_frames = len(chunk_audio)
    if total_frames < minimum_frames * 2:
        return None
    midpoint = total_frames // 2
    search_frames = int(FAILED_CHUNK_BOUNDARY_SEARCH_SECONDS * sample_rate)
    search_start = max(minimum_frames, midpoint - search_frames)
    search_end = min(
        total_frames - minimum_frames,
        midpoint + search_frames,
    )
    if search_end <= search_start:
        return midpoint
    region = np.asarray(
        chunk_audio[search_start:search_end],
        dtype=np.float64,
    )
    if region.ndim == 1:
        frame_energy = np.square(region)
    else:
        frame_energy = np.mean(np.square(region), axis=1)
    if not len(frame_energy):
        return midpoint
    return search_start + int(np.argmin(frame_energy))


def _chunk_attempt_languages(language: str | None) -> tuple[str | None, ...]:
    """Retry auto-detected empty chunks with the product's source language.

    Qwen can return no text for a mixed-language movie chunk in automatic
    mode while the same audio produces stable English timestamps when the
    language is explicit. Explicit language requests keep the existing plain
    retry behavior.
    """

    if language is None:
        return (None, AUTO_LANGUAGE_RECOVERY)
    return tuple(language for _ in range(CHUNK_RETRY_COUNT + 1))


def _is_implausible_chunk_output(
    text: str,
    *,
    chunk_duration: float,
) -> bool:
    """Reject decoder loops that cannot be real speech for this time span."""

    normalized = " ".join(str(text or "").split())
    if len(normalized) < MIN_RUNAWAY_OUTPUT_CHARACTERS:
        return False
    return len(normalized) / max(chunk_duration, 0.001) > MAX_PLAUSIBLE_CHARACTERS_PER_SECOND


def _context_prompt(context_terms: Sequence[str]) -> str | None:
    terms = [" ".join(str(item).split()) for item in context_terms if str(item).strip()][:8]
    if not terms:
        return None
    return "Context terms; use these spellings only when supported by the audio: " + "; ".join(terms)


def _validated_chunk_segments(items: list[dict[str, Any]], *, chunk_duration: float) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for item in items:
        text = str(item.get("text", "")).strip()
        try:
            start = float(item.get("start", 0.0))
            end = float(item.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if not text or not math.isfinite(start) or not math.isfinite(end):
            continue
        start = min(chunk_duration, max(0.0, start))
        end = min(chunk_duration, max(0.0, end))
        if end <= start:
            continue
        validated.append({**item, "text": text, "start": start, "end": end})
    return validated


def _transcription_text(transcription) -> str:
    if hasattr(transcription, "text"):
        return str(getattr(transcription, "text") or "").strip()
    return str(transcription).strip()


def _audio_duration_seconds(audio_path: str) -> float:
    try:
        info = sf.info(audio_path)
    except Exception:
        return 0.0
    return float(info.frames / info.samplerate) if info.samplerate else 0.0


def _soundfile_chunk_ranges(source: sf.SoundFile) -> list[tuple[int, int]]:
    total_frames = source.frames
    sample_rate = source.samplerate
    target_frames = max(sample_rate, int(CHUNK_TARGET_SECONDS * sample_rate))
    search_frames = max(0, int(CHUNK_BOUNDARY_SEARCH_SECONDS * sample_rate))
    energy_window = max(1, int(CHUNK_ENERGY_WINDOW_MS * sample_rate / 1000))
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < total_frames:
        target_end = min(start + target_frames, total_frames)
        if target_end >= total_frames:
            ranges.append((start, total_frames))
            break
        search_start = max(start + sample_rate, target_end - search_frames)
        search_end = min(total_frames, target_end + search_frames)
        source.seek(search_start)
        region = source.read(search_end - search_start, dtype="float32", always_2d=True)
        frame_energy = np.mean(np.square(region, dtype=np.float64), axis=1)
        if len(frame_energy) > energy_window:
            cumulative = np.concatenate(([0.0], np.cumsum(frame_energy, dtype=np.float64)))
            energies = (cumulative[energy_window:] - cumulative[:-energy_window]) / energy_window
            cut = search_start + int(np.argmin(energies)) + energy_window // 2
        else:
            cut = target_end
        cut = min(total_frames, max(start + sample_rate, cut))
        # A cut near EOF must not create a tiny independent decoder request.
        # Keep every frame (including any final speech) in the preceding chunk;
        # the existing boundary-search budget bounds the extra chunk duration.
        if total_frames - cut <= search_frames:
            cut = total_frames
        ranges.append((start, cut))
        start = cut
    return ranges


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
        try:
            start = float(item.get("start", 0))
            end = float(item.get("end", 0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        normalized.append(
            {
                "start_ms": int(round(start * 1000)),
                "end_ms": int(round(end * 1000)),
                "text": text,
                "language": item.get("language"),
            }
        )
    return normalized
