from __future__ import annotations

import json
import os
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Any

from app.schemas.voice_studio import AppSettings
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
    for candidate in model_candidates(engine_id):
        if candidate.exists():
            return candidate
    return model_candidates(engine_id)[0]


def model_candidates(engine_id: str) -> list[Path]:
    s = get()
    base = expand_path(s.model_dir, PROJECT_ROOT)
    if engine_id == "indextts-v2":
        return [base / "mlx-indexTTS-2.0"]
    if engine_id == "qwen3-asr-mlx":
        return [
            base / "qwen3-asr-mlx",
            base / "mlx-community_Qwen3-ASR-1.7B-8bit",
            expand_path("~/Documents/Voxt Modles/mlx-audio/mlx-community_Qwen3-ASR-1.7B-8bit"),
        ]
    return [base / engine_id]


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
        "model_dir": expand_path(s.model_dir, PROJECT_ROOT),
        "voice_dir": expand_path(s.voice_dir),
        "output_dir": expand_path(s.output_dir),
        "export_dir": expand_path(s.export_dir),
        "project_dir": expand_path(s.project_dir),
        "cache_dir": expand_path(s.cache_dir),
        "log_dir": expand_path(s.log_dir),
    }
    locations = [
        _location("data_dir", "数据根目录", paths["data_dir"], category="配置", description="默认承载配置、数据库和各类数据子目录。"),
        _location("database", "本地数据库", db.DB_PATH, category="配置", description="保存设置、任务、历史、音色、ASR 和项目索引。"),
        _location("model_dir", "模型目录", paths["model_dir"], category="模型", description="本地模型权重和引擎依赖目录，通常不建议自动清理。"),
        _location("voice_dir", "音色库音频", paths["voice_dir"], category="音色", description="上传、导入、注册到音色库的参考音频文件。"),
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
        _location("cache_dir", "缓存根目录", paths["cache_dir"], category="缓存", description="ASR 源音频、对齐日志和其他可复用缓存的根目录。"),
        _location(
            "asr_uploads",
            "ASR 源音频",
            paths["cache_dir"] / "asr_uploads",
            category="缓存",
            description="ASR 识别上传的源音频；删除后历史转写仍在，但无法再补时间戳。",
            cleanup_key="asr_uploads",
            cleanup_label="清理 ASR 源音频",
            cleanup_risk="high",
        ),
        _location(
            "qwen_align",
            "Qwen 对齐日志",
            paths["cache_dir"] / "qwen-align",
            category="缓存",
            description="强制对齐 worker 日志和临时记录。",
            cleanup_key="qwen_align",
            cleanup_label="清理对齐缓存",
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
    total_bytes = sum(item["size_bytes"] or 0 for item in locations if item["key"] in {"voice_dir", "output_dir", "export_dir", "project_dir", "cache_dir", "log_dir"})
    flows = [
        {
            "name": "自定义音色拖拽上传",
            "path": str(paths["voice_dir"] / "<file_id>.<ext>"),
            "description": "注册音色库或生成页自定义音色确认后，参考音频会进入音色库目录。",
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
        "asr_uploads": cache_dir() / "asr_uploads",
    }


def _clear_path_contents(path: Path) -> dict[str, Any]:
    before = _dir_stats(path)
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
