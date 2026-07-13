from __future__ import annotations

import json
import os
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Any

from app.schemas.voice_studio import AppSettings
from app.services import confucius4_paths, database as db
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
    values.pop("doubao_api_key", None)
    values.pop("volcengine_access_key_id", None)
    values.pop("volcengine_secret_access_key", None)
    settings = AppSettings(**values)
    settings.mimo_api_key_configured = bool(raw.get("mimo_api_key"))
    settings.doubao_api_key_configured = bool(raw.get("doubao_api_key") or os.environ.get("VOLCENGINE_API_KEY"))
    settings.volcengine_access_key_id_configured = bool(
        raw.get("volcengine_access_key_id") or os.environ.get("VOLCENGINE_ACCESS_KEY_ID")
    )
    settings.volcengine_secret_access_key_configured = bool(
        raw.get("volcengine_secret_access_key") or os.environ.get("VOLCENGINE_SECRET_ACCESS_KEY")
    )
    return settings


def update(settings: AppSettings) -> AppSettings:
    data = settings.model_dump()
    data.pop("mimo_api_key_configured", None)
    data.pop("doubao_api_key_configured", None)
    data.pop("volcengine_access_key_id_configured", None)
    data.pop("volcengine_secret_access_key_configured", None)
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


def update_doubao_api_key(api_key: str | None, clear: bool = False) -> AppSettings:
    if clear:
        db.save_setting("doubao_api_key", "")
    elif api_key is not None and api_key.strip():
        db.save_setting("doubao_api_key", api_key.strip())
        db.save_setting("cloud_enabled", json.dumps(True))
    return get()


def doubao_api_key() -> str | None:
    value = db.get_settings_rows().get("doubao_api_key")
    return value or os.environ.get("VOLCENGINE_API_KEY") or None


def update_volcengine_directory_credentials(
    access_key_id: str | None,
    secret_access_key: str | None,
    *,
    clear_access_key_id: bool = False,
    clear_secret_access_key: bool = False,
) -> AppSettings:
    if clear_access_key_id:
        db.save_setting("volcengine_access_key_id", "")
    elif access_key_id is not None and access_key_id.strip():
        db.save_setting("volcengine_access_key_id", access_key_id.strip())

    if clear_secret_access_key:
        db.save_setting("volcengine_secret_access_key", "")
    elif secret_access_key is not None and secret_access_key.strip():
        db.save_setting("volcengine_secret_access_key", secret_access_key.strip())

    return get()


def volcengine_access_key_id() -> str | None:
    return (
        db.get_settings_rows().get("volcengine_access_key_id")
        or os.environ.get("VOLCENGINE_ACCESS_KEY_ID")
        or None
    )


def volcengine_secret_access_key() -> str | None:
    return (
        db.get_settings_rows().get("volcengine_secret_access_key")
        or os.environ.get("VOLCENGINE_SECRET_ACCESS_KEY")
        or None
    )


def ensure_directories(settings: AppSettings | None = None) -> None:
    s = settings or get()
    for value, base in [
        (s.model_dir, PROJECT_ROOT),
        (s.voice_dir, None),
        (s.output_dir, None),
        (s.export_dir, None),
        (s.project_dir, None),
        (s.cache_dir, None),
        (s.log_dir, None),
    ]:
        expand_path(value, base).mkdir(parents=True, exist_ok=True)
    data_root = expand_path(s.data_dir)
    cache_root = expand_path(s.cache_dir)
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


def model_path(engine_id: str) -> Path:
    for candidate in model_candidates(engine_id):
        if candidate.exists():
            return candidate
    return model_candidates(engine_id)[0]


def model_candidates(engine_id: str) -> list[Path]:
    s = get()
    base = expand_path(s.model_dir, PROJECT_ROOT)
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


def _dir_stats(path: Path, max_entries: int = 20000) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "size_bytes": 0, "file_count": 0, "truncated": False}
    if path.is_symlink():
        return {"exists": True, "size_bytes": 0, "file_count": 0, "truncated": True}
    if path.is_file():
        return {"exists": True, "size_bytes": path.stat().st_size, "file_count": 1, "truncated": False}

    size_bytes = 0
    file_count = 0
    stack = [path]
    truncated = False
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if file_count >= max_entries:
                        truncated = True
                        return {
                            "exists": True,
                            "size_bytes": size_bytes,
                            "file_count": file_count,
                            "truncated": truncated,
                        }
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            size_bytes += stat.st_size
                            file_count += 1
                    except OSError:
                        truncated = True
        except OSError:
            truncated = True
    return {"exists": True, "size_bytes": size_bytes, "file_count": file_count, "truncated": truncated}


