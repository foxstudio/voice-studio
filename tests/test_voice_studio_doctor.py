from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "voice_studio_doctor.py"
SPEC = importlib.util.spec_from_file_location("voice_studio_doctor", SCRIPT)
assert SPEC and SPEC.loader
doctor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(doctor)


def _all_commands(name: str) -> str:
    return f"/tools/{name}"


def test_apple_silicon_is_native_ready_with_posix_tools():
    report = doctor.inspect_environment(
        system="Darwin",
        machine="arm64",
        python_version=(3, 12, 0),
        which=_all_commands,
        launcher="posix",
    )

    assert report["operating_system"] == "macos"
    assert report["native_core_runtime"]["supported"] is True
    assert report["startup_ready"] is True


def test_native_windows_is_explicitly_routed_to_wsl():
    report = doctor.inspect_environment(
        system="Windows",
        machine="AMD64",
        python_version=(3, 12, 0),
        which=_all_commands,
        launcher="windows",
    )

    assert report["native_core_runtime"]["supported"] is False
    assert report["native_core_runtime"]["reason_code"] == "native_windows_mlx_unsupported"
    assert "WSL 2" in report["native_core_runtime"]["message"]
    assert report["startup_ready"] is True
    assert report["launch_route"] == "wsl2"
    assert report["issues"] == []


def test_windows_without_wsl_is_not_startup_ready():
    report = doctor.inspect_environment(
        system="Windows",
        machine="AMD64",
        python_version=(3, 12, 0),
        which=lambda name: None if name == "wsl.exe" else f"/tools/{name}",
        launcher="windows",
    )

    assert report["startup_ready"] is False
    assert report["launch_route"] == "wsl2"
    assert report["missing_commands"] == ["wsl.exe"]
    assert [item["code"] for item in report["issues"]] == ["commands_missing"]


def test_missing_startup_command_is_actionable():
    report = doctor.inspect_environment(
        system="Darwin",
        machine="arm64",
        python_version=(3, 12, 0),
        which=lambda name: None if name == "pnpm" else f"/tools/{name}",
        launcher="posix",
    )

    assert report["missing_commands"] == ["pnpm"]
    assert report["startup_ready"] is False
    assert report["issues"][0]["code"] == "commands_missing"


def test_old_python_is_rejected_before_startup():
    report = doctor.inspect_environment(
        system="Linux",
        machine="x86_64",
        python_version=(3, 9, 19),
        which=_all_commands,
        launcher="posix",
    )

    assert report["python_supported"] is False
    assert report["startup_ready"] is False
    assert report["issues"][0]["code"] == "python_version_unsupported"
