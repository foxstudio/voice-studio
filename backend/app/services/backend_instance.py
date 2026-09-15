from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.services import database
from app.services.interprocess_lock import (
    LockUnavailableError,
    try_exclusive_file_lock,
)


class BackendInstanceAlreadyRunning(RuntimeError):
    """The configured Voice Studio database already has a live backend."""


def lock_path(database_path: Path | None = None) -> Path:
    active_database = Path(database_path or database.DB_PATH).expanduser().resolve()
    return active_database.with_name(f"{active_database.name}.backend.lock")


@contextmanager
def single_backend_instance(
    database_path: Path | None = None,
) -> Iterator[Path]:
    """Prevent two worker-owning backends from sharing one task database."""

    active_lock_path = lock_path(database_path)
    try:
        with try_exclusive_file_lock(active_lock_path):
            yield active_lock_path
    except LockUnavailableError as exc:
        raise BackendInstanceAlreadyRunning(
            "Another Voice Studio backend is already using this data directory. "
            "Stop the existing backend or configure a different "
            "VOICE_STUDIO_DATA_DIR before starting another instance."
        ) from exc
