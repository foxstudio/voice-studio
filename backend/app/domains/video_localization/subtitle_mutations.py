from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
)
from app.domains.video_localization.subtitle_linkage import (
    SourceCueChange,
    SourceCueReplacement,
    audit_subtitle_linkages,
    localized_source_cue_ids,
    remap_localized_source_links,
)
from app.domains.video_localization.subtitles import (
    reconcile_localized_spoken_segments,
)
from app.models.schemas import VideoLocalizationTtsTask


MAPPING_UPDATED_FLAG = "source_mapping_updated"
MAPPING_REVIEW_FLAG = "source_mapping_needs_review"
MAPPING_ORPHANED_FLAG = "source_mapping_orphaned"


@dataclass(frozen=True)
class SubtitleDraftMutationResult:
    draft: VideoLocalizationDraft
    changed_source_cue_ids: tuple[str, ...] = ()
    removed_source_cue_ids: tuple[str, ...] = ()
    changed_localized_subtitle_ids: tuple[str, ...] = ()
    orphaned_localized_subtitle_ids: tuple[str, ...] = ()
    review_required_localized_subtitle_ids: tuple[str, ...] = ()


def delete_source_cue(
    draft: VideoLocalizationDraft,
    cue_id: str,
) -> SubtitleDraftMutationResult:
    """Delete one source cue while retaining dependent clip/task identities."""

    cue = _require_source_cue(draft, cue_id)
    change = SourceCueChange(cue_id, tuple(cue.source_word_ids), ())
    return _apply_source_cue_changes(
        draft,
        next_cues=[item for item in draft.cues if item.cue_id != cue_id],
        changes=[change],
        changed_source_cue_ids=(),
        removed_source_cue_ids=(cue_id,),
    )


def delete_localized_subtitle(
    draft: VideoLocalizationDraft,
    subtitle_id: str,
) -> SubtitleDraftMutationResult:
    """Delete one target subtitle without deleting an already generated audio asset.

    Source cues keep their stable identities. Their compatibility mirror text
    is rebuilt from the remaining target subtitles, while existing dub clips
    are detached from the removed target so they stay usable on the timeline.
    """

    subtitle = _require_localized_subtitle(draft, subtitle_id)
    affected_source_ids = tuple(localized_source_cue_ids(subtitle))
    next_subtitles = [item for item in draft.localized_subtitles if item.subtitle_id != subtitle_id]
    next_cues = _sync_localized_mirrors(draft.cues, next_subtitles, affected_source_ids)
    next_clips: list[dict] = []
    for clip in draft.timeline_clips:
        target_ids = [
            str(value)
            for value in clip.get("target_subtitle_ids") or []
            if str(value) and str(value) != subtitle_id
        ]
        if str(clip.get("subtitle_id") or "") != subtitle_id and target_ids == list(
            clip.get("target_subtitle_ids") or []
        ):
            next_clips.append(dict(clip))
            continue
        next_clip = dict(clip)
        if str(next_clip.get("subtitle_id") or "") == subtitle_id:
            next_clip["subtitle_id"] = None
        if "target_subtitle_ids" in next_clip:
            next_clip["target_subtitle_ids"] = target_ids
        next_clips.append(next_clip)

    return SubtitleDraftMutationResult(
        draft=draft.model_copy(
            update={
                "cues": next_cues,
                "localized_subtitles": next_subtitles,
                "localized_spoken_segments": (
                    reconcile_localized_spoken_segments(
                        draft.localized_spoken_segments,
                        next_subtitles,
                    )
                ),
                "timeline_clips": next_clips,
            }
        ),
        changed_source_cue_ids=affected_source_ids,
        changed_localized_subtitle_ids=(subtitle_id,),
    )


