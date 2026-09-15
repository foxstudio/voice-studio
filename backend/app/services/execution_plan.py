from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services import engine_compatibility, engine_policy, runtime_capabilities


DEVICE_CONTROLLED_ENGINES = {"indextts-v2", "omnivoice", "f5-tts"}


class ExecutionPlanError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


@dataclass(frozen=True)
class ExecutionPlan:
    schema_version: int
    engine_id: str
    operating_system: str
    architecture: str
    runtime_family: str
    requested_device: str
    device: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "engine_id": self.engine_id,
            "operating_system": self.operating_system,
            "architecture": self.architecture,
            "runtime_family": self.runtime_family,
            "requested_device": self.requested_device,
            "device": self.device,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExecutionPlan:
        if payload.get("schema_version") != 1:
            raise ExecutionPlanError("execution_plan_version_unsupported", "任务使用了当前版本无法识别的设备执行计划。")
        return cls(
            schema_version=1,
            engine_id=str(payload["engine_id"]),
            operating_system=str(payload["operating_system"]),
            architecture=str(payload["architecture"]),
            runtime_family=str(payload["runtime_family"]),
            requested_device=str(payload["requested_device"]),
            device=str(payload["device"]) if payload.get("device") is not None else None,
        )


def _runtime_family(engine_id: str) -> str:
    if engine_id == "indextts-v2":
        return "hybrid_mlx_torch"
    if engine_id == "omnivoice":
        return "pytorch"
    if engine_id == "f5-tts":
        return "pytorch_external"
    return "engine_managed"


def _torch_devices(
    snapshot: runtime_capabilities.RuntimeCapabilitySnapshot,
) -> tuple[str, ...]:
    recorded = snapshot.framework_devices.get("torch")
    if recorded is not None:
        return tuple(recorded)
    if not snapshot.frameworks.get("torch", False):
        return ()
    return tuple(
        device
        for device in ("cuda", "mps", "cpu")
        if device in snapshot.available_devices
    )


def resolve(
    engine_id: str,
    requested_device: str,
    snapshot: runtime_capabilities.RuntimeCapabilitySnapshot | None = None,
) -> ExecutionPlan:
    resolved = engine_policy.resolve_engine_id(engine_id)
    current = snapshot or runtime_capabilities.current_snapshot()
    requested = str(requested_device or "auto").strip().lower()
    if requested not in {"auto", "cuda", "mps", "cpu"}:
        raise ExecutionPlanError("device_unknown", f"无法识别计算设备：{requested_device}")

    compatibility = engine_compatibility.evaluate(resolved, current)
    if not compatibility.compatible:
        raise ExecutionPlanError(
            compatibility.reason_code or "engine_incompatible",
            compatibility.message,
        )

    device: str | None = None
    if resolved in DEVICE_CONTROLLED_ENGINES:
        available = _torch_devices(current)
        if not available:
            raise ExecutionPlanError(
                "runtime_missing",
                "当前引擎需要 PyTorch，但没有检测到可用的 PyTorch 计算设备。",
            )
        if requested == "auto":
            device = next(
                (candidate for candidate in ("cuda", "mps", "cpu") if candidate in available),
                None,
            )
        elif requested in available:
            device = requested
        else:
            readable = "、".join(available)
            raise ExecutionPlanError(
                "device_unavailable",
                f"你选择的计算设备 {requested} 当前不可用；本机可用于此引擎的设备为：{readable}。请改为自动选择或可用设备。",
            )

    return ExecutionPlan(
        schema_version=1,
        engine_id=resolved,
        operating_system=current.operating_system,
        architecture=current.architecture,
        runtime_family=_runtime_family(resolved),
        requested_device=requested,
        device=device,
    )
