from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services import engine_registry, faster_whisper_asr, mimo_client, qwen_mlx_asr, settings_store
from app.services.asr_providers.contracts import AsrResult, ProviderCapabilities
from app.services.asr_providers.normalizers import normalize_asr_segments


class BuiltinAsrProvider:
    capabilities = ProviderCapabilities(
        supports_transcription=True,
        supports_segment_timestamps=True,
        supports_language_selection=True,
        supports_long_audio=True,
    )

    def __init__(self, provider_id: str, transcribe_fn: Callable[[str, str], dict[str, Any]]) -> None:
        self.provider_id = provider_id
        self._transcribe_fn = transcribe_fn

    def health_check(self) -> dict[str, object]:
        return engine_registry.health_check(self.provider_id)

    def transcribe(
        self,
        audio_path: str,
        *,
        language: str = "auto",
        hotwords=(),
        timeout_seconds: float | None = None,
        cancel_event=None,
    ) -> AsrResult:
        del hotwords, timeout_seconds
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("ASR request was cancelled")
        raw = self._transcribe_fn(audio_path, language)
        segments = normalize_asr_segments(raw.get("segments") or [], default_time_unit="milliseconds")
        return AsrResult(
            provider_id=self.provider_id,
            text=str(raw.get("text") or "").strip() or " ".join(item.text for item in segments),
            segments=segments,
            duration_ms=max((item.end_ms for item in segments), default=None),
            metadata={
                "usage_seconds": raw.get("usage_seconds"),
                "provider_response_id": raw.get("provider_response_id"),
            },
        )


def qwen_provider() -> BuiltinAsrProvider:
    return BuiltinAsrProvider(
        "qwen3-asr-mlx",
        lambda audio_path, language: qwen_mlx_asr.transcribe_audio(
            audio_path=audio_path,
            language=language,
            model_path=str(settings_store.model_path("qwen3-asr-mlx")),
        ),
    )


def faster_whisper_provider() -> BuiltinAsrProvider:
    return BuiltinAsrProvider(
        "faster-whisper-turbo",
        lambda audio_path, language: faster_whisper_asr.transcribe_audio(
            audio_path=audio_path,
            language=language,
            model_path=str(settings_store.model_path("faster-whisper-turbo")),
        ),
    )


def mimo_provider() -> BuiltinAsrProvider:
    def transcribe(audio_path: str, language: str) -> dict[str, Any]:
        settings = settings_store.get()
        return mimo_client.transcribe_audio(
            base_url=settings.mimo_base_url,
            api_key=settings_store.mimo_api_key() or "",
            audio_path=audio_path,
            language=language,
        )

    return BuiltinAsrProvider("mimo-v2.5-asr", transcribe)


def providers() -> tuple[BuiltinAsrProvider, ...]:
    return qwen_provider(), faster_whisper_provider(), mimo_provider()
