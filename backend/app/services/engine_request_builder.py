from __future__ import annotations

from typing import Any

from app.schemas.voice_studio import BatchGenerateRequest, GenerateRequest, VoiceAsset
from app.models.exceptions import AppException
from app.services import doubao_client, settings_store

MIMO_DEFAULT_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
MIMO_TTS_ENGINE_IDS = {"mimo-v2.5-tts", "mimo-v2.5-tts-preset", "mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voiceclone"}
DOUBAO_TTS_ENGINE_IDS = {"doubao-tts-preset", "doubao-tts-voiceclone"}
DOUBAO_VOICE_READY_STATUSES = {"success", "active", "available", "passed", "2", "3"}


def is_mimo_tts_request(engine_id: str) -> bool:
    return engine_id in MIMO_TTS_ENGINE_IDS


def is_doubao_tts_request(engine_id: str) -> bool:
    return engine_id in DOUBAO_TTS_ENGINE_IDS


def build_doubao_tts_single_kwargs(req: GenerateRequest, output_path: str, *, voice: VoiceAsset | None = None) -> dict[str, Any]:
    settings, api_key = _doubao_auth()
    speaker = _doubao_speaker_for_request(req, voice)
    resource_id = (
        settings.doubao_default_icl_resource_id
        if req.engine_id == "doubao-tts-voiceclone"
        else settings.doubao_default_tts_resource_id
    )
    return {
        "text": req.text,
        "output_path": output_path,
        "base_url": settings.doubao_base_url or doubao_client.DEFAULT_BASE_URL,
        "api_key": api_key,
        "resource_id": resource_id or doubao_client.DEFAULT_TTS_RESOURCE_ID,
        "speaker": speaker,
        "style_instruction": req.style_instruction if req.engine_id == "doubao-tts-preset" else None,
        "speed": req.speed,
        "sample_rate": req.sample_rate or doubao_client.DEFAULT_TTS_SAMPLE_RATE,
        "bit_rate": req.bit_rate or doubao_client.DEFAULT_TTS_BIT_RATE,
        "loudness_rate": req.loudness_rate,
        "pitch_rate": req.pitch_rate,
        "enable_subtitle": req.enable_subtitle if req.engine_id == "doubao-tts-preset" else False,
        "silence_duration": req.silence_duration,
        "aigc_watermark": req.aigc_watermark,
    }


def build_doubao_tts_batch_common_kwargs(req: BatchGenerateRequest, *, voice: VoiceAsset | None = None) -> dict[str, Any]:
    settings, api_key = _doubao_auth()
    values = GenerateRequest(text="placeholder", engine_id=req.engine_id, language=req.language).model_dump()
    values.update(req.parameters)
    request = GenerateRequest(
        text="placeholder",
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        reference_audio_path=req.reference_audio_path,
        speaker_id=values.get("speaker_id"),
        style_instruction=values.get("style_instruction"),
        speed=values.get("speed") or 1.0,
        language=req.language,
    )
    speaker = _doubao_speaker_for_request(request, voice)
    resource_id = (
        settings.doubao_default_icl_resource_id
        if req.engine_id == "doubao-tts-voiceclone"
        else settings.doubao_default_tts_resource_id
    )
    return {
        "base_url": settings.doubao_base_url or doubao_client.DEFAULT_BASE_URL,
        "api_key": api_key,
        "resource_id": resource_id or doubao_client.DEFAULT_TTS_RESOURCE_ID,
        "speaker": speaker,
        "style_instruction": values.get("style_instruction") if req.engine_id == "doubao-tts-preset" else None,
        "speed": values.get("speed"),
        "sample_rate": values.get("sample_rate") or doubao_client.DEFAULT_TTS_SAMPLE_RATE,
        "bit_rate": values.get("bit_rate") or doubao_client.DEFAULT_TTS_BIT_RATE,
        "loudness_rate": values.get("loudness_rate"),
        "pitch_rate": values.get("pitch_rate"),
        "enable_subtitle": bool(values.get("enable_subtitle")) if req.engine_id == "doubao-tts-preset" else False,
        "silence_duration": values.get("silence_duration") or 0,
        "aigc_watermark": bool(values.get("aigc_watermark")),
    }


