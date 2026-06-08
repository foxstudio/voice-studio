from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from app.models.schemas import EngineDetail, EngineManifest, EngineSpeaker, EngineState, EngineStatus, ParameterSchema
from app.services import cosyvoice_worker, f5_worker, mimo_client, qwen_mlx_asr, settings_store
from app.services.paths import PROJECT_ROOT


_EMOTION_OPTIONS = [
    {"label": "自然 calm", "value": "calm"},
    {"label": "高兴 happy", "value": "happy"},
    {"label": "悲伤 sad", "value": "sad"},
    {"label": "愤怒 angry", "value": "angry"},
    {"label": "恐惧 afraid", "value": "afraid"},
    {"label": "反感 disgusted", "value": "disgusted"},
    {"label": "低落 melancholic", "value": "melancholic"},
    {"label": "惊讶 surprised", "value": "surprised"},
]

_EMOTIVOICE_PROMPTS = [
    {"label": "开心", "value": "开心"},
    {"label": "兴奋", "value": "兴奋"},
    {"label": "悲伤", "value": "悲伤"},
    {"label": "愤怒", "value": "愤怒"},
    {"label": "害怕", "value": "害怕"},
    {"label": "惊讶", "value": "惊讶"},
    {"label": "厌恶", "value": "厌恶"},
    {"label": "中立", "value": "中立"},
]

_COSYVOICE_SPEAKERS = [
    {"label": "中文女", "value": "中文女"},
    {"label": "中文男", "value": "中文男"},
    {"label": "粤语女", "value": "粤语女"},
    {"label": "日语男", "value": "日语男"},
    {"label": "韩语女", "value": "韩语女"},
]

_EMOTIVOICE_SPEAKERS = [
    {"label": "8051 Maria Kasper 女声 · 清晰舒缓", "value": "8051"},
    {"label": "11614 Sylviamb 女声 · 清脆旋律", "value": "11614"},
    {"label": "9017 John Van Stan 男声 · 浑厚共鸣", "value": "9017"},
    {"label": "6097 Phil Benson 男声 · 平滑醇厚", "value": "6097"},
    {"label": "6671 Tony Oliva 男声 · 有魅力", "value": "6671"},
    {"label": "6670 Mike Pelton 男声", "value": "6670"},
    {"label": "9136 Helen Taylor 女声", "value": "9136"},
    {"label": "11697 Celine Major 女声", "value": "11697"},
    {"label": "92 Cori Samuel 女声 · 活泼有能量", "value": "92"},
    {"label": "12787 LikeManyWaters 女声", "value": "12787"},
    {"label": "1006 Marta Kornowska 女声", "value": "1006"},
    {"label": "1018 JimmyLogan 男声", "value": "1018"},
]


def _speaker_option_to_detail(option: dict[str, str]) -> EngineSpeaker:
    value = str(option.get("value") or "")
    label = str(option.get("label") or value)
    name = label.replace(value, "", 1).strip(" -·")
    return EngineSpeaker(speaker_id=value, name=name or value, label=label)


@lru_cache(maxsize=1)
def _emotivoice_speaker_catalog() -> list[EngineSpeaker]:
    readme = _external_engine_root("emotivoice") / "data" / "youdao" / "text" / "README.md"
    if not readme.exists():
        return [_speaker_option_to_detail(option) for option in _EMOTIVOICE_SPEAKERS]

    speakers: list[EngineSpeaker] = []
    for line in readme.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        speaker_id, name, gender, description = cells[:4]
        gender_label = {"F": "女声", "M": "男声"}.get(gender, gender)
        label_parts = [speaker_id, name]
        if gender_label:
            label_parts.append(gender_label)
        if description:
            label_parts.append(f"· {description}")
        speakers.append(
            EngineSpeaker(
                speaker_id=speaker_id,
                name=name,
                gender=gender,
                description=description,
                label=" ".join(label_parts),
            )
        )
    return speakers or [_speaker_option_to_detail(option) for option in _EMOTIVOICE_SPEAKERS]


