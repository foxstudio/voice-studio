from __future__ import annotations

from pathlib import Path
from typing import Literal, Mapping

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.video_localization import media_assets
from app.errors import AppException
from app.services import audio_tools


TIMELINE_AUDIO_RENDER_INPUT_SCHEMA_VERSION = (
    "timeline-audio-render-input-v1"
)
TIMELINE_AUDIO_RENDER_OUTPUT_SCHEMA_VERSION = (
    "timeline-audio-render-output-v1"
)
TARGET_SAMPLE_RATE = 48_000


class TimelineAudioRenderItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    track_id: str = Field(min_length=1)
    timeline_start_ms: int = Field(ge=0)
    timeline_end_ms: int = Field(gt=0)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    gain: float = Field(default=1, ge=0, le=4)

    @classmethod
    def from_editorial_ranges(
        cls,
        *,
        item_id: str,
        source_id: str,
        track_id: str,
        timeline_start_ms: int,
        timeline_end_ms: int,
        source_start_ms: int,
        source_end_ms: int,
        gain: float = 1,
    ) -> "TimelineAudioRenderItem":
        """Project an edit to its audible intersection without changing the edit.

        Editorial ranges can reserve more time than their source crop, or cut
        playback before the crop ends. Render at 1x up to the first boundary;
        the complete timeline mix owns any remaining silence. The strict
        render contract still rejects empty, negative and unequal ranges.
        """
        span_ms = min(
            timeline_end_ms - timeline_start_ms,
            source_end_ms - source_start_ms,
        )
        return cls(
            item_id=item_id,
            source_id=source_id,
            track_id=track_id,
            timeline_start_ms=timeline_start_ms,
            timeline_end_ms=timeline_start_ms + span_ms,
            source_start_ms=source_start_ms,
            source_end_ms=source_start_ms + span_ms,
            gain=gain,
        )

    @model_validator(mode="after")
    def validate_ranges(self) -> "TimelineAudioRenderItem":
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("时间线结束时间必须晚于开始时间。")
        if self.source_end_ms <= self.source_start_ms:
            raise ValueError("音频裁剪结束时间必须晚于开始时间。")
        timeline_span = (
            self.timeline_end_ms - self.timeline_start_ms
        )
        source_span = self.source_end_ms - self.source_start_ms
        if abs(timeline_span - source_span) > 1:
            raise ValueError(
                "时间线显示范围必须与源音频播放范围一致。"
            )
        return self


class TimelineAudioRenderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "timeline-audio-render-input-v1"
    ] = TIMELINE_AUDIO_RENDER_INPUT_SCHEMA_VERSION
    timeline_duration_ms: int = Field(ge=1)
    channel_mode: Literal["mono", "preserve"] = "preserve"
    items: list[TimelineAudioRenderItem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_timeline_bounds(self) -> "TimelineAudioRenderInput":
        outside = [
            item.item_id
            for item in self.items
            if item.timeline_end_ms > self.timeline_duration_ms
        ]
        if outside:
            raise ValueError(
                "时间线音频片段超出完整音频时长："
                + ", ".join(outside)
            )
        return self


class TimelineAudioRenderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "timeline-audio-render-output-v1"
    ] = TIMELINE_AUDIO_RENDER_OUTPUT_SCHEMA_VERSION
    audio_sha256: str = Field(min_length=64, max_length=64)
    duration_ms: int = Field(ge=1)
    sample_rate: int = Field(ge=1)
    channel_count: int = Field(ge=1)
    item_count: int = Field(ge=1)


