"""应用设置存储 - SQLite 持久化"""

import json

from app.models.schemas import AppSettings
from app.services import database as db

_DEFAULTS = AppSettings()


def get() -> AppSettings:
    raw = db.db_get_settings()
    if not raw:
        return _DEFAULTS
    return AppSettings(**raw)


def update(data: AppSettings) -> AppSettings:
    for key, value in data.model_dump().items():
        db.db_save_settings(key, json.dumps(value, ensure_ascii=False))
    return data
