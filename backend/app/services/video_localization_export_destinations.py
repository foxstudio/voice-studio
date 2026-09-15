"""Opaque, process-local destination handles for video-localization exports.

Transport callers select either the configured default directory or the native
operating-system picker. They never submit an arbitrary filesystem path.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Protocol

from app.services import settings_store


ExportDestinationMode = Literal["default", "choose"]
ExportDestinationSelectionStatus = Literal["selected", "cancelled"]


class ExportDestinationError(RuntimeError):
    """Base error for export destination selection and resolution."""


class ExportDestinationModeError(ExportDestinationError, ValueError):
    """The caller supplied a mode outside the public contract."""


class ExportDestinationPickerUnavailableError(ExportDestinationError):
    """The current desktop cannot provide a native directory picker."""


class ExportDestinationUnavailableError(ExportDestinationError):
    """A selected directory no longer satisfies the export contract."""


class ExportDestinationNotFoundError(ExportDestinationError, LookupError):
    """The opaque destination handle is unknown to this process."""


@dataclass(frozen=True, slots=True)
class ExportDestinationSelection:
    status: ExportDestinationSelectionStatus
    destination_id: str | None
    display_path: str | None


class DirectoryPicker(Protocol):
    def __call__(self) -> Path | None: ...


class NativeDirectoryPicker:
    """Small desktop adapter for native directory selection."""

    def __init__(
        self,
        *,
        platform: str | None = None,
        os_name: str | None = None,
    ) -> None:
        self._platform = platform or sys.platform
        self._os_name = os_name or os.name

    def __call__(self) -> Path | None:
        if self._platform == "darwin":
            return self._pick_macos()
        if self._os_name == "nt":
            return self._pick_windows()
        if self._platform.startswith("linux"):
            return self._pick_linux()
        raise ExportDestinationPickerUnavailableError(
            f"当前系统不支持选择导出目录：{self._platform}"
        )

    def _pick_macos(self) -> Path | None:
        try:
            completed = subprocess.run(
                [
                    "osascript",
                    "-e",
                    (
                        'POSIX path of (choose folder with prompt '
                        '"选择视频本土化导出目录")'
                    ),
                ],
                capture_output=True,
                text=True,
                shell=False,
            )
        except OSError as exc:
            raise ExportDestinationPickerUnavailableError(
                "macOS 目录选择器不可用：无法启动 osascript。"
            ) from exc
        if completed.returncode == 0:
            return _path_from_picker_output(completed.stdout)
        if "-128" in completed.stderr or "User canceled" in completed.stderr:
            return None
        raise ExportDestinationPickerUnavailableError(
            "macOS 目录选择器不可用，请确认当前进程允许显示系统窗口。"
        )

    def _pick_windows(self) -> Path | None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            raise ExportDestinationPickerUnavailableError(
                "Windows 目录选择器不可用：未找到 PowerShell。"
            )
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dialog.Description = '选择视频本土化导出目录'; "
            "if ($dialog.ShowDialog() -eq "
            "[System.Windows.Forms.DialogResult]::OK) "
            "{ [Console]::Out.Write($dialog.SelectedPath) }"
        )
        try:
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-STA",
                    "-Command",
                    script,
                ],
                capture_output=True,
                text=True,
                shell=False,
            )
        except OSError as exc:
            raise ExportDestinationPickerUnavailableError(
                "Windows 目录选择器不可用：无法启动 PowerShell。"
            ) from exc
        if completed.returncode != 0:
            raise ExportDestinationPickerUnavailableError(
                "Windows 目录选择器执行失败。"
            )
        return _path_from_picker_output(completed.stdout)

    def _pick_linux(self) -> Path | None:
        zenity = shutil.which("zenity")
        if zenity is None:
            raise ExportDestinationPickerUnavailableError(
                "Linux 目录选择器不可用：请安装 zenity 后重试。"
            )
        try:
            completed = subprocess.run(
                [
                    zenity,
                    "--file-selection",
                    "--directory",
                    "--title=选择视频本土化导出目录",
                ],
                capture_output=True,
                text=True,
                shell=False,
            )
        except OSError as exc:
            raise ExportDestinationPickerUnavailableError(
                "Linux 目录选择器不可用：无法启动 zenity。"
            ) from exc
        if completed.returncode == 0:
            return _path_from_picker_output(completed.stdout)
        if completed.returncode == 1:
            return None
        raise ExportDestinationPickerUnavailableError(
            "Linux 目录选择器执行失败。"
        )


class ExportDestinationService:
    """Registers and resolves opaque destination IDs within one process."""

    def __init__(
        self,
        *,
        export_dir_provider: Callable[[], Path] = settings_store.export_dir,
        directory_picker: DirectoryPicker | None = None,
    ) -> None:
        self._export_dir_provider = export_dir_provider
        self._directory_picker = directory_picker or NativeDirectoryPicker()
        self._destinations: dict[str, Path] = {}
        self._lock = threading.RLock()
        self._picker_lock = threading.Lock()

    def select(
        self,
        mode: ExportDestinationMode,
    ) -> ExportDestinationSelection:
        if mode == "default":
            selected_path = self._export_dir_provider()
        elif mode == "choose":
            with self._picker_lock:
                selected_path = self._directory_picker()
            if selected_path is None:
                return ExportDestinationSelection(
                    status="cancelled",
                    destination_id=None,
                    display_path=None,
                )
        else:
            raise ExportDestinationModeError(
                "导出目录模式只支持 default 或 choose。"
            )

        validated_path = _validated_directory(selected_path)
        with self._lock:
            destination_id = _new_destination_id(self._destinations)
            self._destinations[destination_id] = validated_path
        return ExportDestinationSelection(
            status="selected",
            destination_id=destination_id,
            display_path=str(validated_path),
        )

    def resolve(self, destination_id: str) -> Path:
        with self._lock:
            selected_path = self._destinations.get(destination_id)
        if selected_path is None:
            raise ExportDestinationNotFoundError(
                "导出目录选择不存在或已失效，请重新选择。"
            )
        return _validated_directory(selected_path)


def _path_from_picker_output(value: str) -> Path | None:
    path_value = value.rstrip("\r\n")
    return Path(path_value) if path_value else None


def _validated_directory(path: Path) -> Path:
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ExportDestinationUnavailableError(
            "导出目录不存在，请重新选择。"
        ) from exc
    if not resolved.is_dir():
        raise ExportDestinationUnavailableError(
            "选择的导出位置不是目录，请重新选择。"
        )
    if not os.access(resolved, os.W_OK):
        raise ExportDestinationUnavailableError(
            "导出目录不可写，请选择其他目录。"
        )
    return resolved


def _new_destination_id(destinations: dict[str, Path]) -> str:
    while True:
        destination_id = f"vld_{secrets.token_urlsafe(32)}"
        if destination_id not in destinations:
            return destination_id


_service = ExportDestinationService()


def select_destination(
    mode: ExportDestinationMode,
) -> ExportDestinationSelection:
    return _service.select(mode)


def resolve_destination(destination_id: str) -> Path:
    return _service.resolve(destination_id)
