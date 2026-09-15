from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.python_runtime import engine_virtualenv_python, virtualenv_python  # noqa: E402


def test_virtualenv_python_uses_windows_scripts_directory():
    assert virtualenv_python("runtime/.venv", platform="win32") == (
        Path("runtime") / ".venv" / "Scripts" / "python.exe"
    )


def test_virtualenv_python_uses_posix_bin_directory():
    expected = Path("runtime") / ".venv" / "bin" / "python"
    assert virtualenv_python("runtime/.venv", platform="darwin") == expected
    assert virtualenv_python("runtime/.venv", platform="linux") == expected


def test_engine_virtualenv_python_supports_named_environment():
    assert engine_virtualenv_python(
        "runtime",
        virtualenv_name=".venv-qwen-align",
        platform="win32",
    ) == Path("runtime") / ".venv-qwen-align" / "Scripts" / "python.exe"
