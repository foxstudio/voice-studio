"""Ownership rules for durable user-arranged timeline media."""

from __future__ import annotations

from collections.abc import Mapping

from app.domains.video_localization.schemas import VideoLocalizationDraft


_PROVISIONAL_STATUSES = {
    "queued",
    "running",
    "applying",
    "postprocessing",
    "retrying",
}


def is_provisional_timeline_clip(clip: Mapping[str, object]) -> bool:
    """Return whether a clip is only a replaceable runtime/task projection."""

    if clip.get("optimistic_tts_workflow_id") or clip.get(
        "optimistic_history_result_id"
    ):
        return True
    status = str(clip.get("status") or "").strip().lower()
    return status in _PROVISIONAL_STATUSES and not (
        clip.get("audio_path")
        or clip.get("result_id")
        or clip.get("candidate_id")
    )


def mark_tts_target_binding_stale(
    draft: VideoLocalizationDraft,
    clip: Mapping[str, object],
) -> dict:
    """Keep placed media unchanged while marking its generation target stale."""

    next_clip = dict(clip)
    next_clip["tts_target_binding_status"] = "stale"
    if not str(next_clip.get("tts_target_text") or "").strip():
        frozen_text = tts_target_text(draft, next_clip)
        if frozen_text:
            next_clip["tts_target_text"] = frozen_text
    return next_clip


def tts_target_text(
    draft: VideoLocalizationDraft,
    clip: Mapping[str, object],
) -> str:
    target_ids = [
        str(value)
        for value in clip.get("target_subtitle_ids") or []
        if value
    ]
    if not target_ids and clip.get("subtitle_id"):
        target_ids = [str(clip["subtitle_id"])]
    subtitle_by_id = {
        item.subtitle_id: item
        for item in draft.localized_subtitles
    }
    return "\n".join(
        (subtitle_by_id[item].tts_text or subtitle_by_id[item].text).strip()
        for item in target_ids
        if item in subtitle_by_id
        and (
            subtitle_by_id[item].tts_text
            or subtitle_by_id[item].text
        ).strip()
    )


__all__ = [
    "is_provisional_timeline_clip",
    "mark_tts_target_binding_stale",
    "tts_target_text",
]
