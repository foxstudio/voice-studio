from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.util import find_spec
from typing import Any


@dataclass(frozen=True)
class RuntimeCapabilitySnapshot:
    schema_version: int
    operating_system: str
    architecture: str
    python_version: str
    available_devices: tuple[str, ...]
    preferred_device: str
    frameworks: dict[str, bool]
    optional_capabilities: dict[str, bool]
    framework_devices: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def core_ready(self) -> bool:
        return True

    @property
    def optional_runtime_ready(self) -> bool:
        return all(self.optional_capabilities.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operating_system": self.operating_system,
            "architecture": self.architecture,
            "python_version": self.python_version,
            "available_devices": list(self.available_devices),
            "preferred_device": self.preferred_device,
            "frameworks": dict(self.frameworks),
            "framework_devices": {
                name: list(devices)
                for name, devices in self.framework_devices.items()
            },
            "optional_capabilities": dict(self.optional_capabilities),
        }


def _normalized_operating_system(value: str) -> str:
    return {
        "darwin": "macos",
        "windows": "windows",
        "linux": "linux",
    }.get(value.strip().lower(), value.strip().lower() or "unknown")


def _normalized_architecture(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"arm64", "aarch64"}:
        return "arm64"
    if normalized in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    return normalized or "unknown"


def _module_available(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _torch_devices() -> set[str]:
    if not _module_available("torch"):
        return set()
    try:
        import torch

        devices: set[str] = set()
        if torch.cuda.is_available():
            devices.add("cuda")
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            devices.add("mps")
        return devices
    except Exception:
        return set()


def _mlx_devices() -> set[str]:
    if not _module_available("mlx"):
        return set()
    try:
        import mlx.core as mx

        devices: set[str] = set()
        metal = getattr(mx, "metal", None)
        if metal is not None and metal.is_available():
            devices.add("mps")
        cuda = getattr(mx, "cuda", None)
        if cuda is not None and cuda.is_available():
            devices.add("cuda")
        return devices
    except Exception:
        return set()


@lru_cache(maxsize=1)
def current_snapshot() -> RuntimeCapabilitySnapshot:
    frameworks = {
        "mlx": _module_available("mlx"),
        "torch": _module_available("torch"),
    }
    torch_devices = _torch_devices()
    mlx_devices = _mlx_devices()
    if frameworks["torch"]:
        torch_devices.add("cpu")
    if frameworks["mlx"]:
        mlx_devices.add("cpu")
    devices = torch_devices | mlx_devices | {"cpu"}
    ordered_devices = tuple(
        device for device in ("cuda", "mps", "cpu") if device in devices
    )
    preferred_device = ordered_devices[0]
    return RuntimeCapabilitySnapshot(
        schema_version=1,
        operating_system=_normalized_operating_system(platform.system()),
        architecture=_normalized_architecture(platform.machine()),
        python_version=platform.python_version(),
        available_devices=ordered_devices,
        preferred_device=preferred_device,
        frameworks=frameworks,
        framework_devices={
            "torch": tuple(
                device for device in ("cuda", "mps", "cpu")
                if device in torch_devices
            ),
            "mlx": tuple(
                device for device in ("cuda", "mps", "cpu")
                if device in mlx_devices
            ),
        },
        optional_capabilities={
            "mlx_audio": _module_available("mlx_audio"),
            "audio_separator": _module_available("audio_separator"),
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "ffprobe": shutil.which("ffprobe") is not None,
        },
    )


def clear_cached_snapshot() -> None:
    current_snapshot.cache_clear()
