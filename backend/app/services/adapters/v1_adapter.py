"""IndexTTS v1 engine adapter.

Wraps mlx_indextts.generate.IndexTTS for Voice Studio backend.
Implements PRD S8 Engine Adapter interface:
  manifest / parameter_schema / runtime_config
  request_mapper / response_mapper
  health_check / generate / cancel / get_logs / error_mapper

The MLX-converted v1 model runs on Apple Silicon MPS by default
(the original PyTorch IndexTTS 1.5 was torch+mps).
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# project root for imports
_project_root = Path(__file__).resolve().parents[4]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# constants
MODEL_DIR = _project_root / "models" / "mlx-indexTTS-1.5"
SAMPLE_RATE = 24000
OUTPUT_DIR = os.path.expanduser("~/VoiceStudio/outputs")

# error code mapping: exception type -> (status_code, error_code, message)
ERROR_CODES: dict[type[Exception], tuple[int, str, str]] = {
    FileNotFoundError: (503, "MODEL_NOT_FOUND", "Model directory or file missing"),
    RuntimeError: (500, "INFERENCE_ERROR", "TTS inference failed"),
    ValueError: (400, "INVALID_PARAMETER", "Invalid parameter value"),
    MemoryError: (503, "OUT_OF_MEMORY", "GPU memory exhausted"),
    Exception: (500, "INTERNAL_ERROR", "Unexpected adapter error"),
}


class V1Adapter:
    """Adapter for IndexTTS v1 (MLX, Apple Silicon).

    Lifecycle:
      1. adapter = V1Adapter()
      2. adapter.load()            # loads model into memory
      3. adapter.generate(...)     # run inference
      4. adapter.unload()          # free GPU memory
    """

    def __init__(self):
        self._model: Any = None
        self._pipeline_lock = False
        self._generation_started = False
        self._last_error: str | None = None
        self._task_cancelled: set[str] = set()

    # =====================================================
    #  PRD S8.2 - Manifest
    # =====================================================
    @property
    def manifest(self) -> dict[str, Any]:
        """Return engine manifest conforming to PRD S8.2."""
        return {
            "id": "indextts-v1",
            "name": "IndexTTS 1.5",
            "version": "1.5",
            "framework": "torch",
            "device": "mps",
            "model_path": str(MODEL_DIR),
            "sample_rate": SAMPLE_RATE,
            "capabilities": [
                "local_inference",
                "voice_clone",
                "pinyin_control",
            ],
            "parameters": {
                "text": {"type": "string", "required": True},
                "voice_id": {"type": "string", "required": False},
                "ref_audio_path": {"type": "string", "required": True},
                "ref_text": {"type": "string", "required": False},
                "speed": {"type": "float", "min": 0.5, "max": 2.0, "default": 1.0},
                "temperature": {"type": "float", "default": 1.0},
            },
        }

    # =====================================================
    #  PRD S8.4 - Parameter Schema
    # =====================================================
    @property
    def parameter_schema(self) -> list[dict[str, Any]]:
        """Return UI parameter schema per PRD S8.4."""
        return [
            {
                "key": "speed",
                "label": "speed",
                "type": "slider",
                "level": "basic",
                "default": 1.0,
                "min": 0.5,
                "max": 2.0,
                "step": 0.05,
                "tooltip": "Control output speed, 1.0 is normal",
                "required": False,
                "visible_when": None,
                "engine_mapping": "speed",
            },
            {
                "key": "temperature",
                "label": "Temperature",
                "type": "slider",
                "level": "basic",
                "default": 1.0,
                "min": 0.1,
                "max": 2.0,
                "step": 0.05,
                "tooltip": "Higher = more diverse, lower = more stable",
                "required": False,
                "visible_when": None,
                "engine_mapping": "temperature",
            },
            {
                "key": "max_mel_tokens",
                "label": "Max Mel Tokens",
                "type": "slider",
                "level": "advanced",
                "default": 600,
                "min": 100,
                "max": 1500,
                "step": 50,
                "tooltip": "Maximum audio length in mel tokens",
                "required": False,
                "visible_when": None,
                "engine_mapping": "max_mel_tokens",
            },
            {
                "key": "ref_audio_path",
                "label": "Reference Audio",
                "type": "file",
                "level": "basic",
                "default": None,
                "tooltip": "Reference audio for voice cloning",
                "required": True,
                "visible_when": None,
                "engine_mapping": "audio_prompt",
            },
        ]

    # =====================================================
    #  PRD S8 - Runtime Config
    # =====================================================
    @property
    def runtime_config(self) -> dict[str, Any]:
        """Return runtime configuration (model path, device, memory)."""
        config_path = MODEL_DIR / "config.json"
        config = {}
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
        return {
            "model_path": str(MODEL_DIR),
            "model_version": config.get("version", "1.5"),
            "sample_rate": config.get("dataset", {}).get("sample_rate", SAMPLE_RATE),
            "device": "mps",
        }

    # =====================================================
    #  PRD S8.6 - Request Mapper
    # =====================================================
    def request_mapper(self, request: dict[str, Any]) -> dict[str, Any]:
        """Map Voice Studio unified request to IndexTTS v1 infer() args.

        Args:
            request: Unified request dict with keys:
                text, voice_id, ref_audio_path, ref_text,
                speed, temperature, language, parameters

        Returns:
            Dict with keys matching infer() kwargs:
                audio_prompt, text, output_path, speed,
                temperature, max_mel_tokens, etc.
        """
        input_data = request.get("input", request)
        controls = request.get("controls", request.get("parameters", {}))
        params = request.get("parameters", controls)

        mapped: dict[str, Any] = {}

        # required fields
        mapped["text"] = input_data.get("text", "")
        mapped["audio_prompt"] = (
            input_data.get("ref_audio_path")
            or input_data.get("reference_audio_path")
            or ""
        )

        # output path
        if "output_path" in request:
            mapped["output_path"] = request["output_path"]
        else:
            audio_id = uuid.uuid4().hex[:12]
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            mapped["output_path"] = os.path.join(OUTPUT_DIR, f"{audio_id}.wav")

        # basic params
        mapped["speed"] = float(controls.get("speed", params.get("speed", 1.0)))
        mapped["temperature"] = float(
            controls.get("temperature", params.get("temperature", 1.0))
        )

        # advanced params
        mapped["max_mel_tokens"] = int(
            params.get("max_mel_tokens", params.get("max_mel_tokens", 600))
        )
        mapped["max_text_tokens_per_segment"] = int(
            params.get("max_text_tokens_per_segment", 120)
        )
        mapped["top_p"] = float(params.get("top_p", 0.8))
        mapped["top_k"] = int(params.get("top_k", 30))
        mapped["repetition_penalty"] = float(
            params.get("repetition_penalty", 10.0)
        )
        mapped["interval_silence"] = int(params.get("interval_silence", 200))
        mapped["segment_overlap_ms"] = int(params.get("segment_overlap_ms", 50))
        mapped["verbose"] = False

        seed = params.get("seed")
        if seed is not None:
            mapped["seed"] = int(seed)

        return mapped

    # =====================================================
    #  PRD S8.7 - Response Mapper
    # =====================================================
    def response_mapper(
        self,
        raw: dict[str, Any],
        task_id: str = "",
    ) -> dict[str, Any]:
        """Map raw inference result to unified response format per PRD S8.7."""
        return {
            "task_id": task_id,
            "result_id": raw.get("audio_id", uuid.uuid4().hex[:12]),
            "status": "success",
            "engine_id": self.manifest["id"],
            "output": {
                "audio_file_id": raw.get("audio_id", ""),
                "duration_ms": raw.get("duration_ms", 0),
                "format": "wav",
                "sample_rate": raw.get("sample_rate", SAMPLE_RATE),
            },
            "metrics": {
                "generation_time_ms": raw.get("generation_time_ms", 0),
                "rtf": round(
                    raw.get("generation_time_ms", 1)
                    / max(raw.get("duration_ms", 1), 1),
                    3,
                ),
            },
            "snapshot": {
                "input_text": raw.get("input_text", ""),
                "parameters": raw.get("parameters", {}),
            },
            "warnings": raw.get("warnings", []),
            "error": None,
        }

    # =====================================================
    #  PRD S8 - Health Check
    # =====================================================
    def health_check(self) -> dict[str, Any]:
        """Verify model availability without loading weights.

        Checks:
          1. model_path exists
          2. config.json is readable
          3. at least one .safetensors weight file exists
        """
        checks: dict[str, bool | str] = {}
        model_path = MODEL_DIR

        # check 1: directory
        if not model_path.exists():
            checks["model_dir"] = "missing"
            return {
                "engine_id": self.manifest["id"],
                "healthy": False,
                "status": "not_installed",
                "checks": checks,
            }
        checks["model_dir"] = "ok"

        # check 2: config.json
        config_path = model_path / "config.json"
        if not config_path.exists():
            checks["config_json"] = "missing"
            return {
                "engine_id": self.manifest["id"],
                "healthy": False,
                "status": "error",
                "error": "config.json not found",
                "checks": checks,
            }
        try:
            with open(config_path) as f:
                json.load(f)
            checks["config_json"] = "ok"
        except Exception as e:
            checks["config_json"] = f"unreadable: {e}"
            return {
                "engine_id": self.manifest["id"],
                "healthy": False,
                "status": "error",
                "error": f"config.json unreadable: {e}",
                "checks": checks,
            }

        # check 3: safetensors
        sfiles = list(model_path.glob("*.safetensors"))
        idx_file = model_path / "model.safetensors.index.json"
        has_weights = len(sfiles) > 0 or idx_file.exists()
        checks["safetensors"] = "ok" if has_weights else "missing"

        if not has_weights:
            return {
                "engine_id": self.manifest["id"],
                "healthy": False,
                "status": "error",
                "error": "No .safetensors weight files found",
                "checks": checks,
            }

        return {
            "engine_id": self.manifest["id"],
            "healthy": True,
            "status": "available",
            "checks": checks,
        }

    # =====================================================
    #  PRD S8 - Generate
    # =====================================================
    def generate(
        self,
        text: str,
        ref_audio_path: str,
        output_path: str | None = None,
        speed: float = 1.0,
        temperature: float = 1.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate TTS audio.

        Args:
            text: Text to synthesize
            ref_audio_path: Reference audio for voice cloning
            output_path: Output .wav path (auto-generated if None)
            speed: Playback speed (0.5-2.0)
            temperature: Sampling temperature

        Returns:
            Dict with audio_id, duration_ms, generation_time_ms, sample_rate
        """
        if not self._model:
            raise RuntimeError(
                "Model not loaded. Call load() before generate()."
            )
        if self._pipeline_lock:
            raise RuntimeError(
                "Another generation is in progress. Wait or call cancel()."
            )
        if not ref_audio_path or not os.path.exists(ref_audio_path):
            raise FileNotFoundError(
                f"Reference audio not found: {ref_audio_path}"
            )

        audio_id = uuid.uuid4().hex[:12]
        if output_path is None:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            output_path = os.path.join(OUTPUT_DIR, f"{audio_id}.wav")

        kwargs.setdefault("max_mel_tokens", 600)
        kwargs.setdefault("max_text_tokens_per_segment", 120)
        kwargs.setdefault("verbose", False)

        self._pipeline_lock = True
        self._generation_started = True
        self._last_error = None

        try:
            t_start = time.perf_counter()
            self._model.infer(
                audio_prompt=ref_audio_path,
                text=text,
                output_path=output_path,
                speed=speed,
                temperature=temperature,
                **kwargs,
            )
            elapsed_ms = int((time.perf_counter() - t_start) * 1000)

            # compute duration from WAV file size
            duration_ms = 0
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                duration_ms = max(
                    0,
                    int((file_size - 44) / (SAMPLE_RATE * 2) * 1000),
                )

            return {
                "audio_id": audio_id,
                "output_path": output_path,
                "duration_ms": duration_ms,
                "generation_time_ms": elapsed_ms,
                "sample_rate": SAMPLE_RATE,
            }
        except Exception as e:
            self._last_error = str(e)
            raise
        finally:
            self._pipeline_lock = False

    # =====================================================
    #  PRD S8 - Cancel
    # =====================================================
    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress generation.

        Note: MLX generation runs synchronously and cannot be truly
        interrupted mid-inference. This marks the task as cancelled.

        Returns:
            True if task was registered for cancellation
        """
        self._task_cancelled.add(task_id)
        return True

    # =====================================================
    #  PRD S8 - Logs
    # =====================================================
    def get_logs(self, task_id: str | None = None) -> list[dict[str, Any]]:
        """Return adapter logs.

        For v1 (synchronous), logs are limited to error state
        and generation activity.

        Returns:
            List of log entries
        """
        logs: list[dict[str, Any]] = []
        if self._last_error:
            logs.append({
                "level": "error",
                "message": self._last_error,
                "task_id": task_id,
            })
        if self._generation_started:
            logs.append({
                "level": "info",
                "message": "Generation pipeline active",
                "task_id": task_id,
            })
        return logs

    # =====================================================
    #  PRD S8 - Error Mapper
    # =====================================================
    def error_mapper(self, error: Exception) -> dict[str, Any]:
        """Map native exceptions to unified error response.

        Returns:
            {"error_code": str, "status_code": int, "message": str}
        """
        for exc_type, (status_code, code, message) in ERROR_CODES.items():
            if isinstance(error, exc_type):
                return {
                    "error_code": code,
                    "status_code": status_code,
                    "message": f"{message}: {error}",
                }
        return {
            "error_code": "INTERNAL_ERROR",
            "status_code": 500,
            "message": f"Internal error: {error}",
        }

    # =====================================================
    #  Lifecycle: load / unload
    # =====================================================
    def load(self, model_dir: str | None = None, **kwargs: Any) -> None:
        """Load the MLX IndexTTS v1 model.

        Args:
            model_dir: Override model directory
            **kwargs: Passed to IndexTTS.load_model()
        """
        target_dir = model_dir or str(MODEL_DIR)
        if not os.path.exists(target_dir):
            raise FileNotFoundError(
                f"Model directory not found: {target_dir}"
            )

        from mlx_indextts.generate import IndexTTS

        self._model = IndexTTS.load_model(
            target_dir,
            memory_limit_gb=kwargs.pop("memory_limit_gb", 0),
            quantize_bits=kwargs.pop("quantize_bits", None),
        )
        self._last_error = None

    def unload(self) -> None:
        """Unload model and free GPU memory."""
        self._model = None
        self._pipeline_lock = False
        self._last_error = None
