from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from app.errors import AppException
from app.schemas.voice_studio import EngineAudioDiagnosisRequest, EngineDetail, EngineSpeaker
from app.services import audio_tools, engine_registry, settings_store, voice_store

router = APIRouter()


@router.get("", response_model=list[EngineDetail])
async def list_engines():
    return engine_registry.list_engines()


@router.get("/{engine_id}", response_model=EngineDetail)
async def get_engine(engine_id: str):
    detail = engine_registry.get_engine(engine_id)
    if not detail:
        raise AppException(404, "ENGINE_NOT_FOUND", "Engine not found")
    return detail


@router.get("/{engine_id}/speakers", response_model=list[EngineSpeaker])
async def list_speakers(
    engine_id: str,
    q: str = Query("", max_length=80),
    gender: str = Query("all", pattern="^(all|F|M|f|m)$"),
    limit: int = Query(80, ge=1, le=500),
):
    if not engine_registry.get_engine(engine_id):
        raise AppException(404, "ENGINE_NOT_FOUND", "Engine not found")
    return engine_registry.list_speakers(engine_id, query=q, gender=gender, limit=limit)


@router.post("/{engine_id}/start", response_model=EngineDetail)
async def start_engine(engine_id: str):
    if not engine_registry.get_engine(engine_id):
        raise AppException(404, "ENGINE_NOT_FOUND", "Engine not found")
    return await asyncio.to_thread(engine_registry.start_engine, engine_id)


@router.post("/{engine_id}/stop", response_model=EngineDetail)
async def stop_engine(engine_id: str):
    if not engine_registry.get_engine(engine_id):
        raise AppException(404, "ENGINE_NOT_FOUND", "Engine not found")
    return engine_registry.stop_engine(engine_id)


@router.post("/{engine_id}/health-check")
async def health_check(engine_id: str):
    return engine_registry.health_check(engine_id)


