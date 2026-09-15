from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from app.services import qwen3_tts_paths
from app.services.interprocess_lock import exclusive_file_lock
from app.services.paths import project_subprocess_env
from app.services.persistent_worker import PersistentWorker


def _idle_timeout_seconds() -> float:
    raw = os.environ.get("VOICE_STUDIO_QWEN3_TTS_IDLE_SECONDS", "900")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 900.0


_worker = PersistentWorker(
    log_name="qwen3-tts-worker.log",
    error_prefix="Qwen3-TTS",
    pythonpath_from_root=lambda _root: project_subprocess_env()["PYTHONPATH"],
    idle_timeout_seconds=_idle_timeout_seconds(),
    worker_script=(
        "from app.services.qwen3_tts_runtime import serve; serve()"
    ),
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
    # Import lazily because inference_runner owns request normalization and also
    # participates in the generic engine registry import graph.
    from app.services import inference_runner

    started = time.perf_counter()
    payload = inference_runner._prepare_qwen3_tts_request(kwargs, runtime_root=root)
    with exclusive_file_lock(root / ".voice_studio" / "qwen3-tts.lock"):
        _worker.run(
            payload,
            root=root,
            python=python,
            timeout=timeout,
            cancel_check=cancel_check,
            on_tick=on_tick,
        )
    return inference_runner._finalize_qwen3_tts_output(payload, started=started)


def shutdown() -> None:
    _worker.shutdown()
