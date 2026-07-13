from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from fastapi import UploadFile

from app.domains.video_localization import audio_access
from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization import draft_store
from app.domains.video_localization import exporting
from app.domains.video_localization import localization
from app.domains.video_localization import media_assets
from app.domains.video_localization import project_manifest
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
    VideoLocalizationReferenceClipCreate,
    VideoLocalizationReferenceClipUpdate,
    VideoLocalizationSpeakerCreate,
    VideoLocalizationSpeakerUpdate,
    VideoLocalizationSubtitleImportRequest,
)
from app.services import project_store

VIDEO_LOCALIZATION_KEY = draft_store.VIDEO_LOCALIZATION_KEY


def get_video_localization(project_id: str) -> VideoLocalizationDraft | None:
    return draft_store.get(project_id)


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
    current = draft_store.get(project_id)
    if current and current.updated_at and draft.updated_at and current.updated_at != draft.updated_at:
        raise AppException(409, "VIDEO_LOCALIZATION_DRAFT_CONFLICT", "Project changed while this draft was being edited")
    return draft_store.save(project_id, draft)


def update_video_localization_ui_state(project_id: str, patch: dict[str, Any]) -> VideoLocalizationDraft | None:
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
    if any(operation.status == "running" for operation in draft.operations):
        raise AppException(409, "VIDEO_LOCALIZATION_RESET_BLOCKED", "当前仍有后台任务在运行，请先取消或等待完成后再清空")

    media_assets.clear_project_video_localization_dir(project_id)
    if VIDEO_LOCALIZATION_KEY in project.parameters:
        project.parameters = {key: value for key, value in project.parameters.items() if key != VIDEO_LOCALIZATION_KEY}
        project_store.save_project(project)
    return VideoLocalizationDraft()


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


def extract_source_audio(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, source_pipeline.with_extracted_source_audio(project_id, draft))


def separate_source_audio(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = source_pipeline.with_separated_source_audio(project_id, draft)
    new_paths = [next_draft.stems.vocals_clean_path, next_draft.stems.background_path]
    previous_paths = {draft.stems.vocals_clean_path, draft.stems.background_path}
    try:
        saved = save_video_localization(project_id, next_draft)
    except Exception:
        for value in new_paths:
            if value and value not in previous_paths:
                Path(value).unlink(missing_ok=True)
        raise
    if saved:
        media_assets.cleanup_unreferenced_stems(project_id, [saved.stems.vocals_clean_path, saved.stems.background_path])
    return saved


def transcribe_english_source_audio(project_id: str, engine_id: str = "faster-whisper-turbo") -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, source_pipeline.with_english_asr(draft, engine_id))


def import_subtitles(project_id: str, kind: str, request: VideoLocalizationSubtitleImportRequest) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = subtitles.import_srt(draft, kind, request.srt_text, update_timing=request.update_timing, overwrite_tts=request.overwrite_tts)
    return save_video_localization(project_id, next_draft)


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
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    next_draft = cue_tools.with_updated_cue(draft, cue_id, patch)
    return save_video_localization(project_id, speakers.reconcile_speakers(next_draft))


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
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    return save_video_localization(project_id, localization.with_chinese_draft(draft))


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
    clip = next((dict(item) for item in draft.timeline_clips if dict(item).get("clip_id") == clip_id), None)
    if not clip or not clip.get("audio_path"):
        return None
    path = Path(str(clip["audio_path"]))
    return path if path.exists() else None


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
