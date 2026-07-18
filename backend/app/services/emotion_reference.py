"""IndexTTS independent emotion-reference request policy and resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from app.services import voice_store

ENGINE_ID = "indextts-v2"
MODE = "emotion_reference"
REFERENCE_FIELDS = (
    "emotion_reference_voice_id",
    "emotion_reference_audio_path",
    "emotion_reference_source_audio_path",
    "emotion_reference_source_duration_ms",
    "emotion_reference_trim_start_ms",
    "emotion_reference_trim_end_ms",
)


class EmotionReferenceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SegmentEmotionReference:
    overrides_common: bool
    audio_path: str | None
    clears_emotion: bool = False


def mode_value(value: Any) -> str | None:
    if isinstance(value, Enum):
        value = value.value
    normalized = str(value or "").strip()
    return normalized or None


def request_values(request: Any) -> dict[str, Any]:
    if hasattr(request, "model_dump"):
        return dict(request.model_dump())
    if isinstance(request, Mapping):
        return dict(request)
    raise TypeError("emotion reference request must be a model or mapping")


def validate_values(*, engine_id: str, values: Mapping[str, Any], require_reference: bool = True) -> None:
    mode = mode_value(values.get("emotion_mode"))
    has_reference_fields = any(_is_nonempty(values.get(field)) for field in REFERENCE_FIELDS)
    if (mode == MODE or has_reference_fields) and engine_id != ENGINE_ID:
        raise EmotionReferenceError(
            "EMOTION_REFERENCE_UNSUPPORTED",
            "独立情绪参考当前只支持 IndexTTS v2",
        )
    if has_reference_fields and mode != MODE:
        raise EmotionReferenceError(
            "EMOTION_REFERENCE_MODE_REQUIRED",
            "提供独立情绪参考参数时必须选择独立情绪参考模式",
        )
    if mode != MODE:
        return
    if values.get("emotion") or values.get("emotion_values"):
        raise EmotionReferenceError(
            "EMOTION_REFERENCE_CONFLICT",
            "独立情绪参考不能与内置情绪或情绪向量同时使用",
        )
    if require_reference and not (values.get("emotion_reference_audio_path") or values.get("emotion_reference_voice_id")):
        raise EmotionReferenceError(
            "EMOTION_REFERENCE_REQUIRED",
            "独立情绪参考需要上传音频或选择音色库声音",
        )
    start_ms = values.get("emotion_reference_trim_start_ms")
    end_ms = values.get("emotion_reference_trim_end_ms")
    if (start_ms is None) != (end_ms is None):
        raise EmotionReferenceError(
            "EMOTION_REFERENCE_RANGE_INVALID",
            "情绪参考裁切入点和出点必须同时提供",
        )
    if start_ms is not None and end_ms is not None and int(end_ms) <= int(start_ms):
        raise EmotionReferenceError(
            "EMOTION_REFERENCE_RANGE_INVALID",
            "情绪参考裁切出点必须大于入点",
        )
    source_duration_ms = values.get("emotion_reference_source_duration_ms")
    if end_ms is not None and source_duration_ms is not None and int(end_ms) > int(source_duration_ms):
        raise EmotionReferenceError(
            "EMOTION_REFERENCE_RANGE_INVALID",
            "情绪参考裁切出点不能超过源音频时长",
        )


def validate_generate_request(request: Any) -> None:
    values = request_values(request)
    validate_values(engine_id=str(values.get("engine_id") or ""), values=values)


def resolve_values(
    *,
    engine_id: str,
    values: Mapping[str, Any],
    fallback_values: Mapping[str, Any] | None = None,
) -> str | None:
    mode = mode_value(values.get("emotion_mode"))
    if mode != MODE:
        validate_values(engine_id=engine_id, values=values, require_reference=False)
        return None

    effective = dict(fallback_values or {})
    effective.update({key: value for key, value in values.items() if value is not None})
    effective["emotion_mode"] = MODE
    validate_values(engine_id=engine_id, values=effective)

    explicit_path = effective.get("emotion_reference_audio_path")
    if explicit_path:
        path = Path(str(explicit_path)).expanduser()
        if path.is_file():
            return str(path)
        raise EmotionReferenceError(
            "EMOTION_REFERENCE_AUDIO_NOT_FOUND",
            "指定的情绪参考音频不存在",
        )

    voice_id = str(effective.get("emotion_reference_voice_id") or "").strip()
    resolved = voice_store.reference_path(voice_id)
    if resolved and Path(resolved).is_file():
        return resolved
    raise EmotionReferenceError(
        "EMOTION_REFERENCE_AUDIO_NOT_FOUND",
        "所选情绪参考音色没有可用的本地参考音频",
    )


def resolve_generate_request(request: Any) -> str | None:
    values = request_values(request)
    return resolve_values(engine_id=str(values.get("engine_id") or ""), values=values)


def resolve_batch_common(*, engine_id: str, parameters: Mapping[str, Any]) -> str | None:
    return resolve_values(engine_id=engine_id, values=parameters)


def validate_batch_request(request: Any) -> None:
    values = request_values(request)
    engine_id = str(values.get("engine_id") or "")
    common_parameters = dict(values.get("parameters") or {})
    common_audio = resolve_batch_common(engine_id=engine_id, parameters=common_parameters)
    for segment in values.get("segments") or []:
        segment_values = request_values(segment)
        segment_parameters = dict(segment_values.get("parameters") or {})
        if segment_values.get("emotion") is not None:
            segment_parameters["emotion"] = segment_values["emotion"]
        resolve_batch_segment(
            engine_id=engine_id,
            common_parameters=common_parameters,
            common_audio_path=common_audio,
            segment_parameters=segment_parameters,
        )


def resolve_batch_segment(
    *,
    engine_id: str,
    common_parameters: Mapping[str, Any],
    common_audio_path: str | None,
    segment_parameters: Mapping[str, Any],
) -> SegmentEmotionReference:
    segment_mode = mode_value(segment_parameters.get("emotion_mode"))
    if segment_mode is None:
        return SegmentEmotionReference(overrides_common=False, audio_path=common_audio_path)
    if segment_mode != MODE:
        validate_values(engine_id=engine_id, values=segment_parameters, require_reference=False)
        return SegmentEmotionReference(
            overrides_common=True,
            audio_path=None,
            clears_emotion=segment_mode == "follow_reference",
        )

    fallback = {field: common_parameters.get(field) for field in REFERENCE_FIELDS}
    if common_audio_path:
        fallback["emotion_reference_audio_path"] = common_audio_path
    resolved = resolve_values(
        engine_id=engine_id,
        values=segment_parameters,
        fallback_values=fallback,
    )
    return SegmentEmotionReference(overrides_common=True, audio_path=resolved, clears_emotion=True)


def _is_nonempty(value: Any) -> bool:
    return value is not None and value != ""
