from __future__ import annotations

from pathlib import Path
from typing import Literal

from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationDraft, VideoLocalizationOperation

OperationKind = Literal["source_audio", "stems", "english_asr", "reference_clips"]
OperationStatus = Literal["queued", "running", "success", "failed", "cancelled"]

ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"success", "failed", "cancelled"}
KIND_LABELS: dict[OperationKind, str] = {
    "source_audio": "抽取源音轨",
    "stems": "分离人声与背景声",
    "english_asr": "英文 ASR 转字幕",
    "reference_clips": "生成参考音候选",
}


def validate_prerequisites(kind: OperationKind, draft: VideoLocalizationDraft) -> None:
    if kind == "source_audio":
        if not draft.source_media.video_path:
            raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_MISSING", "Import a source video before extracting audio")
        if not Path(draft.source_media.video_path).exists():
            raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_NOT_FOUND", "Source video file is missing")
        return

    if kind in {"stems", "english_asr"}:
        audio_path_value = draft.source_media.audio_path or draft.stems.original_audio_path
        if not audio_path_value:
            raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING", "Extract source audio before running this operation")
        if not Path(audio_path_value).exists():
            raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND", "Source audio file is missing")
        return

    if kind == "reference_clips":
        if draft.stems.separation_status != "completed" or not draft.stems.vocals_clean_path:
            raise AppException(400, "VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING", "Separate clean vocals before creating reference clips")
        if not Path(draft.stems.vocals_clean_path).exists():
            raise AppException(400, "VIDEO_LOCALIZATION_CLEAN_VOCALS_NOT_FOUND", "Clean vocals file is missing")
        return


def active_operation_for_kind(draft: VideoLocalizationDraft, kind: OperationKind) -> VideoLocalizationOperation | None:
    return next((operation for operation in reversed(draft.operations) if operation.kind == kind and operation.status in ACTIVE_STATUSES), None)


def operation_from_draft(draft: VideoLocalizationDraft, operation_id: str) -> VideoLocalizationOperation | None:
    return next((operation for operation in draft.operations if operation.operation_id == operation_id), None)


def operation_was_cancelled(operation: VideoLocalizationOperation | None) -> bool:
    return bool(operation and (operation.cancel_requested or operation.status == "cancelled"))


def with_operation(draft: VideoLocalizationDraft, operation: VideoLocalizationOperation) -> VideoLocalizationDraft:
    return draft.model_copy(update={"operations": [*draft.operations, operation]})


def with_kind_status(
    draft: VideoLocalizationDraft,
    kind: OperationKind,
    status: OperationStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
) -> VideoLocalizationDraft:
    metadata = dict(draft.source_media.metadata)
    stems = draft.stems
    clears_errors = status in {"queued", "running", "success", "cancelled"}
    draft_status = _draft_status_for_operation_status(status)
    if kind == "source_audio":
        metadata["audio_extract_status"] = draft_status
        if clears_errors:
            metadata.pop("audio_extract_error_code", None)
            metadata.pop("audio_extract_error", None)
        if error_code:
            metadata["audio_extract_error_code"] = error_code
        if error_message:
            metadata["audio_extract_error"] = error_message
    elif kind == "english_asr":
        metadata["english_asr_status"] = draft_status
        if clears_errors:
            metadata.pop("english_asr_error_code", None)
            metadata.pop("english_asr_error", None)
        if error_code:
            metadata["english_asr_error_code"] = error_code
        if error_message:
            metadata["english_asr_error"] = error_message
    elif kind == "reference_clips":
        metadata["reference_clips_status"] = draft_status
        if clears_errors:
            metadata.pop("reference_clips_error_code", None)
            metadata.pop("reference_clips_error", None)
        if error_code:
            metadata["reference_clips_error_code"] = error_code
        if error_message:
            metadata["reference_clips_error"] = error_message
    elif kind == "stems":
        quality_flags = draft.stems.quality_flags
        if clears_errors:
            quality_flags = [flag for flag in quality_flags if not flag.startswith("VIDEO_LOCALIZATION_")]
        stems = draft.stems.model_copy(
            update={
                "separation_status": "running" if status in {"queued", "running"} else draft_status,
                "quality_flags": sorted(set([*quality_flags, error_code] if error_code else quality_flags)),
            }
        )
    source_media = draft.source_media.model_copy(update={"metadata": metadata})
    return draft.model_copy(update={"source_media": source_media, "stems": stems})


def source_audio_summary(draft: VideoLocalizationDraft | None) -> dict:
    if not draft:
        return {}
    return {
        "audio_path": draft.source_media.audio_path,
        "duration_ms": draft.source_media.duration_ms,
        "sample_rate": draft.source_media.metadata.get("audio_sample_rate"),
        "channels": draft.source_media.metadata.get("audio_channels"),
    }


def stems_summary(draft: VideoLocalizationDraft | None) -> dict:
    if not draft:
        return {}
    return {
        "vocals_clean_path": draft.stems.vocals_clean_path,
        "background_path": draft.stems.background_path,
        "separation_engine_id": draft.stems.separation_engine_id,
    }


def english_asr_summary(draft: VideoLocalizationDraft | None) -> dict:
    if not draft:
        return {}
    return {
        "engine_id": draft.source_media.metadata.get("english_asr_engine_id"),
        "segment_count": draft.source_media.metadata.get("english_asr_segment_count"),
        "cue_count": len(draft.cues),
    }


def reference_clips_summary(draft: VideoLocalizationDraft | None) -> dict:
    if not draft:
        return {}
    return {
        "reference_clip_count": len(draft.reference_clips),
        "clean_reference_count": len([clip for clip in draft.reference_clips if clip.cleanliness == "clean"]),
    }


def _draft_status_for_operation_status(status: OperationStatus | str) -> str:
    if status == "success":
        return "completed"
    return str(status)
