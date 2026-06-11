from __future__ import annotations

import io
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import cosyvoice_worker, f5_worker


class _FakeWorker:
    def __init__(self, stdout: str, poll_values: list[int | None] | None = None):
        self.stdout = io.StringIO(stdout)
        self._poll_values = list(poll_values or [None])
        self._poll_index = 0

    def poll(self) -> int | None:
        if not self._poll_values:
            return None
        if self._poll_index < len(self._poll_values):
            value = self._poll_values[self._poll_index]
            self._poll_index += 1
            return value
        return self._poll_values[-1]


class _FakeLogHandle:
    def __init__(self, path: Path):
        self.name = str(path)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeClock:
    def __init__(self, values: list[float]):
        self.values = values
        self.i = 0

    def __call__(self) -> float:
        value = self.values[self.i]
        self.i = min(self.i + 1, len(self.values) - 1)
        return value


@pytest.mark.parametrize("worker_module", [f5_worker, cosyvoice_worker], ids=["f5", "cosyvoice"])
def test_worker_read_response_returns_json_after_noisy_non_json(worker_module, monkeypatch):
    worker = _FakeWorker('INFO worker bootstrap\n{"ready": true}\n')
    import select as _select_mod
    monkeypatch.setattr(_select_mod, "select", lambda rlist, wlist, xlist, timeout: (rlist, [], []))
    result = worker_module._worker._read_response(
        worker=worker,
        timeout=10,
        started=time.monotonic(),
        cancel_check=None,
        on_tick=None,
    )
    assert result == {"ready": True}


@pytest.mark.parametrize("worker_module", [f5_worker, cosyvoice_worker], ids=["f5", "cosyvoice"])
def test_worker_read_response_tail_included_on_exit(worker_module, tmp_path, monkeypatch):
    worker = _FakeWorker("WARN unexpected model message\n", poll_values=[None, 1])
    stderr_path = tmp_path / "worker-stderr.log"
    stderr_path.write_text("stderr from worker\n")

    monkeypatch.setattr(worker_module._worker, "_log_handle", _FakeLogHandle(stderr_path))
    import select as _select_mod
    monkeypatch.setattr(_select_mod, "select", lambda rlist, wlist, xlist, timeout: (rlist, [], []))

    with pytest.raises(RuntimeError, match="unexpected model message") as exc:
        worker_module._worker._read_response(
            worker=worker,
            timeout=10,
            started=time.monotonic(),
            cancel_check=None,
            on_tick=None,
        )

    assert "stdout tail" in str(exc.value)
    assert "stderr from worker" in str(exc.value)


@pytest.mark.parametrize("worker_module", [f5_worker, cosyvoice_worker], ids=["f5", "cosyvoice"])
def test_worker_read_response_timeout_includes_tail(worker_module, tmp_path, monkeypatch):
    worker = _FakeWorker("first noisy line\n")
    stderr_path = tmp_path / "worker-timeout-stderr.log"
    stderr_path.write_text("timeout stderr trace\n")
    clock = _FakeClock([0.0, 0.2, 1.5])

    monkeypatch.setattr(worker_module._worker, "_log_handle", _FakeLogHandle(stderr_path))
    monkeypatch.setattr(time, "monotonic", clock)
    import select as _select_mod
    monkeypatch.setattr(_select_mod, "select", lambda rlist, wlist, xlist, timeout: (rlist, [], []))

    with pytest.raises(RuntimeError, match="timed out after 1s") as exc:
        worker_module._worker._read_response(
            worker=worker,
            timeout=1,
            started=0.0,
            cancel_check=None,
            on_tick=None,
        )
    assert "first noisy line" in str(exc.value)
    assert "timeout stderr trace" in str(exc.value)


@pytest.mark.parametrize("worker_module", [f5_worker, cosyvoice_worker], ids=["f5", "cosyvoice"])
def test_worker_ready_false_includes_tail(worker_module, tmp_path, monkeypatch):
    class _FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO()
            self.pid = 999

    stderr_path = tmp_path / "worker-ready-stderr.log"
    stderr_path.write_text("ready failed details\n")

    monkeypatch.setattr(worker_module._worker, "_worker", None)
    monkeypatch.setattr(worker_module._worker, "_log_handle", _FakeLogHandle(stderr_path))
    monkeypatch.setattr(worker_module._worker, "_safe_stderr_tail", lambda _worker: "ready failed details")
    import subprocess as _subprocess_mod
    monkeypatch.setattr(_subprocess_mod, "Popen", lambda *args, **kwargs: _FakePopen())
    monkeypatch.setattr(worker_module._worker, "_read_response", lambda *args, **kwargs: {"ready": False, "error": ""})
    monkeypatch.setattr(worker_module._worker, "_reset_worker", lambda: None)

    with pytest.raises(RuntimeError, match="failed to start") as exc:
        worker_module._worker._ensure_worker(
            root=tmp_path,
            python=str(sys.executable),
            timeout=10,
            started=0.0,
            cancel_check=None,
            on_tick=None,
        )
    assert "ready failed details" in str(exc.value)
    assert "stderr tail" in str(exc.value)
