from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


_worker: subprocess.Popen[str] | None = None
_worker_log_handle = None
_worker_lock = threading.Lock()
_request_lock = threading.Lock()
_STDOUT_TAIL_LIMIT_LINES = 12
_STDOUT_TAIL_LIMIT_CHARS = 2000


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
    started = time.monotonic()
    payload = {"engine_id": engine_id, **kwargs}
    with _request_lock:
        worker = _ensure_worker(root=root, python=python, timeout=timeout, started=started, cancel_check=cancel_check, on_tick=on_tick)
        assert worker.stdin is not None
        worker.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        worker.stdin.flush()
        response = _read_response(worker, timeout=timeout, started=started, cancel_check=cancel_check, on_tick=on_tick)
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "CosyVoice worker failed")
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
        _worker_log_handle = open(log_dir / "cosyvoice-worker.log", "a", encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(root)}
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
        stdout_tail: list[str] = []
        ready = _read_response(
            _worker,
            timeout=timeout,
            started=started,
            cancel_check=cancel_check,
            on_tick=on_tick,
            stdout_tail=stdout_tail,
        )
        if not ready.get("ready"):
            error = ready.get("error") or "CosyVoice worker failed to start"
            stderr_tail = _safe_stderr_tail(_worker)
            _reset_worker()
            raise RuntimeError(_format_worker_error(error, stderr_tail=stderr_tail, stdout_tail_lines=stdout_tail))
        return _worker


def _read_response(
    worker: subprocess.Popen[str],
    *,
    timeout: int,
    started: float,
    cancel_check: Callable[[], bool] | None,
    on_tick: Callable[[float], None] | None,
    stdout_tail: list[str] | None = None,
) -> dict[str, Any]:
    if stdout_tail is None:
        stdout_tail = []
    assert worker.stdout is not None
    while True:
        elapsed = time.monotonic() - started
        if cancel_check and cancel_check():
            _reset_worker()
            raise RuntimeError("Generation cancelled")
        if elapsed > timeout:
            error = _format_worker_error(
                f"Inference timed out after {timeout}s",
                stderr_tail=_safe_stderr_tail(worker),
                stdout_tail_lines=stdout_tail,
            )
            _reset_worker()
            raise RuntimeError(error)
        if on_tick:
            on_tick(elapsed)
        if worker.poll() is not None:
            poll_state = worker.poll()
            stderr = _safe_stderr_tail(worker)
            _reset_worker()
            message = f"CosyVoice worker exited unexpectedly (code={poll_state})"
            raise RuntimeError(_format_worker_error(message, stderr_tail=stderr, stdout_tail_lines=stdout_tail))
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
            _append_stdout_tail(stdout_tail, line)
            continue


def _append_stdout_tail(stdout_tail: list[str], line: str) -> None:
    if not line.strip():
        return
    stdout_tail.append(line.rstrip("\n"))
    if len(stdout_tail) > _STDOUT_TAIL_LIMIT_LINES:
        del stdout_tail[0 : len(stdout_tail) - _STDOUT_TAIL_LIMIT_LINES]


def _format_worker_error(
    base_message: str,
    *,
    stderr_tail: str = "",
    stdout_tail_lines: list[str] | None = None,
) -> str:
    tail = _format_tail(stdout_tail_lines)
    if stderr_tail:
        if tail:
            return f"{base_message} | stdout tail: {tail} | stderr tail: {stderr_tail}"
        return f"{base_message} | stderr tail: {stderr_tail}"
    if tail:
        return f"{base_message} | stdout tail: {tail}"
    return base_message


def _format_tail(lines: list[str] | None) -> str:
    if not lines:
        return ""
    text = "\n".join(lines)
    return text[-_STDOUT_TAIL_LIMIT_CHARS:]


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
"""
