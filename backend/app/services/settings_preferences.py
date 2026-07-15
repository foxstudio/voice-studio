"""Validated application preferences and runtime path resolution."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.schemas.voice_studio import AppSettings, AppSettingsPatch
from app.services import confucius4_paths, database as db, settings_secrets
from app.services.paths import PROJECT_ROOT, expand_path


DERIVED_FIELDS = {
    "mimo_api_key_configured",
    "doubao_api_key_configured",
    "volcengine_access_key_id_configured",
    "volcengine_secret_access_key_configured",
}


def get() -> AppSettings:
    rows = db.get_settings_rows()
    if not rows:
        settings = AppSettings()
        update(settings)
        return settings

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
        (resolved.model_dir, PROJECT_ROOT),
        (resolved.voice_dir, None),
        (resolved.output_dir, None),
        (resolved.export_dir, None),
        (resolved.project_dir, None),
        (resolved.cache_dir, None),
        (resolved.log_dir, None),
    ]:
        expand_path(value, base).mkdir(parents=True, exist_ok=True)

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
    ]:
        path.mkdir(parents=True, exist_ok=True)


def model_candidates(engine_id: str, settings: AppSettings) -> list[Path]:
    base = expand_path(settings.model_dir, PROJECT_ROOT)
    if engine_id == "indextts-v2":
        data_models = expand_path(os.environ.get("VOICE_STUDIO_DATA_DIR", "~/VoiceStudio")) / "models"
        return _dedupe_paths(
            [
                base / "mlx-indexTTS-2.0",
                data_models / "mlx-indexTTS-2.0",
                PROJECT_ROOT / "models" / "mlx-indexTTS-2.0",
            ]
        )
    if engine_id == confucius4_paths.ENGINE_ID:
        return confucius4_paths.model_candidates(base)
    if engine_id == "qwen3-asr-mlx":
        candidates: list[Path] = []
        if configured := os.environ.get("VOICE_STUDIO_QWEN3_ASR_MODEL_DIR"):
            candidates.append(expand_path(configured))
        data_root = expand_path(os.environ.get("VOICE_STUDIO_DATA_DIR", "~/VoiceStudio"))
        repo_models = PROJECT_ROOT / "models"
        candidates.extend(
            [
                data_root / "models" / "qwen3-asr-mlx",
                base / "qwen3-asr-mlx",
                base / "mlx-community_Qwen3-ASR-1.7B-8bit",
                repo_models / "qwen3-asr-mlx",
                repo_models / "mlx-community_Qwen3-ASR-1.7B-8bit",
                *_huggingface_snapshots(("models--mlx-community--Qwen3-ASR-1.7B-8bit", "models--*--*Qwen3*ASR*")),
            ]
        )
        return _dedupe_paths(candidates)
    if engine_id == "faster-whisper-turbo":
        snapshots = _huggingface_snapshots(
            ("models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",),
            latest_only=True,
        )
        return [*snapshots, base / engine_id]
    return [base / engine_id]


def _huggingface_snapshots(repo_patterns: tuple[str, ...], *, latest_only: bool = False) -> list[Path]:
    hub_dir = _huggingface_hub_dir()
    snapshots: list[Path] = []
    seen: set[str] = set()
    for pattern in repo_patterns:
        try:
            repositories = hub_dir.glob(pattern)
            for repository in repositories:
                snapshot_root = repository / "snapshots"
                if not snapshot_root.is_dir():
                    continue
                for snapshot in snapshot_root.iterdir():
                    if snapshot.is_dir() and str(snapshot) not in seen:
                        seen.add(str(snapshot))
                        snapshots.append(snapshot)
        except OSError:
            continue

    def modified_at(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return -1

    snapshots.sort(key=lambda path: (modified_at(path), str(path)), reverse=True)
    return snapshots[:1] if latest_only else snapshots


def _huggingface_hub_dir() -> Path:
    if configured := os.environ.get("HF_HUB_CACHE"):
        return expand_path(configured)
    if configured := os.environ.get("HF_HOME"):
        return expand_path(configured) / "hub"
    return expand_path("~/.cache/huggingface/hub")


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result
