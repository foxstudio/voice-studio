"""TTS 引擎适配 - 调用 mlx_indextts 进行推理"""

import os
import sys
import time
import uuid
import threading

from app.models.schemas import GenerationTask

# 确保 mlx_indextts 在 import 路径中
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

OUTPUT_DIR = os.path.expanduser("~/VoiceStudio/outputs")

# 模型懒加载：只初始化一次，线程安全
_model_lock = threading.Lock()
_model_cache: dict[str, object] = {}


def _ensure_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def _get_model(engine_id: str):
    """懒加载 IndexTTS 模型"""
    with _model_lock:
        if engine_id in _model_cache:
            return _model_cache[engine_id]

        if engine_id == "indextts":
            from mlx_indextts.generate import IndexTTS
            model_dir = os.path.expanduser("~/VoiceStudio/models/IndexTTS")
            # 尝试默认模型路径
            if not os.path.exists(model_dir):
                # 尝试 huggingface cache
                from pathlib import Path
                candidates = [
                    Path.home() / ".cache" / "huggingface" / "hub" / "models--IndexTeam--IndexTTS2",
                    Path.home() / "models" / "IndexTTS",
                ]
                for c in candidates:
                    if c.exists():
                        model_dir = str(c)
                        break

            if not os.path.exists(model_dir):
                raise FileNotFoundError(
                    f"IndexTTS model not found at {model_dir}. "
                    "Please download the model first."
                )

            model = IndexTTS.load_model(model_dir)
            _model_cache[engine_id] = model
            return model

        raise ValueError(f"Unknown engine: {engine_id}")


def _find_reference_audio(voice_id: str | None) -> str | None:
    """查找声音资产的参考音频路径"""
    if not voice_id:
        return None

    from app.services.voice_store import get_voice
    voice = get_voice(voice_id)
    if not voice or not voice.reference_audio_ids:
        return None

    voice_dir = os.path.expanduser("~/VoiceStudio/voices")
    for audio_id in voice.reference_audio_ids:
        for ext in [".wav", ".mp3", ".flac", ".ogg"]:
            candidate = os.path.join(voice_dir, f"{audio_id}{ext}")
            if os.path.exists(candidate):
                return candidate
    return None


async def synthesize(task: GenerationTask) -> dict:
    """调用 MLX IndexTTS 引擎生成语音"""
    _ensure_dir()

    audio_id = uuid.uuid4().hex[:12]
    output_path = os.path.join(OUTPUT_DIR, f"{audio_id}.wav")

    start = time.time()

    try:
        model = _get_model(task.engine_id)
        ref_audio = _find_reference_audio(task.voice_id)

        # 从任务参数中提取 kwargs
        params = task.parameters if hasattr(task, 'parameters') and task.parameters else {}

        if ref_audio:
            model.infer(
                audio_prompt=ref_audio,
                text=task.input_text,
                output_path=output_path,
                verbose=False,
                temperature=params.get("temperature", 1.0),
                top_p=params.get("top_p", 0.8),
                speed=params.get("speed", 1.0),
                seed=params.get("seed"),
            )
        else:
            # 无参考音频，使用模型默认
            audio = model.generate(text=task.input_text)
            model.save_audio(audio, output_path)

        generation_time_ms = int((time.time() - start) * 1000)

        # 估算音频时长
        duration_ms = 0
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            # WAV: 44 header + 44100 * 2 bytes/sec (16bit mono)
            duration_ms = max(0, int((file_size - 44) / (44100 * 2) * 1000))

        return {
            "audio_id": audio_id,
            "duration_ms": duration_ms,
            "generation_time_ms": generation_time_ms,
        }

    except FileNotFoundError as e:
        # 模型未找到，回退到 mock
        return await _mock_synthesize(task, audio_id)
    except Exception as e:
        raise RuntimeError(f"TTS generation failed: {e}") from e


async def _mock_synthesize(task: GenerationTask, audio_id: str | None = None) -> dict:
    """模拟生成（模型未安装时使用）"""
    import asyncio
    await asyncio.sleep(2)
    if not audio_id:
        audio_id = uuid.uuid4().hex[:12]
    return {
        "audio_id": audio_id,
        "duration_ms": len(task.input_text) * 150,
        "generation_time_ms": 2000,
    }
