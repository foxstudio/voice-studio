"""Shared integrity checks for managed direct-reference audio clips."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from app.services import audio_tools, voice_store

DIRECT_REFERENCE_ENGINE_IDS = {
    "indextts-v2",
    "omnivoice",
    "confucius4-mlx-int8",
    "qwen3-tts-mlx-0.6b",
    "mimo-v2.5-tts-voiceclone",
    "f5-tts",
    "cosyvoice-zero-shot",
}
CLIP_DURATION_ABSOLUTE_TOLERANCE_MS = 150
CLIP_DURATION_RELATIVE_TOLERANCE = 0.03


class ReferenceAudioIntegrityError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def request_values(request: Any) -> dict[str, Any]:
    if hasattr(request, "model_dump"):
        return dict(request.model_dump())
    if isinstance(request, Mapping):
        return dict(request)
    raise TypeError("reference audio request must be a model or mapping")


def resolve_primary_reference(request: Any) -> str | None:
    """Resolve and validate one request's effective primary reference audio."""

    values = request_values(request)
    explicit_path = values.get("reference_audio_path")
    if explicit_path:
        path = Path(str(explicit_path)).expanduser()
        if not path.exists():
            raise ReferenceAudioIntegrityError("REFERENCE_AUDIO_NOT_FOUND", "指定的参考音频不存在")
        resolved = str(path)
    else:
        resolved = voice_store.reference_path(values.get("voice_id"))

    if resolved and str(values.get("engine_id") or "") in DIRECT_REFERENCE_ENGINE_IDS:
        validate_reference_clip(
            resolved,
            source_duration_ms=values.get("custom_reference_source_duration_ms"),
            trim_start_ms=values.get("custom_reference_trim_start_ms"),
            trim_end_ms=values.get("custom_reference_trim_end_ms"),
        )
    return resolved


def validate_reference_clip(
    path: str | Path,
    *,
    source_duration_ms: int | None,
    trim_start_ms: int | None,
    trim_end_ms: int | None,
    code_prefix: str = "REFERENCE_AUDIO",
    label: str = "参考音频",
) -> int | None:
    """Validate a materialized clip when its selection metadata is available.

    Older history rows and library voices do not carry trim metadata. They stay
    compatible and are left to the engine's existing format/runtime checks.
    """

    validate_trim_contract(
        source_duration_ms=source_duration_ms,
        trim_start_ms=trim_start_ms,
        trim_end_ms=trim_end_ms,
        code_prefix=code_prefix,
        label=label,
    )
    if trim_start_ms is None and trim_end_ms is None:
        return None

    audio_path = Path(path).expanduser()
    if not audio_path.exists():
        raise ReferenceAudioIntegrityError(f"{code_prefix}_NOT_FOUND", f"指定的{label}不存在")
    try:
        actual_duration_ms = int(audio_tools.probe_audio(audio_path).get("duration_ms") or 0)
    except Exception as exc:
        raise ReferenceAudioIntegrityError(
            f"{code_prefix}_INVALID",
            f"无法读取{label}，请重新裁切或上传可播放的音频",
        ) from exc

    expected_duration_ms = int(trim_end_ms) - int(trim_start_ms)
    tolerance_ms = max(
        CLIP_DURATION_ABSOLUTE_TOLERANCE_MS,
        round(expected_duration_ms * CLIP_DURATION_RELATIVE_TOLERANCE),
    )
    if abs(actual_duration_ms - expected_duration_ms) > tolerance_ms:
        raise ReferenceAudioIntegrityError(
            f"{code_prefix}_CLIP_DURATION_MISMATCH",
            f"{label}实际时长与所选范围不一致，请重新应用选区",
        )
    return actual_duration_ms


def validate_trim_contract(
    *,
    source_duration_ms: int | None,
    trim_start_ms: int | None,
    trim_end_ms: int | None,
    code_prefix: str = "REFERENCE_AUDIO",
    label: str = "参考音频",
) -> None:
    if (trim_start_ms is None) != (trim_end_ms is None):
        raise ReferenceAudioIntegrityError(
            f"{code_prefix}_RANGE_INVALID",
            f"{label}裁切入点和出点必须同时提供",
        )
    if trim_start_ms is None:
        return
    start_ms = int(trim_start_ms)
    end_ms = int(trim_end_ms)
    if start_ms < 0 or end_ms <= start_ms:
        raise ReferenceAudioIntegrityError(
            f"{code_prefix}_RANGE_INVALID",
            f"{label}裁切出点必须大于入点",
        )
    if source_duration_ms is not None and end_ms > int(source_duration_ms):
        raise ReferenceAudioIntegrityError(
            f"{code_prefix}_RANGE_INVALID",
            f"{label}裁切出点不能超过源音频时长",
        )
