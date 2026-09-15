from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.engines.seed_audio.schemas import SeedAudioReference  # noqa: E402
from app.engines.seed_audio.validation import (  # noqa: E402
    SeedAudioValidationError,
    validate_prompt_references,
    validate_reference_constraints,
)


def test_local_audio_metadata_accepts_documented_limits():
    reference = SeedAudioReference(
        audio_data="YXVkaW8=",
        media_format="ogg_opus",
        duration_seconds=30,
        size_bytes=10 * 1024 * 1024,
    )
    validate_reference_constraints([reference])


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        (SeedAudioReference(audio_data="YQ==", media_format="flac"), "不支持的参考音频格式"),
        (SeedAudioReference(audio_data="YQ==", duration_seconds=30.01), "不能超过 30 秒"),
        (SeedAudioReference(audio_data="YQ==", size_bytes=10 * 1024 * 1024 + 1), "不能超过 10 MB"),
        (SeedAudioReference(image_data="YQ==", media_format="gif"), "不支持的参考图片格式"),
        (SeedAudioReference(image_data="YQ==", size_bytes=10 * 1024 * 1024 + 1), "不能超过 10 MB"),
    ],
)
def test_reference_metadata_rejects_documented_limit_violations(reference, message: str):
    with pytest.raises(SeedAudioValidationError, match=message):
        validate_reference_constraints([reference])


def test_speaker_and_remote_url_do_not_require_local_media_metadata():
    references = [
        SeedAudioReference(speaker="speaker-1"),
        SeedAudioReference(audio_url="https://example.test/reference.mp3"),
    ]
    validate_reference_constraints(references)


def test_prompt_reference_validation_tracks_used_unused_and_invalid_numbers():
    result = validate_prompt_references("让@音频2回答@音频1。", reference_count=3)
    assert result.used == (1, 2)
    assert result.unused == (3,)
    assert result.invalid == ()

    invalid = validate_prompt_references("让@音频1和@音频4对话。", reference_count=2)
    assert invalid.used == (1,)
    assert invalid.unused == (2,)
    assert invalid.invalid == (4,)
    with pytest.raises(SeedAudioValidationError, match="不存在的参考声音：@音频4"):
        invalid.raise_for_invalid()
