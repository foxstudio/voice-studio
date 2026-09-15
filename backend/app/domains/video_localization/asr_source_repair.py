"""Deletion-only repair of proven bad source-ASR segments.

This module deliberately has no store, operation, or model-engine dependency.
The service owns CAS, request-id idempotency, and receipt persistence; this
function owns the versioned draft transformation and its safety invariants.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domains.video_localization import localization_tracks
from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationTranscriptSegment,
)
from app.domains.video_localization.subtitle_linkage import (
    SourceCueChange,
    localized_source_cue_ids,
    remap_localized_source_links,
)
from app.schemas.video_localization_asr_repair import (
    AsrSourceRepairDeletedCue,
    AsrSourceRepairDeletedSegment,
    AsrSourceRepairDeletedWord,
    AsrSourceRepairReceipt,
    AsrSourceRepairRequest,
)


_REPAIR_REQUIRED_FLAG = "asr_source_repair_requires_review"
_SEGMENT_TIMING_INTERPOLATED_FLAG = "segment_timing_interpolated"
_STALE_LOCALIZATION_STATE_FIELDS = {
    "source_fingerprint",
    "final_script_fingerprint",
    "dual_tracks_fingerprint",
    "quality_gate_fingerprint",
    "spoken_segment_count",
    "subtitle_count",
    "semantic_tts_grouping",
}
_GENERATED_CUE_FIELDS = {"cue_id", "linked_cue_id", "source_cue_ids", "cue_ids"}
_GENERATED_WORD_FIELDS = {"source_word_ids", "word_ids"}


def repair_asr_source(
    draft: VideoLocalizationDraft,
    request: AsrSourceRepairRequest,
    *,
    new_revision_id: str,
) -> tuple[VideoLocalizationDraft, AsrSourceRepairReceipt]:
    """Return a copied draft after excluding only evidenced whole ASR segments.

    A source word that has reached Chinese, TTS, reference, candidate, or clip
    state cannot be safely guessed away.  Reject that request before changing
    any part of the draft instead of trying to migrate user content by timing.
    """

    transcript = draft.transcription
    if transcript is None:
        raise ValueError("asr source repair requires a transcription")
    if not new_revision_id or new_revision_id == transcript.revision_id:
        raise ValueError("asr source repair requires a distinct new revision")
    if request.transcription_revision_id != transcript.revision_id:
        raise ValueError("asr source repair transcription revision is stale")
    if transcript.source_track_id != "vocals" or transcript.alignment_source_track_id != "vocals":
        raise ValueError("asr source repair requires vocals source and alignment tracks")
    if (
        request.audio_sha256 != transcript.source_audio_sha256
        or request.audio_sha256 != transcript.alignment_audio_sha256
    ):
        raise ValueError("asr source repair audio hash does not match current source alignment")

    excluded_ids = set(request.excluded_segment_ids)
    segment_by_id = {segment.segment_id: segment for segment in transcript.segments}
    if len(segment_by_id) != len(transcript.segments):
        raise ValueError("asr source repair requires unique transcript segment IDs")
    missing = excluded_ids.difference(segment_by_id)
    if missing:
        raise ValueError(f"asr source repair segment not found: {sorted(missing)[0]}")
    if len(excluded_ids) == len(transcript.segments):
        raise ValueError("asr source repair cannot delete every transcript segment")

    all_word_ids = [word.word_id for word in transcript.words]
    if len(all_word_ids) != len(set(all_word_ids)):
        raise ValueError("asr source repair requires unique aligned word IDs")
    if any(word.segment_id not in segment_by_id for word in transcript.words):
        raise ValueError("asr source repair found an aligned word with no transcript segment")
    known_word_ids = set(all_word_ids)
    for cue in draft.cues:
        unknown_ids = set(cue.source_word_ids).difference(known_word_ids)
        if unknown_ids:
            raise ValueError("asr source repair found a cue with an unknown source word")

    deleted_segments = [
        segment for segment in transcript.segments if segment.segment_id in excluded_ids
    ]
    source_start = min(segment.start_ms for segment in deleted_segments)
    source_end = max(segment.end_ms for segment in deleted_segments)
    if (
        request.evidence.source_audio_start_ms > source_start
        or request.evidence.source_audio_end_ms < source_end
    ):
        raise ValueError("asr source repair evidence must cover every excluded segment")

    deleted_words = [word for word in transcript.words if word.segment_id in excluded_ids]
    if not deleted_words:
        raise ValueError("asr source repair excluded segments have no aligned words")
    deleted_word_ids = {word.word_id for word in deleted_words}
    remaining_words = [word for word in transcript.words if word.word_id not in deleted_word_ids]

    affected_cues = [
        cue for cue in draft.cues if set(cue.source_word_ids).intersection(deleted_word_ids)
    ]
    deleted_cues = [
        cue
        for cue in affected_cues
        if not _remaining_cue_word_ids(cue, deleted_word_ids)
    ]
    deleted_cue_ids = {cue.cue_id for cue in deleted_cues}
    affected_cue_ids = {cue.cue_id for cue in affected_cues}

    _reject_unsafe_dependencies(
        draft,
        deleted_word_ids=deleted_word_ids,
        deleted_cue_ids=deleted_cue_ids,
        affected_cue_ids=affected_cue_ids,
        deleted_start_ms=source_start,
        deleted_end_ms=source_end,
    )

    words_by_id = {word.word_id: word for word in remaining_words}
    next_cues: list[VideoLocalizationCue] = []
    updated_cue_ids: list[str] = []
    for cue in draft.cues:
        retained_ids = _remaining_cue_word_ids(cue, deleted_word_ids)
        if cue.cue_id in deleted_cue_ids:
            continue
        if retained_ids == list(cue.source_word_ids):
            next_cues.append(
                _advance_unchanged_cue_revision(
                    cue,
                    old_revision_id=transcript.revision_id,
                    new_revision_id=new_revision_id,
                )
            )
            continue
        retained_words = [words_by_id[word_id] for word_id in retained_ids]
        next_cues.append(
            _rebuild_mixed_cue(cue, retained_words, new_revision_id=new_revision_id)
        )
        updated_cue_ids.append(cue.cue_id)

    linkage_changes = [
        SourceCueChange(cue.cue_id, tuple(cue.source_word_ids), ()) for cue in deleted_cues
    ]
    remapped = remap_localized_source_links(draft.localized_subtitles, linkage_changes)
    if remapped.changed_subtitle_ids or remapped.orphaned_subtitle_ids:
        # The preflight above should make this unreachable.  Do not mutate
        # Chinese source links as a fallback if a new linkage shape appears.
        raise ValueError("asr source repair cannot safely migrate localized subtitle links")

    next_segments = [
        segment for segment in transcript.segments if segment.segment_id not in excluded_ids
    ]
    next_transcript = transcript.model_copy(
        update={
            "revision_id": new_revision_id,
            "raw_text": _joined_segment_text(next_segments, corrected=False),
            "corrected_text": _joined_segment_text(next_segments, corrected=True),
            "segments": next_segments,
            "words": remaining_words,
            "review_status": "partial",
            "review_error": "asr_source_repair_requires_review",
            "transcript_quality_cycle": {
                "status": "stale",
                "reason": "asr_source_repair_requires_review",
                "previous_review_status": transcript.review_status,
                "previous_diagnostics": transcript.transcript_quality_cycle,
            },
            "audio_boundary_status": "not_run",
            "audio_boundary_error": "asr_source_repair_requires_review",
            "audio_boundary_features": [
                item
                for item in transcript.audio_boundary_features
                if not {item.left_word_id, item.right_word_id}.intersection(deleted_word_ids)
            ],
            "asr_vad_source_timing_corrections": [
                item
                for item in transcript.asr_vad_source_timing_corrections
                if not set(item.word_ids).intersection(deleted_word_ids)
            ],
            "boundary_review_status": "partial",
            "boundary_review_error": "asr_source_repair_requires_review",
            "boundary_reviews": [
                item
                for item in transcript.boundary_reviews
                if not {item.left_word_id, item.right_word_id}.intersection(deleted_word_ids)
            ],
            "subtitle_entry_by_word_id": {
                word_id: entry
                for word_id, entry in transcript.subtitle_entry_by_word_id.items()
                if word_id not in deleted_word_ids
            },
            "quality_flags": _append_once(
                transcript.quality_flags, _REPAIR_REQUIRED_FLAG
            ),
        }
    )
    next_draft = draft.model_copy(
        update={
            "transcription": next_transcript,
            "cues": next_cues,
            "localized_subtitles": list(remapped.subtitles),
            "localization_state": _invalidated_localization_state(draft.localization_state),
            "quality_gate": draft.quality_gate.model_copy(
                update={"status": "unknown", "checked_at": None}
            ),
        }
    )
    next_draft = localization_tracks.invalidate_formal_quality_binding(next_draft)

    validated_draft = VideoLocalizationDraft.model_validate(next_draft.model_dump())
    validated_draft._repository_revision = draft._repository_revision
    receipt = AsrSourceRepairReceipt(
        request_id=request.request_id,
        request_fingerprint=request.fingerprint(),
        before_revision_id=transcript.revision_id,
        after_revision_id=new_revision_id,
        audio_sha256=request.audio_sha256,
        evidence=request.evidence,
        deleted_segments=[_deleted_segment(segment) for segment in deleted_segments],
        deleted_words=[_deleted_word(word) for word in deleted_words],
        deleted_cues=[_deleted_cue(cue) for cue in deleted_cues],
        updated_cue_ids=updated_cue_ids,
    )
    return validated_draft, receipt


def _reject_unsafe_dependencies(
    draft: VideoLocalizationDraft,
    *,
    deleted_word_ids: set[str],
    deleted_cue_ids: set[str],
    affected_cue_ids: set[str],
    deleted_start_ms: int,
    deleted_end_ms: int,
) -> None:
    for subtitle in draft.localized_subtitles:
        if set(subtitle.source_word_ids).intersection(deleted_word_ids):
            raise ValueError("asr source repair would delete Chinese subtitle source words")
        if set(localized_source_cue_ids(subtitle)).intersection(deleted_cue_ids):
            raise ValueError("asr source repair would delete a Chinese subtitle source cue")
    for spoken in draft.localized_spoken_segments:
        if set(spoken.source_word_ids).intersection(deleted_word_ids):
            raise ValueError("asr source repair would delete Chinese spoken source words")
        if set(spoken.source_cue_ids).intersection(deleted_cue_ids):
            raise ValueError("asr source repair would delete a Chinese spoken source cue")
    for cue in draft.cues:
        if cue.cue_id in deleted_cue_ids and (
            cue.zh_localized_subtitle_text or cue.tts_recommended_text
        ):
            raise ValueError("asr source repair would delete Chinese cue text")
    for cue in draft.cues:
        if cue.cue_id not in affected_cue_ids:
            continue
        if cue.reference_clip_id:
            raise ValueError("asr source repair would change a cue with a reference clip")
        if any(
            getattr(cue, field)
            for field in (
                "tts_result_id",
                "tts_generation_id",
                "tts_audio_path",
                "tts_batch_task_id",
            )
        ):
            raise ValueError("asr source repair would change a cue with generated audio")
    for reference in draft.reference_clips:
        if (
            reference.source_stem in {"vocals_clean", "original_audio"}
            and _ranges_overlap(
                reference.start_ms,
                reference.end_ms,
                deleted_start_ms,
                deleted_end_ms,
            )
        ):
            raise ValueError("asr source repair would overlap a source-audio reference clip")
    if any(set(task.source_cue_ids).intersection(affected_cue_ids) for task in draft.tts_tasks):
        raise ValueError("asr source repair would change a cue used by a generated task")
    for collection_name, items in (
        ("generated candidate", draft.generated_candidates),
        ("timeline clip", draft.timeline_clips),
    ):
        if any(
            _item_references(item, deleted_word_ids, affected_cue_ids)
            for item in items
        ):
            raise ValueError(f"asr source repair would change a {collection_name} source")


def _item_references(item: Any, word_ids: set[str], cue_ids: set[str]) -> bool:
    """Read known ID-bearing fields recursively without treating text as IDs."""

    if isinstance(item, Mapping):
        for key, value in item.items():
            if key in _GENERATED_CUE_FIELDS and _contains_id(value, cue_ids):
                return True
            if key in _GENERATED_WORD_FIELDS and _contains_id(value, word_ids):
                return True
            if _item_references(value, word_ids, cue_ids):
                return True
    elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
        return any(_item_references(value, word_ids, cue_ids) for value in item)
    return False


def _contains_id(value: Any, expected_ids: set[str]) -> bool:
    if isinstance(value, str):
        return value in expected_ids
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(isinstance(item, str) and item in expected_ids for item in value)
    return False


def _ranges_overlap(
    start_ms: int | None,
    end_ms: int | None,
    other_start_ms: int,
    other_end_ms: int,
) -> bool:
    return (
        start_ms is not None
        and end_ms is not None
        and start_ms < other_end_ms
        and other_start_ms < end_ms
    )


def _remaining_cue_word_ids(cue: VideoLocalizationCue, deleted_word_ids: set[str]) -> list[str]:
    return [word_id for word_id in cue.source_word_ids if word_id not in deleted_word_ids]


def _rebuild_mixed_cue(
    cue: VideoLocalizationCue,
    words: list[VideoLocalizationAlignedWord],
    *,
    new_revision_id: str,
) -> VideoLocalizationCue:
    if not words:
        raise ValueError("asr source repair cannot rebuild an empty cue")
    start_ms = words[0].start_ms
    end_ms = words[-1].end_ms
    source_text = " ".join(word.text for word in words).strip()
    return cue.model_copy(
        update={
            "start_ms": start_ms,
            "end_ms": end_ms,
            "source_duration_ms": end_ms - start_ms,
            "en_subtitle_text": source_text,
            "source_text_raw": source_text,
            "source_word_ids": [word.word_id for word in words],
            "transcription_revision_id": new_revision_id,
            "manual_timing_review_status": "required",
            "manual_timing_confirmed_revision": None,
            "manual_timing_confirmed_at": None,
            "manual_timing_confirmed_start_ms": None,
            "manual_timing_confirmed_end_ms": None,
            "manual_timing_confirmation_method": None,
            "manual_timing_confirmation_evidence": None,
            "review_status": "needs_review",
            "quality_flags": _append_once(
                _recomputed_segment_timing_flag(cue.quality_flags, words),
                _REPAIR_REQUIRED_FLAG,
            ),
        }
    )


def _advance_unchanged_cue_revision(
    cue: VideoLocalizationCue,
    *,
    old_revision_id: str,
    new_revision_id: str,
) -> VideoLocalizationCue:
    """Advance a cue's source version without pretending an old review is new."""

    if cue.transcription_revision_id != old_revision_id:
        return cue
    update: dict[str, Any] = {"transcription_revision_id": new_revision_id}
    if cue.manual_timing_review_status == "confirmed":
        update.update(
            {
                "manual_timing_review_status": "required",
                "manual_timing_confirmed_revision": None,
                "manual_timing_confirmed_at": None,
                "manual_timing_confirmed_start_ms": None,
                "manual_timing_confirmed_end_ms": None,
                "manual_timing_confirmation_method": None,
                "manual_timing_confirmation_evidence": None,
            }
        )
    return cue.model_copy(update=update)


