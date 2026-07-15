from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import AppSettings, BatchSegmentInput, BatchGenerateRequest, GenerateRequest, VoiceAssetCreate, VoiceFile
from app.main import app
from app.services import audio_tools, batch_inference_runner, batch_queue, database, doubao_client, engine_manifests, engine_request_builder, inference_runner, task_queue, voice_store


def _ref_file(tmp_path: Path) -> str:
    path = tmp_path / "reference.wav"
    sf.write(path, np.zeros(2_205, dtype=np.float32), 22_050)
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
        mimo_base_url="https://api.xiaomimimo.com/v1",
    )
    for module in [task_queue.settings_store, batch_queue.settings_store]:
        monkeypatch.setattr(module, "get", lambda: settings)
        monkeypatch.setattr(module, "mimo_api_key", lambda: "test-mimo-token")


def _enable_doubao(monkeypatch) -> None:
    settings = AppSettings(
        cloud_enabled=True,
        doubao_api_key_configured=True,
        doubao_base_url="https://openspeech.bytedance.com",
        doubao_default_tts_resource_id="seed-tts-2.0",
    )
    for module in [task_queue.settings_store, batch_queue.settings_store]:
        monkeypatch.setattr(module, "get", lambda: settings)
        monkeypatch.setattr(module, "doubao_api_key", lambda: "test-doubao-token")


def test_index_tts_api_default_matches_manifest_default():
    request = GenerateRequest(text="参数默认值对账。", engine_id="indextts-v2")
    assert request.max_mel_tokens == 1500


def test_indextts_runtime_limits_match_api_and_visible_controls():
    manifest = engine_manifests.ENGINES["indextts-v2"].manifest
    params = {parameter.key: parameter for parameter in manifest.parameter_schema}

    assert params["speed"].max == 2.0
    assert params["max_mel_tokens"].min == 100
    assert params["max_mel_tokens"].max == 1815
    assert params["repetition_penalty"].min == 1.0
    assert params["emotion"].default == ""
    assert GenerateRequest(text="IndexTTS 参数上限。", engine_id="indextts-v2", speed=2.0, max_mel_tokens=1815).speed == 2.0

    with pytest.raises(ValueError):
        GenerateRequest(text="超出 IndexTTS 语速上限。", engine_id="indextts-v2", speed=2.01)
    with pytest.raises(ValueError):
        GenerateRequest(text="超出 IndexTTS 生成量上限。", engine_id="indextts-v2", max_mel_tokens=1816)
    with pytest.raises(ValueError):
        GenerateRequest(text="低于 IndexTTS 生成量下限。", engine_id="indextts-v2", max_mel_tokens=99)
    with pytest.raises(ValueError):
        GenerateRequest(text="低于重复抑制下限。", engine_id="indextts-v2", repetition_penalty=0.99)
    with pytest.raises(ValueError):
        BatchSegmentInput(text="批量任务也不能绕过语速上限。", speed=2.01)


def test_qwen3_manifest_and_runner_only_expose_runtime_supported_speakers_and_languages():
    manifest = engine_manifests.ENGINES["qwen3-tts-mlx-0.6b"].manifest
    params = {parameter.key: parameter for parameter in manifest.parameter_schema}
    speaker_ids = {option["value"] for option in params["speaker_id"].options}
    language_ids = {option["value"] for option in params["language"].options}

    assert speaker_ids == {"Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee"}
    assert {"auto", "chinese", "english", "japanese", "korean", "german", "italian", "portuguese", "spanish", "french", "russian"} == language_ids
    assert "cfg_scale" not in params
    assert "ddpm_steps" not in params
    assert "style_instruction" in params
    assert "只在使用 Qwen3 预置声音时生效" in params["style_instruction"].description
    assert inference_runner._normalize_qwen3_language("zh") == "chinese"
    assert inference_runner._normalize_qwen3_language("ja") == "japanese"
    assert inference_runner._normalize_qwen3_language("not-a-language") == "auto"


def test_emotivoice_manifest_matches_the_local_official_inference_route():
    """The bundled official frontend auto-detects Chinese/English text.

    Our subprocess route does not call its optional OpenAI API speed
    post-processor, so the visible manifest must not advertise speed.
    """
    manifest = engine_manifests.ENGINES["emotivoice"].manifest

    assert manifest.supported_languages == ["zh", "en"]
    assert {parameter.key for parameter in manifest.parameter_schema} == {"speaker_id", "prompt"}
    assert "上传一段音频" in next(parameter for parameter in manifest.parameter_schema if parameter.key == "speaker_id").description
    prompt = next(parameter for parameter in manifest.parameter_schema if parameter.key == "prompt")
    assert prompt.type == "text"
    assert "自己的提示" in prompt.description


def test_emotivoice_builders_do_not_claim_generic_speed_control(tmp_path):
    single = engine_request_builder.build_emotivoice_single_kwargs(
        GenerateRequest(text="EmotiVoice 只使用说话人和演绎提示。", engine_id="emotivoice", speaker_id="8051", prompt="开心", speed=1.5),
        str(tmp_path / "single.wav"),
    )
    batch = engine_request_builder.build_emotivoice_batch_common_kwargs({"speaker_id": "8051", "prompt": "开心", "speed": 1.5})

    assert single == {"text": "EmotiVoice 只使用说话人和演绎提示。", "output_path": str(tmp_path / "single.wav"), "speaker_id": "8051", "prompt": "开心"}
    assert batch == {"speaker_id": "8051", "prompt": "开心"}


def test_f5_target_rms_api_range_matches_the_visible_safe_range():
    manifest_keys = {parameter.key for parameter in engine_manifests.ENGINES["f5-tts"].manifest.parameter_schema}
    assert "seed" in manifest_keys
    assert GenerateRequest(text="F5 响度范围对账。", engine_id="f5-tts", target_rms=0.5).target_rms == 0.5
    with pytest.raises(ValueError):
        GenerateRequest(text="F5 响度范围对账。", engine_id="f5-tts", target_rms=0.51)


def test_doubao_explicit_watermark_tooltip_explains_the_real_audible_effect():
    """The provider's explicit watermark is an ending rhythm mark, not file metadata."""
    for engine_id in ("doubao-tts-preset", "doubao-tts-voiceclone", "doubao-seed-audio-1.0"):
        params = {parameter.key: parameter for parameter in engine_manifests.ENGINES[engine_id].manifest.parameter_schema}
        assert "结尾" in params["aigc_watermark"].description
        assert "节奏标记" in params["aigc_watermark"].description


