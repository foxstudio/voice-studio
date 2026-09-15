from __future__ import annotations

import importlib.util
import platform
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Sequence

import soundfile as sf

from app.services import vibevoice_model
from app.services.asr_providers.contracts import (
    AsrResult,
    AsrSegment,
    CancellationSignal,
    DiarizationResult,
    DiarizationSegment,
    ProviderCapabilities,
)


PROVIDER_IDS = tuple(vibevoice_model.PROVIDER_TO_VARIANT)
CORE_CHUNK_MS = 5 * 60 * 1000
CONTEXT_MS = 5 * 1000
MAX_TOKENS = 8192
PREFILL_STEP_SIZE = 2048

CAPABILITIES = ProviderCapabilities(
    supports_transcription=True,
    supports_diarization=True,
    supports_segment_timestamps=True,
    supports_word_timestamps=False,
    supports_language_selection=True,
    supports_hotwords=True,
    supports_long_audio=True,
)

_inference_lock = threading.RLock()
_loaded_model = None
_loaded_model_path: str | None = None


class VibeVoiceAsrError(RuntimeError):
    pass


class VibeVoiceAsrProvider:
    capabilities = CAPABILITIES

    def __init__(self, provider_id: str) -> None:
        if provider_id not in PROVIDER_IDS:
            raise ValueError(f"Unsupported VibeVoice provider: {provider_id}")
        self.provider_id = provider_id
        self.variant_id = vibevoice_model.variant_for_provider(provider_id)

    def health_check(self) -> dict[str, object]:
        return model_health(self.provider_id)

    def transcribe(
        self,
        audio_path: str,
        *,
        language: str = "auto",
        hotwords: Sequence[str] = (),
        timeout_seconds: float | None = None,
        cancel_event: CancellationSignal | None = None,
    ) -> AsrResult:
        asr, _diarization = self.transcribe_and_diarize(
            audio_path,
            language=language,
            hotwords=hotwords,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
        )
        return asr

    def diarize(
        self,
        audio_path: str,
        *,
        timeout_seconds: float | None = None,
        cancel_event: CancellationSignal | None = None,
    ) -> DiarizationResult:
        _asr, diarization = self.transcribe_and_diarize(
            audio_path,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
        )
        return diarization

    def transcribe_and_diarize(
        self,
        audio_path: str,
        *,
        language: str = "auto",
        hotwords: Sequence[str] = (),
        timeout_seconds: float | None = None,
        cancel_event: CancellationSignal | None = None,
    ) -> tuple[AsrResult, DiarizationResult]:
        return transcribe_and_diarize(
            provider_id=self.provider_id,
            audio_path=audio_path,
            language=language,
            hotwords=hotwords,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
        )


def providers() -> tuple[VibeVoiceAsrProvider, ...]:
    return tuple(VibeVoiceAsrProvider(provider_id) for provider_id in PROVIDER_IDS)


def runtime_available() -> tuple[bool, str | None]:
    if platform.system().lower() != "darwin" or platform.machine().lower() not in {
        "arm64",
        "aarch64",
    }:
        return False, "VibeVoice MLX 需要 Apple Silicon Mac"
    for module in ("mlx", "mlx_audio", "mlx_lm"):
        if importlib.util.find_spec(module) is None:
            return False, f"缺少 {module} 运行环境"
    try:
        import mlx.core as mx

        if not mx.metal.is_available():
            return False, "MLX Metal 加速不可用"
    except Exception as exc:
        return False, f"无法初始化 MLX Metal：{exc}"
    return True, None


def model_health(
    provider_id: str,
    *,
    verify_integrity: bool = True,
) -> dict[str, object]:
    variant_id = vibevoice_model.variant_for_provider(provider_id)
    available, detail = runtime_available()
    if not available:
        return {
            "healthy": False,
            "status": "runtime_missing",
            "detail": detail,
            "provider_id": provider_id,
            "variant_id": variant_id,
        }
    status = vibevoice_model.installation_status(
        variant_id,
        verify_integrity=verify_integrity,
    )
    return {
        "healthy": bool(status["installed"]),
        "status": "ready" if status["installed"] else "model_missing",
        "detail": None if status["installed"] else "请先在设置中安装对应的 VibeVoice ASR 模型",
        "provider_id": provider_id,
        "variant_id": variant_id,
        "model_path": status["preferred_path"],
        "integrity": status["integrity"],
        "runtime": "mlx-audio",
        "acceleration": "mlx-metal",
    }