def list_speakers(engine_id: str, query: str = "", gender: str = "all", limit: int = 80) -> list[EngineSpeaker]:
    engine_id = _resolve_engine_id(engine_id)
    limit = max(1, min(limit, 500))
    normalized_query = query.strip().lower()
    normalized_gender = gender.strip().upper()

    if engine_id == "emotivoice":
        speakers = _emotivoice_speaker_catalog()
        if normalized_gender in {"F", "M"}:
            speakers = [speaker for speaker in speakers if speaker.gender.upper() == normalized_gender]
        if normalized_query:
            speakers = [
                speaker
                for speaker in speakers
                if normalized_query in " ".join([speaker.speaker_id, speaker.name, speaker.gender, speaker.description, speaker.label]).lower()
            ]
        return speakers[:limit]

    detail = get_engine(engine_id)
    if not detail:
        return []
    speaker_param = next((param for param in detail.manifest.parameter_schema if param.key == "speaker_id"), None)
    return [_speaker_option_to_detail(option) for option in (speaker_param.options if speaker_param else [])][:limit]


def _common_params(v2: bool = False) -> list[ParameterSchema]:
    params = [
        ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05),
        ParameterSchema(key="temperature", label="随机性 Temperature", type="slider", default=0.8 if v2 else 1.0, min=0.1, max=2.0, step=0.05),
        ParameterSchema(key="top_p", label="采样范围 Top-P", type="slider", default=0.8, min=0, max=1, step=0.05, level="advanced"),
        ParameterSchema(key="top_k", label="候选数量 Top-K", type="slider", default=30, min=1, max=100, step=1, level="advanced"),
        ParameterSchema(key="max_text_tokens_per_segment", label="分段 Token", type="slider", default=120, min=20, max=500, step=10, level="advanced"),
        ParameterSchema(key="interval_silence", label="段间静默 ms", type="slider", default=200, min=0, max=2000, step=50, level="advanced"),
    ]
    if v2:
        params.extend([
            ParameterSchema(
                key="emotion",
                label="情绪",
                type="select",
                default="calm",
                options=_EMOTION_OPTIONS,
                capability="emotion_control",
            ),
            ParameterSchema(key="emo_alpha", label="情绪强度", type="slider", default=0.6, min=0, max=1, step=0.05, capability="emotion_control"),
            ParameterSchema(key="diffusion_steps", label="扩散步数 Diffusion Steps", type="slider", default=25, min=5, max=60, step=1, level="advanced"),
            ParameterSchema(key="cfg_rate", label="引导强度 CFG Rate", type="slider", default=0.7, min=0, max=1, step=0.05, level="advanced"),
        ])
    return params


