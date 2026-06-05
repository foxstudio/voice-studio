"""历史记录存储"""

import os

from app.models.schemas import HistoryItem

_HISTORY: dict[str, HistoryItem] = {}


def list_history() -> list[HistoryItem]:
    return sorted(_HISTORY.values(), key=lambda h: h.created_at, reverse=True)


def add(item: HistoryItem) -> None:
    _HISTORY[item.result_id] = item


def delete(result_id: str) -> None:
    _HISTORY.pop(result_id, None)


def get_audio_path(result_id: str) -> str | None:
    item = _HISTORY.get(result_id)
    if not item or not item.output_audio_id:
        return None
    output_dir = os.path.expanduser("~/VoiceStudio/outputs")
    for ext in [".wav", ".mp3", ".flac"]:
        path = os.path.join(output_dir, f"{item.output_audio_id}{ext}")
        if os.path.exists(path):
            return path
    return None
