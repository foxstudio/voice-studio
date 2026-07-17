from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import Callable, Literal

import numpy as np
from fastapi import UploadFile

from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization import media_assets
from app.domains.video_localization import project_manifest, subtitle_segmentation, transcription
from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationCue, VideoLocalizationDraft, now_iso
from app.services import asr_service, audio_tools

EnglishAsrSourceTrackId = Literal["auto", "original", "vocals", "dub"]
ResolvedEnglishAsrSourceTrackId = Literal["original", "vocals", "dub"]
DEFAULT_ENGLISH_ASR_ENGINE_ID = "qwen3-asr-mlx"
SUPPORTED_SOURCE_LANGUAGES = {"auto", "en", "zh"}

_ENGLISH_ASR_METADATA_KEYS = {
    "english_asr_status",
    "english_asr_engine_id",
    "english_asr_source_track_id",
    "english_asr_language",
    "english_asr_source_state_sha256",
    "english_asr_alignment_source_track_id",
    "english_asr_segment_count",
    "english_asr_raw_segment_count",
    "english_asr_word_count",
    "english_asr_review_status",
    "english_asr_alignment_status",
    "english_asr_timing_confidence",
    "english_asr_audio_boundary_status",
    "english_asr_audio_boundary_count",
    "english_asr_audio_boundary_analysis_version",
    "english_asr_boundary_review_status",
    "english_asr_boundary_review_count",
    "english_asr_boundary_review_prompt_version",
    "english_asr_pipeline_version",
    "english_asr_segmentation_profile_id",
    "english_asr_completed_at",
}


def normalize_source_language(value: str | None, *, default: str = "auto") -> str:
    language = str(value or default).strip().lower()
    aliases = {"english": "en", "英文": "en", "chinese": "zh", "中文": "zh"}
    language = aliases.get(language, language)
    if language not in SUPPORTED_SOURCE_LANGUAGES:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_SOURCE_LANGUAGE_UNSUPPORTED",
            "当前字幕听写支持自动识别、英语或中文。",
        )
    return language


async def with_imported_source_media(
    project_id: str, draft: VideoLocalizationDraft, file: UploadFile
) -> VideoLocalizationDraft:
    source_path, size_bytes, content_sha256 = await media_assets.save_uploaded_video(project_id, file)
    video_meta = media_assets.probe_video(source_path)
    draft = _without_source_derivatives(draft)
    source_media = draft.source_media.model_copy(
        update={
            "filename": file.filename or source_path.name,
            "video_path": str(source_path),
            "size_bytes": size_bytes,
            "duration_ms": video_meta.get("duration_ms"),
            "width": video_meta.get("width"),
            "height": video_meta.get("height"),
            "frame_rate": video_meta.get("frame_rate"),
            "imported_at": now_iso(),
            "content_sha256": content_sha256,
            "audio_path": None,
            "audio_sha256": None,
            "metadata": {
                "content_type": file.content_type,
                "upload_status": "stored",
                "probe_status": "completed" if video_meta else "unavailable",
            },
        }
    )
    return draft.model_copy(update={"source_media": source_media, "status": "draft"})


