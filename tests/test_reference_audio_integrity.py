from __future__ import annotations

import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.errors import AppException  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    BatchGenerateRequest,
    BatchSegmentInput,
    GenerateRequest,
    GenerationTask,
    HistoryItem,
    LongformGenerateRequest,
)
from app.services import (  # noqa: E402
    batch_queue,
    emotion_reference,
    longform_queue,
    reference_audio_integrity,
    task_queue,
    voice_store,
)


def _wav(path: Path, duration_ms: int) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\0\0" * round(24_000 * duration_ms / 1000))
    return str(path)


def _enable_mimo(monkeypatch) -> None:
    settings = AppSettings(
        cloud_enabled=True,
        mimo_api_key_configured=True,
        mimo_default_voice="mimo_default",
        mimo_base_url="https://api.xiaomimimo.com/v1",
    )
    monkeypatch.setattr(task_queue.settings_store, "get", lambda: settings)
    monkeypatch.setattr(task_queue.settings_store, "mimo_api_key", lambda: "test-mimo-token")


@pytest.mark.parametrize(
    ("engine_id", "reference_key"),
    [
        ("indextts-v2", "reference_audio"),
        ("omnivoice", "reference_audio"),
        ("confucius4-mlx-int8", "reference_audio"),
        ("qwen3-tts-mlx-0.6b", "reference_audio"),
        ("mimo-v2.5-tts-voiceclone", "reference_audio_path"),
        ("f5-tts", "reference_audio"),
        ("cosyvoice-zero-shot", "reference_audio"),
    ],
)
def test_all_direct_reference_engines_accept_matching_materialized_clip(
    tmp_path,
    monkeypatch,
    engine_id,
    reference_key,
):
    if engine_id.startswith("mimo-"):
        _enable_mimo(monkeypatch)
    clip = _wav(tmp_path / f"{engine_id}.wav", 3_000)
    request = GenerateRequest(
        text="所有直接参考型引擎共用完整性合同。",
        engine_id=engine_id,
        reference_audio_path=clip,
        ref_text="参考台词。",
        custom_reference_source_audio_path=str(tmp_path / "source.wav"),
        custom_reference_source_duration_ms=8_000,
        custom_reference_trim_start_ms=2_000,
        custom_reference_trim_end_ms=5_000,
    )

    kwargs = task_queue._kwargs(request, str(tmp_path / "out.wav"))

    assert kwargs[reference_key] == clip


def test_clip_duration_tolerance_accepts_small_codec_rounding(tmp_path):
    clip = _wav(tmp_path / "rounded.wav", 3_100)

    actual = reference_audio_integrity.validate_reference_clip(
        clip,
        source_duration_ms=10_000,
        trim_start_ms=2_000,
        trim_end_ms=5_000,
    )

    assert actual == 3_100


def test_clip_duration_mismatch_is_rejected(tmp_path):
    clip = _wav(tmp_path / "wrong-range.wav", 2_500)

    with pytest.raises(
        reference_audio_integrity.ReferenceAudioIntegrityError,
        match="REFERENCE_AUDIO_CLIP_DURATION_MISMATCH",
    ):
        reference_audio_integrity.validate_reference_clip(
            clip,
            source_duration_ms=10_000,
            trim_start_ms=2_000,
            trim_end_ms=5_000,
        )


def test_stale_short_clip_reports_range_mismatch_before_quality_warning(tmp_path):
    clip = _wav(tmp_path / "stale-short-range.wav", 1_420)

    with pytest.raises(
        reference_audio_integrity.ReferenceAudioIntegrityError,
        match="REFERENCE_AUDIO_CLIP_DURATION_MISMATCH",
    ):
        reference_audio_integrity.validate_reference_clip(
            clip,
            source_duration_ms=665_074,
            trim_start_ms=13_500,
            trim_end_ms=21_420,
        )


def test_materialized_clip_shorter_than_two_seconds_is_accepted(tmp_path):
    clip = _wav(tmp_path / "too-short.wav", 1_900)

    actual = reference_audio_integrity.validate_reference_clip(
        clip,
        source_duration_ms=5_000,
        trim_start_ms=0,
        trim_end_ms=1_900,
    )

    assert actual == 1_900


def test_short_reference_quality_warning_does_not_block_use():
    quality = voice_store._quality_for_voice_file(500)

    assert quality["passed"] is True
    assert quality["warnings"] == ["参考音频短于 2 秒"]


def test_legacy_reference_without_trim_metadata_remains_compatible(tmp_path):
    legacy = _wav(tmp_path / "legacy-short.wav", 500)
    request = GenerateRequest(
        text="旧记录没有裁切元数据。",
        engine_id="indextts-v2",
        reference_audio_path=legacy,
    )

    kwargs = task_queue._kwargs(request, str(tmp_path / "legacy-out.wav"))

    assert kwargs["reference_audio"] == legacy