def merge_source_cues(
    draft: VideoLocalizationDraft,
    cue_ids: Sequence[str],
    *,
    survivor_cue_id: str | None = None,
) -> SubtitleDraftMutationResult:
    """Merge consecutive same-speaker source cues and migrate all stable links."""

    selected = _ordered_source_cues(draft, cue_ids)
    if len(selected) < 2:
        raise ValueError("source cue merge requires at least two cues")
    positions = [next(index for index, cue in enumerate(draft.cues) if cue.cue_id == item.cue_id) for item in selected]
    if any(right != left + 1 for left, right in zip(positions, positions[1:])):
        raise ValueError("source cue merge requires consecutive cues")
    speaker_ids = {item.speaker_id or "unknown" for item in selected}
    if len(speaker_ids) > 1:
        raise ValueError("source cue merge cannot cross speakers")

    survivor_id = survivor_cue_id or selected[0].cue_id
    if survivor_id not in {item.cue_id for item in selected}:
        raise ValueError("survivor cue must be one of the merged cues")
    survivor = next(item for item in selected if item.cue_id == survivor_id)
    merged = _merged_source_cue(survivor, selected)
    selected_ids = {item.cue_id for item in selected}
    first_position = min(positions)
    next_cues = [item for item in draft.cues if item.cue_id not in selected_ids]
    next_cues.insert(first_position, merged)
    merged_words = tuple(merged.source_word_ids)
    replacement = SourceCueReplacement(survivor_id, merged_words)
    changes = [
        SourceCueChange(item.cue_id, tuple(item.source_word_ids), (replacement,))
        for item in selected
    ]
    return _apply_source_cue_changes(
        draft,
        next_cues=next_cues,
        changes=changes,
        changed_source_cue_ids=(survivor_id,),
        removed_source_cue_ids=tuple(item.cue_id for item in selected if item.cue_id != survivor_id),
    )


def split_source_cue(
    draft: VideoLocalizationDraft,
    cue_id: str,
    replacements: Sequence[VideoLocalizationCue],
) -> SubtitleDraftMutationResult:
    """Replace one source cue with word-partitioned children and migrate links."""

    source = _require_source_cue(draft, cue_id)
    children = tuple(sorted(replacements, key=lambda item: (item.start_ms or 0, item.end_ms or 0, item.cue_id)))
    if len(children) < 2:
        raise ValueError("source cue split requires at least two replacement cues")
    child_ids = [item.cue_id for item in children]
    if len(set(child_ids)) != len(child_ids):
        raise ValueError("source cue split replacement IDs must be unique")
    other_ids = {item.cue_id for item in draft.cues if item.cue_id != cue_id}
    if any(child_id in other_ids for child_id in child_ids):
        raise ValueError("source cue split replacement ID already exists")
    if any(item.end_ms is None or item.start_ms is None or item.end_ms <= item.start_ms for item in children):
        raise ValueError("source cue split replacements require valid timing")
    if any(item.speaker_id != source.speaker_id for item in children):
        raise ValueError("source cue split replacements must preserve speaker")

    original_words = tuple(dict.fromkeys(source.source_word_ids))
    replacement_words = tuple(dict.fromkeys(word_id for item in children for word_id in item.source_word_ids))
    if original_words and replacement_words != original_words:
        raise ValueError("source cue split replacements must preserve source word order and coverage")

    source_position = next(index for index, item in enumerate(draft.cues) if item.cue_id == cue_id)
    next_cues = [item for item in draft.cues if item.cue_id != cue_id]
    next_cues[source_position:source_position] = children
    change = SourceCueChange(
        cue_id,
        tuple(source.source_word_ids),
        tuple(SourceCueReplacement(item.cue_id, tuple(item.source_word_ids)) for item in children),
    )
    return _apply_source_cue_changes(
        draft,
        next_cues=next_cues,
        changes=[change],
        changed_source_cue_ids=tuple(child_ids),
        removed_source_cue_ids=() if cue_id in child_ids else (cue_id,),
    )


