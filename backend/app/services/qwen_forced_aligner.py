from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.services import settings_store
from app.services.paths import PROJECT_ROOT, expand_path

DEFAULT_CHECKPOINT = "Qwen/Qwen3-ForcedAligner-0.6B"
READY_TIMEOUT_SECONDS = 240
_worker: subprocess.Popen[str] | None = None
_worker_lock = threading.Lock()
_request_lock = threading.Lock()
_worker_log_handle = None


def runtime_python() -> Path | None:
    candidates = [
        os.environ.get("VOICE_STUDIO_QWEN_ALIGN_PYTHON"),
        ".venv-qwen-align/bin/python",
    ]
    for raw in candidates:
        if not raw:
            continue
        path = _non_resolving_path(raw)
        if path.exists():
            return path
    return None


def checkpoint_path() -> str:
    explicit = os.environ.get("VOICE_STUDIO_QWEN_ALIGN_CHECKPOINT")
    if explicit:
        path = expand_path(explicit, PROJECT_ROOT)
        return str(path) if path.exists() else explicit

    base = expand_path(settings_store.get().model_dir, PROJECT_ROOT)
    candidates = [
        base / "Qwen3-ForcedAligner-0.6B",
        base / "qwen3-forced-aligner-0.6b",
        base / "Qwen" / "Qwen3-ForcedAligner-0.6B",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return DEFAULT_CHECKPOINT


def preferred_device() -> str:
    configured = settings_store.get().device
    if configured == "cpu":
        return "cpu"
    return "mps"


def health_check() -> dict[str, Any]:
    python_path = runtime_python()
    if not python_path:
        return {
            "healthy": False,
            "status": "runtime_missing",
            "detail": "未找到 qwen-asr 专用 Python 环境，请先创建 .venv-qwen-align",
        }

    try:
        result = subprocess.run(
            [
                str(python_path),
                "-c",
                (
                    "import json, torch, qwen_asr; "
                    "print(json.dumps({'mps': bool(torch.backends.mps.is_available()), 'version': getattr(qwen_asr, '__version__', 'unknown')}))"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        return {
            "healthy": False,
            "status": "runtime_invalid",
            "python_path": str(python_path),
            "detail": str(exc),
        }

    payload = {}
    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        payload = {}

    checkpoint = checkpoint_path()
    is_local = Path(checkpoint).exists()
    return {
        "healthy": True,
        "status": "ready",
        "python_path": str(python_path),
        "checkpoint": checkpoint,
        "checkpoint_source": "local" if is_local else "remote_repo",
        "preferred_device": preferred_device(),
        "mps_available": bool(payload.get("mps")),
        "runtime_version": payload.get("version"),
    }


def align_audio(*, audio_path: str, transcript_text: str, language: str) -> list[dict[str, Any]]:
    worker = _ensure_worker()
    request_id = uuid.uuid4().hex
    payload = {
        "id": request_id,
        "audio_path": audio_path,
        "text": transcript_text,
        "language": language,
    }

    with _request_lock:
        assert worker.stdin is not None
        worker.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        worker.stdin.flush()

        assert worker.stdout is not None
        line = worker.stdout.readline()

    if not line:
        _reset_worker()
        raise RuntimeError("Qwen forced align worker 未返回结果")

    data = json.loads(line)
    if data.get("id") != request_id:
        raise RuntimeError("Qwen forced align worker 返回了意外的响应")
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "Qwen forced align 失败")
    return list(data.get("items") or [])


def shutdown() -> None:
    _reset_worker()


def _ensure_worker() -> subprocess.Popen[str]:
    global _worker, _worker_log_handle

    with _worker_lock:
        if _worker and _worker.poll() is None:
            return _worker

        python_path = runtime_python()
        if not python_path:
            raise RuntimeError("未找到 qwen-asr 专用 Python 环境")

        log_dir = settings_store.cache_dir() / "qwen-align"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "worker.log"
        _worker_log_handle = open(log_path, "a", encoding="utf-8")

        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        env.setdefault("TRANSFORMERS_VERBOSITY", "error")

        _worker = subprocess.Popen(
            [
                str(python_path),
                str(PROJECT_ROOT / "scripts" / "qwen_forced_align_worker.py"),
                "--checkpoint",
                checkpoint_path(),
                "--device",
                preferred_device(),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=_worker_log_handle,
            text=True,
            bufsize=1,
            env=env,
        )

        started = time.time()
        assert _worker.stdout is not None
        while time.time() - started < READY_TIMEOUT_SECONDS:
            line = _worker.stdout.readline()
            if not line:
                if _worker.poll() is not None:
                    raise RuntimeError("Qwen forced align worker 启动失败，请检查缓存日志")
                continue
            data = json.loads(line)
            if data.get("type") == "ready":
                return _worker
            if data.get("type") == "error":
                raise RuntimeError(data.get("error") or "Qwen forced align worker 启动失败")

        _reset_worker()
        raise RuntimeError("Qwen forced align worker 启动超时")


def _reset_worker() -> None:
    global _worker, _worker_log_handle
    worker = _worker
    _worker = None
    if worker:
        try:
            worker.terminate()
            worker.wait(timeout=5)
        except Exception:
            worker.kill()
    if _worker_log_handle:
        _worker_log_handle.close()
        _worker_log_handle = None


def _non_resolving_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path
