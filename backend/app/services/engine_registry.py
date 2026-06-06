"""引擎注册表 — 管理所有 TTS 引擎的生命周期"""

import gc
import os
import sys
import threading

from app.models.schemas import (
    EngineDetail, EngineManifest, EngineState, EngineStatus,
    EngineType,
)

from app.services.adapters.v1_adapter import V1Adapter
from app.services.adapters.omnivoice_adapter import OmniVoiceAdapter
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_lock = threading.Lock()
_engine_instances: dict[str, object | None] = {}
_engine_sample_rates: dict[str, int] = {}

MODEL_DIR = os.path.join(_project_root, "models", "mlx-indexTTS-2.0")
_ENGINES: dict[str, EngineDetail] = {
    "indextts": EngineDetail(
        manifest=EngineManifest(
            engine_id="indextts",
            name="IndexTTS",
            display_name="IndexTTS v2",
            engine_type="local",
            provider="Index Team",
            version="v2",
            description="中文/英文情绪化语音合成引擎（第二代），支持声音克隆与8种情绪控制",
            supported_languages=["zh", "en"],
            capabilities=["local_inference", "voice_clone", "multilingual",
                          "emotion_control", "pinyin_control", "long_text"],
            sample_rate=22050,
            max_tokens=1815,
            default_use_case="中文/英文情绪化配音",
            privacy_level="local_only",
            available_versions=["v1", "v2"],
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
            capabilities=["local_inference", "voice_clone", "voice_design",
                          "multilingual", "emotion_control", "nonverbal_tags"],
            sample_rate=24000,
            default_use_case="多语言语音生成与声音设计",
            privacy_level="local_only",
        ),
        state=EngineState(engine_id="omnivoice", status=EngineStatus.not_installed),
    ),
    "indextts-v1": EngineDetail(
        manifest=EngineManifest(
            engine_id="indextts-v1",
            name="IndexTTS",
            display_name="IndexTTS v1",
            engine_type=EngineType.local,
            provider="Index Team",
            version="v1",
            description="中文/英文语音合成引擎（第一代），支持声音克隆",
            supported_languages=["zh", "en"],
            capabilities=["local_inference", "voice_clone", "multilingual"],
            sample_rate=24000,
            max_tokens=800,
            default_use_case="中文/英文配音",
            privacy_level="local_only",
            available_versions=["v1", "v2"],
        ),
        state=EngineState(
            engine_id="indextts-v1",
            status=EngineStatus.not_installed,
        ),
    ),
    }

engine_adapter_map: dict[str, type] = {
    "indextts-v1": V1Adapter,
    "omnivoice": OmniVoiceAdapter,
}



def list_engines() -> list[EngineDetail]:
    return list(_ENGINES.values())


def get_engine(engine_id: str) -> EngineDetail:
    if engine_id not in _ENGINES:
        from app.models.exceptions import AppException
        raise AppException(404, "ENGINE_NOT_FOUND", f"Engine {engine_id} not found")
    return _ENGINES[engine_id]


def get_engine_instance(engine_id: str) -> object | None:
    return _engine_instances.get(engine_id)

def start_engine(engine_id: str) -> EngineDetail:
    if engine_id not in _ENGINES:
        from app.models.exceptions import AppException
        raise AppException(404, "ENGINE_NOT_FOUND", f"Engine {engine_id} not found")

    detail = _ENGINES[engine_id]

    with _lock:
        if detail.state.status == EngineStatus.loaded and _engine_instances.get(engine_id) is not None:
            return detail
        detail.state.status = EngineStatus.loading
        detail.state.error_message = None

    try:
        if engine_id == "indextts":
            if not os.path.exists(MODEL_DIR):
                raise FileNotFoundError(f"Model not found at {MODEL_DIR}")

            from mlx_indextts.generate_v2 import IndexTTSv2
            instance = IndexTTSv2(MODEL_DIR, device="mps")

            # Read sample_rate from model config.json for accurate duration calculation
            config_path = os.path.join(MODEL_DIR, "config.json")
            sample_rate = 22050  # fallback default
            if os.path.exists(config_path):
                import json
                with open(config_path) as f:
                    cfg = json.load(f)
                    sample_rate = cfg.get("sample_rate", 22050)

            with _lock:
                _engine_instances[engine_id] = instance
                _engine_sample_rates[engine_id] = sample_rate
                detail.state.status = EngineStatus.loaded

        elif engine_id in engine_adapter_map:
            adapter_cls = engine_adapter_map[engine_id]
            adapter = adapter_cls()
            adapter.health_check()  # validate model availability
            if hasattr(adapter, 'load'):
                adapter.load()  # load model weights (V1Adapter needs this; OmniVoice lazy-loads)

            with _lock:
                _engine_instances[engine_id] = adapter
                _engine_sample_rates[engine_id] = adapter.manifest["sample_rate"]
                detail.state.status = EngineStatus.loaded

    except Exception as exc:
        with _lock:
            detail.state.status = EngineStatus.error
            detail.state.error_message = str(exc)

    return detail

def stop_engine(engine_id: str) -> EngineDetail:
    if engine_id not in _ENGINES:
        from app.models.exceptions import AppException
        raise AppException(404, "ENGINE_NOT_FOUND", f"Engine {engine_id} not found")

    detail = _ENGINES[engine_id]

    with _lock:
        _engine_instances[engine_id] = None
        detail.state.status = EngineStatus.stopped
        detail.state.error_message = None

    gc.collect()
    return detail


def get_engine_sample_rate(engine_id: str) -> int:
    """Get the sample rate for a loaded engine, read from its model config.json."""
    return _engine_sample_rates.get(engine_id, 22050)
def health_check(engine_id: str) -> dict:
    if engine_id not in _ENGINES:
        return {"status": "not_found"}

    instance = _engine_instances.get(engine_id)
    if instance is not None and hasattr(instance, 'health_check'):
        return instance.health_check()

    e = _ENGINES[engine_id]
    return {
        "engine_id": engine_id,
        "status": e.state.status.value,
        "healthy": e.state.status == EngineStatus.loaded,
    }


def reload_engine(engine_id: str) -> EngineDetail:
    """Stop and restart an engine."""
    stop_engine(engine_id)
    return start_engine(engine_id)

