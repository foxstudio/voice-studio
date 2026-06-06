"""OmniVoice adapter -- PRD 8 compliant engine adapter for k2-fsa/OmniVoice.

Wraps OmniVoice (PyTorch + MPS) inference behind the Engine Adapter interface:
manifest / parameter_schema / runtime_config / request_mapper /
response_mapper / health_check / generate / cancel / get_logs / error_mapper.

Model: Qwen3-0.6B backbone, 600+ languages, zero-shot TTS with voice cloning.
Lazy singleton load: first generate() call triggers 30-90s model load.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# Module-level model singleton (lazy load)
# ---------------------------------------------------------------
_model: Any = None  # OmniVoice instance
_model_lock = threading.Lock()
_model_loaded = False
_load_start_time: float | None = None
_load_end_time: float | None = None
_cancel_flag = threading.Event()
_generation_log: list[str] = []

MODEL_ID = "k2-fsa/OmniVoice"
MODEL_VERSION = "0.1.5"
HF_CACHE_DIR = os.path.expanduser(
    "~/.cache/huggingface/hub/models--k2-fsa--OmniVoice/snapshots/"
)
DEFAULT_SAMPLE_RATE = 24000


def _get_or_load_model(device_map: str = "mps") -> Any:
    """Lazy singleton: load OmniVoice model on first call. Thread-safe."""
    global _model, _model_loaded, _load_start_time, _load_end_time

    with _model_lock:
        if _model_loaded and _model is not None:
            return _model

        _load_start_time = time.time()
        _generation_log.append(
            f"[{time.strftime('%H:%M:%S')}] Loading OmniVoice from {MODEL_ID} "
            f"with device_map={device_map}"
        )
        logger.info("Loading OmniVoice model %s (device_map=%s)", MODEL_ID, device_map)

        try:
            from omnivoice import OmniVoice  # type: ignore[import-untyped]

            _model = OmniVoice.from_pretrained(MODEL_ID, device_map=device_map)
            _model_loaded = True
            _load_end_time = time.time()
            elapsed = _load_end_time - _load_start_time
            _generation_log.append(
                f"[{time.strftime('%H:%M:%S')}] Model loaded in {elapsed:.1f}s"
            )
            logger.info("OmniVoice model loaded in %.1fs", elapsed)
            return _model
        except Exception as exc:
            _load_end_time = time.time()
            _generation_log.append(
                f"[{time.strftime('%H:%M:%S')}] Model load FAILED: {exc}"
            )
            logger.error("Failed to load OmniVoice: %s", exc)
            raise


# ---------------------------------------------------------------
# Mapped error codes (PRD 25.1)
# ---------------------------------------------------------------
ERROR_MAP: dict[str, str] = {
    "FileNotFoundError": "E_MODEL_FILE_MISSING",
    "OSError": "E_MODEL_FILE_MISSING",
    "ValueError": "E_PARAMETER_INVALID",
    "RuntimeError": "E_RUNTIME_ERROR",
    "NotImplementedError": "E_DEVICE_UNAVAILABLE",
    "ImportError": "E_ENGINE_NOT_READY",
    "ModuleNotFoundError": "E_ENGINE_NOT_READY",
    "MemoryError": "E_RUNTIME_ERROR",
    "KeyboardInterrupt": "E_TASK_CANCELLED",
}


# ---------------------------------------------------------------
# Adapter dataclasses
# ---------------------------------------------------------------
@dataclass
class Manifest:
    """PRD 8.2 manifest fields, served via adapter.manifest property."""

    id: str = "omnivoice"
    name: str = "OmniVoice"
    version: str = MODEL_VERSION
    framework: str = "pytorch"
    device: str = "mps"
    model_id: str = MODEL_ID
    capabilities: list[str] = field(
        default_factory=lambda: [
            "local_inference",
            "voice_clone",
            "multilingual",
            "emotion_control",
            "nonverbal_tags",
        ]
    )
    sample_rate: int = DEFAULT_SAMPLE_RATE
    supported_languages: list[str] = field(
        default_factory=lambda: [
            "zh", "en", "ja", "ko", "fr", "de", "es",
        ]
    )
    engine_type: str = "local"
    provider: str = "k2-fsa"
    description: str = (
        "600+ language zero-shot TTS with voice cloning and voice design"
    )
    privacy_level: str = "local_only"


# ---------------------------------------------------------------
# OmniVoiceAdapter
# ---------------------------------------------------------------
class OmniVoiceAdapter:
    """PRD 8 compliant engine adapter for OmniVoice PyTorch + MPS inference."""

    def __init__(self) -> None:
        self._manifest = Manifest()
        self._device_map = "mps"
        self._cancel = threading.Event()

    # ---- PRD 8.1 interface -----------------------------------

    @property
    def manifest(self) -> dict[str, Any]:
        """Return engine manifest as dict (PRD 8.2)."""
        return {
            "id": self._manifest.id,
            "name": self._manifest.name,
            "version": self._manifest.version,
            "framework": self._manifest.framework,
            "device": self._manifest.device,
            "model_id": self._manifest.model_id,
            "capabilities": self._manifest.capabilities,
            "sample_rate": self._manifest.sample_rate,
            "supported_languages": self._manifest.supported_languages,
            "engine_type": self._manifest.engine_type,
            "provider": self._manifest.provider,
            "description": self._manifest.description,
            "privacy_level": self._manifest.privacy_level,
        }

    @property
    def parameter_schema(self) -> dict[str, Any]:
        """Parameter schema for OmniVoice-specific controls (PRD 8.4)."""
        return {
            "text": {
                "type": "string",
                "label": "Input Text",
                "level": "basic",
                "required": True,
            },
            "ref_audio_path": {
                "type": "string",
                "label": "Reference Audio",
                "level": "basic",
                "required": False,
                "description": "Path to reference audio for voice cloning",
            },
            "ref_text": {
                "type": "string",
                "label": "Reference Text",
                "level": "advanced",
                "required": False,
                "description": "Transcript of reference audio (improves quality)",
            },
            "language": {
                "type": "string",
                "label": "Language",
                "level": "basic",
                "required": False,
                "default": "auto",
                "description": "Language code (e.g. zh, en) or auto-detect",
            },
            "emotion": {
                "type": "string",
                "label": "Emotion",
                "level": "advanced",
                "required": False,
                "enum": [
                    "happy", "sad", "angry", "afraid", "disgusted",
                    "melancholic", "surprised", "calm",
                ],
            },
            "speed": {
                "type": "slider",
                "label": "Speed",
                "level": "basic",
                "default": 1.0,
                "min": 0.5,
                "max": 3.0,
                "step": 0.1,
                "required": False,
            },
            "duration": {
                "type": "number",
                "label": "Target Duration (seconds)",
                "level": "advanced",
                "default": None,
                "required": False,
            },
            "device_map": {
                "type": "string",
                "label": "Device",
                "level": "developer",
                "default": "mps",
                "enum": ["mps", "cpu", "auto"],
                "required": False,
            },
            "supports_nonverbal": {
                "type": "boolean",
                "label": "Nonverbal Tags",
                "level": "advanced",
                "default": True,
                "required": False,
                "description": "Enable [laughter], [sigh], etc.",
            },
        }

    @property
    def runtime_config(self) -> dict[str, Any]:
        """Current runtime configuration."""
        return {
            "device_map": self._device_map,
            "model_id": MODEL_ID,
            "hf_cache_dir": HF_CACHE_DIR,
            "model_loaded": _model_loaded,
            "load_time_seconds": (
                round(_load_end_time - _load_start_time, 1)
                if _load_start_time and _load_end_time
                else None
            ),
        }

    def health_check(self) -> dict[str, Any]:
        """Verify model cache exists (does NOT load model - PRD 8.1)."""
        cache_path = Path(HF_CACHE_DIR)
        if not cache_path.exists():
            return {
                "healthy": False,
                "engine_id": "omnivoice",
                "status": "model_cache_missing",
                "detail": f"HF cache dir not found: {HF_CACHE_DIR}",
            }

        snapshots = list(cache_path.iterdir())
        if not snapshots:
            return {
                "healthy": False,
                "engine_id": "omnivoice",
                "status": "model_cache_empty",
                "detail": f"No snapshots in {HF_CACHE_DIR}",
            }

        for snap in snapshots:
            if snap.is_dir():
                files = list(snap.rglob("*"))
                total_size = sum(f.stat().st_size for f in files if f.is_file())
                return {
                    "healthy": True,
                    "engine_id": "omnivoice",
                    "status": "model_cached",
                    "snapshot": snap.name,
                    "files": len([f for f in files if f.is_file()]),
                    "size_mb": round(total_size / (1024 * 1024), 1),
                }

        return {
            "healthy": False,
            "engine_id": "omnivoice",
            "status": "model_cache_empty",
            "detail": f"No valid snapshots in {HF_CACHE_DIR}",
        }

    def request_mapper(self, params: dict[str, Any]) -> dict[str, Any]:
        """Map unified frontend request fields to OmniVoice.generate() kwargs."""
        mapped: dict[str, Any] = {}

        mapped["text"] = params.get("text", "")

        lang = params.get("language") or params.get("lang")
        if lang and lang != "auto":
            mapped["language"] = lang

        ref_audio = params.get("ref_audio_path") or params.get("reference_audio_path")
        if ref_audio:
            mapped["ref_audio"] = ref_audio

        ref_text = params.get("ref_text") or params.get("reference_text")
        if ref_text:
            mapped["ref_text"] = ref_text

        instruct = params.get("instruct") or params.get("emotion_text")
        if instruct:
            mapped["instruct"] = instruct

        speed = params.get("speed")
        if speed is not None:
            mapped["speed"] = float(speed)

        duration = params.get("duration") or params.get("target_duration_ms")
        if duration is not None:
            mapped["duration"] = float(duration) / 1000.0 if float(duration) > 100 else float(duration)

        return mapped

    def response_mapper(self, raw_result: list[np.ndarray], output_path: str, generation_time_ms: int) -> dict[str, Any]:
        """Map OmniVoice raw output + WAV file to standard response (PRD 8.7)."""
        audio_duration_ms = 0

        if raw_result and len(raw_result) > 0:
            total_samples = sum(arr.shape[-1] for arr in raw_result)
            audio_duration_ms = int((total_samples / DEFAULT_SAMPLE_RATE) * 1000)

        return {
            "output_path": output_path,
            "sample_rate": DEFAULT_SAMPLE_RATE,
            "duration_ms": audio_duration_ms,
            "generation_time_ms": generation_time_ms,
            "num_segments": len(raw_result),
            "format": "wav",
        }

    def error_mapper(self, exc: Exception) -> dict[str, str]:
        """Map Python exception to PRD 25.1 error code."""
        exc_type = type(exc).__name__
        error_code = ERROR_MAP.get(exc_type, "E_RUNTIME_ERROR")
        return {
            "code": error_code,
            "message": str(exc),
            "exception_type": exc_type,
        }

    # ---- Core generate method -------------------------------

    def generate(
        self,
        text: str,
        ref_audio_path: str | None = None,
        ref_text: str | None = None,
        language: str | None = None,
        emotion: str | None = None,
        speed: float = 1.0,
        output_path: str | None = None,
        device_map: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate speech via OmniVoice. Lazy-loads model on first call.

        Returns dict with keys: output_path, sample_rate, duration_ms,
        generation_time_ms, num_segments.
        """
        global _cancel_flag, _generation_log

        _cancel_flag.clear()
        _generation_log.append(
            f"[{time.strftime('%H:%M:%S')}] generate() called: "
            f"text='{text[:50]}{'...' if len(text) > 50 else ''}'"
        )

        dev = device_map or self._device_map
        self._device_map = dev

        if output_path is None:
            import tempfile
            fd, output_path = tempfile.mkstemp(suffix=".wav", prefix="omnivoice_")
            os.close(fd)

        try:
            model = _get_or_load_model(dev)

            gen_kwargs: dict[str, Any] = {"text": text}

            if language and language != "auto":
                gen_kwargs["language"] = language

            if ref_audio_path:
                gen_kwargs["ref_audio"] = ref_audio_path
                if ref_text:
                    gen_kwargs["ref_text"] = ref_text

            if emotion and emotion in [
                "happy", "sad", "angry", "afraid", "disgusted",
                "melancholic", "surprised", "calm",
            ]:
                gen_kwargs["instruct"] = f"Speak with {emotion} emotion"

            if speed != 1.0:
                gen_kwargs["speed"] = speed

            for key in ("duration", "generation_config"):
                if key in kwargs and kwargs[key] is not None:
                    gen_kwargs[key] = kwargs[key]

            _generation_log.append(
                f"[{time.strftime('%H:%M:%S')}] Starting inference..."
            )

            start = time.time()

            if _cancel_flag.is_set():
                _generation_log.append(
                    f"[{time.strftime('%H:%M:%S')}] Cancelled before inference"
                )
                return {
                    "output_path": output_path,
                    "sample_rate": DEFAULT_SAMPLE_RATE,
                    "duration_ms": 0,
                    "generation_time_ms": 0,
                    "num_segments": 0,
                    "status": "cancelled",
                }

            audio_segments: list[np.ndarray] = model.generate(**gen_kwargs)

            generation_time_ms = int((time.time() - start) * 1000)
            _generation_log.append(
                f"[{time.strftime('%H:%M:%S')}] "
                f"Inference complete: {generation_time_ms}ms, "
                f"{len(audio_segments)} segment(s)"
            )

            self._write_wav(audio_segments, output_path)

            result = self.response_mapper(audio_segments, output_path, generation_time_ms)

            _generation_log.append(
                f"[{time.strftime('%H:%M:%S')}] "
                f"WAV written to {output_path}, "
                f"duration={result['duration_ms']}ms"
            )

            return result

        except Exception as exc:
            error_info = self.error_mapper(exc)
            _generation_log.append(
                f"[{time.strftime('%H:%M:%S')}] "
                f"ERROR: [{error_info['code']}] {error_info['message']}"
            )
            logger.error(
                "OmniVoice generation failed: [%s] %s",
                error_info["code"], error_info["message"],
            )
            raise RuntimeError(
                f"[{error_info['code']}] {error_info['message']}"
            ) from exc

    def cancel(self) -> dict[str, str]:
        """Cancel in-progress generation."""
        global _cancel_flag
        _cancel_flag.set()
        _generation_log.append(f"[{time.strftime('%H:%M:%S')}] Cancel requested")
        return {"status": "cancelled", "engine_id": "omnivoice"}

    def get_logs(self, tail: int = 50) -> list[str]:
        """Return recent generation logs."""
        global _generation_log
        return _generation_log[-tail:]

    # ---- Internal helpers -----------------------------------

    @staticmethod
    def _write_wav(audio_segments: list[np.ndarray], output_path: str) -> None:
        """Concatenate audio segments and write to WAV file."""
        if not audio_segments:
            logger.warning("No audio segments to write")
            return

        segments_2d = [
            arr.reshape(1, -1) if arr.ndim == 1 else arr
            for arr in audio_segments
        ]
        audio = np.concatenate(segments_2d, axis=-1)

        if audio.ndim > 1:
            audio = audio.squeeze()

        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        audio = np.clip(audio, -1.0, 1.0)
        try:
            import soundfile as sf
            audio_int16 = (audio * 32767).astype(np.int16)
            sf.write(output_path, audio_int16, DEFAULT_SAMPLE_RATE, subtype="PCM_16")
        except Exception:
            import scipy.io.wavfile as wavfile
            audio_int16 = (audio * 32767).astype(np.int16)
            wavfile.write(output_path, DEFAULT_SAMPLE_RATE, audio_int16)


OmniVoiceEngineAdapter = OmniVoiceAdapter
