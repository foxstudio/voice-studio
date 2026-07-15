"""Write-only credential persistence for settings providers.

The public settings response exposes only configured-state flags. Raw secret
values stay behind this module and are never merged into ``AppSettings``.
"""

from __future__ import annotations

import os

from app.services import database as db


MIMO_API_KEY = "mimo_api_key"
DOUBAO_API_KEY = "doubao_api_key"
VOLCENGINE_ACCESS_KEY_ID = "volcengine_access_key_id"
VOLCENGINE_SECRET_ACCESS_KEY = "volcengine_secret_access_key"


def configured_state(rows: dict[str, str]) -> dict[str, bool]:
    return {
        "mimo_api_key_configured": bool(rows.get(MIMO_API_KEY)),
        "doubao_api_key_configured": bool(rows.get(DOUBAO_API_KEY) or os.environ.get("VOLCENGINE_API_KEY")),
        "volcengine_access_key_id_configured": bool(
            rows.get(VOLCENGINE_ACCESS_KEY_ID) or os.environ.get("VOLCENGINE_ACCESS_KEY_ID")
        ),
        "volcengine_secret_access_key_configured": bool(
            rows.get(VOLCENGINE_SECRET_ACCESS_KEY) or os.environ.get("VOLCENGINE_SECRET_ACCESS_KEY")
        ),
    }


def update_mimo_api_key(api_key: str | None, clear: bool = False) -> None:
    _update_one(MIMO_API_KEY, api_key, clear)


def mimo_api_key() -> str | None:
    return db.get_settings_rows().get(MIMO_API_KEY) or None


def update_doubao_api_key(api_key: str | None, clear: bool = False) -> None:
    _update_one(DOUBAO_API_KEY, api_key, clear)


def doubao_api_key() -> str | None:
    return db.get_settings_rows().get(DOUBAO_API_KEY) or os.environ.get("VOLCENGINE_API_KEY") or None


def update_volcengine_directory_credentials(
    access_key_id: str | None,
    secret_access_key: str | None,
    *,
    clear_access_key_id: bool = False,
    clear_secret_access_key: bool = False,
) -> None:
    changes: dict[str, str] = {}
    if clear_access_key_id:
        changes[VOLCENGINE_ACCESS_KEY_ID] = ""
    elif access_key_id is not None and access_key_id.strip():
        changes[VOLCENGINE_ACCESS_KEY_ID] = access_key_id.strip()

    if clear_secret_access_key:
        changes[VOLCENGINE_SECRET_ACCESS_KEY] = ""
    elif secret_access_key is not None and secret_access_key.strip():
        changes[VOLCENGINE_SECRET_ACCESS_KEY] = secret_access_key.strip()

    if changes:
        db.apply_settings_changes(changes)


def volcengine_access_key_id() -> str | None:
    return (
        db.get_settings_rows().get(VOLCENGINE_ACCESS_KEY_ID)
        or os.environ.get("VOLCENGINE_ACCESS_KEY_ID")
        or None
    )


def volcengine_secret_access_key() -> str | None:
    return (
        db.get_settings_rows().get(VOLCENGINE_SECRET_ACCESS_KEY)
        or os.environ.get("VOLCENGINE_SECRET_ACCESS_KEY")
        or None
    )


def _update_one(key: str, value: str | None, clear: bool) -> None:
    if clear:
        db.apply_settings_changes({key: ""})
    elif value is not None and value.strip():
        db.apply_settings_changes({key: value.strip()})