def test_cosyvoice_sft_manifest_lists_every_speaker_in_the_installed_sft_model():
    speakers = {option["value"] for option in engine_manifests.ENGINES["cosyvoice-sft"].manifest.parameter_schema[0].options}
    assert speakers == {"中文女", "中文男", "粤语女", "日语男", "韩语女", "英文女", "英文男"}


def test_f5_fix_duration_and_fallback_defaults_match_the_visible_contract():
    request = GenerateRequest(text="F5 时长范围对账。", engine_id="f5-tts", fix_duration=30.0)
    assert request.fix_duration == 30.0
    with pytest.raises(ValueError):
        GenerateRequest(text="F5 时长范围对账。", engine_id="f5-tts", fix_duration=30.1)

    _, _, _, _, _, _, nfe_step, cfg_strength, _, _, sway, _, _, _ = inference_runner._build_f5_tts_kwargs(
        output_path="/tmp/f5-defaults.wav",
        text="F5 fallback defaults",
        reference_audio="/tmp/reference.wav",
        ref_text="reference text",
        sway_sampling_coef=0.0,
    )
    assert nfe_step == 32
    assert cfg_strength == 2.0
    assert sway == 0.0


def test_mimo_batch_falls_back_to_official_default_base_url(monkeypatch):
    settings = AppSettings(
        cloud_enabled=True,
        mimo_api_key_configured=True,
        mimo_base_url="",
        mimo_default_voice="mimo_default",
    )
    monkeypatch.setattr(engine_request_builder.settings_store, "get", lambda: settings)
    monkeypatch.setattr(engine_request_builder.settings_store, "mimo_api_key", lambda: "test-mimo-token")
    request = BatchGenerateRequest(
        engine_id="mimo-v2.5-tts-preset",
        parameters={},
        segments=[BatchSegmentInput(text="MiMo 批量参数对账。")],
    )

    kwargs = engine_request_builder.build_mimo_tts_batch_common_kwargs(request, reference_audio_path=None)

    assert kwargs["base_url"] == engine_request_builder.MIMO_DEFAULT_BASE_URL
    assert request.output_format == "wav"


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


