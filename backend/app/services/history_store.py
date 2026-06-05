"""历史记录存储 - SQLite 持久化"""

import os

from app.models.schemas import HistoryItem
from app.services import database as db

OUTPUT_DIR = os.path.expanduser("~/VoiceStudio/outputs")


def list_history() -> list[HistoryItem]:
    return [HistoryItem(**d) for d in db.db_list_history()]


def add(item: HistoryItem) -> None:
    db.db_save_history(item.model_dump())


def delete(result_id: str) -> None:
    db.db_delete_history(result_id)


def get_audio_path(result_id: str) -> str | None:
    item_data = db.db_list_history()
    for d in item_data:
        if d.get("result_id") == result_id and d.get("output_audio_id"):
            for ext in [".wav", ".mp3", ".flac"]:
                path = os.path.join(OUTPUT_DIR, f"{d['output_audio_id']}{ext}")
                if os.path.exists(path):
                    return path
    return None