_ENGINES: dict[str, EngineDetail] = {
    "indextts-v2": EngineDetail(
        manifest=EngineManifest(
            engine_id="indextts-v2",
            display_name="IndexTTS v2",
            provider="Index Team",
            version="2.0",
            description="Bilibili IndexTeam 工业级零样本 TTS，支持 8 种情绪控制和声音克隆",
            supported_languages=["zh", "en"],
            capabilities=["local_inference", "voice_clone", "emotion_control", "long_text", "pinyin_control"],
            sample_rate=22050,
            max_tokens=1815,
            default_use_case="中文/英文情绪化配音",
            parameter_schema=_common_params(v2=True),
        ),
        state=EngineState(engine_id="indextts-v2", status=EngineStatus.stopped),
    ),
    "omnivoice": EngineDetail(
        manifest=EngineManifest(
            engine_id="omnivoice",
            display_name="OmniVoice",
            provider="k2-fsa",
            version="0.1.5",
            description="k2-fsa 出品，600+ 语言零样本声音克隆与声音设计",
            supported_languages=["auto", "zh", "en", "ja", "ko", "fr", "de", "es"],
            capabilities=["local_inference", "voice_clone", "voice_design", "multilingual", "nonverbal_tags", "pinyin_control"],
            sample_rate=24000,
            default_use_case="多语言克隆与声音设计",
            parameter_schema=[
                ParameterSchema(key="language", label="语言", type="select", default="auto", options=[{"label": x, "value": x} for x in ["auto", "zh", "en", "ja", "ko", "fr", "de", "es"]]),
                ParameterSchema(key="emotion_text", label="声音描述/指令", type="textarea", default="", capability="voice_design"),
                ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05),
            ],
        ),
        state=EngineState(engine_id="omnivoice", status=EngineStatus.stopped),
    ),
    "emotivoice": EngineDetail(
        manifest=EngineManifest(
            engine_id="emotivoice",
            display_name="EmotiVoice",
            provider="NetEase Youdao",
            version="open-source",
            description="网易有道开源中英双语情感 TTS，2000+ 预置音色，提示词控制情绪",
            supported_languages=["zh"],
            capabilities=["local_inference", "preset_voice", "emotion_control"],
            sample_rate=16000,
            default_use_case="中文角色感、情绪短句和音色预设试听",
            parameter_schema=[
                ParameterSchema(
                    key="speaker_id",
                    label="说话人",
                    type="select",
                    default="8051",
                    options=_EMOTIVOICE_SPEAKERS,
                    required=True,
                    capability="preset_voice",
                ),
                ParameterSchema(
                    key="prompt",
                    label="情绪提示",
                    type="select",
                    default="开心",
                    options=_EMOTIVOICE_PROMPTS,
                    capability="emotion_control",
                ),
            ],
        ),
        state=EngineState(engine_id="emotivoice", status=EngineStatus.stopped),
    ),
    "f5-tts": EngineDetail(
        manifest=EngineManifest(
            engine_id="f5-tts",
            display_name="F5-TTS",
            provider="SWivid",
            version="v1 Base",
            description="基于 Flow Matching + DiT 的非自回归零样本 TTS，5 秒参考音频即可克隆",
            supported_languages=["zh", "en"],
            capabilities=["local_inference", "voice_clone", "multilingual"],
            sample_rate=24000,
            default_use_case="已授权参考音频的音色迁移和中英文生成研究",
            privacy_level="local_only_noncommercial_model",
            parameter_schema=[
                ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05),
                ParameterSchema(key="nfe_step", label="采样步数 NFE", type="slider", default=32, min=4, max=64, step=1, level="advanced"),
                ParameterSchema(key="cfg_strength", label="引导强度 CFG", type="slider", default=2.0, min=0.1, max=5.0, step=0.1, level="advanced"),
                ParameterSchema(key="target_rms", label="响度目标 RMS", type="slider", default=0.1, min=0.01, max=0.5, step=0.01, level="advanced"),
                ParameterSchema(key="cross_fade_duration", label="分段交叉淡化", type="slider", default=0.15, min=0, max=1, step=0.05, level="advanced"),
                ParameterSchema(key="remove_silence", label="移除静音", type="toggle", default=False, level="advanced"),
            ],
        ),
        state=EngineState(engine_id="f5-tts", status=EngineStatus.stopped),
    ),
    "cosyvoice-sft": EngineDetail(
        manifest=EngineManifest(
            engine_id="cosyvoice-sft",
            display_name="CosyVoice SFT",
            provider="FunAudioLLM",
            version="300M-SFT",
            description="阿里巴巴 FunAudioLLM 多语种 TTS，官方 SFT 预置音色，支持中粤日韩英",
            supported_languages=["zh", "yue", "ja", "ko", "en"],
            capabilities=["local_inference", "preset_voice", "multilingual"],
            sample_rate=22050,
            default_use_case="高质量官方预置音色口播和多语种试听",
            parameter_schema=[
                ParameterSchema(
                    key="speaker_id",
                    label="预置音色",
                    type="select",
                    default="中文女",
                    options=_COSYVOICE_SPEAKERS,
                    required=True,
                    capability="preset_voice",
                ),
                ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05),
            ],
        ),
        state=EngineState(engine_id="cosyvoice-sft", status=EngineStatus.stopped),
    ),
    "cosyvoice-zero-shot": EngineDetail(
        manifest=EngineManifest(
            engine_id="cosyvoice-zero-shot",
            display_name="CosyVoice Zero-Shot",
            provider="FunAudioLLM",
            version="300M-SFT zero-shot path",
            description="CosyVoice 零样本声音克隆，提供参考音频即可复刻任意音色，支持跨语言",
            supported_languages=["zh", "en", "yue", "ja", "ko"],
            capabilities=["local_inference", "voice_clone", "zero_shot", "multilingual"],
            sample_rate=22050,
            default_use_case="使用已授权参考音频做 CosyVoice 本地音色复刻",
            parameter_schema=[
                ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05),
            ],
        ),
        state=EngineState(engine_id="cosyvoice-zero-shot", status=EngineStatus.stopped),
    ),
    "mimo-v2.5-tts-preset": EngineDetail(
        manifest=EngineManifest(
            engine_id="mimo-v2.5-tts-preset",
            display_name="MiMo V2.5 TTS Preset",
            engine_type="cloud",
            provider="Xiaomi MiMo",
            version="2.5",
            description="小米云端精品音色合成，支持自然语言风格控制和唱歌标签",
            supported_languages=["zh", "en"],
            capabilities=["cloud_api", "preset_voice", "natural_language_control", "audio_tags", "singing"],
            default_use_case="云端中文/英文口播、唱歌和官方预置音色快速合成",
            privacy_level="cloud_required",
            parameter_schema=[
                ParameterSchema(
                    key="mimo_voice",
                    label="MiMo 官方音色",
                    type="select",
                    default="mimo_default",
                    options=[{"label": item["label"], "value": item["voice_id"]} for item in mimo_client.MIMO_PRESET_VOICES],
                    required=True,
                    capability="preset_voice",
                ),
                ParameterSchema(key="style_instruction", label="风格指令", type="textarea", default="", capability="natural_language_control"),
                ParameterSchema(key="temperature", label="随机性 Temperature", type="slider", default=0.6, min=0, max=1.5, step=0.05, level="advanced"),
                ParameterSchema(key="top_p", label="采样范围 Top-P", type="slider", default=0.95, min=0.01, max=1.0, step=0.01, level="advanced"),
            ],
        ),
        state=EngineState(engine_id="mimo-v2.5-tts-preset", status=EngineStatus.stopped),
    ),
    "mimo-v2.5-tts-voicedesign": EngineDetail(
        manifest=EngineManifest(
            engine_id="mimo-v2.5-tts-voicedesign",
            display_name="MiMo V2.5 TTS VoiceDesign",
            engine_type="cloud",
            provider="Xiaomi MiMo",
            version="2.5",
            description="小米云端音色设计，用一段文字描述即可生成全新声音",
            supported_languages=["zh", "en"],
            capabilities=["cloud_api", "voice_design", "natural_language_control"],
            default_use_case="探索角色音色、一次性生成定制声音样本",
            privacy_level="cloud_required",
            parameter_schema=[
                ParameterSchema(key="voice_design_prompt", label="音色描述", type="textarea", default="", required=True, capability="voice_design"),
                ParameterSchema(
                    key="optimize_text_preview",
                    label="润色播报文本",
                    type="toggle",
                    default=False,
                    level="advanced",
                    capability="voice_design",
                ),
                ParameterSchema(key="temperature", label="随机性 Temperature", type="slider", default=0.6, min=0, max=1.5, step=0.05, level="advanced"),
                ParameterSchema(key="top_p", label="采样范围 Top-P", type="slider", default=0.95, min=0.01, max=1.0, step=0.01, level="advanced"),
            ],
        ),
        state=EngineState(engine_id="mimo-v2.5-tts-voicedesign", status=EngineStatus.stopped),
    ),
    "mimo-v2.5-tts-voiceclone": EngineDetail(
        manifest=EngineManifest(
            engine_id="mimo-v2.5-tts-voiceclone",
            display_name="MiMo V2.5 TTS VoiceClone",
            engine_type="cloud",
            provider="Xiaomi MiMo",
            version="2.5",
            description="小米云端音色复刻，上传参考音频即可零样本克隆任意说话人",
            supported_languages=["zh", "en"],
            capabilities=["cloud_api", "voice_clone", "natural_language_control", "audio_tags"],
            default_use_case="使用已授权的本地参考音色做云端复刻合成",
            privacy_level="cloud_required",
            parameter_schema=[
                ParameterSchema(key="style_instruction", label="风格指令", type="textarea", default="", capability="natural_language_control"),
                ParameterSchema(key="temperature", label="随机性 Temperature", type="slider", default=0.6, min=0, max=1.5, step=0.05, level="advanced"),
                ParameterSchema(key="top_p", label="采样范围 Top-P", type="slider", default=0.95, min=0.01, max=1.0, step=0.01, level="advanced"),
            ],
        ),
        state=EngineState(engine_id="mimo-v2.5-tts-voiceclone", status=EngineStatus.stopped),
    ),
    "mimo-v2.5-asr": EngineDetail(
        manifest=EngineManifest(
            engine_id="mimo-v2.5-asr",
            display_name="MiMo V2.5 ASR",
            engine_type="cloud",
            provider="Xiaomi MiMo",
            version="2.5",
            description="小米云端语音识别，支持中英文音频转写",
            supported_languages=["auto", "zh", "en"],
            capabilities=["cloud_api", "speech_recognition", "transcription"],
            default_use_case="会议、录音和素材音频转文字",
            privacy_level="cloud_required",
            parameter_schema=[
                ParameterSchema(key="language", label="识别语言", type="select", default="auto", options=[{"label": x, "value": x} for x in ["auto", "zh", "en"]]),
            ],
        ),
        state=EngineState(engine_id="mimo-v2.5-asr", status=EngineStatus.stopped),
    ),
    "qwen3-asr-mlx": EngineDetail(
        manifest=EngineManifest(
            engine_id="qwen3-asr-mlx",
            display_name="Qwen3-ASR MLX",
            provider="Qwen + MLX Community",
            version="1.7B 8-bit",
            description="本地 Apple Silicon 语音识别，Qwen3-ASR 1.7B MLX 量化推理，超越 Whisper-large-v3",
            supported_languages=["auto", "zh", "en"],
            capabilities=["local_inference", "speech_recognition", "transcription", "language_identification"],
            default_use_case="离线音频转写与云端 ASR 备选",
            parameter_schema=[
                ParameterSchema(key="language", label="识别语言", type="select", default="auto", options=[{"label": x, "value": x} for x in ["auto", "zh", "en"]]),
            ],
        ),
        state=EngineState(engine_id="qwen3-asr-mlx", status=EngineStatus.stopped),
    ),
}

