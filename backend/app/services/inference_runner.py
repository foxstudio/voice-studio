from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
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
    start = time.perf_counter()
    from omnivoice import OmniVoice

    model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=kwargs.pop("device", "mps"))
    gen_kwargs = {"text": text}
    if language and language != "auto":
        gen_kwargs["language"] = language
    if ref_audio:
        gen_kwargs["ref_audio"] = ref_audio
        if ref_text:
            gen_kwargs["ref_text"] = ref_text
    elif instruction:
        gen_kwargs["instruct"] = instruction
    if speed != 1.0:
        gen_kwargs["speed"] = speed
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


RUNNERS = {
    "indextts-v2": run_indextts_v2,
    "omnivoice": run_omnivoice,
    "mimo-v2.5-tts": run_mimo_tts,
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
