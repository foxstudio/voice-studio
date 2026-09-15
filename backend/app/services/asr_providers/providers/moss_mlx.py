from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from ..contracts import (
    AsrResult,
    CancellationSignal,
    DiarizationResult,
    ProviderCapabilities,
)
from ..normalizers import normalize_asr_segments, normalize_diarization_segments


RUNTIME_ENV = "VOICE_STUDIO_MOSS_MLX_PYTHON"
MODEL_ENV = "VOICE_STUDIO_MOSS_MLX_MODEL"
CLI_MODULE_ENV = "VOICE_STUDIO_MOSS_MLX_CLI_MODULE"
DEFAULT_CLI_MODULE = "moss_transcribe_diarize.mlx.cli"
DEFAULT_TIMEOUT_SECONDS = 30 * 60
DEFAULT_MAX_NEW_TOKENS = 8192


class MossMlxError(RuntimeError):
    pass


class MossMlxConfigurationError(MossMlxError):
    pass


class MossMlxTimeoutError(MossMlxError):
    pass


class MossMlxCancelledError(MossMlxError):
    pass


class MossMlxProvider:
    provider_id = "moss-transcribe-diarize-mlx"
    capabilities = ProviderCapabilities(
        supports_transcription=True,
        supports_diarization=True,
        supports_segment_timestamps=True,
        supports_word_timestamps=False,
        supports_language_selection=False,
        supports_hotwords=True,
        supports_long_audio=True,
    )

    def __init__(
        self,
        *,
        runtime_python: str | Path | None = None,
        model_path: str | Path | None = None,
        cli_module: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS,
        environment: Mapping[str, str] | None = None,
        popen_factory: Callable[..., Any] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._explicit_runtime = str(runtime_python) if runtime_python is not None else None
        self._explicit_model = str(model_path) if model_path is not None else None
        self._explicit_cli_module = cli_module
        self.timeout_seconds = float(timeout_seconds)
        self.max_new_tokens = max(512, int(max_new_tokens))
        self._environment = dict(environment or {})
        self._popen_factory = popen_factory

    def health_check(self) -> dict[str, object]:
        """Validate paths without importing or starting the external runtime."""

        runtime = self.runtime_python()
        model = self.model_path()
        if runtime is None:
            return {
                "healthy": False,
                "status": "runtime_missing",
                "detail": f"Set {RUNTIME_ENV} to the isolated MOSS Python executable",
            }
        if model is None or not model.is_dir():
            return {
                "healthy": False,
                "status": "model_missing",
                "python_path": runtime,
                "model_path": str(model) if model is not None else None,
                "detail": f"Set {MODEL_ENV} to a local MOSS MLX model directory",
            }
        return {
            "healthy": True,
            "status": "ready",
            "python_path": runtime,
            "model_path": str(model),
            "cli_module": self.cli_module(),
            "isolation": "external_process",
        }

    def runtime_python(self) -> str | None:
        raw = self._explicit_runtime or os.environ.get(RUNTIME_ENV)
        if not raw:
            return None
        expanded = str(Path(raw).expanduser())
        if Path(expanded).is_file():
            return expanded
        return shutil.which(raw)

    def model_path(self) -> Path | None:
        raw = self._explicit_model or os.environ.get(MODEL_ENV)
        return Path(raw).expanduser() if raw else None

    def cli_module(self) -> str:
        return self._explicit_cli_module or os.environ.get(CLI_MODULE_ENV) or DEFAULT_CLI_MODULE

    def transcribe(
        self,
        audio_path: str,
        *,
        language: str = "auto",
        hotwords: Sequence[str] = (),
        timeout_seconds: float | None = None,
        cancel_event: CancellationSignal | None = None,
    ) -> AsrResult:
        asr_result, _ = self.transcribe_and_diarize(
            audio_path,
            language=language,
            hotwords=hotwords,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
        )
        return asr_result

    def diarize(
        self,
        audio_path: str,
        *,
        timeout_seconds: float | None = None,
        cancel_event: CancellationSignal | None = None,
    ) -> DiarizationResult:
        _, diarization_result = self.transcribe_and_diarize(
            audio_path,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
        )
        return diarization_result

    def transcribe_and_diarize(
        self,
        audio_path: str,
        *,
        language: str = "auto",
        hotwords: Sequence[str] = (),
        timeout_seconds: float | None = None,
        cancel_event: CancellationSignal | None = None,
    ) -> tuple[AsrResult, DiarizationResult]:
        del language  # MOSS infers language; retained for the shared provider contract.
        health = self.health_check()
        if not health.get("healthy"):
            raise MossMlxConfigurationError(str(health.get("detail") or health.get("status")))

        audio = Path(audio_path).expanduser()
        if not audio.is_file():
            raise MossMlxConfigurationError(f"Audio file does not exist: {audio}")

        timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        with tempfile.TemporaryDirectory(prefix="voice-studio-moss-mlx-") as output_dir:
            command = self._command(audio, Path(output_dir), hotwords=hotwords)
            process = (self._popen_factory or subprocess.Popen)(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, **self._environment},
            )
            stdout, stderr = _communicate_with_limits(
                process,
                timeout_seconds=timeout,
                cancel_event=cancel_event,
            )
            if process.returncode != 0:
                detail = (stderr or stdout or "MOSS MLX CLI failed").strip()
                raise MossMlxError(f"MOSS MLX CLI exited with code {process.returncode}: {detail[-2000:]}")
            payload = _read_segments_json(Path(output_dir) / "segments.json")

        asr_segments = normalize_asr_segments(payload, default_time_unit="seconds")
        diarization_segments = normalize_diarization_segments(payload, default_time_unit="seconds")
        duration_ms = max((segment.end_ms for segment in asr_segments), default=None)
        asr_result = AsrResult(
            provider_id=self.provider_id,
            text=" ".join(segment.text for segment in asr_segments),
            segments=asr_segments,
            duration_ms=duration_ms,
        )
        diarization_result = DiarizationResult(
            provider_id=self.provider_id,
            segments=diarization_segments,
            duration_ms=max((segment.end_ms for segment in diarization_segments), default=None),
        )
        return asr_result, diarization_result

    def _command(self, audio_path: Path, output_dir: Path, *, hotwords: Sequence[str]) -> list[str]:
        runtime = self.runtime_python()
        model = self.model_path()
        if runtime is None or model is None:
            raise MossMlxConfigurationError("MOSS MLX runtime or model is not configured")
        command = [
            runtime,
            "-m",
            self.cli_module(),
            str(audio_path),
            "--model",
            str(model),
            "--out-dir",
            str(output_dir),
            "--max-new-tokens",
            str(self.max_new_tokens),
            "--strict",
        ]
        cleaned_hotwords = [str(word).strip() for word in hotwords if str(word).strip()]
        if cleaned_hotwords:
            command.extend(
                [
                    "--prompt",
                    "Transcribe the audio with timestamps and speaker IDs. Hotwords: " + ", ".join(cleaned_hotwords),
                ]
            )
        return command


def _read_segments_json(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise MossMlxError("MOSS MLX CLI completed without segments.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MossMlxError(f"Could not parse MOSS segments.json: {exc}") from exc
    if isinstance(payload, dict):
        payload = payload.get("segments")
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise MossMlxError("MOSS segments.json must contain a list of segment objects")
    return payload


def _communicate_with_limits(
    process: Any,
    *,
    timeout_seconds: float,
    cancel_event: CancellationSignal | None,
) -> tuple[str, str]:
    started = time.monotonic()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _terminate_process(process)
            raise MossMlxCancelledError("MOSS MLX request was cancelled")
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            _terminate_process(process)
            raise MossMlxTimeoutError(f"MOSS MLX request timed out after {timeout_seconds:g} seconds")
        try:
            stdout, stderr = process.communicate(timeout=min(0.1, remaining))
            return stdout or "", stderr or ""
        except subprocess.TimeoutExpired:
            continue


def _terminate_process(process: Any) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
