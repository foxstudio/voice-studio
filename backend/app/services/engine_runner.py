from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Any, Callable

from app.services import cosyvoice_worker, engine_health, f5_worker
from app.services.paths import PROJECT_ROOT


def run_isolated(
    engine_id: str,
    kwargs: dict[str, Any],
    timeout: int = 900,
    cancel_check: Callable[[], bool] | None = None,
    on_tick: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    if engine_id == "f5-tts" and os.environ.get("VOICE_STUDIO_F5_PERSISTENT_WORKER", "1") != "0":
        root = engine_health.external_engine_root("f5-tts")
        return f5_worker.run(
            kwargs,
            root=root,
            python=str(root / ".venv" / "bin" / "python"),
            timeout=timeout,
            cancel_check=cancel_check,
            on_tick=on_tick,
        )
    if engine_id in {"cosyvoice-sft", "cosyvoice-zero-shot"} and os.environ.get("VOICE_STUDIO_COSYVOICE_PERSISTENT_WORKER", "1") != "0":
        root = engine_health.external_engine_root(engine_id)
        return cosyvoice_worker.run(
            engine_id,
            kwargs,
            root=root,
            python=str(root / ".venv" / "bin" / "python"),
            timeout=timeout,
            cancel_check=cancel_check,
            on_tick=on_tick,
        )

    payload = __import__("json").dumps({"engine_id": engine_id, "kwargs": kwargs}, ensure_ascii=False)
    env = {"PYTHONPATH": f"{PROJECT_ROOT / 'backend'}:{PROJECT_ROOT}", **os.environ}
    popen_kwargs: dict[str, Any] = {}
    if hasattr(os, "setsid"):
        popen_kwargs["preexec_fn"] = os.setsid
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.services.inference_runner"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
        **popen_kwargs,
    )

    assert proc.stdin is not None
    proc.stdin.write(payload)
    proc.stdin.close()
    proc.stdin = None

    started_at = time.monotonic()
    while proc.poll() is None:
        elapsed = time.monotonic() - started_at
        if cancel_check and cancel_check():
            _terminate_process(proc)
            raise RuntimeError("Generation cancelled")
        if elapsed > timeout:
            _terminate_process(proc)
            raise RuntimeError(f"Inference timed out after {timeout}s")
        if on_tick:
            on_tick(elapsed)
        time.sleep(0.5)

    stdout, stderr = proc.communicate()
    stdout = (stdout or "").strip()
    stderr = (stderr or "").strip()
    if proc.returncode != 0:
        try:
            error = __import__("json").loads(stdout.splitlines()[-1] if stdout else "{}")
        except Exception:
            error = {}
        raise RuntimeError(error.get("error") or stderr[-1200:] or "Inference subprocess failed")
    if not stdout:
        raise RuntimeError("Inference subprocess returned no output")
    return __import__("json").loads(stdout.splitlines()[-1])


def stop_persistent_worker(engine_id: str) -> None:
    if engine_id == "f5-tts":
        f5_worker.shutdown()
    if engine_id in {"cosyvoice-sft", "cosyvoice-zero-shot"}:
        cosyvoice_worker.shutdown()


def shutdown_workers() -> None:
    f5_worker.shutdown()
    cosyvoice_worker.shutdown()


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if hasattr(os, "getpgid"):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            if hasattr(os, "getpgid"):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            proc.kill()
        proc.wait(timeout=5)
