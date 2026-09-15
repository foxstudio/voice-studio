"""Independent generated-speech evidence, shared by history and dubbing."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTENT_ASR_ENGINE = "qwen3-asr-mlx"
CONTENT_ASR_PROTOCOL = "open-asr-auto-no-hints-v1"


class TtsContentEvidence(BaseModel):
    """An observation of audio bytes, never a copy of the requested script."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["tts-content-evidence-v1"] = "tts-content-evidence-v1"
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_id: str = Field(min_length=1)
    protocol: Literal["open-asr-auto-no-hints-v1"] = "open-asr-auto-no-hints-v1"
    status: Literal["complete", "unavailable"]
    transcript: str = ""
    error_code: str | None = None

    def matches_audio(self, audio_sha256: str, *, engine_id: str = CONTENT_ASR_ENGINE) -> bool:
        """Only a complete observation of these exact bytes can be reused."""
        return bool(self.status == "complete" and self.transcript.strip()
                    and self.audio_sha256 == audio_sha256 and self.engine_id == engine_id
                    and self.protocol == CONTENT_ASR_PROTOCOL)
