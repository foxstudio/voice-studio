from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import UploadFile

from app.domains.video_localization import audio_access
from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization import draft_store
from app.domains.video_localization import exporting
from app.domains.video_localization import localization
from app.domains.video_localization import media_assets
from app.domains.video_localization import operation_state
from app.domains.video_localization import project_manifest
from app.domains.video_localization import quality_gate
from app.domains.video_localization import reference_clips
from app.domains.video_localization import speakers
from app.domains.video_localization import source_pipeline
from app.domains.video_localization import subtitles
from app.domains.video_localization import tts_orchestration
from app.domains.video_localization import tts_pipeline
from app.errors import AppException
from app.schemas.voice_studio import Project
from app.domains.video_localization.schemas import (
    BatchGenerateRequest,
    VideoLocalizationCueUpdate,
    VideoLocalizationDraft,
    VideoLocalizationExport,
    VideoLocalizationOperation,
    VideoLocalizationReferenceClipCreate,
    VideoLocalizationReferenceClipUpdate,
    VideoLocalizationSpeakerCreate,
    VideoLocalizationSpeakerUpdate,
    VideoLocalizationSubtitleCueUpdate,
    VideoLocalizationSubtitleImportRequest,
)
from app.services import history_store, llm_runtime, project_store

VIDEO_LOCALIZATION_KEY = draft_store.VIDEO_LOCALIZATION_KEY
_DRAFT_WRITE_LOCK = threading.RLock()


def get_video_localization(project_id: str) -> VideoLocalizationDraft | None:
    return draft_store.get(project_id)


def submit_operation(
    project_id: str,
    kind: operation_state.OperationKind,
    parameters: dict | None = None,
) -> VideoLocalizationOperation | None:
    from app.domains.video_localization import operation_queue

    with _DRAFT_WRITE_LOCK:
        return operation_queue.submit(project_id, kind, parameters)


def sync_local_projects() -> list[Project]:
    """Reconcile the localization project menu with valid project packages on disk."""
    synced: list[Project] = []
    for package in project_manifest.discover_project_packages():
        project_id = package["project_id"]
        project = project_store.get_project(project_id)
        if project is None:
            project = Project(
                project_id=project_id,
                name=package["project_name"],
                description="本地视频本土化项目",
                parameters={
                    media_assets.PROJECT_DIR_NAME_KEY: package["directory_name"],
                    VIDEO_LOCALIZATION_KEY: package["draft"].model_dump(mode="json"),
                },
            )
            synced.append(project_store.save_project(project))
            continue

        next_parameters = dict(project.parameters)
        changed = next_parameters.get(media_assets.PROJECT_DIR_NAME_KEY) != package["directory_name"]
        next_parameters[media_assets.PROJECT_DIR_NAME_KEY] = package["directory_name"]
        if not isinstance(next_parameters.get(VIDEO_LOCALIZATION_KEY), dict):
            next_parameters[VIDEO_LOCALIZATION_KEY] = package["draft"].model_dump(mode="json")
            changed = True
        if changed:
            project.parameters = next_parameters
            project = project_store.save_project(project)
        synced.append(project)
    return synced


def save_video_localization(project_id: str, draft: VideoLocalizationDraft) -> VideoLocalizationDraft | None:
    with _DRAFT_WRITE_LOCK:
        current = draft_store.get(project_id)
        if current and current.updated_at and draft.updated_at and current.updated_at != draft.updated_at:
            raise AppException(409, "VIDEO_LOCALIZATION_DRAFT_CONFLICT", "Project changed while this draft was being edited")
        return draft_store.save(project_id, draft)


def replace_video_localization_from_client(
    project_id: str,
    draft: VideoLocalizationDraft,
) -> VideoLocalizationDraft | None:
    """Replace an editable draft without trusting backend-owned audit fields."""
    with _DRAFT_WRITE_LOCK:
        current = draft_store.get(project_id)
        if current and current.updated_at and draft.updated_at and current.updated_at != draft.updated_at:
            raise AppException(409, "VIDEO_LOCALIZATION_DRAFT_CONFLICT", "Project changed while this draft was being edited")
        sanitized = cue_tools.sanitize_client_draft_timing_provenance(current, draft)
        if current is not None:
            sanitized = _preserve_backend_owned_draft_state(current, sanitized)
        return draft_store.save(project_id, sanitized)


