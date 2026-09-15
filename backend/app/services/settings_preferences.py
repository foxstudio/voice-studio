"""Validated application preferences and runtime path resolution."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.schemas.voice_studio import AppSettings, AppSettingsPatch
from app.services import database as db, model_store, settings_secrets
from app.services.paths import PROJECT_ROOT, expand_path


DERIVED_FIELDS = {
    "mimo_api_key_configured",
    "doubao_api_key_configured",
    "volcengine_access_key_id_configured",
    "volcengine_secret_access_key_configured",
    "model_dir_effective",
    "model_dir_source",
}


def get() -> AppSettings:
    rows = db.get_settings_rows()
    if not rows:
        settings = AppSettings()
        return update(settings)

    values = {}
    for key, value in rows.items():
        try:
            values[key] = json.loads(value)
        except json.JSONDecodeError:
            values[key] = value

    for secret_key in {
        settings_secrets.MIMO_API_KEY,
        settings_secrets.DOUBAO_API_KEY,
        settings_secrets.VOLCENGINE_ACCESS_KEY_ID,
        settings_secrets.VOLCENGINE_SECRET_ACCESS_KEY,
    }:
        values.pop(secret_key, None)

    settings = AppSettings(**values)
    settings.model_dir_effective = str(model_store.managed_model_root(settings))
    settings.model_dir_source = (
        "environment"
        if os.environ.get("VOICE_STUDIO_MODELS_DIR")
        else "settings"
    )
    for field, configured in settings_secrets.configured_state(rows).items():
        setattr(settings, field, configured)
    return settings


def update(settings: AppSettings) -> AppSettings:
    data = settings.model_dump()
    for key in DERIVED_FIELDS:
        data.pop(key, None)
    ensure_directories(settings)
    db.apply_settings_changes(
        {key: json.dumps(value, ensure_ascii=False) for key, value in data.items()}
    )
    return get()


def patch(settings: AppSettingsPatch) -> AppSettings:
    changed_fields = set(settings.model_fields_set) - DERIVED_FIELDS
    if not changed_fields:
        return get()

    merged = get().model_dump()
    merged.update(settings.model_dump(include=changed_fields))
    validated = AppSettings.model_validate(merged)
    normalized = validated.model_dump(include=changed_fields)

    ensure_directories(validated)
    db.apply_settings_changes(
        {key: json.dumps(value, ensure_ascii=False) for key, value in normalized.items()}
    )
    return get()


def ensure_directories(settings: AppSettings | None = None) -> None:
    resolved = settings or get()
    for value, base in [
        (resolved.voice_dir, None),
        (resolved.output_dir, None),
        (resolved.export_dir, None),
        (resolved.project_dir, None),
        (resolved.cache_dir, None),
        (resolved.log_dir, None),
    ]:
        expand_path(value, base).mkdir(parents=True, exist_ok=True)

    model_store.managed_model_root(resolved).mkdir(parents=True, exist_ok=True)

    data_root = expand_path(resolved.data_dir)
    cache_root = expand_path(resolved.cache_dir)
    for path in [
        data_root,
        data_root / "assets",
        data_root / "assets" / "seed-audio" / "images",
        data_root / "assets" / "reference-audio" / "custom",
        cache_root / "waveforms",
        cache_root / "qwen-align",
        cache_root / "provider-catalogs",
        cache_root / "video-preview",
    ]:
        path.mkdir(parents=True, exist_ok=True)


def model_candidates(engine_id: str, settings: AppSettings) -> list[Path]:
    return model_store.model_candidates(engine_id, settings)
