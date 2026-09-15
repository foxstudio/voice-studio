from __future__ import annotations

from typing import Any, Literal

from app.errors import AppException
from app.domains.video_localization import (
    asr_step_results,
    media_health,
    workflow_contracts,
)
from app.domains.video_localization.schemas import VideoLocalizationDraft, VideoLocalizationOperation

OperationKind = Literal[
    "source_audio",
    "stems",
    "english_asr",
    "dub_subtitle_generation",
    "speaker_diarization",
    "localization_draft",
    "reference_clips",
    "semantic_tts_grouping",
    "media_export",
]
OperationStatus = Literal["queued", "running", "success", "failed", "cancelled"]

ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"success", "failed", "cancelled"}
KIND_LABELS: dict[OperationKind, str] = {
    "source_audio": "抽取源音轨",
    "stems": "分离人声与背景声",
    "english_asr": "听写字幕",
    "dub_subtitle_generation": "根据合成配音生成字幕",
    "speaker_diarization": "区分说话人",
    "localization_draft": "生成本土化字幕",
    "reference_clips": "生成参考音候选",
    "semantic_tts_grouping": "按语义组合配音字幕",
    "media_export": "导出成品",
}


def operation_scope(kind: OperationKind, parameters: dict | None = None) -> dict:
    source_track_id = str((parameters or {}).get("source_track_id") or "vocals")
    source_track_id = (
        source_track_id
        if source_track_id in {"original", "vocals"}
        else "vocals"
    )
    if kind == "dub_subtitle_generation":
        source_track_id = "dub"
    if (
        kind == "speaker_diarization"
        or (
            kind == "english_asr"
            and str(
                (parameters or {}).get("execution_mode") or "full"
            )
            in {"stop_after", "development_target"}
        )
        or (
            kind == "localization_draft"
            and str((parameters or {}).get("execution_mode") or "full")
            == "development_target"
        )
        or (
            kind == "dub_subtitle_generation"
            and str((parameters or {}).get("execution_mode") or "full")
            == "development_target"
        )
    ):
        return {
            "area": "development",
            "exclusive": True,
            "cancel_mode": "safe_point",
            "tracks": [
                {"id": source_track_id, "role": "input"},
                {"id": "development_snapshot", "role": "output"},
            ],
        }
    scopes = {
        "source_audio": {
            "area": "timeline",
            "exclusive": True,
            "cancel_mode": "queued_only",
            "tracks": [{"id": "original", "role": "output"}],
        },
        "stems": {
            "area": "timeline",
            "exclusive": True,
            "cancel_mode": "queued_only",
            "tracks": [
                {"id": "original", "role": "input"},
                {"id": "vocals", "role": "output"},
                {"id": "background", "role": "output"},
            ],
        },
        "english_asr": {
            "area": "subtitle",
            "exclusive": True,
            "cancel_mode": "safe_point",
            "tracks": [
                {"id": source_track_id, "role": "input"},
                {"id": "subtitles", "role": "output"},
            ],
        },
        "dub_subtitle_generation": {
            "area": "subtitle",
            "exclusive": True,
            "cancel_mode": "safe_point",
            "tracks": [
                {"id": "dub", "role": "input"},
                {"id": "localizedSubtitles", "role": "output"},
            ],
        },
        "speaker_diarization": {
            "area": "development",
            "exclusive": True,
            "cancel_mode": "safe_point",
            "tracks": [
                {"id": source_track_id, "role": "input"},
                {"id": "development_snapshot", "role": "output"},
            ],
        },
        "localization_draft": {
            "area": "subtitle",
            "exclusive": True,
            "cancel_mode": "safe_point",
            "tracks": [
                {"id": "subtitles", "role": "input"},
                {"id": "localizedSubtitles", "role": "output"},
            ],
        },
        "reference_clips": {
            "area": "voice",
            "exclusive": False,
            "cancel_mode": "queued_only",
            "tracks": [{"id": "vocals", "role": "input"}],
        },
        "semantic_tts_grouping": {
            "area": "subtitle",
            "exclusive": False,
            "cancel_mode": "safe_point",
            "tracks": [{"id": "localizedSubtitles", "role": "input"}],
        },
        "media_export": {
            "area": "delivery",
            "exclusive": False,
            "cancel_mode": "safe_point",
            "tracks": [],
        },
    }
    return scopes[kind]