def test_nonexistent_reference_keeps_original_error_contract(tmp_path):
    request = GenerateRequest(
        text="不存在的路径。",
        engine_id="f5-tts",
        reference_audio_path=str(tmp_path / "missing.wav"),
        ref_text="参考台词。",
        custom_reference_source_duration_ms=5_000,
        custom_reference_trim_start_ms=0,
        custom_reference_trim_end_ms=3_000,
    )

    with pytest.raises(AppException) as exc_info:
        task_queue._kwargs(request, str(tmp_path / "out.wav"))

    assert exc_info.value.code == "REFERENCE_AUDIO_NOT_FOUND"


def test_independent_emotion_reference_uses_same_materialized_clip_contract(tmp_path):
    speaker = _wav(tmp_path / "speaker.wav", 3_000)
    emotion = _wav(tmp_path / "emotion-mismatch.wav", 2_500)
    request = GenerateRequest(
        text="独立情绪参考也校验实际选区。",
        engine_id="indextts-v2",
        reference_audio_path=speaker,
        emotion_mode="emotion_reference",
        emotion_reference_audio_path=emotion,
        emotion_reference_source_duration_ms=8_000,
        emotion_reference_trim_start_ms=2_000,
        emotion_reference_trim_end_ms=5_000,
    )

    with pytest.raises(emotion_reference.EmotionReferenceError, match="EMOTION_REFERENCE_CLIP_DURATION_MISMATCH"):
        emotion_reference.resolve_generate_request(request)


def test_batch_common_and_segment_reference_paths_are_validated(tmp_path):
    common_clip = _wav(tmp_path / "common.wav", 2_500)
    segment_clip = _wav(tmp_path / "segment.wav", 2_400)
    common = BatchGenerateRequest(
        engine_id="indextts-v2",
        reference_audio_path=common_clip,
        parameters={
            "custom_reference_source_duration_ms": 8_000,
            "custom_reference_trim_start_ms": 2_000,
            "custom_reference_trim_end_ms": 5_000,
        },
        segments=[BatchSegmentInput(text="公共参考。")],
    )
    segment = BatchGenerateRequest(
        engine_id="indextts-v2",
        segments=[
            BatchSegmentInput(
                text="逐段参考。",
                reference_audio_path=segment_clip,
                parameters={
                    "custom_reference_source_duration_ms": 8_000,
                    "custom_reference_trim_start_ms": 1_000,
                    "custom_reference_trim_end_ms": 4_000,
                },
            )
        ],
    )

    with pytest.raises(ValueError, match="REFERENCE_AUDIO_CLIP_DURATION_MISMATCH"):
        batch_queue._common_kwargs(common)
    with pytest.raises(ValueError, match="REFERENCE_AUDIO_CLIP_DURATION_MISMATCH"):
        batch_queue._runner_segments(segment, batch_queue.BatchTask(segments=batch_queue._result_segments(segment)), tmp_path)


@pytest.mark.asyncio
async def test_longform_rejects_mismatched_clip_before_persisting(tmp_path, monkeypatch):
    clip = _wav(tmp_path / "longform-mismatch.wav", 2_500)
    request = LongformGenerateRequest(
        generate_request=GenerateRequest(
            text="第一句。第二句。",
            engine_id="indextts-v2",
            reference_audio_path=clip,
            custom_reference_source_duration_ms=8_000,
            custom_reference_trim_start_ms=2_000,
            custom_reference_trim_end_ms=5_000,
        ),
        verify_enabled=False,
        merge_enabled=False,
    )
    monkeypatch.setattr(longform_queue, "start_worker", lambda: None)

    with pytest.raises(AppException) as exc_info:
        await longform_queue.submit(request)

    assert exc_info.value.code == "REFERENCE_AUDIO_CLIP_DURATION_MISMATCH"


def test_retry_and_history_snapshots_remain_revalidatable(tmp_path):
    clip = _wav(tmp_path / "retry-history.wav", 2_500)
    parameters = GenerateRequest(
        text="恢复后仍重新校验。",
        engine_id="indextts-v2",
        reference_audio_path=clip,
        custom_reference_source_duration_ms=8_000,
        custom_reference_trim_start_ms=2_000,
        custom_reference_trim_end_ms=5_000,
    ).model_dump()
    task = GenerationTask(
        engine_id="indextts-v2",
        input_text="恢复后仍重新校验。",
        parameters=parameters,
    )
    history = HistoryItem(
        task_id=task.task_id,
        engine_id="indextts-v2",
        input_text="恢复后仍重新校验。",
        parameter_snapshot=parameters,
    )

    for snapshot in (task.parameters, history.parameter_snapshot):
        with pytest.raises(AppException) as exc_info:
            task_queue._kwargs(GenerateRequest(**snapshot), str(tmp_path / "retry-out.wav"))
        assert exc_info.value.code == "REFERENCE_AUDIO_CLIP_DURATION_MISMATCH"
