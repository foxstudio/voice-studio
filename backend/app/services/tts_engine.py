"""TTS 引擎适配 - 调用 mlx_indextts 进行推理"""

import os
import time
import uuid

from app.models.schemas import GenerationTask

OUTPUT_DIR = os.path.expanduser("~/VoiceStudio/outputs")


def _ensure_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


async def synthesize(task: GenerationTask) -> dict:
    """调用 MLX IndexTTS 引擎生成语音"""
    _ensure_dir()

    # 动态导入 mlx_indextts
    try:
        from mlx_indextts.generate import generate_audio
    except ImportError:
        # 如果 mlx_indextts 不可用，返回模拟结果
        return await _mock_synthesize(task)

    audio_id = uuid.uuid4().hex[:12]
    output_path = os.path.join(OUTPUT_DIR, f"{audio_id}.wav")

    start = time.time()

    # 构建生成参数
    kwargs = {
        "text": task.input_text,
        "output_path": output_path,
    }

    # 如果有声音资产，查找参考音频路径
    if task.voice_id:
        from app.services.voice_store import get_voice
        voice = get_voice(task.voice_id)
        if voice and voice.reference_audio_ids:
            voice_dir = os.path.expanduser("~/VoiceStudio/voices")
            for ext in [".wav", ".mp3", ".flac"]:
                candidate = os.path.join(voice_dir, f"{voice.reference_audio_ids[0]}{ext}")
                if os.path.exists(candidate):
                    kwargs["reference_audio"] = candidate
                    break

    # 调用推理
    try:
        generate_audio(**kwargs)
        duration_ms = int((time.time() - start) * 1000)
        # 获取音频时长
        file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        est_duration = int(file_size / (44100 * 2)) if file_size > 0 else 0

        return {
            "audio_id": audio_id,
            "duration_ms": est_duration,
            "generation_time_ms": duration_ms,
        }
    except Exception as e:
        raise RuntimeError(f"TTS generation failed: {e}") from e


async def _mock_synthesize(task: GenerationTask) -> dict:
    """模拟生成（用于开发测试，无模型时使用）"""
    import asyncio
    await asyncio.sleep(2)  # 模拟推理时间
    audio_id = uuid.uuid4().hex[:12]
    return {
        "audio_id": audio_id,
        "duration_ms": len(task.input_text) * 150,
        "generation_time_ms": 2000,
    }