@router.post("/{engine_id}/diagnose-audio")
async def diagnose_audio(engine_id: str, data: EngineAudioDiagnosisRequest):
    if not engine_registry.get_engine(engine_id):
        raise AppException(404, "ENGINE_NOT_FOUND", "Engine not found")
    try:
        engine_registry.ensure_loaded(engine_id)
        settings_store.ensure_directories()
        ref = data.reference_audio_path
        if not ref and data.voice_id:
            ref = voice_store.reference_path(data.voice_id)
        out_dir = settings_store.output_dir() / "diagnostics"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{engine_id}-diagnosis.wav"
        kwargs = {
            "text": data.text,
            "reference_audio": ref,
            "output_path": str(output_path),
            "model_dir": str(settings_store.model_path(engine_id)),
            "temperature": 0.8,
            "top_p": 0.8,
            "top_k": 30,
            "repetition_penalty": 10.0,
            "max_text_tokens_per_segment": 120,
            "interval_silence": 200,
            "segment_overlap_ms": 50,
            "speed": 1.0,
            "seed": None,
        }
        if engine_id in {"indextts-v2", "confucius4-mlx-int8"} and not ref:
            label = "Confucius4-TTS" if engine_id == "confucius4-mlx-int8" else "IndexTTS v2"
            raise AppException(400, "REFERENCE_AUDIO_REQUIRED", f"{label} diagnosis requires a reference audio")
        if engine_id == "indextts-v2":
            kwargs.update({"max_mel_tokens": 1500, "diffusion_steps": 25, "cfg_rate": 0.7, "emotion": data.emotion, "emo_alpha": 0.6})
        elif engine_id == "confucius4-mlx-int8":
            kwargs = {
                "text": data.text,
                "reference_audio": ref,
                "output_path": str(output_path),
                "model_dir": str(settings_store.model_path(engine_id)),
                "language": data.language if data.language != "auto" else "zh",
                "temperature": 0.8,
                "top_p": 0.8,
                "top_k": 30,
                "repetition_penalty": 10.0,
                "diffusion_steps": 25,
                "cfg_rate": 0.7,
                "seed": 0,
            }
        elif engine_id == "emotivoice":
            kwargs = {
                "text": data.text,
                "output_path": str(output_path),
                "speaker_id": "8051",
                "prompt": "开心",
                "speed": 1.0,
            }
        elif engine_id in {"f5-tts", "cosyvoice-zero-shot"}:
            if not ref:
                raise AppException(400, "REFERENCE_AUDIO_REQUIRED", f"{engine_id} diagnosis requires a reference audio")
            selected_voice = voice_store.get_voice(data.voice_id) if data.voice_id else None
            ref_text = selected_voice.reference_text if selected_voice else ""
            if not ref_text.strip():
                raise AppException(400, "REFERENCE_TEXT_REQUIRED", f"{engine_id} diagnosis requires the selected voice to have reference text")
            kwargs = {
                "text": data.text,
                "reference_audio": ref,
                "ref_text": ref_text,
                "output_path": str(output_path),
                "speed": 1.0,
                "seed": 42,
            }
            if engine_id == "f5-tts":
                kwargs.update({"nfe_step": 32, "cfg_strength": 2.0, "target_rms": 0.1, "cross_fade_duration": 0.15, "remove_silence": False})
        elif engine_id == "cosyvoice-sft":
            kwargs = {
                "text": data.text,
                "output_path": str(output_path),
                "speaker_id": "中文女",
                "speed": 1.0,
            }
        elif engine_id in {"mimo-v2.5-tts", "mimo-v2.5-tts-preset", "mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voiceclone"}:
            settings = settings_store.get()
            api_key = settings_store.mimo_api_key()
            if not api_key:
                raise AppException(400, "MIMO_API_KEY_REQUIRED", "请先在设置中配置 MiMo API Key")
            if engine_id == "mimo-v2.5-tts-voiceclone" and not ref:
                raise AppException(400, "REFERENCE_AUDIO_REQUIRED", "MiMo VoiceClone 诊断需要选择一个参考音色")
            model = "mimo-v2.5-tts" if engine_id in {"mimo-v2.5-tts", "mimo-v2.5-tts-preset"} else engine_id
            kwargs = {
                "text": data.text,
                "output_path": str(output_path),
                "base_url": settings.mimo_base_url or "https://token-plan-cn.xiaomimimo.com/v1",
                "api_key": api_key,
                "model": model,
                "voice": "mimo_default",
                "instruction": None,
                "voice_design_prompt": "温柔女声" if engine_id == "mimo-v2.5-tts-voicedesign" else None,
                "reference_audio_path": ref if engine_id == "mimo-v2.5-tts-voiceclone" else None,
                "temperature": 0.8,
                "top_p": 0.8,
                "audio_format": "wav",
            }
        elif engine_id == "doubao-tts-preset":
            settings = settings_store.get()
            api_key = settings_store.doubao_api_key()
            if not api_key:
                raise AppException(400, "DOUBAO_API_KEY_REQUIRED", "请先在设置中配置豆包 API Key")
            kwargs = {
                "text": data.text,
                "output_path": str(output_path),
                "base_url": settings.doubao_base_url,
                "api_key": api_key,
                "resource_id": settings.doubao_default_tts_resource_id,
                "speaker": "zh_female_vv_uranus_bigtts",
                "style_instruction": None,
                "speed": 1.0,
                "audio_format": "wav",
            }
        else:
            kwargs.update({"language": data.language, "ref_text": None, "emotion": None, "emotion_text": data.emotion_text})
        timeout = 900 if engine_id in {"cosyvoice-sft", "cosyvoice-zero-shot"} else 600 if engine_id in {"f5-tts", "emotivoice", "confucius4-mlx-int8"} else 300
        result = await asyncio.to_thread(engine_registry.run_isolated, engine_id, kwargs, timeout)
        final_path = Path(result["output_path"])
        quality = audio_tools.quality_metrics(final_path)
        return {
            "engine_id": engine_id,
            "status": "passed" if quality["passed"] else "failed",
            "output_path": str(final_path),
            "quality": quality,
            "generation_time_ms": result.get("generation_time_ms"),
        }
    except AppException:
        raise
    except Exception as exc:
        return {
            "engine_id": engine_id,
            "status": "failed",
            "output_path": None,
            "quality": {"passed": False, "warnings": [str(exc)]},
            "generation_time_ms": None,
        }


@router.get("/{engine_id}/diagnostic-audio")
async def get_diagnostic_audio(engine_id: str):
    if not engine_registry.get_engine(engine_id):
        raise AppException(404, "ENGINE_NOT_FOUND", "Engine not found")
    path = settings_store.output_dir() / "diagnostics" / f"{engine_id}-diagnosis.wav"
    if not path.exists():
        raise AppException(404, "DIAGNOSTIC_AUDIO_NOT_FOUND", "Diagnostic audio not found")
    return FileResponse(path)
