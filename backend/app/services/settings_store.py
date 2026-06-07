from __future__ import annotations

import json
from pathlib import Path

from app.models.schemas import AppSettings
from app.services import database as db
from app.services.paths import PROJECT_ROOT, expand_path


def get() -> AppSettings:
    raw = db.get_settings_rows()
    if not raw:
        settings = AppSettings()
        update(settings)
        return settings
    values = {}
    for key, value in raw.items():
        try:
            values[key] = json.loads(value)
        except json.JSONDecodeError:
            values[key] = value
    values.pop("mimo_api_key", None)
    settings = AppSettings(**values)
    settings.mimo_api_key_configured = bool(raw.get("mimo_api_key"))
    return settings


def update(settings: AppSettings) -> AppSettings:
    data = settings.model_dump()
    data.pop("mimo_api_key_configured", None)
    for key, value in data.items():
        db.save_setting(key, json.dumps(value, ensure_ascii=False))
    ensure_directories(settings)
    return get()


def update_mimo_api_key(api_key: str | None, clear: bool = False) -> AppSettings:
    if clear:
        db.save_setting("mimo_api_key", "")
    elif api_key is not None and api_key.strip():
        db.save_setting("mimo_api_key", api_key.strip())
        db.save_setting("cloud_enabled", json.dumps(True))
    return get()


def mimo_api_key() -> str | None:
    value = db.get_settings_rows().get("mimo_api_key")
    return value or None


def ensure_directories(settings: AppSettings | None = None) -> None:
    s = settings or get()
    for value in [s.voice_dir, s.output_dir, s.export_dir, s.project_dir, s.cache_dir, s.log_dir]:
        expand_path(value).mkdir(parents=True, exist_ok=True)
    expand_path(s.data_dir).mkdir(parents=True, exist_ok=True)


def model_path(engine_id: str) -> Path:
    s = get()
    base = expand_path(s.model_dir, PROJECT_ROOT)
    if engine_id == "indextts-v2":
        return base / "mlx-indexTTS-2.0"
    return base / engine_id


def voice_dir() -> Path:
    return expand_path(get().voice_dir)


def output_dir() -> Path:
    return expand_path(get().output_dir)


def export_dir() -> Path:
    return expand_path(get().export_dir)
