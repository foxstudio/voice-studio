from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import interprocess_lock  # noqa: E402


class _FakePosixLock:
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    def __init__(self):
        self.operations: list[int] = []

    def flock(self, _file_descriptor: int, operation: int) -> None:
        self.operations.append(operation)


class _FakeWindowsLock:
    LK_LOCK = 1
    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self):
        self.operations: list[tuple[int, int]] = []

    def locking(self, _file_descriptor: int, operation: int, size: int) -> None:
        self.operations.append((operation, size))


def test_platform_lock_backend_can_lock_and_release_a_real_file(tmp_path):
    lock_path = tmp_path / "native.lock"

    with interprocess_lock.exclusive_file_lock(lock_path):
        assert lock_path.exists()

    assert lock_path.exists()


def test_posix_lock_is_acquired_and_released_around_context(tmp_path):
    backend = _FakePosixLock()

    with interprocess_lock._exclusive_file_lock(
        tmp_path / "nested" / "resource.lock",
        platform_name="posix",
        posix_module=backend,
    ):
        assert backend.operations == [backend.LOCK_EX]

    assert backend.operations == [backend.LOCK_EX, backend.LOCK_UN]


def test_windows_lock_reserves_one_real_byte_and_releases_same_region(tmp_path):
    backend = _FakeWindowsLock()
    lock_path = tmp_path / "resource.lock"

    with interprocess_lock._exclusive_file_lock(
        lock_path,
        platform_name="nt",
        windows_module=backend,
    ):
        assert backend.operations == [(backend.LK_LOCK, 1)]
        assert lock_path.read_bytes() == b"\0"

    assert backend.operations == [
        (backend.LK_LOCK, 1),
        (backend.LK_UNLCK, 1),
    ]


def test_lock_acquisition_failure_is_not_silently_ignored(tmp_path):
    class FailingLock(_FakePosixLock):
        def flock(self, _file_descriptor: int, operation: int) -> None:
            if operation == self.LOCK_EX:
                raise OSError("lock unavailable")

    with pytest.raises(OSError, match="lock unavailable"):
        with interprocess_lock._exclusive_file_lock(
            tmp_path / "resource.lock",
            platform_name="posix",
            posix_module=FailingLock(),
        ):
            raise AssertionError("the protected operation must not run")


def test_unknown_platform_fails_closed(tmp_path):
    with pytest.raises(RuntimeError, match="Unsupported file-lock platform"):
        with interprocess_lock._exclusive_file_lock(
            tmp_path / "resource.lock",
            platform_name="unknown",
        ):
            raise AssertionError("the protected operation must not run")


@pytest.mark.parametrize("platform_name", ["posix", "nt"])
def test_nonblocking_lock_reports_competing_instance(tmp_path, platform_name):
    if platform_name == "posix":
        class BusyPosix(_FakePosixLock):
            def flock(self, _file_descriptor, operation):
                if operation == self.LOCK_EX | self.LOCK_NB:
                    raise BlockingIOError("busy")

        kwargs = {"posix_module": BusyPosix()}
    else:
        class BusyWindows(_FakeWindowsLock):
            def locking(self, _file_descriptor, operation, size):
                if operation == self.LK_NBLCK:
                    raise OSError("busy")

        kwargs = {"windows_module": BusyWindows()}

    with pytest.raises(interprocess_lock.LockUnavailableError, match="already held"):
        with interprocess_lock._exclusive_file_lock(
            tmp_path / "busy.lock",
            platform_name=platform_name,
            blocking=False,
            **kwargs,
        ):
            raise AssertionError("a busy lock must not enter the protected block")
