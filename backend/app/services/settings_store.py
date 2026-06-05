"""应用设置存储"""

from app.models.schemas import AppSettings

_current = AppSettings()


def get() -> AppSettings:
    return _current


def update(data: AppSettings) -> AppSettings:
    global _current
    _current = data
    return _current
