from __future__ import annotations

from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


SeedAudioInputMode = Literal["text", "audio", "image"]
SeedAudioOutputFormat = Literal["wav", "mp3", "pcm", "ogg_opus"]
class SeedAudioStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class SeedAudioReference(SeedAudioStrictModel):
    """One official reference entry plus optional local validation metadata.

    ``media_format``, ``duration_seconds`` and ``size_bytes`` are never sent to
    Volcengine. They allow callers that resolved a managed file to preserve the
    evidence required for local validation before building the API payload.
    """

    speaker: str | None = Field(default=None, min_length=1)
    audio_data: str | None = Field(default=None, min_length=1)
    audio_url: AnyHttpUrl | None = None
    image_data: str | None = Field(default=None, min_length=1)
    image_url: AnyHttpUrl | None = None
    media_format: str | None = None
    duration_seconds: float | None = Field(default=None, gt=0)
    size_bytes: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_exactly_one_source(self) -> "SeedAudioReference":
        sources = (self.speaker, self.audio_data, self.audio_url, self.image_data, self.image_url)
        if sum(value is not None for value in sources) != 1:
            raise ValueError("必须且只能提供一个参考来源")
        return self

    @property
    def is_audio(self) -> bool:
        return any(value is not None for value in (self.speaker, self.audio_data, self.audio_url))

    @property
    def is_image(self) -> bool:
        return any(value is not None for value in (self.image_data, self.image_url))

    def to_api_reference(self) -> dict[str, str]:
        for field in ("speaker", "audio_data", "audio_url", "image_data", "image_url"):
            value = getattr(self, field)
            if value is not None:
                return {field: str(value)}
        raise AssertionError("reference source was validated but is missing")


class SeedAudioAudioConfig(SeedAudioStrictModel):
    format: SeedAudioOutputFormat = "wav"
    sample_rate: Literal[8000, 16000, 24000, 32000, 44100, 48000] = 24000
    speech_rate: int = Field(default=0, ge=-50, le=100)
    loudness_rate: int = Field(default=0, ge=-50, le=100)
    pitch_rate: int = Field(default=0, ge=-12, le=12)
    enable_subtitle: bool = False


class SeedAudioAIGCMetadata(SeedAudioStrictModel):
    enable: bool = False
    content_producer: str | None = None
    produce_id: str | None = None
    content_propagator: str | None = None
    propagate_id: str | None = None


class SeedAudioWatermark(SeedAudioStrictModel):
    aigc_watermark: bool = False
    aigc_metadata: SeedAudioAIGCMetadata | None = None

    @property
    def enabled(self) -> bool:
        return self.aigc_watermark or bool(self.aigc_metadata and self.aigc_metadata.enable)


class SeedAudioRequest(SeedAudioStrictModel):
    model: Literal["seed-audio-1.0"] = "seed-audio-1.0"
    input_mode: SeedAudioInputMode
    text_prompt: str = Field(min_length=1, max_length=3000)
    references: list[SeedAudioReference] = Field(default_factory=list, max_length=3)
    audio_config: SeedAudioAudioConfig = Field(default_factory=SeedAudioAudioConfig)
    watermark: SeedAudioWatermark | None = None

    @model_validator(mode="after")
    def validate_mode_inputs(self) -> "SeedAudioRequest":
        if self.input_mode == "text":
            if self.references:
                raise ValueError("文字描述模式不能包含参考资源")
            return self

        if self.input_mode == "audio":
            if not self.references:
                raise ValueError("参考声音模式至少需要一条音频参考")
            if any(not reference.is_audio for reference in self.references):
                raise ValueError("参考声音模式只能包含音频参考")
            return self

        if len(self.references) != 1 or not self.references[0].is_image:
            raise ValueError("参考图片模式只能包含一张图片")
        return self