def render_timeline_audio(
    request: TimelineAudioRenderInput,
    *,
    source_paths: Mapping[str, Path],
    output_path: Path,
) -> TimelineAudioRenderOutput:
    missing = [
        item.item_id
        for item in request.items
        if (
            item.source_id not in source_paths
            or not Path(source_paths[item.source_id]).is_file()
        )
    ]
    if missing:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TIMELINE_AUDIO_SOURCE_MISSING",
            (
                f"有 {len(missing)} 个时间线音频片段找不到源文件，"
                "已停止渲染。"
            ),
            {"item_ids": missing},
        )

    source_cache: dict[str, tuple[np.ndarray, int]] = {}
    for item in request.items:
        if item.source_id not in source_cache:
            source_cache[item.source_id] = (
                audio_tools.read_audio_multichannel(
                    source_paths[item.source_id]
                )
            )
    target_channels = (
        1
        if request.channel_mode == "mono"
        else max(
            _channel_count(audio)
            for audio, _ in source_cache.values()
        )
    )
    total_frames = max(
        1,
        int(
            TARGET_SAMPLE_RATE
            * request.timeline_duration_ms
            / 1_000
        ),
    )
    mixed = np.zeros(
        (
            (total_frames, target_channels)
            if target_channels > 1
            else (total_frames,)
        ),
        dtype=np.float32,
    )
    for item in request.items:
        audio, sample_rate = source_cache[item.source_id]
        source_start = min(
            len(audio),
            int(sample_rate * item.source_start_ms / 1_000),
        )
        source_end = min(
            len(audio),
            int(sample_rate * item.source_end_ms / 1_000),
        )
        timeline_duration_ms = (
            item.timeline_end_ms - item.timeline_start_ms
        )
        source_end = min(
            source_end,
            source_start
            + max(
                1,
                int(sample_rate * timeline_duration_ms / 1_000),
            ),
        )
        segment = audio[source_start:source_end]
        if not segment.size:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TIMELINE_AUDIO_RANGE_INVALID",
                f"时间线音频片段 {item.item_id} 没有可渲染的声音范围。",
                {"item_ids": [item.item_id]},
            )
        segment = _resample_audio(
            segment,
            sample_rate,
            TARGET_SAMPLE_RATE,
        )
        segment = _match_channels(segment, target_channels)
        target_start = int(
            TARGET_SAMPLE_RATE * item.timeline_start_ms / 1_000
        )
        target_end = min(
            len(mixed),
            int(
                TARGET_SAMPLE_RATE
                * item.timeline_end_ms
                / 1_000
            ),
            target_start + len(segment),
        )
        if target_end <= target_start:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TIMELINE_AUDIO_RANGE_INVALID",
                f"时间线音频片段 {item.item_id} 没有可写入的时间范围。",
                {"item_ids": [item.item_id]},
            )
        mixed[target_start:target_end] += (
            segment[: target_end - target_start] * item.gain
        )

    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.98:
        mixed *= 0.98 / peak
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    try:
        audio_tools.write_audio(
            output_path,
            mixed,
            TARGET_SAMPLE_RATE,
            fmt="wav",
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return TimelineAudioRenderOutput(
        audio_sha256=media_assets.file_sha256(output_path),
        duration_ms=request.timeline_duration_ms,
        sample_rate=TARGET_SAMPLE_RATE,
        channel_count=target_channels,
        item_count=len(request.items),
    )


def _channel_count(audio: np.ndarray) -> int:
    return audio.shape[1] if audio.ndim > 1 else 1


def _resample_audio(
    audio: np.ndarray,
    source_rate: int,
    target_rate: int,
) -> np.ndarray:
    if source_rate == target_rate or not audio.size:
        return audio.astype(np.float32)
    if audio.ndim <= 1:
        return _resample_channel(audio, source_rate, target_rate)
    channels = [
        _resample_channel(
            audio[:, index],
            source_rate,
            target_rate,
        )
        for index in range(audio.shape[1])
    ]
    length = min(len(channel) for channel in channels)
    return np.stack(
        [channel[:length] for channel in channels],
        axis=1,
    )


def _resample_channel(
    audio: np.ndarray,
    source_rate: int,
    target_rate: int,
) -> np.ndarray:
    next_length = max(
        1,
        int(len(audio) * target_rate / source_rate),
    )
    return np.interp(
        np.linspace(0, len(audio), next_length, endpoint=False),
        np.arange(len(audio)),
        audio,
    ).astype(np.float32)


def _match_channels(
    audio: np.ndarray,
    target_channels: int,
) -> np.ndarray:
    if target_channels <= 1:
        return (
            audio.mean(axis=1).astype(np.float32)
            if audio.ndim > 1
            else audio.astype(np.float32)
        )
    values = (
        audio[:, np.newaxis]
        if audio.ndim <= 1
        else audio
    ).astype(np.float32)
    if values.shape[1] == target_channels:
        return values
    if values.shape[1] == 1:
        return np.repeat(values, target_channels, axis=1)
    repeats = (
        target_channels + values.shape[1] - 1
    ) // values.shape[1]
    return np.tile(values, (1, repeats))[:, :target_channels]


__all__ = [
    "TIMELINE_AUDIO_RENDER_INPUT_SCHEMA_VERSION",
    "TIMELINE_AUDIO_RENDER_OUTPUT_SCHEMA_VERSION",
    "TimelineAudioRenderInput",
    "TimelineAudioRenderItem",
    "TimelineAudioRenderOutput",
    "render_timeline_audio",
]
