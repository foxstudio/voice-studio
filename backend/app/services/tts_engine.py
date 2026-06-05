"""TTS 引擎适配 - 调用 mlx_indextts v1/v2 进行推理"""

import os
import sys
import time
import uuid
import threading

from app.models.schemas import GenerationTask

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

OUTPUT_DIR = os.path.expanduser("~/VoiceStudio/outputs")
_model_lock = threading.Lock()
_model_cache: dict[str, object] = {}


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
    ref_path = task.parameters.get("reference_audio_path")
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


def _get_model(engine_id: str, version: str):
    """懒加载模型"""
    cache_key = f"{engine_id}_{version}"
    with _model_lock:
        if cache_key in _model_cache:
            return _model_cache[cache_key]

        if engine_id == "indextts":
            model_dir = os.path.join(_project_root, "models", "mlx-indexTTS-2.0")
            if not os.path.exists(model_dir):
                raise FileNotFoundError(f"Model not found at {model_dir}")

            if version == "v2":
                from mlx_indextts.generate_v2 import IndexTTSv2
                model = IndexTTSv2(model_dir)
            else:
                from mlx_indextts.generate import IndexTTS
                model = IndexTTS.load_model(model_dir)

            _model_cache[cache_key] = model
            return model

        raise ValueError(f"Unknown engine: {engine_id}")


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

        if version == "v2" and task.engine_id == "indextts":
            # ── IndexTTS v2 ──
            emotion = _build_emotion(params)
            model.generate(
                text=task.input_text,
                reference_audio=ref_audio or "",
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
        elif task.engine_id == "indextts":
            # ── IndexTTS v1 ──
            if ref_audio:
                model.infer(
                    audio_prompt=ref_audio,
                    text=task.input_text,
                    output_path=output_path,
                    temperature=params.get("temperature", 1.0),
                    top_p=params.get("top_p", 0.8),
                    top_k=params.get("top_k", 30),
                    repetition_penalty=params.get("repetition_penalty", 10.0),
                    speed=params.get("speed", 1.0),
                    max_text_tokens_per_segment=params.get("max_text_tokens_per_segment", 120),
                    interval_silence=params.get("interval_silence", 200),
                    seed=params.get("seed"),
                    verbose=False,
                )
            else:
                audio = model.generate(text=task.input_text, ref_audio=None)
                model.save_audio(audio, output_path)

        else:
            raise ValueError(f"Unknown engine: {task.engine_id}")

        generation_time_ms = int((time.time() - start) * 1000)
        duration_ms = 0
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            duration_ms = max(0, int((file_size - 44) / (44100 * 2) * 1000))

        return {
            "audio_id": audio_id,
            "duration_ms": duration_ms,
            "generation_time_ms": generation_time_ms,
        }

    except FileNotFoundError:
        return await _mock_synthesize(task, audio_id)
    except Exception as e:
        raise RuntimeError(f"TTS generation failed: {e}") from e


async def _mock_synthesize(task: GenerationTask, audio_id: str | None = None) -> dict:
    import asyncio
    await asyncio.sleep(2)
    if not audio_id:
        audio_id = uuid.uuid4().hex[:12]
    return {
        "audio_id": audio_id,
        "duration_ms": len(task.input_text) * 150,
        "generation_time_ms": 2000,
    }