def test_cosyvoice_zero_shot_rejects_reference_audio_longer_than_official_limit(tmp_path):
    short_reference = tmp_path / "reference-30s.wav"
    long_reference = tmp_path / "reference-30_1s.wav"
    sf.write(short_reference, np.zeros(22_050 * 30, dtype=np.float32), 22_050)
    sf.write(long_reference, np.zeros(int(22_050 * 30.1), dtype=np.float32), 22_050)

    allowed = GenerateRequest(
        text="30 秒参考音频仍可使用。",
        engine_id="cosyvoice-zero-shot",
        reference_audio_path=str(short_reference),
        ref_text="参考台词。",
    )
    assert task_queue._kwargs(allowed, str(tmp_path / "allowed.wav"))["reference_audio"] == str(short_reference)

    too_long = allowed.model_copy(update={"reference_audio_path": str(long_reference)})
    with pytest.raises(Exception) as single_error:
        task_queue._kwargs(too_long, str(tmp_path / "too-long.wav"))
    assert getattr(single_error.value, "code", None) == "COSYVOICE_REFERENCE_AUDIO_TOO_LONG"

    segment_override = BatchGenerateRequest(
        engine_id="cosyvoice-zero-shot",
        reference_audio_path=str(short_reference),
        ref_text="公共参考台词。",
        segments=[BatchSegmentInput(text="批量段落", reference_audio_path=str(long_reference), ref_text="分段参考台词。")],
    )
    with pytest.raises(ValueError, match="COSYVOICE_REFERENCE_AUDIO_TOO_LONG"):
        batch_queue._common_kwargs(segment_override)

    response = TestClient(app).post(
        "/api/generate",
        json={
            "text": "接口应在入队前拒绝超长参考音频。",
            "engine_id": "cosyvoice-zero-shot",
            "reference_audio_path": str(long_reference),
            "ref_text": "参考台词。",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "COSYVOICE_REFERENCE_AUDIO_TOO_LONG"

    batch_response = TestClient(app).post(
        "/api/batches/generate",
        json={
            "engine_id": "cosyvoice-zero-shot",
            "reference_audio_path": str(long_reference),
            "ref_text": "参考台词。",
            "segments": [{"text": "批量接口校验测试。"}],
        },
    )
    assert batch_response.status_code == 400
    assert batch_response.json()["error"]["code"] == "COSYVOICE_REFERENCE_AUDIO_TOO_LONG"


def test_cosyvoice_sft_single_and_batch_keep_the_selected_speaker_id(tmp_path, monkeypatch):
    """An unknown SFT speaker must reach the runtime validator unchanged.

    Do not replace it with a default while building either request type: that
    would make a failed selection look like a successful generation.
    """
    monkeypatch.setenv("VOICE_STUDIO_COSYVOICE_ROOT", str(tmp_path / "CosyVoice"))
    requested_speaker_id = "不存在的音色"
    req = GenerateRequest(
        text="测试文本-单次",
        engine_id="cosyvoice-sft",
        speaker_id=requested_speaker_id,
        speed=1.15,
    )
    single = task_queue._kwargs(req, str(tmp_path / "single.wav"))

    batch_req = BatchGenerateRequest(
        engine_id="cosyvoice-sft",
        parameters={"speaker_id": requested_speaker_id, "speed": 1.15},
        segments=[BatchSegmentInput(text="测试文本-批量")],
    )
    batch = _batch_payload(batch_req, tmp_path)

    assert single["speaker_id"] == requested_speaker_id
    assert batch["speaker_id"] == requested_speaker_id
    _, _, _, single_speaker_id, single_speed = inference_runner._build_cosyvoice_sft_kwargs(**single)
    _, _, _, batch_speaker_id, batch_speed = inference_runner._build_cosyvoice_sft_kwargs(**batch)
    assert single_speaker_id == requested_speaker_id
    assert batch_speaker_id == requested_speaker_id
    assert single_speed == batch_speed == 1.15


def test_cosyvoice_sft_nonpersistent_runner_rejects_unknown_speaker(tmp_path, monkeypatch):
    """The subprocess fallback must not reintroduce the old silent fallback."""
    root = tmp_path / "CosyVoice"
    package = root / "cosyvoice" / "cli"
    package.mkdir(parents=True)
    (root / "cosyvoice" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (root / "torch.py").write_text("", encoding="utf-8")
    (root / "torchaudio.py").write_text("", encoding="utf-8")
    (package / "cosyvoice.py").write_text(
        """
class AutoModel:
    def __init__(self, **_kwargs):
        pass

    def list_available_spks(self):
        return ["中文女", "中文男"]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(inference_runner, "_external_root", lambda _engine_id: root)
    monkeypatch.setattr(inference_runner, "_external_python", lambda _root: sys.executable)

    with pytest.raises(RuntimeError, match="COSYVOICE_SPEAKER_NOT_FOUND") as exc:
        inference_runner.run_cosyvoice_sft(
            output_path=str(tmp_path / "should-not-exist.wav"),
            text="这是一段测试文本。",
            speaker_id="不存在的音色",
            speed=1.0,
        )

    assert "请从音色列表中重新选择" in str(exc.value)
    assert "中文女、中文男" in str(exc.value)


def test_omnivoice_speed_uses_postprocess_stretch_not_model_speed(tmp_path):
    req = GenerateRequest(
        text="OmniVoice speed should preserve full text coverage.",
        engine_id="omnivoice",
        speed=2.0,
        duration=0.0,
    )
    single = task_queue._kwargs(req, str(tmp_path / "single.wav"))

    _, output_path, _, gen_kwargs, postprocess_speed, target_duration = inference_runner._build_omnivoice_kwargs(**single)

    assert output_path == str(tmp_path / "single.wav")
    assert "speed" not in gen_kwargs
    assert "duration" not in gen_kwargs
    assert gen_kwargs["text"] == req.text
    assert postprocess_speed == 2.0
    assert target_duration is None


def test_omnivoice_fixed_duration_overrides_postprocess_speed(tmp_path):
    _, _, _, gen_kwargs, postprocess_speed, target_duration = inference_runner._build_omnivoice_kwargs(
        output_path=str(tmp_path / "single.wav"),
        text="Duration should be applied after full text generation.",
        speed=2.0,
        duration=4.0,
    )

    assert "duration" not in gen_kwargs
    assert "speed" not in gen_kwargs
    assert postprocess_speed == 1.0
    assert target_duration == 4.0


def test_omnivoice_uses_its_own_default_steps_without_overwriting_an_explicit_api_value(tmp_path):
    default_request = GenerateRequest(text="OmniVoice 默认步数。", engine_id="omnivoice")
    default_kwargs = engine_request_builder.build_omnivoice_single_kwargs(
        default_request,
        str(tmp_path / "default.wav"),
        reference_audio=None,
        ref_text=None,
        model_dir=str(tmp_path / "model"),
    )
    explicit_request = GenerateRequest(text="OmniVoice 自定义步数。", engine_id="omnivoice", diffusion_steps=25)
    explicit_kwargs = engine_request_builder.build_omnivoice_single_kwargs(
        explicit_request,
        str(tmp_path / "explicit.wav"),
        reference_audio=None,
        ref_text=None,
        model_dir=str(tmp_path / "model"),
    )

    assert default_kwargs["diffusion_steps"] == 32
    assert explicit_kwargs["diffusion_steps"] == 25

    batch_default = _batch_payload(
        BatchGenerateRequest(engine_id="omnivoice", segments=[BatchSegmentInput(text="OmniVoice 批量默认步数。")]),
        tmp_path,
    )
    batch_explicit = _batch_payload(
        BatchGenerateRequest(
            engine_id="omnivoice",
            parameters={"diffusion_steps": 25},
            segments=[BatchSegmentInput(text="OmniVoice 批量自定义步数。")],
        ),
        tmp_path,
    )
    assert batch_default["diffusion_steps"] == 32
    assert batch_explicit["diffusion_steps"] == 25


def test_omnivoice_forwards_every_exposed_generation_config_control(tmp_path):
    controls = {
        "t_shift": 0.25,
        "layer_penalty_factor": 3.0,
        "position_temperature": 4.0,
        "class_temperature": 0.5,
        "denoise": False,
        "preprocess_prompt": False,
        "postprocess_output": False,
    }
    request = GenerateRequest(
        text="OmniVoice 官方生成配置直达测试。",
        engine_id="omnivoice",
        engine_parameters=controls,
    )
    single = engine_request_builder.build_omnivoice_single_kwargs(
        request,
        str(tmp_path / "single.wav"),
        reference_audio=None,
        ref_text=None,
        model_dir=str(tmp_path / "model"),
    )
    _, _, _, generation, _, _ = inference_runner._build_omnivoice_kwargs(**single)
    assert generation["generation_config"] == {"num_step": 32, "guidance_scale": 2.0, "audio_chunk_duration": 15.0, "audio_chunk_threshold": 30.0, **controls}

    batch = engine_request_builder.build_omnivoice_batch_common_kwargs(
        {"diffusion_steps": 32, "guidance_scale": 2.0, "audio_chunk_duration": 15.0, "audio_chunk_threshold": 30.0, **controls},
        reference_audio=None,
        ref_text=None,
        language="auto",
        model_dir=str(tmp_path / "model"),
    )
    _, _, _, batch_generation, _, _ = inference_runner._build_omnivoice_kwargs(text="批量直达测试。", output_path=str(tmp_path / "batch.wav"), **batch)
    assert batch_generation["generation_config"] == {"num_step": 32, "guidance_scale": 2.0, "audio_chunk_duration": 15.0, "audio_chunk_threshold": 30.0, **controls}


def test_omnivoice_builder_does_not_forward_generic_controls_the_runtime_cannot_consume(tmp_path):
    request = GenerateRequest(
        text="OmniVoice 只接收自己支持的控制项。",
        engine_id="omnivoice",
        temperature=1.2,
        top_p=0.3,
        top_k=77,
        repetition_penalty=13.0,
        seed=2026,
        emotion="happy",
        emo_alpha=0.9,
    )
    single = engine_request_builder.build_omnivoice_single_kwargs(
        request,
        str(tmp_path / "single.wav"),
        reference_audio=None,
        ref_text=None,
        model_dir=str(tmp_path / "model"),
    )
    batch = engine_request_builder.build_omnivoice_batch_common_kwargs(
        {
            "temperature": 1.2,
            "top_p": 0.3,
            "top_k": 77,
            "repetition_penalty": 13.0,
            "seed": 2026,
            "emotion": "happy",
            "emo_alpha": 0.9,
            "speed": 1.1,
            "diffusion_steps": 20,
            "guidance_scale": 2.5,
        },
        reference_audio=None,
        ref_text=None,
        language="auto",
        model_dir=str(tmp_path / "model"),
    )

    unsupported = {"temperature", "top_p", "top_k", "repetition_penalty", "max_text_tokens_per_segment", "interval_silence", "segment_overlap_ms", "seed", "max_mel_tokens", "cfg_rate", "emotion", "emo_alpha"}
    assert unsupported.isdisjoint(single)
    assert unsupported.isdisjoint(batch)
    assert batch["speed"] == 1.1
    assert batch["diffusion_steps"] == 20
    assert batch["guidance_scale"] == 2.5


def test_audio_time_stretch_file_changes_duration_without_pitch_flag(tmp_path):
    sr = 24000
    path = tmp_path / "tone.wav"
    timeline = np.linspace(0, 1, sr, endpoint=False, dtype=np.float32)
    audio = 0.2 * np.sin(2 * np.pi * 440 * timeline)
    sf.write(path, audio, sr, subtype="PCM_16")

    meta = audio_tools.time_stretch_file(path, 2.0)

    assert 450 <= meta["duration_ms"] <= 650


def test_confucius4_single_batch_worker_payload_contract(tmp_path, monkeypatch):
    model_dir = tmp_path / "confucius4-model"
    monkeypatch.setattr(task_queue.settings_store, "model_path", lambda engine_id: model_dir)
    monkeypatch.setattr(batch_queue.settings_store, "model_path", lambda engine_id: model_dir)

    params = {
        "language": "en",
        "temperature": 0.66,
        # Both zero values are legal and must survive the single and batch
        # request builders all the way into the Confucius4 runner.
        "top_p": 0.0,
        "top_k": 22,
        "repetition_penalty": 9.5,
        "diffusion_steps": 31,
        "cfg_rate": 0.0,
        "seed": 2026,
    }
    reference_audio_path = _ref_file(tmp_path)
    req = GenerateRequest(
        text="Confucius4 single test",
        engine_id="confucius4-mlx-int8",
        reference_audio_path=reference_audio_path,
        **params,
    )
    single = task_queue._kwargs(req, str(tmp_path / "single.wav"))

    batch_req = BatchGenerateRequest(
        engine_id="confucius4-mlx-int8",
        reference_audio_path=reference_audio_path,
        language="en",
        parameters=params,
        segments=[BatchSegmentInput(text="Confucius4 batch test")],
    )
    batch = _batch_payload(batch_req, tmp_path)

    for payload, text in [(single, "Confucius4 single test"), (batch, "Confucius4 batch test")]:
        assert payload["text"] == text
        assert payload["reference_audio"] == reference_audio_path
        assert payload["model_dir"] == str(model_dir)
        for key, expected in params.items():
            assert payload[key] == expected

        _, out, parsed_text, ref_audio, parsed_model_dir, _, language, temperature, top_k, top_p, repetition_penalty, diffusion_steps, cfg_rate, seed = inference_runner._build_confucius4_mlx_kwargs(**payload)
        assert out == payload["output_path"]
        assert parsed_text == text
        assert ref_audio == reference_audio_path
        assert parsed_model_dir == model_dir
        assert language == params["language"]
        assert temperature == params["temperature"]
        assert top_k == params["top_k"]
        assert top_p == params["top_p"]
        assert repetition_penalty == params["repetition_penalty"]
        assert diffusion_steps == params["diffusion_steps"]
        assert cfg_rate == params["cfg_rate"]
        assert seed == params["seed"]


def test_confucius4_language_options_match_the_pinned_runtime_and_reject_fallbacks(tmp_path):
    manifest = engine_manifests.ENGINES["confucius4-mlx-int8"].manifest
    visible = tuple(item["value"] for item in next(parameter for parameter in manifest.parameter_schema if parameter.key == "language").options)
    assert visible == ("zh", "en", "vi", "ja", "ko", "th")
    assert tuple(manifest.supported_languages) == visible

    with pytest.raises(ValueError, match="CONFUCIUS4_LANGUAGE_UNSUPPORTED"):
        engine_request_builder.build_confucius4_mlx_single_kwargs(
            GenerateRequest(text="不应静默回退为英文。", engine_id="confucius4-mlx-int8", language="de"),
            str(tmp_path / "single.wav"),
            reference_audio=_ref_file(tmp_path),
            model_dir=str(tmp_path / "model"),
        )


def test_qwen3_tts_single_batch_worker_payload_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_STUDIO_QWEN3_TTS_ROOT", str(inference_runner.qwen3_tts_paths.DEFAULT_ROOT))

    # The visible Qwen control allows 200 candidates, so the shared request
    # schema must accept the same upper bound instead of rejecting it at 100.
    assert GenerateRequest(text="Qwen3 top-k boundary", engine_id="qwen3-tts-mlx-0.6b", top_k=200).top_k == 200

    params = {
        "language": "zh",
        "speaker_id": "Vivian",
        "style_instruction": "自然清晰",
        "voice_design_prompt": "低沉、平静的旁白声线",
        "speed": 1.1,
        "temperature": 0.7,
        "top_p": 0.88,
        "top_k": 48,
        "repetition_penalty": 1.15,
        "max_tokens": 900,
        "cfg_scale": 1.2,
        "ddpm_steps": 12,
    }
    reference_audio_path = _ref_file(tmp_path)
    req = GenerateRequest(
        text="Qwen3 single test",
        engine_id="qwen3-tts-mlx-0.6b",
        reference_audio_path=reference_audio_path,
        ref_text="参考台词。",
        **params,
    )
    single = task_queue._kwargs(req, str(tmp_path / "single.wav"))

    batch_req = BatchGenerateRequest(
        engine_id="qwen3-tts-mlx-0.6b",
        reference_audio_path=reference_audio_path,
        ref_text="参考台词。",
        language="zh",
        parameters=params,
        segments=[BatchSegmentInput(text="Qwen3 batch test")],
    )
    batch = _batch_payload(batch_req, tmp_path)

    for payload, text in [(single, "Qwen3 single test"), (batch, "Qwen3 batch test")]:
        assert payload["text"] == text
        assert payload["reference_audio"] == reference_audio_path
        assert payload["ref_text"] == "参考台词。"
        assert payload["language"] == params["language"]
        assert payload["speaker_id"] is None
        assert payload["style_instruction"] is None
        assert payload["voice_design_prompt"] is None
        assert payload["speed"] == params["speed"]
        assert payload["temperature"] == params["temperature"]
        assert payload["top_p"] == params["top_p"]
        assert payload["top_k"] == params["top_k"]
        assert payload["repetition_penalty"] == params["repetition_penalty"]
        assert payload["max_tokens"] == params["max_tokens"]
        assert "cfg_scale" not in payload
        assert "ddpm_steps" not in payload

        root, out, parsed_text, ref_audio, ref_text, language, speaker_id, instruction, voice_design_prompt, speed, temperature, top_p, top_k, repetition_penalty, max_tokens = inference_runner._build_qwen3_tts_kwargs(**payload)
        assert root == inference_runner.qwen3_tts_paths.DEFAULT_ROOT
        assert out == payload["output_path"]
        assert parsed_text == text
        assert ref_audio == reference_audio_path
        assert ref_text == "参考台词。"
        assert language == "chinese"
        assert speaker_id == "Vivian"
        assert instruction == "Normal tone"
        assert voice_design_prompt == ""
        assert speed == params["speed"]
        assert temperature == params["temperature"]
        assert top_p == params["top_p"]
        assert top_k == params["top_k"]
        assert repetition_penalty == params["repetition_penalty"]
        assert max_tokens == params["max_tokens"]

    design_req = GenerateRequest(
        text="Qwen3 design test",
        engine_id="qwen3-tts-mlx-0.6b",
        speaker_id="Vivian",
        style_instruction="自然清晰",
        voice_design_prompt="年轻中文女声，吐字清晰",
    )
    design = task_queue._kwargs(design_req, str(tmp_path / "design.wav"))
    assert design["reference_audio"] is None
    assert design["speaker_id"] is None
    assert design["style_instruction"] is None
    assert design["voice_design_prompt"] == "年轻中文女声，吐字清晰"

    preset_req = GenerateRequest(
        text="Qwen3 preset test",
        engine_id="qwen3-tts-mlx-0.6b",
        speaker_id="Vivian",
        style_instruction="自然清晰",
        voice_design_prompt=None,
    )
    preset = task_queue._kwargs(preset_req, str(tmp_path / "preset.wav"))
    assert preset["reference_audio"] is None
    assert preset["speaker_id"] == "Vivian"
    assert preset["style_instruction"] == "自然清晰"
    assert preset["voice_design_prompt"] is None

    _, _, _, _, _, _, _, preset_instruction, preset_voice_design_prompt, *_ = inference_runner._build_qwen3_tts_kwargs(**preset)
    assert preset_instruction == "自然清晰"
    assert preset_voice_design_prompt == ""


def test_qwen3_reference_route_requires_accurate_reference_text(tmp_path):
    reference = _ref_file(tmp_path)
    request = GenerateRequest(
        text="Qwen3 reference test",
        engine_id="qwen3-tts-mlx-0.6b",
        reference_audio_path=reference,
    )

    with pytest.raises(Exception) as exc:
        task_queue._kwargs(request, str(tmp_path / "qwen3.wav"))

    assert getattr(exc.value, "code", None) == "REFERENCE_TEXT_REQUIRED"


def test_qwen3_batch_customvoice_preserves_segment_style_instruction_and_other_routes_remove_it(tmp_path):
    custom = BatchGenerateRequest(
        engine_id="qwen3-tts-mlx-0.6b",
        segments=[BatchSegmentInput(text="CustomVoice batch test", style_instruction="更温柔、更耐心地讲解")],
    )
    custom_payload = _batch_payload(custom, tmp_path)

    # Batch payload omits the default speaker; the runtime supplies Vivian,
    # matching the single-request CustomVoice fallback.
    assert custom_payload["speaker_id"] is None
    assert custom_payload["style_instruction"] == "更温柔、更耐心地讲解"
    *_prefix, runtime_speaker, instruction, voice_design_prompt, _speed, _temperature, _top_p, _top_k, _repetition_penalty, _max_tokens = inference_runner._build_qwen3_tts_kwargs(**custom_payload)
    assert runtime_speaker == "Vivian"
    assert instruction == "更温柔、更耐心地讲解"
    assert voice_design_prompt == ""

    design = BatchGenerateRequest(
        engine_id="qwen3-tts-mlx-0.6b",
        parameters={"style_instruction": "不应与声音描述混用", "voice_design_prompt": "低沉平静的旁白声线"},
        segments=[BatchSegmentInput(text="VoiceDesign batch test")],
    )
    design_payload = _batch_payload(design, tmp_path)
    assert design_payload["speaker_id"] is None
    assert design_payload["style_instruction"] is None
    assert design_payload["voice_design_prompt"] == "低沉平静的旁白声线"

    reference_audio = _ref_file(tmp_path)
    reference = BatchGenerateRequest(
        engine_id="qwen3-tts-mlx-0.6b",
        reference_audio_path=reference_audio,
        ref_text="准确的参考台词。",
        parameters={"style_instruction": "不应与参考音色混用"},
        segments=[BatchSegmentInput(text="Reference batch test")],
    )
    reference_payload = _batch_payload(reference, tmp_path)
    assert reference_payload["speaker_id"] is None
    assert reference_payload["style_instruction"] is None
    assert reference_payload["voice_design_prompt"] is None


def test_qwen3_speed_is_postprocessed_into_a_real_duration_change(tmp_path, monkeypatch):
    root = tmp_path / "qwen3-runtime"
    model_dir = tmp_path / "qwen3-model"
    model_dir.mkdir()
    output_path = tmp_path / "qwen3.wav"

    monkeypatch.setattr(inference_runner, "_external_root", lambda engine_id: root)
    monkeypatch.setattr(inference_runner, "_external_python", lambda _: "python")
    monkeypatch.setattr(inference_runner.qwen3_tts_paths, "model_dir", lambda kind: model_dir)

    def fake_external(cmd, cwd, env=None):
        payload = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        # Simulate the current MLX runtime, which receives speed but produces a
        # fixed-duration wav because native speed control is not implemented.
        sf.write(payload["output_path"], np.zeros(24_000, dtype=np.float32), 24_000, subtype="PCM_16")

    monkeypatch.setattr(inference_runner, "_run_external", fake_external)

    meta = inference_runner.run_qwen3_tts(
        output_path=str(output_path),
        text="Qwen3 speed verification.",
        speed=2.0,
    )

    assert 450 <= meta["duration_ms"] <= 650


def test_single_request_prefers_explicit_reference_audio_over_voice_id(tmp_path):
    original_db = database.DB_PATH
    database.set_db_path(tmp_path / "voice_studio.db")
    library_ref = tmp_path / "library.wav"
    explicit_ref = tmp_path / "explicit.wav"
    library_ref.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    explicit_ref.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    try:
        voice_file = VoiceFile(
            file_id="library-file",
            original_name="library.wav",
            path=str(library_ref),
            size_bytes=library_ref.stat().st_size,
        )
        database.upsert("voice_files", voice_file.file_id, voice_file.model_dump())
        voice = voice_store.create_voice(
            VoiceAssetCreate(
                name="库内声音",
                reference_audio_ids=[voice_file.file_id],
                reference_text="库内参考文本",
            )
        )

        req = GenerateRequest(
            text="测试文本",
            engine_id="f5-tts",
            voice_id=voice.voice_id,
            reference_audio_path=str(explicit_ref),
            ref_text="外部参考文本",
        )

        kwargs = task_queue._kwargs(req, str(tmp_path / "out.wav"))

        assert kwargs["reference_audio"] == str(explicit_ref)
        assert kwargs["ref_text"] == "外部参考文本"
    finally:
        database.set_db_path(original_db)


def test_single_request_does_not_fallback_when_explicit_reference_is_missing(tmp_path):
    original_db = database.DB_PATH
    database.set_db_path(tmp_path / "voice_studio.db")
    library_ref = tmp_path / "library.wav"
    library_ref.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    try:
        voice_file = VoiceFile(
            file_id="library-file",
            original_name="library.wav",
            path=str(library_ref),
            size_bytes=library_ref.stat().st_size,
        )
        database.upsert("voice_files", voice_file.file_id, voice_file.model_dump())
        voice = voice_store.create_voice(
            VoiceAssetCreate(
                name="库内声音",
                reference_audio_ids=[voice_file.file_id],
                reference_text="库内参考文本",
            )
        )
        req = GenerateRequest(
            text="测试文本",
            engine_id="f5-tts",
            voice_id=voice.voice_id,
            reference_audio_path=str(tmp_path / "missing.wav"),
            ref_text="外部参考文本",
        )

        with pytest.raises(Exception) as exc:
            task_queue._kwargs(req, str(tmp_path / "out.wav"))

        assert getattr(exc.value, "code", None) == "REFERENCE_AUDIO_NOT_FOUND"
    finally:
        database.set_db_path(original_db)


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
def test_custom_reference_audio_flows_to_all_reference_voice_engines(tmp_path, monkeypatch, engine_id, reference_key):
    if engine_id.startswith("mimo-"):
        _enable_mimo(monkeypatch)

    reference = _ref_file(tmp_path)
    req = GenerateRequest(
        text="测试文本",
        engine_id=engine_id,
        reference_audio_path=reference,
        ref_text="自定义音色对应的参考台词。",
    )

    kwargs = task_queue._kwargs(req, str(tmp_path / f"{engine_id}.wav"))

    assert kwargs[reference_key] == reference
    if engine_id in {"omnivoice", "f5-tts", "cosyvoice-zero-shot"}:
        assert kwargs["ref_text"] == "自定义音色对应的参考台词。"


def test_confucius4_requires_reference_audio(tmp_path):
    req = GenerateRequest(text="测试文本", engine_id="confucius4-mlx-int8")

    with pytest.raises(Exception) as exc:
        task_queue._kwargs(req, str(tmp_path / "out.wav"))

    assert getattr(exc.value, "code", None) == "REFERENCE_AUDIO_REQUIRED"


def test_confucius4_runner_defaults_match_official_values(tmp_path):
    payload = {
        "output_path": str(tmp_path / "out.wav"),
        "text": "默认值测试",
        "reference_audio": str(tmp_path / "ref.wav"),
        "model_dir": str(tmp_path / "confucius4-model"),
    }

    _, _, _, _, _, _, language, temperature, top_k, top_p, repetition_penalty, diffusion_steps, cfg_rate, seed = inference_runner._build_confucius4_mlx_kwargs(**payload)

    assert language == "zh"
    assert temperature == 0.8
    assert top_k == 30
    assert top_p == 0.8
    assert repetition_penalty == 10.0
    assert diffusion_steps == 25
    assert cfg_rate == 0.7
    assert seed == 0


def test_confucius4_runner_passes_sampling_kwargs_to_mlx_model(tmp_path, monkeypatch):
    import types

    model_dir = tmp_path / "model"
    runtime_root = tmp_path / "runtime"
    for rel in inference_runner.confucius4_paths.REQUIRED_MODEL_FILES:
        target = model_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    for rel in inference_runner.confucius4_paths.REQUIRED_RUNTIME_FILES:
        target = runtime_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# test runtime\n", encoding="utf-8")

    captured: dict = {}

    class FakeResult:
        sample_rate = 22050
        audio = inference_runner.np.zeros(32, dtype=inference_runner.np.float32)

    class FakeModel:
        def generate(self, **kwargs):
            captured.update(kwargs)
            yield FakeResult()

    fake_utils = types.ModuleType("mlx_audio.tts.utils")
    fake_utils.load = lambda *args, **kwargs: FakeModel()
    monkeypatch.setitem(sys.modules, "mlx_audio", types.ModuleType("mlx_audio"))
    monkeypatch.setitem(sys.modules, "mlx_audio.tts", types.ModuleType("mlx_audio.tts"))
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", fake_utils)
    monkeypatch.setattr(inference_runner, "_prepare_confucius4_runtime", lambda root: None)
    monkeypatch.setattr(inference_runner, "_confucius4_ref_audio_16k", lambda ref, tmp: ref)

    ref_path = tmp_path / "ref.wav"
    ref_path.write_bytes(b"fake")
    output_path = tmp_path / "out.wav"
    inference_runner.run_confucius4_mlx(
        text="参数传递测试",
        reference_audio=str(ref_path),
        output_path=str(output_path),
        model_dir=str(model_dir),
        runtime_root=str(runtime_root),
        language="zh",
        temperature=0.83,
        top_p=0.0,
        top_k=18,
        repetition_penalty=9.0,
        diffusion_steps=5,
        cfg_rate=0.0,
        seed=7,
    )

    assert output_path.exists()
    assert captured["temperature"] == 0.83
    assert captured["top_p"] == 0.0
    assert captured["top_k"] == 18
    assert captured["repetition_penalty"] == 9.0
    assert captured["diffusion_steps"] == 5
    assert captured["cfg_rate"] == 0.0
    assert captured["seed"] == 7


def test_confucius4_splits_text_before_runner_window(tmp_path):
    text = (
        "你问 GPT-3：腿上有几只眼睛？它说：两只。"
        "你再问它：太阳有几只眼睛？它说：一只。"
        "这不是它不认识眼睛。它只是没搞懂，眼睛到底属于哪种情境。"
        "腿，会被它拉向身体。太阳，会被它拉向刺眼的句子。"
    )

    parts = inference_runner._confucius4_split_text(text)

    assert len(parts) > 1
    assert "".join(parts) == text
    assert all(inference_runner._confucius4_char_count(part) <= 24 for part in parts)


def test_batch_segment_reference_audio_can_replace_library_voice(tmp_path):
    reference = _ref_file(tmp_path)
    batch_req = BatchGenerateRequest(
        engine_id="f5-tts",
        segments=[
            BatchSegmentInput(
                text="测试文本-批量",
                reference_audio_path=reference,
                ref_text="逐段参考文本。",
            )
        ],
    )

    batch = _batch_payload(batch_req, tmp_path)

    assert batch["reference_audio"] == reference
    assert batch["ref_text"] == "逐段参考文本。"


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
    assert "temperature" not in single_kwargs
    assert "top_p" not in single_kwargs
    assert "temperature" not in batch_kwargs
    assert "top_p" not in batch_kwargs


def test_doubao_preset_profile_is_normalized_between_single_and_batch(tmp_path, monkeypatch):
    _enable_doubao(monkeypatch)
    params = {
        "speaker_id": "zh_female_xiaohe_uranus_bigtts",
        "language": "en",
        "style_instruction": "自然、清晰，像课程旁白。",
        "speed": 1.12,
        "pitch_rate": -4,
        "sample_rate": 48000,
        "bit_rate": 160000,
        "loudness_rate": 20,
        "enable_subtitle": True,
        "silence_duration": 800,
        "aigc_watermark": True,
    }
    additions = {
        "max_length_to_filter_parenthesis": True,
        "disable_markdown_filter": True,
        "latex_parser_mode": "enhanced",
        "aigc_metadata_enable": True,
        "content_producer": "Voice Studio",
        "produce_id": "project-42",
    }
    single = GenerateRequest(
        text="测试文本",
        engine_id="doubao-tts-preset",
        output_format="mp3",
        engine_parameters=additions,
        **params,
    )
    single_kwargs = task_queue._kwargs(single, str(tmp_path / "doubao-single.mp3"))

    batch_req = BatchGenerateRequest(
        engine_id="doubao-tts-preset",
        parameters={**params, **additions},
        segments=[BatchSegmentInput(text="测试文本")],
    )
    batch_kwargs = _batch_payload(batch_req, tmp_path)

    for data in [single_kwargs, batch_kwargs]:
        assert data["base_url"] == "https://openspeech.bytedance.com"
        assert data["api_key"] == "test-doubao-token"
        assert data["resource_id"] == "seed-tts-2.0"
        assert data["speaker"] == "zh_female_xiaohe_uranus_bigtts"
        assert data["explicit_language"] == "en"
        assert data["style_instruction"] == params["style_instruction"]
        assert data["speed"] == params["speed"]
        assert data["pitch_rate"] == params["pitch_rate"]
        assert data["sample_rate"] == params["sample_rate"]
        assert data["bit_rate"] == params["bit_rate"]
        assert data["loudness_rate"] == params["loudness_rate"]
        assert data["enable_subtitle"] is True
        assert data["silence_duration"] == params["silence_duration"]
        assert data["aigc_watermark"] is True
        assert data["max_length_to_filter_parenthesis"] is True
        assert data["disable_markdown_filter"] is True
        assert data["latex_parser_mode"] == "enhanced"
        assert data["aigc_metadata_enable"] is True
        assert data["content_producer"] == "Voice Studio"
        assert data["produce_id"] == "project-42"


def test_doubao_tts_uses_high_quality_defaults_for_single_and_batch(tmp_path, monkeypatch):
    _enable_doubao(monkeypatch)
    single = GenerateRequest(text="测试文本", engine_id="doubao-tts-preset", output_format="mp3")
    single_kwargs = task_queue._kwargs(single, str(tmp_path / "doubao-default-single.mp3"))
    batch_req = BatchGenerateRequest(
        engine_id="doubao-tts-preset",
        segments=[BatchSegmentInput(text="测试文本")],
    )
    batch_kwargs = _batch_payload(batch_req, tmp_path)

    for data in [single_kwargs, batch_kwargs]:
        assert data["sample_rate"] == 48000
        assert data["bit_rate"] == 160000


def test_doubao_pitch_rate_range_is_validated():
    GenerateRequest(text="测试", engine_id="doubao-tts-preset", pitch_rate=-12)
    GenerateRequest(text="测试", engine_id="doubao-tts-preset", pitch_rate=12)
    with pytest.raises(ValueError):
        GenerateRequest(text="测试", engine_id="doubao-tts-preset", pitch_rate=-13)
    with pytest.raises(ValueError):
        GenerateRequest(text="测试", engine_id="doubao-tts-preset", pitch_rate=13)


def test_doubao_official_audio_parameter_ranges_are_validated():
    for sample_rate in [8000, 16000, 22050, 24000, 32000, 44100, 48000]:
        GenerateRequest(text="测试", engine_id="doubao-tts-preset", sample_rate=sample_rate)
    with pytest.raises(ValueError):
        GenerateRequest(text="测试", engine_id="doubao-tts-preset", sample_rate=12000)
    GenerateRequest(text="测试", engine_id="doubao-tts-preset", bit_rate=64000, loudness_rate=-50, silence_duration=0)
    GenerateRequest(text="测试", engine_id="doubao-tts-preset", bit_rate=160000, loudness_rate=100, silence_duration=30000)
    with pytest.raises(ValueError):
        GenerateRequest(text="测试", engine_id="doubao-tts-preset", bit_rate=160001)
    with pytest.raises(ValueError):
        GenerateRequest(text="测试", engine_id="doubao-tts-preset", loudness_rate=-51)
    with pytest.raises(ValueError):
        GenerateRequest(text="测试", engine_id="doubao-tts-preset", silence_duration=30001)


def test_doubao_tts_keeps_its_two_official_raw_output_formats_end_to_end(tmp_path):
    for output_format in ("pcm", "ogg_opus"):
        request = GenerateRequest(text="官方输出格式对账。", engine_id="doubao-tts-preset", output_format=output_format)
        assert request.output_format == output_format
        manifest = engine_manifests.ENGINES["doubao-tts-preset"].manifest
        assert output_format in manifest.supported_output_formats

        batch = BatchGenerateRequest(
            engine_id="doubao-tts-preset",
            output_format=output_format,
            segments=[BatchSegmentInput(text="批量官方输出格式对账。")],
        )
        assert batch.output_format == output_format
        assert task_queue._direct_cloud_output_format(request) == output_format

    with pytest.raises(ValueError, match="PCM 和 OGG Opus"):
        GenerateRequest(text="本地引擎不能假装支持 PCM。", engine_id="indextts-v2", output_format="pcm")


@pytest.mark.parametrize("output_format", ["pcm", "ogg_opus"])
def test_doubao_tts_raw_formats_keep_the_provider_output_path(tmp_path, monkeypatch, output_format):
    """PCM/OGG Opus are direct provider outputs, never a disguised WAV conversion."""
    provider_calls: list[dict] = []

    def fake_generate_tts(**kwargs):
        provider_calls.append(kwargs)
        Path(kwargs["output_path"]).write_bytes(b"provider-native-audio")
        return {"output_path": kwargs["output_path"], "request_id": "request-raw", "logid": "log-raw"}

    monkeypatch.setattr(doubao_client, "generate_tts_unidirectional_http", fake_generate_tts)
    monkeypatch.setattr(audio_tools, "copy_or_convert", lambda *_: pytest.fail("raw provider output must not be converted"))

    output_path = tmp_path / f"doubao-native.{output_format}"
    result = inference_runner.run_doubao_tts(
        base_url="https://openspeech.bytedance.com",
        api_key="token",
        text="原生格式链路验证。",
        output_path=str(output_path),
        speaker="speaker",
    )

    assert provider_calls[0]["audio_format"] == output_format
    assert provider_calls[0]["output_path"] == str(output_path)
    assert result["output_path"] == str(output_path)
    assert output_path.read_bytes() == b"provider-native-audio"


def test_doubao_parenthesis_filter_updates_asr_expected_text():
    from app.schemas.voice_studio import GenerationTask

    task = GenerationTask(
        engine_id="doubao-tts-preset",
        input_text="你好（这是不会朗读的脚本备注）世界。",
        parameters={"engine_parameters": {"max_length_to_filter_parenthesis": True}},
    )
    assert task_queue.verification_expected_text_for_task(task) == "你好世界。"


def test_doubao_single_and_batch_flac_use_wav_then_convert(tmp_path, monkeypatch):
    provider_calls: list[dict] = []
    conversions: list[tuple[Path, Path, str]] = []

    def fake_generate_tts(**kwargs):
        provider_calls.append(kwargs)
        Path(kwargs["output_path"]).write_bytes(b"provider-wav")
        return {"output_path": kwargs["output_path"], "request_id": "request-1", "logid": "log-1"}

    def fake_convert(src, dest, fmt):
        src_path, dest_path = Path(src), Path(dest)
        conversions.append((src_path, dest_path, fmt))
        dest_path.write_bytes(b"local-flac")
        return dest_path

    monkeypatch.setattr(doubao_client, "generate_tts_unidirectional_http", fake_generate_tts)
    monkeypatch.setattr(audio_tools, "copy_or_convert", fake_convert)

    single_out = tmp_path / "single.flac"
    single_meta = inference_runner.run_doubao_tts(
        base_url="https://openspeech.bytedance.com",
        api_key="token",
        text="单条",
        output_path=str(single_out),
        speaker="speaker",
        explicit_language="en",
        latex_parser_mode="basic",
        tone_fidelity=True,
        pitch_rate=5,
    )
    batch_out = tmp_path / "batch.flac"
    batch_results = batch_inference_runner.run_doubao_tts(
        {
            "common": {
                "base_url": "https://openspeech.bytedance.com",
                "api_key": "token",
                "speaker": "speaker",
                "explicit_language": "ja",
                "max_length_to_filter_parenthesis": True,
                "aigc_metadata_enable": True,
                "content_producer": "Voice Studio",
                "pitch_rate": -2,
            },
            "segments": [{"segment_id": "segment-1", "text": "批量", "output_path": str(batch_out), "parameters": {}}],
        }
    )

    assert single_meta["output_path"] == str(single_out)
    assert batch_results[0]["status"] == "success"
    assert batch_results[0]["output_path"] == str(batch_out)
    assert [call["audio_format"] for call in provider_calls] == ["wav", "wav"]
    assert [call["explicit_language"] for call in provider_calls] == ["en", "ja"]
    assert [call["latex_parser_mode"] for call in provider_calls] == ["basic", None]
    assert [call["tone_fidelity"] for call in provider_calls] == [True, False]
    assert [call["max_length_to_filter_parenthesis"] for call in provider_calls] == [None, True]
    assert [call["aigc_metadata_enable"] for call in provider_calls] == [False, True]
    assert [call["pitch_rate"] for call in provider_calls] == [5, -2]
    assert all(Path(call["output_path"]).suffix == ".wav" for call in provider_calls)
    assert [item[2] for item in conversions] == ["flac", "flac"]
    assert single_out.read_bytes() == b"local-flac"
    assert batch_out.read_bytes() == b"local-flac"
    assert not (tmp_path / "single.doubao-tmp.wav").exists()
    assert not (tmp_path / "batch.doubao-tmp.wav").exists()


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


def test_indextts_rejects_unverified_free_text_emotion_mode(tmp_path):
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
    with pytest.raises(Exception) as single_error:
        task_queue._kwargs(single, str(tmp_path / "single.wav"))
    assert getattr(single_error.value, "code", None) == "INDEXTTS_EMOTION_TEXT_UNSUPPORTED"

    batch_req = BatchGenerateRequest(
        engine_id="indextts-v2",
        reference_audio_path=reference,
        parameters={"emotion_text": "喜悦", "emotion": "happy", "emotion_mode": "emotion_text", "emo_alpha": 0.6},
        segments=[BatchSegmentInput(text="测试文本")],
    )
    with pytest.raises(ValueError, match="INDEXTTS_EMOTION_TEXT_UNSUPPORTED"):
        _batch_payload(batch_req, tmp_path)