def _preserve_backend_owned_draft_state(
    current: VideoLocalizationDraft,
    incoming: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    is_uninitialized = (
        current.updated_at is None
        and not current.operations
        and not current.localization_state
    )
    updates: dict[str, Any] = {
        "operations": incoming.operations if is_uninitialized else current.operations,
        "localization_state": incoming.localization_state if is_uninitialized else current.localization_state,
    }
    if not _localization_draft_is_active(current):
        return incoming.model_copy(update=updates)

    current_cues = {cue.cue_id: cue for cue in current.cues}
    next_cues = []
    for cue in incoming.cues:
        current_cue = current_cues.get(cue.cue_id)
        next_cues.append(
            cue.model_copy(
                update={
                    "zh_localized_subtitle_text": (
                        current_cue.zh_localized_subtitle_text if current_cue is not None else None
                    ),
                    "tts_recommended_text": current_cue.tts_recommended_text if current_cue is not None else None,
                }
            )
        )
    updates.update(
        {
            "cues": next_cues,
            "localized_subtitles": current.localized_subtitles,
        }
    )
    return incoming.model_copy(update=updates)


def update_video_localization_atomic(
    project_id: str,
    updater: Callable[[VideoLocalizationDraft], VideoLocalizationDraft],
) -> VideoLocalizationDraft | None:
    """Merge a backend-owned patch into the latest draft under one write lock."""
    with _DRAFT_WRITE_LOCK:
        current = draft_store.get(project_id)
        if current is None:
            return None
        return draft_store.save(project_id, updater(current))


def update_video_localization_ui_state(project_id: str, patch: dict[str, Any]) -> VideoLocalizationDraft | None:
    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        next_draft = draft.model_copy(update={"ui_state": {**draft.ui_state, **patch}})
        return draft_store.save(project_id, next_draft)


def reset_video_localization(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    if any(operation.status in operation_state.ACTIVE_STATUSES for operation in draft.operations):
        raise AppException(409, "VIDEO_LOCALIZATION_RESET_BLOCKED", "当前仍有后台任务在运行，请先取消或等待完成后再清空")

    media_assets.clear_project_video_localization_dir(project_id)
    if VIDEO_LOCALIZATION_KEY in project.parameters:
        project.parameters = {key: value for key, value in project.parameters.items() if key != VIDEO_LOCALIZATION_KEY}
        project_store.save_project(project)
    return VideoLocalizationDraft()


def delete_project(project_id: str) -> bool:
    project = project_store.get_project(project_id)
    if not project:
        return False
    draft = get_video_localization(project_id)
    if draft and any(operation.status in operation_state.ACTIVE_STATUSES for operation in draft.operations):
        raise AppException(409, "VIDEO_LOCALIZATION_DELETE_BLOCKED", "当前仍有后台任务，请先取消或等待完成后再删除项目")
    if VIDEO_LOCALIZATION_KEY in project.parameters or media_assets.PROJECT_DIR_NAME_KEY in project.parameters:
        media_assets.delete_project_video_localization_dir(project_id)
    project_store.delete_project(project_id)
    return True


def open_project_directory(project_id: str) -> dict[str, str] | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    return media_assets.open_project_video_localization_dir(project_id)


def prepare_project_rename(project: Any, next_name: str) -> Any:
    name = next_name.strip() or project.name
    if VIDEO_LOCALIZATION_KEY not in project.parameters and media_assets.PROJECT_DIR_NAME_KEY not in project.parameters:
        project.name = name
        return project

    old_dir = media_assets.project_video_localization_dir(project.project_id)
    new_dir_name = media_assets.project_dir_name(project.project_id, name)
    new_dir = media_assets.project_video_localization_dir_for_name(project.project_id, name)

    if old_dir != new_dir and old_dir.exists():
        _move_project_root(old_dir, new_dir)

    project.parameters = {
        **project.parameters,
        media_assets.PROJECT_DIR_NAME_KEY: new_dir_name,
    }
    if VIDEO_LOCALIZATION_KEY in project.parameters:
        project.parameters[VIDEO_LOCALIZATION_KEY] = _replace_path_prefix(project.parameters[VIDEO_LOCALIZATION_KEY], old_dir, new_dir)
    project.name = name
    project_store.save_project(project)
    if VIDEO_LOCALIZATION_KEY in project.parameters:
        project_manifest.write_project_snapshot(project, VideoLocalizationDraft(**project.parameters[VIDEO_LOCALIZATION_KEY]))
    return project


async def import_source_media(project_id: str, file: UploadFile) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    _ensure_project_dir_name(project)
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, await source_pipeline.with_imported_source_media(project_id, draft, file))


def extract_source_audio(
    project_id: str,
    *,
    commit_guard: Callable[
        [Callable[[], VideoLocalizationDraft | None]],
        tuple[bool, VideoLocalizationDraft | None],
    ]
    | None = None,
) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    source_revision = source_pipeline.source_video_revision(draft)
    extracted = source_pipeline.with_extracted_source_audio(project_id, draft)
    generated_path = extracted.source_media.audio_path
    previous_paths = {draft.source_media.audio_path, draft.stems.original_audio_path}

    def commit_extracted_audio() -> VideoLocalizationDraft | None:
        with _DRAFT_WRITE_LOCK:
            latest = get_video_localization(project_id)
            if latest is None:
                return None
            if source_pipeline.source_video_revision(latest) != source_revision:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_SOURCE_CHANGED",
                    "抽取原音轨期间源视频发生了变化，因此没有写入旧音轨。请基于当前视频重新抽取。",
                )
            merged = latest.model_copy(
                update={
                    "source_media": latest.source_media.model_copy(
                        update={
                            "audio_path": extracted.source_media.audio_path,
                            "audio_sha256": extracted.source_media.audio_sha256,
                            "duration_ms": extracted.source_media.duration_ms,
                            "metadata": extracted.source_media.metadata,
                        }
                    ),
                    "stems": latest.stems.model_copy(
                        update={
                            "original_audio_path": extracted.stems.original_audio_path,
                            "original_audio_sha256": extracted.stems.original_audio_sha256,
                        }
                    ),
                }
            )
            return draft_store.save(project_id, merged)

    try:
        if commit_guard is not None:
            committed, saved = commit_guard(commit_extracted_audio)
            if not committed:
                if generated_path and generated_path not in previous_paths:
                    Path(generated_path).unlink(missing_ok=True)
                return get_video_localization(project_id)
            return saved
        return commit_extracted_audio()
    except Exception:
        if generated_path and generated_path not in previous_paths:
            Path(generated_path).unlink(missing_ok=True)
        raise


