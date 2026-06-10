from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.services.paths import expand_path

def _default_db_path() -> Path:
    explicit = os.environ.get("VOICE_STUDIO_DB_PATH")
    if explicit:
        return expand_path(explicit)
    data_dir = expand_path(os.environ.get("VOICE_STUDIO_DATA_DIR", "~/VoiceStudio"))
    return data_dir / "config" / "voice_studio.db"


DB_PATH = _default_db_path()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voices (
    voice_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voice_files (
    file_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    result_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exports (
    export_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transcriptions (
    transcription_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asr_tasks (
    task_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    batch_task_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS longform_tasks (
    longform_task_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presets (
    preset_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_longform ON tasks(json_extract(data, '$.longform_task_id'));
CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(json_extract(data, '$.task_type'));
"""


def set_db_path(path: str | Path) -> None:
    global DB_PATH
    DB_PATH = expand_path(str(path))


def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def conn():
    ensure_db()
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.executescript(INDEX_DDL)
    try:
        yield db
        db.commit()
    finally:
        db.close()


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def _load(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return json.loads(row["data"]) if row else None


def upsert(table: str, key: str, data: dict[str, Any], time_field: str = "updated_at") -> None:
    timestamp = data.get(time_field) or data.get("created_at") or ""
    id_field = f"{table[:-1]}_id"
    if table == "history":
        id_field = "result_id"
        time_field = "created_at"
    elif table == "tasks":
        id_field = "task_id"
    elif table == "voice_files":
        id_field = "file_id"
        time_field = "created_at"
    elif table == "exports":
        id_field = "export_id"
        time_field = "created_at"
    elif table == "asr_tasks":
        id_field = "task_id"
        time_field = "created_at"
    elif table == "batches":
        id_field = "batch_task_id"
        time_field = "created_at"
    elif table == "longform_tasks":
        id_field = "longform_task_id"
        time_field = "created_at"
    with conn() as db:
        if table == "tasks":
            db.execute(
                "INSERT OR REPLACE INTO tasks (task_id, data, created_at, status) VALUES (?, ?, ?, ?)",
                (key, _dump(data), data.get("created_at", ""), data.get("status", "")),
            )
        elif table == "batches":
            db.execute(
                "INSERT OR REPLACE INTO batches (batch_task_id, data, created_at, status) VALUES (?, ?, ?, ?)",
                (key, _dump(data), data.get("created_at", ""), data.get("status", "")),
            )
        elif table == "asr_tasks":
            db.execute(
                "INSERT OR REPLACE INTO asr_tasks (task_id, data, created_at, status) VALUES (?, ?, ?, ?)",
                (key, _dump(data), data.get("created_at", ""), data.get("status", "")),
            )
        elif table == "longform_tasks":
            db.execute(
                "INSERT OR REPLACE INTO longform_tasks (longform_task_id, data, created_at, status) VALUES (?, ?, ?, ?)",
                (key, _dump(data), data.get("created_at", ""), data.get("status", "")),
            )
        else:
            db.execute(
                f"INSERT OR REPLACE INTO {table} ({id_field}, data, {time_field}) VALUES (?, ?, ?)",
                (key, _dump(data), timestamp),
            )


def get_one(table: str, key_field: str, key: str) -> dict[str, Any] | None:
    with conn() as db:
        row = db.execute(f"SELECT data FROM {table} WHERE {key_field} = ?", (key,)).fetchone()
    return _load(row)


def list_all(table: str, order_field: str = "created_at", desc: bool = True, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    direction = "DESC" if desc else "ASC"
    with conn() as db:
        if limit < 0:
            rows = db.execute(f"SELECT data FROM {table} ORDER BY {order_field} {direction}").fetchall()
        else:
            rows = db.execute(f"SELECT data FROM {table} ORDER BY {order_field} {direction} LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [json.loads(r["data"]) for r in rows]


def delete_one(table: str, key_field: str, key: str) -> None:
    with conn() as db:
        db.execute(f"DELETE FROM {table} WHERE {key_field} = ?", (key,))


def get_settings_rows() -> dict[str, str]:
    with conn() as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def save_setting(key: str, value: str) -> None:
    with conn() as db:
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
