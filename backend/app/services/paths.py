from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def expand_path(value: str, base: Path | None = None) -> Path:
    path = Path(os.path.expandvars(str(value))).expanduser()
    if not path.is_absolute():
        path = (base or PROJECT_ROOT) / path
    return path.resolve()


def project_subprocess_env(
    parent_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child environment that can always import the backend app."""

    env = dict(os.environ if parent_env is None else parent_env)
    required_paths = [
        str(PROJECT_ROOT / "backend"),
        str(PROJECT_ROOT),
    ]
    inherited_paths = [
        item
        for item in str(env.get("PYTHONPATH") or "").split(os.pathsep)
        if item
    ]
    env["PYTHONPATH"] = os.pathsep.join(
        [
            *required_paths,
            *[
                item
                for item in inherited_paths
                if item not in required_paths
            ],
        ]
    )
    return env
