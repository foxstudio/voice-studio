from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from fastapi import UploadFile

from app.domains.video_localization.asr_pipeline import (
    AtomicSnapshotCallback,
    AsrInitialAnalysisSnapshot,
    AsrPipelineInput,
    AsrRunContext,
    DEFAULT_ASR_PIPELINE,
)
from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization import media_assets, media_health
from app.domains.video_localization import (
    asr_timing_contracts,
    project_manifest,
    speaker_diarization,
    speakers,
    subtitle_segmentation,
    transcription,
)
from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationCue, VideoLocalizationDraft, now_iso
from app.services import asr_service, audio_tools

EnglishAsrSourceTrackId = Literal["auto", "original", "vocals"]
ResolvedEnglishAsrSourceTrackId = Literal["original", "vocals"]
DEFAULT_ENGLISH_ASR_ENGINE_ID = "qwen3-asr-mlx"
SUPPORTED_SOURCE_LANGUAGES = {"auto", "en", "zh"}


@dataclass(frozen=True)
class ReusableStemSeparation:
    source_audio_sha256: str
    vocals_clean_path: Path
    vocals_clean_sha256: str
    background_path: Path
    background_sha256: str
    separation_engine_id: str
    quality_flags: tuple[str, ...]


_ENGLISH_ASR_METADATA_KEYS = {
    "english_asr_status",
    "english_asr_engine_id",
    "english_asr_source_track_id",
    "english_asr_language",
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
    "english_asr_diarization_status",
    "english_asr_diarization_engine_id",
    "english_asr_speaker_count",
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
    video_meta = await asyncio.to_thread(media_assets.probe_video, source_path)
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
    resolution = media_health.inspect_project_media(draft)
    video_path = resolution.paths.source_video
    if video_path is None:
        if resolution.health.source_video.status == "unconfigured":
            raise AppException(
                400, "VIDEO_LOCALIZATION_SOURCE_MISSING", "Import a source video before extracting audio"
            )
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_NOT_FOUND", "Source video file is missing")

    audio_dir = media_assets.ensure_project_video_localization_dir(project_id) / "audio"
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


def with_separated_source_audio(
    project_id: str,
    draft: VideoLocalizationDraft,
    *,
    output_prefix: str | None = None,
) -> VideoLocalizationDraft:
    resolution = media_health.inspect_project_media(draft)
    audio_path = resolution.paths.source_audio
    if audio_path is None:
        if resolution.health.source_audio.status == "unconfigured":
            raise AppException(
                400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING", "Extract source audio before running stem separation"
            )
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND", "Source audio file is missing")

    stems_dir = media_assets.ensure_project_video_localization_dir(project_id) / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    separation = (
        media_assets.separate_audio_file(
            audio_path,
            stems_dir,
            output_prefix=output_prefix,
        )
        if output_prefix is not None
        else media_assets.separate_audio_file(
            audio_path,
            stems_dir,
        )
    )
    vocals_clean_sha256 = media_assets.file_sha256(separation["vocals_clean_path"])
    background_sha256 = media_assets.file_sha256(separation["background_path"])
    stems = draft.stems.model_copy(
        update={
            "vocals_clean_path": str(separation["vocals_clean_path"]),
            "background_path": str(separation["background_path"]),
            "original_audio_path": str(audio_path),
            "separation_engine_id": separation["engine_id"],
            "separation_status": "completed",
            "quality_flags": separation.get("quality_flags", []),
            "original_audio_sha256": (
                draft.source_media.audio_sha256
                if resolution.health.source_audio.selected_source == "source_media"
                else draft.stems.original_audio_sha256
            )
            or media_assets.file_sha256(audio_path),
            "vocals_clean_sha256": vocals_clean_sha256,
            "background_sha256": background_sha256,
        }
    )
    return draft.model_copy(update={"stems": stems})


def with_reused_separated_source_audio(
    draft: VideoLocalizationDraft,
    reusable: ReusableStemSeparation,
) -> VideoLocalizationDraft:
    """Reattach one operation-owned, hash-verified separation result."""

    resolution = media_health.inspect_project_media(draft)
    audio_path = resolution.paths.source_audio
    if audio_path is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SOURCE_AUDIO_CHANGED",
            "源音轨已不可用，不能恢复之前完成的分轨结果。",
        )
    if media_assets.file_sha256(audio_path) != reusable.source_audio_sha256:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SOURCE_AUDIO_CHANGED",
            "源音轨已经变化，不能恢复之前完成的分轨结果。",
        )
    for path, expected_sha256 in (
        (
            reusable.vocals_clean_path,
            reusable.vocals_clean_sha256,
        ),
        (
            reusable.background_path,
            reusable.background_sha256,
        ),
    ):
        if not path.is_file() or media_assets.file_sha256(path) != expected_sha256:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_STEM_MEDIA_INVALID",
                "已完成的分轨媒体缺失或校验失败，需要重新生成。",
            )
    stems = draft.stems.model_copy(
        update={
            "vocals_clean_path": str(reusable.vocals_clean_path),
            "background_path": str(reusable.background_path),
            "original_audio_path": str(audio_path),
            "separation_engine_id": (reusable.separation_engine_id),
            "separation_status": "completed",
            "quality_flags": list(reusable.quality_flags),
            "original_audio_sha256": (reusable.source_audio_sha256),
            "vocals_clean_sha256": (reusable.vocals_clean_sha256),
            "background_sha256": (reusable.background_sha256),
        }
    )
    return draft.model_copy(update={"stems": stems})