def split_localized_subtitle(
    draft: VideoLocalizationDraft,
    subtitle_id: str,
    children: Sequence[VideoLocalizationSubtitleCue],
    *,
    source_word_ids_by_subtitle_id: Mapping[str, Sequence[str]] | None = None,
) -> SubtitleDraftMutationResult:
    """Split one localized subtitle without copying confirmed source evidence.

    The first child must retain the original subtitle ID so existing timeline
    clips and TTS workflows keep their segment identity. Callers may supply
    explicit word partitions. Without them, both children retain the source
    cue range but are marked for mapping review.
    """

    original = _require_localized_subtitle(draft, subtitle_id)
    child_items = tuple(children)
    if len(child_items) < 2:
        raise ValueError("localized subtitle split requires at least two children")
    if child_items[0].subtitle_id != subtitle_id:
        raise ValueError("first localized split child must retain the original subtitle ID")
    child_ids = [item.subtitle_id for item in child_items]
    if len(set(child_ids)) != len(child_ids):
        raise ValueError("localized split child IDs must be unique")
    existing_other_ids = {item.subtitle_id for item in draft.localized_subtitles if item.subtitle_id != subtitle_id}
    if any(child_id in existing_other_ids for child_id in child_ids):
        raise ValueError("localized split child ID already exists")
    if child_items[0].start_ms != original.start_ms or child_items[-1].end_ms != original.end_ms:
        raise ValueError("localized split children must preserve the original time range")
    if any(left.end_ms != right.start_ms for left, right in zip(child_items, child_items[1:])):
        raise ValueError("localized split children must form a contiguous time partition")

    cue_by_id = {item.cue_id: item for item in draft.cues}
    original_source_ids = localized_source_cue_ids(original)
    original_words = set(original.source_word_ids)
    explicit_partitions = source_word_ids_by_subtitle_id is not None
    normalized_children: list[VideoLocalizationSubtitleCue] = []
    review_ids: list[str] = []
    for child in child_items:
        if explicit_partitions:
            child_words = tuple(dict.fromkeys(source_word_ids_by_subtitle_id.get(child.subtitle_id, ())))
        else:
            child_words = ()
        needs_review = not explicit_partitions
        if explicit_partitions and any(word_id not in original_words for word_id in child_words):
            needs_review = True
        source_ids = [
            cue_id
            for cue_id in original_source_ids
            if child_words
            and cue_id in cue_by_id
            and set(cue_by_id[cue_id].source_word_ids).intersection(child_words)
        ]
        if not source_ids:
            source_ids = list(original_source_ids)
            needs_review = True
        flags = _mapping_flags(
            child.quality_flags,
            changed=True,
            orphaned=not source_ids,
            review_required=needs_review,
        )
        normalized_children.append(
            child.model_copy(
                update={
                    "linked_cue_id": source_ids[0] if source_ids else None,
                    "source_cue_ids": source_ids,
                    "source_word_ids": list(child_words),
                    # A display-track split refines timing/text inside the same
                    # localized spoken unit.  Letting callers invent new spoken
                    # segment IDs would orphan both children from planning.
                    "spoken_segment_id": original.spoken_segment_id,
                    "tts_result_id": None,
                    "tts_generation_id": None,
                    "tts_audio_path": None,
                    "tts_batch_task_id": None,
                    "tts_batch_status": None,
                    "tts_batch_error": None,
                    "tts_attempted_at": None,
                    "generated_duration_ms": None,
                    "quality_flags": flags,
                }
            )
        )
        if needs_review:
            review_ids.append(child.subtitle_id)

    if explicit_partitions:
        assigned_words = [word_id for item in normalized_children for word_id in item.source_word_ids]
        if set(assigned_words) != original_words or len(assigned_words) != len(set(assigned_words)):
            review_ids.extend(child_ids)
            normalized_children = [
                item.model_copy(
                    update={
                        "quality_flags": _mapping_flags(
                            item.quality_flags,
                            changed=True,
                            orphaned=not localized_source_cue_ids(item),
                            review_required=True,
                        )
                    }
                )
                for item in normalized_children
            ]

    position = next(index for index, item in enumerate(draft.localized_subtitles) if item.subtitle_id == subtitle_id)
    next_subtitles = [item for item in draft.localized_subtitles if item.subtitle_id != subtitle_id]
    next_subtitles[position:position] = normalized_children
    affected_source_ids = tuple(dict.fromkeys(original_source_ids))
    next_cues = _sync_localized_mirrors(draft.cues, next_subtitles, affected_source_ids)
    next_clips, next_tasks = _sync_assets_from_subtitles(
        draft.timeline_clips,
        draft.tts_tasks,
        next_subtitles,
        changes=(),
    )
    next_draft = draft.model_copy(
        update={
            "cues": next_cues,
            "localized_subtitles": next_subtitles,
            "localized_spoken_segments": (
                reconcile_localized_spoken_segments(
                    draft.localized_spoken_segments,
                    next_subtitles,
                )
            ),
            "timeline_clips": next_clips,
            "tts_tasks": next_tasks,
        }
    )
    audits = {item.subtitle_id: item for item in audit_subtitle_linkages(next_cues, normalized_children)}
    orphaned = tuple(
        child_id
        for child_id in child_ids
        if any(code in {"source_cues_missing", "source_cue_not_found"} for code in audits[child_id].codes)
    )
    return SubtitleDraftMutationResult(
        draft=next_draft,
        changed_source_cue_ids=affected_source_ids,
        changed_localized_subtitle_ids=tuple(child_ids),
        orphaned_localized_subtitle_ids=orphaned,
        review_required_localized_subtitle_ids=tuple(dict.fromkeys(review_ids)),
    )


