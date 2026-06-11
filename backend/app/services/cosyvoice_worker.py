from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.services.persistent_worker import PersistentWorker


_worker = PersistentWorker(
    log_name="cosyvoice-worker.log",
    error_prefix="CosyVoice",
    pythonpath_from_root=lambda root: str(root),
    worker_script=r"""
import contextlib
import json
import sys
import traceback
from pathlib import Path

root = Path(sys.argv[1])
model_dir = root / "pretrained_models" / "CosyVoice-300M-SFT"

try:
    sys.path.insert(0, ".")
    sys.path.append("third_party/Matcha-TTS")
    import torchaudio
    with contextlib.redirect_stdout(sys.stderr):
        from cosyvoice.cli.cosyvoice import AutoModel
        model = AutoModel(model_dir=str(model_dir))
        speakers = model.list_available_spks()
    print(json.dumps({"ready": True, "speakers": speakers}, ensure_ascii=False), flush=True)
except Exception as exc:
    print(json.dumps({"ready": False, "error": str(exc), "traceback": traceback.format_exc()[-2000:]}, ensure_ascii=False), flush=True)
    raise SystemExit(1)

def audio_meta(path):
    try:
        import soundfile as sf
        info = sf.info(path)
        return {"duration_ms": int(info.frames / info.samplerate * 1000), "sample_rate": info.samplerate}
    except Exception:
        return {"duration_ms": None, "sample_rate": getattr(model, "sample_rate", 22050)}

def first_result(generator):
    for item in generator:
        return item
    raise RuntimeError("CosyVoice returned no audio")

for line in sys.stdin:
    try:
        payload = json.loads(line)
        engine_id = payload["engine_id"]
        output_path = payload["output_path"]
        text = payload["text"].strip()
        speed = float(payload.get("speed") or 1.0)
        if not text:
            raise RuntimeError("Text is empty")
        with contextlib.redirect_stdout(sys.stderr):
            if engine_id == "cosyvoice-sft":
                speaker_id = str(payload.get("speaker_id") or "中文女")
                speaker = speaker_id if speaker_id in speakers else speakers[0]
                item = first_result(model.inference_sft(text, speaker, stream=False, speed=speed))
            elif engine_id == "cosyvoice-zero-shot":
                reference_audio = payload.get("reference_audio")
                ref_text = (payload.get("ref_text") or "").strip()
                if not reference_audio:
                    raise RuntimeError("REFERENCE_AUDIO_REQUIRED")
                if not ref_text:
                    raise RuntimeError("REFERENCE_TEXT_REQUIRED")
                item = first_result(model.inference_zero_shot(text, ref_text, reference_audio, stream=False, speed=speed))
            else:
                raise RuntimeError(f"Unsupported CosyVoice engine: {engine_id}")
            torchaudio.save(output_path, item["tts_speech"].cpu(), model.sample_rate)
        result = {"output_path": output_path, **audio_meta(output_path)}
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "traceback": traceback.format_exc()[-2000:]}, ensure_ascii=False), flush=True)
""",
)


def run(
    engine_id: str,
    kwargs: dict[str, Any],
    *,
    root: Path,
    python: str,
    timeout: int,
    cancel_check: Callable[[], bool] | None = None,
    on_tick: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    payload = {"engine_id": engine_id, **kwargs}
    return _worker.run(payload, root=root, python=python, timeout=timeout, cancel_check=cancel_check, on_tick=on_tick)


def shutdown() -> None:
    _worker.shutdown()