def separate_source_audio(
    project_id: str,
    *,
    commit_guard: Callable[
        [Callable[[], VideoLocalizationDraft | None]],
        tuple[bool, VideoLocalizationDraft | None],
    ]
    | None = None,
) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    source_revision = source_pipeline.source_audio_revision(draft)
    separated_draft = source_pipeline.with_separated_source_audio(project_id, draft)
    separated_stems = separated_draft.stems
    new_paths = [separated_stems.vocals_clean_path, separated_stems.background_path]
    previous_paths = {draft.stems.vocals_clean_path, draft.stems.background_path}
    try:
        def commit_separated_audio() -> VideoLocalizationDraft | None:
            # Separation can take minutes. Merge only its result into the newest draft so
            # autosaved UI/timeline changes made while Demucs was running are preserved.
            with _DRAFT_WRITE_LOCK:
                latest = get_video_localization(project_id)
                if latest is None:
                    return None
                if source_pipeline.source_audio_revision(latest) != source_revision:
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_SOURCE_AUDIO_CHANGED",
                        "分离人声期间源音轨发生了变化，因此没有写入旧的分轨结果。请基于当前音轨重新分离。",
                    )
                return draft_store.save(project_id, latest.model_copy(update={"stems": separated_stems}))

        if commit_guard is not None:
            committed, saved = commit_guard(commit_separated_audio)
            if not committed:
                saved = get_video_localization(project_id)
        else:
            saved = commit_separated_audio()
    except Exception:
        for value in new_paths:
            if value and value not in previous_paths:
                Path(value).unlink(missing_ok=True)
        raise
    if saved:
        media_assets.cleanup_unreferenced_stems(project_id, [saved.stems.vocals_clean_path, saved.stems.background_path])
    return saved


