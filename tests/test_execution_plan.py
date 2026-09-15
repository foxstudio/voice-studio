from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import execution_plan, runtime_capabilities  # noqa: E402


def _snapshot(
    *,
    operating_system: str,
    architecture: str = "x86_64",
    torch_devices: tuple[str, ...] = ("cpu",),
    mlx_devices: tuple[str, ...] = (),
) -> runtime_capabilities.RuntimeCapabilitySnapshot:
    available = tuple(
        device
        for device in ("cuda", "mps", "cpu")
        if device in {*torch_devices, *mlx_devices, "cpu"}
    )
    return runtime_capabilities.RuntimeCapabilitySnapshot(
        schema_version=1,
        operating_system=operating_system,
        architecture=architecture,
        python_version="3.12.0",
        available_devices=available,
        preferred_device=available[0],
        frameworks={"torch": bool(torch_devices), "mlx": bool(mlx_devices)},
        optional_capabilities={},
        framework_devices={"torch": torch_devices, "mlx": mlx_devices},
    )


def test_auto_selects_cuda_for_windows_omnivoice():
    plan = execution_plan.resolve(
        "omnivoice",
        "auto",
        _snapshot(operating_system="windows", torch_devices=("cuda", "cpu")),
    )

    assert plan.device == "cuda"
    assert plan.runtime_family == "pytorch"
    assert plan.to_dict()["schema_version"] == 1


def test_auto_selects_cuda_for_windows_f5_external_runtime():
    plan = execution_plan.resolve(
        "f5-tts",
        "auto",
        _snapshot(operating_system="windows", torch_devices=("cuda", "cpu")),
    )

    assert plan.device == "cuda"
    assert plan.runtime_family == "pytorch_external"


def test_auto_selects_torch_mps_for_hybrid_indextts_on_apple_silicon():
    plan = execution_plan.resolve(
        "indextts-v2",
        "auto",
        _snapshot(
            operating_system="macos",
            architecture="arm64",
            torch_devices=("mps", "cpu"),
            mlx_devices=("mps", "cpu"),
        ),
    )

    assert plan.device == "mps"
    assert plan.runtime_family == "hybrid_mlx_torch"


def test_explicit_unavailable_device_fails_instead_of_falling_back():
    with pytest.raises(execution_plan.ExecutionPlanError) as exc_info:
        execution_plan.resolve(
            "omnivoice",
            "mps",
            _snapshot(operating_system="windows", torch_devices=("cuda", "cpu")),
        )

    assert exc_info.value.reason_code == "device_unavailable"
    assert "自动选择" in exc_info.value.message


def test_native_windows_indextts_is_rejected_before_device_selection():
    with pytest.raises(execution_plan.ExecutionPlanError) as exc_info:
        execution_plan.resolve(
            "indextts-v2",
            "auto",
            _snapshot(
                operating_system="windows",
                torch_devices=("cuda", "cpu"),
                mlx_devices=("cuda", "cpu"),
            ),
        )

    assert exc_info.value.reason_code == "platform_unsupported"
    assert "Windows" in exc_info.value.message


def test_versioned_plan_round_trips_for_persisted_tasks():
    original = execution_plan.resolve(
        "omnivoice",
        "cpu",
        _snapshot(operating_system="linux", torch_devices=("cuda", "cpu")),
    )

    restored = execution_plan.ExecutionPlan.from_dict(original.to_dict())

    assert restored == original
