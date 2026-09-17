"""按保留天数清理过程产物，删除统一走系统废纸篓。

分三类，安全等级不同：

- ``rebuildable_cache``：波形图、对齐缓存、供应商目录、视频预览。打开就重建，随便清。
- ``process_artifacts``：自定义参考音频、转写上传的音频。清掉不影响历史文字，
  但之后不能再基于源音频补时间戳。
- ``generated_outputs``：历史记录里的合成音频。清掉就无法回听，只能重新生成，
  所以默认保留天数是 0（永不清理），要用户自己改成保留 N 天。

保留天数取自 ``AppSettings`` 上的 ``storage_retention_*_days``；0 表示永不清理。
文件一律移进系统废纸篓，不做不可恢复的删除；废纸篓不可用时保留文件并报告原因。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.models.schemas import AppSettings
from app.services import custom_reference_store, seed_asset_store, trash_bin
from app.services.paths import expand_path

log = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400

# outputs 下的诊断目录有自己的清理入口，不参与保留策略。
_OUTPUT_SKIP_DIRS = frozenset({"diagnostics", "batches"})


@dataclass(frozen=True)
class RetentionCategory:
    key: str
    label: str
    description: str
    settings_field: str
    warning: str | None = None


CATEGORIES: tuple[RetentionCategory, ...] = (
    RetentionCategory(
        key="rebuildable_cache",
        label="可重建缓存",
        description="波形图、对齐缓存、供应商目录和视频预览，需要时会自动重建。",
        settings_field="storage_retention_cache_days",
    ),
    RetentionCategory(
        key="process_artifacts",
        label="过程产物",
        description="自定义音色的参考音频、上传图片和转写上传的音频；不影响历史文字，之后不能再用源音频补时间戳。",
        settings_field="storage_retention_artifact_days",
    ),
    RetentionCategory(
        key="generated_outputs",
        label="生成结果",
        description="历史记录里的合成音频。",
        settings_field="storage_retention_output_days",
        warning="清理后历史记录里的音频将无法播放，需要重新生成。",
    ),
)

CATEGORY_BY_KEY = {category.key: category for category in CATEGORIES}


def policy_days(settings: AppSettings, category_key: str) -> int:
    category = CATEGORY_BY_KEY.get(category_key)
    if category is None:
        raise ValueError(f"Unknown retention category: {category_key}")
    return int(getattr(settings, category.settings_field, 0) or 0)


def category_roots(settings: AppSettings, category_key: str) -> list[Path]:
    """返回该分类涉及的根目录（可能不存在）。"""
    data_dir = expand_path(settings.data_dir)
    cache_dir = expand_path(settings.cache_dir)
    if category_key == "rebuildable_cache":
        return [
            cache_dir / "waveforms",
            cache_dir / "qwen-align",
            cache_dir / "provider-catalogs",
            cache_dir / "video-preview",
        ]
    if category_key == "process_artifacts":
        return [
            data_dir / "assets" / "reference-audio" / "custom",
            data_dir / "assets" / "seed-audio" / "images",
            cache_dir / "asr_uploads",
        ]
    if category_key == "generated_outputs":
        return [expand_path(settings.output_dir)]
    raise ValueError(f"Unknown retention category: {category_key}")


def _iter_files(
    root: Path,
    *,
    skip_dirs: frozenset[str] = frozenset(),
) -> Iterator[tuple[Path, int, float]]:
    """遍历 root 下的普通文件，产出 (路径, 字节数, 最后使用时间)。

    跳过符号链接；只把 root 之内、真实存在的普通文件交给上层。
    """
    if not root.exists() or root.is_symlink() or not root.is_dir():
        return
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in skip_dirs:
                                continue
                            stack.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        metadata = entry.stat(follow_symlinks=False)
                        yield (
                            Path(entry.path),
                            metadata.st_size,
                            max(metadata.st_atime, metadata.st_mtime),
                        )
                    except OSError as exc:
                        log.warning("保留策略扫描跳过 %s：%s", entry.path, exc)
        except OSError as exc:
            log.warning("保留策略无法扫描 %s：%s", current, exc)


def _skip_dirs_for(category_key: str) -> frozenset[str]:
    return _OUTPUT_SKIP_DIRS if category_key == "generated_outputs" else frozenset()


def _ttl_cutoff(ttl_days: int, now: float | None) -> float | None:
    if ttl_days <= 0:
        return None
    return (time.time() if now is None else now) - ttl_days * SECONDS_PER_DAY


def scan_category(
    settings: AppSettings,
    category_key: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """统计该分类的占用和按当前策略可清理的量。"""
    if category_key not in CATEGORY_BY_KEY:
        raise ValueError(f"Unknown retention category: {category_key}")

    days = policy_days(settings, category_key)
    cutoff = _ttl_cutoff(days, now)
    skip_dirs = _skip_dirs_for(category_key)

    total_bytes = 0
    total_files = 0
    reclaimable_bytes = 0
    reclaimable_files = 0
    for root in category_roots(settings, category_key):
        for _path, size, last_used in _iter_files(root, skip_dirs=skip_dirs):
            total_files += 1
            total_bytes += size
            if cutoff is not None and last_used <= cutoff:
                reclaimable_files += 1
                reclaimable_bytes += size

    return {
        "key": category_key,
        "label": CATEGORY_BY_KEY[category_key].label,
        "description": CATEGORY_BY_KEY[category_key].description,
        "warning": CATEGORY_BY_KEY[category_key].warning,
        "retention_days": days,
        "total_bytes": total_bytes,
        "total_files": total_files,
        "reclaimable_bytes": reclaimable_bytes,
        "reclaimable_files": reclaimable_files,
        "roots": [str(root) for root in category_roots(settings, category_key)],
    }


def _trash_old_files(
    roots: Iterable[Path],
    *,
    ttl_days: int,
    skip_dirs: frozenset[str],
    now: float | None,
) -> dict[str, Any]:
    cutoff = _ttl_cutoff(ttl_days, now)
    result: dict[str, Any] = {
        "trashed_files": 0,
        "trashed_bytes": 0,
        "failed": [],
    }
    if cutoff is None:
        result["skipped"] = "保留天数为 0，永不自动清理"
        return result

    for root in roots:
        for path, size, last_used in _iter_files(root, skip_dirs=skip_dirs):
            if last_used > cutoff:
                continue
            outcome = trash_bin.move_to_trash([path])
            if outcome.moved_count == 1:
                result["trashed_files"] += 1
                result["trashed_bytes"] += size
            else:
                reason = outcome.failed[0][1] if outcome.failed else "未知原因"
                result["failed"].append({"path": str(path), "reason": reason})
    return result


def cleanup_category(
    settings: AppSettings,
    category_key: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """按保留策略清理一个分类，文件统一进系统废纸篓。"""
    if category_key not in CATEGORY_BY_KEY:
        raise ValueError(f"Unknown retention category: {category_key}")

    days = policy_days(settings, category_key)
    summary: dict[str, Any] = {
        "key": category_key,
        "label": CATEGORY_BY_KEY[category_key].label,
        "retention_days": days,
        "trashed_files": 0,
        "trashed_bytes": 0,
        "failed": [],
    }
    if days <= 0:
        summary["skipped"] = "保留天数为 0，永不自动清理"
        return summary

    if not trash_bin.trash_available():
        summary["failed"].append({"path": "", "reason": trash_bin.unavailable_reason()})
        return summary

    if category_key == "process_artifacts":
        # 参考音频和图片素材都只清“已无任何引用”的文件，复用各自的引用扫描。
        errors: list[str] = []
        removed_ids = custom_reference_store.cleanup_orphaned_uploads(
            ttl_seconds=days * SECONDS_PER_DAY,
            errors=errors,
        )
        summary["trashed_files"] += len(removed_ids)
        summary["failed"].extend({"path": "", "reason": message} for message in errors)
        try:
            removed_images = seed_asset_store.cleanup_orphaned_assets(
                ttl_seconds=days * SECONDS_PER_DAY,
            )
            summary["trashed_files"] += len(removed_images)
        except Exception as exc:
            log.warning("图片素材清理失败：%s", exc)
            summary["failed"].append({"path": "", "reason": f"图片素材清理失败：{exc}"})
        files_result = _trash_old_files(
            [expand_path(settings.cache_dir) / "asr_uploads"],
            ttl_days=days,
            skip_dirs=frozenset(),
            now=now,
        )
    else:
        files_result = _trash_old_files(
            category_roots(settings, category_key),
            ttl_days=days,
            skip_dirs=_skip_dirs_for(category_key),
            now=now,
        )

    summary["trashed_files"] += files_result["trashed_files"]
    summary["trashed_bytes"] += files_result["trashed_bytes"]
    summary["failed"].extend(files_result["failed"])
    return summary


def report(settings: AppSettings, *, now: float | None = None) -> list[dict[str, Any]]:
    """三类产物的完整状态，供设置页展示。"""
    return [scan_category(settings, category.key, now=now) for category in CATEGORIES]


def run_all(
    settings: AppSettings,
    *,
    categories: Iterable[str] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """按策略清理指定分类（默认全部），返回每类结果。"""
    selected = list(categories) if categories else [category.key for category in CATEGORIES]
    results: list[dict[str, Any]] = []
    for key in selected:
        if key not in CATEGORY_BY_KEY:
            raise ValueError(f"Unknown retention category: {key}")
        try:
            results.append(cleanup_category(settings, key, now=now))
        except Exception as exc:  # 单类失败不影响其它分类
            log.exception("保留策略清理失败：%s", key)
            results.append(
                {
                    "key": key,
                    "label": CATEGORY_BY_KEY[key].label,
                    "trashed_files": 0,
                    "trashed_bytes": 0,
                    "failed": [{"path": "", "reason": str(exc)}],
                }
            )
    return {
        "categories": results,
        "trashed_files": sum(item["trashed_files"] for item in results),
        "trashed_bytes": sum(item["trashed_bytes"] for item in results),
        "trash_available": trash_bin.trash_available(),
    }


def run_startup_cleanup(settings: AppSettings | None = None) -> dict[str, Any]:
    """启动时的保留策略清理：三类里保留天数大于 0 的才会动。"""
    if settings is None:
        from app.services import settings_store

        settings = settings_store.get()
    return run_all(settings)