_ALIASES = {"mimo-v2.5-tts": "mimo-v2.5-tts-preset"}


def _resolve_engine_id(engine_id: str) -> str:
    return _ALIASES.get(engine_id, engine_id)


def list_engines() -> list[EngineDetail]:
    return list(_ENGINES.values())


def get_engine(engine_id: str) -> EngineDetail | None:
    return _ENGINES.get(_resolve_engine_id(engine_id))


def _mlx_audio_runtime_available() -> tuple[bool, str | None]:
    return qwen_mlx_asr.runtime_available()


def _external_engine_root(engine_id: str) -> Path:
    env_names = {
        "emotivoice": "VOICE_STUDIO_EMOTIVOICE_ROOT",
        "f5-tts": "VOICE_STUDIO_F5_TTS_ROOT",
        "cosyvoice-sft": "VOICE_STUDIO_COSYVOICE_ROOT",
        "cosyvoice-zero-shot": "VOICE_STUDIO_COSYVOICE_ROOT",
    }
    defaults = {
        "emotivoice": "/Users/foxmacstudio/Projects/tts-engine-lab/EmotiVoice",
        "f5-tts": "/Users/foxmacstudio/Projects/tts-engine-lab/F5-TTS",
        "cosyvoice-sft": "/Users/foxmacstudio/Projects/tts-engine-lab/CosyVoice",
        "cosyvoice-zero-shot": "/Users/foxmacstudio/Projects/tts-engine-lab/CosyVoice",
    }
    value = os.environ.get(env_names[engine_id], defaults[engine_id])
    return Path(value).expanduser()


