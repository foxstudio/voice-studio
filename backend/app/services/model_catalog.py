from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services import (
    confucius4_paths,
    cosyvoice_worker,
    engine_health,
    engine_runtime_paths,
    qwen3_tts_paths,
    settings_store,
    speaker_diarization_service,
    speaker_verification_service,
)


SOURCES: dict[str, dict[str, Any]] = {
    "indextts-v2": {
        "source_url": "https://github.com/index-tts/index-tts",
        "source_label": "IndexTTS 官方仓库",
        "install_kind": "download_and_convert",
        "license_note": "模型权重不随 Voice Studio 仓库分发；按官方许可自行下载并转换。",
        "download_sources": [
            {
                "provider": "modelscope",
                "label": "IndexTTS-2 ModelScope 国内模型",
                "url": "https://modelscope.cn/models/IndexTeam/IndexTTS-2",
                "region": "cn",
                "preferred": True,
                "compatibility_note": "官方发布者的国内模型页；仍需按本项目说明完成转换。",
            }
        ],
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
        "download_sources": [
            {
                "provider": "modelscope",
                "label": "CosyVoice-300M-SFT 国内模型",
                "url": "https://modelscope.cn/models/iic/CosyVoice-300M-SFT",
                "region": "cn",
                "preferred": True,
                "compatibility_note": "ModelScope 官方组织 iic 发布；下载到 pretrained_models/CosyVoice-300M-SFT 后可被 SFT 引擎识别。",
            }
        ],
    },
    "cosyvoice-zero-shot": {
        "source_url": "https://github.com/FunAudioLLM/CosyVoice",
        "source_label": "CosyVoice 官方仓库",
        "install_kind": "external_runtime",
        "license_note": "SFT 与 Zero-Shot 共用同一个 CosyVoice 运行时。",
        "download_sources": [
            {
                "provider": "modelscope",
                "label": "CosyVoice-300M 国内模型",
                "url": "https://modelscope.cn/models/iic/CosyVoice-300M",
                "region": "cn",
                "preferred": True,
                "compatibility_note": "ModelScope 官方组织 iic 发布；下载到 pretrained_models/CosyVoice-300M 后可被 Zero-Shot 引擎识别。",
            }
        ],
    },
    "qwen3-asr-mlx": {
        "source_url": "https://github.com/moona3k/mlx-qwen3-asr",
        "source_label": "Qwen3-ASR MLX 社区运行时",
        "install_kind": "python_package_and_model",
        "license_note": "Python 包与模型缓存分开管理。",
        "download_sources": [
            {
                "provider": "modelscope",
                "label": "Qwen3-ASR 1.7B 8-bit MLX 国内社区镜像",
                "url": "https://modelscope.cn/models/mlx-community/Qwen3-ASR-1.7B-8bit",
                "region": "cn",
                "preferred": True,
                "compatibility_note": "MLX Community 发布的 8-bit MLX 转换权重，与当前运行时格式兼容；并非 Qwen 官方发布者镜像。",
            }
        ],
    },
    "faster-whisper-turbo": {
        "source_url": "https://github.com/SYSTRAN/faster-whisper",
        "source_label": "faster-whisper 官方仓库",
        "install_kind": "python_package_and_cache",
        "license_note": "模型通常下载到共享缓存，不复制进代码仓库。",
    },
    "moss-transcribe-diarize-mlx": {
        "source_url": "https://github.com/OpenMOSS/MOSS-Transcribe-Diarize",
        "source_label": "OpenMOSS 官方仓库与社区 MLX 移植",
        "install_kind": "external_runtime",
        "license_note": "MOSS 仅作为说话人分离旁路，不替换主 ASR；运行时与模型独立安装。",
        "download_sources": [
            {
                "provider": "hf-mirror",
                "label": "Hugging Face 国内镜像",
                "url": "https://hf-mirror.com/vanch007/mlx-MOSS-Transcribe-Diarize-8bit",
                "region": "cn",
                "preferred": True,
                "compatibility_note": "优先尝试；本机 POC 主权重成功但元数据请求失败，必须校验完整性，不能静默视为成功。",
            },
            {
                "provider": "huggingface",
                "label": "MLX 8bit 社区模型官方页",
                "url": "https://huggingface.co/vanch007/mlx-MOSS-Transcribe-Diarize-8bit",
                "region": "global",
                "preferred": False,
                "compatibility_note": "仅在镜像失败后显式回退；可复用镜像已下载的大权重 blob。",
            },
        ],
    },
    "campplus-modelscope": {
        "source_url": "https://modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common",
        "source_label": "ModelScope iic CAM++",
        "install_kind": "external_runtime",
        "license_note": "仅用于核验 MOSS 匿名声纹簇是否应合并，不负责识别真实人物。",
        "download_sources": [
            {
                "provider": "modelscope",
                "label": "CAM++ 国内模型",
                "url": "https://modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common",
                "region": "cn",
                "preferred": True,
                "compatibility_note": "本机 POC 已验证，模型约 27 MB。",
            }
        ],
    },
}


