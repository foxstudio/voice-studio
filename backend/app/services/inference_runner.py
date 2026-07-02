from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import logging
import tempfile
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from app.services import audio_tools, confucius4_paths, qwen3_tts_paths

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

logger = logging.getLogger(__name__)
_model_cache: dict = {}
_model_cache_lock = threading.Lock()
DEFAULT_EXTERNAL_ROOTS = {
    "emotivoice": Path("/Users/foxmacstudio/Projects/tts-engine-lab/EmotiVoice"),
    "f5-tts": Path("/Users/foxmacstudio/Projects/tts-engine-lab/F5-TTS"),
    "cosyvoice-sft": Path("/Users/foxmacstudio/Projects/tts-engine-lab/CosyVoice"),
    "cosyvoice-zero-shot": Path("/Users/foxmacstudio/Projects/tts-engine-lab/CosyVoice"),
    "qwen3-tts-mlx-0.6b": qwen3_tts_paths.DEFAULT_ROOT,
}


def evict_cache(engine_id: str) -> None:
    """Remove a model from the cache when its engine stops."""
    with _model_cache_lock:
        _model_cache.pop(engine_id, None)
        for key in [key for key in _model_cache if str(key).startswith(f"{engine_id}:")]:
            _model_cache.pop(key, None)
def _audio_meta(path: str, sample_rate: int) -> dict:
    try:
        import soundfile as sf

        info = sf.info(path)
        return {"duration_ms": int(info.frames / info.samplerate * 1000), "sample_rate": info.samplerate}
    except Exception:
        size = os.path.getsize(path) if os.path.exists(path) else 0
        return {"duration_ms": max(0, int((size - 44) / (sample_rate * 2) * 1000)), "sample_rate": sample_rate}


def _external_root(engine_id: str) -> Path:
    env_names = {
        "emotivoice": "VOICE_STUDIO_EMOTIVOICE_ROOT",
        "f5-tts": "VOICE_STUDIO_F5_TTS_ROOT",
        "cosyvoice-sft": "VOICE_STUDIO_COSYVOICE_ROOT",
        "cosyvoice-zero-shot": "VOICE_STUDIO_COSYVOICE_ROOT",
        "qwen3-tts-mlx-0.6b": "VOICE_STUDIO_QWEN3_TTS_ROOT",
    }
    env_value = os.environ.get(env_names[engine_id])
    if env_value:
        return Path(env_value).expanduser()
    fallback = DEFAULT_EXTERNAL_ROOTS.get(engine_id)
    if fallback and fallback.exists():
        return fallback
    raise RuntimeError(
        f"Environment variable {env_names[engine_id]} is not set. "
        f"Please set it to the root directory of the {engine_id} engine."
    )


def _external_python(root: Path) -> str:
    python = root / ".venv" / "bin" / "python"
    if not python.exists():
        raise RuntimeError(f"External Python not found: {python}")
    return str(python)


def _prepare_confucius4_runtime(root: Path) -> None:
    if not (root / "mlx_audio" / "tts" / "models" / "confucius4" / "confucius4.py").exists():
        raise RuntimeError(f"Confucius4 MLX runtime not found: {root}")
    root_str = str(root)
    if sys.path[0] != root_str:
        sys.path.insert(0, root_str)
    for name in list(sys.modules):
        if name == "mlx_audio" or name.startswith("mlx_audio."):
            sys.modules.pop(name, None)


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except Exception as e:
            logger.warning("File lock acquire failed for %s: %s", path, e)
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.warning("File lock release failed for %s: %s", path, e)
        handle.close()