def _health_external_engine(engine_id: str) -> dict[str, Any]:
    root = _external_engine_root(engine_id)
    python = root / ".venv" / "bin" / "python"
    required: list[Path] = [python]
    if engine_id == "emotivoice":
        required.extend([
            root / "frontend.py",
            root / "inference_am_vocoder_joint.py",
            root / "WangZeJun" / "simbert-base-chinese" / "pytorch_model.bin",
            root / "outputs" / "prompt_tts_open_source_joint" / "ckpt" / "g_00140000",
        ])
    elif engine_id == "f5-tts":
        required.extend([
            root / "src" / "f5_tts" / "api.py",
            root / "local_smoke" / "modelscope" / "F5-TTS_Emilia-ZH-EN" / "model_1250000.safetensors",
            root / "local_smoke" / "modelscope" / "F5-TTS_Emilia-ZH-EN" / "vocab.txt",
        ])
    elif engine_id in {"cosyvoice-sft", "cosyvoice-zero-shot"}:
        required.extend([
            root / "cosyvoice" / "cli" / "cosyvoice.py",
            root / "third_party" / "Matcha-TTS",
            root / "pretrained_models" / "CosyVoice-300M-SFT" / "cosyvoice.yaml",
            root / "pretrained_models" / "CosyVoice-300M-SFT" / "llm.pt",
            root / "pretrained_models" / "CosyVoice-300M-SFT" / "spk2info.pt",
        ])
    missing = [str(path.relative_to(root)) if path.is_relative_to(root) else str(path) for path in required if not path.exists()]
    return {
        "healthy": not missing,
        "status": "ok" if not missing else "external_runtime_missing",
        "model_path": str(root),
        "python": str(python),
        "missing": missing,
    }