def transcribe_english_source_audio(
    project_id: str,
    engine_id: str = source_pipeline.DEFAULT_ENGLISH_ASR_ENGINE_ID,
    source_track_id: source_pipeline.EnglishAsrSourceTrackId | str = "auto",
    source_language: str = "auto",
    is_cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    on_preview: Callable[[str, list[dict]], None] | None = None,
    segmentation_profile_id: str = "generic_zh",
    diarization_engine_id: str | None = None,
    commit_guard: Callable[
        [Callable[[], VideoLocalizationDraft | None]],
        tuple[bool, VideoLocalizationDraft | None],
    ]
    | None = None,
) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    source_revision = source_pipeline.english_asr_source_revision(draft)
    result = source_pipeline.with_english_asr(
        draft,
        engine_id,
        source_track_id,
        source_language=source_language,
        project_id=project_id,
        segmentation_profile_id=segmentation_profile_id,
        progress_callback=on_progress,
        is_cancelled=is_cancelled,
        preview_callback=on_preview,
        diarization_engine_id=diarization_engine_id,
    )

    def commit_asr_result() -> VideoLocalizationDraft | None:
        # ASR can take minutes. Re-read and merge into the latest draft so autosaved
        # UI edits survive, while changed source media can never receive stale text.
        with _DRAFT_WRITE_LOCK:
            latest = get_video_localization(project_id)
            if latest is None:
                return None
            if is_cancelled and is_cancelled():
                return latest
            source_pipeline.ensure_english_asr_source_unchanged(
                latest,
                result,
                expected_source_revision=source_revision,
            )
            merged = source_pipeline.merge_english_asr_result(latest, result)
            if is_cancelled and is_cancelled():
                return latest
            return draft_store.save(project_id, merged)

    if commit_guard is not None:
        committed, saved = commit_guard(commit_asr_result)
        if not committed:
            return get_video_localization(project_id)
        return saved
    return commit_asr_result()


def import_subtitles(project_id: str, kind: str, request: VideoLocalizationSubtitleImportRequest) -> VideoLocalizationDraft | None:
    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        if kind == "zh":
            _ensure_localization_track_editable(draft)
        next_draft = subtitles.import_srt(
            draft,
            kind,
            request.srt_text,
            update_timing=request.update_timing,
            overwrite_tts=request.overwrite_tts,
        )
        return draft_store.save(project_id, next_draft)


def create_reference_clips_from_cues(project_id: str, payload: VideoLocalizationReferenceClipCreate | None = None) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = reference_clips.with_reference_clips_from_cues(project_id, draft, payload)
    return save_video_localization(project_id, speakers.reconcile_speakers(next_draft))


def update_reference_clip(project_id: str, reference_clip_id: str, patch: VideoLocalizationReferenceClipUpdate) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = reference_clips.with_updated_reference_clip(draft, reference_clip_id, patch)
    return save_video_localization(project_id, speakers.reconcile_speakers(next_draft))


def delete_reference_clip(project_id: str, reference_clip_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = reference_clips.without_reference_clip(draft, reference_clip_id)
    return save_video_localization(project_id, speakers.reconcile_speakers(next_draft))


def update_cue(project_id: str, cue_id: str, patch: VideoLocalizationCueUpdate) -> VideoLocalizationDraft | None:
    target_fields = {"zh_localized_subtitle_text", "tts_recommended_text"}
    updates_target_track = bool(target_fields.intersection(patch.model_fields_set))
    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        if updates_target_track:
            _ensure_localization_track_editable(draft)
        next_draft = cue_tools.with_updated_cue(draft, cue_id, patch)
        return draft_store.save(project_id, speakers.reconcile_speakers(next_draft))


def update_localized_subtitle(
    project_id: str,
    subtitle_id: str,
    patch: VideoLocalizationSubtitleCueUpdate,
) -> VideoLocalizationDraft | None:
    with _DRAFT_WRITE_LOCK:
        project = project_store.get_project(project_id)
        if not project:
            return None
        draft = get_video_localization(project_id) or VideoLocalizationDraft()
        _ensure_localization_track_editable(draft)
        next_draft = subtitles.with_updated_localized_subtitle(draft, subtitle_id, patch)
        return draft_store.save(project_id, next_draft)


def clear_subtitles(project_id: str, kind: str) -> VideoLocalizationDraft | None:
    def clear(current: VideoLocalizationDraft) -> VideoLocalizationDraft:
        if kind == "en":
            if any(
                operation.kind == "english_asr" and operation.status in {"queued", "running"}
                for operation in current.operations
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_SUBTITLE_CLEAR_BLOCKED",
                    "字幕听写仍在运行，请先取消或等待完成后再清空 ASR 字幕轨。",
                )
            return source_pipeline.without_english_asr(current)
        _ensure_localization_track_editable(current)
        return subtitles.without_localized_subtitle_track(current)

    return update_video_localization_atomic(project_id, clear)


def _ensure_localization_track_editable(draft: VideoLocalizationDraft) -> None:
    if _localization_draft_is_active(draft):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_TRACK_BUSY",
            "本土化字幕正在生成，请等待完成或先取消任务。",
        )


