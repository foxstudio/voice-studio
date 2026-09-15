from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.export_contracts import (  # noqa: E402
    VideoLocalizationMediaExportRequest,
)
from app.domains.video_localization.export_filenames import (  # noqa: E402
    ExportFilenameSpec,
    build_export_filename,
    media_export_filename_spec,
)


def test_build_export_filename_is_readable_safe_and_versioned():
    filename = build_export_filename(
        ' 疯狂/飞机: "终极版" ',
        ExportFilenameSpec(
            category="视频",
            content="配音字幕",
            settings=("4K-原画质",),
            extension="mp4",
        ),
        exported_at=datetime(
            2026,
            8,
            4,
            14,
            30,
            25,
            tzinfo=timezone.utc,
        ),
        revision="a1b2c3",
    )

    assert filename == (
        "疯狂-飞机-终极版__视频__配音字幕__4K-原画质"
        "__20260804-143025__版本-A1B2C3.mp4"
    )
    assert not any(character in filename for character in '/\\:*?"<>|')


def test_media_export_filename_spec_describes_tracks_and_settings():
    request = VideoLocalizationMediaExportRequest(
        kind="video",
        audio_tracks=["dub", "background"],
        dub_lanes=[0, 2],
        subtitle_tracks=["asr", "localized"],
        localized_subtitle_variant="dub",
        video_size="source",
        video_quality="high",
    )

    spec = media_export_filename_spec(
        request,
        source_width=3840,
        source_height=2160,
    )

    assert spec == ExportFilenameSpec(
        category="视频",
        content="ASR+配音字幕",
        settings=(
            "4K-高质量",
            "音轨-背景+配音",
            "配音轨-1+3",
        ),
        extension="mp4",
    )


def test_audio_export_filename_spec_keeps_format_and_bitrate():
    request = VideoLocalizationMediaExportRequest(
        kind="audio",
        audio_tracks=["background", "dub"],
        dub_lanes=[1],
        audio_format="mp3",
        audio_bitrate_kbps=192,
    )

    spec = media_export_filename_spec(request)

    assert spec == ExportFilenameSpec(
        category="音频",
        content="背景+配音",
        settings=("MP3-192K", "配音轨-2"),
        extension="mp3",
    )


def test_subtitle_export_filename_spec_describes_selected_track():
    request = VideoLocalizationMediaExportRequest(
        kind="subtitle",
        subtitle_tracks=["localized"],
        localized_subtitle_variant="dub",
    )

    spec = media_export_filename_spec(request)

    assert spec == ExportFilenameSpec(
        category="字幕",
        content="配音字幕",
        extension="srt",
    )