def transcribe_and_diarize(
    *,
    provider_id: str,
    audio_path: str,
    language: str = "auto",
    hotwords: Sequence[str] = (),
    timeout_seconds: float | None = None,
    cancel_event: CancellationSignal | None = None,
) -> tuple[AsrResult, DiarizationResult]:
    health = model_health(provider_id)
    if not health.get("healthy"):
        raise VibeVoiceAsrError(str(health.get("detail") or health.get("status")))
    source = Path(audio_path).expanduser()
    if not source.is_file():
        raise VibeVoiceAsrError(f"音频文件不存在：{source}")
    timeout = float(timeout_seconds or 0)
    if timeout < 0:
        raise ValueError("timeout_seconds must not be negative")
    info = sf.info(str(source))
    duration_ms = max(1, round(info.frames * 1000 / info.samplerate))
    context = _context_prompt(language=language, hotwords=hotwords)
    variant_id = vibevoice_model.variant_for_provider(provider_id)
    model_path = vibevoice_model.require_variant(variant_id)
    started_at = time.monotonic()
    asr_segments: list[AsrSegment] = []
    diarization_segments: list[DiarizationSegment] = []
    incomplete_ranges: list[dict[str, object]] = []
    generation_seconds = 0.0

    with _inference_lock:
        model = _load_model(model_path)
        windows = _chunk_windows(duration_ms)
        with tempfile.TemporaryDirectory(prefix="voice-studio-vibevoice-asr-") as temp_dir:
            for chunk_index, window in enumerate(windows, start=1):
                _ensure_active(cancel_event)
                if timeout and time.monotonic() - started_at >= timeout:
                    raise TimeoutError("VibeVoice ASR 超过任务时限")
                core_start_ms, core_end_ms, read_start_ms, read_end_ms = window
                chunk_path = source
                if len(windows) > 1:
                    chunk_path = Path(temp_dir) / f"chunk-{chunk_index:03d}.wav"
                    _write_audio_window(
                        source,
                        chunk_path,
                        start_ms=read_start_ms,
                        end_ms=read_end_ms,
                    )
                result = model.generate(
                    str(chunk_path),
                    context=context,
                    max_tokens=MAX_TOKENS,
                    temperature=0.0,
                    repetition_penalty=1.05,
                    repetition_context_size=256,
                    prefill_step_size=PREFILL_STEP_SIZE,
                    verbose=False,
                )
                generation_seconds += float(getattr(result, "total_time", 0.0) or 0.0)
                raw_segments = list(getattr(result, "segments", None) or [])
                if not raw_segments or _looks_repetitive(str(getattr(result, "text", "") or "")):
                    incomplete_ranges.append(
                        {
                            "start_ms": core_start_ms,
                            "end_ms": core_end_ms,
                            "reason": "structured_segments_missing" if not raw_segments else "repetition_detected",
                        }
                    )
                for item in raw_segments:
                    start_ms = read_start_ms + max(0, round(float(item.get("start", 0.0)) * 1000))
                    end_ms = read_start_ms + max(0, round(float(item.get("end", 0.0)) * 1000))
                    start_ms = min(duration_ms, start_ms)
                    end_ms = min(duration_ms, max(start_ms, end_ms))
                    midpoint_ms = start_ms + (end_ms - start_ms) // 2
                    if end_ms <= start_ms or midpoint_ms < core_start_ms or midpoint_ms >= core_end_ms:
                        continue
                    text = " ".join(str(item.get("text") or "").split()).strip()
                    if not text:
                        continue
                    speaker_value = item.get("speaker_id")
                    source_speaker = str(
                        "speaker_unknown" if speaker_value is None else speaker_value
                    )
                    chunk_speaker = f"chunk_{chunk_index:03d}:{source_speaker}"
                    asr_segments.append(
                        AsrSegment(
                            start_ms=start_ms,
                            end_ms=end_ms,
                            text=text,
                            language=None if language == "auto" else language,
                            speaker_cluster=chunk_speaker,
                        )
                    )
                    diarization_segments.append(
                        DiarizationSegment(
                            start_ms=start_ms,
                            end_ms=end_ms,
                            speaker_cluster=chunk_speaker,
                        )
                    )
                _ensure_active(cancel_event)

    asr_segments.sort(key=lambda item: (item.start_ms, item.end_ms, item.text))
    diarization_segments.sort(key=lambda item: (item.start_ms, item.end_ms, item.speaker_cluster))
    elapsed_seconds = max(0.0, time.monotonic() - started_at)
    asr = AsrResult(
        provider_id=provider_id,
        text=" ".join(item.text for item in asr_segments),
        segments=tuple(asr_segments),
        language=None if language == "auto" else language,
        duration_ms=duration_ms,
        metadata={
            "usage_seconds": generation_seconds,
            "wall_seconds": elapsed_seconds,
            "incomplete_chunk_ranges": incomplete_ranges,
            "chunk_duration_ms": CORE_CHUNK_MS,
            "chunk_context_ms": CONTEXT_MS,
            "variant_id": variant_id,
        },
    )
    diarization = DiarizationResult(
        provider_id=provider_id,
        segments=tuple(diarization_segments),
        duration_ms=duration_ms,
        metadata={
            "variant_id": variant_id,
            "chunk_duration_ms": CORE_CHUNK_MS,
            "chunk_context_ms": CONTEXT_MS,
        },
    )
    return asr, diarization