def _localization_draft_is_active(draft: VideoLocalizationDraft) -> bool:
    return any(
        operation.kind == "localization_draft" and operation.status in {"queued", "running"}
        for operation in draft.operations
    )


def create_speaker(project_id: str, payload: VideoLocalizationSpeakerCreate) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, speakers.with_created_speaker(draft, payload))


def update_speaker(project_id: str, speaker_id: str, payload: VideoLocalizationSpeakerUpdate) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, speakers.with_updated_speaker(draft, speaker_id, payload))


def generate_localization_draft(project_id: str) -> VideoLocalizationDraft | None:
    updated, _summary = run_localization_draft(project_id)
    return updated


def run_localization_draft(
    project_id: str,
    *,
    source_language: str = "auto",
    target_language: str | None = None,
    profile_id: str | None = None,
    localization_level: str = "L1",
    worldview_permeability: str = "W0",
    is_cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    on_preview: Callable[[str, list[dict]], None] | None = None,
    commit_guard: Callable[
        [Callable[[], VideoLocalizationDraft | None]],
        tuple[bool, VideoLocalizationDraft | None],
    ]
    | None = None,
) -> tuple[VideoLocalizationDraft | None, dict]:
    project = project_store.get_project(project_id)
    if not project:
        return None, {}
    snapshot = get_video_localization(project_id) or VideoLocalizationDraft()
    resolved_source_language = source_pipeline.normalize_source_language(
        source_language or snapshot.language_config.source_language
    )
    if resolved_source_language == "auto":
        detected_source_language = snapshot.language_config.detected_source_language
        if detected_source_language is None and snapshot.transcription is not None:
            transcription_language = source_pipeline.normalize_source_language(snapshot.transcription.language)
            detected_source_language = transcription_language if transcription_language != "auto" else None
        resolved_source_language = detected_source_language or _infer_source_language_from_cues(snapshot)
    resolved_target_language = target_language or snapshot.language_config.target_language
    fingerprint = localization.source_fingerprint(snapshot)
    try:
        run = localization.generate_localization_draft(
            snapshot,
            source_language=resolved_source_language,
            target_language=resolved_target_language,
            profile_id=profile_id,
            localization_level=localization_level,
            worldview_permeability=worldview_permeability,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
            on_preview=on_preview,
        )
    except llm_runtime.LlmRuntimeError as exc:
        raise AppException(exc.status_code, exc.code, str(exc)) from exc

    def commit_generated_track() -> VideoLocalizationDraft | None:
        with _DRAFT_WRITE_LOCK:
            latest = get_video_localization(project_id)
            if latest is None:
                return None
            if is_cancelled and is_cancelled():
                return latest
            if localization.source_fingerprint(latest) != fingerprint:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_SOURCE_CHANGED",
                    "任务运行期间原文字幕发生了变化，因此没有覆盖当前编辑。请重新生成本土化字幕。",
                )
            generated_by_id = {cue.cue_id: cue for cue in run.draft.cues}
            next_cues = []
            for cue in latest.cues:
                generated = generated_by_id.get(cue.cue_id)
                if generated is None:
                    next_cues.append(cue)
                    continue
                next_cues.append(
                    cue.model_copy(
                        update={
                            "zh_localized_subtitle_text": generated.zh_localized_subtitle_text,
                            "tts_recommended_text": generated.tts_recommended_text,
                            "quality_flags": generated.quality_flags,
                        }
                    )
                )
            merged = latest.model_copy(
                update={
                    "cues": next_cues,
                    "localized_subtitles": run.draft.localized_subtitles,
                    "localization_state": run.draft.localization_state,
                    "language_config": latest.language_config.model_copy(
                        update={
                            "detected_source_language": resolved_source_language,
                            "target_language": resolved_target_language,
                        }
                    ),
                }
            )
            if is_cancelled and is_cancelled():
                return latest
            return draft_store.save(project_id, merged)

    if commit_guard is not None:
        committed, saved = commit_guard(commit_generated_track)
        if not committed:
            return get_video_localization(project_id), run.summary
        return saved, run.summary
    return commit_generated_track(), run.summary


