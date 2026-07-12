from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.engines.seed_audio.schemas import (  # noqa: E402
    SeedAudioAIGCMetadata,
    SeedAudioAudioConfig,
    SeedAudioReference,
    SeedAudioRequest,
    SeedAudioWatermark,
)


def test_text_audio_and_image_modes_accept_only_their_own_inputs():
    text = SeedAudioRequest(input_mode="text", text_prompt="雨夜里响起远处的钟声。")
    assert text.references == []

    audio = SeedAudioRequest(
        input_mode="audio",
        text_prompt="让@音频1平静地说：你好。",
        references=[SeedAudioReference(speaker="zh_female_vv_uranus_bigtts")],
    )
    assert audio.references[0].speaker == "zh_female_vv_uranus_bigtts"

    image = SeedAudioRequest(
        input_mode="image",
        text_prompt="画面中的人轻声说：你好。",
        references=[SeedAudioReference(image_url="https://assets.example.test/reference.webp")],
    )
    assert image.references[0].image_url is not None

    with pytest.raises(ValidationError, match="文字描述模式不能包含参考资源"):
        SeedAudioRequest(
            input_mode="text",
            text_prompt="测试",
            references=[SeedAudioReference(speaker="speaker-1")],
        )
    with pytest.raises(ValidationError, match="参考声音模式只能包含音频参考"):
        SeedAudioRequest(
            input_mode="audio",
            text_prompt="测试",
            references=[SeedAudioReference(image_url="https://example.test/a.png")],
        )
    with pytest.raises(ValidationError, match="参考图片模式只能包含一张图片"):
        SeedAudioRequest(
            input_mode="image",
            text_prompt="测试",
            references=[SeedAudioReference(speaker="speaker-1")],
        )


def test_prompt_and_reference_count_boundaries_are_strict():
    SeedAudioRequest(input_mode="text", text_prompt="字" * 3000)
    with pytest.raises(ValidationError):
        SeedAudioRequest(input_mode="text", text_prompt="字" * 3001)
    with pytest.raises(ValidationError):
        SeedAudioRequest(input_mode="text", text_prompt="   ")

    three = [SeedAudioReference(speaker=f"speaker-{index}") for index in range(3)]
    SeedAudioRequest(input_mode="audio", text_prompt="测试", references=three)
    with pytest.raises(ValidationError):
        SeedAudioRequest(
            input_mode="audio",
            text_prompt="测试",
            references=three + [SeedAudioReference(speaker="speaker-4")],
        )


@pytest.mark.parametrize(
    "reference",
    [
        {},
        {"speaker": "speaker-1", "audio_url": "https://example.test/ref.wav"},
        {"audio_data": "YXVkaW8=", "image_data": "aW1hZ2U="},
        {"image_data": "aW1hZ2U=", "image_url": "https://example.test/ref.png"},
    ],
)
def test_each_reference_has_exactly_one_source(reference: dict[str, str]):
    with pytest.raises(ValidationError, match="必须且只能提供一个参考来源"):
        SeedAudioReference(**reference)


def test_output_parameters_accept_documented_edges_and_reject_out_of_range_values():
    edge = SeedAudioAudioConfig(
        format="ogg_opus",
        sample_rate=48000,
        speech_rate=100,
        loudness_rate=-50,
        pitch_rate=12,
        enable_subtitle=True,
    )
    assert edge.model_dump() == {
        "format": "ogg_opus",
        "sample_rate": 48000,
        "speech_rate": 100,
        "loudness_rate": -50,
        "pitch_rate": 12,
        "enable_subtitle": True,
    }

    for field, invalid in (
        ("speech_rate", -51),
        ("speech_rate", 101),
        ("loudness_rate", -51),
        ("loudness_rate", 101),
        ("pitch_rate", -13),
        ("pitch_rate", 13),
    ):
        with pytest.raises(ValidationError):
            SeedAudioAudioConfig(**{field: invalid})
    with pytest.raises(ValidationError):
        SeedAudioAudioConfig(format="flac")
    with pytest.raises(ValidationError):
        SeedAudioAudioConfig(sample_rate=22050)


def test_models_forbid_unknown_fields_and_watermark_metadata_is_explicit():
    with pytest.raises(ValidationError):
        SeedAudioRequest(input_mode="text", text_prompt="测试", future_field=True)
    with pytest.raises(ValidationError):
        SeedAudioAudioConfig(speech_rate="10")

    watermark = SeedAudioWatermark(
        aigc_watermark=True,
        aigc_metadata=SeedAudioAIGCMetadata(
            enable=True,
            content_producer="Voice Studio",
            produce_id="job-1",
            content_propagator="local",
            propagate_id="task-1",
        ),
    )
    assert watermark.aigc_metadata.enable is True
