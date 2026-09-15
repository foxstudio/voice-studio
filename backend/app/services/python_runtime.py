from __future__ import annotations

import sys
from pathlib import Path


def virtualenv_python(
    virtualenv_root: str | Path,
    *,
    platform: str | None = None,
) -> Path:
    """Return the native Python executable path for a virtual environment."""

    root = Path(virtualenv_root)
    platform_name = sys.platform if platform is None else platform
    if platform_name == "win32":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def engine_virtualenv_python(
    engine_root: str | Path,
    *,
    virtualenv_name: str = ".venv",
    platform: str | None = None,
) -> Path:
    """Return the managed virtualenv Python path below an engine runtime."""

    return virtualenv_python(
        Path(engine_root) / virtualenv_name,
        platform=platform,
    )