def _apply_source_cue_changes(
    draft: VideoLocalizationDraft,
    *,
    next_cues: Sequence[VideoLocalizationCue],
    changes: Sequence[SourceCueChange],
    changed_source_cue_ids: tuple[str, ...],
    removed_source_cue_ids: tuple[str, ...],
) -> SubtitleDraftMutationResult:
    remapped = remap_localized_source_links(draft.localized_subtitles, changes)
    audit_by_id = {
        item.subtitle_id: item for item in audit_subtitle_linkages(next_cues, remapped.subtitles)
    }
    changed_ids = set(remapped.changed_subtitle_ids)
    orphaned_ids = set(remapped.orphaned_subtitle_ids)
    review_ids = set(remapped.review_required_subtitle_ids)
    next_subtitles: list[VideoLocalizationSubtitleCue] = []
    for subtitle in remapped.subtitles:
        audit = audit_by_id[subtitle.subtitle_id]
        orphaned = bool({"source_cues_missing", "source_cue_not_found"}.intersection(audit.codes))
        review_required = orphaned or bool(
            {
                "source_cues_noncontiguous",
                "source_cues_cross_speaker",
                "source_words_outside_linked_cues",
                "source_words_multiply_assigned",
                "shared_source_without_word_partition",
            }.intersection(audit.codes)
        )
        if orphaned:
            orphaned_ids.add(subtitle.subtitle_id)
        if review_required:
            review_ids.add(subtitle.subtitle_id)
        if subtitle.subtitle_id in changed_ids or orphaned or review_required:
            subtitle = subtitle.model_copy(
                update={
                    "quality_flags": _mapping_flags(
                        subtitle.quality_flags,
                        changed=subtitle.subtitle_id in changed_ids,
                        orphaned=orphaned,
                        review_required=review_required,
                    )
                }
            )
        next_subtitles.append(subtitle)

    affected_source_ids = tuple(
        dict.fromkeys(
            [
                *(change.cue_id for change in changes),
                *(replacement.cue_id for change in changes for replacement in change.replacements),
            ]
        )
    )
    mirrored_cues = _sync_localized_mirrors(next_cues, next_subtitles, affected_source_ids)
    next_clips, next_tasks = _sync_assets_from_subtitles(
        draft.timeline_clips,
        draft.tts_tasks,
        next_subtitles,
        changes,
    )
    next_draft = draft.model_copy(
        update={
            "cues": mirrored_cues,
            "localized_subtitles": next_subtitles,
            "timeline_clips": next_clips,
            "tts_tasks": next_tasks,
        }
    )
    return SubtitleDraftMutationResult(
        draft=next_draft,
        changed_source_cue_ids=changed_source_cue_ids,
        removed_source_cue_ids=removed_source_cue_ids,
        changed_localized_subtitle_ids=tuple(remapped.changed_subtitle_ids),
        orphaned_localized_subtitle_ids=tuple(
            subtitle.subtitle_id for subtitle in next_subtitles if subtitle.subtitle_id in orphaned_ids
        ),
        review_required_localized_subtitle_ids=tuple(
            subtitle.subtitle_id for subtitle in next_subtitles if subtitle.subtitle_id in review_ids
        ),
    )


