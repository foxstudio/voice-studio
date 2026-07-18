from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_transcription: bool = False
    supports_diarization: bool = False
    supports_segment_timestamps: bool = False
    supports_word_timestamps: bool = False
    supports_language_selection: bool = False
    supports_hotwords: bool = False
    supports_long_audio: bool = False


@dataclass(frozen=True, slots=True)
class AsrSegment:
    start_ms: int
    end_ms: int
    text: str
    language: str | None = None
    speaker_cluster: str | None = None
    confidence: float | None = None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True, slots=True)
class AsrResult:
    provider_id: str
    text: str
    segments: tuple[AsrSegment, ...]
    language: str | None = None
    duration_ms: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DiarizationSegment:
    start_ms: int
    end_ms: int
    speaker_cluster: str
    confidence: float | None = None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True, slots=True)
class DiarizationResult:
    provider_id: str
    segments: tuple[DiarizationSegment, ...]
    duration_ms: int | None = None

    @property
    def speaker_clusters(self) -> tuple[str, ...]:
        return tuple(sorted({segment.speaker_cluster for segment in self.segments}))


@runtime_checkable
class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...


@runtime_checkable
class AsrProvider(Protocol):
    provider_id: str
    capabilities: ProviderCapabilities

    def health_check(self) -> dict[str, object]: ...

    def transcribe(
        self,
        audio_path: str,
        *,
        language: str = "auto",
        hotwords: Sequence[str] = (),
        timeout_seconds: float | None = None,
        cancel_event: CancellationSignal | None = None,
    ) -> AsrResult: ...


@runtime_checkable
class DiarizationProvider(Protocol):
    provider_id: str
    capabilities: ProviderCapabilities

    def health_check(self) -> dict[str, object]: ...

    def diarize(
        self,
        audio_path: str,
        *,
        timeout_seconds: float | None = None,
        cancel_event: CancellationSignal | None = None,
    ) -> DiarizationResult: ...
