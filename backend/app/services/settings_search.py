"""Persistence for the optional web-search provider."""

from __future__ import annotations

import json

from app.schemas.voice_studio import WebSearchSettings, WebSearchSettingsUpdate
from app.services import database as db


SETTINGS_KEY = "web_search_settings"
SECRET_KEY = "web_search_api_key"


def get() -> WebSearchSettings:
    rows = db.get_settings_rows()
    raw = rows.get(SETTINGS_KEY)
    payload: dict[str, object] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload = parsed
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    payload["api_key_configured"] = bool(rows.get(SECRET_KEY))
    return WebSearchSettings.model_validate(payload)


def api_key() -> str | None:
    return db.get_settings_rows().get(SECRET_KEY) or None


def update(data: WebSearchSettingsUpdate) -> WebSearchSettings:
    value = WebSearchSettings(
        enabled=data.enabled,
        provider=data.provider,
        base_url=data.base_url,
        max_queries=data.max_queries,
        max_results_per_query=data.max_results_per_query,
    )
    upserts = {
        SETTINGS_KEY: json.dumps(value.model_dump(exclude={"api_key_configured"}), ensure_ascii=False)
    }
    deletes: list[str] = []
    if data.clear_api_key:
        deletes.append(SECRET_KEY)
    elif data.api_key is not None and data.api_key.strip():
        upserts[SECRET_KEY] = data.api_key.strip()
    db.apply_settings_changes(upserts, deletes)
    return get()