def _doubao_speaker_for_request(req: GenerateRequest, voice: VoiceAsset | None) -> str:
    if req.engine_id == "doubao-tts-preset":
        return req.speaker_id or "zh_female_vv_uranus_bigtts"
    if req.engine_id != "doubao-tts-voiceclone":
        return req.speaker_id or "zh_female_vv_uranus_bigtts"
    if req.reference_audio_path:
        raise AppException(400, "DOUBAO_REFERENCE_AUDIO_NOT_SUPPORTED", "豆包云端复刻合成只能使用已训练的 speaker_id，不支持直接上传本地参考音频")
    if not voice or voice.external_provider != "doubao" or not voice.external_voice_id:
        raise AppException(400, "DOUBAO_VOICE_NOT_BOUND", "请选择已训练成功的豆包云端音色")
    status = str(voice.external_status or "").strip().lower()
    if status not in DOUBAO_VOICE_READY_STATUSES:
        raise AppException(400, "DOUBAO_VOICE_NOT_READY", f"豆包云端音色还不可用，当前状态：{voice.external_status or '未知'}")
    return voice.external_voice_id


def build_mimo_tts_single_kwargs(
    req: GenerateRequest,
    output_path: str,
    *,
    reference_audio_path: str | None,
    idempotency_marker: str,
) -> dict[str, Any]:
    settings, api_key = _mimo_auth()
    return {
        "text": req.text,
        "output_path": output_path,
        "base_url": settings.mimo_base_url or MIMO_DEFAULT_BASE_URL,
        "api_key": api_key,
        "model": _mimo_model(req.engine_id),
        "voice": req.mimo_voice or settings.mimo_default_voice,
        "instruction": req.style_instruction or req.emotion_text or req.emotion,
        "voice_design_prompt": req.voice_design_prompt or req.style_instruction or req.emotion_text,
        "optimize_text_preview": req.optimize_text_preview,
        "reference_audio_path": reference_audio_path,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "idempotency_marker": idempotency_marker,
    }


def build_mimo_tts_batch_common_kwargs(
    req: BatchGenerateRequest,
    *,
    reference_audio_path: str | None,
) -> dict[str, Any]:
    settings, api_key = _mimo_auth()
    instruction = _mimo_instruction(req.parameters)
    return {
        "base_url": settings.mimo_base_url,
        "api_key": api_key,
        "model": _mimo_model(req.engine_id),
        "mimo_voice": req.parameters.get("mimo_voice") or settings.mimo_default_voice,
        "instruction": instruction,
        "style_instruction": instruction,
        "voice_design_prompt": _mimo_voice_design_prompt(req.parameters),
        "optimize_text_preview": req.parameters.get("optimize_text_preview", False),
        "reference_audio_path": reference_audio_path,
        "temperature": req.parameters.get("temperature", 0.6),
        "top_p": req.parameters.get("top_p", 0.95),
    }


def build_f5_tts_single_kwargs(
    req: GenerateRequest,
    output_path: str,
    *,
    reference_audio: str | None,
    ref_text: str | None,
) -> dict[str, Any]:
    return {
        "text": req.text,
        "reference_audio": reference_audio,
        "ref_text": ref_text,
        "output_path": output_path,
        "speed": req.speed,
        "nfe_step": req.nfe_step,
        "cfg_strength": req.cfg_strength,
        "target_rms": req.target_rms,
        "cross_fade_duration": req.cross_fade_duration,
        "sway_sampling_coef": req.sway_sampling_coef,
        "fix_duration": req.fix_duration if req.fix_duration > 0 else None,
        "remove_silence": req.remove_silence,
        "seed": req.seed,
    }


def build_f5_tts_batch_common_kwargs(
    values: dict[str, Any],
    *,
    reference_audio: str | None,
    ref_text: str | None,
) -> dict[str, Any]:
    return {
        "reference_audio": reference_audio,
        "ref_text": ref_text,
        "speed": values.get("speed"),
        "nfe_step": values.get("nfe_step"),
        "cfg_strength": values.get("cfg_strength"),
        "target_rms": values.get("target_rms"),
        "cross_fade_duration": values.get("cross_fade_duration"),
        "sway_sampling_coef": values.get("sway_sampling_coef"),
        "fix_duration": values.get("fix_duration") or None,
        "remove_silence": values.get("remove_silence"),
        "seed": values.get("seed"),
    }


def build_cosyvoice_zero_shot_single_kwargs(
    req: GenerateRequest,
    output_path: str,
    *,
    reference_audio: str | None,
    ref_text: str | None,
) -> dict[str, Any]:
    return {
        "text": req.text,
        "reference_audio": reference_audio,
        "ref_text": ref_text,
        "output_path": output_path,
        "speed": req.speed,
    }


def build_cosyvoice_zero_shot_batch_common_kwargs(
    values: dict[str, Any],
    *,
    reference_audio: str | None,
    ref_text: str | None,
) -> dict[str, Any]:
    return {
        "reference_audio": reference_audio,
        "ref_text": ref_text,
        "speed": values.get("speed"),
    }


