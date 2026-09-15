from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import database  # noqa: E402


pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows file privacy is provided by WSL2/POSIX permissions",
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_new_database_and_sqlite_sidecars_are_private(tmp_path):
    database_path = tmp_path / "config" / "voice_studio.db"
    database.set_db_path(database_path)

    with database.conn() as connection:
        connection.execute("SELECT 1")
        existing = [
            path
            for path in (
                database_path,
                Path(f"{database_path}-wal"),
                Path(f"{database_path}-shm"),
            )
            if path.exists()
        ]
        assert existing
        assert all(_mode(path) == 0o600 for path in existing)

    assert _mode(database_path) == 0o600


def test_existing_database_permissions_are_tightened(tmp_path):
    database_path = tmp_path / "voice_studio.db"
    database_path.write_bytes(b"")
    database_path.chmod(0o644)
    database.set_db_path(database_path)

    with database.conn() as connection:
        connection.execute("SELECT 1")

    assert _mode(database_path) == 0o600
