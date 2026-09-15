"""Voice Studio release version resolution.

``pyproject.toml`` is the single version source for a source checkout. Built
distributions expose the same value through standard package metadata.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from typing import Callable


DISTRIBUTION_NAME = "voice-studio"
UNKNOWN_VERSION = "0+unknown"
_PROJECT_VERSION = re.compile(r'^version\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)


def _source_tree_version(project_file: Path) -> str | None:
    """Read the project version without adding a TOML dependency on Python 3.10."""

    try:
        contents = project_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    _, project_marker, after_project = contents.partition("[project]")
    if not project_marker:
        return None
    project_section = after_project.split("\n[", maxsplit=1)[0]
    match = _PROJECT_VERSION.search(project_section)
    if match is None:
        return None
    resolved = match.group(1).strip()
    return resolved or None


def resolve_version(
    metadata_resolver: Callable[[str], str] = distribution_version,
    *,
    project_file: Path | None = None,
) -> str:
    """Resolve the installed version, then fall back to the source manifest."""

    try:
        resolved = metadata_resolver(DISTRIBUTION_NAME).strip()
    except PackageNotFoundError:
        resolved = ""
    if resolved:
        return resolved

    manifest = project_file or Path(__file__).resolve().parents[1] / "pyproject.toml"
    return _source_tree_version(manifest) or UNKNOWN_VERSION


__version__ = resolve_version()
