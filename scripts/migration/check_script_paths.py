#!/usr/bin/env python3
"""Dry-run script path migration checker.

This checker does not move files. It compares the script inventory proposal
against the current repository and reports absolute-path risks plus references
that must be reviewed before any script directory migration.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = PROJECT_ROOT / "docs" / "architecture" / "SCRIPTS_INVENTORY.md"

SCRIPT_EXTENSIONS = {".py", ".mjs", ".json"}
REFERENCE_EXTENSIONS = {".md", ".py", ".mjs", ".toml", ".yml", ".yaml", ".json", ".sh"}
IGNORED_DIRS = {
    ".git",
    ".mimocode",
    ".omo",
    ".playwright-cli",
    ".pytest_cache",
    ".ruff_cache",
    ".sisyphus",
    ".venv",
    ".venv-qwen-align",
    "frontend/node_modules",
    "frontend/.svelte-kit",
    "frontend/build",
}
SPECIAL_TOP_LEVEL_SCRIPTS = {
    "scripts/voice_studio_batch.py",
}
ABSOLUTE_PATH_RE = re.compile(r"/Users/[^\s'\"`),\]]+")
INVENTORY_ROW_RE = re.compile(r"^\|\s*`(?P<current>scripts/[^`]+)`\s*\|\s*`(?P<proposed>scripts/[^`]+)`\s*\|")


@dataclass(frozen=True)
class InventoryEntry:
    current: str
    proposed: str


@dataclass(frozen=True)
class AbsolutePathHit:
    path: str
    line: int
    value: str


@dataclass(frozen=True)
class ReferenceHit:
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class ScriptPathReport:
    inventory_entries: list[InventoryEntry]
    missing_inventory_sources: list[str]
    unmanaged_scripts: list[str]
    absolute_path_hits: list[AbsolutePathHit]
    reference_hits: dict[str, list[ReferenceHit]]


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _is_ignored(path: Path) -> bool:
    rel = _relative(path)
    parts = rel.split("/")
    if "__pycache__" in parts:
        return True
    return any(rel == item or rel.startswith(f"{item}/") for item in IGNORED_DIRS)


def iter_files(paths: Iterable[Path], extensions: set[str]) -> Iterable[Path]:
    for base in paths:
        if not base.exists():
            continue
        if base.is_file():
            if base.suffix in extensions and not _is_ignored(base):
                yield base
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in extensions and not _is_ignored(path):
                yield path


def parse_inventory(path: Path = INVENTORY_PATH) -> list[InventoryEntry]:
    entries: list[InventoryEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = INVENTORY_ROW_RE.match(line)
        if match:
            entries.append(InventoryEntry(current=match.group("current"), proposed=match.group("proposed")))
    return entries


def current_script_files() -> list[str]:
    return sorted(_relative(path) for path in iter_files([PROJECT_ROOT / "scripts"], SCRIPT_EXTENSIONS))


def scan_absolute_paths() -> list[AbsolutePathHit]:
    hits: list[AbsolutePathHit] = []
    for path in iter_files([PROJECT_ROOT / "scripts"], SCRIPT_EXTENSIONS):
        rel = _relative(path)
        if rel == "scripts/migration/check_script_paths.py":
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            for match in ABSOLUTE_PATH_RE.finditer(line):
                hits.append(AbsolutePathHit(path=rel, line=line_no, value=match.group(0)))
    return hits


def scan_references(entries: list[InventoryEntry]) -> dict[str, list[ReferenceHit]]:
    moved_sources = [entry.current for entry in entries if entry.current != entry.proposed]
    reference_files = list(iter_files([PROJECT_ROOT / "README.md", PROJECT_ROOT / "docs", PROJECT_ROOT / "scripts"], REFERENCE_EXTENSIONS))
    reference_hits: dict[str, list[ReferenceHit]] = {source: [] for source in moved_sources}

    for path in reference_files:
        rel = _relative(path)
        if rel == _relative(INVENTORY_PATH):
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            for source in moved_sources:
                if rel == source:
                    continue
                if source in line:
                    reference_hits[source].append(ReferenceHit(path=rel, line=line_no, text=line.strip()))

    return {source: hits for source, hits in reference_hits.items() if hits}


def build_report() -> ScriptPathReport:
    entries = parse_inventory()
    inventory_sources = {entry.current for entry in entries}
    scripts = set(current_script_files())
    missing_sources = sorted(source for source in inventory_sources if source not in scripts)
    unmanaged = sorted(
        path
        for path in scripts
        if path not in inventory_sources
        and path != "scripts/migration/check_script_paths.py"
        and path not in SPECIAL_TOP_LEVEL_SCRIPTS
    )
    return ScriptPathReport(
        inventory_entries=entries,
        missing_inventory_sources=missing_sources,
        unmanaged_scripts=unmanaged,
        absolute_path_hits=scan_absolute_paths(),
        reference_hits=scan_references(entries),
    )


def print_text(report: ScriptPathReport) -> None:
    print("script_path_dry_run=ok")
    print(f"inventory_entries={len(report.inventory_entries)}")
    print(f"missing_inventory_sources={len(report.missing_inventory_sources)}")
    print(f"unmanaged_scripts={len(report.unmanaged_scripts)}")
    print(f"absolute_path_hits={len(report.absolute_path_hits)}")
    print(f"referenced_moved_sources={len(report.reference_hits)}")

    if report.missing_inventory_sources:
        print("\nMissing inventory sources:")
        for item in report.missing_inventory_sources:
            print(f"- {item}")

    if report.unmanaged_scripts:
        print("\nUnmanaged scripts/artifacts:")
        for item in report.unmanaged_scripts:
            print(f"- {item}")

    if report.absolute_path_hits:
        print("\nAbsolute path hits:")
        for hit in report.absolute_path_hits:
            print(f"- {hit.path}:{hit.line} {hit.value}")

    if report.reference_hits:
        print("\nReferences to proposed moved scripts:")
        for source, hits in report.reference_hits.items():
            print(f"- {source}: {len(hits)} reference(s)")
            for hit in hits[:5]:
                print(f"  - {hit.path}:{hit.line} {hit.text}")
            if len(hits) > 5:
                print(f"  - ... {len(hits) - 5} more")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run script path migration checker.")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument(
        "--fail-on-risk",
        action="store_true",
        help="Exit non-zero when missing sources, unmanaged files, absolute paths, or references are found.",
    )
    args = parser.parse_args()

    report = build_report()
    if args.format == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print_text(report)

    has_risk = bool(
        report.missing_inventory_sources
        or report.unmanaged_scripts
        or report.absolute_path_hits
        or report.reference_hits
    )
    return 1 if args.fail_on_risk and has_risk else 0


if __name__ == "__main__":
    raise SystemExit(main())
