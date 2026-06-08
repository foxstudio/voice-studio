from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


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
    }
    defaults = {
        "emotivoice": "/Users/foxmacstudio/Projects/tts-engine-lab/EmotiVoice",
        "f5-tts": "/Users/foxmacstudio/Projects/tts-engine-lab/F5-TTS",
        "cosyvoice-sft": "/Users/foxmacstudio/Projects/tts-engine-lab/CosyVoice",
        "cosyvoice-zero-shot": "/Users/foxmacstudio/Projects/tts-engine-lab/CosyVoice",
    }
    return Path(os.environ.get(env_names[engine_id], defaults[engine_id])).expanduser()


def _external_python(root: Path) -> str:
    python = root / ".venv" / "bin" / "python"
    if not python.exists():
        raise RuntimeError(f"External Python not found: {python}")
    return str(python)


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        handle.close()


def _run_external(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = {**os.environ, **(env or {})}
    proc = subprocess.run(cmd, cwd=str(cwd), env=merged_env, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        detail = "\n".join(part for part in [proc.stdout[-1600:], proc.stderr[-2000:]] if part.strip())
        raise RuntimeError(detail or f"External command failed: {' '.join(cmd)}")
    return proc


def run_indextts_v2(**kwargs):
    from mlx_indextts.generate_v2 import IndexTTSv2

    output_path = kwargs.pop("output_path")
    model_dir = kwargs.pop("model_dir")
    start = time.perf_counter()
    model = IndexTTSv2(model_dir, device=kwargs.pop("device", "mps"))
    model.generate(output_path=output_path, **kwargs)
    meta = _audio_meta(output_path, 22050)
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta


def run_indextts_v1(**kwargs):
    from mlx_indextts.generate import IndexTTS

    output_path = kwargs.pop("output_path")
    model_dir = kwargs.pop("model_dir")
    ref_audio = kwargs.pop("reference_audio")
    text = kwargs.pop("text")
    start = time.perf_counter()
    model = IndexTTS.load_model(model_dir)
    audio = model.generate(text=text, ref_audio=ref_audio, **kwargs)
    model.save_audio(audio, output_path)
    meta = _audio_meta(output_path, 24000)
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta


def run_omnivoice(**kwargs):
    output_path = kwargs.pop("output_path")
    text = kwargs.pop("text")
    ref_audio = kwargs.pop("reference_audio", None)
    ref_text = kwargs.pop("ref_text", None)
    language = kwargs.pop("language", "auto")
    instruction = kwargs.pop("emotion_text", None) or kwargs.pop("emotion", None)
    speed = kwargs.pop("speed", 1.0)
    device = kwargs.pop("device", "mps")
    diffusion_steps = kwargs.pop("diffusion_steps", None) or kwargs.pop("num_step", None)
    start = time.perf_counter()
    from omnivoice import OmniVoice

    load_kwargs = {"device_map": device}
    if str(device).startswith("mps"):
        import torch

        load_kwargs["attn_implementation"] = "eager"
        load_kwargs["dtype"] = torch.float32
    model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", **load_kwargs)
    gen_kwargs = {"text": text}
    if language and language != "auto":
        gen_kwargs["language"] = language
    if ref_audio:
        gen_kwargs["ref_audio"] = ref_audio
        if ref_text is not None:
            gen_kwargs["ref_text"] = ref_text
    elif instruction:
        gen_kwargs["instruct"] = instruction
    if speed != 1.0:
        gen_kwargs["speed"] = speed
    if diffusion_steps:
        gen_kwargs["num_step"] = int(diffusion_steps)
    result = model.generate(**gen_kwargs)
    if isinstance(result, (str, Path)):
        shutil.copy2(str(result), output_path)
    else:
        import soundfile as sf

        audio = np.concatenate([np.asarray(x).reshape(-1) for x in result]).astype(np.float32)
        sf.write(output_path, np.clip(audio, -1, 1), getattr(model, "sampling_rate", 24000), subtype="PCM_16")
    meta = _audio_meta(output_path, getattr(model, "sampling_rate", 24000))
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta


def run_mimo_tts(**kwargs):
    from app.services import mimo_client

    output_path = kwargs.pop("output_path")
    start = time.perf_counter()
    fmt = Path(output_path).suffix.lstrip(".") or "wav"
    result = mimo_client.generate_tts(output_path=output_path, audio_format=fmt, **kwargs)
    meta = _audio_meta(output_path, 24000)
    meta.update({"output_path": result["output_path"], "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta


def run_emotivoice(**kwargs):
    root = _external_root("emotivoice")
    output_path = kwargs.pop("output_path")
    text = kwargs.pop("text").strip()
    speaker_id = str(kwargs.pop("speaker_id", "") or "8051")
    prompt = str(kwargs.pop("prompt", "") or kwargs.pop("emotion", "") or "开心")
    python = _external_python(root)
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


def run_f5_tts(**kwargs):
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
    remove_silence = bool(kwargs.pop("remove_silence", False))
    seed = kwargs.pop("seed", None)
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


def run_cosyvoice_sft(**kwargs):
    root = _external_root("cosyvoice-sft")
    output_path = kwargs.pop("output_path")
    text = kwargs.pop("text").strip()
    speaker_id = str(kwargs.pop("speaker_id", "") or "中文女")
    speed = float(kwargs.pop("speed", 1.0) or 1.0)
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

import torchaudio

sys.path.insert(0, ".")
sys.path.append("third_party/Matcha-TTS")
from cosyvoice.cli.cosyvoice import AutoModel

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
model = AutoModel(model_dir=payload["model_dir"])
speakers = model.list_available_spks()
speaker = payload["speaker_id"] if payload["speaker_id"] in speakers else speakers[0]
for item in model.inference_sft(payload["text"], speaker, stream=False, speed=payload["speed"]):
    torchaudio.save(payload["output_path"], item["tts_speech"].cpu(), model.sample_rate)
    break
"""
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="voice-studio-cosyvoice-") as tmp:
        payload_path = Path(tmp) / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _run_external([python, "-c", script, str(payload_path)], root)
    meta = _audio_meta(output_path, 22050)
    meta.update({"output_path": output_path, "generation_time_ms": int((time.perf_counter() - start) * 1000)})
    return meta


def run_cosyvoice_zero_shot(**kwargs):
    root = _external_root("cosyvoice-zero-shot")
    output_path = kwargs.pop("output_path")
    text = kwargs.pop("text").strip()
    ref_audio = kwargs.pop("reference_audio", None)
    ref_text = (kwargs.pop("ref_text", None) or "").strip()
    speed = float(kwargs.pop("speed", 1.0) or 1.0)
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

import torchaudio

sys.path.insert(0, ".")
sys.path.append("third_party/Matcha-TTS")
from cosyvoice.cli.cosyvoice import AutoModel

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
model = AutoModel(model_dir=payload["model_dir"])
for item in model.inference_zero_shot(
    payload["text"],
    payload["ref_text"],
    payload["reference_audio"],
    stream=False,
    speed=payload["speed"],
):
    torchaudio.save(payload["output_path"], item["tts_speech"].cpu(), model.sample_rate)
    break
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
    "f5-tts": run_f5_tts,
    "cosyvoice-sft": run_cosyvoice_sft,
    "cosyvoice-zero-shot": run_cosyvoice_zero_shot,
    "mimo-v2.5-tts": run_mimo_tts,
    "mimo-v2.5-tts-preset": run_mimo_tts,
    "mimo-v2.5-tts-voicedesign": run_mimo_tts,
    "mimo-v2.5-tts-voiceclone": run_mimo_tts,
}


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        result = RUNNERS[payload["engine_id"]](**payload["kwargs"])
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "traceback": traceback.format_exc()[-3000:]}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
