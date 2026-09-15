from __future__ import annotations

import importlib
import json
from importlib.metadata import PackageNotFoundError
import re
import sys
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_public_package_import_is_lazy(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx", None)
    sys.modules.pop("mlx_indextts", None)

    package = importlib.import_module("mlx_indextts")

    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

    assert package.__version__ == project_version
    assert "IndexTTS" not in package.__dict__


def test_source_checkout_version_falls_back_to_project_manifest(tmp_path):
    from mlx_indextts.version import resolve_version

    project_file = tmp_path / "pyproject.toml"
    project_file.write_text(
        '[build-system]\nrequires = []\n\n[project]\nname = "example"\nversion = "2.3.4"\n',
        encoding="utf-8",
    )

    def missing_distribution(_name: str) -> str:
        raise PackageNotFoundError

    assert resolve_version(missing_distribution, project_file=project_file) == "2.3.4"


def test_unresolvable_version_is_explicitly_unknown(tmp_path):
    from mlx_indextts.version import UNKNOWN_VERSION, resolve_version

    def missing_distribution(_name: str) -> str:
        raise PackageNotFoundError

    assert (
        resolve_version(
            missing_distribution,
            project_file=tmp_path / "missing-pyproject.toml",
        )
        == UNKNOWN_VERSION
    )


def test_release_version_has_one_manifest_source():
    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    frontend_manifest = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert "version" not in frontend_manifest
    assert frontend_manifest["private"] is True
    assert f"## {project_version} " in changelog


def test_native_windows_cli_exits_with_wsl_guidance():
    from mlx_indextts.cli import require_mlx_runtime

    with pytest.raises(SystemExit) as exc_info:
        require_mlx_runtime("win32")

    message = str(exc_info.value)
    assert "Windows 原生" in message
    assert "WSL 2" in message
    assert "start.ps1" in message


def test_mlx_dependencies_select_an_official_backend_per_platform():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = set(project["dependencies"])
    asr_dependencies = set(project["optional-dependencies"]["asr"])

    assert "mlx>=0.18.0; sys_platform == 'darwin'" in dependencies
    assert "mlx[cpu]>=0.18.0; sys_platform == 'linux'" in dependencies
    assert "mlx>=0.18.0" not in dependencies
    assert "omnivoice>=0.2.1,<0.3" in dependencies
    assert "mlx-audio>=0.4.6,<0.4.7; sys_platform != 'win32'" in asr_dependencies


def test_indextts_v2_extra_does_not_look_like_a_pep440_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    optional_dependencies = project["optional-dependencies"]

    assert "indextts2" in optional_dependencies
    assert "v2" not in optional_dependencies


def test_readme_syncs_complete_application_extras_together():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    install_section = readme.split("### 安装依赖", 1)[1].split("### 启动服务", 1)[0]
    sync_commands = re.findall(
        r"^uv sync[^\n]*$",
        install_section,
        flags=re.MULTILINE,
    )

    complete_install = next(
        command for command in sync_commands if "--extra server" in command
    )
    assert "--extra convert" in complete_install
    assert "--extra asr" in complete_install
    assert "--extra video_localization" in complete_install
    assert "--locked" in complete_install
    assert "pnpm install --frozen-lockfile" in install_section
    assert "uv sync --extra server" not in sync_commands
    assert "uv sync --extra convert" not in sync_commands
    assert "uv sync --extra asr" not in sync_commands
    assert "uv sync --extra video_localization" not in sync_commands

    conversion_section = readme.split("### IndexTTS v2", 1)[1].split("### 其他本地引擎", 1)[0]
    assert not re.search(r"^uv sync\b", conversion_section, flags=re.MULTILINE)
    assert "uv run --no-sync voice-studio convert" in conversion_section


def test_wheel_metadata_declares_all_bundled_source_terms():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["license"] == ("MIT AND Apache-2.0 AND LicenseRef-Bilibili-Model-Use-License")
    assert set(project["license-files"]) == {
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "third_party_licenses/*.txt",
    }
    assert "License :: OSI Approved :: MIT License" not in project["classifiers"]
