from __future__ import annotations

from typing import Literal

RunnerKind = Literal["local", "cloud", "external_subprocess", "persistent_worker", "asr_local"]

_ALIASES = {"mimo-v2.5-tts": "mimo-v2.5-tts-preset"}

MIMO_TTS_ENGINES = {"mimo-v2.5-tts", "mimo-v2.5-tts-preset", "mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voiceclone"}
MIMO_ENGINES = {*MIMO_TTS_ENGINES, "mimo-v2.5-asr"}
EXTERNAL_WORKER_ENGINES = {"f5-tts", "cosyvoice-sft", "cosyvoice-zero-shot"}
EXTERNAL_SUBPROCESS_ENGINES = {"emotivoice", *EXTERNAL_WORKER_ENGINES}
LOCAL_MODEL_ENGINES = {"indextts-v2", "omnivoice"}
LOCAL_ASR_ENGINES = {"qwen3-asr-mlx"}

_TIMEOUTS = {
    "omnivoice": 600,
    "indextts-v2": 420,
    "emotivoice": 420,
    "f5-tts": 600,
    "cosyvoice-sft": 900,
    "cosyvoice-zero-shot": 900,
    "mimo-v2.5-tts": 300,
    "mimo-v2.5-tts-preset": 300,
    "mimo-v2.5-tts-voicedesign": 300,
    "mimo-v2.5-tts-voiceclone": 300,
}


def resolve_engine_id(engine_id: str) -> str:
    return _ALIASES.get(engine_id, engine_id)


def is_mimo_tts(engine_id: str) -> bool:
    return engine_id in MIMO_TTS_ENGINES


def is_cloud_engine(engine_id: str) -> bool:
    return engine_id in MIMO_ENGINES


def requires_idempotency_marker(engine_id: str) -> bool:
    return is_mimo_tts(engine_id)


def timeout_seconds_for(engine_id: str) -> int:
    return _TIMEOUTS.get(engine_id, 300)


def runner_kind_for(engine_id: str) -> RunnerKind:
    resolved = resolve_engine_id(engine_id)
    if resolved in MIMO_ENGINES:
        return "cloud"
    if resolved in EXTERNAL_WORKER_ENGINES:
        return "persistent_worker"
    if resolved in EXTERNAL_SUBPROCESS_ENGINES:
        return "external_subprocess"
    if resolved in LOCAL_ASR_ENGINES:
        return "asr_local"
    return "local"