def with_extracted_source_audio(project_id: str, draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    if not draft.source_media.video_path:
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_MISSING", "Import a source video before extracting audio")

    video_path = Path(draft.source_media.video_path)
    if not video_path.exists():
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_NOT_FOUND", "Source video file is missing")

    audio_dir = media_assets.project_video_localization_dir(project_id) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = media_assets.unique_path(audio_dir / f"{video_path.stem}-source.wav")
    audio_meta = media_assets.extract_audio_file(video_path, audio_path)
    audio_sha256 = media_assets.file_sha256(audio_path)
    source_media = draft.source_media.model_copy(
        update={
            "audio_path": str(audio_path),
            "duration_ms": draft.source_media.duration_ms or audio_meta.get("duration_ms"),
            "audio_sha256": audio_sha256,
            "metadata": {
                **draft.source_media.metadata,
                "audio_extract_status": "completed",
                "audio_sample_rate": audio_meta.get("sample_rate"),
                "audio_channels": audio_meta.get("channels"),
            },
        }
    )
    stems = draft.stems.model_copy(
        update={"original_audio_path": str(audio_path), "original_audio_sha256": audio_sha256}
    )
    return draft.model_copy(update={"source_media": source_media, "stems": stems})


def with_separated_source_audio(project_id: str, draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    audio_path_value = draft.source_media.audio_path or draft.stems.original_audio_path
    if not audio_path_value:
        raise AppException(
            400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING", "Extract source audio before running stem separation"
        )

    audio_path = Path(audio_path_value)
    if not audio_path.exists():
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND", "Source audio file is missing")

    stems_dir = media_assets.project_video_localization_dir(project_id) / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    separation = media_assets.separate_audio_file(audio_path, stems_dir)
    vocals_clean_sha256 = media_assets.file_sha256(separation["vocals_clean_path"])
    background_sha256 = media_assets.file_sha256(separation["background_path"])
    stems = draft.stems.model_copy(
        update={
            "vocals_clean_path": str(separation["vocals_clean_path"]),
            "background_path": str(separation["background_path"]),
            "original_audio_path": str(audio_path),
            "separation_engine_id": separation.get("engine_id", "demucs:htdemucs"),
            "separation_status": "completed",
            "quality_flags": separation.get("quality_flags", []),
            "original_audio_sha256": draft.source_media.audio_sha256 or media_assets.file_sha256(audio_path),
            "vocals_clean_sha256": vocals_clean_sha256,
            "background_sha256": background_sha256,
        }
    )
    return draft.model_copy(update={"stems": stems})


def _without_source_derivatives(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    return draft.model_copy(
        update={
            "status": "draft",
            "source_media": draft.source_media.model_copy(update={"metadata": {}}),
            "language_config": draft.language_config.model_copy(update={"detected_source_language": None}),
            "stems": type(draft.stems)(),
            "speakers": [],
            "reference_clips": [],
            "cues": [],
            "transcription": None,
            "localized_subtitles": [],
            "quality_gate": type(draft.quality_gate)(),
            "exports": type(draft.exports)(),
            "operations": [],
            "project_voice_samples": [],
            "voice_recipes": [],
            "generated_candidates": [],
            "timeline_clips": [],
        }
    )


def without_english_asr(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    metadata = {key: value for key, value in draft.source_media.metadata.items() if not key.startswith("english_asr_")}
    localized_subtitles = [
        subtitle.model_copy(update={"linked_cue_id": None}) for subtitle in draft.localized_subtitles
    ]
    return draft.model_copy(
        update={
            "source_media": draft.source_media.model_copy(update={"metadata": metadata}),
            "language_config": draft.language_config.model_copy(update={"detected_source_language": None}),
            "cues": [],
            "transcription": None,
            "localized_subtitles": localized_subtitles,
            "ui_state": {**draft.ui_state, "selected_cue_id": ""},
        }
    )


def resolve_english_asr_source(
    draft: VideoLocalizationDraft,
    source_track_id: EnglishAsrSourceTrackId | str = "auto",
) -> tuple[Path, ResolvedEnglishAsrSourceTrackId]:
    resolved_track = validate_english_asr_source(draft, source_track_id)
    if resolved_track == "dub":
        return _render_dub_asr_track(draft), "dub"

    original_value = draft.source_media.audio_path or draft.stems.original_audio_path
    vocals_value = draft.stems.vocals_clean_path
    if resolved_track == "vocals":
        return Path(str(vocals_value)), "vocals"
    return Path(str(original_value)), "original"


def resolve_english_alignment_source(
    draft: VideoLocalizationDraft,
    asr_audio_path: Path,
    asr_source_track_id: ResolvedEnglishAsrSourceTrackId,
) -> tuple[Path, ResolvedEnglishAsrSourceTrackId]:
    if asr_source_track_id != "vocals":
        return asr_audio_path, asr_source_track_id

    original_value = draft.source_media.audio_path or draft.stems.original_audio_path
    if original_value:
        original_path = Path(original_value)
        if original_path.exists():
            return original_path, "original"
    return asr_audio_path, asr_source_track_id


def validate_english_asr_source(
    draft: VideoLocalizationDraft,
    source_track_id: EnglishAsrSourceTrackId | str = "auto",
) -> ResolvedEnglishAsrSourceTrackId:
    requested_track = str(source_track_id or "auto").strip().lower()
    if requested_track not in {"auto", "original", "vocals", "dub"}:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_ASR_SOURCE_TRACK_UNSUPPORTED",
            "字幕听写音轨仅支持 auto、original、vocals 或 dub。",
        )

    original_value = draft.source_media.audio_path or draft.stems.original_audio_path
    vocals_value = draft.stems.vocals_clean_path
    if requested_track == "dub":
        _validated_dub_clips(draft)
        return "dub"
    if requested_track == "vocals":
        _required_asr_source(
            vocals_value,
            missing_code="VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING",
            missing_message="请先完成人声分离，再从纯人声轨执行字幕听写。",
            not_found_code="VIDEO_LOCALIZATION_CLEAN_VOCALS_NOT_FOUND",
            not_found_message="纯人声轨文件不存在，请重新执行人声分离。",
        )
        return "vocals"
    if requested_track == "original":
        _required_asr_source(
            original_value,
            missing_code="VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING",
            missing_message="请先提取源音轨，再执行字幕听写。",
            not_found_code="VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND",
            not_found_message="源音轨文件不存在，请重新提取。",
        )
        return "original"

    if vocals_value and Path(vocals_value).exists():
        return "vocals"
    _required_asr_source(
        original_value,
        missing_code="VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING",
        missing_message="请先提取源音轨，再执行字幕听写。",
        not_found_code="VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND",
        not_found_message="源音轨文件不存在，请重新提取。",
    )
    return "original"


def with_english_asr(
    draft: VideoLocalizationDraft,
    engine_id: str = DEFAULT_ENGLISH_ASR_ENGINE_ID,
    source_track_id: EnglishAsrSourceTrackId | str = "auto",
    source_language: str = "en",
    project_id: str | None = None,
    segmentation_profile_id: str = subtitle_segmentation.DEFAULT_PROFILE_ID,
    progress_callback: Callable[[float, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    preview_callback: Callable[[str, list[dict]], None] | None = None,
) -> VideoLocalizationDraft:
    resolved_track_id = validate_english_asr_source(draft, source_track_id)
    if resolved_track_id == "dub":
        source_state_sha256 = dub_asr_source_state_sha256(draft)
        audio_path = _render_dub_asr_track(draft)
    else:
        source_state_sha256 = None
        audio_path, resolved_track_id = resolve_english_asr_source(draft, resolved_track_id)
    alignment_audio_path, alignment_track_id = resolve_english_alignment_source(
        draft,
        audio_path,
        resolved_track_id,
    )
    try:
        source_audio_sha256 = media_assets.file_sha256(audio_path)
        alignment_audio_sha256 = media_assets.file_sha256(alignment_audio_path)
        requested_language = normalize_source_language(source_language, default="en")
        asr_language = "zh" if resolved_track_id == "dub" and requested_language == "auto" else requested_language
        previous_transcription = draft.transcription
        can_reuse_boundary_reviews = bool(
            previous_transcription
            and previous_transcription.source_audio_sha256 == source_audio_sha256
            and previous_transcription.alignment_audio_sha256 == alignment_audio_sha256
            and previous_transcription.engine_id == engine_id
            and previous_transcription.source_track_id == resolved_track_id
            and previous_transcription.alignment_source_track_id == alignment_track_id
            and (asr_language == "auto" or previous_transcription.language == asr_language)
        )
        asr_duration_ms = draft.source_media.duration_ms
        if resolved_track_id == "dub" and asr_duration_ms is None:
            asr_duration_ms = int(audio_tools.probe_audio(audio_path).get("duration_ms") or 0)
        transcript = transcription.transcribe_and_process(
            audio_path=audio_path,
            alignment_audio_path=alignment_audio_path,
            engine_id=engine_id,
            source_track_id=resolved_track_id,
            alignment_source_track_id=alignment_track_id,
            language=asr_language,
            duration_ms=asr_duration_ms,
            glossary=draft.glossary,
            scene_context=_transcription_scene_context(draft),
            research_cache_dir=(
                project_manifest.ensure_project_layout(project_id)["research_cache"] if project_id else None
            ),
            segmentation_profile_id=subtitle_segmentation.resolve_profile(segmentation_profile_id).profile_id,
            existing_boundary_reviews=previous_transcription.boundary_reviews
            if can_reuse_boundary_reviews and previous_transcription
            else None,
            source_audio_sha256=source_audio_sha256,
            alignment_audio_sha256=alignment_audio_sha256,
            progress_callback=progress_callback,
            is_cancelled=is_cancelled,
            preview_callback=preview_callback,
        )
    finally:
        if resolved_track_id == "dub":
            audio_path.unlink(missing_ok=True)
    subtitle_track_started_at = time.perf_counter()
    preserved_cues = [cue for cue in draft.cues if not cue_tools.is_replaceable_asr_candidate(cue)]
    generated_cues = subtitle_segmentation.cues_from_transcription(
        transcript,
        existing_cue_ids={cue.cue_id for cue in preserved_cues},
        profile_id=transcript.segmentation_profile_id,
    )
    if not generated_cues:
        raise AppException(
            400, "VIDEO_LOCALIZATION_ASR_EMPTY", "语音识别没有返回有效的字幕文本，请检查音轨内容或更换识别引擎后重试。"
        )
    if preview_callback:
        preview_callback(
            "timing_segmentation",
            [
                {
                    "cue_id": cue.cue_id,
                    "start_ms": cue.start_ms,
                    "end_ms": cue.end_ms,
                    "text": cue.en_subtitle_text or "",
                }
                for cue in generated_cues
                if cue.start_ms is not None and cue.end_ms is not None
            ],
        )

    subtitle_track_duration_ms = max(0, int(round((time.perf_counter() - subtitle_track_started_at) * 1000)))
    pipeline_timing = dict(transcript.pipeline_timing or {})
    pipeline_stages = dict(pipeline_timing.get("stages") or {})
    pipeline_stages["subtitle_track"] = {
        "duration_ms": subtitle_track_duration_ms,
        "cue_count": len(generated_cues),
    }
    pipeline_timing["stages"] = pipeline_stages
    pipeline_timing["total_duration_ms"] = int(pipeline_timing.get("total_duration_ms") or 0) + subtitle_track_duration_ms
    transcript = transcript.model_copy(update={"pipeline_timing": pipeline_timing})

    source_media = draft.source_media.model_copy(
        update={
            "metadata": {
                **draft.source_media.metadata,
                "english_asr_status": "completed",
                "english_asr_engine_id": engine_id,
                "english_asr_source_track_id": resolved_track_id,
                "english_asr_language": transcript.language,
                **({"english_asr_source_state_sha256": source_state_sha256} if source_state_sha256 is not None else {}),
                "english_asr_alignment_source_track_id": alignment_track_id,
                "english_asr_segment_count": len(generated_cues),
                "english_asr_raw_segment_count": len(transcript.segments),
                "english_asr_word_count": len(transcript.words),
                "english_asr_review_status": transcript.review_status,
                "english_asr_alignment_status": transcript.alignment_status,
                "english_asr_timing_confidence": transcript.timing_confidence,
                "english_asr_audio_boundary_status": transcript.audio_boundary_status,
                "english_asr_audio_boundary_count": len(transcript.audio_boundary_features),
                "english_asr_audio_boundary_analysis_version": transcript.audio_boundary_analysis_version,
                "english_asr_boundary_review_status": transcript.boundary_review_status,
                "english_asr_boundary_review_count": len(transcript.boundary_reviews),
                "english_asr_boundary_review_prompt_version": transcript.boundary_review_prompt_version,
                "english_asr_pipeline_version": "aligned-review-audio-v4",
                "english_asr_segmentation_profile_id": transcript.segmentation_profile_id,
                "english_asr_completed_at": now_iso(),
            }
        }
    )
    return draft.model_copy(
        update={
            "source_media": source_media,
            "language_config": draft.language_config.model_copy(
                update={"detected_source_language": transcript.language}
            ),
            "transcription": transcript,
            "cues": _ordered_cues([*preserved_cues, *generated_cues]),
        }
    )


def merge_english_asr_result(
    latest: VideoLocalizationDraft,
    result: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Apply a completed ASR result without replacing concurrent draft edits."""
    transcript = result.transcription
    if transcript is None:
        raise AppException(500, "VIDEO_LOCALIZATION_ASR_RESULT_INVALID", "语音识别结果缺少转录数据。")

    preserved_cues = [cue for cue in latest.cues if not cue_tools.is_replaceable_asr_candidate(cue)]
    generated_cues = subtitle_segmentation.cues_from_transcription(
        transcript,
        existing_cue_ids={cue.cue_id for cue in preserved_cues},
        profile_id=transcript.segmentation_profile_id,
    )
    if not generated_cues:
        raise AppException(
            400, "VIDEO_LOCALIZATION_ASR_EMPTY", "语音识别没有返回有效的字幕文本，请检查音轨内容或更换识别引擎后重试。"
        )

    result_metadata = result.source_media.metadata
    metadata = {
        **latest.source_media.metadata,
        **{key: result_metadata[key] for key in _ENGLISH_ASR_METADATA_KEYS if key in result_metadata},
    }
    metadata["english_asr_segment_count"] = len(generated_cues)
    source_media = latest.source_media.model_copy(update={"metadata": metadata})
    return latest.model_copy(
        update={
            "source_media": source_media,
            "language_config": latest.language_config.model_copy(
                update={"detected_source_language": transcript.language}
            ),
            "transcription": transcript,
            "cues": _ordered_cues([*preserved_cues, *generated_cues]),
        }
    )


def ensure_english_asr_source_unchanged(
    latest: VideoLocalizationDraft,
    result: VideoLocalizationDraft,
    *,
    expected_source_revision: str,
) -> None:
    """Reject a completed ASR result when any source used by the run changed."""
    if english_asr_source_revision(latest) != expected_source_revision:
        raise _asr_source_changed()
    transcript = result.transcription
    if transcript is None or not transcript.source_track_id:
        raise AppException(500, "VIDEO_LOCALIZATION_ASR_RESULT_INVALID", "语音识别结果缺少来源音轨信息。")

    source_track_id = transcript.source_track_id
    if source_track_id == "dub":
        expected_state = result.source_media.metadata.get("english_asr_source_state_sha256")
        try:
            current_state = dub_asr_source_state_sha256(latest)
        except AppException as exc:
            raise _asr_source_changed() from exc
        if not expected_state or current_state != expected_state:
            raise _asr_source_changed()
        return

    # Legacy/imported test results may not carry content fingerprints. The
    # structural source revision above still prevents cross-media writeback.
    if not transcript.source_audio_sha256 or not transcript.alignment_audio_sha256:
        return

    try:
        source_path, current_source_track_id = resolve_english_asr_source(latest, source_track_id)
        alignment_path, current_alignment_track_id = resolve_english_alignment_source(
            latest,
            source_path,
            current_source_track_id,
        )
        current_source_sha256 = media_assets.file_sha256(source_path)
        current_alignment_sha256 = media_assets.file_sha256(alignment_path)
    except (AppException, OSError) as exc:
        raise _asr_source_changed() from exc

    if (
        current_source_track_id != source_track_id
        or current_alignment_track_id != transcript.alignment_source_track_id
        or current_source_sha256 != transcript.source_audio_sha256
        or current_alignment_sha256 != transcript.alignment_audio_sha256
    ):
        raise _asr_source_changed()


def _asr_source_changed() -> AppException:
    return AppException(
        409,
        "VIDEO_LOCALIZATION_ASR_SOURCE_CHANGED",
        "听写期间视频或音轨发生了变化，因此没有覆盖当前字幕。请基于最新音轨重新生成 ASR 字幕。",
    )


def english_asr_source_revision(draft: VideoLocalizationDraft) -> str:
    """Fingerprint source-bearing draft fields without including UI/editor state."""
    payload = {
        "source_media": {
            "filename": draft.source_media.filename,
            "video_path": draft.source_media.video_path,
            "audio_path": draft.source_media.audio_path,
            "duration_ms": draft.source_media.duration_ms,
            "content_sha256": draft.source_media.content_sha256,
            "audio_sha256": draft.source_media.audio_sha256,
        },
        "stems": {
            "original_audio_path": draft.stems.original_audio_path,
            "original_audio_sha256": draft.stems.original_audio_sha256,
            "vocals_clean_path": draft.stems.vocals_clean_path,
            "vocals_clean_sha256": draft.stems.vocals_clean_sha256,
        },
        "dub_clips": [
            {
                "clip_id": str(clip.get("clip_id") or ""),
                "audio_path": str(clip.get("audio_path") or ""),
                "start_ms": clip.get("start_ms"),
                "end_ms": clip.get("end_ms"),
                "source_start_ms": clip.get("source_start_ms"),
                "source_end_ms": clip.get("source_end_ms"),
            }
            for item in draft.timeline_clips
            if (clip := dict(item)).get("track_id") == "dub"
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_video_revision(draft: VideoLocalizationDraft) -> str:
    return _revision_hash(
        {
            "video_path": draft.source_media.video_path,
            "content_sha256": draft.source_media.content_sha256,
            "duration_ms": draft.source_media.duration_ms,
        }
    )


def source_audio_revision(draft: VideoLocalizationDraft) -> str:
    return _revision_hash(
        {
            "audio_path": draft.source_media.audio_path,
            "audio_sha256": draft.source_media.audio_sha256,
            "original_audio_path": draft.stems.original_audio_path,
            "original_audio_sha256": draft.stems.original_audio_sha256,
        }
    )


def _revision_hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ordered_cues(cues: list[VideoLocalizationCue]) -> list[VideoLocalizationCue]:
    return sorted(
        cues,
        key=lambda cue: (
            cue.start_ms is None,
            cue.start_ms if cue.start_ms is not None else 0,
            cue.end_ms if cue.end_ms is not None else 0,
            cue.cue_id,
        ),
    )


def _transcription_scene_context(draft: VideoLocalizationDraft) -> str:
    parts = []
    if draft.source_media.filename:
        parts.append(f"源视频标题：{draft.source_media.filename}")
    if draft.scene_context.strip():
        parts.append(draft.scene_context.strip())
    return "\n".join(parts)


def _required_asr_source(
    value: str | None,
    *,
    missing_code: str,
    missing_message: str,
    not_found_code: str,
    not_found_message: str,
) -> Path:
    if not value:
        raise AppException(400, missing_code, missing_message)
    path = Path(value)
    if not path.exists():
        raise AppException(400, not_found_code, not_found_message)
    return path


def _validated_dub_clips(draft: VideoLocalizationDraft) -> list[dict]:
    clips = [dict(item) for item in draft.timeline_clips if dict(item).get("track_id") == "dub"]
    if not clips:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DUB_TRACK_MISSING",
            "时间线上没有可听写的合成配音轨，请先生成 dub 配音片段。",
        )
    for index, clip in enumerate(clips, start=1):
        clip_id = str(clip.get("clip_id") or f"第 {index} 个")
        audio_path_value = clip.get("audio_path")
        if not isinstance(audio_path_value, str) or not audio_path_value.strip():
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_CLIP_AUDIO_MISSING",
                f"dub 片段 {clip_id} 缺少有效的音频路径，请先完成配音生成。",
            )
        if not Path(audio_path_value).is_file():
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_CLIP_AUDIO_NOT_FOUND",
                f"dub 片段 {clip_id} 的音频文件不存在，请重新生成该片段。",
            )
        try:
            audio_tools.probe_audio(audio_path_value)
        except Exception as exc:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_CLIP_AUDIO_INVALID",
                f"dub 片段 {clip_id} 的音频文件无法读取，请重新生成该片段。",
            ) from exc
    return sorted(clips, key=lambda item: (_clip_ms(item, "start_ms", 0), str(item.get("clip_id") or "")))


def dub_asr_source_state_sha256(draft: VideoLocalizationDraft) -> str:
    """Fingerprint every input that changes the rendered dub transcription track."""
    clips = _validated_dub_clips(draft)
    normalized = []
    for clip in clips:
        audio_path = Path(str(clip["audio_path"]))
        source_duration_ms = int(audio_tools.probe_audio(audio_path).get("duration_ms") or 0)
        source_start_ms = _clip_ms(clip, "source_start_ms", 0)
        source_end_ms = _clip_ms(clip, "source_end_ms", source_duration_ms)
        start_ms = _clip_ms(clip, "start_ms", 0)
        end_ms = _clip_ms(clip, "end_ms", start_ms + max(0, source_end_ms - source_start_ms))
        normalized.append(
            {
                "clip_id": str(clip.get("clip_id") or ""),
                "audio_sha256": media_assets.file_sha256(audio_path),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_start_ms": source_start_ms,
                "source_end_ms": source_end_ms,
            }
        )
    payload = {
        "duration_ms": int(draft.source_media.duration_ms or 0),
        "clips": normalized,
    }
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _render_dub_asr_track(draft: VideoLocalizationDraft) -> Path:
    clips = _validated_dub_clips(draft)
    target_sr = 48000
    rendered: list[tuple[int, int, np.ndarray]] = []
    output_duration_ms = int(draft.source_media.duration_ms or 0)

    for clip in clips:
        source_path = Path(str(clip["audio_path"]))
        audio, sample_rate = audio_tools.read_audio(source_path)
        source_duration_ms = int(len(audio) / sample_rate * 1000) if sample_rate else 0
        timeline_start_ms = _clip_ms(clip, "start_ms", 0)
        source_start_ms = _clip_ms(clip, "source_start_ms", 0)
        source_end_ms = _clip_ms(clip, "source_end_ms", source_duration_ms)
        timeline_end_ms = _clip_ms(clip, "end_ms", timeline_start_ms + max(0, source_end_ms - source_start_ms))
        if (
            timeline_start_ms < 0
            or source_start_ms < 0
            or timeline_end_ms <= timeline_start_ms
            or source_end_ms <= source_start_ms
        ):
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_CLIP_RANGE_INVALID",
                f"dub 片段 {clip.get('clip_id') or ''} 的时间范围无效，请检查时间线和源音频入出点。",
            )

        source_start_frame = min(len(audio), int(sample_rate * source_start_ms / 1000))
        source_end_frame = min(len(audio), int(sample_rate * source_end_ms / 1000))
        timeline_duration_ms = timeline_end_ms - timeline_start_ms
        max_source_frames = int(sample_rate * timeline_duration_ms / 1000)
        segment = audio[source_start_frame : min(source_end_frame, source_start_frame + max_source_frames)]
        if not segment.size:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_CLIP_RANGE_INVALID",
                f"dub 片段 {clip.get('clip_id') or ''} 没有可听写的有效音频范围。",
            )
        rendered.append((timeline_start_ms, timeline_end_ms, _resample_audio(segment, sample_rate, target_sr)))
        output_duration_ms = max(output_duration_ms, timeline_end_ms)

    mixed = np.zeros(max(1, int(target_sr * max(output_duration_ms, 1) / 1000)), dtype=np.float32)
    for timeline_start_ms, timeline_end_ms, segment in rendered:
        start_frame = int(target_sr * timeline_start_ms / 1000)
        end_frame = min(len(mixed), int(target_sr * timeline_end_ms / 1000), start_frame + len(segment))
        if end_frame > start_frame:
            mixed[start_frame:end_frame] += segment[: end_frame - start_frame]
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.98:
        mixed *= 0.98 / peak

    handle = tempfile.NamedTemporaryFile(prefix="video-localization-dub-asr-", suffix=".wav", delete=False)
    output_path = Path(handle.name)
    handle.close()
    try:
        audio_tools.write_audio(output_path, mixed, target_sr, fmt="wav")
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return output_path


def _clip_ms(clip: dict, key: str, default: int) -> int:
    value = clip.get(key)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DUB_CLIP_RANGE_INVALID",
            f"dub 片段 {clip.get('clip_id') or ''} 的时间参数 {key} 无效。",
        ) from exc


def _resample_audio(audio: np.ndarray, sample_rate: int, target_sr: int) -> np.ndarray:
    if sample_rate == target_sr:
        return audio.astype(np.float32)
    output_length = max(1, int(len(audio) / sample_rate * target_sr))
    return np.interp(
        np.linspace(0, len(audio), output_length, endpoint=False),
        np.arange(len(audio)),
        audio,
    ).astype(np.float32)
