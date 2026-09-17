"""SER (Speech Emotion Recognition) API 路由"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from app.errors import AppException
from app.schemas.voice_studio import SERPredictRequest, SERPredictFileRequest, SERBatchPredictRequest, SEREmotionResult
from app.services import ser_service, voice_store

log = logging.getLogger(__name__)
router = APIRouter()


def _ensure_available() -> None:
    """情绪识别不可用时给出明确原因，而不是让 worker 报出底层堆栈。"""
    health = ser_service.health_check()
    if not health.get("healthy"):
        raise AppException(503, "SER_UNAVAILABLE", ser_service.unavailable_detail(health))


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

    _ensure_available()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, ser_service.predict_emotion, vf.path)

    if "error" in result:
        raise AppException(500, "SER_ERROR", result["error"])

    return SEREmotionResult(
        voice_id=body.voice_id,
        top_emotion=result["top_emotion"],
        emotion_scores=result["emotion_scores"],
    )


@router.post("/predict-file", response_model=SEREmotionResult)
async def predict_file_emotion(body: SERPredictFileRequest):
    """对已上传但尚未注册到音色库的参考音频做情绪识别。"""
    vf = voice_store.get_file(body.file_id)
    if not vf:
        raise AppException(400, "NO_AUDIO", "Reference audio file not found")

    _ensure_available()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, ser_service.predict_emotion, vf.path)

    if "error" in result:
        raise AppException(500, "SER_ERROR", result["error"])

    return SEREmotionResult(
        voice_id=body.file_id,
        top_emotion=result["top_emotion"],
        emotion_scores=result["emotion_scores"],
    )


@router.post("/batch-predict")
async def batch_predict_emotions(body: SERBatchPredictRequest):
    """批量情绪识别。

    所有音频交给同一个 worker，模型只加载一次；缺音频的音色直接返回占位结果。
    """
    if body.all:
        voices = voice_store.list_voices()
        voice_ids = [v.voice_id for v in voices if v.reference_audio_ids]
    else:
        voice_ids = body.voice_ids

    results: list[dict] = []
    pending: list[tuple[str, str]] = []
    for vid in voice_ids:
        voice = voice_store.get_voice(vid)
        if not voice or not voice.reference_audio_ids:
            results.append({"voice_id": vid, "top_emotion": None, "emotion_scores": {}, "error": "No audio"})
            continue
        vf = voice_store.get_file(voice.reference_audio_ids[0])
        if not vf:
            results.append({"voice_id": vid, "top_emotion": None, "emotion_scores": {}, "error": "File not found"})
            continue
        pending.append((vid, vf.path))

    if not pending:
        return {"results": results}

    _ensure_available()

    loop = asyncio.get_event_loop()
    predictions = await loop.run_in_executor(
        None, ser_service.predict_emotions, [path for _, path in pending]
    )

    for (vid, _path), prediction in zip(pending, predictions):
        if "error" in prediction:
            results.append({"voice_id": vid, "top_emotion": None, "emotion_scores": {}, "error": prediction["error"]})
        else:
            results.append(
                {
                    "voice_id": vid,
                    "top_emotion": prediction["top_emotion"],
                    "emotion_scores": prediction["emotion_scores"],
                }
            )

    return {"results": results}
