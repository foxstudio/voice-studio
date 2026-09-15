from __future__ import annotations

from app.schemas.voice_studio import EngineCompatibility
from app.services import engine_policy, runtime_capabilities


DESKTOP_PLATFORMS = ("macos", "windows", "linux")
MLX_PLATFORMS = ("macos", "linux")
MLX_ENGINES = {
    "indextts-v2",
    "confucius4-mlx-int8",
    "qwen3-tts-mlx-0.6b",
    "qwen3-asr-mlx",
    "moss-transcribe-diarize-mlx",
    "vibevoice-asr-mlx-4bit",
    "vibevoice-asr-mlx-8bit",
}
PYTORCH_ENGINES = {"indextts-v2", "omnivoice", "f5-tts"}


def supported_platforms(engine_id: str) -> tuple[str, ...]:
    resolved = engine_policy.resolve_engine_id(engine_id)
    if resolved in MLX_ENGINES:
        return MLX_PLATFORMS
    return DESKTOP_PLATFORMS


def evaluate(
    engine_id: str,
    snapshot: runtime_capabilities.RuntimeCapabilitySnapshot | None = None,
) -> EngineCompatibility:
    resolved = engine_policy.resolve_engine_id(engine_id)
    current = snapshot or runtime_capabilities.current_snapshot()
    platforms = supported_platforms(resolved)
    base = {
        "operating_system": current.operating_system,
        "architecture": current.architecture,
        "supported_platforms": list(platforms),
        "available_devices": list(current.available_devices),
    }

    if current.operating_system not in platforms:
        message = (
            "此引擎使用 MLX，当前不支持 Windows 原生运行。"
            "请改用支持 Windows 的本地引擎或云端引擎。"
            if current.operating_system == "windows" and resolved in MLX_ENGINES
            else f"此引擎不支持当前系统 {current.operating_system}。"
        )
        return EngineCompatibility(
            status="unavailable",
            compatible=False,
            reason_code="platform_unsupported",
            message=message,
            **base,
        )

    if (
        resolved in MLX_ENGINES
        and current.operating_system == "macos"
        and current.architecture != "arm64"
    ):
        return EngineCompatibility(
            status="unavailable",
            compatible=False,
            reason_code="architecture_unsupported",
            message="此 MLX 引擎需要 Apple Silicon；当前 Mac 架构不受支持。",
            **base,
        )

    if resolved in MLX_ENGINES and not current.frameworks.get("mlx", False):
        return EngineCompatibility(
            status="unavailable",
            compatible=False,
            reason_code="runtime_missing",
            message="当前系统可以运行此引擎，但尚未安装匹配平台的 MLX 运行环境。",
            **base,
        )

    if resolved in PYTORCH_ENGINES and not current.frameworks.get("torch", False):
        return EngineCompatibility(
            status="unavailable",
            compatible=False,
            reason_code="runtime_missing",
            message="当前系统可以运行此引擎，但尚未安装匹配平台的 PyTorch 运行环境。",
            **base,
        )

    return EngineCompatibility(
        status="compatible",
        compatible=True,
        reason_code=None,
        message="当前系统兼容；模型和引擎运行环境仍需通过环境检查。",
        **base,
    )
