"""Field-level optimistic merges for editor-owned cue/subtitle collections."""

from __future__ import annotations

from pydantic import ValidationError

from app.domains.video_localization import cues, localization_tracks, subtitles
from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
)
from app.errors import AppException


_CUE_FIELDS = frozenset({
    "speaker_id", "start_ms", "end_ms", "audio_route", "en_subtitle_text",
    "zh_localized_subtitle_text", "tts_recommended_text", "reference_clip_id",
    "review_status", "notes", "source_word_ids", "source_text_raw",
})
_SUBTITLE_FIELDS = frozenset({
    "start_ms", "end_ms", "text", "tts_text", "linked_cue_id", "source_cue_ids",
    "source_word_ids", "spoken_segment_id", "adaptation_note",
})
_MEDIA_IDENTITY_FIELDS = frozenset({"tts_result_id", "tts_generation_id", "tts_batch_task_id"})


def _conflict(collection: str, item_id: str, fields=()) -> None:
    raise AppException(
        409,
        "VIDEO_LOCALIZATION_TIMELINE_COLLECTION_CHANGED",
        "这条字幕已被修改，旧编辑没有覆盖新结果。请刷新后重试。",
        {"collection": collection, "item_id": item_id, "fields": sorted(fields)},
    )


def _merge(current, change, *, collection: str, key: str, fields: frozenset, model):
    before = {getattr(item, key): item for item in change.expected}
    desired = {getattr(item, key): item for item in change.desired}
    latest = {getattr(item, key): item for item in current}
    changed_ids: set[str] = set()
    for item_id, previous in before.items():
        item = latest.get(item_id)
        wanted = desired.get(item_id)
        touched = fields if wanted is None else {
            field for field in fields if getattr(previous, field) != getattr(wanted, field)
        }
        if not touched:
            continue
        # Already committed deletion is safe to acknowledge after a lost reply.
        if wanted is None and item is None:
            continue
        if item is None:
            _conflict(collection, item_id, touched)
        compare_fields = touched | (_MEDIA_IDENTITY_FIELDS if wanted is None else frozenset())
        conflicts = {
            field for field in compare_fields
            if getattr(item, field) != getattr(previous, field)
            and (wanted is None or getattr(item, field) != getattr(wanted, field))
        }
        if conflicts:
            _conflict(collection, item_id, conflicts)
        changed_ids.add(item_id)
        if wanted is None:
            del latest[item_id]
        else:
            updates = {field: getattr(wanted, field) for field in touched}
            if model is VideoLocalizationCue:
                updates = cues.with_manual_edit_provenance(item, updates)
            latest[item_id] = item.model_copy(update=updates)
    for item_id, wanted in desired.items():
        if item_id in before:
            continue
        if item_id in latest:
            if any(getattr(latest[item_id], field) != getattr(wanted, field) for field in fields):
                _conflict(collection, item_id, fields)
            continue
        # Client snapshots may carry old TTS/audit metadata. New editorial
        # entities never acquire those backend-owned locators or receipts.
        latest[item_id] = model(**{key: item_id, **{
            field: getattr(wanted, field) for field in fields
        }})
        changed_ids.add(item_id)

    order = [getattr(item, key) for item in current if getattr(item, key) in latest]
    retained_before = [item_id for item_id in before if item_id in desired]
    retained_desired = [item_id for item_id in desired if item_id in before]
    if retained_before != retained_desired:
        observed = [item_id for item_id in order if item_id in retained_before]
        if observed not in (retained_before, retained_desired):
            _conflict(collection, "", ["order"])
        replacements = iter(retained_desired)
        order = [next(replacements) if item_id in retained_before else item_id for item_id in order]
    desired_order = list(desired)
    for index, item_id in enumerate(desired_order):
        if item_id in order or item_id not in latest:
            continue
        following = next((value for value in desired_order[index + 1:] if value in order), None)
        order.insert(order.index(following) if following is not None else len(order), item_id)
    return [latest[item_id] for item_id in order], changed_ids


def apply_collection_changes(draft: VideoLocalizationDraft, patch) -> VideoLocalizationDraft:
    """Merge only edited entities, then validate their final combined values."""
    updated = draft
    try:
        if patch.cue_collection_change is not None:
            merged, changed = _merge(
                draft.cues, patch.cue_collection_change, collection="cues", key="cue_id",
                fields=_CUE_FIELDS, model=VideoLocalizationCue,
            )
            # Reuse the cue owner's provenance boundary on touched rows only;
            # unrelated ASR quality flags and confirmation records stay intact.
            affected = cues.sanitize_client_draft_timing_provenance(
                draft.model_copy(update={"cues": [item for item in draft.cues if item.cue_id in changed]}),
                draft.model_copy(update={"cues": [item for item in merged if item.cue_id in changed]}),
            )
            sanitized = {item.cue_id: VideoLocalizationCue.model_validate(item.model_dump()) for item in affected.cues}
            updated = updated.model_copy(update={
                "cues": [sanitized.get(item.cue_id, item) for item in merged],
            })
            if updated.cues != draft.cues:
                updated = localization_tracks.invalidate_formal_quality_binding(updated)
        if patch.localized_subtitle_collection_change is not None:
            merged, changed = _merge(
                draft.localized_subtitles, patch.localized_subtitle_collection_change,
                collection="localized_subtitles", key="subtitle_id",
                fields=_SUBTITLE_FIELDS, model=VideoLocalizationSubtitleCue,
            )
            updated = updated.model_copy(update={
                "localized_subtitles": [
                    VideoLocalizationSubtitleCue.model_validate(item.model_dump())
                    if item.subtitle_id in changed else item for item in merged
                ],
            })
            if updated.localized_subtitles != draft.localized_subtitles:
                updated = subtitles.with_editorial_subtitle_collection(draft, updated, changed)
    except ValidationError as exc:
        raise AppException(
            422, "VIDEO_LOCALIZATION_TIMELINE_COLLECTION_INVALID",
            "字幕修改后的时间或内容无效。",
        ) from exc
    return updated
