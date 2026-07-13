"""Conservative startup maintenance for rebuildable runtime caches."""

from __future__ import annotations

import logging
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.services import custom_reference_store, seed_asset_store, settings_store

logger = logging.getLogger(__name__)

CACHE_NAMES = ("waveforms", "qwen-align", "provider-catalogs")
DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True)
class _CacheFile:
    path: Path
    root: Path
    size: int
    last_used: float


def run_startup_maintenance() -> dict[str, Any]:
    """Run one best-effort maintenance pass using environment configuration."""

    env = os.environ
    if not _env_bool(env, ("VOICE_STUDIO_CACHE_MAINTENANCE_ENABLED", "VOICE_STUDIO_CACHE_MAINTENANCE"), True):
        return _empty_result(enabled=False)
    ttl_seconds = _env_int(
        env,
        ("VOICE_STUDIO_CACHE_MAINTENANCE_TTL_SECONDS", "VOICE_STUDIO_CACHE_TTL_SECONDS"),
        DEFAULT_TTL_SECONDS,
    )
    if not any(name in env for name in ("VOICE_STUDIO_CACHE_MAINTENANCE_TTL_SECONDS", "VOICE_STUDIO_CACHE_TTL_SECONDS")):
        ttl_days = _env_int(env, ("VOICE_STUDIO_CACHE_TTL_DAYS",), DEFAULT_TTL_SECONDS // 86400)
        ttl_seconds = ttl_days * 86400
    max_bytes = _env_int(
        env,
        ("VOICE_STUDIO_CACHE_MAINTENANCE_MAX_BYTES", "VOICE_STUDIO_CACHE_MAX_BYTES"),
        DEFAULT_MAX_BYTES,
    )
    try:
        result = maintain_rebuildable_caches(
            settings_store.cache_dir(),
            ttl_seconds=ttl_seconds,
            max_bytes=max_bytes,
        )
        asset_ttl_days = _env_int(env, ("VOICE_STUDIO_ORPHAN_ASSET_TTL_DAYS",), 7)
        asset_ttl_seconds = asset_ttl_days * 86400
        try:
            result["orphan_custom_references_removed"] = len(
                custom_reference_store.cleanup_orphaned_uploads(ttl_seconds=asset_ttl_seconds)
            )
        except Exception as exc:
            logger.exception("Custom reference cleanup failed")
            result["errors"].append(f"custom reference cleanup failed: {exc}")
        try:
            result["orphan_seed_images_removed"] = len(
                seed_asset_store.cleanup_orphaned_assets(ttl_seconds=asset_ttl_seconds)
            )
        except Exception as exc:
            logger.exception("Seed image cleanup failed")
            result["errors"].append(f"seed image cleanup failed: {exc}")
        return result
    except Exception:
        logger.exception("Voice Studio cache maintenance failed")
        result = _empty_result(enabled=True)
        result["errors"].append("maintenance failed before completion")
        return result


def maintain_rebuildable_caches(
    cache_root: Path,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    now: float | None = None,
) -> dict[str, Any]:
    """Apply TTL eviction, then a total size cap using least-recently-used order.

    Only regular files below ``CACHE_NAMES`` are considered. Directory symlinks,
    file symlinks, sockets, and all sibling cache/data directories are skipped.
    """

    if ttl_seconds < 0 or max_bytes < 0:
        raise ValueError("cache maintenance limits must be non-negative")

    result = _empty_result(enabled=True)
    files: list[_CacheFile] = []
    root = Path(cache_root).expanduser()
    if root.is_symlink():
        result["skipped_symlinks"] = 1
        return result
    for name in CACHE_NAMES:
        approved_root = root / name
        found, skipped, errors = _scan_regular_files(approved_root)
        files.extend(found)
        result["skipped_symlinks"] += skipped
        result["errors"].extend(errors)

    result["scanned_files"] = len(files)
    result["before_bytes"] = sum(item.size for item in files)
    current_bytes = result["before_bytes"]
    cutoff = (time.time() if now is None else now) - ttl_seconds
    removed: set[Path] = set()

    for item in sorted(files, key=lambda candidate: (candidate.last_used, str(candidate.path))):
        if item.last_used > cutoff:
            continue
        if _unlink_regular_file(item, result["errors"]):
            removed.add(item.path)
            current_bytes -= item.size
            result["ttl_removed_files"] += 1
            result["removed_bytes"] += item.size

    if current_bytes > max_bytes:
        remaining = sorted(
            (item for item in files if item.path not in removed),
            key=lambda candidate: (candidate.last_used, str(candidate.path)),
        )
        for item in remaining:
            if current_bytes <= max_bytes:
                break
            if _unlink_regular_file(item, result["errors"]):
                removed.add(item.path)
                current_bytes -= item.size
                result["lru_removed_files"] += 1
                result["removed_bytes"] += item.size

    result["removed_files"] = len(removed)
    result["after_bytes"] = max(0, current_bytes)
    return result


def _scan_regular_files(root: Path) -> tuple[list[_CacheFile], int, list[str]]:
    if not root.exists():
        return [], 0, []
    if root.is_symlink():
        return [], 1, []
    if not root.is_dir():
        return [], 0, [f"cache path is not a directory: {root}"]

    files: list[_CacheFile] = []
    skipped_symlinks = 0
    errors: list[str] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            skipped_symlinks += 1
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            metadata = entry.stat(follow_symlinks=False)
                            files.append(
                                _CacheFile(
                                    path=Path(entry.path),
                                    root=root,
                                    size=metadata.st_size,
                                    last_used=max(metadata.st_atime, metadata.st_mtime),
                                )
                            )
                    except OSError as exc:
                        errors.append(f"could not inspect {entry.path}: {exc}")
        except OSError as exc:
            errors.append(f"could not scan {current}: {exc}")
    return files, skipped_symlinks, errors


def _unlink_regular_file(item: _CacheFile, errors: list[str]) -> bool:
    try:
        if item.root.is_symlink() or item.path.is_symlink():
            return False
        root = item.root.resolve(strict=True)
        parent = item.path.parent.resolve(strict=True)
        parent.relative_to(root)
        metadata = item.path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            return False
        item.path.unlink()
        return True
    except (OSError, RuntimeError, ValueError) as exc:
        errors.append(f"could not remove {item.path}: {exc}")
        return False


def _env_int(env: Mapping[str, str], names: tuple[str, ...], default: int) -> int:
    for name in names:
        raw = env.get(name)
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError:
            logger.warning("Ignoring invalid %s=%r", name, raw)
            return default
        if value < 0:
            logger.warning("Ignoring negative %s=%r", name, raw)
            return default
        return value
    return default


def _env_bool(env: Mapping[str, str], names: tuple[str, ...], default: bool) -> bool:
    for name in names:
        raw = env.get(name)
        if raw is None:
            continue
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        logger.warning("Ignoring invalid %s=%r", name, raw)
        return default
    return default


def _empty_result(*, enabled: bool) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "cache_names": list(CACHE_NAMES),
        "scanned_files": 0,
        "before_bytes": 0,
        "after_bytes": 0,
        "removed_files": 0,
        "removed_bytes": 0,
        "ttl_removed_files": 0,
        "lru_removed_files": 0,
        "skipped_symlinks": 0,
        "orphan_custom_references_removed": 0,
        "orphan_seed_images_removed": 0,
        "errors": [],
    }