def validate_prerequisites(kind: OperationKind, draft: VideoLocalizationDraft, parameters: dict | None = None) -> None:
    media = media_health.inspect_project_media(draft)
    if kind == "source_audio":
        if media.paths.source_video is None:
            if media.health.source_video.status == "unconfigured":
                raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_MISSING", "Import a source video before extracting audio")
            raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_NOT_FOUND", "Source video file is missing")
        return

    if kind == "stems":
        if media.paths.source_audio is None:
            if media.health.source_audio.status == "unconfigured":
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING",
                    "Extract source audio before running this operation",
                )
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND",
                "Source audio file is missing",
            )
        return

    if kind in {"english_asr", "speaker_diarization"}:
        if (
            kind == "english_asr"
            and str(
                (parameters or {}).get("execution_mode") or ""
            )
            == "development_target"
        ):
            return
        if (
            kind == "english_asr"
            and str((parameters or {}).get("execution_mode") or "") == "stop_after"
            and str((parameters or {}).get("stop_after_step") or "")
            in {
                "understand_document",
                "visual_evidence",
                "research",
                "normalize_entities",
                "section_review_r1",
                "review_decisions_r1",
            }
        ):
            profile_id = str(
                (parameters or {}).get("profile_id") or ""
            ).strip()
            if profile_id:
                from app.services import llm_runtime

                try:
                    llm_runtime.resolve_profile(profile_id)
                except llm_runtime.LlmRuntimeError as exc:
                    raise AppException(
                        exc.status_code,
                        exc.code,
                        str(exc),
                    ) from exc
            return
        from app.domains.video_localization import source_pipeline

        source_pipeline.validate_english_asr_source(draft, str((parameters or {}).get("source_track_id") or "auto"))
        if kind == "english_asr":
            source_pipeline.normalize_source_language(str((parameters or {}).get("source_language") or "auto"))
        return

    if kind == "dub_subtitle_generation":
        from app.domains.video_localization import dub_subtitles

        dub_subtitles.validate_prerequisites(draft)
        return

    if kind == "localization_draft":
        if not draft.cues or not any((cue.en_subtitle_text or "").strip() for cue in draft.cues):
            raise AppException(400, "VIDEO_LOCALIZATION_CUES_MISSING", "请先生成并校对 ASR 字幕。")
        from app.domains.video_localization import source_pipeline

        source_pipeline.normalize_source_language(
            str((parameters or {}).get("source_language") or draft.language_config.source_language)
        )
        target_language = str(
            (parameters or {}).get("target_language") or draft.language_config.target_language
        )
        if target_language != "zh-Hans":
            raise AppException(400, "VIDEO_LOCALIZATION_TARGET_LANGUAGE_UNSUPPORTED", "当前版本先支持简体中文本土化。")
        model_free_source_lock = bool(
            str((parameters or {}).get("execution_mode") or "full")
            == "development_target"
            and str(
                (parameters or {}).get("development_target_step_id") or ""
            )
            in {
                "lock_localization_source",
                "lock_localization_context_intent",
            }
        )
        if not model_free_source_lock:
            from app.services import llm_runtime

            try:
                llm_runtime.resolve_profile(
                    str((parameters or {}).get("profile_id") or "") or None
                )
            except llm_runtime.LlmRuntimeError as exc:
                raise AppException(exc.status_code, exc.code, str(exc)) from exc
        return

    if kind == "reference_clips":
        if (
            draft.stems.separation_status != "completed"
            or media.health.vocals.status == "unconfigured"
        ):
            raise AppException(400, "VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING", "Separate clean vocals before creating reference clips")
        if media.paths.vocals is None:
            raise AppException(400, "VIDEO_LOCALIZATION_CLEAN_VOCALS_NOT_FOUND", "Clean vocals file is missing")
        return

    if kind == "semantic_tts_grouping":
        if not draft.localized_subtitles:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_LOCALIZED_SUBTITLES_MISSING",
                "请先生成本土化字幕。",
            )
        from app.services import llm_runtime

        try:
            llm_runtime.resolve_profile(str((parameters or {}).get("profile_id") or "") or None)
        except llm_runtime.LlmRuntimeError as exc:
            raise AppException(exc.status_code, exc.code, str(exc)) from exc
        return

    if kind == "media_export":
        return


def active_operation_for_kind(draft: VideoLocalizationDraft, kind: OperationKind) -> VideoLocalizationOperation | None:
    return next((operation for operation in reversed(draft.operations) if operation.kind == kind and operation.status in ACTIVE_STATUSES), None)


def operation_from_draft(draft: VideoLocalizationDraft, operation_id: str) -> VideoLocalizationOperation | None:
    return next((operation for operation in draft.operations if operation.operation_id == operation_id), None)


def operation_was_cancelled(operation: VideoLocalizationOperation | None) -> bool:
    return bool(operation and (operation.cancel_requested or operation.status == "cancelled"))


