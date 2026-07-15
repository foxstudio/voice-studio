"""Persistence boundary for OpenAI-compatible LLM provider profiles."""

from __future__ import annotations

import json

from app.schemas.voice_studio import (
    LlmProviderListResponse,
    LlmProviderProfile,
    LlmProviderProfileUpsert,
)
from app.services import database as db


PROFILE_PREFIX = "llm_provider_profile:"
SECRET_PREFIX = "llm_provider_secret:"
DEFAULT_PROFILE_KEY = "llm_default_profile_id"


def profiles() -> LlmProviderListResponse:
    return _profiles_from_rows(db.get_settings_rows())


def profile(profile_id: str) -> LlmProviderProfile | None:
    return next((item for item in profiles().profiles if item.profile_id == profile_id), None)


def api_key(profile_id: str) -> str | None:
    return db.get_settings_rows().get(_secret_key(profile_id)) or None


def update_profile(profile_id: str, data: LlmProviderProfileUpsert) -> LlmProviderListResponse:
    profile_value = LlmProviderProfile(
        profile_id=profile_id,
        name=data.name,
        protocol=data.protocol,
        base_url=data.base_url,
        model_id=data.model_id,
        enabled=data.enabled,
    )
    rows = db.get_settings_rows()
    upserts = {
        _profile_key(profile_id): json.dumps(
            profile_value.model_dump(exclude={"profile_id", "api_key_configured"}),
            ensure_ascii=False,
        )
    }
    deletes: list[str] = []

    if data.clear_api_key:
        deletes.append(_secret_key(profile_id))
    elif data.api_key is not None and data.api_key.strip():
        upserts[_secret_key(profile_id)] = data.api_key.strip()

    if data.make_default or not _default_profile_id(rows):
        upserts[DEFAULT_PROFILE_KEY] = json.dumps(profile_id)

    db.apply_settings_changes(upserts, deletes)
    return profiles()


def delete_profile(profile_id: str) -> LlmProviderListResponse:
    rows = db.get_settings_rows()
    remaining_rows = {
        key: value
        for key, value in rows.items()
        if key not in {_profile_key(profile_id), _secret_key(profile_id)}
    }
    remaining = _profiles_from_rows(remaining_rows)
    upserts: dict[str, str] = {}
    deletes = [_profile_key(profile_id), _secret_key(profile_id)]

    if _default_profile_id(rows) == profile_id:
        if remaining.profiles:
            upserts[DEFAULT_PROFILE_KEY] = json.dumps(remaining.profiles[0].profile_id)
        else:
            deletes.append(DEFAULT_PROFILE_KEY)

    db.apply_settings_changes(upserts, deletes)
    return profiles()


def _profiles_from_rows(rows: dict[str, str]) -> LlmProviderListResponse:
    provider_profiles: list[LlmProviderProfile] = []
    for key, raw in rows.items():
        if not key.startswith(PROFILE_PREFIX):
            continue
        profile_id = key.removeprefix(PROFILE_PREFIX)
        try:
            payload = json.loads(raw)
            payload["profile_id"] = profile_id
            payload["api_key_configured"] = bool(rows.get(_secret_key(profile_id)))
            provider_profiles.append(LlmProviderProfile(**payload))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    provider_profiles.sort(key=lambda item: (item.name.casefold(), item.profile_id))
    default_profile_id = _default_profile_id(rows)
    if default_profile_id and not any(item.profile_id == default_profile_id for item in provider_profiles):
        default_profile_id = None
    return LlmProviderListResponse(
        profiles=provider_profiles,
        default_profile_id=default_profile_id,
    )


def _profile_key(profile_id: str) -> str:
    return f"{PROFILE_PREFIX}{profile_id}"


def _secret_key(profile_id: str) -> str:
    return f"{SECRET_PREFIX}{profile_id}"


def _default_profile_id(rows: dict[str, str]) -> str | None:
    raw = rows.get(DEFAULT_PROFILE_KEY)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return str(value).strip() or None