def health_check(engine_id: str) -> dict[str, Any]:
    engine_id = _resolve_engine_id(engine_id)
    detail = _ENGINES.get(engine_id)
    if not detail:
        return {"healthy": False, "status": "not_found"}
    if engine_id.startswith("indextts"):
        model_dir = settings_store.model_path(engine_id)
        required = ["tokenizer.model"]
        required.append("gpt.safetensors")
        missing = [name for name in required if not (model_dir / name).exists()]
        return {
            "healthy": not missing,
            "status": "ok" if not missing else "model_missing",
            "model_path": str(model_dir),
            "missing": missing,
        }
    if engine_id.startswith("mimo-v2.5-tts") or engine_id == "mimo-v2.5-asr":
        settings = settings_store.get()
        if not settings.cloud_enabled:
            return {"healthy": False, "status": "cloud_disabled", "detail": "云端引擎未启用"}
        if not settings.mimo_api_key_configured:
            return {"healthy": False, "status": "api_key_missing", "detail": "MiMo Token Plan API Key 未配置"}
        return {"healthy": True, "status": "configured", "base_url": settings.mimo_base_url}
    if engine_id == "qwen3-asr-mlx":
        model_path = settings_store.model_path(engine_id)
        health = qwen_mlx_asr.model_health(model_path)
        if health.get("status") == "runtime_missing":
            ok, detail = _mlx_audio_runtime_available()
            health["healthy"] = ok
            health["detail"] = f"mlx-audio runtime is unavailable: {detail}" if detail else health.get("detail")
        return health
    if engine_id in {"emotivoice", "f5-tts", "cosyvoice-sft", "cosyvoice-zero-shot"}:
        return _health_external_engine(engine_id)
    try:
        import omnivoice  # noqa: F401

        return {"healthy": True, "status": "package_available", "model_id": "k2-fsa/OmniVoice"}
    except Exception as exc:
        return {"healthy": False, "status": "package_missing", "detail": str(exc)}


def start_engine(engine_id: str) -> EngineDetail:
    engine_id = _resolve_engine_id(engine_id)
    detail = _ENGINES[engine_id]
    detail.state.status = EngineStatus.loading
    hc = health_check(engine_id)
    if not hc.get("healthy"):
        detail.state.status = EngineStatus.error
        detail.state.error_message = str(hc)
        return detail
    detail.state.status = EngineStatus.loaded
    detail.state.model_path = hc.get("model_path") or hc.get("base_url")
    detail.state.error_message = None
    detail.state.loaded_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    return detail


