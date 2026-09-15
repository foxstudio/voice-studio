from __future__ import annotations

import importlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class LockUnavailableError(RuntimeError):
    """Raised when a non-blocking cross-process lock is already held."""


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Hold a fail-closed, cross-process lock for the context duration."""

    with _exclusive_file_lock(path, platform_name=os.name):
        yield


@contextmanager
def try_exclusive_file_lock(path: Path) -> Iterator[None]:
    """Acquire immediately or fail without waiting for another process."""

    with _exclusive_file_lock(path, platform_name=os.name, blocking=False):
        yield


@contextmanager
def _exclusive_file_lock(
    path: Path,
    *,
    platform_name: str,
    posix_module: Any | None = None,
    windows_module: Any | None = None,
    blocking: bool = True,
) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        backend = _locking_backend(
            platform_name,
            posix_module=posix_module,
            windows_module=windows_module,
        )
        try:
            _acquire(handle, backend, platform_name, blocking=blocking)
        except OSError as exc:
            if not blocking:
                raise LockUnavailableError(
                    f"Cross-process lock is already held: {path}"
                ) from exc
            raise
        try:
            yield
        finally:
            _release(handle, backend, platform_name)


def _locking_backend(
    platform_name: str,
    *,
    posix_module: Any | None,
    windows_module: Any | None,
) -> Any:
    if platform_name == "nt":
        return windows_module or importlib.import_module("msvcrt")
    if platform_name == "posix":
        return posix_module or importlib.import_module("fcntl")
    raise RuntimeError(f"Unsupported file-lock platform: {platform_name}")


def _acquire(
    handle: Any,
    backend: Any,
    platform_name: str,
    *,
    blocking: bool,
) -> None:
    if platform_name == "nt":
        _ensure_windows_lock_byte(handle)
        handle.seek(0)
        operation = backend.LK_LOCK if blocking else backend.LK_NBLCK
        backend.locking(handle.fileno(), operation, 1)
        return
    operation = backend.LOCK_EX
    if not blocking:
        operation |= backend.LOCK_NB
    backend.flock(handle.fileno(), operation)


def _release(handle: Any, backend: Any, platform_name: str) -> None:
    if platform_name == "nt":
        handle.seek(0)
        backend.locking(handle.fileno(), backend.LK_UNLCK, 1)
        return
    backend.flock(handle.fileno(), backend.LOCK_UN)


def _ensure_windows_lock_byte(handle: Any) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
