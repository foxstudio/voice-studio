#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTING_PATH_FIELDS = [
    "data_dir",
    "voice_dir",
    "output_dir",
    "export_dir",
    "project_dir",
    "cache_dir",
    "log_dir",
    "model_dir",
]
HISTORY_PATH_FIELDS = ["output_path", "path", "audio_path"]
EXPORT_PATH_FIELDS = ["path"]
VOICE_FILE_PATH_FIELDS = ["path"]


def _default_data_dir() -> Path:
    env_value = os.environ.get("VOICE_STUDIO_DATA_DIR", "~/VoiceStudio")
    return Path(env_value).expanduser().resolve(strict=False)


def _default_db_path(data_dir: Path) -> Path:
    env_value = os.environ.get("VOICE_STUDIO_DB_PATH")
    if env_value:
        return Path(env_value).expanduser().resolve(strict=False)
    return (data_dir / "config" / "voice_studio.db").resolve(strict=False)


def _resolve_path(raw_value: str, base: Path | None = None) -> Path:
    candidate = Path(raw_value).expanduser()
    if not candidate.is_absolute():
        base_path = base or Path.cwd()
        candidate = base_path / candidate
    return candidate.resolve(strict=False)


def _path_stats(
    path_value: str | None,
    *,
    resolve_base: Path | None = None,
    inside_data_dir_base: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(path_value, str):
        return "", {
            "expanded_path": "",
            "exists": False,
            "is_file": False,
            "is_dir": False,
            "size_bytes": 0,
            "mtime": "",
            "inside_data_dir": False,
        }

    if not path_value.strip():
        return "", {
            "expanded_path": "",
            "exists": False,
            "is_file": False,
            "is_dir": False,
            "size_bytes": 0,
            "mtime": "",
            "inside_data_dir": False,
        }

    expanded = _resolve_path(path_value, base=resolve_base)
    try:
        exists = expanded.exists()
    except OSError:
        exists = False

    is_file = False
    is_dir = False
    size_bytes = 0
    mtime = ""
    if exists:
        try:
            is_file = expanded.is_file()
            is_dir = expanded.is_dir()
            stat = expanded.stat()
            size_bytes = stat.st_size if is_file else 0
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            exists = False

    inside_data_dir = False
    if inside_data_dir_base is not None and expanded.is_absolute():
        try:
            data_base = inside_data_dir_base.resolve(strict=False)
            inside_data_dir = expanded.is_relative_to(data_base)
        except (RuntimeError, ValueError):
            inside_data_dir = False
    return str(expanded), {
        "expanded_path": str(expanded),
        "exists": bool(exists),
        "is_file": bool(is_file),
        "is_dir": bool(is_dir),
        "size_bytes": int(size_bytes),
        "mtime": mtime,
        "inside_data_dir": inside_data_dir,
    }


def _append_path_record(
    paths: list[dict[str, Any]],
    table: str,
    source_id: str,
    field: str,
    raw_path: Any,
    resolve_base: Path | None,
    inside_data_dir_base: Path | None,
    warning: str = "",
) -> None:
    expanded_path, stats = _path_stats(
        raw_path if isinstance(raw_path, str) else None,
        resolve_base=resolve_base,
        inside_data_dir_base=inside_data_dir_base,
    )
    entry = {
        "source_table": table,
        "source_id": source_id,
        "field": field,
        "path": raw_path if isinstance(raw_path, str) else "",
        "expanded_path": expanded_path,
        "exists": stats["exists"],
        "is_file": stats["is_file"],
        "is_dir": stats["is_dir"],
        "size_bytes": stats["size_bytes"],
        "mtime": stats["mtime"],
        "inside_data_dir": stats["inside_data_dir"],
        "warning": warning,
    }
    paths.append(entry)


def _append_warning(warnings: list[str], message: str) -> None:
    if message and message not in warnings:
        warnings.append(message)


def _parse_json(value: str | bytes | bytearray, table: str, row_id: str, warnings: list[str]) -> dict[str, Any] | None:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        _append_warning(warnings, f"{table} row={row_id}: invalid JSON - {exc}")
        return None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def _load_settings(conn: sqlite3.Connection, data_dir: Path, paths: list[dict[str, Any]], warnings: list[str]) -> int:
    if not _table_exists(conn, "settings"):
        _append_warning(warnings, "settings table is missing.")
        return 0

    try:
        rows = conn.execute("SELECT rowid, key, value FROM settings").fetchall()
    except sqlite3.Error as exc:
        _append_warning(warnings, f"settings query failed: {exc}")
        return 0

    row_map = {row["key"]: (row["rowid"], row["value"]) for row in rows}
    added = 0
    for field in SETTING_PATH_FIELDS:
        row_pair = row_map.get(field)
        if row_pair is None:
            _append_warning(warnings, f"settings field missing: {field}.")
            continue

        row_id, value = row_pair
        parsed = value
        if isinstance(value, str):
            try:
                parsed_json = json.loads(value)
            except json.JSONDecodeError:
                parsed_json = value
        else:
            parsed_json = value

        if parsed_json is None:
            _append_warning(warnings, f"settings.{field} (row={row_id}) is null.")
            continue

        if not isinstance(parsed_json, str):
            _append_warning(
                warnings,
                f"settings.{field} (row={row_id}) is not a path string (type {type(parsed_json).__name__}).",
            )
            continue

        base = PROJECT_ROOT if field == "model_dir" else data_dir
        _append_path_record(paths, "settings", str(row_id), field, parsed_json, base, data_dir)
        added += 1
    return added


def _load_json_path_fields(
    conn: sqlite3.Connection,
    table: str,
    id_field: str,
    path_fields: list[str],
    paths: list[dict[str, Any]],
    data_dir: Path,
    warnings: list[str],
    resolve_base: Path | None = None,
) -> int:
    if not _table_exists(conn, table):
        _append_warning(warnings, f"{table} table is missing.")
        return 0

    try:
        rows = conn.execute(f"SELECT rowid, {id_field}, data FROM {table}").fetchall()
    except sqlite3.Error as exc:
        _append_warning(warnings, f"{table} query failed: {exc}")
        return 0

    added = 0
    for row in rows:
        row_id = str(row[id_field]) if row[id_field] else str(row["rowid"])
        data = _parse_json(row["data"], table, row_id, warnings)
        if not isinstance(data, dict):
            continue

        has_any = False
        for field in path_fields:
            if field not in data:
                continue
            has_any = True
            value = data.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                _append_warning(warnings, f"{table} row={row_id} field={field} is not a string path.")
                _append_path_record(
                    paths,
                    table,
                    row_id,
                    field,
                    "",
                    resolve_base or data_dir,
                    data_dir,
                    warning=f"{field} value not a string.",
                )
            else:
                _append_path_record(
                    paths,
                    table,
                    row_id,
                    field,
                    value,
                    resolve_base or data_dir,
                    data_dir,
                )
            added += 1
        if not has_any:
            _append_warning(warnings, f"{table} row={row_id} has no configured path field(s).")
    return added


def _audit(
    data_dir: Path,
    db_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    paths: list[dict[str, Any]] = []
    warnings: list[str] = []
    db_exists = db_path.exists()
    counts = {
        "settings_paths": 0,
        "voice_files_paths": 0,
        "history_paths": 0,
        "exports_paths": 0,
        "missing_paths": 0,
        "exists_paths": 0,
    }

    if not db_exists:
        _append_warning(warnings, f"Database is missing: {db_path}")
        return {
            "settings_paths": 0,
            "voice_files_paths": 0,
            "history_paths": 0,
            "exports_paths": 0,
            "missing_paths": 0,
            "exists_paths": 0,
            "total": 0,
        }, paths, warnings

    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        _append_warning(warnings, f"Failed to open database {db_path}: {exc}")
        return {
            "settings_paths": 0,
            "voice_files_paths": 0,
            "history_paths": 0,
            "exports_paths": 0,
            "missing_paths": 0,
            "exists_paths": 0,
            "total": 0,
        }, paths, warnings

    conn.row_factory = sqlite3.Row
    try:
        counts["settings_paths"] = _load_settings(conn, data_dir, paths, warnings)
        counts["voice_files_paths"] = _load_json_path_fields(
            conn,
            "voice_files",
            "file_id",
            VOICE_FILE_PATH_FIELDS,
            paths,
            data_dir,
            warnings,
        )
        counts["history_paths"] = _load_json_path_fields(
            conn,
            "history",
            "result_id",
            HISTORY_PATH_FIELDS,
            paths,
            data_dir,
            warnings,
            resolve_base=data_dir,
        )
        counts["exports_paths"] = _load_json_path_fields(
            conn,
            "exports",
            "export_id",
            EXPORT_PATH_FIELDS,
            paths,
            data_dir,
            warnings,
        )
    finally:
        conn.close()

    for item in paths:
        if not bool(item.get("exists", False)):
            counts["missing_paths"] += 1
        else:
            counts["exists_paths"] += 1

    counts["total"] = len(paths)
    counts["warnings"] = len(warnings)
    return counts, paths, warnings


def _build_manifest(
    data_dir: Path,
    db_path: Path,
    db_exists: bool,
    counts: dict[str, Any],
    paths: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(PROJECT_ROOT),
        "data_dir": str(data_dir),
        "db_path": str(db_path),
        "db_exists": db_exists,
        "counts": counts,
        "warnings": warnings,
        "paths": paths,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Voice Studio real data paths (read-only).")
    parser.add_argument(
        "--data-dir",
        type=str,
        default=str(_default_data_dir()),
        help="Voice Studio data directory. Default from VOICE_STUDIO_DATA_DIR or ~/VoiceStudio.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help=(
            "Path to Voice Studio sqlite database. Default from VOICE_STUDIO_DB_PATH "
            "or <data-dir>/config/voice_studio.db."
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional manifest output path.",
    )
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="Write audit manifest to output path. Without this flag, no manifest is written.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).expanduser().resolve(strict=False)
    db_path = (
        Path(args.db_path).expanduser().resolve(strict=False)
        if args.db_path
        else _default_db_path(data_dir)
    )

    db_exists = db_path.exists()
    counts, paths, warnings = _audit(data_dir, db_path)
    manifest = _build_manifest(data_dir, db_path, db_exists, counts, paths, warnings)

    missing_count = counts.get("missing_paths", 0)
    path_count = counts.get("total", len(paths))
    warning_count = counts.get("warnings", len(warnings))

    print(
        "db_exists={db_exists} path_count={path_count} "
        "missing_count={missing_count} warning_count={warning_count}".format(
            db_exists=db_exists,
            path_count=path_count,
            missing_count=missing_count,
            warning_count=warning_count,
        )
    )

    if warnings:
        for warning in warnings:
            print(f"warn: {warning}")
    else:
        print("warnings: 0")

    manifest_path = None
    if args.write_manifest:
        output = (
            Path(args.output).expanduser()
            if args.output
            else data_dir / "reports" / "audits" / f"voice-studio-data-audit-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        )
        manifest_path = output.expanduser().resolve(strict=False)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"manifest_path={manifest_path}")
    else:
        print("manifest_path=not_written")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
