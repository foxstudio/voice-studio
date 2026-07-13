from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services import confucius4_paths, engine_health, engine_runtime_paths, qwen3_tts_paths, settings_store


SOURCES: dict[str, dict[str, str]] = {
    "indextts-v2": {
        "source_url": "https://github.com/index-tts/index-tts",
        "source_label": "IndexTTS 官方仓库",
        "install_kind": "download_and_convert",
        "license_note": "模型权重不随 Voice Studio 仓库分发；按官方许可自行下载并转换。",
    },
    "omnivoice": {
        "source_url": "https://github.com/k2-fsa/OmniVoice",
        "source_label": "OmniVoice 官方仓库",
        "install_kind": "python_package_and_cache",
        "license_note": "运行包与模型缓存由依赖管理器和 Hugging Face 管理。",
    },
    "emotivoice": {
        "source_url": "https://github.com/netease-youdao/EmotiVoice",
        "source_label": "EmotiVoice 官方仓库",
        "install_kind": "external_runtime",
        "license_note": "外部运行时与模型保持独立，Voice Studio 只记录路径。",
    },
    "confucius4-mlx-int8": {
        "source_url": "https://huggingface.co/beyoru/Confucius4-TTS-mlx-int8",
        "source_label": "Confucius4 MLX 模型页",
        "install_kind": "model_and_runtime",
        "license_note": "模型权重与 mlx-audio 运行时分开安装。",
    },
    "qwen3-tts-mlx-0.6b": {
        "source_url": "https://github.com/kapi2800/qwen3-tts-apple-silicon",
        "source_label": "Qwen3-TTS Apple Silicon",
        "install_kind": "external_runtime",
        "license_note": "运行时和多个量化模型由外部项目管理。",
    },
    "f5-tts": {
        "source_url": "https://github.com/SWivid/F5-TTS",
        "source_label": "F5-TTS 官方仓库",
        "install_kind": "external_runtime",
        "license_note": "代码与模型许可需分别遵守，默认不自动下载。",
    },
    "cosyvoice-sft": {
        "source_url": "https://github.com/FunAudioLLM/CosyVoice",
        "source_label": "CosyVoice 官方仓库",
        "install_kind": "external_runtime",
        "license_note": "SFT 与 Zero-Shot 共用同一个 CosyVoice 运行时。",
    },
    "cosyvoice-zero-shot": {
        "source_url": "https://github.com/FunAudioLLM/CosyVoice",
        "source_label": "CosyVoice 官方仓库",
        "install_kind": "external_runtime",
        "license_note": "SFT 与 Zero-Shot 共用同一个 CosyVoice 运行时。",
    },
    "qwen3-asr-mlx": {
        "source_url": "https://github.com/moona3k/mlx-qwen3-asr",
        "source_label": "Qwen3-ASR MLX 官方仓库",
        "install_kind": "python_package_and_model",
        "license_note": "Python 包与模型缓存分开管理。",
    },
    "faster-whisper-turbo": {
        "source_url": "https://github.com/SYSTRAN/faster-whisper",
        "source_label": "faster-whisper 官方仓库",
        "install_kind": "python_package_and_cache",
        "license_note": "模型通常下载到共享缓存，不复制进代码仓库。",
    },
}


def list_installations() -> list[dict[str, Any]]:
    return [_entry(engine_id, source) for engine_id, source in SOURCES.items()]


def _entry(engine_id: str, source: dict[str, str]) -> dict[str, Any]:
    candidates = _candidates(engine_id)
    discovered = []
    for path in candidates:
        exists = path.exists()
        discovered.append(
            {
                "path": str(path),
                "exists": exists,
                "is_symlink": path.is_symlink(),
                "resolved_path": str(path.resolve()) if exists else None,
            }
        )
    preferred = candidates[0] if candidates else None
    health = engine_health.health_check(engine_id)
    return {
        "engine_id": engine_id,
        **source,
        "preferred_path": str(preferred) if preferred else None,
        "installed": health.get("healthy") is True,
        "installation_status": str(health.get("status") or "unknown"),
        "discovered_paths": discovered,
        "automatic_download_supported": False,
        "reuse_note": "已有文件可通过环境变量或软链接复用，不需要重复下载。",
    }


def _candidates(engine_id: str) -> list[Path]:
    if engine_id == "indextts-v2":
        return settings_store.model_candidates(engine_id)
    if engine_id == confucius4_paths.ENGINE_ID:
        return [*confucius4_paths.model_candidates(), confucius4_paths.runtime_root()]
    if engine_id == qwen3_tts_paths.ENGINE_ID:
        return engine_runtime_paths.engine_root_candidates(engine_id)
    if engine_id == "omnivoice":
        hub = Path.home() / ".cache" / "huggingface" / "hub" / "models--k2-fsa--OmniVoice" / "snapshots"
        return sorted((path for path in hub.glob("*") if path.is_dir()), reverse=True) or [hub]
    if engine_id in engine_runtime_paths.ENGINE_LAYOUT:
        return engine_runtime_paths.engine_root_candidates(engine_id)
    return settings_store.model_candidates(engine_id)
