from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import engine_compatibility, runtime_capabilities  # noqa: E402


def _snapshot(
    *,
    operating_system: str,
    architecture: str = "x86_64",
    devices: tuple[str, ...] = ("cpu",),
    mlx: bool = False,
) -> runtime_capabilities.RuntimeCapabilitySnapshot:
    return runtime_capabilities.RuntimeCapabilitySnapshot(
        schema_version=1,
        operating_system=operating_system,
        architecture=architecture,
        python_version="3.12.0",
        available_devices=devices,
        preferred_device=devices[0],
        frameworks={"mlx": mlx, "torch": True},
        optional_capabilities={},
    )


def test_mlx_engine_is_unavailable_on_native_windows_with_actionable_message():
    result = engine_compatibility.evaluate(
        "indextts-v2",
        _snapshot(operating_system="windows", devices=("cuda", "cpu")),
    )

    assert result.compatible is False
    assert result.status == "unavailable"
    assert result.reason_code == "platform_unsupported"
    assert result.supported_platforms == ["macos", "linux"]
    assert "Windows" in result.message
    assert "本地引擎或云端引擎" in result.message


def test_windows_compatible_external_engine_remains_available_for_runtime_check():
    result = engine_compatibility.evaluate(
        "f5-tts",
        _snapshot(operating_system="windows", devices=("cuda", "cpu")),
    )

    assert result.compatible is True
    assert result.status == "compatible"
    assert "windows" in result.supported_platforms


def test_f5_requires_pytorch_even_when_the_platform_is_supported():
    snapshot = _snapshot(
        operating_system="windows",
        devices=("cpu",),
    )
    snapshot.frameworks["torch"] = False

    result = engine_compatibility.evaluate("f5-tts", snapshot)

    assert result.compatible is False
    assert result.reason_code == "runtime_missing"
    assert "PyTorch" in result.message


def test_omnivoice_uses_pytorch_and_is_not_blocked_as_an_mlx_engine_on_windows():
    result = engine_compatibility.evaluate(
        "omnivoice",
        _snapshot(operating_system="windows", devices=("cuda", "cpu")),
    )

    assert result.compatible is True
    assert "windows" in result.supported_platforms


def test_cloud_engine_is_platform_compatible_without_local_mlx():
    result = engine_compatibility.evaluate(
        "mimo-v2.5-tts-preset",
        _snapshot(operating_system="windows"),
    )

    assert result.compatible is True


def test_mlx_engine_requires_apple_silicon_on_macos():
    result = engine_compatibility.evaluate(
        "indextts-v2",
        _snapshot(operating_system="macos", architecture="x86_64", mlx=True),
    )

    assert result.compatible is False
    assert result.reason_code == "architecture_unsupported"


def test_mlx_engine_reports_missing_runtime_on_supported_platform():
    result = engine_compatibility.evaluate(
        "qwen3-asr-mlx",
        _snapshot(operating_system="linux", mlx=False),
    )

    assert result.compatible is False
    assert result.reason_code == "runtime_missing"


def test_indextts_requires_pytorch_for_reference_preprocessing():
    snapshot = _snapshot(
        operating_system="macos",
        architecture="arm64",
        devices=("mps", "cpu"),
        mlx=True,
    )
    snapshot.frameworks["torch"] = False

    result = engine_compatibility.evaluate("indextts-v2", snapshot)

    assert result.compatible is False
    assert result.reason_code == "runtime_missing"
    assert "PyTorch" in result.message


def test_mlx_engine_is_compatible_when_platform_and_framework_match():
    result = engine_compatibility.evaluate(
        "indextts-v2",
        _snapshot(
            operating_system="macos",
            architecture="arm64",
            devices=("mps", "cpu"),
            mlx=True,
        ),
    )

    assert result.compatible is True
    assert result.available_devices == ["mps", "cpu"]
