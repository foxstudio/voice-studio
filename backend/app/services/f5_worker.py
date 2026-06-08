from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable


_worker: subprocess.Popen[str] | None = None
_worker_log_handle = None
_worker_lock = threading.Lock()
_request_lock = threading.Lock()


def run(
    kwargs: dict[str, Any],
    *,
    root: Path,
    python: str,
    timeout: int,
    cancel_check: Callable[[], bool] | None = None,
    on_tick: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    with _request_lock:
        worker = _ensure_worker(root=root, python=python, timeout=timeout, started=started, cancel_check=cancel_check, on_tick=on_tick)
        assert worker.stdin is not None
        worker.stdin.write(json.dumps(kwargs, ensure_ascii=False) + "\n")
        worker.stdin.flush()
        response = _read_response(worker, timeout=timeout, started=started, cancel_check=cancel_check, on_tick=on_tick)
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "F5 worker failed")
    result = response.get("result") or {}
    result["generation_time_ms"] = int((time.monotonic() - started) * 1000)
    return result


def shutdown() -> None:
    _reset_worker()


def _ensure_worker(
    *,
    root: Path,
    python: str,
    timeout: int,
    started: float,
    cancel_check: Callable[[], bool] | None,
    on_tick: Callable[[float], None] | None,
) -> subprocess.Popen[str]:
    global _worker, _worker_log_handle
    with _worker_lock:
        if _worker and _worker.poll() is None:
            return _worker
        if _worker:
            _reset_worker()
        log_dir = root / ".voice_studio"
        log_dir.mkdir(parents=True, exist_ok=True)
        _worker_log_handle = open(log_dir / "f5-worker.log", "a", encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(root / "src")}
        popen_kwargs: dict[str, Any] = {}
        if hasattr(os, "setsid"):
            popen_kwargs["preexec_fn"] = os.setsid
        _worker = subprocess.Popen(
            [python, "-u", "-c", _WORKER_SCRIPT, str(root)],
            cwd=str(root),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=_worker_log_handle,
            text=True,
            bufsize=1,
            **popen_kwargs,
        )
        ready = _read_response(_worker, timeout=timeout, started=started, cancel_check=cancel_check, on_tick=on_tick)
        if not ready.get("ready"):
            error = ready.get("error") or "F5 worker failed to start"
            _reset_worker()
            raise RuntimeError(error)
        return _worker


def _read_response(
    worker: subprocess.Popen[str],
    *,
    timeout: int,
    started: float,
    cancel_check: Callable[[], bool] | None,
    on_tick: Callable[[float], None] | None,
) -> dict[str, Any]:
    assert worker.stdout is not None
    while True:
        elapsed = time.monotonic() - started
        if cancel_check and cancel_check():
            _reset_worker()
            raise RuntimeError("Generation cancelled")
        if elapsed > timeout:
            _reset_worker()
            raise RuntimeError(f"Inference timed out after {timeout}s")
        if on_tick:
            on_tick(elapsed)
        if worker.poll() is not None:
            stderr = _safe_stderr_tail(worker)
            _reset_worker()
            raise RuntimeError(stderr or "F5 worker exited unexpectedly")
        readable, _, _ = select.select([worker.stdout], [], [], 0.5)
        if not readable:
            continue
        line = worker.stdout.readline()
        if not line:
            continue
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue


def _safe_stderr_tail(worker: subprocess.Popen[str]) -> str:
    try:
        if _worker_log_handle:
            _worker_log_handle.flush()
        return (_log_path_for_worker(worker).read_text(encoding="utf-8", errors="ignore")[-2000:])
    except Exception:
        return ""


def _reset_worker() -> None:
    global _worker, _worker_log_handle
    worker = _worker
    _worker = None
    try:
        if worker and worker.poll() is None:
            try:
                if hasattr(os, "getpgid"):
                    os.killpg(os.getpgid(worker.pid), signal.SIGTERM)
                else:
                    worker.terminate()
                worker.wait(timeout=5)
            except Exception:
                try:
                    if hasattr(os, "getpgid"):
                        os.killpg(os.getpgid(worker.pid), signal.SIGKILL)
                    else:
                        worker.kill()
                except Exception:
                    worker.kill()
                worker.wait(timeout=5)
    finally:
        if _worker_log_handle:
            _worker_log_handle.close()
            _worker_log_handle = None


def _log_path_for_worker(worker: subprocess.Popen[str]) -> Path:
    if _worker_log_handle and hasattr(_worker_log_handle, "name"):
        return Path(_worker_log_handle.name)
    return Path("")


_WORKER_SCRIPT = r"""
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
"""
