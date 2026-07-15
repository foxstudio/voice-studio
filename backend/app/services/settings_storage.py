"""Storage inventory and whitelisted cleanup operations for settings."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from app.schemas.voice_studio import AppSettings
from app.services.paths import PROJECT_ROOT, expand_path


def audit(settings: AppSettings, database_path: Path) -> dict[str, Any]:
    paths = _paths(settings)
    locations = [
        _location("data_dir", "数据根目录", paths["data_dir"], category="配置", description="默认承载配置、数据库、持久素材和运行时缓存。"),
        _location("database", "本地数据库", database_path, category="配置", description="保存设置、任务、历史、持久音色、ASR 和项目索引。"),
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
    counted_roots = {
        "assets",
        "legacy_seed_audio_assets",
        "voice_dir",
        "output_dir",
        "export_dir",
        "project_dir",
        "cache_dir",
        "log_dir",
    }
    total_bytes = sum(
        item["size_bytes"] or 0
        for item in locations
        if item["key"] in counted_roots
    )
    return {
        "locations": locations,
        "flows": _flows(paths),
        "total_bytes": total_bytes,
    }


def cleanup(targets: list[str], allowed: dict[str, Path]) -> dict[str, Any]:
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


def _paths(settings: AppSettings) -> dict[str, Path]:
    data_dir = expand_path(settings.data_dir)
    return {
        "data_dir": data_dir,
        "assets": data_dir / "assets",
        "seed_audio_images": data_dir / "assets" / "seed-audio" / "images",
        "custom_reference_audio": data_dir / "assets" / "reference-audio" / "custom",
        "legacy_seed_audio_assets": data_dir / "seed_audio" / "assets",
        "model_dir": expand_path(settings.model_dir, PROJECT_ROOT),
        "voice_dir": expand_path(settings.voice_dir),
        "output_dir": expand_path(settings.output_dir),
        "export_dir": expand_path(settings.export_dir),
        "project_dir": expand_path(settings.project_dir),
        "cache_dir": expand_path(settings.cache_dir),
        "log_dir": expand_path(settings.log_dir),
    }


def _flows(paths: dict[str, Path]) -> list[dict[str, str]]:
    return [
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
                        return {
                            "exists": True,
                            "size_bytes": size_bytes,
                            "file_count": file_count,
                            "truncated": True,
                        }
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            size_bytes += entry.stat(follow_symlinks=False).st_size
                            file_count += 1
                    except OSError:
                        truncated = True
        except OSError:
            truncated = True
    return {
        "exists": True,
        "size_bytes": size_bytes,
        "file_count": file_count,
        "truncated": truncated,
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