def _infer_source_language_from_cues(draft: VideoLocalizationDraft) -> str:
    text = "\n".join(
        value
        for cue in draft.cues
        if (value := (cue.en_subtitle_text or "").strip())
    )
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    return "zh" if cjk_count > latin_count else "en"


def build_tts_batch_request(project_id: str, engine_id: str = "indextts-v2") -> BatchGenerateRequest | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return tts_orchestration.build_batch_request(
        project_id=project_id,
        project_name=project.name,
        draft=draft,
        engine_id=engine_id,
    )


def build_single_tts_handoff(project_id: str, segment_id: str):
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return tts_orchestration.build_single_handoff(project_id, draft, segment_id)


def mark_tts_batch_submitted(project_id: str, batch_task_id: str, cue_ids: list[str]) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = tts_orchestration.mark_batch_submitted(draft, batch_task_id, cue_ids)
    if next_draft is draft:
        return draft
    return save_video_localization(project_id, next_draft)


def export_subtitles(project_id: str, kind: str) -> str | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    blockers = quality_gate.subtitle_export_blockers(draft, kind)
    if blockers:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED",
            f"字幕未通过导出检查，请先处理 {len(blockers)} 个时间或内容问题。",
            {"issues": [issue.model_dump(mode="json") for issue in blockers]},
        )
    return exporting.export_subtitles(draft, kind)


def sync_tts_batch_results(project_id: str, batch_task_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, tts_orchestration.sync_batch_results(project_id, draft, batch_task_id))


def sync_single_tts_result(
    project_id: str,
    cue_id: str,
    *,
    result_id: str,
    output_path: str,
    duration_ms: int | None,
    task_id: str | None = None,
    generation_id: str | None = None,
) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    source_path = Path(output_path)
    if source_path.exists():
        adopted_path = media_assets.adopt_tts_audio(project_id, source_path, cue_id, task_id or result_id)
        output_path = str(adopted_path)
    return save_video_localization(
        project_id,
        tts_orchestration.sync_single_result(
            draft,
            cue_id,
            result_id=result_id,
            output_path=output_path,
            duration_ms=duration_ms,
            task_id=task_id,
            generation_id=generation_id,
        ),
    )


def apply_tts_history_to_timeline_clip(
    project_id: str,
    clip_id: str,
    result_id: str,
) -> VideoLocalizationDraft | None:
    return apply_tts_history_to_timeline(
        project_id,
        result_id,
        segment_id="",
        clip_id=clip_id,
    )


