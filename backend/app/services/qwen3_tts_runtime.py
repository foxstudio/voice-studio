"""Qwen3-TTS runtime shared by persistent and one-shot execution."""

from __future__ import annotations

import argparse
import contextlib
import gc
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable


_load_model: Callable[..., Any] | None = None
_generate_audio: Callable[..., Any] | None = None
_active_model: Any | None = None
_active_model_path: Path | None = None


def initialize() -> None:
    """Import the external MLX runtime without polluting the JSON protocol."""

    global _generate_audio, _load_model
    if _load_model is not None and _generate_audio is not None:
        return
    with contextlib.redirect_stdout(sys.stderr):
        from mlx_audio.tts.generate import generate_audio
        from mlx_audio.tts.utils import load_model

    _load_model = load_model
    _generate_audio = generate_audio


def _model_for(model_dir: str | Path) -> Any:
    global _active_model, _active_model_path

    initialize()
    resolved = Path(model_dir).expanduser().resolve()
    if _active_model is not None and _active_model_path == resolved:
        return _active_model

    _active_model = None
    _active_model_path = None
    gc.collect()
    try:
        import mlx.core as mx

        mx.clear_cache()
    except (ImportError, AttributeError):
        pass

    assert _load_model is not None
    with contextlib.redirect_stdout(sys.stderr):
        model = _load_model(resolved)
    _active_model = model
    _active_model_path = resolved
    return model


def generate(payload: dict[str, Any]) -> dict[str, str]:
    """Generate one WAV while retaining only the currently selected model."""

    model = _model_for(payload["model_dir"])
    assert _generate_audio is not None
    with tempfile.TemporaryDirectory(prefix="voice-studio-qwen3-") as tmp:
        generation_kwargs: dict[str, Any] = {
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
        if payload.get("reference_audio"):
            generation_kwargs["ref_audio"] = payload["reference_audio"]
            generation_kwargs["ref_text"] = payload["ref_text"]
        elif payload.get("voice_design_prompt"):
            generation_kwargs["instruct"] = payload["voice_design_prompt"]
        else:
            generation_kwargs["voice"] = payload.get("speaker_id") or "Vivian"
            generation_kwargs["instruct"] = payload.get("instruction") or ""

        with contextlib.redirect_stdout(sys.stderr):
            _generate_audio(**generation_kwargs)
        candidates = sorted(Path(tmp).glob("*.wav"))
        if not candidates:
            raise RuntimeError("Qwen3-TTS returned no wav output")
        output = Path(payload["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidates[0], output)
    return {"output_path": str(output)}


def serve() -> None:
    """Serve newline-delimited JSON requests for ``PersistentWorker``."""

    try:
        initialize()
        print(json.dumps({"ready": True}), flush=True)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ready": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc()[-2000:],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return

    for line in sys.stdin:
        try:
            result = generate(json.loads(line))
            print(
                json.dumps({"ok": True, "result": result}, ensure_ascii=False),
                flush=True,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": str(exc),
                        "traceback": traceback.format_exc()[-2000:],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


def run_once(payload_path: str | Path) -> None:
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    generate(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", metavar="PAYLOAD")
    args = parser.parse_args()
    if args.once:
        run_once(args.once)
    else:
        serve()


if __name__ == "__main__":
    main()
