from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Any, Callable

from app.services import (
    cosyvoice_worker,
    confucius4_paths,
    engine_health,
    engine_policy,
    execution_plan,
    f5_worker,
    local_engine_worker,
    qwen3_tts_paths,
    qwen3_tts_worker,
    settings_store,
)
from app.services.paths import PROJECT_ROOT, expand_path, project_subprocess_env
from app.services.python_runtime import engine_virtualenv_python


def run_isolated(
    engine_id: str,
    kwargs: dict[str, Any],
    timeout: int = 900,
    cancel_check: Callable[[], bool] | None = None,
    on_tick: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    kwargs = _with_resolved_runtime_paths(engine_id, kwargs)
    kwargs = _with_resolved_device(engine_id, kwargs)
    if (
        engine_id in local_engine_worker.SUPPORTED_ENGINES
        and os.environ.get("VOICE_STUDIO_LOCAL_PERSISTENT_WORKER", "1") != "0"
    ):
        return local_engine_worker.run(
            engine_id,
            kwargs,
            state_root=settings_store.log_dir(),
            python=sys.executable,
            timeout=timeout,
            cancel_check=cancel_check,
            on_tick=on_tick,
        )
    if engine_id == "f5-tts" and os.environ.get("VOICE_STUDIO_F5_PERSISTENT_WORKER", "1") != "0":
        root = engine_health.external_engine_root("f5-tts")
        return f5_worker.run(
            kwargs,
            root=root,
            python=str(engine_virtualenv_python(root)),
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
            python=str(engine_virtualenv_python(root)),
            timeout=timeout,
            cancel_check=cancel_check,
            on_tick=on_tick,
        )
    if (
        engine_id == qwen3_tts_paths.ENGINE_ID
        and os.environ.get("VOICE_STUDIO_QWEN3_TTS_PERSISTENT_WORKER", "1") != "0"
    ):
        root = engine_health.external_engine_root(engine_id)
        return qwen3_tts_worker.run(
            kwargs,
            root=root,
            python=str(engine_virtualenv_python(root)),
            timeout=timeout,
            cancel_check=cancel_check,
            on_tick=on_tick,
        )

    payload = __import__("json").dumps({"engine_id": engine_id, "kwargs": kwargs}, ensure_ascii=False)
    env = project_subprocess_env()
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
    while True:
        elapsed = time.monotonic() - started_at
        if cancel_check and cancel_check():
            _terminate_process(proc)
            raise RuntimeError("Generation cancelled")
        if elapsed > timeout:
            _terminate_process(proc)
            raise RuntimeError(f"Inference timed out after {timeout}s")
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            break
        remaining = max(0.01, timeout - elapsed)
        try:
            stdout, stderr = proc.communicate(timeout=min(0.5, remaining))
            break
        except subprocess.TimeoutExpired:
            pass
        if on_tick:
            on_tick(time.monotonic() - started_at)

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


def _with_resolved_runtime_paths(
    engine_id: str,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    if engine_policy.resolve_engine_id(engine_id) != confucius4_paths.ENGINE_ID:
        return kwargs
    if kwargs.get("runtime_root"):
        return kwargs
    configured_data_root = expand_path(settings_store.get().data_dir)
    return {
        **kwargs,
        "runtime_root": str(confucius4_paths.runtime_root(configured_data_root)),
    }


def _with_resolved_device(
    engine_id: str,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    resolved_engine_id = engine_policy.resolve_engine_id(engine_id)
    if (
        resolved_engine_id not in execution_plan.DEVICE_CONTROLLED_ENGINES
        or kwargs.get("device")
    ):
        return kwargs
    try:
        plan = execution_plan.resolve(
            resolved_engine_id,
            settings_store.get().device,
        )
    except execution_plan.ExecutionPlanError as exc:
        raise RuntimeError(exc.message) from exc
    if plan.device is None:
        return kwargs
    return {**kwargs, "device": plan.device}


def stop_persistent_worker(engine_id: str) -> None:
    local_engine_worker.shutdown(engine_id)
    if engine_id == "f5-tts":
        f5_worker.shutdown()
    if engine_id in {"cosyvoice-sft", "cosyvoice-zero-shot"}:
        cosyvoice_worker.shutdown()
    if engine_id == qwen3_tts_paths.ENGINE_ID:
        qwen3_tts_worker.shutdown()


def shutdown_workers() -> None:
    local_engine_worker.shutdown_all()
    f5_worker.shutdown()
    cosyvoice_worker.shutdown()
    qwen3_tts_worker.shutdown()


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