def _location(
    key: str,
    label: str,
    path: Path,
    *,
    category: str,
    description: str,
    cleanup_key: str | None = None,
    cleanup_label: str | None = None,
    cleanup_risk: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "path": str(path),
        "category": category,
        "description": description,
        "cleanup_key": cleanup_key,
        "cleanup_label": cleanup_label,
        "cleanup_risk": cleanup_risk,
        **_dir_stats(path),
    }


def storage_audit() -> dict[str, Any]:
    s = get()
    paths = {
        "data_dir": expand_path(s.data_dir),
        "assets": expand_path(s.data_dir) / "assets",
        "seed_audio_images": expand_path(s.data_dir) / "assets" / "seed-audio" / "images",
        "custom_reference_audio": expand_path(s.data_dir) / "assets" / "reference-audio" / "custom",
        "legacy_seed_audio_assets": expand_path(s.data_dir) / "seed_audio" / "assets",
        "model_dir": expand_path(s.model_dir, PROJECT_ROOT),
        "voice_dir": expand_path(s.voice_dir),
        "output_dir": expand_path(s.output_dir),
        "export_dir": expand_path(s.export_dir),
        "project_dir": expand_path(s.project_dir),
        "cache_dir": expand_path(s.cache_dir),
        "log_dir": expand_path(s.log_dir),
    }
    locations = [
        _location("data_dir", "数据根目录", paths["data_dir"], category="配置", description="默认承载配置、数据库、持久素材和运行时缓存。"),
        _location("database", "本地数据库", db.DB_PATH, category="配置", description="保存设置、任务、历史、持久音色、ASR 和项目索引。"),
        _location("assets", "受管素材", paths["assets"], category="素材", description="应用统一管理的输入素材根目录；不同子目录按各自引用关系保留或回收。"),
        _location("seed_audio_images", "Seed Audio 图片", paths["seed_audio_images"], category="受管素材", description="任务或历史仍引用时保留；未使用上传超过 7 天后回收，内置预设图片长期保留。"),
        _location("custom_reference_audio", "自定义参考音频", paths["custom_reference_audio"], category="受管临时素材", description="生成记录、长文本、批处理、预设或音色库仍引用时保留；无引用超过 7 天后自动回收。"),
        _location("legacy_seed_audio_assets", "旧版 Seed Audio 素材", paths["legacy_seed_audio_assets"], category="素材", description="旧版本保存的参考图片；仍可读取和删除，不参与自动缓存清理。"),
        _location("model_dir", "模型目录", paths["model_dir"], category="模型", description="本地模型权重目录，不参与自动清理；引擎运行时代码单独放在 engines。"),
        _location("voice_dir", "持久音色音频", paths["voice_dir"], category="音色", description="已注册到音色库的参考音频；属于持久数据，不会自动清理。"),
        _location("output_dir", "生成输出", paths["output_dir"], category="生成", description="单条生成、长文本分段和最终音频结果。"),
        _location("batch_outputs", "批处理输出", paths["output_dir"] / "batches", category="生成", description="批量合成任务默认输出目录。"),
        _location(
            "diagnostics",
            "诊断音频",
            paths["output_dir"] / "diagnostics",
            category="生成",
            description="引擎诊断和试跑产生的临时音频。",
            cleanup_key="diagnostics",
            cleanup_label="清理诊断音频",
            cleanup_risk="low",
        ),
        _location("export_dir", "导出结果", paths["export_dir"], category="导出", description="合并导出、打包导出和音频工具导出的结果。"),
        _location("project_dir", "项目目录", paths["project_dir"], category="项目", description="视频/长项目相关资产目录。"),
        _location("cache_dir", "缓存根目录", paths["cache_dir"], category="缓存", description="仅 waveforms、qwen-align、provider-catalogs 会按 TTL 和容量自动治理；其他子目录会保留。"),
        _location(
            "asr_uploads",
            "ASR 源音频",
            paths["cache_dir"] / "asr_uploads",
            category="持久数据",
            description="ASR 识别上传的源音频；删除后历史转写仍在，但无法再补时间戳。",
            cleanup_risk=None,
        ),
        _location(
            "waveforms",
            "波形缓存",
            paths["cache_dir"] / "waveforms",
            category="可重建缓存",
            description="从生成音频重建的波形峰值；低风险，受自动缓存治理。",
            cleanup_risk="low",
        ),
        _location(
            "qwen_align",
            "Qwen 对齐日志",
            paths["cache_dir"] / "qwen-align",
            category="可重建缓存",
            description="强制对齐 worker 日志和临时记录；低风险，受自动缓存治理。",
            cleanup_key="qwen_align",
            cleanup_label="清理对齐缓存",
            cleanup_risk="low",
        ),
        _location(
            "provider_catalogs",
            "服务商目录缓存",
            paths["cache_dir"] / "provider-catalogs",
            category="可重建缓存",
            description="云服务商音色目录与预览缓存；低风险，受自动缓存治理。",
            cleanup_risk="low",
        ),
        _location(
            "log_dir",
            "应用日志",
            paths["log_dir"],
            category="日志",
            description="应用层日志目录；部分外部引擎还会在自身根目录写 .voice_studio 日志。",
            cleanup_key="logs",
            cleanup_label="清理应用日志",
            cleanup_risk="medium",
        ),
    ]
    total_bytes = sum(item["size_bytes"] or 0 for item in locations if item["key"] in {"assets", "legacy_seed_audio_assets", "voice_dir", "output_dir", "export_dir", "project_dir", "cache_dir", "log_dir"})
    flows = [
        {
            "name": "Seed Audio 参考图片",
            "path": str(paths["seed_audio_images"] / "<file_id>.<jpg|png|webp>"),
            "description": "新上传图片进入统一持久素材目录；旧版素材在读取时兼容迁移。",
        },
        {
            "name": "自定义参考音频上传",
            "path": str(paths["custom_reference_audio"] / "<file_id>.<ext>"),
            "description": "生成页上传和裁切的参考音频先进入持久素材目录，并按任务、历史和音色引用管理。",
        },
        {
            "name": "自定义音色注册",
            "path": str(paths["voice_dir"] / "<file_id>.<ext>"),
            "description": "注册到音色库后，参考音频移动到持久音色目录。",
        },
        {
            "name": "自定义音色 ASR",
            "path": str(paths["cache_dir"] / "asr_uploads" / "<engine_id>" / "<transcription_id>.<ext>"),
            "description": "ASR 识别保留源音频，供字幕导出和后续时间戳补齐使用。",
        },
        {
            "name": "单条/长文本生成",
            "path": str(paths["output_dir"] / "<task_id>.<wav|mp3|flac>"),
            "description": "任务先生成 wav，需要其他格式时再转换为目标格式并写入历史。",
        },
        {
            "name": "批量生成",
            "path": str(paths["output_dir"] / "batches" / "<batch_task_id>" / "<segment_id>.<format>"),
            "description": "未指定输出目录时，批处理按任务 ID 分目录保存每段结果。",
        },
        {
            "name": "导出/合并",
            "path": str(paths["export_dir"] / "<export_id>.<format>"),
            "description": "历史结果合并、项目导出和音频工具合并会写入导出目录。",
        },
        {
            "name": "引擎诊断",
            "path": str(paths["output_dir"] / "diagnostics" / "<engine_id>-diagnosis.wav"),
            "description": "引擎中心诊断音频，属于低风险可清理产物。",
        },
    ]
    return {"locations": locations, "flows": flows, "total_bytes": total_bytes}


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


