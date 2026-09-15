"""Shared persistent worker infrastructure for external TTS runtimes."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


_STDOUT_TAIL_LIMIT_LINES = 12
_STDOUT_TAIL_LIMIT_CHARS = 2000
_STDERR_TAIL_LIMIT_BYTES = 4096
_DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_LOG_BACKUP_COUNT = 2


class PersistentWorker:
    """Manages a long-running subprocess worker for TTS inference."""

    def __init__(
        self,
        log_name: str,
        worker_script: str,
        error_prefix: str,
        pythonpath_from_root: Callable[[Path], str],
        *,
        idle_timeout_seconds: float | None = None,
        log_max_bytes: int = _DEFAULT_LOG_MAX_BYTES,
        log_backup_count: int = _DEFAULT_LOG_BACKUP_COUNT,
        worker_args_from_kwargs: Callable[[dict[str, Any]], tuple[str, ...]] | None = None,
    ):
        self._worker: subprocess.Popen[str] | None = None
        self._stdout_worker: subprocess.Popen[str] | None = None
        self._stdout_queue: queue.Queue[str | None] | None = None
        self._idle_timer: threading.Timer | None = None
        self._idle_generation = 0
        self._log_path: Path | None = None
        self._stderr_thread: threading.Thread | None = None
        self._worker_identity: tuple[str, str, tuple[str, ...]] | None = None
        self._worker_lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._log_name = log_name
        self._worker_script = worker_script
        self._error_prefix = error_prefix
        self._pythonpath_from_root = pythonpath_from_root
        self._idle_timeout_seconds = idle_timeout_seconds
        self._log_max_bytes = max(1, int(log_max_bytes))
        self._log_backup_count = max(0, int(log_backup_count))
        self._worker_args_from_kwargs = worker_args_from_kwargs

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
            self._cancel_idle_shutdown()
            worker_args = (
                self._worker_args_from_kwargs(kwargs)
                if self._worker_args_from_kwargs is not None
                else ()
            )
            worker = self._ensure_worker(
                root=root,
                python=python,
                timeout=timeout,
                started=started,
                cancel_check=cancel_check,
                on_tick=on_tick,
                worker_args=worker_args,
            )
            assert worker.stdin is not None
            worker.stdin.write(json.dumps(kwargs, ensure_ascii=False) + "\n")
            worker.stdin.flush()
            response = self._read_response(worker, timeout=timeout, started=started, cancel_check=cancel_check, on_tick=on_tick)
            self._schedule_idle_shutdown()
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or f"{self._error_prefix} worker failed")
        result = response.get("result") or {}
        result["generation_time_ms"] = int((time.monotonic() - started) * 1000)
        return result

    def shutdown(self) -> None:
        self._cancel_idle_shutdown()
        self._reset_worker()

    def _cancel_idle_shutdown(self) -> None:
        self._idle_generation += 1
        timer = self._idle_timer
        self._idle_timer = None
        if timer is not None:
            timer.cancel()

    def _schedule_idle_shutdown(self) -> None:
        timeout = self._idle_timeout_seconds
        if timeout is None or timeout <= 0:
            return
        self._cancel_idle_shutdown()
        generation = self._idle_generation
        timer = threading.Timer(timeout, self._shutdown_if_idle, args=(generation,))
        timer.name = f"{self._error_prefix}-idle-shutdown"
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _shutdown_if_idle(self, generation: int) -> None:
        with self._request_lock:
            if generation != self._idle_generation:
                return
            self._idle_timer = None
            with self._worker_lock:
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
        worker_args: tuple[str, ...] = (),
    ) -> subprocess.Popen[str]:
        with self._worker_lock:
            identity = (str(root.resolve()), str(python), worker_args)
            if (
                self._worker
                and self._worker.poll() is None
                and self._worker_identity == identity
            ):
                return self._worker
            if self._worker:
                self._reset_worker()
            log_dir = root / ".voice_studio"
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = log_dir / self._log_name
            env = {
                **os.environ,
                "PYTHONPATH": self._pythonpath_from_root(root),
                "PYTHONIOENCODING": "utf-8",
            }
            popen_kwargs: dict[str, Any] = {}
            if hasattr(os, "setsid"):
                popen_kwargs["preexec_fn"] = os.setsid
            self._worker = subprocess.Popen(
                [python, "-u", "-c", self._worker_script, str(root), *worker_args],
                cwd=str(root),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                **popen_kwargs,
            )
            self._worker_identity = identity
            assert self._worker.stderr is not None
            self._stderr_thread = threading.Thread(
                target=_drain_stderr,
                args=(
                    self._worker.stderr,
                    self._log_path,
                    self._log_max_bytes,
                    self._log_backup_count,
                ),
                name=f"{self._error_prefix}-stderr-reader",
                daemon=True,
            )
            self._stderr_thread.start()
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
        response_queue = self._response_queue(worker)
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
            try:
                line = response_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if line is None:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                _append_stdout_tail(stdout_tail, line)
                continue

    def _response_queue(self, worker: subprocess.Popen[str]) -> queue.Queue[str | None]:
        with self._worker_lock:
            if worker is self._stdout_worker and self._stdout_queue is not None:
                return self._stdout_queue
            assert worker.stdout is not None
            response_queue: queue.Queue[str | None] = queue.Queue()
            reader = threading.Thread(
                target=_read_stdout_lines,
                args=(worker.stdout, response_queue),
                name=f"{self._error_prefix}-stdout-reader",
                daemon=True,
            )
            self._stdout_worker = worker
            self._stdout_queue = response_queue
            reader.start()
            return response_queue

    def _reset_worker(self) -> None:
        worker = self._worker
        self._worker = None
        self._worker_identity = None
        self._stdout_worker = None
        self._stdout_queue = None
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
            stderr_thread = self._stderr_thread
            self._stderr_thread = None
            if stderr_thread is not None and stderr_thread is not threading.current_thread():
                stderr_thread.join(timeout=1)
            self._log_path = None

    def _safe_stderr_tail(self, worker: subprocess.Popen[str]) -> str:
        try:
            if worker.poll() is not None and self._stderr_thread is not None:
                self._stderr_thread.join(timeout=0.5)
            if self._log_path is None:
                return ""
            return _read_file_tail(self._log_path, _STDERR_TAIL_LIMIT_BYTES)
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


def _read_stdout_lines(stream: Any, output: queue.Queue[str | None]) -> None:
    try:
        while line := stream.readline():
            output.put(line)
    finally:
        output.put(None)


def _drain_stderr(
    stream: Any,
    log_path: Path,
    max_bytes: int,
    backup_count: int,
) -> None:
    try:
        while chunk := stream.readline(8192):
            _append_rotating_log(
                log_path,
                chunk,
                max_bytes=max_bytes,
                backup_count=backup_count,
            )
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _append_rotating_log(
    path: Path,
    text: str,
    *,
    max_bytes: int,
    backup_count: int,
) -> None:
    if not text or path.is_symlink():
        return
    data = text.encode("utf-8", errors="replace")
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    try:
        current_size = path.stat().st_size if path.exists() else 0
        if current_size + len(data) > max_bytes:
            _rotate_log(path, backup_count=backup_count)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
        finally:
            os.close(descriptor)
        path.chmod(0o600)
    except OSError:
        # Logging must never block or fail an otherwise valid inference.
        return


def _rotate_log(path: Path, *, backup_count: int) -> None:
    if not path.exists() or path.is_symlink():
        return
    if backup_count <= 0:
        descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC)
        os.close(descriptor)
        return
    oldest = path.with_name(f"{path.name}.{backup_count}")
    if oldest.exists() and not oldest.is_symlink():
        oldest.unlink()
    for index in range(backup_count - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        target = path.with_name(f"{path.name}.{index + 1}")
        if source.exists() and not source.is_symlink():
            source.replace(target)
    path.replace(path.with_name(f"{path.name}.1"))


def _read_file_tail(path: Path, limit_bytes: int) -> str:
    if limit_bytes <= 0 or not path.is_file() or path.is_symlink():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - limit_bytes), os.SEEK_SET)
        return handle.read(limit_bytes).decode("utf-8", errors="ignore")


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