def _joined_segment_text(
    segments: list[VideoLocalizationTranscriptSegment], *, corrected: bool
) -> str:
    return " ".join(
        (segment.corrected_text if corrected else segment.raw_text) or segment.raw_text
        for segment in segments
        if ((segment.corrected_text if corrected else segment.raw_text) or segment.raw_text).strip()
    )


def _invalidated_localization_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key not in _STALE_LOCALIZATION_STATE_FIELDS
    } | {"status": "edited"}


def _append_once(values: list[str], value: str) -> list[str]:
    return [*values, value] if value not in values else list(values)


def _recomputed_segment_timing_flag(
    flags: list[str], words: list[VideoLocalizationAlignedWord]
) -> list[str]:
    """Keep this source-derived flag only when a surviving word still has it."""

    without_stale_flag = [
        flag for flag in flags if flag != _SEGMENT_TIMING_INTERPOLATED_FLAG
    ]
    if any(word.timing_source == "asr_segment_interpolation" for word in words):
        return _append_once(without_stale_flag, _SEGMENT_TIMING_INTERPOLATED_FLAG)
    return without_stale_flag


def _deleted_segment(segment: VideoLocalizationTranscriptSegment) -> AsrSourceRepairDeletedSegment:
    return AsrSourceRepairDeletedSegment(
        segment_id=segment.segment_id,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        raw_text=segment.raw_text,
        corrected_text=segment.corrected_text,
    )


def _deleted_word(word: VideoLocalizationAlignedWord) -> AsrSourceRepairDeletedWord:
    return AsrSourceRepairDeletedWord(
        word_id=word.word_id,
        segment_id=word.segment_id,
        text=word.text,
        start_ms=word.start_ms,
        end_ms=word.end_ms,
    )


def _deleted_cue(cue: VideoLocalizationCue) -> AsrSourceRepairDeletedCue:
    return AsrSourceRepairDeletedCue(
        cue_id=cue.cue_id,
        start_ms=cue.start_ms,
        end_ms=cue.end_ms,
        en_subtitle_text=cue.en_subtitle_text,
        source_word_ids=list(cue.source_word_ids),
    )


__all__ = ["repair_asr_source"]
