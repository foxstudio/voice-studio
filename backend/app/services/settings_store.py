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
from app.services import database as db, model_store, settings_llm, settings_preferences, settings_search, settings_secrets, settings_storage
from app.services.paths import PROJECT_ROOT, expand_path


_VIDEO_LOCALIZATION_PROFILE_FIELDS = (
    "video_localization_understanding_profile_id",
    "video_localization_creation_profile_id",
    "video_localization_review_profile_id",
    "video_localization_alignment_profile_id",
)


def get() -> AppSettings:
    return settings_preferences.get()


def update(settings: AppSettings) -> AppSettings:
    _validate_video_localization_profile_references(
        settings,
        _VIDEO_LOCALIZATION_PROFILE_FIELDS,
    )
    return settings_preferences.update(settings)


def patch(settings: AppSettingsPatch) -> AppSettings:
    _validate_video_localization_profile_references(
        settings,
        set(settings.model_fields_set)
        & set(_VIDEO_LOCALIZATION_PROFILE_FIELDS),
    )
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
    current = get()
    cleared_fields = {
        field: None
        for field in _VIDEO_LOCALIZATION_PROFILE_FIELDS
        if getattr(current, field) == profile_id
    }
    result = settings_llm.delete_profile(profile_id)
    if cleared_fields:
        settings_preferences.patch(AppSettingsPatch(**cleared_fields))
    return result


def _validate_video_localization_profile_references(
    settings: AppSettings,
    fields: Any,
) -> None:
    for field in fields:
        profile_id = getattr(settings, field)
        if profile_id is None:
            continue
        profile = settings_llm.profile(profile_id)
        if profile is None:
            raise ValueError(
                f"本土化模型配置不存在：{profile_id}"
            )
        if not profile.enabled:
            raise ValueError(
                f"本土化模型配置已停用：{profile_id}"
            )


def web_search_settings() -> WebSearchSettings:
    return settings_search.get()


def web_search_api_key() -> str | None:
    return settings_search.api_key()


def update_web_search_settings(data: WebSearchSettingsUpdate) -> WebSearchSettings:
    return settings_search.update(data)


def ensure_directories(settings: AppSettings | None = None) -> None:
    settings_preferences.ensure_directories(settings)


def model_path(engine_id: str) -> Path:
    """Resolve a readable model path, including supported legacy locations."""
    return model_store.locate(engine_id, get()).path


def managed_model_path(engine_id: str) -> Path:
    """Return the canonical write target for managed model installation."""
    return model_store.managed_model_path(engine_id, get())


def managed_model_root() -> Path:
    """Return the configured root for all application-managed model weights."""
    return model_store.managed_model_root(get())


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
        "waveforms": cache_dir() / "waveforms",
        "qwen_align": cache_dir() / "qwen-align",
        "video_preview": cache_dir() / "video-preview",
        "logs": log_dir(),
    }


def cleanup_storage(targets: list[str]) -> dict[str, Any]:
    proxy_target = "video_media_proxy"
    if "video_preview" in targets or proxy_target in targets:
        from app.domains.video_localization import playback_proxy, preview_cache

        playback_proxy.cancel_all()
        preview_cache.cancel_all()
    regular_targets = [target for target in targets if target != proxy_target]
    result = settings_storage.cleanup(regular_targets, _cleanup_targets())
    if proxy_target in targets:
        from app.domains.video_localization import media_assets

        cleaned = {"target": proxy_target, **media_assets.clear_all_preview_proxies()}
        result["cleaned"].append(cleaned)
        result["removed_bytes"] += cleaned["removed_bytes"]
    return result