def with_operation(draft: VideoLocalizationDraft, operation: VideoLocalizationOperation) -> VideoLocalizationDraft:
    return draft.model_copy(update={"operations": [*draft.operations, operation]})


def with_operation_updates(
    draft: VideoLocalizationDraft,
    operation_id: str,
    updates: dict[str, Any],
    *,
    kind: OperationKind | None = None,
) -> VideoLocalizationDraft:
    """Apply one lifecycle transition without allowing stale progress rollback."""
    next_operations: list[VideoLocalizationOperation] = []
    applied = False
    incoming_status = updates.get("status")
    for operation in draft.operations:
        if operation.operation_id != operation_id:
            next_operations.append(operation)
            continue
        if (
            operation.cancel_requested
            or operation.status in TERMINAL_STATUSES
        ) and incoming_status in ACTIVE_STATUSES:
            next_operations.append(operation)
            continue
        operation_updates = dict(updates)
        if incoming_status in ACTIVE_STATUSES and isinstance(
            operation_updates.get("result_summary"),
            dict,
        ):
            operation_updates["result_summary"] = _merge_active_result_summary(
                operation.result_summary,
                operation_updates["result_summary"],
            )
        next_operations.append(
            operation.model_copy(update=operation_updates)
        )
        applied = True
    next_draft = draft.model_copy(update={"operations": next_operations})
    if (
        applied
        and kind is not None
        and incoming_status in TERMINAL_STATUSES.union(ACTIVE_STATUSES)
    ):
        return with_kind_status(
            next_draft,
            kind,
            incoming_status,
        )
    return next_draft