def _clear_path_contents(path: Path) -> dict[str, Any]:
    before = _dir_stats(path)
    if path.is_symlink():
        return {
            "path": str(path),
            "before_bytes": before["size_bytes"],
            "after_bytes": before["size_bytes"],
            "removed_bytes": 0,
            "before_files": before["file_count"],
            "after_files": before["file_count"],
        }
    if path.exists():
        if path.is_file():
            path.unlink()
        else:
            for child in path.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink(missing_ok=True)
    path.mkdir(parents=True, exist_ok=True)
    after = _dir_stats(path)
    return {
        "path": str(path),
        "before_bytes": before["size_bytes"],
        "after_bytes": after["size_bytes"],
        "removed_bytes": max(0, before["size_bytes"] - after["size_bytes"]),
        "before_files": before["file_count"],
        "after_files": after["file_count"],
    }


def cleanup_storage(targets: list[str]) -> dict[str, Any]:
    allowed = _cleanup_targets()
    cleaned: list[dict[str, Any]] = []
    skipped: list[str] = []
    for target in targets:
        path = allowed.get(target)
        if path is None:
            skipped.append(target)
            continue
        cleaned.append({"target": target, **_clear_path_contents(path)})
    return {
        "cleaned": cleaned,
        "skipped": skipped,
        "removed_bytes": sum(item["removed_bytes"] for item in cleaned),
    }
