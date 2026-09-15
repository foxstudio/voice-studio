from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import engine_runner  # noqa: E402


def test_confucius_runner_injects_the_current_managed_runtime_root(
    tmp_path,
    monkeypatch,
):
    data_root = tmp_path / "configured-data"
    monkeypatch.delenv("VOICE_STUDIO_CONFUCIUS4_MLX_AUDIO_ROOT", raising=False)
    monkeypatch.setattr(
        engine_runner.settings_store,
        "get",
        lambda: SimpleNamespace(data_dir=str(data_root)),
    )

    resolved = engine_runner._with_resolved_runtime_paths(
        "confucius4-mlx-int8",
        {"text": "统一运行时目录"},
    )

    assert resolved["runtime_root"] == str(
        data_root / "engines" / "mlx-audio-confucius4"
    )


def test_isolated_runner_drains_large_subprocess_output(monkeypatch):
    real_popen = subprocess.Popen
    script = """
import json
import sys

sys.stdin.read()
sys.stderr.write("diagnostic-log\\n" * 200000)
sys.stderr.flush()
print(json.dumps({"output_path": "/tmp/generated.wav"}))
"""

    def launch_test_process(_args, *args, **kwargs):
        return real_popen([sys.executable, "-c", script], *args, **kwargs)

    monkeypatch.setenv("VOICE_STUDIO_LOCAL_PERSISTENT_WORKER", "0")
    monkeypatch.setattr(engine_runner.subprocess, "Popen", launch_test_process)

    result = engine_runner.run_isolated(
        "omnivoice",
        {"text": "大输出回归"},
        timeout=3,
    )

    assert result["output_path"] == "/tmp/generated.wav"


def test_isolated_runner_remains_cancellable_while_draining_output(monkeypatch):
    real_popen = subprocess.Popen
    script = """
import sys
import time

sys.stdin.read()
sys.stderr.write("worker-started\\n")
sys.stderr.flush()
time.sleep(30)
"""

    def launch_test_process(_args, *args, **kwargs):
        return real_popen([sys.executable, "-c", script], *args, **kwargs)

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    monkeypatch.setenv("VOICE_STUDIO_LOCAL_PERSISTENT_WORKER", "0")
    monkeypatch.setattr(engine_runner.subprocess, "Popen", launch_test_process)

    with pytest.raises(RuntimeError, match="Generation cancelled"):
        engine_runner.run_isolated(
            "omnivoice",
            {"text": "取消回归"},
            timeout=5,
            cancel_check=cancelled,
        )
