"""Compatibility aliases for shared LLM observability contracts."""

from __future__ import annotations

from app.schemas.video_localization_llm_observability import (
    VideoLocalizationLlmCallPurpose,
    VideoLocalizationLlmCallRecord,
)


AsrLlmCallPurpose = VideoLocalizationLlmCallPurpose
AsrLlmCallRecord = VideoLocalizationLlmCallRecord


__all__ = [
    "AsrLlmCallPurpose",
    "AsrLlmCallRecord",
    "VideoLocalizationLlmCallPurpose",
]
