"""Pure invalidation rules for generated speech bound to localized subtitles."""

from __future__ import annotations

from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    now_iso,
)
from app.domains.video_localization.timeline_clip_ownership import (
    is_provisional_timeline_clip,
    mark_tts_target_binding_stale,
)


def invalidate_after_localized_subtitle_edit(
    before: VideoLocalizationDraft,
    after: VideoLocalizationDraft,
    subtitle_id: str,
) -> VideoLocalizationDraft:
    """Detach generated speech when its frozen target content or range changed."""

    previous = next(
        item
        for item in before.localized_subtitles
        if item.subtitle_id == subtitle_id
    )
    current = next(
        (item
        for item in after.localized_subtitles
        if item.subtitle_id == subtitle_id),
        None,
    )
    previous_spoken_text = (previous.tts_text or previous.text).strip()
    current_spoken_text = (current.tts_text or current.text).strip() if current else ""
    target_changed = (
        current is None
        or previous_spoken_text != current_spoken_text
        or previous.start_ms != current.start_ms
        or previous.end_ms != current.end_ms
    )
    if not target_changed:
        return after

    affected_clip_ids: set[str] = set()
    discarded_ids = {
        str(value)
        for value in after.ui_state.get("discarded_tts_task_ids", [])
        if isinstance(value, str) and value
    }
    next_clips: list[dict] = []
    for clip in after.timeline_clips:
        target_ids = {
            str(value)
            for value in clip.get("target_subtitle_ids") or []
            if value
        }
        targets_changed_subtitle = (
            subtitle_id in target_ids
            or (
                not target_ids
                and str(clip.get("subtitle_id") or "") == subtitle_id
            )
        )
        if not targets_changed_subtitle:
            next_clips.append(dict(clip))
            continue
        clip_id = str(clip.get("clip_id") or "")
        if clip_id:
            affected_clip_ids.add(clip_id)
        if is_provisional_timeline_clip(clip):
            discarded_ids.update(
                str(value)
                for value in (
                    clip.get("task_id"),
                    clip.get("generation_id"),
                    clip.get("optimistic_tts_workflow_id"),
                )
                if value
            )
            continue
        next_clips.append(mark_tts_target_binding_stale(before, clip))

    affected_workflow_ids: set[str] = set()
    active_workflow_ids: set[str] = set()
    affected_latest_task_ids: set[str] = set()
    for task in after.tts_tasks:
        target_ids = _task_target_ids(task)
        if (
            subtitle_id in target_ids
            or (
                task.timeline_clip_id
                and task.timeline_clip_id in affected_clip_ids
            )
            or (
                task.generation_task_id
                and task.generation_task_id in discarded_ids
            )
            or (not target_ids and task.segment_id == subtitle_id)
        ):
            affected_workflow_ids.add(task.workflow_id)
            affected_latest_task_ids.add(task.workflow_id)
            if task.generation_task_id:
                affected_latest_task_ids.add(task.generation_task_id)
            if task.status not in {"success", "failed", "cancelled"}:
                active_workflow_ids.add(task.workflow_id)
                discarded_ids.add(task.workflow_id)
                if task.generation_task_id:
                    discarded_ids.add(task.generation_task_id)

    invalidated_at = now_iso()
    stale_artifact_present = bool(
        previous.tts_result_id
        or previous.tts_generation_id
        or previous.tts_audio_path
        or previous.tts_batch_task_id
        or affected_clip_ids
        or affected_workflow_ids
    )
    next_subtitles = [
        item.model_copy(
            update={
                "tts_result_id": None,
                "tts_generation_id": None,
                "tts_audio_path": None,
                "tts_batch_task_id": None,
                "tts_batch_status": None,
                "tts_batch_error": None,
                "tts_attempted_at": None,
                "generated_duration_ms": None,
                "quality_flags": list(
                    dict.fromkeys(
                        [
                            *(
                                flag
                                for flag in item.quality_flags
                                if flag != "tts_generated"
                            ),
                            *(
                                ["tts_result_stale"]
                                if stale_artifact_present
                                else []
                            ),
                        ]
                    )
                ),
            }
        )
        if item.subtitle_id == subtitle_id
        else item
        for item in after.localized_subtitles
    ]
    latest = after.ui_state.get("latest_tts_task_by_segment", {})
    next_latest = {
        key: value
        for key, value in (
            latest.items() if isinstance(latest, dict) else []
        )
        if key != subtitle_id
        and str(value or "") not in discarded_ids
        and str(value or "") not in affected_latest_task_ids
    }
    return after.model_copy(
        update={
            "localized_subtitles": next_subtitles,
            "timeline_clips": next_clips,
            "tts_tasks": [
                _cancel_active_task(
                    task,
                    affected_workflow_ids=active_workflow_ids,
                    invalidated_at=invalidated_at,
                )
                for task in after.tts_tasks
            ],
            "ui_state": {
                **after.ui_state,
                **(
                    {
                        "discarded_tts_task_ids": sorted(discarded_ids)
                    }
                    if discarded_ids
                    else {}
                ),
                **(
                    {"latest_tts_task_by_segment": next_latest}
                    if isinstance(latest, dict)
                    else {}
                ),
            },
        }
    )


def _task_target_ids(task) -> set[str]:
    target_ids: set[str] = set()
    for stage in task.stages:
        parameters = stage.parameters
        values = parameters.get(
            "video_localization_target_subtitle_ids"
        )
        if isinstance(values, list):
            target_ids.update(str(value) for value in values if value)
        snapshot = parameters.get("target_snapshot")
        if isinstance(snapshot, dict):
            target_ids.update(
                str(value)
                for value in snapshot.get("subtitle_ids") or []
                if value
            )
        parameter_pack = parameters.get(
            "video_localization_parameter_pack"
        )
        if isinstance(parameter_pack, dict):
            target = parameter_pack.get("target")
            if isinstance(target, dict):
                target_ids.update(
                    str(value)
                    for value in target.get("subtitle_ids") or []
                    if value
                )
    return target_ids


def _cancel_active_task(
    task,
    *,
    affected_workflow_ids: set[str],
    invalidated_at: str,
):
    if task.workflow_id not in affected_workflow_ids:
        return task
    if task.status in {"success", "failed", "cancelled"}:
        return task
    stages = []
    for stage in task.stages:
        if stage.status in {"success", "failed", "cancelled"}:
            stages.append(stage)
            continue
        stages.append(
            stage.model_copy(
                update={
                    "status": "cancelled",
                    "error_code": (
                        "VIDEO_LOCALIZATION_TTS_SUBTITLE_CHANGED"
                    ),
                    "error_message": (
                        "字幕内容或时间已修改，旧配音未放入轨道"
                    ),
                    "completed_at": invalidated_at,
                }
            )
        )
    return task.model_copy(
        update={
            "status": "cancelled",
            "stages": stages,
            "updated_at": invalidated_at,
            "completed_at": invalidated_at,
        }
    )


__all__ = ["invalidate_after_localized_subtitle_edit"]
