from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_NAME = ".voice-studio-integrity.json"
MANIFEST_VERSION = 1
READ_CHUNK_BYTES = 8 * 1024 * 1024


def verify_model_file(
    model_dir: Path,
    filename: str,
    *,
    expected_size: int,
    expected_sha256: str,
    revision: str,
) -> tuple[bool, dict[str, Any]]:
    path = model_dir / filename
    if not path.is_file():
        return False, {"status": "missing", "path": str(path)}

    stat = path.stat()
    if stat.st_size != expected_size:
        return False, {
            "status": "size_mismatch",
            "path": str(path),
            "expected_size": expected_size,
            "actual_size": stat.st_size,
        }

    cached = _manifest_entry(model_dir, filename)
    if _entry_matches(cached, stat.st_size, stat.st_mtime_ns, expected_sha256, revision):
        return True, {"status": "sha256_verified", "sha256": expected_sha256, "revision": revision, "cached": True}

    actual_sha256 = _sha256(path)
    if actual_sha256 != expected_sha256:
        return False, {
            "status": "sha256_mismatch",
            "path": str(path),
            "expected_sha256": expected_sha256,
            "actual_sha256": actual_sha256,
        }

    entry = {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": actual_sha256,
        "revision": revision,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_manifest_entry(model_dir, filename, entry)
    return True, {"status": "sha256_verified", "sha256": actual_sha256, "revision": revision, "cached": False}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_entry(model_dir: Path, filename: str) -> dict[str, Any] | None:
    try:
        payload = json.loads((model_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != MANIFEST_VERSION:
        return None
    files = payload.get("files")
    if not isinstance(files, dict) or not isinstance(files.get(filename), dict):
        return None
    return files[filename]


def _entry_matches(entry: dict[str, Any] | None, size: int, mtime_ns: int, sha256: str, revision: str) -> bool:
    return bool(
        entry
        and entry.get("size") == size
        and entry.get("mtime_ns") == mtime_ns
        and entry.get("sha256") == sha256
        and entry.get("revision") == revision
    )


def _save_manifest_entry(model_dir: Path, filename: str, entry: dict[str, Any]) -> None:
    manifest_path = model_dir / MANIFEST_NAME
    payload: dict[str, Any] = {"schema_version": MANIFEST_VERSION, "files": {}}
    try:
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(current, dict) and current.get("schema_version") == MANIFEST_VERSION and isinstance(current.get("files"), dict):
            payload = current
    except (OSError, json.JSONDecodeError):
        pass
    payload["files"][filename] = entry

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=model_dir, prefix=f"{MANIFEST_NAME}.", delete=False) as target:
            temporary_path = Path(target.name)
            json.dump(payload, target, ensure_ascii=False, indent=2, sort_keys=True)
            target.write("\n")
        os.replace(temporary_path, manifest_path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