def _require_source_cue(draft: VideoLocalizationDraft, cue_id: str) -> VideoLocalizationCue:
    cue = next((item for item in draft.cues if item.cue_id == cue_id), None)
    if cue is None:
        raise KeyError(f"source cue not found: {cue_id}")
    return cue


def _require_localized_subtitle(
    draft: VideoLocalizationDraft,
    subtitle_id: str,
) -> VideoLocalizationSubtitleCue:
    subtitle = next((item for item in draft.localized_subtitles if item.subtitle_id == subtitle_id), None)
    if subtitle is None:
        raise KeyError(f"localized subtitle not found: {subtitle_id}")
    return subtitle


def _ordered_source_cues(
    draft: VideoLocalizationDraft,
    cue_ids: Sequence[str],
) -> tuple[VideoLocalizationCue, ...]:
    requested = tuple(dict.fromkeys(cue_ids))
    selected = tuple(item for item in draft.cues if item.cue_id in requested)
    if len(selected) != len(requested):
        missing = [cue_id for cue_id in requested if not any(item.cue_id == cue_id for item in selected)]
        raise KeyError(f"source cue not found: {', '.join(missing)}")
    return selected


def _merged_source_cue(
    survivor: VideoLocalizationCue,
    selected: Sequence[VideoLocalizationCue],
) -> VideoLocalizationCue:
    start_ms = min(int(item.start_ms or 0) for item in selected)
    end_ms = max(int(item.end_ms or start_ms) for item in selected)

    def joined(field: str, *, dedupe: bool = False) -> str | None:
        values = [str(getattr(item, field) or "").strip() for item in selected]
        values = [value for value in values if value]
        if dedupe:
            values = list(dict.fromkeys(values))
        return "\n".join(values) or None

    confidence_rank = {None: 0, "high": 1, "medium": 2, "low": 3}
    timing_confidence = max(
        (item.timing_confidence for item in selected),
        key=lambda value: confidence_rank[value],
    )
    transcription_revision_id = selected[0].transcription_revision_id
    if any(item.transcription_revision_id != transcription_revision_id for item in selected[1:]):
        transcription_revision_id = None
    return survivor.model_copy(
        update={
            "start_ms": start_ms,
            "end_ms": end_ms,
            "en_subtitle_text": joined("en_subtitle_text"),
            "zh_localized_subtitle_text": None,
            "tts_recommended_text": None,
            "source_word_ids": list(dict.fromkeys(word_id for item in selected for word_id in item.source_word_ids)),
            "source_text_raw": joined("source_text_raw", dedupe=True),
            "source_duration_ms": max(0, end_ms - start_ms),
            "timing_confidence": timing_confidence,
            "transcription_revision_id": transcription_revision_id,
            "tts_result_id": None,
            "tts_generation_id": None,
            "tts_audio_path": None,
            "tts_batch_task_id": None,
            "tts_batch_status": None,
            "tts_batch_error": None,
            "tts_attempted_at": None,
            "generated_duration_ms": None,
            "review_status": "needs_review",
            "manual_timing_revision": max(item.manual_timing_revision for item in selected) + 1,
            "manual_timing_review_status": "required",
            "manual_timing_confirmed_revision": None,
            "manual_timing_confirmed_at": None,
            "manual_timing_confirmed_start_ms": None,
            "manual_timing_confirmed_end_ms": None,
            "manual_timing_confirmation_method": None,
            "quality_flags": list(
                dict.fromkeys([*(flag for item in selected for flag in item.quality_flags), "timeline_merge"])
            ),
        }
    )


