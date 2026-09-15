from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from app.services.paths import project_subprocess_env
from app.services.persistent_worker import PersistentWorker


SUPPORTED_ENGINES = frozenset(
    {
        "indextts-v2",
        "omnivoice",
        "confucius4-mlx-int8",
    }
)
DEFAULT_IDLE_TIMEOUT_SECONDS = 15 * 60


def _idle_timeout_seconds() -> float | None:
    raw = os.environ.get(
        "VOICE_STUDIO_LOCAL_WORKER_IDLE_SECONDS",
        str(DEFAULT_IDLE_TIMEOUT_SECONDS),
    )
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_IDLE_TIMEOUT_SECONDS)
    return value if value > 0 else None


def _worker_script(engine_id: str) -> str:
    return f"""
import contextlib
import json
import sys
import traceback

from app.services.inference_runner import RUNNERS

engine_id = {engine_id!r}
runner = RUNNERS[engine_id]
print(json.dumps({{"ready": True}}, ensure_ascii=False), flush=True)

for line in sys.stdin:
    try:
        kwargs = json.loads(line)
        kwargs["engine_id"] = engine_id
        with contextlib.redirect_stdout(sys.stderr):
            result = runner(**kwargs)
        print(json.dumps({{"ok": True, "result": result}}, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(
            json.dumps(
                {{
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc()[-3000:],
                }},
                ensure_ascii=False,
            ),
            flush=True,
        )
"""


def _pythonpath(_root: Path) -> str:
    return project_subprocess_env()["PYTHONPATH"]


_workers = {
    engine_id: PersistentWorker(
        log_name=f"{engine_id}-worker.log",
        worker_script=_worker_script(engine_id),
        error_prefix=engine_id,
        pythonpath_from_root=_pythonpath,
        idle_timeout_seconds=_idle_timeout_seconds(),
    )
    for engine_id in SUPPORTED_ENGINES
}


def run(
    engine_id: str,
    kwargs: dict[str, Any],
    *,
    state_root: Path,
    python: str,
    timeout: int,
    cancel_check: Callable[[], bool] | None = None,
    on_tick: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    try:
        worker = _workers[engine_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported local persistent engine: {engine_id}") from exc
    return worker.run(
        kwargs,
        root=state_root,
        python=python,
        timeout=timeout,
        cancel_check=cancel_check,
        on_tick=on_tick,
    )


def shutdown(engine_id: str) -> None:
    worker = _workers.get(engine_id)
    if worker is not None:
        worker.shutdown()


def shutdown_all() -> None:
    for worker in _workers.values():
        worker.shutdown()