def apply_tts_history_to_timeline(
    project_id: str,
    result_id: str,
    *,
    segment_id: str,
    clip_id: str | None = None,
) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None

    history = history_store.get(result_id)
    if not history or not history_store.audio_path(result_id):
        raise AppException(404, "VIDEO_LOCALIZATION_TTS_HISTORY_NOT_FOUND", "TTS history audio is not available")
    if history.project_id != project_id or history.parameter_snapshot.get("source") != "video_localization":
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_HISTORY_PROJECT_MISMATCH", "该历史记录不属于当前视频本土化项目")

    clip = next((dict(item) for item in draft.timeline_clips if item.get("clip_id") == clip_id), None) if clip_id else None
    if clip_id and not clip:
        raise AppException(404, "VIDEO_LOCALIZATION_TIMELINE_CLIP_NOT_FOUND", "Timeline clip not found")
    if clip and clip.get("track_id", "dub") != "dub":
        raise AppException(400, "VIDEO_LOCALIZATION_TIMELINE_CLIP_NOT_DUB", "Only dub clips can use TTS history")

    target_segment_id = str(
        segment_id
        or (clip or {}).get("subtitle_id")
        or (clip or {}).get("cue_id")
        or ""
    )
    subtitle = next((item for item in draft.localized_subtitles if item.subtitle_id == target_segment_id), None)
    cue = next((item for item in draft.cues if item.cue_id == target_segment_id), None)
    if not subtitle and not cue and not clip:
        raise AppException(404, "VIDEO_LOCALIZATION_TTS_SEGMENT_NOT_FOUND", "找不到要采用配音的字幕片段")

    if not clip:
        clip = next(
            (
                dict(item)
                for item in draft.timeline_clips
                if item.get("track_id", "dub") == "dub"
                and (
                    (subtitle and item.get("subtitle_id") == subtitle.subtitle_id)
                    or (cue and not item.get("subtitle_id") and item.get("cue_id") == cue.cue_id)
                )
            ),
            None,
        )

    subtitle_id = str((clip or {}).get("subtitle_id") or (subtitle.subtitle_id if subtitle else ""))
    cue_id = str((clip or {}).get("cue_id") or (cue.cue_id if cue else ""))
    if subtitle_id:
        matches_clip = history.localized_subtitle_id == subtitle_id or history.segment_id == subtitle_id
    else:
        matches_clip = history.cue_id == cue_id or history.segment_id == cue_id
    if not matches_clip:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_HISTORY_SEGMENT_MISMATCH", "该历史声音与当前字幕片段不匹配，不能直接替换")

    source_path = history_store.audio_path(result_id)
    assert source_path is not None
    adopted_path = media_assets.adopt_tts_audio(project_id, source_path, subtitle_id or cue_id, history.task_id or result_id)

    working_draft = draft
    if not clip:
        used_ids = {str(item.get("clip_id") or "") for item in draft.timeline_clips}
        base_clip_id = f"clip_{subtitle_id or cue_id}"
        next_clip_id = base_clip_id
        suffix = 2
        while next_clip_id in used_ids:
            next_clip_id = f"{base_clip_id}_{suffix}"
            suffix += 1
        source_cue_ids = list(dict.fromkeys(subtitle.source_cue_ids)) if subtitle else []
        primary_cue_id = next(iter(source_cue_ids), subtitle.linked_cue_id if subtitle else cue_id)
        clip = {
            "clip_id": next_clip_id,
            "track_id": "dub",
            "subtitle_id": subtitle_id or None,
            "cue_id": primary_cue_id,
            "source_cue_ids": source_cue_ids,
            "start_ms": subtitle.start_ms if subtitle else cue.start_ms,
            "end_ms": subtitle.end_ms if subtitle else cue.end_ms,
            "status": "ready",
        }
        working_draft = draft.model_copy(update={"timeline_clips": [*draft.timeline_clips, clip]})

    return save_video_localization(
        project_id,
        tts_pipeline.with_applied_history_result(
            working_draft,
            str(clip["clip_id"]),
            result_id=result_id,
            output_path=str(adopted_path),
            duration_ms=history.duration_ms,
            generation_id=history.generation_id or history.task_id,
        ),
    )


