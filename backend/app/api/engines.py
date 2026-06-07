from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter

from app.models.exceptions import AppException
from app.models.schemas import EngineAudioDiagnosisRequest, EngineDetail
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
        if engine_id == "indextts-v2" and not ref:
            raise AppException(400, "REFERENCE_AUDIO_REQUIRED", "IndexTTS v2 diagnosis requires a reference audio")
        if engine_id == "indextts-v2":
            kwargs.update({"max_mel_tokens": 1500, "diffusion_steps": 25, "cfg_rate": 0.7, "emotion": data.emotion, "emo_alpha": 0.6})
        else:
            kwargs.update({"language": data.language, "ref_text": None, "emotion": None, "emotion_text": data.emotion_text})
        result = await asyncio.to_thread(engine_registry.run_isolated, engine_id, kwargs, 300)
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
