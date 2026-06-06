#!/usr/bin/env python3
"""Standalone inference runner — called via subprocess from tts_engine.py.

Completely isolates MPS/PyTorch from the uvicorn process to avoid deadlocks.
Receives JSON via stdin, runs inference, outputs JSON result to stdout.
"""

from __future__ import annotations

import json as _json
import os
import sys
import time
import traceback
from pathlib import Path

_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

_backend_root = str(_project_root / "backend")
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)


def run_omnivoice(
    text: str,
    ref_audio_path: str | None = None,
    ref_text: str | None = None,
    language: str | None = None,
    emotion: str | None = None,
    speed: float = 1.0,
    output_path: str | None = None,
    **kwargs,
) -> dict:
    from app.services.adapters.omnivoice_adapter import OmniVoiceAdapter

    adapter = OmniVoiceAdapter()
    start = time.time()
    result = adapter.generate(
        text=text,
        ref_audio_path=ref_audio_path,
        ref_text=ref_text,
        language=language,
        emotion=emotion,
        speed=speed,
        output_path=output_path,
        **kwargs,
    )
    result["generation_time_ms"] = int((time.time() - start) * 1000)
    result["output_path"] = output_path
    return result


def run_indextts_v2(
    text: str,
    reference_audio: str,
    output_path: str | None = None,
    temperature: float = 0.8,
    top_p: float = 0.8,
    top_k: int = 30,
    repetition_penalty: float = 10.0,
    max_mel_tokens: int = 1500,
    max_text_tokens_per_segment: int = 120,
    interval_silence: int = 200,
    diffusion_steps: int = 25,
    cfg_rate: float = 0.7,
    emotion: str | dict | None = None,
    emo_alpha: float = 0.6,
    speed: float = 1.0,
    seed: int | None = None,
    **kwargs,
) -> dict:
    from mlx_indextts.generate_v2 import IndexTTSv2

    model_dir = os.path.join(str(_project_root), "models", "mlx-indexTTS-2.0")
    model = IndexTTSv2(model_dir, device="mps")
    start = time.time()
    model.generate(
        text=text,
        reference_audio=reference_audio,
        output_path=output_path,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        repetition_penalty=repetition_penalty,
        max_mel_tokens=max_mel_tokens,
        max_text_tokens_per_segment=max_text_tokens_per_segment,
        interval_silence=interval_silence,
        diffusion_steps=diffusion_steps,
        cfg_rate=cfg_rate,
        emotion=emotion,
        emo_alpha=emo_alpha,
        speed=speed,
        seed=seed,
    )
    return {
        "output_path": output_path,
        "generation_time_ms": int((time.time() - start) * 1000),
    }


def run_indextts_v1(
    text: str,
    ref_audio_path: str,
    output_path: str | None = None,
    temperature: float = 1.0,
    speed: float = 1.0,
    max_mel_tokens: int = 600,
    max_text_tokens_per_segment: int = 120,
    top_p: float = 0.8,
    top_k: int = 30,
    repetition_penalty: float = 10.0,
    interval_silence: int = 200,
    seed: int | None = None,
    **kwargs,
) -> dict:
    from app.services.adapters.v1_adapter import V1Adapter

    adapter = V1Adapter()
    adapter.load()
    start = time.time()
    result = adapter.generate(
        text=text,
        ref_audio_path=ref_audio_path,
        output_path=output_path,
        temperature=temperature,
        speed=speed,
        max_mel_tokens=max_mel_tokens,
        max_text_tokens_per_segment=max_text_tokens_per_segment,
        top_p=top_p,
        top_k=top_k,
        repetition_penalty=repetition_penalty,
        interval_silence=interval_silence,
        seed=seed,
    )
    result["generation_time_ms"] = int((time.time() - start) * 1000)
    return result


ENGINE_DISPATCH = {
    "omnivoice": run_omnivoice,
    "indextts": run_indextts_v2,
    "indextts-v1": run_indextts_v1,
}


def main() -> None:
    raw = sys.stdin.read()
    try:
        args = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        _json.dump({"error": f"Invalid JSON input: {exc}"}, sys.stdout)
        sys.exit(1)

    engine_id = args.get("engine_id", "")
    kwargs = args.get("kwargs", {})

    runner = ENGINE_DISPATCH.get(engine_id)
    if runner is None:
        _json.dump({"error": f"Unknown engine: {engine_id}"}, sys.stdout)
        sys.exit(1)

    try:
        result = runner(**kwargs)
    except Exception as exc:
        _json.dump(
            {
                "error": str(exc),
                "traceback": traceback.format_exc()[-2000:],
            },
            sys.stdout,
        )
        sys.exit(1)

    _json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
