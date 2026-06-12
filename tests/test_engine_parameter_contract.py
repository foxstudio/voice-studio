from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import AppSettings, BatchSegmentInput, BatchGenerateRequest, GenerateRequest
from app.services import batch_queue, inference_runner, task_queue


def _ref_file(tmp_path: Path) -> str:
    path = tmp_path / "reference.wav"
    path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    return str(path)


def _batch_payload(req: BatchGenerateRequest, output_dir: Path) -> dict:
    batch = batch_queue.BatchTask(
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        output_format=req.output_format,
        output_dir=str(output_dir),
        segments=batch_queue._result_segments(req),
        parameters=req.parameters,
    )
    segment_payload = batch_queue._runner_segments(req, batch, output_dir)[0]
    merged = dict(batch_queue._common_kwargs(req))
    merged.update(segment_payload["parameters"])
    merged["text"] = segment_payload["text"]
    merged["output_path"] = segment_payload["output_path"]
    return merged


def _enable_mimo(monkeypatch) -> None:
    settings = AppSettings(
        cloud_enabled=True,
        mimo_api_key_configured=True,
        mimo_default_voice="mimo_default",
        mimo_base_url="https://token-plan-cn.xiaomimimo.com/v1",
    )
    for module in [task_queue.settings_store, batch_queue.settings_store]:
        monkeypatch.setattr(module, "get", lambda: settings)
        monkeypatch.setattr(module, "mimo_api_key", lambda: "test-mimo-token")


def test_f5_single_batch_worker_payload_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_STUDIO_F5_TTS_ROOT", str(tmp_path / "F5-TTS"))

    params = {
        "speed": 1.23,
        "nfe_step": 33,
        "cfg_strength": 2.5,
        "target_rms": 0.12,
        "cross_fade_duration": 0.2,
        "sway_sampling_coef": -0.4,
        "fix_duration": 8.5,
        "remove_silence": True,
        "seed": 123,
    }
    reference_audio_path = _ref_file(tmp_path)
    req = GenerateRequest(
        text="测试文本-单次",
        engine_id="f5-tts",
        reference_audio_path=reference_audio_path,
        ref_text="我是一段参考文本。",
        **params,
    )
    single = task_queue._kwargs(req, str(tmp_path / "single.wav"))

    batch_req = BatchGenerateRequest(
        engine_id="f5-tts",
        reference_audio_path=reference_audio_path,
        ref_text="我是一段参考文本。",
        parameters=params,
        segments=[BatchSegmentInput(text="测试文本-批量")],
    )
    batch = _batch_payload(batch_req, tmp_path)

    for key, expected in params.items():
        assert single[key] == expected
        assert batch[key] == expected

    assert single["text"] == "测试文本-单次"
    assert single["reference_audio"] == reference_audio_path
    assert single["ref_text"] == "我是一段参考文本。"

    _, single_out, single_text, single_ref_audio, single_ref_text, speed, nfe_step, cfg_strength, target_rms, cross_fade_duration, sway_sampling_coef, fix_duration, remove_silence, seed = inference_runner._build_f5_tts_kwargs(**single)
    assert single_out == str(tmp_path / "single.wav")
    assert single_text == "测试文本-单次"
    assert single_ref_audio == reference_audio_path
    assert single_ref_text == "我是一段参考文本。"
    assert speed == params["speed"]
    assert nfe_step == params["nfe_step"]
    assert cfg_strength == params["cfg_strength"]
    assert target_rms == params["target_rms"]
    assert cross_fade_duration == params["cross_fade_duration"]
    assert sway_sampling_coef == params["sway_sampling_coef"]
    assert fix_duration == params["fix_duration"]
    assert remove_silence == params["remove_silence"]
    assert seed == params["seed"]

    _, batch_out, batch_text, batch_ref_audio, batch_ref_text, batch_speed, batch_nfe_step, batch_cfg_strength, batch_target_rms, batch_cross_fade_duration, batch_sway_sampling_coef, batch_fix_duration, batch_remove_silence, batch_seed = inference_runner._build_f5_tts_kwargs(**batch)
    assert batch["text"] == batch_text
    assert batch_out == batch["output_path"]
    assert batch_ref_audio == reference_audio_path
    assert batch_ref_text == "我是一段参考文本。"
    assert batch_speed == params["speed"]
    assert batch_nfe_step == params["nfe_step"]
    assert batch_cfg_strength == params["cfg_strength"]
    assert batch_target_rms == params["target_rms"]
    assert batch_cross_fade_duration == params["cross_fade_duration"]
    assert batch_sway_sampling_coef == params["sway_sampling_coef"]
    assert batch_fix_duration == params["fix_duration"]
    assert batch_remove_silence == params["remove_silence"]
    assert batch_seed == params["seed"]


