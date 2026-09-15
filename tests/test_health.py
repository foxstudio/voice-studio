from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import main as app_main  # noqa: E402


@pytest.mark.parametrize(
    ("available", "expected_optional_ready"),
    [
        ({"mlx_audio", "audio_separator"}, True),
        ({"mlx_audio"}, False),
    ],
)
def test_health_separates_core_readiness_from_optional_capabilities(
    monkeypatch,
    available,
    expected_optional_ready,
):
    snapshot = app_main.runtime_capabilities.RuntimeCapabilitySnapshot(
        schema_version=1,
        operating_system="macos",
        architecture="arm64",
        python_version="3.12.0",
        available_devices=("mps", "cpu"),
        preferred_device="mps",
        frameworks={"mlx": True, "torch": True},
        framework_devices={"mlx": ("mps", "cpu"), "torch": ("mps", "cpu")},
        optional_capabilities={
            "mlx_audio": "mlx_audio" in available,
            "audio_separator": "audio_separator" in available,
        },
    )
    monkeypatch.setattr(app_main.runtime_capabilities, "current_snapshot", lambda: snapshot)
    private_data_dir = "/Users/example/VoiceStudio"
    monkeypatch.setattr(
        app_main.settings_store,
        "get",
        lambda: SimpleNamespace(data_dir=private_data_dir),
    )

    response = TestClient(app_main.app).get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_ready"] is True
    assert body["data_dir"] == "<redacted>"
    assert private_data_dir not in response.text
    assert body["optional_runtime_ready"] is expected_optional_ready
    assert body["runtime_capabilities"] == {
        "mlx_audio": "mlx_audio" in available,
        "audio_separator": "audio_separator" in available,
    }
    assert body["platform_capabilities"]["operating_system"] == "macos"
    assert body["platform_capabilities"]["available_devices"] == ["mps", "cpu"]
    assert body["platform_capabilities"]["framework_devices"]["torch"] == ["mps", "cpu"]
    assert set(body["startup_maintenance"]) == {
        "state",
        "started_at",
        "finished_at",
        "error_count",
    }
    assert not any("path" in key for key in body["startup_maintenance"])
