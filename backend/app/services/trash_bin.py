"""把文件移进系统废纸篓，而不是直接从磁盘抹掉。

保留策略触发的清理都走这里：用户能在系统废纸篓里看到被清掉的东西，也可以
自己「放回原处」恢复。系统废纸篓不可用时**不静默删除**，而是把失败原因回报
给调用方，由上层决定是保留还是提示用户手动处理。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

log = logging.getLogger(__name__)

# 单次调用传太多路径会撞上命令行的长度上限，按批切分。
MAX_PATHS_PER_CALL = 400


@dataclass
class TrashResult:
    """一次回收的结果。``moved`` 是成功移走的路径，``failed`` 是 (路径, 原因)。"""

    moved: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def moved_count(self) -> int:
        return len(self.moved)

    def to_dict(self) -> dict[str, object]:
        return {
            "moved_count": self.moved_count,
            "moved": list(self.moved),
            "failed_count": len(self.failed),
            "failed": [{"path": path, "reason": reason} for path, reason in self.failed],
        }


def _macos_trash_command() -> str | None:
    candidate = Path("/usr/bin/trash")
    return str(candidate) if candidate.exists() else None


def _linux_trash_command() -> list[str] | None:
    for args in (["gio", "trash"], ["trash-put"], ["trash"]):
        if shutil.which(args[0]):
            return args
    return None


def trash_command() -> list[str] | None:
    """返回当前平台可用的回收命令；都没有时返回 None。"""
    if sys.platform == "darwin":
        command = _macos_trash_command()
        return [command] if command else None
    if sys.platform.startswith("linux"):
        return _linux_trash_command()
    if sys.platform == "win32":  # PowerShell 的回收站 API 由上层改写为 send2trash 时再补
        return None
    return None


def trash_override_dir() -> Path | None:
    """回收目标覆盖目录。

    测试把清理重定向到一个临时目录，否则跑一遍用例就会往用户真实的系统废纸篓里
    塞几十个临时文件。正式运行不设这个变量，仍然使用系统废纸篓。
    """
    value = os.environ.get("VOICE_STUDIO_TRASH_DIR")
    if not value:
        return None
    return Path(value).expanduser()


def trash_available() -> bool:
    return trash_command() is not None


def unavailable_reason() -> str:
    if sys.platform == "win32":
        return "当前平台暂不支持自动移入废纸篓。"
    return "没有找到可用的废纸篓命令，已跳过清理以免直接删除文件。"


def _chunks(items: Sequence[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield list(items[start:start + size])


def move_to_trash(paths: Iterable[str | os.PathLike[str]]) -> TrashResult:
    """把路径批量移进系统废纸篓。

    不存在的路径会被忽略（已经不在磁盘上就没有可回收的东西）。
    """
    result = TrashResult()
    targets: list[str] = []
    for raw in paths:
        path = Path(raw)
        try:
            if path.exists():
                targets.append(str(path))
        except OSError as exc:
            result.failed.append((str(path), str(exc)))

    if not targets:
        return result

    override = trash_override_dir()
    if override is not None:
        # 测试或特殊部署：移到普通目录而不是系统废纸篓。
        try:
            override.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            for target in targets:
                result.failed.append((target, f"cannot create trash dir: {exc}"))
            return result
        for target in targets:
            try:
                shutil.move(target, str(override / Path(target).name))
            except OSError as exc:
                result.failed.append((target, str(exc)))
            else:
                result.moved.append(target)
        return result

    command = trash_command()
    if command is None:
        for target in targets:
            result.failed.append((target, unavailable_reason()))
        return result

    for batch in _chunks(targets, MAX_PATHS_PER_CALL):
        completed = subprocess.run(
            [*command, *batch],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            result.moved.extend(batch)
            continue
        # 批次失败时逐个重试，避免一个坏文件拖垮整批；仍然失败的记录真实原因。
        detail = (completed.stderr or completed.stdout or "废纸篓命令执行失败").strip()[-400:]
        log.warning("批量移入废纸篓失败，改为逐个重试：%s", detail)
        for target in batch:
            single = subprocess.run(
                [*command, target],
                capture_output=True,
                text=True,
                check=False,
            )
            if single.returncode == 0:
                result.moved.append(target)
            else:
                reason = (single.stderr or single.stdout or detail).strip()[-200:]
                result.failed.append((target, reason))

    return result


def trash_directory_contents(directory: str | os.PathLike[str]) -> TrashResult:
    """把目录里的内容（不含目录本身）移进废纸篓。"""
    root = Path(directory)
    if not root.is_dir():
        return TrashResult()
    return move_to_trash(sorted(root.iterdir()))
