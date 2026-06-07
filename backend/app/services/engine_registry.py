from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from app.models.schemas import EngineDetail, EngineManifest, EngineState, EngineStatus, ParameterSchema
from app.services import mimo_client, qwen_mlx_asr, settings_store
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
            description="本地 MLX 情绪化语音合成，支持声音克隆和 8 种情绪控制",
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
            description="600+ 语言本地声音克隆 / 声音设计引擎",
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
    "mimo-v2.5-tts-preset": EngineDetail(
        manifest=EngineManifest(
            engine_id="mimo-v2.5-tts-preset",
            display_name="MiMo V2.5 TTS Preset",
            engine_type="cloud",
            provider="Xiaomi MiMo",
            version="2.5",
            description="小米 MiMo Token Plan 云端语音合成，使用官方预置精品音色，支持自然语言风格控制和唱歌标签",
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
            description="小米 MiMo 文本音色设计，根据一段音色描述生成全新声音",
            supported_languages=["zh", "en"],
            capabilities=["cloud_api", "voice_design", "natural_language_control"],
            default_use_case="探索角色音色、一次性生成定制声音样本",
            privacy_level="cloud_required",
            parameter_schema=[
                ParameterSchema(key="voice_design_prompt", label="音色描述", type="textarea", default="", required=True, capability="voice_design"),
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
            description="小米 MiMo 音色复刻，生成时上传本次选择的 wav/mp3 参考音频样本",
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
            description="小米 MiMo 云端语音识别，将 wav/mp3 音频转写为文本",
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
            description="本地 MLX 语音识别，优先对接 Qwen3-ASR 1.7B 量化模型，预留后续更完整的离线转写能力",
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