def build_confucius4_mlx_single_kwargs(
    req: GenerateRequest,
    output_path: str,
    *,
    reference_audio: str | None,
    model_dir: str,
) -> dict[str, Any]:
    return {
        "text": req.text,
        "reference_audio": reference_audio,
        "output_path": output_path,
        "model_dir": model_dir,
        "language": req.language,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "top_k": req.top_k,
        "repetition_penalty": req.repetition_penalty,
        "diffusion_steps": req.diffusion_steps,
        "cfg_rate": req.cfg_rate,
        "seed": req.seed if req.seed is not None else 0,
    }


def build_confucius4_mlx_batch_common_kwargs(
    values: dict[str, Any],
    *,
    reference_audio: str | None,
    language: str,
    model_dir: str,
) -> dict[str, Any]:
    return {
        "reference_audio": reference_audio,
        "model_dir": model_dir,
        "language": values.get("language") or language,
        "temperature": values.get("temperature"),
        "top_p": values.get("top_p"),
        "top_k": values.get("top_k"),
        "repetition_penalty": values.get("repetition_penalty"),
        "diffusion_steps": values.get("diffusion_steps"),
        "cfg_rate": values.get("cfg_rate"),
        "seed": values.get("seed") if values.get("seed") is not None else 0,
    }


def build_qwen3_tts_single_kwargs(
    req: GenerateRequest,
    output_path: str,
    *,
    reference_audio: str | None,
    ref_text: str | None,
) -> dict[str, Any]:
    voice_design_prompt = req.voice_design_prompt if not reference_audio else None
    preset_route = not reference_audio and not voice_design_prompt
    return {
        "text": req.text,
        "reference_audio": reference_audio,
        "ref_text": ref_text,
        "output_path": output_path,
        "language": req.language,
        "speaker_id": req.speaker_id if preset_route else None,
        "style_instruction": (req.style_instruction or req.prompt or req.emotion_text or req.emotion) if preset_route else None,
        "voice_design_prompt": voice_design_prompt,
        "speed": req.speed,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "top_k": req.top_k,
        "repetition_penalty": req.repetition_penalty,
        "max_tokens": req.max_tokens,
        "cfg_scale": req.cfg_scale,
        "ddpm_steps": req.ddpm_steps,
    }


def build_qwen3_tts_batch_common_kwargs(
    values: dict[str, Any],
    *,
    reference_audio: str | None,
    ref_text: str | None,
    language: str,
) -> dict[str, Any]:
    voice_design_prompt = values.get("voice_design_prompt") if not reference_audio else None
    preset_route = not reference_audio and not voice_design_prompt
    return {
        "reference_audio": reference_audio,
        "ref_text": ref_text,
        "language": values.get("language") or language,
        "speaker_id": values.get("speaker_id") if preset_route else None,
        "style_instruction": (values.get("style_instruction") or values.get("prompt") or values.get("emotion_text") or values.get("emotion")) if preset_route else None,
        "voice_design_prompt": voice_design_prompt,
        "speed": values.get("speed"),
        "temperature": values.get("temperature"),
        "top_p": values.get("top_p"),
        "top_k": values.get("top_k"),
        "repetition_penalty": values.get("repetition_penalty"),
        "max_tokens": values.get("max_tokens"),
        "cfg_scale": values.get("cfg_scale"),
        "ddpm_steps": values.get("ddpm_steps"),
    }


def build_indextts_v2_single_kwargs(
    req: GenerateRequest,
    output_path: str,
    *,
    reference_audio: str | None,
    model_dir: str,
) -> dict[str, Any]:
    return {
        "text": req.text,
        "reference_audio": reference_audio,
        "output_path": output_path,
        "model_dir": model_dir,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "top_k": req.top_k,
        "repetition_penalty": req.repetition_penalty,
        "max_text_tokens_per_segment": req.max_text_tokens_per_segment,
        "interval_silence": req.interval_silence,
        "segment_overlap_ms": req.segment_overlap_ms,
        "speed": req.speed,
        "seed": req.seed,
        "max_mel_tokens": req.max_mel_tokens or 1500,
        "diffusion_steps": req.diffusion_steps,
        "cfg_rate": req.cfg_rate,
        "emotion": _indextts_request_emotion(req),
        "emo_alpha": req.emo_alpha,
    }


def build_indextts_v2_batch_common_kwargs(
    values: dict[str, Any],
    *,
    parameters: dict[str, Any],
    reference_audio: str | None,
    language: str,
    model_dir: str,
) -> dict[str, Any]:
    common = {
        "reference_audio": reference_audio,
        "language": language,
        "model_dir": model_dir,
    }
    for key in [
        "temperature",
        "top_p",
        "top_k",
        "repetition_penalty",
        "max_text_tokens_per_segment",
        "interval_silence",
        "segment_overlap_ms",
        "speed",
        "seed",
        "max_mel_tokens",
        "diffusion_steps",
        "cfg_rate",
        "emotion",
        "emo_alpha",
        "emotion_text",
    ]:
        common[key] = values.get(key)
    common["emotion"] = _indextts_batch_emotion(parameters, values)
    return common


