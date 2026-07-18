from __future__ import annotations

import json
from pathlib import Path

from app.schemas.voice_studio import HistoryItem
from app.services import custom_reference_store, database as db, waveform_cache


def add(item: HistoryItem) -> HistoryItem:
    db.upsert("history", item.result_id, item.model_dump())
    return item


def list_history(
    limit: int = 100,
    offset: int = 0,
    *,
    project_id: str | None = None,
    segment_id: str | None = None,
    source: str | None = None,
) -> list[HistoryItem]:
    conditions: list[str] = []
    values: list[object] = []
    for path, value in (
        ("$.project_id", project_id),
        ("$.segment_id", segment_id),
        ("$.parameter_snapshot.source", source),
    ):
        if value is not None:
            conditions.append("json_extract(data, ?) = ?")
            values.extend((path, value))
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT data FROM history{where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
    values.extend((limit if limit < 0 else max(0, limit), max(0, offset)))
    with db.conn() as connection:
        rows = connection.execute(query, values).fetchall()
    return [HistoryItem(**json.loads(row["data"])) for row in rows]


def get(result_id: str) -> HistoryItem | None:
    data = db.get_one("history", "result_id", result_id)
    return HistoryItem(**data) if data else None


def delete(result_id: str) -> None:
    item = get(result_id)
    managed_paths = custom_reference_store.managed_paths_in(item.parameter_snapshot) if item else set()
    if item and item.output_path:
        path = Path(item.output_path)
        if path.exists():
            path.unlink(missing_ok=True)
    waveform_cache.delete_result_cache(result_id)
    db.delete_one("history", "result_id", result_id)
    for path in managed_paths:
        custom_reference_store.delete_if_unreferenced(path)


def audio_path(result_id: str) -> Path | None:
    item = get(result_id)
    if not item or not item.output_path:
        return None
    path = Path(item.output_path)
    if path.exists():
        return path
    for suffix in (".wav", ".mp3", ".flac"):
        alternate = path.with_suffix(suffix)
        if alternate.exists():
            return alternate
    return None
