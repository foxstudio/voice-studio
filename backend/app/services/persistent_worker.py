"""Shared persistent worker infrastructure for F5-TTS and CosyVoice."""

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


_STDOUT_TAIL_LIMIT_LINES = 12
_STDOUT_TAIL_LIMIT_CHARS = 2000


class PersistentWorker:
    """Manages a long-running subprocess worker for TTS inference."""

    def __init__(self, log_name: str, worker_script: str, error_prefix: str, pythonpath_from_root: Callable[[Path], str]):
        self._worker: subprocess.Popen[str] | None = None
        self._log_handle = None
        self._worker_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._log_name = log_name
        self._worker_script = worker_script
        self._error_prefix = error_prefix
        self._pythonpath_from_root = pythonpath_from_root

    def run(
        self,
        kwargs: dict[str, Any],
        *,
        root: Path,
        python: str,
        timeout: int,
        cancel_check: Callable[[], bool] | None = None,
        on_tick: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        with self._request_lock:
            worker = self._ensure_worker(root=root, python=python, timeout=timeout, started=started, cancel_check=cancel_check, on_tick=on_tick)
            assert worker.stdin is not None
            worker.stdin.write(json.dumps(kwargs, ensure_ascii=False) + "\n")
            worker.stdin.flush()
            response = self._read_response(worker, timeout=timeout, started=started, cancel_check=cancel_check, on_tick=on_tick)
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or f"{self._error_prefix} worker failed")
        result = response.get("result") or {}
        result["generation_time_ms"] = int((time.monotonic() - started) * 1000)
        return result

    def shutdown(self) -> None:
        self._reset_worker()

    def _ensure_worker(
        self,
        *,
        root: Path,
        python: str,
        timeout: int,
        started: float,
        cancel_check: Callable[[], bool] | None,
        on_tick: Callable[[float], None] | None,
    ) -> subprocess.Popen[str]:
        with self._worker_lock:
            if self._worker and self._worker.poll() is None:
                return self._worker
            if self._worker:
                self._reset_worker()
            log_dir = root / ".voice_studio"
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_handle = open(log_dir / self._log_name, "a", encoding="utf-8")
            env = {**os.environ, "PYTHONPATH": self._pythonpath_from_root(root)}
            popen_kwargs: dict[str, Any] = {}
            if hasattr(os, "setsid"):
                popen_kwargs["preexec_fn"] = os.setsid
            self._worker = subprocess.Popen(
                [python, "-u", "-c", self._worker_script, str(root)],
                cwd=str(root),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._log_handle,
                text=True,
                bufsize=1,
                **popen_kwargs,
            )
            stdout_tail: list[str] = []
            ready = self._read_response(
                self._worker,
                timeout=timeout,
                started=started,
                cancel_check=cancel_check,
                on_tick=on_tick,
                stdout_tail=stdout_tail,
            )
            if not ready.get("ready"):
                error = ready.get("error") or f"{self._error_prefix} worker failed to start"
                stderr_tail = self._safe_stderr_tail(self._worker)
                self._reset_worker()
                raise RuntimeError(self._format_worker_error(error, stderr_tail=stderr_tail, stdout_tail_lines=stdout_tail))
            return self._worker

    def _read_response(
        self,
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
                self._reset_worker()
                raise RuntimeError("Generation cancelled")
            if elapsed > timeout:
                error = self._format_worker_error(
                    f"Inference timed out after {timeout}s",
                    stderr_tail=self._safe_stderr_tail(worker),
                    stdout_tail_lines=stdout_tail,
                )
                self._reset_worker()
                raise RuntimeError(error)
            if on_tick:
                on_tick(elapsed)
            if worker.poll() is not None:
                poll_state = worker.poll()
                stderr = self._safe_stderr_tail(worker)
                self._reset_worker()
                message = f"{self._error_prefix} worker exited unexpectedly (code={poll_state})"
                raise RuntimeError(self._format_worker_error(message, stderr_tail=stderr, stdout_tail_lines=stdout_tail))
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

    def _reset_worker(self) -> None:
        worker = self._worker
        self._worker = None
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
            if self._log_handle:
                self._log_handle.close()
                self._log_handle = None

    def _safe_stderr_tail(self, worker: subprocess.Popen[str]) -> str:
        try:
            if self._log_handle:
                self._log_handle.flush()
            log_path = Path(self._log_handle.name) if self._log_handle and hasattr(self._log_handle, "name") else Path("")
            return log_path.read_text(encoding="utf-8", errors="ignore")[-2000:]
        except Exception:
            return ""

    def _format_worker_error(
        self,
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


def _append_stdout_tail(stdout_tail: list[str], line: str) -> None:
    if not line.strip():
        return
    stdout_tail.append(line.rstrip("\n"))
    if len(stdout_tail) > _STDOUT_TAIL_LIMIT_LINES:
        del stdout_tail[0 : len(stdout_tail) - _STDOUT_TAIL_LIMIT_LINES]


def _format_tail(lines: list[str] | None) -> str:
    if not lines:
        return ""
    text = "\n".join(lines)
    return text[-_STDOUT_TAIL_LIMIT_CHARS:]
