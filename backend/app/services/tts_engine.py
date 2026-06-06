"""TTS 引擎适配 - 调用 mlx_indextts v1/v2 进行推理"""


import os
import sys
import time
import uuid
import subprocess
import json as _json


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
        _get_model(task.engine_id, version)  # validate engine
        ref_audio = _resolve_ref_audio(task)
        params = task.parameters

        start = time.time()

        # Build subprocess arguments for isolated inference
        if (version == "v2" or version == "indextts") and task.engine_id == "indextts":
            if not ref_audio:
                raise HTTPException(
                    status_code=400,
                    detail="REFERENCE_AUDIO_REQUIRED: IndexTTS v2 requires reference audio. Provide voice_id or reference_audio_path."
                )
            emotion = params.get("emotion") or _build_emotion(params)
            kwargs = dict(
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
            )
            engine_id = "indextts"
        elif task.engine_id == "indextts-v1":
            if not ref_audio:
                raise HTTPException(
                    status_code=400,
                    detail="REFERENCE_AUDIO_REQUIRED: IndexTTS v1 requires reference audio. Provide voice_id or reference_audio_path."
                )
            kwargs = dict(
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
            engine_id = "indextts-v1"
        elif task.engine_id == "omnivoice":
            import requests as _requests
            import shutil

            gradio_base = "http://127.0.0.1:7861"
            ref_audio_path = ref_audio
            language = params.get("language", "zh")
            ref_text = params.get("ref_text", "")
            emotion_param = params.get("emotion", "")
            speed = params.get("speed", 1.0)

            payload = {
                "data": [
                    task.input_text,
                    "Auto",
                    32,
                    2.0,
                    True,
                    speed,
                    None,
                    True,
                    True,
                    "Auto",
                    "Auto",
                    "Auto",
                    "Auto",
                    "Auto",
                    "Auto",
                ]
            }

            resp = _requests.post(
                f"{gradio_base}/gradio_api/call/_design_fn",
                json=payload,
                timeout=300
            )
            resp.raise_for_status()
            event_id = resp.json()["event_id"]

            result_resp = _requests.get(
                f"{gradio_base}/gradio_api/call/_design_fn/{event_id}",
                timeout=300
            )
            result_data = None
            for line in result_resp.text.strip().split("\n"):
                if line.startswith("data:"):
                    result_data = _json.loads(line[5:].strip())

            if result_data is None:
                raise RuntimeError("No data received from Gradio API")

            output_audio_info = result_data[0]
            if isinstance(output_audio_info, dict) and "path" in output_audio_info:
                shutil.copy2(output_audio_info["path"], output_path)
            else:
                raise RuntimeError(f"Unexpected Gradio output: {output_audio_info}")

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
        else:
            raise ValueError(f"Unknown engine: {task.engine_id}")

        # Run inference in isolated subprocess (avoids MPS/PyTorch deadlock in uvicorn)
        backend_root = os.path.join(_project_root, "backend")
        subprocess_env = {**os.environ, "PYTHONPATH": backend_root}
        proc = subprocess.run(
            [sys.executable, "-m", "app.services.inference_runner"],
            input=_json.dumps({"engine_id": engine_id, "kwargs": kwargs}),
            capture_output=True,
            text=True,
            timeout=120,
            cwd=_project_root,
            env=subprocess_env,
        )

        if proc.returncode != 0:
            try:
                err_result = _json.loads(proc.stdout) if proc.stdout else {}
            except _json.JSONDecodeError:
                err_result = {}
            error_msg = err_result.get("error") or (proc.stderr[:500] if proc.stderr else "unknown error")
            raise RuntimeError(f"Inference subprocess failed ({proc.returncode}): {error_msg}")

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