def tts_audio_file(project_id: str, cue_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.tts_audio_path(draft, cue_id)


def generated_candidate_audio_file(project_id: str, candidate_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return tts_pipeline.generated_candidate_audio_path(draft, candidate_id)


def timeline_clip_audio_file(project_id: str, clip_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.timeline_clip_audio_path(draft, clip_id)


def apply_generated_candidate(project_id: str, candidate_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, tts_pipeline.with_applied_generated_candidate(draft, candidate_id))


def source_video_file(project_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.source_video_path(draft)


def source_preview_video_file(project_id: str) -> Path | None:
    source_path = source_video_file(project_id)
    if not source_path:
        return None
    return media_assets.existing_editing_proxy(project_id, source_path) or source_path


def ensure_source_preview_video(project_id: str) -> Path | None:
    source_path = source_video_file(project_id)
    if not source_path:
        return None
    return media_assets.ensure_editing_proxy(project_id, source_path)


def prepare_source_preview_video(project_id: str) -> Path | None:
    try:
        return ensure_source_preview_video(project_id)
    except AppException:
        return None


def source_preview_profile(video_path: Path | None) -> str:
    if not video_path:
        return "missing"
    return media_assets.EDITING_PROXY_PROFILE if video_path.parent.name == "preview" else "source"


def source_audio_file(project_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.source_audio_path(draft)


def stem_audio_file(project_id: str, kind: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.stem_audio_path(draft, kind)


def reference_clip_audio_file(project_id: str, reference_clip_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.reference_clip_audio_path(draft, reference_clip_id)


def reference_clip_cover_file(project_id: str, reference_clip_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    clip = next((item for item in draft.reference_clips if item.reference_clip_id == reference_clip_id), None)
    if not clip or not clip.cover_frame_path:
        return None
    path = Path(clip.cover_frame_path)
    return path if path.exists() else None


def source_cue_audio_file(project_id: str, cue_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return audio_access.source_cue_audio_path(project_id, draft, cue_id)


def export_video_localization(project_id: str) -> VideoLocalizationExport | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return exporting.export_bundle(project.project_id, project.name, draft)


def export_timeline_edl(project_id: str) -> dict | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return exporting.timeline_edl(project.project_id, project.name, draft)


def export_timeline_audio_package(project_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    manifest = exporting.timeline_audio_package(project.project_id, project.name, draft)
    next_exports = draft.exports.model_copy(
        update={
            "timeline_audio_package_path": manifest.get("package_path"),
            "timeline_audio_manifest_path": str(Path(str(manifest.get("package_dir", ""))) / "manifest.json") if manifest.get("package_dir") else None,
            "last_exported_at": manifest.get("exported_at"),
        }
    )
    save_video_localization(project_id, draft.model_copy(update={"exports": next_exports}))
    return Path(str(manifest["package_path"]))


def export_localized_video(project_id: str) -> Path | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    manifest = exporting.localized_video_file(project.project_id, project.name, draft)
    next_exports = draft.exports.model_copy(
        update={
            "timeline_audio_package_path": manifest.get("package_path"),
            "timeline_audio_manifest_path": str(Path(str(manifest.get("package_dir", ""))) / "manifest.json") if manifest.get("package_dir") else None,
            "localized_video_path": manifest.get("localized_video_path"),
            "last_exported_at": manifest.get("exported_at"),
        }
    )
    save_video_localization(project_id, draft.model_copy(update={"exports": next_exports}))
    return Path(str(manifest["localized_video_path"]))


def production_readiness_audit(project_id: str) -> dict | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    return exporting.production_readiness(project.project_id, project.name, draft)


def _ensure_project_dir_name(project: Any) -> None:
    if project.parameters.get(media_assets.PROJECT_DIR_NAME_KEY):
        return
    project.parameters = {
        **project.parameters,
        media_assets.PROJECT_DIR_NAME_KEY: media_assets.project_dir_name(project.project_id, project.name),
    }
    project_store.save_project(project)


def _move_project_root(old_root: Path, new_root: Path) -> None:
    new_root.parent.mkdir(parents=True, exist_ok=True)
    if not new_root.exists():
        shutil.move(str(old_root), str(new_root))
        return
    for child in old_root.iterdir():
        target = new_root / child.name
        if child.is_dir() and target.exists() and target.is_dir():
            _move_project_root(child, target)
        else:
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(child), str(target))
    try:
        old_root.rmdir()
    except OSError:
        pass


def _replace_path_prefix(value: Any, old_root: Path, new_root: Path) -> Any:
    old_prefix = str(old_root)
    new_prefix = str(new_root)
    if isinstance(value, str):
        return f"{new_prefix}{value[len(old_prefix):]}" if value.startswith(old_prefix) else value
    if isinstance(value, list):
        return [_replace_path_prefix(item, old_root, new_root) for item in value]
    if isinstance(value, dict):
        return {key: _replace_path_prefix(item, old_root, new_root) for key, item in value.items()}
    return value
