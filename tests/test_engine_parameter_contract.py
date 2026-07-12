from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import AppSettings, BatchSegmentInput, BatchGenerateRequest, GenerateRequest, VoiceAssetCreate, VoiceFile
from app.services import audio_tools, batch_inference_runner, batch_queue, database, doubao_client, inference_runner, task_queue, voice_store


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
        "top_p": 0.77,
        "top_k": 22,
        "repetition_penalty": 9.5,
        "diffusion_steps": 31,
        "cfg_rate": 0.85,
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


def test_qwen3_tts_single_batch_worker_payload_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_STUDIO_QWEN3_TTS_ROOT", str(inference_runner.qwen3_tts_paths.DEFAULT_ROOT))

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
        assert payload["cfg_scale"] == params["cfg_scale"]
        assert payload["ddpm_steps"] == params["ddpm_steps"]

        root, out, parsed_text, ref_audio, ref_text, language, speaker_id, instruction, voice_design_prompt, speed, temperature, top_p, top_k, repetition_penalty, max_tokens, cfg_scale, ddpm_steps = inference_runner._build_qwen3_tts_kwargs(**payload)
        assert root == inference_runner.qwen3_tts_paths.DEFAULT_ROOT
        assert out == payload["output_path"]
        assert parsed_text == text
        assert ref_audio == reference_audio_path
        assert ref_text == "参考台词。"
        assert language == params["language"]
        assert speaker_id == "Vivian"
        assert instruction == "Normal tone"
        assert voice_design_prompt == ""
        assert speed == params["speed"]
        assert temperature == params["temperature"]
        assert top_p == params["top_p"]
        assert top_k == params["top_k"]
        assert repetition_penalty == params["repetition_penalty"]
        assert max_tokens == params["max_tokens"]
        assert cfg_scale == params["cfg_scale"]
        assert ddpm_steps == params["ddpm_steps"]

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
        top_p=0.76,
        top_k=18,
        repetition_penalty=9.0,
        diffusion_steps=5,
        cfg_rate=0.4,
        seed=7,
    )

    assert output_path.exists()
    assert captured["temperature"] == 0.83
    assert captured["top_p"] == 0.76
    assert captured["top_k"] == 18
    assert captured["repetition_penalty"] == 9.0
    assert captured["diffusion_steps"] == 5
    assert captured["cfg_rate"] == 0.4
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


def test_doubao_preset_profile_is_normalized_between_single_and_batch(tmp_path, monkeypatch):
    _enable_doubao(monkeypatch)
    params = {
        "speaker_id": "zh_female_xiaohe_uranus_bigtts",
        "style_instruction": "自然、清晰，像课程旁白。",
        "speed": 1.12,
        "pitch_rate": -4,
    }
    single = GenerateRequest(
        text="测试文本",
        engine_id="doubao-tts-preset",
        output_format="mp3",
        **params,
    )
    single_kwargs = task_queue._kwargs(single, str(tmp_path / "doubao-single.mp3"))

    batch_req = BatchGenerateRequest(
        engine_id="doubao-tts-preset",
        parameters=params,
        segments=[BatchSegmentInput(text="测试文本")],
    )
    batch_kwargs = _batch_payload(batch_req, tmp_path)

    for data in [single_kwargs, batch_kwargs]:
        assert data["base_url"] == "https://openspeech.bytedance.com"
        assert data["api_key"] == "test-doubao-token"
        assert data["resource_id"] == "seed-tts-2.0"
        assert data["speaker"] == "zh_female_xiaohe_uranus_bigtts"
        assert data["style_instruction"] == params["style_instruction"]
        assert data["speed"] == params["speed"]
        assert data["pitch_rate"] == params["pitch_rate"]


def test_doubao_pitch_rate_range_is_validated():
    GenerateRequest(text="测试", engine_id="doubao-tts-preset", pitch_rate=-12)
    GenerateRequest(text="测试", engine_id="doubao-tts-preset", pitch_rate=12)
    with pytest.raises(ValueError):
        GenerateRequest(text="测试", engine_id="doubao-tts-preset", pitch_rate=-13)
    with pytest.raises(ValueError):
        GenerateRequest(text="测试", engine_id="doubao-tts-preset", pitch_rate=13)


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
        pitch_rate=5,
    )
    batch_out = tmp_path / "batch.flac"
    batch_results = batch_inference_runner.run_doubao_tts(
        {
            "common": {
                "base_url": "https://openspeech.bytedance.com",
                "api_key": "token",
                "speaker": "speaker",
                "pitch_rate": -2,
            },
            "segments": [{"segment_id": "segment-1", "text": "批量", "output_path": str(batch_out), "parameters": {}}],
        }
    )

    assert single_meta["output_path"] == str(single_out)
    assert batch_results[0]["status"] == "success"
    assert batch_results[0]["output_path"] == str(batch_out)
    assert [call["audio_format"] for call in provider_calls] == ["wav", "wav"]
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