def build_preset_voice_single_kwargs(req: GenerateRequest, output_path: str) -> dict[str, Any]:
    return {
        "text": req.text,
        "output_path": output_path,
        "speaker_id": req.speaker_id,
        "prompt": req.prompt or req.emotion,
        "speed": req.speed,
    }


def build_preset_voice_batch_common_kwargs(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "speaker_id": values.get("speaker_id"),
        "prompt": values.get("prompt"),
        "speed": values.get("speed"),
    }


def build_omnivoice_single_kwargs(
    req: GenerateRequest,
    output_path: str,
    *,
    reference_audio: str | None,
    ref_text: str | None,
    model_dir: str,
) -> dict[str, Any]:
    return {
        "text": req.text,
        "reference_audio": reference_audio,
        "output_path": output_path,
        "model_dir": model_dir,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "top_k": req.top_k,
        "repetition_penalty": req.repetition_penalty,
        "max_text_tokens_per_segment": req.max_text_tokens_per_segment,
        "interval_silence": req.interval_silence,
        "segment_overlap_ms": req.segment_overlap_ms,
        "speed": req.speed,
        "seed": req.seed,
        "language": req.language,
        "ref_text": ref_text,
        "emotion": req.emotion,
        "emotion_text": req.emotion_text,
        "diffusion_steps": req.diffusion_steps or 16,
        "guidance_scale": req.guidance_scale,
        "duration": req.duration,
        "audio_chunk_duration": req.audio_chunk_duration,
        "audio_chunk_threshold": req.audio_chunk_threshold,
    }


def build_omnivoice_batch_common_kwargs(
    values: dict[str, Any],
    *,
    reference_audio: str | None,
    ref_text: str | None,
    language: str,
    model_dir: str,
) -> dict[str, Any]:
    common = {
        "reference_audio": reference_audio,
        "language": language,
        "model_dir": model_dir,
        "ref_text": ref_text,
    }
    for key in [
        "temperature",
        "top_p",
        "top_k",
        "repetition_penalty",
        "max_text_tokens_per_segment",
        "interval_silence",
        "segment_overlap_ms",
        "speed",
        "seed",
        "max_mel_tokens",
        "diffusion_steps",
        "guidance_scale",
        "duration",
        "audio_chunk_duration",
        "audio_chunk_threshold",
        "cfg_rate",
        "emotion",
        "emo_alpha",
        "emotion_text",
    ]:
        common[key] = values.get(key)
    return common


def _mimo_auth():
    settings = settings_store.get()
    api_key = settings_store.mimo_api_key()
    if not settings.cloud_enabled:
        raise AppException(403, "MIMO_CLOUD_DISABLED", "MiMo 云端引擎未启用，请在设置中开启")
    if not api_key:
        raise AppException(403, "MIMO_API_KEY_MISSING", "缺少 MiMo API Key，请在设置中配置")
    return settings, api_key


def _doubao_auth():
    settings = settings_store.get()
    api_key = settings_store.doubao_api_key()
    if not settings.cloud_enabled:
        raise AppException(403, "DOUBAO_CLOUD_DISABLED", "豆包云端引擎未启用，请在设置中开启")
    if not api_key:
        raise AppException(403, "DOUBAO_API_KEY_MISSING", "缺少豆包 API Key，请在设置中配置")
    return settings, api_key


def _mimo_model(engine_id: str) -> str:
    return "mimo-v2.5-tts" if engine_id in {"mimo-v2.5-tts", "mimo-v2.5-tts-preset"} else engine_id


def _mimo_instruction(values: dict[str, Any]) -> str | None:
    return values.get("style_instruction") or values.get("emotion_text") or values.get("emotion")


def _mimo_voice_design_prompt(values: dict[str, Any]) -> str | None:
    return values.get("voice_design_prompt") or values.get("style_instruction") or values.get("emotion_text")


def _indextts_request_emotion(req: GenerateRequest):
    if req.emotion_mode == "follow_reference":
        return None
    if req.emotion_mode == "emotion_vector":
        return req.emotion_values if req.emotion_values else req.emotion
    if req.emotion_mode == "emotion_text":
        return req.emotion_text
    return None


def _indextts_batch_emotion(parameters: dict[str, Any], values: dict[str, Any]) -> str | None:
    mode = parameters.get("emotion_mode")
    if mode == "emotion_text":
        return parameters.get("emotion_text") or values.get("emotion")
    if mode == "emotion_vector":
        return parameters.get("emotion_values") or values.get("emotion")
    if mode == "follow_reference":
        return None
    return values.get("emotion")
