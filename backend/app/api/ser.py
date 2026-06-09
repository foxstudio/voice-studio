"""SER (Speech Emotion Recognition) API 路由"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from app.models.exceptions import AppException
from app.models.schemas import SERPredictRequest, SERBatchPredictRequest, SEREmotionResult
from app.services import ser_service, voice_store

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/predict", response_model=SEREmotionResult)
async def predict_emotion(body: SERPredictRequest):
    """单个音色情绪识别"""
    voice = voice_store.get_voice(body.voice_id)
    if not voice:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    if not voice.reference_audio_ids:
        raise AppException(400, "NO_AUDIO", "Voice has no reference audio")

    vf = voice_store.get_file(voice.reference_audio_ids[0])
    if not vf:
        raise AppException(400, "NO_AUDIO", "Reference audio file not found")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, ser_service.predict_emotion, vf.path)

    if "error" in result:
        raise AppException(500, "SER_ERROR", result["error"])

    return SEREmotionResult(
        voice_id=body.voice_id,
        top_emotion=result["top_emotion"],
        emotion_scores=result["emotion_scores"],
    )


@router.post("/batch-predict")
async def batch_predict_emotions(body: SERBatchPredictRequest):
    """批量情绪识别"""
    if body.all:
        voices = voice_store.list_voices()
        voice_ids = [v.voice_id for v in voices if v.reference_audio_ids]
    else:
        voice_ids = body.voice_ids

    results: list[dict] = []
    loop = asyncio.get_event_loop()

    for vid in voice_ids:
        voice = voice_store.get_voice(vid)
        if not voice or not voice.reference_audio_ids:
            results.append({"voice_id": vid, "top_emotion": None, "emotion_scores": {}, "error": "No audio"})
            continue

        vf = voice_store.get_file(voice.reference_audio_ids[0])
        if not vf:
            results.append({"voice_id": vid, "top_emotion": None, "emotion_scores": {}, "error": "File not found"})
            continue

        try:
            result = await loop.run_in_executor(None, ser_service.predict_emotion, vf.path)
            if "error" in result:
                results.append({"voice_id": vid, "top_emotion": None, "emotion_scores": {}, "error": result["error"]})
            else:
                results.append({
                    "voice_id": vid,
                    "top_emotion": result["top_emotion"],
                    "emotion_scores": result["emotion_scores"],
                })
        except Exception as e:
            log.exception("SER failed for voice %s", vid)
            results.append({"voice_id": vid, "top_emotion": None, "emotion_scores": {}, "error": str(e)})

    return {"results": results}
