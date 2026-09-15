from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import runtime_capabilities  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_runtime_capability_cache():
    runtime_capabilities.clear_cached_snapshot()
    try:
        yield
    finally:
        runtime_capabilities.clear_cached_snapshot()


def _configure_detection(
    monkeypatch,
    *,
    system: str,
    machine: str,
    modules: set[str],
    torch_devices: set[str],
    mlx_devices: set[str],
    tools: set[str],
) -> None:
    monkeypatch.setattr(runtime_capabilities.platform, "system", lambda: system)
    monkeypatch.setattr(runtime_capabilities.platform, "machine", lambda: machine)
    monkeypatch.setattr(runtime_capabilities.platform, "python_version", lambda: "3.12.0")
    monkeypatch.setattr(
        runtime_capabilities,
        "_module_available",
        lambda name: name in modules,
    )
    monkeypatch.setattr(runtime_capabilities, "_torch_devices", lambda: torch_devices)
    monkeypatch.setattr(runtime_capabilities, "_mlx_devices", lambda: mlx_devices)
    monkeypatch.setattr(
        runtime_capabilities.shutil,
        "which",
        lambda name: f"/tools/{name}" if name in tools else None,
    )
    runtime_capabilities.clear_cached_snapshot()


def test_macos_snapshot_prefers_mps_and_keeps_optional_status_separate(monkeypatch):
    _configure_detection(
        monkeypatch,
        system="Darwin",
        machine="arm64",
        modules={"mlx", "torch", "mlx_audio"},
        torch_devices={"mps"},
        mlx_devices={"mps"},
        tools={"ffmpeg", "ffprobe"},
    )

    snapshot = runtime_capabilities.current_snapshot()

    assert snapshot.operating_system == "macos"
    assert snapshot.architecture == "arm64"
    assert snapshot.available_devices == ("mps", "cpu")
    assert snapshot.preferred_device == "mps"
    assert snapshot.framework_devices == {
        "torch": ("mps", "cpu"),
        "mlx": ("mps", "cpu"),
    }
    assert snapshot.core_ready is True
    assert snapshot.optional_runtime_ready is False
    assert snapshot.optional_capabilities["audio_separator"] is False


def test_windows_snapshot_prefers_cuda_when_torch_reports_it(monkeypatch):
    _configure_detection(
        monkeypatch,
        system="Windows",
        machine="AMD64",
        modules={"torch", "audio_separator"},
        torch_devices={"cuda"},
        mlx_devices=set(),
        tools={"ffmpeg", "ffprobe"},
    )

    snapshot = runtime_capabilities.current_snapshot()

    assert snapshot.operating_system == "windows"
    assert snapshot.architecture == "x86_64"
    assert snapshot.available_devices == ("cuda", "cpu")
    assert snapshot.preferred_device == "cuda"
    assert snapshot.frameworks == {"mlx": False, "torch": True}
    assert snapshot.framework_devices == {"torch": ("cuda", "cpu"), "mlx": ()}


def test_windows_snapshot_falls_back_to_cpu_without_accelerator(monkeypatch):
    _configure_detection(
        monkeypatch,
        system="Windows",
        machine="AMD64",
        modules={"torch"},
        torch_devices=set(),
        mlx_devices=set(),
        tools=set(),
    )

    snapshot = runtime_capabilities.current_snapshot()

    assert snapshot.available_devices == ("cpu",)
    assert snapshot.preferred_device == "cpu"
    assert snapshot.core_ready is True
    assert snapshot.optional_runtime_ready is False


def test_snapshot_serializes_as_a_versioned_public_contract(monkeypatch):
    _configure_detection(
        monkeypatch,
        system="Linux",
        machine="aarch64",
        modules={"mlx"},
        torch_devices=set(),
        mlx_devices={"cuda"},
        tools={"ffmpeg"},
    )

    payload = runtime_capabilities.current_snapshot().to_dict()

    assert payload["schema_version"] == 1
    assert payload["operating_system"] == "linux"
    assert payload["architecture"] == "arm64"
    assert payload["available_devices"] == ["cuda", "cpu"]
    assert payload["framework_devices"] == {"torch": [], "mlx": ["cuda", "cpu"]}
    assert payload["optional_capabilities"]["ffprobe"] is False
