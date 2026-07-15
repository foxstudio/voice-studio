from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.schemas.voice_studio import (
    AppSettings,
    AppSettingsPatch,
    LlmProviderListResponse,
    LlmProviderProfile,
    LlmProviderProfileUpsert,
    WebSearchSettings,
    WebSearchSettingsUpdate,
)
from app.services import database as db, settings_llm, settings_preferences, settings_search, settings_secrets, settings_storage
from app.services.paths import PROJECT_ROOT, expand_path


def get() -> AppSettings:
    return settings_preferences.get()


def update(settings: AppSettings) -> AppSettings:
    return settings_preferences.update(settings)


def patch(settings: AppSettingsPatch) -> AppSettings:
    return settings_preferences.patch(settings)


def update_mimo_api_key(api_key: str | None, clear: bool = False) -> AppSettings:
    settings_secrets.update_mimo_api_key(api_key, clear)
    return get()


def mimo_api_key() -> str | None:
    return settings_secrets.mimo_api_key()


def update_doubao_api_key(api_key: str | None, clear: bool = False) -> AppSettings:
    settings_secrets.update_doubao_api_key(api_key, clear)
    return get()


def doubao_api_key() -> str | None:
    return settings_secrets.doubao_api_key()


def update_volcengine_directory_credentials(
    access_key_id: str | None,
    secret_access_key: str | None,
    *,
    clear_access_key_id: bool = False,
    clear_secret_access_key: bool = False,
) -> AppSettings:
    settings_secrets.update_volcengine_directory_credentials(
        access_key_id,
        secret_access_key,
        clear_access_key_id=clear_access_key_id,
        clear_secret_access_key=clear_secret_access_key,
    )
    return get()


def volcengine_access_key_id() -> str | None:
    return settings_secrets.volcengine_access_key_id()


def volcengine_secret_access_key() -> str | None:
    return settings_secrets.volcengine_secret_access_key()


def llm_profiles() -> LlmProviderListResponse:
    return settings_llm.profiles()


def llm_profile(profile_id: str) -> LlmProviderProfile | None:
    return settings_llm.profile(profile_id)


def llm_api_key(profile_id: str) -> str | None:
    return settings_llm.api_key(profile_id)


def update_llm_profile(profile_id: str, data: LlmProviderProfileUpsert) -> LlmProviderListResponse:
    return settings_llm.update_profile(profile_id, data)


def mark_llm_profile_verified(profile_id: str) -> LlmProviderListResponse:
    return settings_llm.mark_profile_verified(profile_id)


def clear_llm_profile_verification(profile_id: str) -> LlmProviderListResponse:
    return settings_llm.clear_profile_verification(profile_id)


def set_default_llm_profile(profile_id: str) -> LlmProviderListResponse:
    return settings_llm.set_default_profile(profile_id)


def delete_llm_profile(profile_id: str) -> LlmProviderListResponse:
    return settings_llm.delete_profile(profile_id)


def web_search_settings() -> WebSearchSettings:
    return settings_search.get()


def web_search_api_key() -> str | None:
    return settings_search.api_key()


def update_web_search_settings(data: WebSearchSettingsUpdate) -> WebSearchSettings:
    return settings_search.update(data)


def ensure_directories(settings: AppSettings | None = None) -> None:
    settings_preferences.ensure_directories(settings)


def model_path(engine_id: str) -> Path:
    for candidate in model_candidates(engine_id):
        if candidate.exists():
            return candidate
    return model_candidates(engine_id)[0]


def model_candidates(engine_id: str) -> list[Path]:
    return settings_preferences.model_candidates(engine_id, get())


def voice_dir() -> Path:
    return expand_path(get().voice_dir)


def output_dir() -> Path:
    return expand_path(get().output_dir)


def export_dir() -> Path:
    return expand_path(get().export_dir)


def cache_dir() -> Path:
    return expand_path(get().cache_dir)


def log_dir() -> Path:
    return expand_path(get().log_dir)


def storage_audit() -> dict[str, Any]:
    return settings_storage.audit(get(), db.DB_PATH)


def open_storage_location(key: str) -> dict[str, str]:
    locations = {item["key"]: Path(item["path"]) for item in storage_audit()["locations"]}
    target = locations.get(key)
    if target is None:
        raise ValueError(f"Unknown storage location: {key}")

    open_path = target.parent if key == "database" else target
    open_path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(open_path)])
    elif os.name == "nt":
        os.startfile(str(open_path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(open_path)])
    return {"status": "opened", "key": key, "path": str(open_path)}


def _cleanup_targets() -> dict[str, Path]:
    return {
        "diagnostics": output_dir() / "diagnostics",
        "qwen_align": cache_dir() / "qwen-align",
        "logs": log_dir(),
    }


def cleanup_storage(targets: list[str]) -> dict[str, Any]:
    return settings_storage.cleanup(targets, _cleanup_targets())
