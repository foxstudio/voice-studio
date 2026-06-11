from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.services.persistent_worker import PersistentWorker


_worker = PersistentWorker(
    log_name="f5-worker.log",
    error_prefix="F5",
    pythonpath_from_root=lambda root: str(root / "src"),
    worker_script=r"""
import contextlib
import json
import sys
import time
import traceback
from pathlib import Path

root = Path(sys.argv[1])
ckpt = root / "local_smoke" / "modelscope" / "F5-TTS_Emilia-ZH-EN" / "model_1250000.safetensors"
vocab = root / "local_smoke" / "modelscope" / "F5-TTS_Emilia-ZH-EN" / "vocab.txt"

try:
    with contextlib.redirect_stdout(sys.stderr):
        from f5_tts.api import F5TTS
        model = F5TTS(device="mps", ckpt_file=str(ckpt), vocab_file=str(vocab))
    print(json.dumps({"ready": True}), flush=True)
except Exception as exc:
    print(json.dumps({"ready": False, "error": str(exc), "traceback": traceback.format_exc()[-2000:]}), flush=True)
    raise SystemExit(1)

def audio_meta(path):
    try:
        import soundfile as sf
        info = sf.info(path)
        return {"duration_ms": int(info.frames / info.samplerate * 1000), "sample_rate": info.samplerate}
    except Exception:
        return {"duration_ms": None, "sample_rate": 24000}

for line in sys.stdin:
    try:
        payload = json.loads(line)
        output_path = payload["output_path"]
        with contextlib.redirect_stdout(sys.stderr):
            model.infer(
                ref_file=payload["reference_audio"],
                ref_text=payload["ref_text"],
                gen_text=payload["text"],
                file_wave=output_path,
                speed=payload["speed"],
                nfe_step=payload["nfe_step"],
                cfg_strength=payload["cfg_strength"],
                target_rms=payload["target_rms"],
                cross_fade_duration=payload["cross_fade_duration"],
                remove_silence=payload["remove_silence"],
                seed=payload["seed"],
            )
        result = {"output_path": output_path, **audio_meta(output_path)}
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "traceback": traceback.format_exc()[-2000:]}, ensure_ascii=False), flush=True)
""",
)


def run(
    kwargs: dict[str, Any],
    *,
    root: Path,
    python: str,
    timeout: int,
    cancel_check: Callable[[], bool] | None = None,
    on_tick: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    return _worker.run(kwargs, root=root, python=python, timeout=timeout, cancel_check=cancel_check, on_tick=on_tick)


def shutdown() -> None:
    _worker.shutdown()