def _merge_active_result_summary(
    previous: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    merged_incoming = dict(incoming)
    previous_step_results = previous.get("task_step_results")
    incoming_step_results = incoming.get("task_step_results")
    if isinstance(previous_step_results, dict) and isinstance(
        incoming_step_results,
        dict,
    ):
        step_ids = [
            *previous_step_results,
            *(
                step_id
                for step_id in incoming_step_results
                if step_id not in previous_step_results
            ),
        ]
        merged_incoming["task_step_results"] = {
            step_id: (
                {
                    **previous_step_results[step_id],
                    **incoming_step_results[step_id],
                }
                if isinstance(previous_step_results.get(step_id), dict)
                and isinstance(incoming_step_results.get(step_id), dict)
                else incoming_step_results.get(
                    step_id,
                    previous_step_results.get(step_id),
                )
            )
            for step_id in step_ids
        }

    previous_timings = previous.get("task_stage_timings")
    incoming_timings = incoming.get("task_stage_timings")
    if isinstance(previous_timings, dict) and isinstance(
        incoming_timings,
        dict,
    ):
        merged_timings = {
            **previous_timings,
            **incoming_timings,
        }
        for step_id, previous_timing in previous_timings.items():
            incoming_timing = incoming_timings.get(step_id)
            if (
                isinstance(previous_timing, dict)
                and previous_timing.get("atomic") is True
                and (
                    not isinstance(incoming_timing, dict)
                    or incoming_timing.get("atomic") is not True
                )
            ):
                merged_timings[step_id] = previous_timing
        merged_incoming["task_stage_timings"] = merged_timings

    return {
        **previous,
        **merged_incoming,
    }


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
    elif kind == "dub_subtitle_generation":
        metadata["dub_subtitle_generation_status"] = draft_status
        if clears_errors:
            metadata.pop(
                "dub_subtitle_generation_error_code",
                None,
            )
            metadata.pop(
                "dub_subtitle_generation_error",
                None,
            )
        if error_code:
            metadata[
                "dub_subtitle_generation_error_code"
            ] = error_code
        if error_message:
            metadata[
                "dub_subtitle_generation_error"
            ] = error_message
    elif kind == "localization_draft":
        metadata["localization_draft_status"] = draft_status
        if clears_errors:
            metadata.pop("localization_draft_error_code", None)
            metadata.pop("localization_draft_error", None)
        if error_code:
            metadata["localization_draft_error_code"] = error_code
        if error_message:
            metadata["localization_draft_error"] = error_message
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
    media = media_health.inspect_project_media(draft).health
    summary = {
        "audio_path": draft.source_media.audio_path,
        "duration_ms": draft.source_media.duration_ms,
        "sample_rate": draft.source_media.metadata.get("audio_sample_rate"),
        "channels": draft.source_media.metadata.get("audio_channels"),
        "audio_extract_status": draft.source_media.metadata.get("audio_extract_status"),
        "track_count": 1 if draft.source_media.audio_path else 0,
        "available_track_count": (
            1 if media.source_audio.status == "available" else 0
        ),
        "media_status": media.source_audio.status,
        "selected_source": media.source_audio.selected_source,
    }
    return {key: value for key, value in summary.items() if value is not None and value != ""}


def stems_summary(draft: VideoLocalizationDraft | None) -> dict:
    if not draft:
        return {}
    stems = draft.stems
    media = media_health.inspect_project_media(draft).health
    summary = {
        "vocals_clean_path": stems.vocals_clean_path,
        "background_path": stems.background_path,
        "duration_ms": getattr(stems, "duration_ms", None) or draft.source_media.duration_ms,
        "sample_rate": getattr(stems, "sample_rate", None),
        "channels": getattr(stems, "channels", None),
        "separation_engine_id": stems.separation_engine_id,
        "separation_status": stems.separation_status,
        "track_count": sum(
            bool(path)
            for path in (stems.vocals_clean_path, stems.background_path)
        ),
        "available_track_count": sum(
            status == "available"
            for status in (media.vocals.status, media.background.status)
        ),
        "media_status": media.stems_status,
    }
    return {key: value for key, value in summary.items() if value is not None and value != ""}


def english_asr_summary(draft: VideoLocalizationDraft | None) -> dict:
    if not draft:
        return {}
    pipeline_timing = getattr(draft.transcription, "pipeline_timing", None) if draft.transcription else None
    pipeline_timing = pipeline_timing if isinstance(pipeline_timing, dict) else {}
    stages = pipeline_timing.get("stages") if isinstance(pipeline_timing.get("stages"), dict) else {}
    boundary_review_timing = (
        stages.get("boundary_review") if isinstance(stages.get("boundary_review"), dict) else {}
    )
    text_review_timing = stages.get("text_review") if isinstance(stages.get("text_review"), dict) else {}
    transcription = draft.transcription
    speaker_review_required = bool(
        transcription
        and (
            any(cluster.merge_status == "needs_review" for cluster in transcription.speaker_clusters)
            or "speaker_overlap_review_required" in transcription.quality_flags
            or "speaker_cluster_review_required" in transcription.quality_flags
        )
    )
    summary = {
        "engine_id": draft.source_media.metadata.get("english_asr_engine_id"),
        "source_track_id": draft.source_media.metadata.get("english_asr_source_track_id"),
        "language": draft.source_media.metadata.get("english_asr_language")
        or (draft.transcription.language if draft.transcription else None),
        "segment_count": draft.source_media.metadata.get("english_asr_raw_segment_count"),
        "cue_count": len(draft.cues),
        "diarization_status": draft.source_media.metadata.get("english_asr_diarization_status")
        or (transcription.diarization_status if transcription else "not_run"),
        "diarization_engine_id": draft.source_media.metadata.get("english_asr_diarization_engine_id")
        or (transcription.diarization_engine_id if transcription else None),
        "speaker_count": draft.source_media.metadata.get("english_asr_speaker_count")
        if draft.source_media.metadata.get("english_asr_speaker_count") is not None
        else len(transcription.speaker_clusters) if transcription else 0,
        "speaker_review_required": speaker_review_required,
        "audio_boundary_status": draft.source_media.metadata.get("english_asr_audio_boundary_status"),
        "audio_boundary_count": draft.source_media.metadata.get("english_asr_audio_boundary_count"),
        "audio_boundary_analysis_version": draft.source_media.metadata.get("english_asr_audio_boundary_analysis_version"),
        "boundary_review_status": draft.source_media.metadata.get("english_asr_boundary_review_status"),
        "boundary_review_count": draft.source_media.metadata.get("english_asr_boundary_review_count"),
        "boundary_review_prompt_version": draft.source_media.metadata.get("english_asr_boundary_review_prompt_version"),
        "duration_ms": pipeline_timing.get("total_duration_ms"),
        "llm_profile_id": boundary_review_timing.get("profile_id")
        or text_review_timing.get("profile_id")
        or (transcription.review_profile_id if transcription else None),
        "llm_model_id": boundary_review_timing.get("model_id")
        or text_review_timing.get("model_id")
        or (transcription.review_model_id if transcription else None),
        "stage_timings": stages,
        "boundary_review_rounds": boundary_review_timing.get("rounds", []),
    }
    summary["task_step_results"] = asr_step_results.build_asr_step_results(draft, stages)
    workflow = workflow_contracts.asr_workflow_summary()
    summary["workflow_schema_version"] = workflow["schema_version"]
    summary["workflow_id"] = workflow["workflow_id"]
    summary["task_stage_groups"] = workflow["stages"]
    return summary


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
