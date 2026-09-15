from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import video_localization_export_destinations as destinations


def test_default_mode_registers_configured_export_directory(tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    service = destinations.ExportDestinationService(
        export_dir_provider=lambda: export_dir,
        directory_picker=lambda: pytest.fail("default mode must not open a picker"),
    )

    selected = service.select("default")

    assert selected.status == "selected"
    assert selected.destination_id
    assert selected.display_path == str(export_dir.resolve())
    assert service.resolve(selected.destination_id) == export_dir.resolve()


def test_choose_mode_registers_directory_returned_by_picker(tmp_path: Path) -> None:
    chosen_dir = tmp_path / "chosen"
    chosen_dir.mkdir()
    service = destinations.ExportDestinationService(
        export_dir_provider=lambda: pytest.fail("choose mode must not read the default directory"),
        directory_picker=lambda: chosen_dir,
    )

    selected = service.select("choose")

    assert selected.status == "selected"
    assert selected.destination_id
    assert selected.display_path == str(chosen_dir.resolve())
    assert service.resolve(selected.destination_id) == chosen_dir.resolve()


def test_choose_cancellation_is_an_explicit_result(tmp_path: Path) -> None:
    service = destinations.ExportDestinationService(
        export_dir_provider=lambda: tmp_path,
        directory_picker=lambda: None,
    )

    selected = service.select("choose")

    assert selected.status == "cancelled"
    assert selected.destination_id is None
    assert selected.display_path is None


def test_only_default_and_choose_modes_are_accepted(tmp_path: Path) -> None:
    service = destinations.ExportDestinationService(
        export_dir_provider=lambda: tmp_path,
        directory_picker=lambda: tmp_path,
    )

    with pytest.raises(destinations.ExportDestinationModeError, match="default.*choose"):
        service.select("custom")  # type: ignore[arg-type]


def test_resolve_rejects_unknown_opaque_token(tmp_path: Path) -> None:
    service = destinations.ExportDestinationService(
        export_dir_provider=lambda: tmp_path,
        directory_picker=lambda: tmp_path,
    )

    with pytest.raises(destinations.ExportDestinationNotFoundError, match="不存在或已失效"):
        service.resolve("not-a-registered-destination")


def test_resolve_revalidates_directory_existence(tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    service = destinations.ExportDestinationService(
        export_dir_provider=lambda: export_dir,
        directory_picker=lambda: export_dir,
    )
    selected = service.select("default")
    export_dir.rmdir()

    with pytest.raises(destinations.ExportDestinationUnavailableError, match="不存在"):
        service.resolve(selected.destination_id or "")


def test_resolve_revalidates_directory_writability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = destinations.ExportDestinationService(
        export_dir_provider=lambda: tmp_path,
        directory_picker=lambda: tmp_path,
    )
    selected = service.select("default")
    original_access = os.access

    def deny_registered_directory(path: os.PathLike[str] | str, mode: int) -> bool:
        if Path(path) == tmp_path.resolve():
            return False
        return original_access(path, mode)

    monkeypatch.setattr(destinations.os, "access", deny_registered_directory)

    with pytest.raises(destinations.ExportDestinationUnavailableError, match="不可写"):
        service.resolve(selected.destination_id or "")


def test_registry_is_thread_safe_and_tokens_are_unique(tmp_path: Path) -> None:
    service = destinations.ExportDestinationService(
        export_dir_provider=lambda: tmp_path,
        directory_picker=lambda: tmp_path,
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        selections = list(pool.map(lambda _: service.select("default"), range(64)))

    destination_ids = [
        item.destination_id
        for item in selections
        if item.destination_id is not None
    ]
    assert len(destination_ids) == 64
    assert len(set(destination_ids)) == 64
    assert all(service.resolve(item) == tmp_path.resolve() for item in destination_ids)


def test_macos_picker_uses_native_osascript_without_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    class Completed:
        returncode = 0
        stdout = f"{tmp_path}\n"
        stderr = ""

    def fake_run(command: list[str], **kwargs: object) -> Completed:
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(destinations.subprocess, "run", fake_run)

    picked = destinations.NativeDirectoryPicker(platform="darwin", os_name="posix")()

    assert picked == tmp_path
    assert calls
    assert calls[0][0][0] == "osascript"
    assert calls[0][1]["shell"] is False


def test_macos_picker_maps_user_cancellation_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "execution error: User canceled. (-128)"

    monkeypatch.setattr(
        destinations.subprocess,
        "run",
        lambda *_args, **_kwargs: Completed(),
    )

    assert destinations.NativeDirectoryPicker(platform="darwin", os_name="posix")() is None


def test_macos_picker_reports_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_launch(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("osascript")

    monkeypatch.setattr(destinations.subprocess, "run", fail_to_launch)

    with pytest.raises(
        destinations.ExportDestinationPickerUnavailableError,
        match="osascript",
    ):
        destinations.NativeDirectoryPicker(platform="darwin", os_name="posix")()


def test_windows_picker_uses_folder_browser_and_maps_empty_result_to_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        destinations.shutil,
        "which",
        lambda command: "C:\\Windows\\System32\\powershell.exe"
        if command == "powershell"
        else None,
    )

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        calls.append(command)
        return Completed()

    monkeypatch.setattr(destinations.subprocess, "run", fake_run)

    picked = destinations.NativeDirectoryPicker(platform="win32", os_name="nt")()

    assert picked is None
    assert calls
    assert "-STA" in calls[0]
    assert "FolderBrowserDialog" in calls[0][-1]


def test_linux_without_supported_picker_has_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(destinations.shutil, "which", lambda _command: None)

    with pytest.raises(
        destinations.ExportDestinationPickerUnavailableError,
        match="Linux.*zenity",
    ):
        destinations.NativeDirectoryPicker(platform="linux", os_name="posix")()