def stop_engine(engine_id: str) -> EngineDetail:
    engine_id = _resolve_engine_id(engine_id)
    if engine_id == "f5-tts":
        f5_worker.shutdown()
    if engine_id in {"cosyvoice-sft", "cosyvoice-zero-shot"}:
        cosyvoice_worker.shutdown()
    detail = _ENGINES[engine_id]
    detail.state.status = EngineStatus.stopped
    detail.state.error_message = None
    return detail


def ensure_loaded(engine_id: str) -> None:
    engine_id = _resolve_engine_id(engine_id)
    detail = _ENGINES.get(engine_id)
    if not detail:
        raise ValueError(f"Unknown engine: {engine_id}")
    if detail.state.status != EngineStatus.loaded:
        start_engine(engine_id)
    if detail.state.status != EngineStatus.loaded:
        raise RuntimeError(detail.state.error_message or f"Engine {engine_id} is not available")


def run_isolated(
    engine_id: str,
    kwargs: dict[str, Any],
    timeout: int = 900,
    cancel_check: Callable[[], bool] | None = None,
    on_tick: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    if engine_id == "f5-tts" and os.environ.get("VOICE_STUDIO_F5_PERSISTENT_WORKER", "1") != "0":
        root = _external_engine_root("f5-tts")
        return f5_worker.run(
            kwargs,
            root=root,
            python=str(root / ".venv" / "bin" / "python"),
            timeout=timeout,
            cancel_check=cancel_check,
            on_tick=on_tick,
        )
    if engine_id in {"cosyvoice-sft", "cosyvoice-zero-shot"} and os.environ.get("VOICE_STUDIO_COSYVOICE_PERSISTENT_WORKER", "1") != "0":
        root = _external_engine_root(engine_id)
        return cosyvoice_worker.run(
            engine_id,
            kwargs,
            root=root,
            python=str(root / ".venv" / "bin" / "python"),
            timeout=timeout,
            cancel_check=cancel_check,
            on_tick=on_tick,
        )

    payload = __import__("json").dumps({"engine_id": engine_id, "kwargs": kwargs}, ensure_ascii=False)
    env = {"PYTHONPATH": f"{PROJECT_ROOT / 'backend'}:{PROJECT_ROOT}", **__import__("os").environ}
    popen_kwargs: dict[str, Any] = {}
    if hasattr(os, "setsid"):
        popen_kwargs["preexec_fn"] = os.setsid
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.services.inference_runner"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
        **popen_kwargs,
    )

    assert proc.stdin is not None
    proc.stdin.write(payload)
    proc.stdin.close()
    proc.stdin = None

    started_at = time.monotonic()
    while proc.poll() is None:
        elapsed = time.monotonic() - started_at
        if cancel_check and cancel_check():
            _terminate_process(proc)
            raise RuntimeError("Generation cancelled")
        if elapsed > timeout:
            _terminate_process(proc)
            raise RuntimeError(f"Inference timed out after {timeout}s")
        if on_tick:
            on_tick(elapsed)
        time.sleep(0.5)

    stdout, stderr = proc.communicate()
    stdout = (stdout or "").strip()
    stderr = (stderr or "").strip()
    if proc.returncode != 0:
        try:
            error = __import__("json").loads(stdout.splitlines()[-1] if stdout else "{}")
        except Exception:
            error = {}
        raise RuntimeError(error.get("error") or stderr[-1200:] or "Inference subprocess failed")
    if not stdout:
        raise RuntimeError("Inference subprocess returned no output")
    return __import__("json").loads(stdout.splitlines()[-1])


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if hasattr(os, "getpgid"):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            if hasattr(os, "getpgid"):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            proc.kill()
        proc.wait(timeout=5)


def shutdown_workers() -> None:
    f5_worker.shutdown()
    cosyvoice_worker.shutdown()
