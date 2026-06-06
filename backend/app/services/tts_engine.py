"""TTS 引擎适配 - 调用 mlx_indextts v1/v2 进行推理"""


import asyncio
import os
import sys
import threading
import time
import uuid
from concurrent.futures import Future

from fastapi import HTTPException

from app.models.exceptions import AppException
from app.models.schemas import GenerationTask
from app.services import engine_registry
from app.services.settings_store import get as get_settings

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

OUTPUT_DIR = os.path.expanduser("~/VoiceStudio/outputs")


def _ensure_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def _find_reference_audio(voice_id: str | None) -> str | None:
    if not voice_id:
        return None
    from app.services.voice_store import get_voice
    voice = get_voice(voice_id)
    if not voice or not voice.reference_audio_ids:
        return None
    voice_dir = os.path.expanduser("~/VoiceStudio/voices")
    for audio_id in voice.reference_audio_ids:
        for ext in [".wav", ".mp3", ".flac", ".ogg", ".npz"]:
            candidate = os.path.join(voice_dir, f"{audio_id}{ext}")
            if os.path.exists(candidate):
                return candidate
    return None


def _resolve_ref_audio(task: GenerationTask) -> str | None:
    """解析参考音频：优先用指定路径，否则从声音资产查找"""
    ref_path = task.parameters.get("reference_audio_path") or task.parameters.get("ref_audio_path")
    if ref_path and os.path.exists(ref_path):
        return ref_path
    return _find_reference_audio(task.voice_id)

def _build_emotion(params: dict) -> str | dict | None:
    """构建情绪参数（v2 专用）"""
    emotion_mode = params.get("emotion_mode", "follow_reference")
    if emotion_mode == "follow_reference":
        return None
    if emotion_mode == "emotion_vector":
        return params.get("emotion_values")  # dict[str, float]
    if emotion_mode == "emotion_text":
        return params.get("emotion_text")  # str
    return None


def _run_in_thread(fn):
    """Run fn() in a dedicated thread, return a concurrent.futures.Future.
    Avoids asyncio.to_thread's default ThreadPoolExecutor which deadlocks with MPS."""
    future: Future = Future()
    def _worker():
        try:
            result = fn()
            future.set_result(result)
        except Exception as e:
            future.set_exception(e)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return future

def _get_model(engine_id: str, version: str = "v2"):
    instance = engine_registry.get_engine_instance(engine_id)
    if instance is None:
        raise AppException(503, "ENGINE_NOT_LOADED", f"Engine '{engine_id}' is not loaded. Call POST /api/engines/{engine_id}/start first.")
    return instance


async def synthesize(task: GenerationTask) -> dict:
    _ensure_dir()
    audio_id = uuid.uuid4().hex[:12]
    output_path = os.path.join(OUTPUT_DIR, f"{audio_id}.wav")

    try:
        version = task.parameters.get("engine_version", "v1")
        model = _get_model(task.engine_id, version)
        ref_audio = _resolve_ref_audio(task)
        params = task.parameters

        start = time.time()

        def _run_inference():
            if (version == "v2" or version == "indextts") and task.engine_id == "indextts":
                if not ref_audio:
                    raise HTTPException(
                        status_code=400,
                        detail="REFERENCE_AUDIO_REQUIRED: IndexTTS v2 requires reference audio. Provide voice_id or reference_audio_path."
                    )
                emotion = params.get("emotion") or _build_emotion(params)
                model.generate(
                    text=task.input_text,
                    reference_audio=ref_audio,
                    output_path=output_path,
                    temperature=params.get("temperature", 0.8),
                    top_p=params.get("top_p", 0.8),
                    top_k=params.get("top_k", 30),
                    repetition_penalty=params.get("repetition_penalty", 10.0),
                    diffusion_steps=params.get("diffusion_steps", 25),
                    cfg_rate=params.get("cfg_rate", 0.7),
                    emotion=emotion,
                    emo_alpha=params.get("emo_alpha", 0.6),
                    speed=params.get("speed", 1.0),
                    max_text_tokens_per_segment=params.get("max_text_tokens_per_segment", 120),
                    interval_silence=params.get("interval_silence", 200),
                    seed=params.get("seed"),
                    verbose=False,
                )
            elif task.engine_id == "indextts-v1":
                if not ref_audio:
                    raise HTTPException(
                        status_code=400,
                        detail="REFERENCE_AUDIO_REQUIRED: IndexTTS v1 requires reference audio. Provide voice_id or reference_audio_path."
                    )
                model.generate(
                    text=task.input_text,
                    ref_audio_path=ref_audio,
                    output_path=output_path,
                    temperature=params.get("temperature", 1.0),
                    speed=params.get("speed", 1.0),
                    max_mel_tokens=params.get("max_mel_tokens", 600),
                    max_text_tokens_per_segment=params.get("max_text_tokens_per_segment", 120),
                    top_p=params.get("top_p", 0.8),
                    top_k=params.get("top_k", 30),
                    repetition_penalty=params.get("repetition_penalty", 10.0),
                    interval_silence=params.get("interval_silence", 200),
                    seed=params.get("seed"),
                )
            elif task.engine_id == "omnivoice":
                model.generate(
                    text=task.input_text,
                    ref_audio_path=ref_audio,
                    ref_text=params.get("ref_text"),
                    language=params.get("language", "zh"),
                    emotion=params.get("emotion"),
                    speed=params.get("speed", 1.0),
                    output_path=output_path,
                )
            else:
                raise ValueError(f"Unknown engine: {task.engine_id}")

        future = _run_in_thread(_run_inference)
        while not future.done():
            await asyncio.sleep(0.1)
        future.result()  # raises if inference failed
        generation_time_ms = int((time.time() - start) * 1000)
        duration_ms = 0
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            duration_ms = max(0, int((file_size - 44) / (engine_registry.get_engine_sample_rate(task.engine_id) * 2) * 1000))

        return {
            "audio_id": audio_id,
            "duration_ms": duration_ms,
            "generation_time_ms": generation_time_ms,
        }

    except FileNotFoundError as e:
        raise AppException(503, "SERVICE_UNAVAILABLE", f"TTS model not available: {e}")
    except Exception as e:
        raise RuntimeError(f"TTS generation failed: {e}") from e


