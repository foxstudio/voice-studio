"""引擎注册表 — 管理所有 TTS 引擎的生命周期"""

import gc
import os
import sys
import threading

from app.models.schemas import (
    EngineDetail, EngineManifest, EngineState, EngineStatus,
)

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_lock = threading.Lock()
_engine_instances: dict[str, object | None] = {}

MODEL_DIR = os.path.join(_project_root, "models", "mlx-indexTTS-2.0")
_ENGINES: dict[str, EngineDetail] = {
    "indextts": EngineDetail(
        manifest=EngineManifest(
            engine_id="indextts",
            name="IndexTTS",
            display_name="IndexTTS",
            engine_type="local",
            provider="Index Team",
            description="中文/英文情绪化语音合成引擎，支持声音克隆",
            supported_languages=["zh", "en"],
            capabilities=["local_inference", "voice_clone", "emotion_reference",
                          "emotion_vector", "emotion_text", "pinyin_control", "long_text"],
            default_use_case="中文/英文情绪化配音",
            privacy_level="local_only",
        ),
        state=EngineState(engine_id="indextts", status=EngineStatus.stopped),
    ),
    "omnivoice": EngineDetail(
        manifest=EngineManifest(
            engine_id="omnivoice",
            name="OmniVoice",
            display_name="OmniVoice",
            engine_type="local",
            provider="OmniVoice Team",
            description="600+ 语言零样本 TTS，支持声音克隆与声音设计",
            supported_languages=["zh", "en", "ja", "ko", "fr", "de", "es"],
            capabilities=["local_inference", "voice_clone", "voice_design", "auto_voice",
                          "multilingual", "nonverbal_tags", "pinyin_control", "phoneme_control"],
            default_use_case="多语言语音生成与声音设计",
            privacy_level="local_only",
        ),
        state=EngineState(engine_id="omnivoice", status=EngineStatus.not_installed),
    ),
}


def list_engines() -> list[EngineDetail]:
    return list(_ENGINES.values())


def get_engine(engine_id: str) -> EngineDetail:
    if engine_id not in _ENGINES:
        from fastapi import HTTPException
        raise HTTPException(404, f"Engine {engine_id} not found")
    return _ENGINES[engine_id]


def get_engine_instance(engine_id: str) -> object | None:
    return _engine_instances.get(engine_id)

def start_engine(engine_id: str) -> EngineDetail:
    if engine_id not in _ENGINES:
        from fastapi import HTTPException
        raise HTTPException(404, f"Engine {engine_id} not found")

    detail = _ENGINES[engine_id]

    with _lock:
        if detail.state.status == EngineStatus.loaded and _engine_instances.get(engine_id) is not None:
            return detail
        detail.state.status = EngineStatus.loading
        detail.state.error_message = None

    try:
        if not os.path.exists(MODEL_DIR):
            raise FileNotFoundError(f"Model not found at {MODEL_DIR}")

        from mlx_indextts.generate_v2 import IndexTTSv2
        instance = IndexTTSv2(MODEL_DIR, device="mps")

        with _lock:
            _engine_instances[engine_id] = instance
            detail.state.status = EngineStatus.loaded

    except Exception as exc:
        with _lock:
            detail.state.status = EngineStatus.error
            detail.state.error_message = str(exc)

    return detail

def stop_engine(engine_id: str) -> EngineDetail:
    if engine_id not in _ENGINES:
        from fastapi import HTTPException
        raise HTTPException(404, f"Engine {engine_id} not found")

    detail = _ENGINES[engine_id]

    with _lock:
        _engine_instances[engine_id] = None
        detail.state.status = EngineStatus.stopped
        detail.state.error_message = None

    gc.collect()
    return detail

def health_check(engine_id: str) -> dict:
    if engine_id not in _ENGINES:
        return {"status": "not_found"}
    e = _ENGINES[engine_id]
    return {
        "engine_id": engine_id,
        "status": e.state.status.value,
        "healthy": e.state.status == EngineStatus.loaded,
    }