def test_cosyvoice_zero_shot_single_batch_worker_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_STUDIO_COSYVOICE_ROOT", str(tmp_path / "CosyVoice"))

    params = {
        "speed": 1.15,
    }
    reference_audio_path = _ref_file(tmp_path)
    req = GenerateRequest(
        text="测试文本-单次",
        engine_id="cosyvoice-zero-shot",
        reference_audio_path=reference_audio_path,
        ref_text="一句中文参考文本。",
        **params,
    )
    single = task_queue._kwargs(req, str(tmp_path / "single.wav"))

    batch_req = BatchGenerateRequest(
        engine_id="cosyvoice-zero-shot",
        reference_audio_path=reference_audio_path,
        ref_text="一句中文参考文本。",
        parameters=params,
        segments=[BatchSegmentInput(text="测试文本-批量")],
    )
    batch = _batch_payload(batch_req, tmp_path)

    assert single["speed"] == params["speed"]
    assert batch["speed"] == params["speed"]
    assert single["reference_audio"] == reference_audio_path
    assert single["ref_text"] == "一句中文参考文本。"
    assert batch["reference_audio"] == reference_audio_path
    assert batch["ref_text"] == "一句中文参考文本。"

    _, single_out, single_text, single_ref_audio, single_ref_text, single_speed = inference_runner._build_cosyvoice_zero_shot_kwargs(**single)
    assert single_out == str(tmp_path / "single.wav")
    assert single_text == "测试文本-单次"
    assert single_ref_audio == reference_audio_path
    assert single_ref_text == "一句中文参考文本。"
    assert single_speed == params["speed"]

    _, batch_out, batch_text, batch_ref_audio, batch_ref_text, batch_speed = inference_runner._build_cosyvoice_zero_shot_kwargs(**batch)
    assert batch_out == batch["output_path"]
    assert batch_text == "测试文本-批量"
    assert batch_ref_audio == reference_audio_path
    assert batch_ref_text == "一句中文参考文本。"
    assert batch_speed == params["speed"]


def test_indextts_v2_single_batch_contract_for_required_parameters(tmp_path):
    params = {
        "speed": 1.08,
        "temperature": 0.52,
        "top_p": 0.91,
        "top_k": 40,
        "repetition_penalty": 12.0,
        "max_text_tokens_per_segment": 88,
        "interval_silence": 260,
        "segment_overlap_ms": 120,
        "seed": 2026,
        "diffusion_steps": 31,
        "cfg_rate": 0.77,
        "emo_alpha": 0.44,
        "emotion_mode": "emotion_vector",
        "emotion": "happy",
    }
    req = GenerateRequest(text="测试文本-单次", engine_id="indextts-v2", **params)
    req.reference_audio_path = _ref_file(tmp_path)
    single = task_queue._kwargs(req, str(tmp_path / "single.wav"))

    batch_req = BatchGenerateRequest(
        engine_id="indextts-v2",
        reference_audio_path=_ref_file(tmp_path),
        segments=[BatchSegmentInput(text="测试文本-批量")],
        parameters={k: v for k, v in params.items() if k != "emotion_mode"},
    )
    batch = _batch_payload(batch_req, tmp_path)

    expected_keys = [
        "speed",
        "temperature",
        "top_p",
        "top_k",
        "repetition_penalty",
        "max_text_tokens_per_segment",
        "interval_silence",
        "segment_overlap_ms",
        "seed",
        "diffusion_steps",
        "cfg_rate",
        "emotion",
        "emo_alpha",
    ]
    for key in expected_keys:
        assert key in single
        assert key in batch
        if key == "emotion":
            assert single[key] == params["emotion"]
            assert batch[key] == params["emotion"]
        else:
            assert single[key] == params[key]
            assert batch[key] == params[key]

    _, _, _, _, single_runner_kwargs = inference_runner._build_indextts_v2_kwargs(**single)
    _, _, _, _, batch_runner_kwargs = inference_runner._build_indextts_v2_kwargs(**batch)
    for key in expected_keys:
        expected = params["emotion"] if key == "emotion" else params[key]
        assert single_runner_kwargs[key] == expected
        assert batch_runner_kwargs[key] == expected