def list_installations() -> list[dict[str, Any]]:
    return [_entry(engine_id, source) for engine_id, source in SOURCES.items()]


def _entry(engine_id: str, source: dict[str, Any]) -> dict[str, Any]:
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
    if engine_id == speaker_diarization_service.ENGINE_ID:
        health = speaker_diarization_service.health_check()
    elif engine_id == speaker_verification_service.ENGINE_ID:
        health = speaker_verification_service.health_check()
    else:
        health = engine_health.health_check(engine_id)
    return {
        "engine_id": engine_id,
        **source,
        "preferred_path": str(preferred) if preferred else None,
        "installed": health.get("healthy") is True,
        "installation_status": str(health.get("status") or "unknown"),
        "discovered_paths": discovered,
        "automatic_download_supported": False,
        "download_sources": list(source.get("download_sources") or []),
        "download_policy": "国内镜像优先；国际官方源仅作为手动备选，不静默切换。",
        "reuse_note": "已有文件可通过环境变量或软链接复用，不需要重复下载。",
    }


def _candidates(engine_id: str) -> list[Path]:
    if engine_id == speaker_diarization_service.ENGINE_ID:
        return [speaker_diarization_service.DEFAULT_RUNTIME_PYTHON.parent.parent, speaker_diarization_service.DEFAULT_MODEL_PATH]
    if engine_id == speaker_verification_service.ENGINE_ID:
        return [speaker_verification_service.DEFAULT_RUNTIME_ROOT, speaker_verification_service.DEFAULT_MODEL_PATH]
    if engine_id == "indextts-v2":
        return settings_store.model_candidates(engine_id)
    if engine_id == confucius4_paths.ENGINE_ID:
        return [*confucius4_paths.model_candidates(), confucius4_paths.runtime_root()]
    if engine_id == qwen3_tts_paths.ENGINE_ID:
        return engine_runtime_paths.engine_root_candidates(engine_id)
    if engine_id == "omnivoice":
        hub = Path.home() / ".cache" / "huggingface" / "hub" / "models--k2-fsa--OmniVoice" / "snapshots"
        return sorted((path for path in hub.glob("*") if path.is_dir()), reverse=True) or [hub]
    if engine_id in cosyvoice_worker.MODEL_DIRECTORY_NAMES:
        roots = engine_runtime_paths.engine_root_candidates(engine_id)
        try:
            preferred_root = engine_health.external_engine_root(engine_id)
        except RuntimeError:
            preferred_root = roots[0]
        ordered_roots = list(dict.fromkeys([preferred_root, *roots]))
        return [cosyvoice_worker.model_directory(root, engine_id) for root in ordered_roots]
    if engine_id in engine_runtime_paths.ENGINE_LAYOUT:
        return engine_runtime_paths.engine_root_candidates(engine_id)
    return settings_store.model_candidates(engine_id)
