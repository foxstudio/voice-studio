from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from app.domains.video_localization.export_contracts import (
    VideoLocalizationMediaExportRequest,
)

_INVALID_FILENAME_CHARACTERS = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')
_MULTIPLE_HYPHENS = re.compile(r"-{2,}")
_WHITESPACE = re.compile(r"\s+")

_AUDIO_TRACK_LABELS = {
    "original": "原声",
    "vocals": "人声",
    "background": "背景",
    "dub": "配音",
}
_AUDIO_TRACK_ORDER = tuple(_AUDIO_TRACK_LABELS)
_VIDEO_QUALITY_LABELS = {
    "source": "原画质",
    "high": "高质量",
    "balanced": "均衡",
    "compact": "压缩",
}


@dataclass(frozen=True)
class ExportFilenameSpec:
    category: str
    content: str
    settings: tuple[str, ...] = ()
    extension: str = ""


def build_export_filename(
    project_name: str,
    spec: ExportFilenameSpec,
    *,
    exported_at: datetime,
    revision: str,
) -> str:
    """Build one readable, cross-platform export download name."""

    parts = [
        _filename_part(project_name, "未命名项目", max_length=56),
        _filename_part(spec.category, "导出"),
        _filename_part(spec.content, "成品"),
        *[
            _filename_part(setting, "设置")
            for setting in spec.settings
            if setting.strip()
        ],
        exported_at.strftime("%Y%m%d-%H%M%S"),
        f"版本-{_revision_label(revision)}",
    ]
    extension = re.sub(
        r"[^A-Za-z0-9]+",
        "",
        spec.extension.lstrip("."),
    ).lower() or "bin"
    return f"{'__'.join(parts)}.{extension}"


def media_export_filename_spec(
    request: VideoLocalizationMediaExportRequest,
    *,
    source_width: int | None = None,
    source_height: int | None = None,
) -> ExportFilenameSpec:
    if request.kind == "subtitle":
        return ExportFilenameSpec(
            category="字幕",
            content=_video_subtitle_label(request),
            extension="srt",
        )

    tracks = [
        _AUDIO_TRACK_LABELS[track_id]
        for track_id in _AUDIO_TRACK_ORDER
        if track_id in request.audio_tracks
    ]
    dub_lane_setting = _dub_lane_setting(request)
    if request.kind == "audio":
        bitrate = (
            "原码率"
            if request.audio_bitrate_kbps == "source"
            else f"{request.audio_bitrate_kbps}K"
        )
        settings = [f"{request.audio_format.upper()}-{bitrate}"]
        if dub_lane_setting:
            settings.append(dub_lane_setting)
        return ExportFilenameSpec(
            category="音频",
            content="+".join(tracks) or "无音轨",
            settings=tuple(settings),
            extension=request.audio_format,
        )

    content = _video_subtitle_label(request)
    settings = [
        (
            f"{_video_size_label(request, source_width, source_height)}"
            f"-{_VIDEO_QUALITY_LABELS[request.video_quality]}"
        ),
        f"音轨-{'+'.join(tracks) if tracks else '无音轨'}",
    ]
    if dub_lane_setting:
        settings.append(dub_lane_setting)
    return ExportFilenameSpec(
        category="视频",
        content=content,
        settings=tuple(settings),
        extension="mp4",
    )


def _video_subtitle_label(
    request: VideoLocalizationMediaExportRequest,
) -> str:
    labels: list[str] = []
    if "asr" in request.subtitle_tracks:
        labels.append("ASR")
    if "localized" in request.subtitle_tracks:
        labels.append(
            "配音字幕"
            if request.localized_subtitle_variant == "dub"
            else "本土化字幕"
        )
    return "+".join(labels) if labels else "无字幕"


def _video_size_label(
    request: VideoLocalizationMediaExportRequest,
    source_width: int | None,
    source_height: int | None,
) -> str:
    if request.video_size == "1080p":
        return "1080P"
    if request.video_size == "720p":
        return "720P"
    if (
        source_width is not None
        and source_height is not None
    ):
        if source_width >= 3840 and source_height >= 2160:
            return "4K"
        if source_width >= 1920 and source_height >= 1080:
            return "1080P"
        if source_width >= 1280 and source_height >= 720:
            return "720P"
    return "原尺寸"


def _dub_lane_setting(
    request: VideoLocalizationMediaExportRequest,
) -> str | None:
    if "dub" not in request.audio_tracks or not request.dub_lanes:
        return None
    lanes = "+".join(str(lane + 1) for lane in request.dub_lanes)
    return f"配音轨-{lanes}"


def _filename_part(
    value: str,
    fallback: str,
    *,
    max_length: int = 36,
) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _INVALID_FILENAME_CHARACTERS.sub("-", normalized)
    normalized = _WHITESPACE.sub(" ", normalized)
    normalized = re.sub(r"\s*-\s*", "-", normalized)
    normalized = _MULTIPLE_HYPHENS.sub("-", normalized)
    normalized = normalized.strip(" ._-")
    return (normalized or fallback)[:max_length].rstrip(" ._-")


def _revision_label(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "", value).upper()
    return (normalized or "000000")[:6].ljust(6, "0")


__all__ = [
    "ExportFilenameSpec",
    "build_export_filename",
    "media_export_filename_spec",
]