def _load_model(model_path: Path):
    global _loaded_model, _loaded_model_path
    resolved = str(model_path.resolve())
    if _loaded_model is not None and _loaded_model_path == resolved:
        return _loaded_model
    if _loaded_model is not None:
        _loaded_model = None
        _loaded_model_path = None
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:
            pass
    from mlx_audio.stt.utils import load_model

    _loaded_model = load_model(resolved, lazy=False, strict=False)
    _loaded_model_path = resolved
    return _loaded_model


def clear_loaded_model() -> None:
    global _loaded_model, _loaded_model_path
    with _inference_lock:
        _loaded_model = None
        _loaded_model_path = None
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:
            return


def _chunk_windows(duration_ms: int) -> tuple[tuple[int, int, int, int], ...]:
    windows = []
    for core_start_ms in range(0, duration_ms, CORE_CHUNK_MS):
        core_end_ms = min(duration_ms, core_start_ms + CORE_CHUNK_MS)
        windows.append(
            (
                core_start_ms,
                core_end_ms,
                max(0, core_start_ms - CONTEXT_MS),
                min(duration_ms, core_end_ms + CONTEXT_MS),
            )
        )
    return tuple(windows)


def _write_audio_window(
    source_path: Path,
    destination: Path,
    *,
    start_ms: int,
    end_ms: int,
) -> None:
    with sf.SoundFile(str(source_path)) as source:
        start_frame = max(0, round(start_ms * source.samplerate / 1000))
        end_frame = min(
            source.frames,
            max(start_frame + 1, round(end_ms * source.samplerate / 1000)),
        )
        source.seek(start_frame)
        audio = source.read(end_frame - start_frame, dtype="float32", always_2d=True)
        sf.write(str(destination), audio, source.samplerate, subtype="PCM_16")


def _context_prompt(*, language: str, hotwords: Sequence[str]) -> str | None:
    parts = []
    if language in {"en", "zh"}:
        parts.append(f"Primary language: {'English' if language == 'en' else 'Chinese'}.")
    terms = [" ".join(str(item).split()).strip() for item in hotwords]
    terms = [item for item in terms if item][:8]
    if terms:
        parts.append("Hotwords: " + ", ".join(terms))
    return " ".join(parts) or None


def _ensure_active(cancel_event: CancellationSignal | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise VibeVoiceAsrError("VibeVoice ASR 任务已取消")


def _looks_repetitive(text: str) -> bool:
    words = re.findall(r"[A-Za-z0-9']+|[\u3400-\u9fff]", text.lower())
    if len(words) < 120:
        return False
    windows = [tuple(words[index : index + 12]) for index in range(len(words) - 11)]
    if not windows:
        return False
    counts: dict[tuple[str, ...], int] = {}
    for window in windows:
        counts[window] = counts.get(window, 0) + 1
    return max(counts.values(), default=0) >= 8