def _run_external(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = {**os.environ, **(env or {})}
    proc = subprocess.run(cmd, cwd=str(cwd), env=merged_env, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        detail = "\n".join(part for part in [proc.stdout[-1600:], proc.stderr[-2000:]] if part.strip())
        raise RuntimeError(detail or f"External command failed: {' '.join(cmd)}")
    return proc


def _build_indextts_v2_kwargs(**kwargs):
    engine_id = kwargs.pop("engine_id", "indextts-v2")
    output_path = kwargs.pop("output_path")
    model_dir = kwargs.pop("model_dir")
    device = kwargs.pop("device", "mps")
    return engine_id, output_path, model_dir, device, kwargs


def run_indextts_v2(**kwargs):
    from mlx_indextts.generate_v2 import IndexTTSv2

    engine_id, output_path, model_dir, device, gen_kwargs = _build_indextts_v2_kwargs(**kwargs)
    start = time.perf_counter()
    with _model_cache_lock:
        model = _model_cache.get(engine_id)
        if model is None:
            model = IndexTTSv2(model_dir, device=device)
            _model_cache[engine_id] = model
    model.generate(output_path=output_path, **gen_kwargs)
    meta = _audio_meta(output_path, 22050)
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta



def _build_indextts_v1_kwargs(**kwargs):
    engine_id = kwargs.pop("engine_id", "indextts")
    output_path = kwargs.pop("output_path")
    model_dir = kwargs.pop("model_dir")
    ref_audio = kwargs.pop("reference_audio")
    text = kwargs.pop("text")
    return engine_id, output_path, model_dir, ref_audio, text, kwargs

def run_indextts_v1(**kwargs):
    from mlx_indextts.generate import IndexTTS

    engine_id, output_path, model_dir, ref_audio, text, gen_kwargs = _build_indextts_v1_kwargs(**kwargs)
    start = time.perf_counter()
    with _model_cache_lock:
        model = _model_cache.get(engine_id)
        if model is None:
            model = IndexTTS.load_model(model_dir)
            _model_cache[engine_id] = model
    audio = model.generate(text=text, ref_audio=ref_audio, **gen_kwargs)
    model.save_audio(audio, output_path)
    meta = _audio_meta(output_path, 24000)
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta



def _build_omnivoice_kwargs(**kwargs):
    engine_id = kwargs.pop("engine_id", "omnivoice")
    output_path = kwargs.pop("output_path")
    text = kwargs.pop("text")
    ref_audio = kwargs.pop("reference_audio", None)
    ref_text = kwargs.pop("ref_text", None)
    language = kwargs.pop("language", "auto")
    instruction = kwargs.pop("emotion_text", None) or kwargs.pop("emotion", None)
    speed = kwargs.pop("speed", 1.0)
    device = kwargs.pop("device", "mps")
    diffusion_steps = kwargs.pop("diffusion_steps", None) or kwargs.pop("num_step", None)
    guidance_scale = kwargs.pop("guidance_scale", None)
    duration = kwargs.pop("duration", None)
    audio_chunk_duration = kwargs.pop("audio_chunk_duration", None)
    audio_chunk_threshold = kwargs.pop("audio_chunk_threshold", None)

    gen_kwargs = {"text": text}
    if language and language != "auto":
        gen_kwargs["language"] = language
    if ref_audio:
        gen_kwargs["ref_audio"] = ref_audio
        if ref_text is not None:
            gen_kwargs["ref_text"] = ref_text
    elif instruction:
        gen_kwargs["instruct"] = instruction
    generation_config: dict[str, float | int] = {}
    if diffusion_steps:
        generation_config["num_step"] = int(diffusion_steps)
    if guidance_scale is not None:
        generation_config["guidance_scale"] = float(guidance_scale)
    if audio_chunk_duration is not None:
        generation_config["audio_chunk_duration"] = float(audio_chunk_duration)
    if audio_chunk_threshold is not None:
        generation_config["audio_chunk_threshold"] = float(audio_chunk_threshold)
    if generation_config:
        gen_kwargs["generation_config"] = generation_config

    target_duration = float(duration) if duration is not None and float(duration) > 0 else None
    postprocess_speed = 1.0 if target_duration else float(speed or 1.0)
    return engine_id, output_path, device, gen_kwargs, postprocess_speed, target_duration

def run_omnivoice(**kwargs):
    engine_id, output_path, device, gen_kwargs, postprocess_speed, target_duration = _build_omnivoice_kwargs(**kwargs)
    start = time.perf_counter()
    with _model_cache_lock:
        model = _model_cache.get(engine_id)
        if model is None:
            from omnivoice import OmniVoice

            load_kwargs = {"device_map": device}
            if str(device).startswith("mps"):
                import torch

                load_kwargs["attn_implementation"] = "eager"
                load_kwargs["dtype"] = torch.float32
            model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", **load_kwargs)
            _model_cache[engine_id] = model
    if isinstance(gen_kwargs.get("generation_config"), dict):
        from omnivoice.models.omnivoice import OmniVoiceGenerationConfig

        gen_kwargs["generation_config"] = OmniVoiceGenerationConfig.from_dict(gen_kwargs["generation_config"])
    result = model.generate(**gen_kwargs)
    if isinstance(result, (str, Path)):
        shutil.copy2(str(result), output_path)
    else:
        import soundfile as sf

        audio = np.concatenate([np.asarray(x).reshape(-1) for x in result]).astype(np.float32)
        sf.write(output_path, np.clip(audio, -1, 1), getattr(model, "sampling_rate", 24000), subtype="PCM_16")
    if target_duration:
        current = audio_tools.probe_audio(output_path)
        current_seconds = (current.get("duration_ms") or 0) / 1000
        if current_seconds > 0:
            audio_tools.time_stretch_file(output_path, current_seconds / target_duration)
    elif postprocess_speed != 1.0:
        audio_tools.time_stretch_file(output_path, postprocess_speed)
    meta = _audio_meta(output_path, getattr(model, "sampling_rate", 24000))
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta



def _build_mimo_kwargs(**kwargs):
    output_path = kwargs.pop("output_path")
    fmt = Path(output_path).suffix.lstrip(".") or "wav"
    return {
        "output_path": output_path,
        "audio_format": fmt,
        "base_url": kwargs.get("base_url", ""),
        "api_key": kwargs.get("api_key", ""),
        "text": kwargs["text"],
        "voice": kwargs.get("voice", "mimo_default"),
        "instruction": kwargs.get("instruction"),
        "model": kwargs.get("model", "mimo-v2.5-tts"),
        "voice_design_prompt": kwargs.get("voice_design_prompt"),
        "optimize_text_preview": kwargs.get("optimize_text_preview", False),
        "reference_audio_path": kwargs.get("reference_audio_path"),
        "temperature": kwargs.get("temperature"),
        "top_p": kwargs.get("top_p"),
    }

def run_mimo_tts(**kwargs):
    from app.services import mimo_client

    params = _build_mimo_kwargs(**kwargs)
    start = time.perf_counter()
    result = mimo_client.generate_tts(**params)
    meta = _audio_meta(params["output_path"], 24000)
    meta.update({"output_path": result["output_path"], "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta



def _build_doubao_kwargs(**kwargs):
    output_path = kwargs.pop("output_path")
    fmt = Path(output_path).suffix.lstrip(".") or "mp3"
    return {
        "output_path": output_path,
        "audio_format": fmt,
        "base_url": kwargs.get("base_url", ""),
        "api_key": kwargs.get("api_key", ""),
        "text": kwargs["text"],
        "speaker": kwargs.get("speaker") or "zh_female_vv_uranus_bigtts",
        "resource_id": kwargs.get("resource_id") or "seed-tts-2.0",
        "style_instruction": kwargs.get("style_instruction"),
        "speed": kwargs.get("speed"),
    }


def run_doubao_tts(**kwargs):
    from app.services import doubao_client

    params = _build_doubao_kwargs(**kwargs)
    start = time.perf_counter()
    result = doubao_client.generate_tts_unidirectional_http(**params)
    meta = _audio_meta(params["output_path"], 24000)
    meta.update(
        {
            "output_path": result["output_path"],
            "generation_time_ms": int((time.perf_counter() - start) * 1000),
            "provider_request_id": result.get("request_id"),
            "provider_logid": result.get("logid"),
        }
    )
    return meta


def _build_emotivoice_kwargs(**kwargs):
    root = _external_root("emotivoice")
    output_path = kwargs.pop("output_path")
    text = kwargs.pop("text").strip()
    speaker_id = str(kwargs.pop("speaker_id", "") or "8051")
    prompt = str(kwargs.pop("prompt", "") or kwargs.pop("emotion", "") or "开心")
    python = _external_python(root)
    return root, output_path, text, speaker_id, prompt, python

def run_emotivoice(**kwargs):
    root, output_path, text, speaker_id, prompt, python = _build_emotivoice_kwargs(**kwargs)
    if not text:
        raise RuntimeError("Text is empty")
    start = time.perf_counter()
    with _file_lock(root / ".voice_studio" / "emotivoice.lock"), tempfile.TemporaryDirectory(prefix="voice-studio-emotivoice-") as tmp:
        tmp_dir = Path(tmp)
        plain = tmp_dir / "plain.txt"
        plain.write_text(text + "\n", encoding="utf-8")
        phoneme_proc = _run_external([python, "frontend.py", str(plain)], root)
        phoneme_lines = [line.strip() for line in phoneme_proc.stdout.splitlines() if line.strip()]
        if not phoneme_lines:
            raise RuntimeError("EmotiVoice phoneme frontend returned no text")
        test_file = tmp_dir / "tts_input.txt"
        test_file.write_text(f"{speaker_id}|{prompt}|{phoneme_lines[-1]}|{text}\n", encoding="utf-8")
        _run_external(
            [
                python,
                "inference_am_vocoder_joint.py",
                "--logdir",
                "prompt_tts_open_source_joint",
                "--config_folder",
                "config/joint",
                "--checkpoint",
                "g_00140000",
                "--test_file",
                str(test_file),
            ],
            root,
        )
        generated = root / "outputs" / "prompt_tts_open_source_joint" / "test_audio" / "audio" / "g_00140000" / "1.wav"
        if not generated.exists():
            raise RuntimeError(f"EmotiVoice output missing: {generated}")
        shutil.copy2(generated, output_path)
    meta = _audio_meta(output_path, 16000)
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta


def _build_confucius4_mlx_kwargs(**kwargs):
    engine_id = kwargs.pop("engine_id", confucius4_paths.ENGINE_ID)
    output_path = kwargs.pop("output_path")
    text = kwargs.pop("text").strip()
    ref_audio = kwargs.pop("reference_audio", None)
    model_dir = confucius4_paths.model_dir(kwargs.pop("model_dir", None))
    runtime_root = Path(kwargs.pop("runtime_root", None) or confucius4_paths.runtime_root())
    language = str(kwargs.pop("language", "zh") or "zh")
    temperature = float(kwargs.pop("temperature", 0.8) or 0.8)
    top_k = int(kwargs.pop("top_k", 30) or 30)
    top_p = float(kwargs.pop("top_p", 0.8) or 0.8)
    repetition_penalty = float(kwargs.pop("repetition_penalty", 10.0) or 10.0)
    diffusion_steps = int(kwargs.pop("diffusion_steps", 25) or 25)
    cfg_rate = float(kwargs.pop("cfg_rate", 0.7) or 0.7)
    seed = int(kwargs.pop("seed", 0) or 0)
    return engine_id, output_path, text, ref_audio, model_dir, runtime_root, language, temperature, top_k, top_p, repetition_penalty, diffusion_steps, cfg_rate, seed


def _confucius4_ref_audio_16k(ref_audio: str, tmp_dir: Path) -> str:
    import soundfile as sf

    audio, sample_rate = sf.read(ref_audio)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if int(sample_rate) == 16000:
        return ref_audio
    import librosa

    converted = librosa.resample(audio, orig_sr=int(sample_rate), target_sr=16000)
    out = tmp_dir / "reference-16k.wav"
    sf.write(out, np.asarray(converted, dtype=np.float32), 16000, subtype="PCM_16")
    return str(out)


def _confucius4_char_count(text: str) -> int:
    return len("".join(text.split()))


def _confucius4_split_text(text: str, max_chars: int = 24) -> list[str]:
    import re

    normalized = text.strip()
    if _confucius4_char_count(normalized) <= max_chars:
        return [normalized] if normalized else []

    raw_parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", normalized)
    units: list[str] = []
    for part in [item.strip() for item in raw_parts if item.strip()]:
        if _confucius4_char_count(part) <= max_chars:
            units.append(part)
            continue
        weak_parts = re.split(r"(?<=[，,、：:])\s*", part)
        for weak in [item.strip() for item in weak_parts if item.strip()]:
            if _confucius4_char_count(weak) <= max_chars:
                units.append(weak)
            else:
                chunk = ""
                for ch in weak:
                    chunk += ch
                    if _confucius4_char_count(chunk) >= max_chars:
                        units.append(chunk.strip())
                        chunk = ""
                if chunk.strip():
                    units.append(chunk.strip())

    merged: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{unit}" if current else unit
        if current and _confucius4_char_count(candidate) > max_chars:
            merged.append(current.strip())
            current = unit
        else:
            current = candidate
    if current.strip():
        merged.append(current.strip())
    return merged


def run_confucius4_mlx(**kwargs):
    engine_id, output_path, text, ref_audio, model_dir, runtime_root, language, temperature, top_k, top_p, repetition_penalty, diffusion_steps, cfg_rate, seed = _build_confucius4_mlx_kwargs(**kwargs)
    if not text:
        raise RuntimeError("Text is empty")
    if not ref_audio:
        raise RuntimeError("REFERENCE_AUDIO_REQUIRED")
    if not Path(ref_audio).exists():
        raise RuntimeError("REFERENCE_AUDIO_NOT_FOUND")
    missing_model = confucius4_paths.missing_model_files(model_dir)
    missing_runtime = confucius4_paths.missing_runtime_files(runtime_root)
    if missing_model or missing_runtime:
        missing = [*(f"model:{item}" for item in missing_model), *(f"runtime:{item}" for item in missing_runtime)]
        raise RuntimeError(f"Confucius4 MLX files missing: {', '.join(missing)}")

    start = time.perf_counter()
    with _file_lock(model_dir / ".voice_studio" / "confucius4-mlx.lock"), tempfile.TemporaryDirectory(prefix="voice-studio-confucius4-") as tmp:
        _prepare_confucius4_runtime(runtime_root)
        from mlx_audio.tts.utils import load

        cache_key = f"{engine_id}:{model_dir}"
        with _model_cache_lock:
            model = _model_cache.get(cache_key)
            if model is None:
                model = load(str(model_dir), lazy=False, strict=False)
                _model_cache[cache_key] = model
        import soundfile as sf

        ref_16k = _confucius4_ref_audio_16k(ref_audio, Path(tmp))
        parts = _confucius4_split_text(text)
        audio_parts: list[np.ndarray] = []
        sample_rate = 22050
        for index, part in enumerate(parts):
            result = next(
                model.generate(
                    text=part,
                    ref_audio=ref_16k,
                    lang=language,
                    temperature=temperature,
                    top_k=top_k,
                    top_p=top_p,
                    repetition_penalty=repetition_penalty,
                    diffusion_steps=diffusion_steps,
                    cfg_rate=cfg_rate,
                    seed=seed + index,
                )
            )
            sample_rate = int(getattr(result, "sample_rate", 22050) or 22050)
            audio_parts.append(np.asarray(result.audio).reshape(-1).astype(np.float32))
            if index < len(parts) - 1:
                audio_parts.append(np.zeros(int(sample_rate * 0.18), dtype=np.float32))

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        audio = np.concatenate(audio_parts) if audio_parts else np.zeros(0, dtype=np.float32)
        sf.write(out, np.clip(audio, -1, 1), sample_rate, subtype="PCM_16")
    meta = _audio_meta(output_path, 22050)
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta


def _build_qwen3_tts_kwargs(**kwargs):
    root = _external_root(qwen3_tts_paths.ENGINE_ID)
    output_path = kwargs.pop("output_path")
    text = kwargs.pop("text").strip()
    ref_audio = kwargs.pop("reference_audio", None)
    ref_text = (kwargs.pop("ref_text", None) or "").strip()
    language = str(kwargs.pop("language", "zh") or "zh")
    speaker_id = str(kwargs.pop("speaker_id", "") or "Vivian")
    instruction = str(kwargs.pop("style_instruction", "") or "Normal tone")
    voice_design_prompt = str(kwargs.pop("voice_design_prompt", "") or "").strip()
    speed = float(kwargs.pop("speed", 1.0) or 1.0)
    temperature = float(kwargs.pop("temperature", 0.7) or 0.7)
    top_p = float(kwargs.pop("top_p", 0.9) or 0.9)
    top_k = int(kwargs.pop("top_k", 50) or 50)
    repetition_penalty = float(kwargs.pop("repetition_penalty", 1.1) or 1.1)
    max_tokens = int(kwargs.pop("max_tokens", 1200) or 1200)
    cfg_scale = kwargs.pop("cfg_scale", None)
    ddpm_steps = kwargs.pop("ddpm_steps", None)
    return root, output_path, text, ref_audio, ref_text, language, speaker_id, instruction, voice_design_prompt, speed, temperature, top_p, top_k, repetition_penalty, max_tokens, cfg_scale, ddpm_steps


def run_qwen3_tts(**kwargs):
    root, output_path, text, ref_audio, ref_text, language, speaker_id, instruction, voice_design_prompt, speed, temperature, top_p, top_k, repetition_penalty, max_tokens, cfg_scale, ddpm_steps = _build_qwen3_tts_kwargs(**kwargs)
    if not text:
        raise RuntimeError("Text is empty")
    python = _external_python(root)
    model_kind = "base" if ref_audio else "design" if voice_design_prompt else "custom"
    model_dir = qwen3_tts_paths.model_dir(model_kind)
    if not model_dir.exists():
        raise RuntimeError(f"Qwen3-TTS model not found: {model_dir}")
    if ref_audio and not Path(ref_audio).exists():
        raise RuntimeError("REFERENCE_AUDIO_NOT_FOUND")
    if ref_audio and not ref_text:
        ref_text = "."
    payload = {
        "text": text,
        "reference_audio": ref_audio,
        "ref_text": ref_text,
        "language": language,
        "speaker_id": speaker_id,
        "instruction": instruction,
        "voice_design_prompt": voice_design_prompt,
        "speed": speed,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "repetition_penalty": repetition_penalty,
        "max_tokens": max_tokens,
        "cfg_scale": cfg_scale,
        "ddpm_steps": ddpm_steps,
        "model_dir": str(model_dir),
        "output_path": output_path,
    }
    script = r"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

from mlx_audio.tts.utils import load_model
from mlx_audio.tts.generate import generate_audio

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
model = load_model(Path(payload["model_dir"]))
with tempfile.TemporaryDirectory(prefix="voice-studio-qwen3-") as tmp:
    kwargs = {
        "model": model,
        "text": payload["text"],
        "lang_code": payload["language"],
        "speed": payload["speed"],
        "temperature": payload["temperature"],
        "top_p": payload["top_p"],
        "top_k": payload["top_k"],
        "repetition_penalty": payload["repetition_penalty"],
        "max_tokens": payload["max_tokens"],
        "output_path": tmp,
        "file_prefix": "qwen3",
        "play": False,
        "verbose": False,
    }
    if payload.get("cfg_scale") is not None:
        kwargs["cfg_scale"] = payload["cfg_scale"]
    if payload.get("ddpm_steps") is not None:
        kwargs["ddpm_steps"] = payload["ddpm_steps"]
    if payload.get("reference_audio"):
        kwargs["ref_audio"] = payload["reference_audio"]
        kwargs["ref_text"] = payload.get("ref_text") or "."
    elif payload.get("voice_design_prompt"):
        kwargs["instruct"] = payload["voice_design_prompt"]
    else:
        kwargs["voice"] = payload.get("speaker_id") or "Vivian"
        kwargs["instruct"] = payload.get("instruction") or ""
    generate_audio(**kwargs)
    candidates = sorted(Path(tmp).glob("*.wav"))
    if not candidates:
        raise RuntimeError("Qwen3-TTS returned no wav output")
    out = Path(payload["output_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidates[0], out)
"""
    start = time.perf_counter()
    with _file_lock(root / ".voice_studio" / "qwen3-tts.lock"), tempfile.TemporaryDirectory(prefix="voice-studio-qwen3-payload-") as tmp:
        payload_path = Path(tmp) / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _run_external([python, "-c", script, str(payload_path)], root)
    meta = _audio_meta(output_path, 24000)
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta



def _build_f5_tts_kwargs(**kwargs):
    root = _external_root("f5-tts")
    output_path = kwargs.pop("output_path")
    text = kwargs.pop("text").strip()
    ref_audio = kwargs.pop("reference_audio", None)
    ref_text = (kwargs.pop("ref_text", None) or "").strip()
    speed = float(kwargs.pop("speed", 1.0) or 1.0)
    nfe_step = int(kwargs.pop("nfe_step", 16) or 16)
    cfg_strength = float(kwargs.pop("cfg_strength", 1.5) or 1.5)
    target_rms = float(kwargs.pop("target_rms", 0.1) or 0.1)
    cross_fade_duration = float(kwargs.pop("cross_fade_duration", 0.15) or 0.15)
    sway_sampling_coef = float(kwargs.pop("sway_sampling_coef", -1.0) or -1.0)
    fix_duration_raw = float(kwargs.pop("fix_duration", 0.0) or 0.0)
    fix_duration = fix_duration_raw if fix_duration_raw > 0 else None
    remove_silence = bool(kwargs.pop("remove_silence", False))
    seed = kwargs.pop("seed", None)
    return root, output_path, text, ref_audio, ref_text, speed, nfe_step, cfg_strength, target_rms, cross_fade_duration, sway_sampling_coef, fix_duration, remove_silence, seed

def run_f5_tts(**kwargs):
    root, output_path, text, ref_audio, ref_text, speed, nfe_step, cfg_strength, target_rms, cross_fade_duration, sway_sampling_coef, fix_duration, remove_silence, seed = _build_f5_tts_kwargs(**kwargs)
    if not ref_audio:
        raise RuntimeError("REFERENCE_AUDIO_REQUIRED")
    if not ref_text:
        raise RuntimeError("REFERENCE_TEXT_REQUIRED")
    if not text:
        raise RuntimeError("Text is empty")
    python = _external_python(root)
    ckpt = root / "local_smoke" / "modelscope" / "F5-TTS_Emilia-ZH-EN" / "model_1250000.safetensors"
    vocab = root / "local_smoke" / "modelscope" / "F5-TTS_Emilia-ZH-EN" / "vocab.txt"
    payload = {
        "text": text,
        "ref_audio": ref_audio,
        "ref_text": ref_text,
        "output_path": output_path,
        "ckpt": str(ckpt),
        "vocab": str(vocab),
        "speed": speed,
        "nfe_step": nfe_step,
        "cfg_strength": cfg_strength,
        "target_rms": target_rms,
        "cross_fade_duration": cross_fade_duration,
        "sway_sampling_coef": sway_sampling_coef,
        "fix_duration": fix_duration,
        "remove_silence": remove_silence,
        "seed": seed,
    }
    script = r"""
import json
import sys
from pathlib import Path
from f5_tts.api import F5TTS

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
model = F5TTS(device="mps", ckpt_file=payload["ckpt"], vocab_file=payload["vocab"])
model.infer(
    ref_file=payload["ref_audio"],
    ref_text=payload["ref_text"],
    gen_text=payload["text"],
    file_wave=payload["output_path"],
    speed=payload["speed"],
    nfe_step=payload["nfe_step"],
    cfg_strength=payload["cfg_strength"],
    target_rms=payload["target_rms"],
    cross_fade_duration=payload["cross_fade_duration"],
    sway_sampling_coef=payload["sway_sampling_coef"],
    fix_duration=payload["fix_duration"],
    remove_silence=payload["remove_silence"],
    seed=payload["seed"],
)
"""
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="voice-studio-f5-") as tmp:
        payload_path = Path(tmp) / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _run_external([python, "-c", script, str(payload_path)], root, {"PYTHONPATH": str(root / "src")})
    meta = _audio_meta(output_path, 24000)
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta



def _build_cosyvoice_sft_kwargs(**kwargs):
    root = _external_root("cosyvoice-sft")
    output_path = kwargs.pop("output_path")
    text = kwargs.pop("text").strip()
    speaker_id = str(kwargs.pop("speaker_id", "") or "中文女")
    speed = float(kwargs.pop("speed", 1.0) or 1.0)
    return root, output_path, text, speaker_id, speed

def run_cosyvoice_sft(**kwargs):
    root, output_path, text, speaker_id, speed = _build_cosyvoice_sft_kwargs(**kwargs)
    if not text:
        raise RuntimeError("Text is empty")
    python = _external_python(root)
    model_dir = root / "pretrained_models" / "CosyVoice-300M-SFT"
    payload = {
        "text": text,
        "speaker_id": speaker_id,
        "speed": speed,
        "output_path": output_path,
        "model_dir": str(model_dir),
    }
    script = r"""
import json
import sys
from pathlib import Path

import torch
import torchaudio

sys.path.insert(0, ".")
sys.path.append("third_party/Matcha-TTS")
from cosyvoice.cli.cosyvoice import AutoModel

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
model = AutoModel(model_dir=payload["model_dir"])
speakers = model.list_available_spks()
speaker = payload["speaker_id"] if payload["speaker_id"] in speakers else speakers[0]
chunks = []
for item in model.inference_sft(payload["text"], speaker, stream=False, speed=payload["speed"]):
    speech = item["tts_speech"].detach().cpu()
    if speech.ndim == 1:
        speech = speech.unsqueeze(0)
    chunks.append(speech)
if not chunks:
    raise RuntimeError("CosyVoice returned no audio")
speech = chunks[0] if len(chunks) == 1 else torch.cat(chunks, dim=1)
torchaudio.save(payload["output_path"], speech, model.sample_rate)
"""
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="voice-studio-cosyvoice-") as tmp:
        payload_path = Path(tmp) / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _run_external([python, "-c", script, str(payload_path)], root)
    meta = _audio_meta(output_path, 22050)
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta



def _build_cosyvoice_zero_shot_kwargs(**kwargs):
    root = _external_root("cosyvoice-zero-shot")
    output_path = kwargs.pop("output_path")
    text = kwargs.pop("text").strip()
    ref_audio = kwargs.pop("reference_audio", None)
    ref_text = (kwargs.pop("ref_text", None) or "").strip()
    speed = float(kwargs.pop("speed", 1.0) or 1.0)
    return root, output_path, text, ref_audio, ref_text, speed

def run_cosyvoice_zero_shot(**kwargs):
    root, output_path, text, ref_audio, ref_text, speed = _build_cosyvoice_zero_shot_kwargs(**kwargs)
    if not ref_audio:
        raise RuntimeError("REFERENCE_AUDIO_REQUIRED")
    if not ref_text:
        raise RuntimeError("REFERENCE_TEXT_REQUIRED")
    if not text:
        raise RuntimeError("Text is empty")
    python = _external_python(root)
    model_dir = root / "pretrained_models" / "CosyVoice-300M-SFT"
    payload = {
        "text": text,
        "reference_audio": ref_audio,
        "ref_text": ref_text,
        "speed": speed,
        "output_path": output_path,
        "model_dir": str(model_dir),
    }
    script = r"""
import json
import sys
from pathlib import Path

import torch
import torchaudio

sys.path.insert(0, ".")
sys.path.append("third_party/Matcha-TTS")
from cosyvoice.cli.cosyvoice import AutoModel

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
model = AutoModel(model_dir=payload["model_dir"])
chunks = []
for item in model.inference_zero_shot(
    payload["text"],
    payload["ref_text"],
    payload["reference_audio"],
    stream=False,
    speed=payload["speed"],
):
    speech = item["tts_speech"].detach().cpu()
    if speech.ndim == 1:
        speech = speech.unsqueeze(0)
    chunks.append(speech)
if not chunks:
    raise RuntimeError("CosyVoice returned no audio")
speech = chunks[0] if len(chunks) == 1 else torch.cat(chunks, dim=1)
torchaudio.save(payload["output_path"], speech, model.sample_rate)
"""
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="voice-studio-cosyvoice-zero-") as tmp:
        payload_path = Path(tmp) / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _run_external([python, "-c", script, str(payload_path)], root)
    meta = _audio_meta(output_path, 22050)
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta


RUNNERS = {
    "indextts-v2": run_indextts_v2,
    "omnivoice": run_omnivoice,
    "emotivoice": run_emotivoice,
    "confucius4-mlx-int8": run_confucius4_mlx,
    "qwen3-tts-mlx-0.6b": run_qwen3_tts,
    "f5-tts": run_f5_tts,
    "cosyvoice-sft": run_cosyvoice_sft,
    "cosyvoice-zero-shot": run_cosyvoice_zero_shot,
    "mimo-v2.5-tts": run_mimo_tts,
    "mimo-v2.5-tts-preset": run_mimo_tts,
    "mimo-v2.5-tts-voicedesign": run_mimo_tts,
    "mimo-v2.5-tts-voiceclone": run_mimo_tts,
    "doubao-tts-preset": run_doubao_tts,
    "doubao-tts-voiceclone": run_doubao_tts,
}


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        kwargs = payload["kwargs"]
        kwargs["engine_id"] = payload["engine_id"]
        result = RUNNERS[payload["engine_id"]](**kwargs)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "traceback": traceback.format_exc()[-3000:]}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
