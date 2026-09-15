#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Callable


SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def inspect_environment(
    *,
    system: str | None = None,
    machine: str | None = None,
    python_version: tuple[int, int, int] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    launcher: str = "auto",
) -> dict[str, object]:
    system_name = system or platform.system()
    architecture = (machine or platform.machine()).lower()
    version = python_version or sys.version_info[:3]
    operating_system = _operating_system(system_name)
    selected_launcher = _launcher(launcher, operating_system)
    native = _native_core_support(operating_system, architecture)
    if selected_launcher == "windows":
        required_commands = ["wsl.exe"]
    else:
        required_commands = ["uv", "pnpm", "curl", "lsof"]
    commands = {name: which(name) for name in required_commands}
    missing_commands = [name for name, path in commands.items() if path is None]
    python_supported = version >= (3, 10, 0)
    launch_route = "wsl2" if selected_launcher == "windows" else "native"
    route_supported = bool(native["supported"]) or (
        launch_route == "wsl2" and not missing_commands
    )
    issues: list[dict[str, str]] = []
    if not python_supported:
        issues.append(
            {
                "code": "python_version_unsupported",
                "message": "Voice Studio 需要 Python 3.10 或更高版本。",
            }
        )
    if missing_commands:
        issues.append(
            {
                "code": "commands_missing",
                "message": f"缺少启动工具：{', '.join(missing_commands)}。",
            }
        )
    if not native["supported"] and launch_route != "wsl2":
        issues.append(
            {
                "code": str(native["reason_code"]),
                "message": str(native["message"]),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "operating_system": operating_system,
        "architecture": architecture,
        "python_version": ".".join(str(value) for value in version),
        "python_supported": python_supported,
        "launcher": selected_launcher,
        "launch_route": launch_route,
        "native_core_runtime": native,
        "commands": commands,
        "missing_commands": missing_commands,
        "startup_ready": python_supported and not missing_commands and route_supported,
        "issues": issues,
    }


def _operating_system(system: str) -> str:
    normalized = system.strip().lower()
    return {"darwin": "macos", "windows": "windows", "linux": "linux"}.get(
        normalized,
        normalized or "unknown",
    )


def _launcher(requested: str, operating_system: str) -> str:
    if requested != "auto":
        return requested
    return "windows" if operating_system == "windows" else "posix"


def _native_core_support(operating_system: str, architecture: str) -> dict[str, object]:
    if operating_system == "macos" and architecture in {"arm64", "aarch64"}:
        return {
            "supported": True,
            "reason_code": None,
            "message": "Apple Silicon 可使用 MLX/Metal 本地引擎。",
        }
    if operating_system == "linux" and architecture in {"x86_64", "amd64", "arm64", "aarch64"}:
        return {
            "supported": True,
            "reason_code": None,
            "message": "Linux 需要按机器选择 MLX CPU 或 CUDA 后端。",
        }
    if operating_system == "windows":
        return {
            "supported": False,
            "reason_code": "native_windows_mlx_unsupported",
            "message": (
                "MLX 官方当前未提供 Windows 原生后端；"
                "IndexTTS/MLX 引擎请通过 WSL 2 启动。"
            ),
        }
    return {
        "supported": False,
        "reason_code": "platform_unsupported",
        "message": f"当前本地 MLX 核心不支持 {operating_system}/{architecture}。",
    }


def _print_human(report: dict[str, object]) -> None:
    state = "可启动" if report["startup_ready"] else "需要处理"
    print(
        f"Voice Studio 环境检查：{state}\n"
        f"- 系统：{report['operating_system']} / {report['architecture']}\n"
        f"- Python：{report['python_version']}\n"
        f"- 启动方式：{report['launcher']} / {report['launch_route']}"
    )
    native = report["native_core_runtime"]
    if isinstance(native, dict):
        print(f"- 本地模型：{native['message']}")
    for issue in report["issues"]:
        if isinstance(issue, dict):
            print(f"- [{issue['code']}] {issue['message']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Voice Studio 跨平台启动前检查")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--strict", action="store_true", help="不可启动时返回非零状态")
    parser.add_argument(
        "--launcher",
        choices=("auto", "posix", "windows"),
        default="auto",
        help="检查对应启动器的命令依赖",
    )
    args = parser.parse_args(argv)
    report = inspect_environment(launcher=args.launcher)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 1 if args.strict and not report["startup_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