def test_mimo_profiles_preserve_independent_profiles(tmp_path, monkeypatch):
    _enable_mimo(monkeypatch)
    profile_cases = [
        ("mimo-v2.5-tts-preset", "mimo-v2.5-tts"),
        ("mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voicedesign"),
        ("mimo-v2.5-tts-voiceclone", "mimo-v2.5-tts-voiceclone"),
    ]
    shared_params = {
        "mimo_voice": "mimo_voice_profile",
        "voice_design_prompt": "低沉男声，缓慢节奏",
        "temperature": 0.62,
        "top_p": 0.93,
    }

    for engine_id, expected_model in profile_cases:
        single = GenerateRequest(
            text="测试文本",
            engine_id=engine_id,
            style_instruction="更稳重的叙述语气",
            optimize_text_preview=True,
            **shared_params,
        )
        single_kwargs = task_queue._kwargs(single, str(tmp_path / f"{engine_id}-single.wav"))
        batch_req = BatchGenerateRequest(
            engine_id=engine_id,
            parameters=shared_params,
            segments=[BatchSegmentInput(text="测试文本")],
        )
        batch_kwargs = _batch_payload(batch_req, tmp_path)

        assert single_kwargs["model"] == expected_model
        assert batch_kwargs["model"] == expected_model
        assert single_kwargs["voice"] == shared_params["mimo_voice"]
        assert batch_kwargs["mimo_voice"] == shared_params["mimo_voice"]


def test_mimo_top_level_style_instruction_is_normalized_between_single_and_batch(tmp_path, monkeypatch):
    _enable_mimo(monkeypatch)

    top_level_style = "更轻松的播音口吻"
    single = GenerateRequest(
        text="测试文本",
        engine_id="mimo-v2.5-tts-voicedesign",
        style_instruction=top_level_style,
        voice_design_prompt="软而亲近",
        temperature=0.6,
    )
    single_kwargs = task_queue._kwargs(single, str(tmp_path / "mimo-single.wav"))

    batch_req = BatchGenerateRequest(
        engine_id="mimo-v2.5-tts-voicedesign",
        parameters={"voice_design_prompt": "软而亲近", "style_instruction": top_level_style},
        segments=[BatchSegmentInput(text="测试文本")],
    )
    batch_kwargs = _batch_payload(batch_req, tmp_path)

    assert single_kwargs["instruction"] == top_level_style
    assert batch_kwargs["instruction"] == top_level_style
    assert batch_kwargs["style_instruction"] == top_level_style
    assert single_kwargs["voice_design_prompt"] == batch_kwargs["voice_design_prompt"]


def test_indextts_emotion_text_mode_is_normalized_between_single_and_batch(tmp_path):
    reference = _ref_file(tmp_path)
    single = GenerateRequest(
        text="测试文本",
        engine_id="indextts-v2",
        reference_audio_path=reference,
        emotion_mode="emotion_text",
        emotion_text="喜悦",
        emotion="happy",
        emo_alpha=0.6,
    )
    single_kwargs = task_queue._kwargs(single, str(tmp_path / "single.wav"))

    batch_req = BatchGenerateRequest(
        engine_id="indextts-v2",
        reference_audio_path=reference,
        parameters={"emotion_text": "喜悦", "emotion": "happy", "emotion_mode": "emotion_text", "emo_alpha": 0.6},
        segments=[BatchSegmentInput(text="测试文本")],
    )
    batch_kwargs = _batch_payload(batch_req, tmp_path)

    assert single_kwargs["emotion"] == batch_kwargs["emotion"]
    assert single_kwargs["emo_alpha"] == batch_kwargs["emo_alpha"]
