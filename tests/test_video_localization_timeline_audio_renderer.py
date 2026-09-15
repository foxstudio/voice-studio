from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import timeline_audio_renderer  # noqa: E402
from app.errors import AppException  # noqa: E402
from app.services import audio_tools  # noqa: E402


def test_render_timeline_audio_supports_multiple_tracks_and_preserves_gaps(
    tmp_path: Path,
):
    background = tmp_path / "background.wav"
    dub = tmp_path / "dub.wav"
    audio_tools.write_audio(
        background,
        np.full(48_000, 0.1, dtype=np.float32),
        48_000,
    )
    audio_tools.write_audio(
        dub,
        np.full(48_000, 0.3, dtype=np.float32),
        48_000,
    )
    destination = tmp_path / "timeline.wav"
    request = timeline_audio_renderer.TimelineAudioRenderInput(
        timeline_duration_ms=3_000,
        channel_mode="mono",
        items=[
            timeline_audio_renderer.TimelineAudioRenderItem(
                item_id="background",
                source_id="background",
                track_id="background",
                timeline_start_ms=0,
                timeline_end_ms=1_000,
                source_start_ms=0,
                source_end_ms=1_000,
                gain=1,
            ),
            timeline_audio_renderer.TimelineAudioRenderItem(
                item_id="dub",
                source_id="dub",
                track_id="dub",
                timeline_start_ms=2_000,
                timeline_end_ms=3_000,
                source_start_ms=0,
                source_end_ms=1_000,
                gain=0.5,
            ),
        ],
    )

    result = timeline_audio_renderer.render_timeline_audio(
        request,
        source_paths={
            "background": background,
            "dub": dub,
        },
        output_path=destination,
    )

    rendered, sample_rate = audio_tools.read_audio(destination)
    assert result.duration_ms == 3_000
    assert result.item_count == 2
    assert result.channel_count == 1
    assert sample_rate == 48_000
    assert float(np.mean(rendered[:48_000])) == pytest.approx(
        0.1,
        abs=0.02,
    )
    assert float(np.max(np.abs(rendered[48_000:96_000]))) < 0.001
    assert float(np.mean(rendered[96_000:])) == pytest.approx(
        0.15,
        abs=0.02,
    )


def test_render_timeline_audio_accepts_every_current_audio_track_and_new_track_ids(
    tmp_path: Path,
):
    source_paths: dict[str, Path] = {}
    items: list[timeline_audio_renderer.TimelineAudioRenderItem] = []
    track_ids = [
        "original",
        "vocals",
        "background",
        "dub",
        "future-effects",
    ]
    for index, track_id in enumerate(track_ids):
        source_id = f"source-{index}"
        source_path = tmp_path / f"{source_id}.wav"
        audio_tools.write_audio(
            source_path,
            np.full(24_000, 0.1 + index * 0.05, dtype=np.float32),
            48_000,
        )
        source_paths[source_id] = source_path
        start_ms = 250 + index * 750
        items.append(
            timeline_audio_renderer.TimelineAudioRenderItem(
                item_id=f"item-{index}",
                source_id=source_id,
                track_id=track_id,
                timeline_start_ms=start_ms,
                timeline_end_ms=start_ms + 500,
                source_start_ms=0,
                source_end_ms=500,
            )
        )

    destination = tmp_path / "all-tracks.wav"
    result = timeline_audio_renderer.render_timeline_audio(
        timeline_audio_renderer.TimelineAudioRenderInput(
            timeline_duration_ms=4_000,
            channel_mode="mono",
            items=items,
        ),
        source_paths=source_paths,
        output_path=destination,
    )

    rendered, sample_rate = audio_tools.read_audio(destination)
    assert result.item_count == len(track_ids)
    assert result.duration_ms == 4_000
    assert sample_rate == 48_000
    assert len(rendered) == 192_000
    assert float(np.max(np.abs(rendered[:12_000]))) < 0.001
    for index in range(len(track_ids)):
        start = int(48_000 * (250 + index * 750) / 1_000)
        end = start + 24_000
        assert float(np.mean(rendered[start:end])) == pytest.approx(
            0.1 + index * 0.05,
            abs=0.02,
        )


def test_render_timeline_audio_fails_when_any_selected_source_is_missing(
    tmp_path: Path,
):
    request = timeline_audio_renderer.TimelineAudioRenderInput(
        timeline_duration_ms=1_000,
        items=[
            timeline_audio_renderer.TimelineAudioRenderItem(
                item_id="missing",
                source_id="missing",
                track_id="dub",
                timeline_start_ms=0,
                timeline_end_ms=1_000,
                source_start_ms=0,
                source_end_ms=1_000,
            )
        ],
    )

    with pytest.raises(AppException) as raised:
        timeline_audio_renderer.render_timeline_audio(
            request,
            source_paths={},
            output_path=tmp_path / "missing.wav",
        )

    assert raised.value.code == (
        "VIDEO_LOCALIZATION_TIMELINE_AUDIO_SOURCE_MISSING"
    )
    assert raised.value.detail_dict["item_ids"] == ["missing"]


@pytest.mark.parametrize("timeline_span,source_span,expected", [(5833, 5792, 5792), (1083, 1088, 1083)])
def test_editorial_projection_preserves_anchor_and_crop_start(timeline_span, source_span, expected):
    item = timeline_audio_renderer.TimelineAudioRenderItem.from_editorial_ranges(
        item_id="edit", source_id="source", track_id="dub",
        timeline_start_ms=374542, timeline_end_ms=374542 + timeline_span,
        source_start_ms=8750, source_end_ms=8750 + source_span,
    )
    assert item.timeline_start_ms == 374542
    assert item.source_start_ms == 8750
    assert item.timeline_end_ms == 374542 + expected
    assert item.source_end_ms == 8750 + expected


@pytest.mark.parametrize("timeline_end,source_end", [(100, 500), (99, 500), (600, 100), (600, 99)])
def test_editorial_projection_still_rejects_empty_or_reversed_ranges(timeline_end, source_end):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        timeline_audio_renderer.TimelineAudioRenderItem.from_editorial_ranges(
            item_id="edit", source_id="source", track_id="dub",
            timeline_start_ms=100, timeline_end_ms=timeline_end,
            source_start_ms=100, source_end_ms=source_end,
        )
