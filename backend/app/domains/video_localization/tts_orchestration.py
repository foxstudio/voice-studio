from __future__ import annotations

from app.domains.video_localization import media_assets
from app.domains.video_localization import tts_pipeline
from app.errors import AppException
from app.domains.video_localization.schemas import BatchGenerateRequest, VideoLocalizationDraft, now_iso
from app.services import batch_queue


def build_batch_request(
    *,
    project_id: str,
    project_name: str,
    draft: VideoLocalizationDraft,
    engine_id: str = "indextts-v2",
) -> BatchGenerateRequest:
    return tts_pipeline.build_batch_request(
        project_id=project_id,
        project_name=project_name,
        draft=draft,
        output_dir=media_assets.project_video_localization_dir(project_id) / "tts",
        engine_id=engine_id,
    )


def mark_batch_submitted(draft: VideoLocalizationDraft, batch_task_id: str, cue_ids: list[str]) -> VideoLocalizationDraft:
    return tts_pipeline.with_batch_submitted(draft, batch_task_id, cue_ids, attempted_at=now_iso())


def sync_batch_results(project_id: str, draft: VideoLocalizationDraft, batch_task_id: str) -> VideoLocalizationDraft:
    batch = batch_queue.get_batch(batch_task_id)
    if not batch:
        raise AppException(404, "VIDEO_LOCALIZATION_TTS_BATCH_NOT_FOUND", "TTS batch task not found")
    request_parameters = batch.parameters.get("parameters") if isinstance(batch.parameters, dict) else None
    if not isinstance(request_parameters, dict) or request_parameters.get("source") != "video_localization" or request_parameters.get("project_id") != project_id:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_BATCH_PROJECT_MISMATCH", "Batch task does not belong to this video localization project")
    return tts_pipeline.with_synced_batch_results(draft, batch)


def sync_single_result(
    draft: VideoLocalizationDraft,
    cue_id: str,
    *,
    result_id: str,
    output_path: str,
    duration_ms: int | None,
) -> VideoLocalizationDraft:
    return tts_pipeline.with_single_tts_result(draft, cue_id, result_id=result_id, output_path=output_path, duration_ms=duration_ms)
