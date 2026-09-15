"""Pure, typed validation for advisory dubbing timeline-edit evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.domains.video_localization import dubbing_candidate_alignment


def first_primary_clip_overlap(candidate_clips, other_clips, *, staged: bool = False):
    """Check the selected projection against playable primary neighbours.

    Staged typed clips deliberately omit file paths; only their side can skip
    the media-presence predicate. This check never moves either projection.
    """
    def primary(clip, require_audio):
        return (clip.get("track_id") == "dub"
                and not clip.get("manual_history_copy")
                and int(clip.get("dub_lane") or 0) == 0
                and str(clip.get("status") or "ready") == "ready"
                and (not require_audio or bool(clip.get("audio_path"))))

    selected = [clip for clip in candidate_clips if primary(clip, not staged)]
    others = [clip for clip in other_clips if primary(clip, True)]
    for index, left in enumerate(selected):
        start, end = int(left.get("start_ms") or 0), int(left.get("end_ms") or 0)
        if end <= start:
            return str(left.get("clip_id") or ""), str(left.get("clip_id") or "")
        for right in [*others, *selected[index + 1:]]:
            right_start, right_end = int(right.get("start_ms") or 0), int(right.get("end_ms") or 0)
            if right_end > right_start and start < right_end and right_start < end:
                return str(left.get("clip_id") or ""), str(right.get("clip_id") or "")
    return None


def candidate_protected_speech_bounds(audio):
    """Keep placement anchors separate from the union required for safe cuts."""
    onset, end = candidate_source_speech_bounds(audio)
    vad_start = audio.get("speech_start_ms") if isinstance(audio, dict) else getattr(audio, "speech_start_ms", None)
    if vad_start is not None:
        onset = min(onset, vad_start) if onset is not None else vad_start
    return onset, end


def first_protected_audio_overlap(candidate_clips, other_clips, audio):
    """Ignore removable edge padding, but never ignore measured speech."""
    onset, end = candidate_protected_speech_bounds(audio)
    if onset is None or end is None:
        return None
    protected = []
    for clip in candidate_clips:
        source_start = int(clip.get("source_start_ms") or 0)
        source_end = int(clip.get("source_end_ms") or 0)
        start, stop = max(source_start, onset), min(source_end, end)
        if stop > start:
            offset = int(clip.get("start_ms") or 0) - source_start
            protected.append({**clip, "start_ms": offset + start, "end_ms": offset + stop})
    return first_primary_clip_overlap(protected, other_clips, staged=True)


def retained_candidate_projection_fingerprint(clips, candidate_id: str) -> str:
    """Content identity is stable when the same playback changes dub lanes."""
    from app.schemas.video_localization_dubbing_production import DubbingStagedCandidateClip
    fields = DubbingStagedCandidateClip.model_fields
    normalized = [DubbingStagedCandidateClip.model_validate({
        **{key: value for key, value in clip.items() if key in fields},
        "dub_lane": 0,
    }).model_dump(exclude_none=True) for clip in clips]
    # Alignment labels and slice numbering describe the edit; they do not
    # change the audio sent to ASR. Recomputing those labels must not invalidate
    # evidence for identical retained bytes and playback positions.
    for clip in normalized:
        for key in ("dubbing_alignment_word_ids", "dubbing_slice_index",
                    "dubbing_slice_count", "dubbing_timeline_gap_before_ms"):
            clip.pop(key, None)
    return candidate_clip_projection_fingerprint(normalized)


def has_verified_retained_content(frozen, clips) -> bool:
    def value(item, key, default=None):
        return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)
    proof = value(frozen, "retained_content_evidence")
    if not proof or not clips or value(proof, "audio_sha256") != value(frozen, "audio_sha256"):
        return False
    observation = value(proof, "observation")
    def normalized(text):
        return "".join(char for char in str(text).casefold() if char.isalnum())
    return bool(
        value(observation, "status") == "complete"
        and normalized(value(observation, "transcript", ""))
        == normalized(value(frozen, "expected_spoken_text", ""))
        and value(proof, "candidate_clip_projection_fingerprint")
        == retained_candidate_projection_fingerprint(clips, value(frozen, "candidate_id"))
    )


def candidate_clip_projection_fingerprint(
    clips: list[dict[str, Any]],
) -> str:
    """Bind evidence to one candidate's editable clip projection, not global state."""

    fields = (
        "clip_id",
        "candidate_id",
        "result_id",
        "dubbing_group_id",
        "target_subtitle_ids",
        "subtitle_id",
        "start_ms",
        "end_ms",
        "source_start_ms",
        "source_end_ms",
        "dub_lane",
        "media_source_clip_id",
        "dubbing_slice_index",
        "dubbing_slice_count",
        "dubbing_alignment_word_ids",
        "dubbing_timeline_gap_before_ms",
        "audio_sha256",
    )
    projection = [
        {key: clip.get(key) for key in fields if key in clip}
        for clip in sorted(clips, key=lambda item: str(item.get("clip_id") or ""))
    ]
    serialized = json.dumps(
        projection,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def candidate_audible_timeline_bounds(
    clips: list[dict[str, Any]],
    *,
    speech_start_ms: int,
    speech_end_ms: int,
) -> tuple[int, int] | None:
    """Project the candidate's real audible speech edges onto the timeline."""

    audible_ranges: list[tuple[int, int]] = []
    for clip in clips:
        timeline_start_ms = int(clip.get("start_ms") or 0)
        timeline_end_ms = int(clip.get("end_ms") or timeline_start_ms)
        source_start_ms = int(clip.get("source_start_ms") or 0)
        source_end_ms = int(
            clip.get("source_end_ms")
            or source_start_ms + timeline_end_ms - timeline_start_ms
        )
        overlap_start_ms = max(source_start_ms, speech_start_ms)
        overlap_end_ms = min(source_end_ms, speech_end_ms)
        if overlap_end_ms <= overlap_start_ms:
            continue
        audible_ranges.append(
            (
                timeline_start_ms + overlap_start_ms - source_start_ms,
                timeline_start_ms + overlap_end_ms - source_start_ms,
            )
        )
    if not audible_ranges:
        return None
    return (
        min(start_ms for start_ms, _end_ms in audible_ranges),
        max(end_ms for _start_ms, end_ms in audible_ranges),
    )


def candidate_source_speech_bounds(audio: object) -> tuple[int | None, int | None]:
    """Return the source bounds used for placement without weakening trim proof.

    A positive-duration aligned word is the finest source onset available, so
    it anchors a newly generated candidate when it disagrees with VAD.  VAD is
    still part of the protected tail envelope, together with every aligned
    word end.  Continuation must use this same pair when proving that an
    accepted crop has not moved.
    """

    def value(item: object, name: str, default: object = None) -> object:
        return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)

    words = sorted(
        (
            word
            for word in (value(audio, "aligned_words", []) or [])
            if int(value(word, "end_ms", 0) or 0) > int(value(word, "start_ms", 0) or 0)
        ),
        key=lambda word: (
            int(value(word, "start_ms", 0) or 0),
            int(value(word, "end_ms", 0) or 0),
            str(value(word, "word_id", "") or ""),
        ),
    )
    onset_ms = (
        int(value(words[0], "start_ms", 0) or 0)
        if words else value(audio, "speech_start_ms")
    )
    protected_end = [
        int(candidate)
        for candidate in [value(audio, "speech_end_ms")]
        if candidate is not None
    ]
    protected_end.extend(int(value(word, "end_ms", 0) or 0) for word in words)
    return (
        int(onset_ms) if onset_ms is not None else None,
        max(protected_end, default=None),
    )


