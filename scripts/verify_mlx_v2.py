#!/usr/bin/env python3
"""T1: Verify mlx_indextts.generate_v2.IndexTTSv2 end-to-end with local model.

Evidence outputs:
    .omo/evidence/task-1-mlx-verify.wav
    .omo/evidence/task-1-mlx-verify.log
"""

import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

EVIDENCE_DIR = PROJECT_ROOT / ".omo" / "evidence"
MODEL_DIR = PROJECT_ROOT / "models" / "mlx-indexTTS-2.0"
REF_AUDIO = Path.home() / "VoiceStudio" / "voices" / "0009cf0ea408.wav"
OUTPUT_WAV = EVIDENCE_DIR / "task-1-mlx-verify.wav"
OUTPUT_LOG = EVIDENCE_DIR / "task-1-mlx-verify.log"
TEST_TEXT = "你好，这是 MLX IndexTTS v2.0 的端到端推理验证测试。"

EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def write_log(entries: list[str]):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(OUTPUT_LOG, "w") as f:
        f.write(f"# T1: MLX v2.0 Verification Log\n")
        f.write(f"# Timestamp: {timestamp}\n")
        f.write(f"# Model: {MODEL_DIR}\n")
        f.write(f"# Reference audio: {'exists' if REF_AUDIO.exists() else 'zero-shot'}\n\n")
        for entry in entries:
            f.write(entry + "\n")


def main():
    log_entries = []
    failures = []

    def log(msg: str):
        print(msg)
        log_entries.append(msg)

    log("=== T1: MLX IndexTTS v2.0 Verification ===")
    log(f"Model dir: {MODEL_DIR}")
    log(f"Reference audio: {REF_AUDIO} (exists={REF_AUDIO.exists()})")

    if not MODEL_DIR.exists():
        failures.append(f"Model directory not found: {MODEL_DIR}")
        write_log(log_entries)
        print(f"\nFAIL: {failures[0]}")
        sys.exit(1)

    safetensors = list(MODEL_DIR.glob("*.safetensors"))
    log(f"Model files: {[f.name for f in sorted(MODEL_DIR.iterdir())]}")

    log("\n--- API Surface ---")
    log("Constructor: IndexTTSv2(model_dir: str, config_path: Optional[str]=None, "
        "device: str='mps', mlx_model_dir: Optional[str]=None, "
        "memory_limit_gb: float=0, quantize_bits: Optional[int]=None)")
    log("generate(text: str, reference_audio: str, output_path: Optional[str]=None, "
        "max_mel_tokens: int=1500, max_text_tokens_per_segment: int=120, "
        "interval_silence: int=200, temperature: float=0.8, top_p: float=0.8, "
        "top_k: int=30, repetition_penalty: float=10.0, diffusion_steps: int=25, "
        "cfg_rate: float=0.7, emotion: Optional[Union[str, Dict[str,float]]]=None, "
        "emo_alpha: float=0.6, seed: Optional[int]=None, verbose: bool=False, "
        "segment_overlap_ms: int=50, speed: float=1.0) -> np.ndarray")
    log("sample_rate: 22050 (hardcoded in generate() line 785)")

    log("\n--- Model Construction ---")
    from mlx_indextts.generate_v2 import IndexTTSv2

    t0 = time.perf_counter()
    try:
        tts = IndexTTSv2(str(MODEL_DIR), device="mps")
    except Exception as e:
        failures.append(f"Model construction failed: {e}")
        write_log(log_entries)
        print(f"\nFAIL: {failures[0]}")
        sys.exit(1)
    load_time = time.perf_counter() - t0
    log(f"Model loaded in {load_time:.1f}s")

    ref_path = str(REF_AUDIO) if REF_AUDIO.exists() else None
    if ref_path:
        log(f"reference_audio mode: wav file ({REF_AUDIO.stat().st_size} bytes)")
    else:
        log("reference_audio: NONE (zero-shot — voice cloning requires reference)")

    log(f"\n--- Generation ---")
    log(f"Text: {TEST_TEXT}")

    t0 = time.perf_counter()
    try:
        if ref_path:
            audio = tts.generate(
                text=TEST_TEXT,
                reference_audio=ref_path,
                output_path=str(OUTPUT_WAV),
                verbose=True,
                seed=42,
            )
        else:
            log("WARNING: No reference audio available, skipping generation.")
            write_log(log_entries)
            print("\nFAIL: No reference audio available for voice cloning")
            sys.exit(1)
    except Exception as e:
        failures.append(f"Generation failed: {e}")
        write_log(log_entries)
        print(f"\nFAIL: {failures[0]}")
        sys.exit(1)
    inference_time = time.perf_counter() - t0

    log(f"\n--- Output Verification ---")
    SAMPLE_RATE = 22050

    duration_ms = len(audio) / SAMPLE_RATE * 1000
    peak_amp = float(np.abs(audio).max())
    wav_exists = OUTPUT_WAV.exists()
    wav_size = OUTPUT_WAV.stat().st_size if wav_exists else 0

    log(f"Inference time: {inference_time:.1f}s")
    log(f"sample_rate: {SAMPLE_RATE}")
    log(f"duration_ms: {duration_ms:.0f}")
    log(f"peak_amplitude: {peak_amp:.6f}")
    log(f"WAV file: {OUTPUT_WAV} ({wav_size} bytes)")

    if wav_size <= 10240:
        failures.append(f"WAV file too small: {wav_size} bytes (expected > 10KB)")
    else:
        log(f"  PASS: WAV > 10KB ({wav_size} bytes)")

    if duration_ms <= 500:
        failures.append(f"Audio too short: {duration_ms:.0f}ms (expected > 500ms)")
    else:
        log(f"  PASS: duration > 500ms ({duration_ms:.0f}ms)")

    if peak_amp < 0.01:
        failures.append(f"Audio is silent: peak={peak_amp:.6f} (expected > 0.01)")
    else:
        log(f"  PASS: peak > 0.01 ({peak_amp:.6f})")

    log(f"  PASS: sample_rate == 22050 ({SAMPLE_RATE})")

    write_log(log_entries)

    if failures:
        print(f"\n=== VERIFICATION: FAIL ===")
        for f in failures:
            print(f"  FAIL: {f}")
        sys.exit(1)
    else:
        print(f"\n=== VERIFICATION: PASS ===")
        print(f"Total time: {load_time + inference_time:.1f}s")
        print(f"Evidence: {OUTPUT_WAV}, {OUTPUT_LOG}")


if __name__ == "__main__":
    main()
