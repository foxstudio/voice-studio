from __future__ import annotations

from pathlib import Path

from app.models.schemas import HistoryItem
from app.services import database as db


def add(item: HistoryItem) -> HistoryItem:
    db.upsert("history", item.result_id, item.model_dump())
    return item


def list_history(limit: int = 100, offset: int = 0) -> list[HistoryItem]:
    rows = db.list_all("history", "created_at")
    return [HistoryItem(**d) for d in rows[offset : offset + limit]]


def get(result_id: str) -> HistoryItem | None:
    data = db.get_one("history", "result_id", result_id)
    return HistoryItem(**data) if data else None


def delete(result_id: str) -> None:
    db.delete_one("history", "result_id", result_id)


def audio_path(result_id: str) -> Path | None:
    item = get(result_id)
    if not item or not item.output_path:
        return None
    path = Path(item.output_path)
    return path if path.exists() else None