def timeline_edit_gate_matches(
    gate: dict[str, Any],
    *,
    candidate_clips: list[dict[str, Any]],
    source_revision: str,
    plan_revision: int,
    candidate_id: str,
    cqc_report_fingerprint: str,
    expected_gap_evidence: list[object],
    expected_aligned_words: list[object],
    speaking_rate_ratio: float | None,
    content_speed_exception_applied: bool,
) -> bool:
    """Validate one durable edit record against current owned facts."""

    def field(value: object, key: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    if (
        gate.get("schema_version") != "dubbing-timeline-edit-gate-v1"
        or gate.get("status") not in {"passed", "failed", "needs_review"}
        or str(gate.get("source_revision") or "") != source_revision
        or int(gate.get("plan_revision") or 0) != plan_revision
        or str(gate.get("candidate_id") or "") != candidate_id
        or str(gate.get("cqc_report_fingerprint") or "")
        != cqc_report_fingerprint
        or str(gate.get("candidate_clip_projection_fingerprint") or "")
        != candidate_clip_projection_fingerprint(candidate_clips)
        or gate.get("actual_speech_start_delta_ms") is None
        or gate.get("actual_speech_end_delta_ms") is None
        or speaking_rate_ratio is None
        or abs(float(gate.get("speaking_rate_ratio") or 0) - speaking_rate_ratio)
        > 1e-9
        or bool(gate.get("content_speed_exception_applied"))
        != content_speed_exception_applied
    ):
        return False
    def normalized_gap(value: object) -> dict[str, Any]:
        result = (
            value.model_dump(mode="json")
            if hasattr(value, "model_dump")
            else dict(value)
        )
        for field in ("semantic_role", "semantic_pause_scale"):
            if result.get(field) is None:
                result.pop(field, None)
        return result

    expected_gaps = {
        str(getattr(value, "gap_id", None) or value.get("gap_id", "")): normalized_gap(value)
        for value in expected_gap_evidence
        if str(getattr(value, "gap_id", None) or value.get("gap_id", ""))
    }
    actual_gaps = {
        str(value.get("gap_id") or ""): normalized_gap(value)
        for value in gate.get("gap_decisions") or []
        if isinstance(value, dict) and str(value.get("gap_id") or "")
    }
    expected_word_ids = [
        str(getattr(value, "word_id", None) or value.get("word_id", ""))
        for value in expected_aligned_words
        if str(getattr(value, "word_id", None) or value.get("word_id", ""))
    ]
    try:
        dubbing_candidate_alignment.validate_candidate_clip_coverage(
            clips=candidate_clips,
            words=list(expected_aligned_words),
            speech_start_ms=(
                min(
                    int(field(value, "start_ms", 0))
                    for value in expected_aligned_words
                )
                if expected_aligned_words
                else None
            ),
            speech_end_ms=(
                max(
                    int(field(value, "end_ms", 0))
                    for value in expected_aligned_words
                )
                if expected_aligned_words
                else None
            ),
        )
    except (TypeError, ValueError):
        return False
    return (
        bool(actual_gaps)
        and actual_gaps == expected_gaps
        and bool(expected_word_ids)
        and gate.get("alignment_word_ids") == expected_word_ids
        and all(
            int(value.get("retained_duration_ms") or 0)
            == rendered_gap_duration_ms(value, candidate_clips)
            for value in actual_gaps.values()
        )
    )


def rendered_gap_duration_ms(
    gap: dict[str, Any],
    candidate_clips: list[dict[str, Any]],
) -> int:
    """Measure one original candidate gap after source crops and placement."""

    gap_start_ms = int(gap.get("start_ms") or 0)
    gap_end_ms = int(gap.get("end_ms") or 0)
    ordered = sorted(
        candidate_clips,
        key=lambda clip: (
            int(clip.get("source_start_ms") or 0),
            int(clip.get("start_ms") or 0),
        ),
    )
    retained_ms = 0
    for clip in ordered:
        source_start_ms = int(clip.get("source_start_ms") or 0)
        source_end_ms = int(
            clip.get("source_end_ms")
            or source_start_ms
            + int(clip.get("end_ms") or 0)
            - int(clip.get("start_ms") or 0)
        )
        retained_ms += max(
            0,
            min(source_end_ms, gap_end_ms)
            - max(source_start_ms, gap_start_ms),
        )
    for left, right in zip(ordered, ordered[1:]):
        left_source_end_ms = int(left.get("source_end_ms") or 0)
        right_source_start_ms = int(right.get("source_start_ms") or 0)
        if not (
            gap_start_ms <= left_source_end_ms <= gap_end_ms
            and gap_start_ms <= right_source_start_ms <= gap_end_ms
        ):
            continue
        retained_ms += max(
            0,
            int(right.get("start_ms") or 0)
            - int(left.get("end_ms") or 0),
        )
    return retained_ms


def reconcile_gap_evidence_with_projection(
    gaps: list[object],
    candidate_clips: list[dict[str, Any]],
) -> list[object]:
    """Make edit dispositions describe the final, safely cropped projection.

    Candidate placement deliberately keeps bounded lead-in padding and may
    preserve a quiet tail to avoid clipping a phoneme.  Semantic review owns
    whether a gap may be edited; this projection pass owns the exact amount
    that the persisted clips actually retain.
    """

    projection_fingerprint = candidate_clip_projection_fingerprint(
        candidate_clips
    )
    reconciled: list[object] = []
    for value in gaps:
        serialized = (
            value.model_dump(mode="json")
            if hasattr(value, "model_dump")
            else dict(value)
        )
        duration_ms = int(serialized.get("duration_ms") or 0)
        retained_ms = rendered_gap_duration_ms(
            serialized,
            candidate_clips,
        )
        if (
            serialized.get("edit_decision") in {"remove", "shorten"}
            and retained_ms >= duration_ms
        ):
            raise ValueError(
                "时间线没有执行已确认的气口删除或缩短，不能改写为保留后通过门禁。"
            )
        if retained_ms <= 0:
            decision = "remove"
            retained_ms = 0
        elif retained_ms < duration_ms:
            decision = "shorten"
        elif retained_ms == duration_ms:
            decision = "retain"
        else:
            decision = "extend"
        evidence_id = (
            "dubbing-timeline-projection-v1:"
            f"{projection_fingerprint}:{serialized.get('gap_id')}:{retained_ms}"
        )
        review_evidence_ids = list(
            dict.fromkeys(
                [
                    *list(serialized.get("review_evidence_ids") or []),
                    evidence_id,
                ]
            )
        )
        reason = str(serialized.get("decision_reason") or "").rstrip("。")
        update = {
            "edit_decision": decision,
            "retained_duration_ms": retained_ms,
            "decision_reason": (
                f"{reason}；最终时间线实际保留 {retained_ms} 毫秒。"
            ),
            "review_evidence_ids": review_evidence_ids,
        }
        reconciled.append(
            value.model_copy(update=update)
            if hasattr(value, "model_copy")
            else {**serialized, **update}
        )
    return reconciled


__all__ = [
    "candidate_audible_timeline_bounds",
    "candidate_source_speech_bounds",
    "candidate_protected_speech_bounds",
    "first_protected_audio_overlap",
    "candidate_clip_projection_fingerprint",
    "reconcile_gap_evidence_with_projection",
    "rendered_gap_duration_ms",
    "timeline_edit_gate_matches",
]
