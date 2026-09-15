from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import media_health  # noqa: E402
from app.domains.video_localization import operation_state  # noqa: E402
from app.domains.video_localization import readiness  # noqa: E402
from app.domains.video_localization import source_pipeline  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationSourceMedia,
    VideoLocalizationStems,
)


def _draft(
    *,
    video_path: Path | None = None,
    source_audio_path: Path | None = None,
    original_audio_path: Path | None = None,
    vocals_path: Path | None = None,
    background_path: Path | None = None,
    filename: str | None = None,
    separation_status: str = "pending",
) -> VideoLocalizationDraft:
    return VideoLocalizationDraft(
        source_media=VideoLocalizationSourceMedia(
            filename=filename,
            video_path=str(video_path) if video_path is not None else None,
            audio_path=(
                str(source_audio_path) if source_audio_path is not None else None
            ),
        ),
        stems=VideoLocalizationStems(
            original_audio_path=(
                str(original_audio_path) if original_audio_path is not None else None
            ),
            vocals_clean_path=str(vocals_path) if vocals_path is not None else None,
            background_path=(
                str(background_path) if background_path is not None else None
            ),
            separation_status=separation_status,
        ),
    )


def test_media_health_distinguishes_unconfigured_missing_and_not_file(
    tmp_path: Path,
):
    missing = tmp_path / "missing.mp4"
    directory = tmp_path / "looks-like-video.mp4"
    directory.mkdir()

    unconfigured = media_health.inspect_project_media(_draft())
    missing_health = media_health.inspect_project_media(
        _draft(video_path=missing, filename="source.mp4")
    )
    directory_health = media_health.inspect_project_media(
        _draft(video_path=directory, filename="source.mp4")
    )

    assert unconfigured.health.source_video.status == "unconfigured"
    assert missing_health.health.source_video.status == "missing"
    assert directory_health.health.source_video.status == "not_file"
    assert unconfigured.paths.source_video is None
    assert missing_health.paths.source_video is None
    assert directory_health.paths.source_video is None


def test_source_audio_uses_available_fallback_candidate_everywhere(
    tmp_path: Path,
):
    stale_primary = tmp_path / "stale-source.wav"
    original_stem = tmp_path / "original.wav"
    original_stem.write_bytes(b"fixture-audio")
    draft = _draft(
        source_audio_path=stale_primary,
        original_audio_path=original_stem,
    )

    resolution = media_health.inspect_project_media(draft)

    assert resolution.health.source_audio.status == "available"
    assert resolution.health.source_audio.selected_source == "original_stem"
    assert resolution.paths.source_audio == original_stem.resolve()
    operation_state.validate_prerequisites("stems", draft)
    assert source_pipeline.resolve_english_asr_source(
        draft, "original"
    ) == (original_stem.resolve(), "original")


def test_partial_stems_are_reported_from_real_files(tmp_path: Path):
    source_audio = tmp_path / "source.wav"
    vocals = tmp_path / "vocals.wav"
    missing_background = tmp_path / "background.wav"
    source_audio.write_bytes(b"source")
    vocals.write_bytes(b"vocals")
    draft = _draft(
        source_audio_path=source_audio,
        vocals_path=vocals,
        background_path=missing_background,
        separation_status="completed",
    )

    resolution = media_health.inspect_project_media(draft)

    assert resolution.health.stems_status == "partial"
    assert resolution.health.vocals.status == "available"
    assert resolution.health.background.status == "missing"
    assert resolution.paths.vocals == vocals.resolve()
    assert resolution.paths.background is None


@pytest.mark.parametrize(
    ("draft", "expected_source_video", "expected_source_audio"),
    [
        (
            _draft(filename="metadata-only.mp4"),
            "blocked",
            "blocked",
        ),
    ],
)
def test_readiness_uses_media_health_not_metadata(
    draft: VideoLocalizationDraft,
    expected_source_video: str,
    expected_source_audio: str,
):
    audit = readiness.build_production_readiness_audit(
        project_id="project-health",
        project_name="Health",
        draft=draft,
    )
    checks = {item["code"]: item for item in audit["checks"]}

    assert checks["source_video"]["status"] == expected_source_video
    assert checks["source_audio"]["status"] == expected_source_audio
