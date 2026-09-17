from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services import (
    confucius4_paths,
    cosyvoice_worker,
    engine_health,
    engine_runtime_paths,
    model_install_policy,
    omnivoice_model,
    qwen_forced_aligner,
    qwen_mlx_asr,
    qwen3_tts_paths,
    semantic_alignment_labse,
    ser_service,
    settings_store,
    speaker_diarization_service,
    speaker_verification_service,
    stem_separation_model,
    vibevoice_asr,
    vibevoice_model,
)
from app.services.paths import expand_path


SOURCES: dict[str, dict[str, Any]] = {
    stem_separation_model.ENGINE_ID: {
        "display_name": stem_separation_model.DISPLAY_NAME,
        "category": "media_tool",
        "source_url": stem_separation_model.MODEL_SOURCE_URL,
        "source_label": "UVR / TRvlvr 模型发布源",
        "runtime_url": stem_separation_model.RUNTIME_URL,
        "runtime_label": "python-audio-separator 官方仓库",
        "install_kind": "managed_model_download",
        "model_license_status": "unverified",
        "license_note": (
            "运行时代码为 MIT；模型不随仓库分发，安装时从 UVR/TRvlvr "
            "发布源下载。模型权重缺少独立许可文件，正式开源前需再次核验。"
        ),
        "model_filename": stem_separation_model.MODEL_FILENAME,
        "model_sha256": stem_separation_model.MODEL_SHA256,
        "architecture": "BS-RoFormer (MDXC)",
        "recommended_for": "YouTube 知识、教程及含作品片段的视频",
        "download_sources": [
            {
                "provider": "github",
                "label": "UVR/TRvlvr 官方发布文件",
                "url": stem_separation_model.MODEL_URL,
                "region": "global",
                "preferred": True,
                "compatibility_note": (
                    "固定使用已试听验收的 Viperx-1297 权重，安装后执行 "
                    "SHA-256 完整性校验。"
                ),
            }
        ],
    },
    "indextts-v2": {
        "source_url": "https://github.com/index-tts/index-tts",
        "source_label": "IndexTTS 官方仓库",
        "install_kind": "download_and_convert",
        "model_revision": "740dcaff396282ffb241903d150ac011cd4b1ede",
        "license_note": "模型权重不随 Voice Studio 仓库分发；按官方许可自行下载并转换。",
        "download_sources": [
            {
                "provider": "modelscope",
                "label": "IndexTTS-2 ModelScope 国内模型",
                "url": "https://modelscope.cn/models/IndexTeam/IndexTTS-2",
                "region": "cn",
                "preferred": True,
                "compatibility_note": "官方发布者的国内模型页；仍需按本项目说明完成转换。",
            },
            {
                "provider": "huggingface",
                "label": "IndexTTS-2 官方 Hugging Face 模型",
                "url": "https://huggingface.co/IndexTeam/IndexTTS-2",
                "region": "global",
                "preferred": False,
                "compatibility_note": "官方原始权重；当前 MLX 引擎需要完成一次转换。",
            },
            {
                "provider": "huggingface",
                "label": "配套：MaskGCT 参考音频编码器",
                "url": "https://huggingface.co/amphion/MaskGCT",
                "region": "global",
                "preferred": False,
                "compatibility_note": "WAV 声音克隆需要；权重为 CC-BY-NC-4.0，非商业许可。",
            },
            {
                "provider": "huggingface",
                "label": "配套：W2V-BERT 2.0",
                "url": "https://huggingface.co/facebook/w2v-bert-2.0",
                "region": "global",
                "preferred": False,
                "compatibility_note": "WAV 声音克隆需要；固定版本离线读取。",
            },
            {
                "provider": "huggingface",
                "label": "配套：CAM++ 说话人编码器",
                "url": "https://huggingface.co/funasr/campplus",
                "region": "global",
                "preferred": False,
                "compatibility_note": "WAV 声音克隆需要；与声纹复核模型用途不同。",
            },
        ],
    },
    "omnivoice": {
        "source_url": "https://huggingface.co/k2-fsa/OmniVoice",
        "source_label": "OmniVoice 官方模型页",
        "runtime_url": "https://github.com/k2-fsa/OmniVoice",
        "runtime_label": "OmniVoice 官方代码仓库",
        "install_kind": "managed_model_snapshot",
        "license_note": (
            "代码采用 Apache-2.0；官方说明预训练权重因训练数据限制采用 "
            "CC-BY-NC，仅限非商业用途。下载前必须明确确认。"
        ),
        "code_license": omnivoice_model.CODE_LICENSE,
        "model_license": omnivoice_model.MODEL_LICENSE,
        "model_license_status": "verified",
        "license_acceptance_required": True,
        "license_acceptance_id": omnivoice_model.LICENSE_ACCEPTANCE_ID,
        "model_revision": omnivoice_model.REVISION,
        "download_sources": [
            {
                "provider": "huggingface",
                "label": "OmniVoice 官方模型快照",
                "url": "https://huggingface.co/k2-fsa/OmniVoice",
                "region": "global",
                "preferred": True,
                "compatibility_note": (
                    "固定到已校验版本；约 3.27 GB。模型权重仅限非商业用途。"
                ),
            }
        ],
    },
    "emotivoice": {
        "source_url": "https://github.com/netease-youdao/EmotiVoice",
        "source_label": "EmotiVoice 官方仓库",
        "install_kind": "external_runtime",
        "license_note": "外部运行时与模型保持独立，Voice Studio 只记录路径。",
        "download_sources": [
            {
                "provider": "modelscope",
                "label": "EmotiVoice 官方 ModelScope 权重",
                "url": "https://modelscope.cn/models/syq163/outputs",
                "region": "cn",
                "preferred": True,
                "compatibility_note": "上游 README 指定的预训练权重目录；仍需按 EmotiVoice 运行时结构放置。",
            }
        ],
    },
    "confucius4-mlx-int8": {
        "source_url": "https://huggingface.co/mlx-community/Confucius4-TTS-mlx-int8",
        "source_label": "Confucius4 MLX 模型页",
        "install_kind": "model_and_runtime",
        "license_note": "模型权重与 mlx-audio 运行时分开安装。",
        "download_sources": [
            {
                "provider": "huggingface",
                "label": "MLX Community int8 权重",
                "url": "https://huggingface.co/mlx-community/Confucius4-TTS-mlx-int8",
                "region": "global",
                "preferred": True,
                "compatibility_note": "当前模型卡标记为约 2.8 GB 的 Apache-2.0 int8 MLX 版本。",
            }
        ],
    },
    "qwen3-tts-mlx-0.6b": {
        "source_url": "https://github.com/kapi2800/qwen3-tts-apple-silicon",
        "source_label": "Qwen3-TTS Apple Silicon",
        "install_kind": "external_runtime",
        "license_note": "运行时和多个量化模型由外部项目管理。",
        "download_sources": [
            {
                "provider": "huggingface",
                "label": "0.6B CustomVoice 8bit",
                "url": "https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
                "region": "global",
                "preferred": True,
                "compatibility_note": "对应当前预置音色模型目录。",
            },
            {
                "provider": "huggingface",
                "label": "0.6B Base 8bit",
                "url": "https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
                "region": "global",
                "preferred": False,
                "compatibility_note": "对应当前参考音频克隆模型目录。",
            },
        ],
    },
    "f5-tts": {
        "source_url": "https://github.com/SWivid/F5-TTS",
        "source_label": "F5-TTS 官方仓库",
        "install_kind": "external_runtime",
        "license_note": "代码与模型许可需分别遵守，默认不自动下载。",
        "download_sources": [
            {
                "provider": "huggingface",
                "label": "F5-TTS v1 官方权重",
                "url": "https://huggingface.co/SWivid/F5-TTS",
                "region": "global",
                "preferred": True,
                "compatibility_note": "当前运行时使用 F5TTS_v1_Base；预训练权重为 CC-BY-NC。",
            }
        ],
    },
    "cosyvoice-sft": {
        "source_url": "https://github.com/QwenAudio/CosyVoice",
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
        "source_url": "https://github.com/QwenAudio/CosyVoice",
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
        "model_license": "Apache-2.0",
        "model_license_status": "verified",
        "model_revision": qwen_mlx_asr.MODEL_REVISION,
        "model_sha256": qwen_mlx_asr.MODEL_WEIGHTS_SHA256,
        "license_note": "Python 运行时与 MLX 模型分开管理；模型固定版本后离线使用。",
        "download_sources": [
            {
                "provider": "modelscope",
                "label": "Qwen3-ASR 1.7B 8-bit MLX 国内社区镜像",
                "url": "https://modelscope.cn/models/mlx-community/Qwen3-ASR-1.7B-8bit",
                "region": "cn",
                "preferred": True,
                "compatibility_note": "MLX Community 发布的 8-bit MLX 转换权重，与当前运行时格式兼容；并非 Qwen 官方发布者镜像。",
            },
            {
                "provider": "huggingface",
                "label": "Qwen3-ASR 1.7B 8-bit MLX 国际模型页",
                "url": "https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-8bit",
                "region": "global",
                "preferred": False,
                "compatibility_note": "与当前 mlx-audio 运行时兼容的固定 MLX 转换版本。",
            },
        ],
    },
    "qwen3-forced-aligner": {
        "display_name": "Qwen3 Forced Aligner",
        "runtime_engine_id": None,
        "category": "localization_model",
        "source_url": qwen_forced_aligner.HUGGING_FACE_MODEL_URL,
        "source_label": "Qwen 官方模型页",
        "install_kind": "python_package_and_model",
        "model_license": "Apache-2.0",
        "model_license_status": "verified",
        "model_revision": qwen_forced_aligner.MODEL_REVISION,
        "model_sha256": qwen_forced_aligner.MODEL_WEIGHTS_SHA256,
        "architecture": "Qwen3 Forced Aligner 0.6B",
        "recommended_for": "字幕逐词时间与参考音频对齐",
        "license_note": "Qwen 官方 Apache-2.0 模型；运行时保持离线，不在对齐时自动下载。",
        "download_sources": qwen_forced_aligner.download_sources(),
    },
    "semantic-alignment-labse": {
        "display_name": "LaBSE 语义对齐",
        "runtime_engine_id": None,
        "category": "localization_model",
        "source_url": "https://huggingface.co/sentence-transformers/LaBSE",
        "source_label": "Sentence Transformers 官方模型页",
        "install_kind": "managed_model_snapshot_manual",
        "model_license": "Apache-2.0",
        "model_license_status": "verified",
        "architecture": "LaBSE multilingual sentence encoder",
        "recommended_for": "本土化中英文字幕语义映射",
        "license_note": "Apache-2.0；固定到本地目录后仅离线读取。",
        "download_sources": [
            {
                "provider": "modelscope",
                "label": "LaBSE ModelScope 国内镜像",
                "url": "https://modelscope.cn/models/sentence-transformers/LaBSE",
                "region": "cn",
                "preferred": True,
                "compatibility_note": "国内下载页；下载后放入统一模型目录。",
            },
            {
                "provider": "huggingface",
                "label": "LaBSE 官方 Hugging Face 模型",
                "url": "https://huggingface.co/sentence-transformers/LaBSE",
                "region": "global",
                "preferred": False,
                "compatibility_note": "官方模型页；需保留 2_Dense 子目录。",
            },
        ],
    },
    "vibevoice-asr-4bit": {
        "display_name": "VibeVoice ASR 4bit（推荐）", "family_id": vibevoice_model.MODEL_FAMILY_ID, "variant_id": "4bit", "category": "asr_model", "runtime_engine_id": "vibevoice-asr-mlx-4bit",
        "source_url": "https://huggingface.co/mlx-community/VibeVoice-ASR-4bit", "source_label": "MLX Community 模型页",
        "runtime_url": "https://github.com/Blaizzy/mlx-audio", "runtime_label": "mlx-audio", "install_kind": "managed_model_download",
        "model_license": "MIT", "model_license_status": "verified", "model_sha256": "per-file-sha256-manifest",
        "license_note": "微软 VibeVoice-ASR 的社区 MLX 4bit 转换；代码和模型许可为 MIT。", "architecture": "VibeVoice ASR 4bit MLX",
        "recommended_for": vibevoice_model.VARIANTS["4bit"].recommended_for,
        "download_sources": [{"provider": "modelscope", "label": "ModelScope 国内直连", "url": "https://modelscope.cn/models/mlx-community/VibeVoice-ASR-4bit", "region": "cn", "preferred": True, "compatibility_note": "固定国内站直连，不使用系统代理；逐文件校验大小和 SHA-256。"}],
    },
    "vibevoice-asr-8bit": {
        "display_name": "VibeVoice ASR 8bit", "family_id": vibevoice_model.MODEL_FAMILY_ID, "variant_id": "8bit", "category": "asr_model", "runtime_engine_id": "vibevoice-asr-mlx-8bit",
        "source_url": "https://huggingface.co/mlx-community/VibeVoice-ASR-8bit", "source_label": "MLX Community 模型页",
        "runtime_url": "https://github.com/Blaizzy/mlx-audio", "runtime_label": "mlx-audio", "install_kind": "managed_model_download",
        "model_license": "MIT", "model_license_status": "verified", "model_sha256": "per-file-sha256-manifest",
        "license_note": "微软 VibeVoice-ASR 的社区 MLX 8bit 转换；代码和模型许可为 MIT。", "architecture": "VibeVoice ASR 8bit MLX",
        "recommended_for": vibevoice_model.VARIANTS["8bit"].recommended_for,
        "download_sources": [{"provider": "modelscope", "label": "ModelScope 国内直连", "url": "https://modelscope.cn/models/mlx-community/VibeVoice-ASR-8bit", "region": "cn", "preferred": True, "compatibility_note": "固定国内站直连，不使用系统代理；逐文件校验大小和 SHA-256。"}],
    },
    "vibevoice-asr-official": {
        "display_name": "VibeVoice ASR 官方完整版", "family_id": vibevoice_model.MODEL_FAMILY_ID, "variant_id": "official", "category": "asr_model", "runtime_engine_id": None, "reference_only": True,
        "source_url": "https://huggingface.co/microsoft/VibeVoice-ASR", "source_label": "Microsoft 官方模型页",
        "runtime_url": "https://github.com/microsoft/VibeVoice", "runtime_label": "Microsoft VibeVoice 官方运行时", "install_kind": "managed_model_download",
        "model_license": "MIT", "model_license_status": "verified", "model_sha256": "per-file-sha256-manifest",
        "license_note": "Microsoft Research 官方模型；代码和模型许可为 MIT。", "architecture": "VibeVoice ASR BF16 PyTorch",
        "recommended_for": vibevoice_model.VARIANTS["official"].recommended_for,
        "download_sources": [{"provider": "modelscope", "label": "Microsoft ModelScope 国内镜像", "url": "https://modelscope.cn/models/microsoft/VibeVoice-ASR", "region": "cn", "preferred": True, "compatibility_note": "固定国内站直连，不使用系统代理；逐文件校验大小和 SHA-256。"}],
    },
    "faster-whisper-turbo": {
        "source_url": "https://github.com/SYSTRAN/faster-whisper",
        "source_label": "faster-whisper 官方仓库",
        "install_kind": "python_package_and_cache",
        "license_note": "模型通常下载到共享缓存，不复制进代码仓库。",
        "download_sources": [
            {
                "provider": "huggingface",
                "label": "faster-whisper large-v3-turbo CTranslate2 权重",
                "url": "https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo",
                "region": "global",
                "preferred": True,
                "compatibility_note": "对应当前 Faster Whisper Turbo 运行时。",
            }
        ],
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
    ser_service.MODEL_ID: {
        "display_name": "emotion2vec+ 情绪识别",
        "source_url": "https://modelscope.cn/models/iic/emotion2vec_plus_large",
        "source_label": "ModelScope iic emotion2vec+ large",
        "install_kind": "external_runtime",
        "license_note": "用于给音色参考音频打情绪标签，供语音合成挑选情绪参考；模型约 1.8 GB。",
        "download_sources": [
            {
                "provider": "modelscope",
                "label": "emotion2vec+ 国内模型",
                "url": "https://modelscope.cn/models/iic/emotion2vec_plus_large",
                "region": "cn",
                "preferred": True,
                "compatibility_note": "复用 CAM++ 引擎运行时里的 funasr，无需另装依赖。",
            }
        ],
    },
}


def list_installations() -> list[dict[str, Any]]:
    return [_entry(engine_id, source) for engine_id, source in SOURCES.items()]


def automatic_download_allowed(engine_id: str) -> bool:
    return model_install_policy.automatic_download_decision(
        SOURCES.get(engine_id)
    ).allowed


def automatic_download_blockers(engine_id: str) -> list[str]:
    return list(
        model_install_policy.automatic_download_decision(
            SOURCES.get(engine_id)
        ).blockers
    )


def _entry(engine_id: str, source: dict[str, Any]) -> dict[str, Any]:
    if engine_id in vibevoice_model.INSTALLATION_TO_VARIANT:
        variant_id = vibevoice_model.variant_for_installation(engine_id)
        managed = vibevoice_model.installation_status(
            variant_id,
            verify_integrity=False,
        )
        provider_id = f"vibevoice-asr-mlx-{variant_id}"
        health = vibevoice_asr.model_health(
            provider_id,
            verify_integrity=False,
        ) if variant_id in {"4bit", "8bit"} else {"healthy": False, "status": "reference_only", "detail": "官方完整版保留为质量基准，不参与 Mac 自动选择。"}
        preferred = Path(str(managed["preferred_path"]))
        return {
            "engine_id": engine_id, **source, **managed,
            "runtime_ready": health.get("healthy") is True, "runtime_status": str(health.get("status") or "unknown"), "runtime_detail": health.get("detail"),
            "discovered_paths": [{"path": str(preferred), "exists": preferred.exists(), "has_payload": preferred.exists() and _has_local_payload(preferred), "is_symlink": preferred.is_symlink(), "resolved_path": str(preferred.resolve()) if preferred.exists() else None}],
            "automatic_download_supported": automatic_download_allowed(engine_id), "automatic_download_blockers": automatic_download_blockers(engine_id),
            "download_sources": list(source.get("download_sources") or []),
            "download_policy": "只走 ModelScope 国内直连并绕过系统代理；不静默切换国际源。",
            "reuse_note": "三个版本位于同一模型家族目录，并共享一份 tokenizer；完整文件不会重复下载。",
        }
    if engine_id == stem_separation_model.ENGINE_ID:
        managed = stem_separation_model.installation_status(
            verify_integrity=False,
        )
        return {
            "engine_id": engine_id,
            **source,
            **managed,
            "discovered_paths": [
                {
                    "path": managed["preferred_path"],
                    "exists": Path(managed["preferred_path"]).exists(),
                    "is_symlink": Path(managed["preferred_path"]).is_symlink(),
                    "resolved_path": (
                        str(Path(managed["preferred_path"]).resolve())
                        if Path(managed["preferred_path"]).exists()
                        else None
                    ),
                }
            ],
            "automatic_download_supported": automatic_download_allowed(engine_id),
            "automatic_download_blockers": automatic_download_blockers(engine_id),
            "download_sources": list(source.get("download_sources") or []),
            "download_policy": "只从列出的固定 HTTPS 来源下载，并校验文件指纹。",
            "reuse_note": "模型安装一次后由所有视频本土化项目共用，不会重复下载。",
        }
    candidates = _candidates(engine_id)
    discovered = []
    for path in candidates:
        exists = path.exists()
        has_payload = _has_local_payload(path) if exists else False
        discovered.append(
            {
                "path": str(path),
                "exists": exists,
                "has_payload": has_payload,
                "is_symlink": path.is_symlink(),
                "resolved_path": str(path.resolve()) if exists else None,
            }
        )
    preferred = candidates[0] if candidates else None
    if engine_id == "qwen3-forced-aligner":
        health = qwen_forced_aligner.health_check()
    elif engine_id == "semantic-alignment-labse":
        health = semantic_alignment_labse.health_check()
    elif engine_id == speaker_diarization_service.ENGINE_ID:
        health = speaker_diarization_service.health_check()
    elif engine_id == speaker_verification_service.ENGINE_ID:
        health = speaker_verification_service.health_check()
    elif engine_id == ser_service.MODEL_ID:
        health = ser_service.health_check()
    else:
        health = engine_health.health_check(engine_id)
    runtime_ready = health.get("product_ready", health.get("healthy")) is True
    runtime_status = str(health.get("status") or "unknown")
    reported_model_path = health.get("model_path")
    if engine_id == omnivoice_model.ENGINE_ID:
        complete_candidates = [
            Path(item["path"])
            for item in discovered
            if item["exists"]
            and omnivoice_model.is_complete_directory(
                Path(item["path"]),
                verify_integrity=False,
            )
        ]
        files_present = bool(complete_candidates)
        if complete_candidates:
            preferred = complete_candidates[0]
        managed_status = omnivoice_model.installation_status(
            verify_integrity=False,
        )
    else:
        payload_candidates = [
            Path(item["path"])
            for item in discovered
            if item["has_payload"]
        ]
        if reported_model_path:
            preferred = Path(str(reported_model_path))
        elif payload_candidates:
            preferred = payload_candidates[0]
        files_present = (
            runtime_ready
            or (runtime_status != "model_missing" and bool(payload_candidates))
        )
        managed_status = None
    result = {
        "engine_id": engine_id,
        "runtime_engine_id": source.get("runtime_engine_id", engine_id),
        **source,
        "preferred_path": str(preferred) if preferred else None,
        "installed": files_present,
        "runtime_ready": runtime_ready,
        "runtime_status": runtime_status,
        "installation_status": (
            "ready"
            if runtime_ready
            else "files_present_runtime_unavailable"
            if files_present
            else runtime_status
        ),
        "runtime_detail": health.get("detail") or health.get("missing"),
        "discovered_paths": discovered,
        "automatic_download_supported": automatic_download_allowed(engine_id),
        "automatic_download_blockers": automatic_download_blockers(engine_id),
        "download_sources": list(source.get("download_sources") or []),
        "download_policy": "国内镜像优先；国际官方源仅作为手动备选，不静默切换。",
        "reuse_note": "已有文件可通过环境变量或软链接复用，不需要重复下载。",
    }
    if managed_status is not None:
        if managed_status["installation_status"] in {"installing", "failed"}:
            result["installation_status"] = managed_status["installation_status"]
        result.update(
            integrity="verified" if files_present else managed_status["integrity"],
            progress=1.0 if files_present else managed_status["progress"],
            downloaded_bytes=managed_status["downloaded_bytes"],
            total_bytes=managed_status["total_bytes"],
            size_bytes=(
                omnivoice_model.TOTAL_BYTES
                if files_present
                else managed_status["size_bytes"]
            ),
            error=managed_status["error"],
            download_policy=(
                "仅在用户确认非商业模型许可后，从官方 Hugging Face 仓库下载固定版本，"
                "完成大小和 SHA-256 校验后才可使用。"
            ),
            reuse_note=(
                "优先使用统一模型目录；已有完整 Hugging Face 缓存仍可复用，运行时不会静默联网。"
            ),
        )
    return result


def _has_local_payload(path: Path) -> bool:
    if path.is_file() or path.is_symlink():
        return True
    try:
        return any(child.name not in {".locks", ".downloads"} for child in path.iterdir())
    except OSError:
        return False


def _candidates(engine_id: str) -> list[Path]:
    if engine_id == speaker_diarization_service.ENGINE_ID:
        return [
            speaker_diarization_service.model_path(),
            speaker_diarization_service.runtime_root(),
        ]
    if engine_id == speaker_verification_service.ENGINE_ID:
        return [
            speaker_verification_service.model_path(),
            speaker_verification_service.runtime_root(),
        ]
    if engine_id == ser_service.MODEL_ID:
        return [ser_service.model_path()]
    if engine_id == "indextts-v2":
        return settings_store.model_candidates(engine_id)
    if engine_id == confucius4_paths.ENGINE_ID:
        settings = settings_store.get()
        return [
            *settings_store.model_candidates(engine_id),
            confucius4_paths.runtime_root(expand_path(settings.data_dir)),
        ]
    if engine_id == qwen3_tts_paths.ENGINE_ID:
        return list(
            dict.fromkeys(
                [
                    *qwen3_tts_paths.model_root_candidates(),
                    *engine_runtime_paths.engine_root_candidates(engine_id),
                ]
            )
        )
    if engine_id == "omnivoice":
        return settings_store.model_candidates(engine_id)
    if engine_id in {"qwen3-forced-aligner", "semantic-alignment-labse"}:
        return settings_store.model_candidates(engine_id)
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