def _mapping_flags(
    flags: Sequence[str],
    *,
    changed: bool,
    orphaned: bool,
    review_required: bool,
) -> list[str]:
    next_flags = [flag for flag in flags if flag != MAPPING_ORPHANED_FLAG or orphaned]
    if changed:
        next_flags.append(MAPPING_UPDATED_FLAG)
    if orphaned:
        next_flags.append(MAPPING_ORPHANED_FLAG)
    if review_required:
        next_flags.append(MAPPING_REVIEW_FLAG)
    return list(dict.fromkeys(next_flags))


def _sync_localized_mirrors(
    cues: Sequence[VideoLocalizationCue],
    subtitles: Sequence[VideoLocalizationSubtitleCue],
    affected_cue_ids: Sequence[str],
) -> list[VideoLocalizationCue]:
    affected = set(affected_cue_ids)
    output: list[VideoLocalizationCue] = []
    for cue in cues:
        if cue.cue_id not in affected:
            output.append(cue)
            continue
        related = [item for item in subtitles if cue.cue_id in localized_source_cue_ids(item)]
        display_text = "\n".join(item.text.strip() for item in related if item.text.strip()) or None
        tts_text = "\n".join(
            item.tts_text.strip() for item in related if item.tts_text and item.tts_text.strip()
        ) or None
        output.append(
            cue.model_copy(
                update={
                    "zh_localized_subtitle_text": display_text,
                    "tts_recommended_text": tts_text,
                    "quality_flags": list(dict.fromkeys([*cue.quality_flags, "localized_track_sync"])),
                }
            )
        )
    return output


def _sync_assets_from_subtitles(
    timeline_clips: Sequence[dict],
    tasks: Sequence[VideoLocalizationTtsTask],
    subtitles: Sequence[VideoLocalizationSubtitleCue],
    changes: Sequence[SourceCueChange],
) -> tuple[list[dict], list[VideoLocalizationTtsTask]]:
    subtitle_by_id = {item.subtitle_id: item for item in subtitles}
    change_by_id = {item.cue_id: item for item in changes}
    next_clips: list[dict] = []
    for clip in timeline_clips:
        next_clip = dict(clip)
        subtitle = subtitle_by_id.get(str(clip.get("subtitle_id") or ""))
        if subtitle is not None:
            source_ids = list(localized_source_cue_ids(subtitle))
        else:
            current_source_ids = clip.get("source_cue_ids") or ([clip.get("cue_id")] if clip.get("cue_id") else [])
            if not any(str(cue_id) in change_by_id for cue_id in current_source_ids):
                next_clips.append(next_clip)
                continue
            source_ids = _remap_asset_source_ids(current_source_ids, change_by_id)
        next_clip["source_cue_ids"] = source_ids
        if "cue_id" in next_clip or source_ids:
            next_clip["cue_id"] = source_ids[0] if source_ids else None
        next_clips.append(next_clip)

    next_tasks: list[VideoLocalizationTtsTask] = []
    for task in tasks:
        subtitle = subtitle_by_id.get(task.segment_id)
        source_ids = (
            list(localized_source_cue_ids(subtitle))
            if subtitle is not None
            else _remap_asset_source_ids(task.source_cue_ids, change_by_id)
        )
        next_tasks.append(
            task if source_ids == task.source_cue_ids else task.model_copy(update={"source_cue_ids": source_ids})
        )
    return next_clips, next_tasks


def _remap_asset_source_ids(
    source_cue_ids: Sequence[str],
    change_by_id: Mapping[str, SourceCueChange],
) -> list[str]:
    output: list[str] = []
    for cue_id in source_cue_ids:
        change = change_by_id.get(str(cue_id))
        if change is None:
            output.append(str(cue_id))
        else:
            output.extend(item.cue_id for item in change.replacements)
    return list(dict.fromkeys(cue_id for cue_id in output if cue_id))
