from __future__ import annotations

from dataclasses import dataclass

from app.services import engine_registry, settings_store, vibevoice_model


DEFAULT_FAST_ASR_ENGINE_ID = "qwen3-asr-mlx"
VIBEVOICE_4BIT_PROVIDER_ID = "vibevoice-asr-mlx-4bit"
VIBEVOICE_8BIT_PROVIDER_ID = "vibevoice-asr-mlx-8bit"
VIBEVOICE_PROVIDER_IDS = {
    VIBEVOICE_4BIT_PROVIDER_ID,
    VIBEVOICE_8BIT_PROVIDER_ID,
}


@dataclass(frozen=True, slots=True)
class AsrSelection:
    engine_id: str
    diarization_engine_id: str | None
    mode: str
    reason: str


def select(
    *,
    requested_engine_id: str | None,
    needs_speaker_diarization: bool,
) -> AsrSelection:
    requested = str(requested_engine_id or "auto").strip().lower() or "auto"
    if requested != "auto":
        return _explicit_selection(requested, needs_speaker_diarization)

    configured = settings_store.get().default_asr_engine_id
    if configured != "auto":
        return _explicit_selection(configured, needs_speaker_diarization)

    if needs_speaker_diarization:
        memory = vibevoice_model.physical_memory_bytes()
        if _variant_ready("4bit") and (memory is None or memory >= 24 * 1024**3):
            return AsrSelection(
                engine_id=VIBEVOICE_4BIT_PROVIDER_ID,
                diarization_engine_id=VIBEVOICE_4BIT_PROVIDER_ID,
                mode="auto",
                reason="已安装 4bit；本机内存满足长视频文字与说话人联合识别门槛",
            )
        if _variant_ready("8bit") and (memory is None or memory >= 32 * 1024**3):
            return AsrSelection(
                engine_id=VIBEVOICE_8BIT_PROVIDER_ID,
                diarization_engine_id=VIBEVOICE_8BIT_PROVIDER_ID,
                mode="auto",
                reason="4bit 不可用，已安装 8bit 且本机内存满足联合识别门槛",
            )

    return AsrSelection(
        engine_id=DEFAULT_FAST_ASR_ENGINE_ID,
        diarization_engine_id="auto" if needs_speaker_diarization else None,
        mode="auto",
        reason=(
            "短音频或单说话人任务优先使用更快的 Qwen"
            if not needs_speaker_diarization
            else "VibeVoice 不满足安装或内存条件，保留 Qwen + MOSS 兼容流程"
        ),
    )


def _explicit_selection(engine_id: str, needs_speaker_diarization: bool) -> AsrSelection:
    return AsrSelection(
        engine_id=engine_id,
        diarization_engine_id=(
            engine_id
            if needs_speaker_diarization and engine_id in VIBEVOICE_PROVIDER_IDS
            else "auto"
            if needs_speaker_diarization
            else None
        ),
        mode="manual",
        reason="使用设置中指定的 ASR 引擎",
    )


def _variant_ready(variant_id: vibevoice_model.VariantId) -> bool:
    status = vibevoice_model.installation_status(variant_id)
    if not status["installed"]:
        return False
    provider_id = f"vibevoice-asr-mlx-{variant_id}"
    return engine_registry.health_check(provider_id).get("healthy") is True
