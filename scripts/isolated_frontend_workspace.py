"""Prepare a disposable frontend checkout without touching developer build state."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


def prepare_frontend_workspace(source: Path, destination: Path) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    if destination == source or source in destination.parents:
        raise ValueError("Frontend validation must use an external workspace")
    if destination.exists():
        raise FileExistsError("Frontend validation workspace already exists")
    if not (source / "node_modules").is_dir():
        raise FileNotFoundError("Install the frontend dependencies before validation")
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            "node_modules", ".svelte-kit", "build", "package", ".vite", ".env*",
        ),
    )
    # A link back to the developer checkout leaks its path into Rolldown's
    # region comments and lets dependency caches escape the owned workspace.
    shutil.copytree(
        source / "node_modules",
        destination / "node_modules",
        symlinks=True,
        ignore=shutil.ignore_patterns(".vite", ".cache"),
    )
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    prepare_frontend_workspace(args.source, args.destination)