def source_audio_content_fingerprint(
    draft: VideoLocalizationDraft,
) -> str:
    resolution = media_health.inspect_project_media(draft)
    audio_path = resolution.paths.source_audio
    if audio_path is None:
        if resolution.health.source_audio.status == "unconfigured":
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING",
                "Extract source audio before running stem separation",
            )
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND",
            "Source audio file is missing",
        )
    return media_assets.file_sha256(audio_path)


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
            "localization_state": {},
            "quality_gate": type(draft.quality_gate)(),
            "operations": [],
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
    resolution = media_health.inspect_project_media(draft)
    if resolved_track == "vocals":
        return _expect_resolved_media_path(resolution.paths.vocals), "vocals"
    return _expect_resolved_media_path(resolution.paths.source_audio), "original"


def resolve_english_alignment_source(
    draft: VideoLocalizationDraft,
    asr_audio_path: Path,
    asr_source_track_id: ResolvedEnglishAsrSourceTrackId,
) -> tuple[Path, ResolvedEnglishAsrSourceTrackId]:
    del draft
    return asr_audio_path, asr_source_track_id


def validate_english_asr_source(
    draft: VideoLocalizationDraft,
    source_track_id: EnglishAsrSourceTrackId | str = "auto",
) -> ResolvedEnglishAsrSourceTrackId:
    requested_track = str(source_track_id or "auto").strip().lower()
    if requested_track not in {"auto", "original", "vocals"}:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_ASR_SOURCE_TRACK_UNSUPPORTED",
            "源语言字幕听写仅支持 auto、original 或 vocals；合成配音字幕请使用独立工作流。",
        )

    resolution = media_health.inspect_project_media(draft)
    if requested_track == "vocals":
        _required_resolved_asr_source(
            resolution.paths.vocals,
            status=resolution.health.vocals.status,
            missing_code="VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING",
            missing_message="请先完成人声分离，再从纯人声轨执行字幕听写。",
            not_found_code="VIDEO_LOCALIZATION_CLEAN_VOCALS_NOT_FOUND",
            not_found_message="纯人声轨文件不存在，请重新执行人声分离。",
        )
        return "vocals"
    if requested_track == "original":
        _required_resolved_asr_source(
            resolution.paths.source_audio,
            status=resolution.health.source_audio.status,
            missing_code="VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING",
            missing_message="请先提取源音轨，再执行字幕听写。",
            not_found_code="VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND",
            not_found_message="源音轨文件不存在，请重新提取。",
        )
        return "original"

    _required_resolved_asr_source(
        resolution.paths.vocals,
        status=resolution.health.vocals.status,
        missing_code="VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING",
        missing_message="默认听写只使用分离人声，请先完成人声分离。若要改用原音轨，请明确选择 original。",
        not_found_code="VIDEO_LOCALIZATION_CLEAN_VOCALS_NOT_FOUND",
        not_found_message="纯人声轨文件不存在，请重新执行人声分离。若要改用原音轨，请明确选择 original。",
    )
    return "vocals"


