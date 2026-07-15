"""Persistence boundary for OpenAI-compatible LLM provider profiles."""

from __future__ import annotations

import hashlib
import hmac
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
VERIFIED_PREFIX = "llm_provider_verified:"


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

    current_api_key = rows.get(_secret_key(profile_id)) or None
    effective_api_key = current_api_key
    if data.clear_api_key:
        deletes.append(_secret_key(profile_id))
        effective_api_key = None
    elif data.api_key is not None and data.api_key.strip():
        effective_api_key = data.api_key.strip()
        upserts[_secret_key(profile_id)] = effective_api_key

    verified_signature = rows.get(_verified_key(profile_id))
    if not verified_signature or not hmac.compare_digest(
        verified_signature,
        _verification_signature(profile_value, effective_api_key),
    ):
        deletes.append(_verified_key(profile_id))
        if _default_profile_id(rows) == profile_id:
            deletes.append(DEFAULT_PROFILE_KEY)

    db.apply_settings_changes(upserts, deletes)
    return profiles()


def mark_profile_verified(profile_id: str) -> LlmProviderListResponse:
    rows = db.get_settings_rows()
    profile_value = _profile_from_rows(profile_id, rows)
    if profile_value is None:
        raise ValueError("未找到这个语言模型配置")
    db.apply_settings_changes(
        {_verified_key(profile_id): _verification_signature(profile_value, rows.get(_secret_key(profile_id)) or None)}
    )
    return profiles()


def clear_profile_verification(profile_id: str) -> LlmProviderListResponse:
    rows = db.get_settings_rows()
    deletes = [_verified_key(profile_id)]
    if _default_profile_id(rows) == profile_id:
        deletes.append(DEFAULT_PROFILE_KEY)
    db.apply_settings_changes({}, deletes)
    return profiles()


def set_default_profile(profile_id: str) -> LlmProviderListResponse:
    profile_value = profile(profile_id)
    if profile_value is None:
        raise ValueError("未找到这个语言模型配置")
    if not profile_value.enabled:
        raise ValueError("已停用的服务不能设为默认")
    if not profile_value.model_test_verified:
        raise ValueError("请先完成“测试模型”，通过后才能设为默认")
    db.apply_settings_changes({DEFAULT_PROFILE_KEY: json.dumps(profile_id)})
    return profiles()


def delete_profile(profile_id: str) -> LlmProviderListResponse:
    rows = db.get_settings_rows()
    deletes = [_profile_key(profile_id), _secret_key(profile_id), _verified_key(profile_id)]

    if _default_profile_id(rows) == profile_id:
        deletes.append(DEFAULT_PROFILE_KEY)

    db.apply_settings_changes({}, deletes)
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
            profile_value = LlmProviderProfile(**payload)
            provider_profiles.append(
                profile_value.model_copy(
                    update={"model_test_verified": _profile_is_verified(profile_value, rows)}
                )
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    provider_profiles.sort(key=lambda item: (item.name.casefold(), item.profile_id))
    default_profile_id = _default_profile_id(rows)
    if default_profile_id and not any(
        item.profile_id == default_profile_id and item.enabled and item.model_test_verified
        for item in provider_profiles
    ):
        default_profile_id = None
    return LlmProviderListResponse(
        profiles=provider_profiles,
        default_profile_id=default_profile_id,
    )


def _profile_key(profile_id: str) -> str:
    return f"{PROFILE_PREFIX}{profile_id}"


def _secret_key(profile_id: str) -> str:
    return f"{SECRET_PREFIX}{profile_id}"


def _verified_key(profile_id: str) -> str:
    return f"{VERIFIED_PREFIX}{profile_id}"


def _profile_from_rows(profile_id: str, rows: dict[str, str]) -> LlmProviderProfile | None:
    raw = rows.get(_profile_key(profile_id))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        payload["profile_id"] = profile_id
        payload["api_key_configured"] = bool(rows.get(_secret_key(profile_id)))
        return LlmProviderProfile(**payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _profile_is_verified(profile_value: LlmProviderProfile, rows: dict[str, str]) -> bool:
    saved_signature = rows.get(_verified_key(profile_value.profile_id))
    if not saved_signature:
        return False
    expected = _verification_signature(
        profile_value,
        rows.get(_secret_key(profile_value.profile_id)) or None,
    )
    return hmac.compare_digest(saved_signature, expected)


def _verification_signature(profile_value: LlmProviderProfile, api_key: str | None) -> str:
    payload = {
        "protocol": profile_value.protocol,
        "base_url": profile_value.base_url,
        "model_id": profile_value.model_id,
        "enabled": profile_value.enabled,
        "api_key": api_key or "",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _default_profile_id(rows: dict[str, str]) -> str | None:
    raw = rows.get(DEFAULT_PROFILE_KEY)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return str(value).strip() or None
