"""SQLite 持久化存储"""

import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.path.expanduser("~/VoiceStudio/config/voice_studio.db")

_schema = """
CREATE TABLE IF NOT EXISTS voices (
    voice_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    result_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _ensure_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def get_conn():
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_schema)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Voices ──

def db_list_voices() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT data FROM voices ORDER BY json_extract(data, '$.updated_at') DESC").fetchall()
    return [json.loads(r[0]) for r in rows]


def db_get_voice(voice_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT data FROM voices WHERE voice_id = ?", (voice_id,)).fetchone()
    return json.loads(row[0]) if row else None


def db_save_voice(data: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO voices (voice_id, data) VALUES (?, ?)",
            (data["voice_id"], json.dumps(data, ensure_ascii=False)),
        )


def db_delete_voice(voice_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM voices WHERE voice_id = ?", (voice_id,))


# ── History ──

def db_list_history() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT data FROM history ORDER BY json_extract(data, '$.created_at') DESC").fetchall()
    return [json.loads(r[0]) for r in rows]


def db_save_history(data: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO history (result_id, data) VALUES (?, ?)",
            (data["result_id"], json.dumps(data, ensure_ascii=False)),
        )


def db_delete_history(result_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM history WHERE result_id = ?", (result_id,))


# ── Tasks ──

def db_list_tasks() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT data FROM tasks ORDER BY json_extract(data, '$.created_at') DESC").fetchall()
    return [json.loads(r[0]) for r in rows]


def db_save_task(data: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tasks (task_id, data) VALUES (?, ?)",
            (data["task_id"], json.dumps(data, ensure_ascii=False)),
        )


# ── Settings ──

def db_get_settings() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return dict(rows)


def db_save_settings(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