def with_english_asr(
    draft: VideoLocalizationDraft,
    engine_id: str = DEFAULT_ENGLISH_ASR_ENGINE_ID,
    source_track_id: EnglishAsrSourceTrackId | str = "auto",
    source_language: str = "en",
    project_id: str | None = None,
    operation_id: str = "formal-workflow",
    segmentation_profile_id: str = subtitle_segmentation.DEFAULT_PROFILE_ID,
    llm_profile_id: str | None = None,
    vision_profile_id: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    preview_callback: Callable[[str, list[dict]], None] | None = None,
    report_callback: Callable[[str, dict], None] | None = None,
    atomic_snapshot_callback: AtomicSnapshotCallback | None = None,
    diarization_engine_id: str | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> VideoLocalizationDraft:
    resolved_track_id = validate_english_asr_source(draft, source_track_id)
    audio_path, resolved_track_id = resolve_english_asr_source(
        draft,
        resolved_track_id,
    )
    alignment_audio_path, alignment_track_id = resolve_english_alignment_source(
        draft,
        audio_path,
        resolved_track_id,
    )
    source_audio_sha256 = media_assets.file_sha256(audio_path)
    alignment_audio_sha256 = media_assets.file_sha256(alignment_audio_path)
    asr_language = normalize_source_language(
        source_language,
        default="en",
    )
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
    research_cache_dir = project_manifest.ensure_project_layout(project_id)["research_cache"] if project_id else None
    visual_evidence_dir = (
        media_assets.visual_evidence_frame_dir(
            project_id,
            operation_id,
        )
        if project_id
        else None
    )
    transcript = DEFAULT_ASR_PIPELINE.run_full(
        AsrPipelineInput(
            operation_id=operation_id,
            audio_path=str(audio_path),
            alignment_audio_path=str(alignment_audio_path),
            engine_id=engine_id,
            source_track_id=resolved_track_id,
            alignment_source_track_id=alignment_track_id,
            language=asr_language,
            duration_ms=asr_duration_ms,
            source_filename=draft.source_media.filename,
            source_video_path=draft.source_media.video_path,
            source_video_sha256=draft.source_media.content_sha256,
            source_video_duration_ms=(
                draft.source_media.duration_ms
                if draft.source_media.duration_ms and draft.source_media.duration_ms > 0
                else None
            ),
            source_video_frame_rate=(
                draft.source_media.frame_rate
                if draft.source_media.frame_rate and draft.source_media.frame_rate > 0
                else None
            ),
            llm_profile_id=llm_profile_id,
            vision_profile_id=vision_profile_id,
            visual_evidence_dir=(str(visual_evidence_dir) if visual_evidence_dir else None),
            glossary=draft.glossary,
            scene_context=_transcription_scene_context(draft),
            research_cache_dir=(str(research_cache_dir) if research_cache_dir else None),
            segmentation_profile_id=(subtitle_segmentation.resolve_profile(segmentation_profile_id).profile_id),
            existing_boundary_reviews=(
                previous_transcription.boundary_reviews if can_reuse_boundary_reviews and previous_transcription else []
            ),
            source_audio_sha256=source_audio_sha256,
            alignment_audio_sha256=alignment_audio_sha256,
            diarization_engine_id=diarization_engine_id,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        ),
        context=AsrRunContext(
            on_progress=progress_callback,
            is_cancelled=is_cancelled,
            on_preview=preview_callback,
            on_report=report_callback,
            on_atomic_snapshot=atomic_snapshot_callback,
        ),
    )
    subtitle_track_started_at = time.perf_counter()
    subtitle_track_input = asr_timing_contracts.AsrSubtitleTrackInput(
        transcription=transcript,
        existing_speakers=draft.speakers,
        source_duration_ms=(
            draft.source_media.duration_ms
            if draft.source_media.duration_ms and draft.source_media.duration_ms > 0
            else None
        ),
        video_frame_rate=(
            draft.source_media.frame_rate
            if draft.source_media.frame_rate and draft.source_media.frame_rate > 0
            else None
        ),
    )
    if atomic_snapshot_callback is not None:
        atomic_snapshot_callback(
            "subtitle_track_input",
            subtitle_track_input,
        )
    subtitle_track_result = run_subtitle_track_step(subtitle_track_input)
    if atomic_snapshot_callback is not None:
        atomic_snapshot_callback(
            "subtitle_track_result",
            subtitle_track_result,
        )
    transcript = subtitle_track_result.transcription
    next_speakers = subtitle_track_result.speakers
    generated_cues = subtitle_track_result.cues
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
    pipeline_timing["total_duration_ms"] = (
        int(pipeline_timing.get("total_duration_ms") or 0) + subtitle_track_duration_ms
    )
    transcript = transcript.model_copy(update={"pipeline_timing": pipeline_timing})

    source_media = draft.source_media.model_copy(
        update={
            "metadata": {
                **draft.source_media.metadata,
                "english_asr_status": "completed",
                "english_asr_engine_id": engine_id,
                "english_asr_source_track_id": resolved_track_id,
                "english_asr_language": transcript.language,
                "english_asr_alignment_source_track_id": alignment_track_id,
                "english_asr_segment_count": len(generated_cues),
                "english_asr_raw_segment_count": len(transcript.segments),
                "english_asr_word_count": len(transcript.words),
                "english_asr_diarization_status": transcript.diarization_status,
                "english_asr_diarization_engine_id": transcript.diarization_engine_id,
                "english_asr_speaker_count": len(transcript.speaker_clusters),
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
            "speakers": next_speakers,
            "cues": _ordered_cues(generated_cues),
        }
    )


def run_subtitle_track_step(
    request: asr_timing_contracts.AsrSubtitleTrackInput,
) -> asr_timing_contracts.AsrSubtitleTrackResult:
    """Build the source subtitle track without writing project state."""

    transcript = request.transcription.model_copy(deep=True)
    generated_cues = subtitle_segmentation.cues_from_transcription(
        transcript,
        existing_cue_ids=set(),
        profile_id=transcript.segmentation_profile_id,
        frame_rate=request.video_frame_rate,
        media_duration_ms=request.source_duration_ms,
    )
    speaker_draft = VideoLocalizationDraft(speakers=[item.model_copy(deep=True) for item in request.existing_speakers])
    next_speakers, generated_cues, bound_clusters = speakers.bind_diarization_clusters(
        speaker_draft,
        generated_cues,
        transcript.speaker_clusters,
    )
    if bound_clusters != transcript.speaker_clusters:
        transcript = transcript.model_copy(update={"speaker_clusters": bound_clusters})
    return asr_timing_contracts.AsrSubtitleTrackResult(
        input=request.model_copy(deep=True),
        transcription=transcript,
        speakers=next_speakers,
        cues=generated_cues,
    )


def transcribe_raw(
    draft: VideoLocalizationDraft,
    engine_id: str = DEFAULT_ENGLISH_ASR_ENGINE_ID,
    source_track_id: EnglishAsrSourceTrackId | str = "auto",
    source_language: str = "en",
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> transcription.TranscribeRawOutput:
    """Run the first ASR subtask without mutating the video-localization draft."""

    with prepared_raw_asr_request(
        draft,
        engine_id=engine_id,
        source_track_id=source_track_id,
        source_language=source_language,
    ) as request:
        return DEFAULT_ASR_PIPELINE.run_raw_asr(
            request,
            context=AsrRunContext(is_cancelled=is_cancelled),
        )


@contextmanager
def prepared_raw_asr_request(
    draft: VideoLocalizationDraft,
    engine_id: str = DEFAULT_ENGLISH_ASR_ENGINE_ID,
    source_track_id: EnglishAsrSourceTrackId | str = "auto",
    source_language: str = "en",
) -> Iterator[transcription.TranscribeRawInput]:
    """Resolve and fingerprint one runtime ASR input before inference."""

    resolved_track_id = validate_english_asr_source(draft, source_track_id)
    audio_path, resolved_track_id = resolve_english_asr_source(
        draft,
        resolved_track_id,
    )
    yield transcription.TranscribeRawInput(
        audio_path=str(audio_path),
        audio_sha256=media_assets.file_sha256(audio_path),
        engine_id=engine_id,
        source_track_id=resolved_track_id,
        requested_language=normalize_source_language(
            source_language,
            default="en",
        ),
        duration_ms=draft.source_media.duration_ms,
        context_terms=transcription.automatic_asr_context_terms(
            source_filename=draft.source_media.filename,
            source_video_path=draft.source_media.video_path,
            glossary=draft.glossary,
        ),
    )


def analyze_initial_speech(
    draft: VideoLocalizationDraft,
    asr_engine_id: str = DEFAULT_ENGLISH_ASR_ENGINE_ID,
    diarization_engine_id: str = "auto",
    source_track_id: EnglishAsrSourceTrackId | str = "auto",
    source_language: str = "en",
    *,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> AsrInitialAnalysisSnapshot:
    """Run the formal ASR pipeline's two independent first branches in parallel."""

    with prepared_initial_analysis_requests(
        draft,
        asr_engine_id=asr_engine_id,
        diarization_engine_id=diarization_engine_id,
        source_track_id=source_track_id,
        source_language=source_language,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
    ) as (raw_request, diarization_request):
        analysis = DEFAULT_ASR_PIPELINE.run_initial_analysis(
            raw_asr=raw_request,
            diarization=diarization_request,
            context=AsrRunContext(is_cancelled=is_cancelled),
        )
        return DEFAULT_ASR_PIPELINE.snapshot_initial_analysis(analysis)


@contextmanager
def prepared_initial_analysis_requests(
    draft: VideoLocalizationDraft,
    asr_engine_id: str = DEFAULT_ENGLISH_ASR_ENGINE_ID,
    diarization_engine_id: str = "auto",
    source_track_id: EnglishAsrSourceTrackId | str = "auto",
    source_language: str = "en",
    *,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> Iterator[
    tuple[
        transcription.TranscribeRawInput,
        speaker_diarization.DiarizeSpeakersInput,
    ]
]:
    """Resolve one runtime audio file for both parallel initial branches."""

    resolved_track_id = validate_english_asr_source(draft, source_track_id)
    audio_path, resolved_track_id = resolve_english_asr_source(
        draft,
        resolved_track_id,
    )
    requested_language = normalize_source_language(
        source_language,
        default="en",
    )
    duration_ms = draft.source_media.duration_ms
    audio_sha256 = media_assets.file_sha256(audio_path)
    yield (
        transcription.TranscribeRawInput(
            audio_path=str(audio_path),
            audio_sha256=audio_sha256,
            engine_id=asr_engine_id,
            source_track_id=resolved_track_id,
            requested_language=requested_language,
            duration_ms=duration_ms,
            context_terms=transcription.automatic_asr_context_terms(
                source_filename=draft.source_media.filename,
                source_video_path=draft.source_media.video_path,
                glossary=draft.glossary,
            ),
        ),
        speaker_diarization.DiarizeSpeakersInput(
            audio_path=str(audio_path),
            audio_sha256=audio_sha256,
            source_track_id=resolved_track_id,
            engine_id=diarization_engine_id,
            duration_ms=duration_ms,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        ),
    )


def diarize_speakers(
    draft: VideoLocalizationDraft,
    engine_id: str = "auto",
    source_track_id: EnglishAsrSourceTrackId | str = "auto",
    *,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> speaker_diarization.DiarizeSpeakersOutput:
    """Run the audio-only speaker subtask without mutating the draft."""

    with prepared_speaker_diarization_request(
        draft,
        engine_id=engine_id,
        source_track_id=source_track_id,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
    ) as request:
        return DEFAULT_ASR_PIPELINE.run_speaker_diarization(
            request,
            context=AsrRunContext(is_cancelled=is_cancelled),
        )


@contextmanager
def prepared_speaker_diarization_request(
    draft: VideoLocalizationDraft,
    engine_id: str = "auto",
    source_track_id: EnglishAsrSourceTrackId | str = "auto",
    *,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> Iterator[speaker_diarization.DiarizeSpeakersInput]:
    """Resolve and fingerprint one runtime input before local inference."""

    resolved_track_id = validate_english_asr_source(
        draft,
        source_track_id,
    )
    audio_path, resolved_track_id = resolve_english_asr_source(
        draft,
        resolved_track_id,
    )
    yield speaker_diarization.DiarizeSpeakersInput(
        audio_path=str(audio_path),
        audio_sha256=media_assets.file_sha256(audio_path),
        source_track_id=resolved_track_id,
        engine_id=engine_id,
        duration_ms=draft.source_media.duration_ms,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
    )


def merge_english_asr_result(
    latest: VideoLocalizationDraft,
    result: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Apply a completed ASR result as the sole source-subtitle track."""
    transcript = result.transcription
    if transcript is None:
        raise AppException(500, "VIDEO_LOCALIZATION_ASR_RESULT_INVALID", "语音识别结果缺少转录数据。")

    generated_cues = subtitle_segmentation.cues_from_transcription(
        transcript,
        existing_cue_ids=set(),
        profile_id=transcript.segmentation_profile_id,
        frame_rate=latest.source_media.frame_rate,
        media_duration_ms=latest.source_media.duration_ms,
    )
    if not generated_cues:
        raise AppException(
            400, "VIDEO_LOCALIZATION_ASR_EMPTY", "语音识别没有返回有效的字幕文本，请检查音轨内容或更换识别引擎后重试。"
        )
    next_speakers, generated_cues, bound_clusters = speakers.bind_diarization_clusters(
        latest,
        generated_cues,
        transcript.speaker_clusters,
    )
    if bound_clusters != transcript.speaker_clusters:
        transcript = transcript.model_copy(update={"speaker_clusters": bound_clusters})

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
            "speakers": next_speakers,
            "cues": _ordered_cues(generated_cues),
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


def _required_resolved_asr_source(
    path: Path | None,
    *,
    status: media_health.MediaAssetStatus,
    missing_code: str,
    missing_message: str,
    not_found_code: str,
    not_found_message: str,
) -> Path:
    if path is not None:
        return path
    if status == "unconfigured":
        raise AppException(400, missing_code, missing_message)
    raise AppException(400, not_found_code, not_found_message)


def _expect_resolved_media_path(path: Path | None) -> Path:
    if path is None:
        raise RuntimeError("validated media source was not resolved")
    return path
