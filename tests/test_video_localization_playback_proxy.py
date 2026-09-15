from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    media_assets,
    media_work_coordinator,
    playback_proxy,
)
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import database, settings_store  # noqa: E402


def _configure(tmp_path: Path) -> None:
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    playback_proxy.cancel_all()


def test_browser_supported_source_is_ready_without_creating_proxy_work(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure(tmp_path)
    source = tmp_path / "source.webm"
    source.write_bytes(b"source-video")
    monkeypatch.setattr(
        media_assets,
        "probe_video",
        lambda _path: {"duration_ms": 90_000, "codec_name": "av1"},
    )
    build_calls: list[int] = []
    monkeypatch.setattr(
        media_assets,
        "build_playback_proxy_segment",
        lambda *_args, **_kwargs: build_calls.append(1),
    )

    status = playback_proxy.request_playback(
        "project-direct",
        source,
        source_playable=True,
        start_ms=45_000,
    )

    assert status["contract_version"] == "video-playback-proxy-status-v2"
    assert status["state"] == "ready"
    assert status["mode"] == "source"
    assert status["playable"] is True
    assert status["ready_ranges"] == [{"start_ms": 0, "end_ms": 90_000}]
    assert build_calls == []


def test_incompatible_source_publishes_requested_segment_before_background_fill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure(tmp_path)
    source = tmp_path / "source.mov"
    source.write_bytes(b"source-video")
    monkeypatch.setattr(
        media_assets,
        "probe_video",
        lambda _path: {"duration_ms": 20_000, "codec_name": "prores"},
    )
    built: list[int] = []

    def fake_build(_source, destination, *, start_ms, duration_ms, cancel_event):
        assert not cancel_event.is_set()
        built.append(start_ms)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"segment:{start_ms}:{duration_ms}".encode())

    monkeypatch.setattr(media_assets, "build_playback_proxy_segment", fake_build)

    initial = playback_proxy.request_playback(
        "project-segmented",
        source,
        source_playable=False,
        start_ms=8_000,
        end_ms=12_000,
        fill_background=False,
    )
    assert initial["state"] in {"building", "partial"}
    assert initial["mode"] == "segmented"

    deadline = time.monotonic() + 2
    status = initial
    while time.monotonic() < deadline:
        status = playback_proxy.playback_status("project-segmented", source)
        if status["playable"]:
            break
        time.sleep(0.01)

    assert built == [8_000]
    assert status["state"] == "partial"
    assert status["playable"] is True
    assert status["ready_segments"] == 1
    assert status["total_segments"] == 5
    assert status["ready_ranges"] == [{"start_ms": 8_000, "end_ms": 12_000}]
    segment = playback_proxy.segment_file(
        "project-segmented",
        source,
        2,
        revision=status["revision"],
    )
    assert segment is not None
    assert segment.read_bytes() == b"segment:8000:4000"
    assert playback_proxy.segment_file(
        "project-segmented",
        source,
        2,
        revision="stale-revision",
    ) is None


def test_complete_segment_cache_reuses_stable_revision_and_updates_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure(tmp_path)
    source = tmp_path / "source.mov"
    source.write_bytes(b"source-video")
    monkeypatch.setattr(
        media_assets,
        "probe_video",
        lambda _path: {"duration_ms": 8_000, "codec_name": "prores"},
    )
    root = playback_proxy._cache_root("project-complete", source)
    root.mkdir(parents=True)
    for index in range(2):
        (root / f"segment-{index:05d}.mp4").write_bytes(b"ready")
    monkeypatch.setattr(
        media_assets,
        "build_playback_proxy_segment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete cache must not start an encoder")
        ),
    )

    first = playback_proxy.request_playback(
        "project-complete",
        source,
        source_playable=False,
        start_ms=4_000,
        fill_background=True,
    )
    second = playback_proxy.request_playback(
        "project-complete",
        source,
        source_playable=False,
        start_ms=0,
        fill_background=True,
    )

    assert first["state"] == second["state"] == "ready"
    assert first["requested_range"]["start_ms"] == 4_000
    assert second["requested_range"]["start_ms"] == 0
    assert first["revision"] == second["revision"]
    assert "project-complete" not in playback_proxy._JOBS


def test_playback_segment_command_is_video_only_and_fragmented(tmp_path: Path) -> None:
    source = tmp_path / "source.mov"
    destination = tmp_path / "segment.mp4"

    command = media_assets.playback_proxy_segment_command(
        "/usr/bin/ffmpeg",
        source,
        destination,
        encoder="h264_videotoolbox",
        start_ms=12_000,
        duration_ms=4_000,
    )

    assert command[command.index("-ss") + 1] == "12.000"
    assert command[command.index("-t") + 1] == "4.000"
    assert command[command.index("-map") + 1] == "0:v:0"
    assert "-an" in command
    assert "0:a:0?" not in command
    assert command[command.index("-profile:v") + 1] == "high"
    assert command[command.index("-level:v") + 1] == "4.0"
    assert command[command.index("-movflags") + 1] == "+frag_keyframe+empty_moov+default_base_moof"


def test_new_seek_range_preempts_stale_lookahead_and_background(tmp_path: Path) -> None:
    source = tmp_path / "source.mov"
    source.write_bytes(b"source-video")
    job = playback_proxy.PlaybackProxyJob(
        project_id="project-priority",
        source_path=source,
        signature="signature",
        root=tmp_path / "proxy",
        duration_ms=80_000,
        generation="generation",
        requested_start_ms=0,
        requested_end_ms=12_000,
        fill_background=True,
    )
    playback_proxy._prioritize_segments(job, [0, 1, 2])
    playback_proxy._prioritize_segments(job, [10, 11, 12])

    assert [playback_proxy._next_segment(job)[0] for _ in range(4)] == [10, 11, 12, 0]


def test_media_work_coordinator_runs_waiting_interactive_work_first() -> None:
    coordinator = media_work_coordinator.MediaWorkCoordinator()
    release_active = threading.Event()
    active_started = threading.Event()
    order: list[str] = []

    def run(name: str, priority: media_work_coordinator.MediaWorkPriority) -> None:
        cancel = threading.Event()
        with coordinator.slot(priority, cancel) as acquired:
            assert acquired
            if name == "active":
                active_started.set()
                assert release_active.wait(timeout=2)
            order.append(name)

    active = threading.Thread(
        target=run,
        args=("active", media_work_coordinator.MediaWorkPriority.BACKGROUND),
    )
    preview = threading.Thread(
        target=run,
        args=("preview", media_work_coordinator.MediaWorkPriority.PREVIEW),
    )
    interactive = threading.Thread(
        target=run,
        args=("interactive", media_work_coordinator.MediaWorkPriority.INTERACTIVE),
    )
    active.start()
    assert active_started.wait(timeout=2)
    preview.start()
    interactive.start()
    time.sleep(0.05)
    release_active.set()
    for thread in (active, preview, interactive):
        thread.join(timeout=2)

    assert order == ["active", "interactive", "preview"]
