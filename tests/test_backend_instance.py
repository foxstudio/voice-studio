from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import backend_instance  # noqa: E402


def _hold_backend_lock(
    database_path: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    with backend_instance.single_backend_instance(Path(database_path)):
        ready.set()
        release.wait(timeout=10)


def test_backend_instance_lock_rejects_competitor_and_releases_after_exit(tmp_path):
    database_path = tmp_path / "config" / "voice_studio.db"
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    process = multiprocessing.Process(
        target=_hold_backend_lock,
        args=(str(database_path), ready, release),
    )
    process.start()
    try:
        assert ready.wait(timeout=5)
        with pytest.raises(
            backend_instance.BackendInstanceAlreadyRunning,
            match="already using this data directory",
        ):
            with backend_instance.single_backend_instance(database_path):
                raise AssertionError("a second backend must not start")
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert process.exitcode == 0
    with backend_instance.single_backend_instance(database_path) as acquired_path:
        assert acquired_path == backend_instance.lock_path(database_path)
