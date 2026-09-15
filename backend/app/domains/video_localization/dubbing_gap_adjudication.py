"""Deterministic gas editing for generated speech.

The acoustic analyzer and forced aligner own all evidence. This module never
calls a model, reviews dialogue, or changes text; it only turns safe measured
gaps into local edit decisions.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.schemas.video_localization_dubbing_production import (
    DubbingAudioGapEvidence,
    DubbingCandidateAlignedWord,
    DubbingSemanticBoundaryAudit,
    DubbingSemanticBoundaryEvidence,
)


GAP_PROCESSING_EVIDENCE_ID = "dubbing-gap-processing-v1"
OUTER_SAFETY_PADDING_MS = 80
_SEMANTIC_PUNCTUATION_RE = re.compile(r"[，,、；;。！？!?：:]$")
_STRONG_BREAK_RE = re.compile(r"[；;。！？!?：:]$")
_STRONG_PUNCTUATION = frozenset("；;。！？!?：:")
_WEAK_PUNCTUATION = frozenset("，,、")


@dataclass(frozen=True)
class SourcePauseRhythmEvidence:
    duration_ms: int
    right_word_start_ms: int


def _lexical_text(text: str) -> str:
    return "".join(
        character.casefold()
        for character in unicodedata.normalize("NFKC", text)
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def semantic_boundary_word_pairs(
    expected_text: str,
    words: list[DubbingCandidateAlignedWord],
) -> list[tuple[tuple[str, str], str]]:
    """Map punctuation in the requested text onto punctuation-free alignment."""

    normalized_text = unicodedata.normalize("NFKC", expected_text)
    lexical: list[str] = []
    boundary_by_offset: dict[int, str] = {}
    for character in normalized_text:
        if character in _STRONG_PUNCTUATION:
            if lexical:
                boundary_by_offset[len(lexical)] = "deliberate"
            continue
        if character in _WEAK_PUNCTUATION:
            if lexical and boundary_by_offset.get(len(lexical)) != "deliberate":
                boundary_by_offset[len(lexical)] = "normal"
            continue
        if character.isspace() or unicodedata.category(character).startswith("P"):
            continue
        lexical.append(character.casefold())

    word_texts = [_lexical_text(word.text) for word in words]
    if "".join(word_texts) != "".join(lexical):
        return []
    pairs: list[tuple[tuple[str, str], str]] = []
    offset = 0
    for left, right, word_text in zip(words, words[1:], word_texts):
        offset += len(word_text)
        scale = boundary_by_offset.get(offset)
        if scale is not None:
            pairs.append(((left.word_id, right.word_id), scale))
    return pairs


def adjacent_words(
    gap: DubbingAudioGapEvidence,
    words: list[DubbingCandidateAlignedWord],
) -> tuple[DubbingCandidateAlignedWord | None, DubbingCandidateAlignedWord | None]:
    left = [word for word in words if word.end_ms <= gap.start_ms + 80]
    right = [word for word in words if word.start_ms >= gap.end_ms - 80]
    return (left[-1] if left else None, right[0] if right else None)


def map_source_pause_rhythm(
    expected_text: str,
    words: list[DubbingCandidateAlignedWord],
    source_pauses: list[SourcePauseRhythmEvidence] | None,
) -> dict[tuple[str, str], SourcePauseRhythmEvidence]:
    """Map ordered source pauses onto the candidate's semantic boundaries."""

    semantic_pairs = semantic_boundary_word_pairs(expected_text, words)
    evidence = [item for item in source_pauses or [] if item.duration_ms > 0]
    mapped: dict[tuple[str, str], SourcePauseRhythmEvidence] = {}
    for index, (pair, _scale) in enumerate(semantic_pairs):
        if not evidence:
            break
        source_index = min(
            len(evidence) - 1,
            round(
                index
                * (len(evidence) - 1)
                / max(1, len(semantic_pairs) - 1)
            ),
        )
        mapped[pair] = evidence[source_index]
    return mapped


def process_gap_evidence(
    gaps: list[DubbingAudioGapEvidence],
    *,
    aligned_words: list[DubbingCandidateAlignedWord],
    speech_start_ms: int | None = None,
    speech_end_ms: int | None = None,
    audio_duration_ms: int | None = None,
    expected_spoken_text: str = "",
    source_pauses: list[SourcePauseRhythmEvidence] | None = None,
) -> list[DubbingAudioGapEvidence]:
    """Trim only gaps proven safe by local acoustic and word evidence."""

    semantic_pairs = semantic_boundary_word_pairs(
        expected_spoken_text,
        aligned_words,
    )
    mapping_uncertain = bool(
        expected_spoken_text
        and _lexical_text(expected_spoken_text)
        != "".join(_lexical_text(word.text) for word in aligned_words)
    )
    semantic_scale_by_pair = dict(semantic_pairs)
    source_pause_by_pair = map_source_pause_rhythm(
        expected_spoken_text,
        aligned_words,
        source_pauses,
    )

    preferred_gap_by_word_pair: dict[tuple[str, str], str] = {}
    for gap in gaps:
        if gap.kind != "internal" or gap.safe_edit_boundary is not True:
            continue
        left, right = adjacent_words(gap, aligned_words)
        if (
            left is None
            or right is None
            or (left.word_id, right.word_id) in semantic_scale_by_pair
            or _SEMANTIC_PUNCTUATION_RE.search(left.text)
        ):
            continue
        pair = (left.word_id, right.word_id)
        previous_id = preferred_gap_by_word_pair.get(pair)
        previous = next(
            (item for item in gaps if item.gap_id == previous_id),
            None,
        )
        if previous is None or gap.duration_ms > previous.duration_ms:
            preferred_gap_by_word_pair[pair] = gap.gap_id

    processed: list[DubbingAudioGapEvidence] = []
    for gap in gaps:
        evidence_ids = list(
            dict.fromkeys([*gap.review_evidence_ids, GAP_PROCESSING_EVIDENCE_ID])
        )
        if gap.kind in {"leading", "trailing"}:
            removable = gap.safe_edit_boundary is True
            retained_duration_ms = (
                min(gap.duration_ms, OUTER_SAFETY_PADDING_MS)
                if removable
                else gap.duration_ms
            )
            if removable and gap.kind == "leading":
                protected_start_ms = speech_start_ms
                if aligned_words:
                    protected_start_ms = min(
                        (
                            protected_start_ms
                            if protected_start_ms is not None
                            else aligned_words[0].start_ms
                        ),
                        min(word.start_ms for word in aligned_words),
                    )
                if protected_start_ms is not None:
                    crop_start_ms = max(
                        0,
                        protected_start_ms - OUTER_SAFETY_PADDING_MS,
                    )
                    retained_duration_ms = max(
                        0,
                        gap.end_ms - max(gap.start_ms, crop_start_ms),
                    )
            elif removable and gap.kind == "trailing":
                protected_end_ms = speech_end_ms
                if aligned_words:
                    protected_end_ms = max(
                        (
                            protected_end_ms
                            if protected_end_ms is not None
                            else aligned_words[-1].end_ms
                        ),
                        max(word.end_ms for word in aligned_words),
                    )
                if (
                    protected_end_ms is not None
                    and audio_duration_ms is not None
                ):
                    crop_end_ms = min(
                        audio_duration_ms,
                        protected_end_ms + OUTER_SAFETY_PADDING_MS,
                    )
                    retained_duration_ms = max(
                        0,
                        min(gap.end_ms, crop_end_ms) - gap.start_ms,
                    )
            if removable and retained_duration_ms == 0:
                decision = "remove"
                reason = "逐词对齐已证明候选外边界安全，删除首尾空白。"
            elif removable and retained_duration_ms < gap.duration_ms:
                decision = "shorten"
                reason = "逐词对齐已证明候选外边界安全，缩短首尾空白并保留 80 毫秒安全余量。"
            elif removable:
                decision = "retain"
                reason = "VAD 与逐词锚的保护范围覆盖当前首尾空白，原样保留。"
            else:
                decision = "retain"
                retained_duration_ms = gap.duration_ms
                reason = "外边界没有安全切点，保留原始音频。"
            processed.append(
                gap.model_copy(
                    update={
                        "edit_decision": decision,
                        "retained_duration_ms": retained_duration_ms,
                        "decision_reason": reason,
                        "semantic_role": (
                            "continuous_phrase" if removable else None
                        ),
                        "semantic_pause_scale": None,
                        "review_evidence_ids": evidence_ids,
                    }
                )
            )
            continue

        left, right = adjacent_words(gap, aligned_words)
        pair = (
            (left.word_id, right.word_id)
            if left is not None and right is not None
            else None
        )
        semantic_boundary = bool(
            left is not None
            and (
                _SEMANTIC_PUNCTUATION_RE.search(left.text)
                or pair in semantic_scale_by_pair
            )
        )
        source_pause = source_pause_by_pair.get(pair) if pair is not None else None
        source_pause_ms = source_pause.duration_ms if source_pause is not None else None
        semantic_scale = (
            semantic_scale_by_pair.get(pair)
            if pair is not None
            else None
        )
        if source_pause_ms is not None and source_pause_ms >= 600:
            semantic_scale = "deliberate"
        duplicate_word_pair_gap = bool(
            left is not None
            and right is not None
            and preferred_gap_by_word_pair.get(
                (left.word_id, right.word_id)
            )
            not in {None, gap.gap_id}
        )
        removable = bool(
            gap.safe_edit_boundary is True
            and left is not None
            and right is not None
            and not semantic_boundary
            and not duplicate_word_pair_gap
            and not mapping_uncertain
        )
        if source_pause_ms is not None:
            evidence_ids = list(
                dict.fromkeys([*evidence_ids, f"source_pause:{source_pause_ms}ms"])
            )
        processed.append(
            gap.model_copy(
                update={
                    "edit_decision": "remove" if removable else "retain",
                    "retained_duration_ms": 0 if removable else gap.duration_ms,
                    "decision_reason": (
                        "台词与对齐文字无法完整对应，保留停顿并等待核对。"
                        if mapping_uncertain else
                        f"{left.text} / {right.text} 之间有逐词安全切点且不是标点语义边界，删除内部空白。"
                        if removable
                        else (
                            "标点表明这里是语义边界，保留停顿。"
                            if semantic_boundary
                            else (
                                "同一词对已有更强的安全气口，保留其余区间，"
                                "避免制造没有逐词证据的孤立切片。"
                                if duplicate_word_pair_gap
                                else "内部气口没有可靠安全切点，保留原始停顿。"
                            )
                        )
                    ),
                    "semantic_role": (
                        "uncertain" if mapping_uncertain else
                        "continuous_phrase"
                        if removable
                        else (
                            "semantic_boundary"
                            if semantic_boundary
                            else "uncertain" if duplicate_word_pair_gap else None
                        )
                    ),
                    "semantic_pause_scale": (
                        semantic_scale or "normal" if semantic_boundary else None
                    ),
                    "review_evidence_ids": evidence_ids,
                }
            )
        )
    return processed


def needs_single_regeneration(
    gaps: list[DubbingAudioGapEvidence],
    *,
    aligned_words: list[DubbingCandidateAlignedWord],
    expected_spoken_text: str = "",
) -> bool:
    """Detect a strong sentence break that has no safe acoustic cut.

    This is deliberately narrow: it does not judge wording or naturalness.
    It only catches a punctuation-backed break whose aligned words overlap, or
    whose measured internal gap is explicitly not safe to cut.
    """

    ordered = sorted(aligned_words, key=lambda word: (word.start_ms, word.end_ms))
    strong_pairs = {
        pair for pair, scale in semantic_boundary_word_pairs(expected_spoken_text, ordered)
        if scale == "deliberate"
    }

    def strong_break(left, right):
        return bool(_STRONG_BREAK_RE.search(left.text)) or (
            left.word_id, right.word_id
        ) in strong_pairs

    for left, right in zip(ordered, ordered[1:]):
        if strong_break(left, right) and right.start_ms <= left.end_ms:
            return True
    for gap in gaps:
        if gap.kind != "internal" or gap.safe_edit_boundary is True:
            continue
        left, right = adjacent_words(gap, ordered)
        if left is not None and right is not None and strong_break(left, right):
            return True
    return False


def build_semantic_boundary_audit(
    *,
    source_revision: str,
    plan_revision: int,
    candidate_id: str,
    audio_sha256: str,
    candidate_evidence_fingerprint: str,
    candidate_clip_projection_fingerprint: str,
    expected_spoken_text: str,
    aligned_words: list[DubbingCandidateAlignedWord],
    gaps: list[DubbingAudioGapEvidence],
    clips: list[dict],
) -> DubbingSemanticBoundaryAudit:
    """Project every adjacent aligned token onto the final candidate clips.

    The report deliberately preserves zero/overlap edges and low-energy spans
    that intrude into a word.  It is evidence for an Agent decision, never an
    acoustic shortcut for deciding whether speech sounds natural.
    """
    slices = sorted(
        [
            (
                int(clip.get("source_start_ms") or 0),
                int(clip.get("source_end_ms") or 0),
                int(clip.get("start_ms") or 0),
            )
            for clip in clips
            if int(clip.get("source_end_ms") or 0) > int(clip.get("source_start_ms") or 0)
        ]
    )

    def fragments(word):
        if word.end_ms == word.start_ms:
            for source_start, source_end, timeline_start in slices:
                if source_start <= word.start_ms <= source_end:
                    point = timeline_start + word.start_ms - source_start
                    return "zero_width_anchor", [(point, point)]
            return "removed", []
        pieces = []
        for source_start, source_end, timeline_start in slices:
            start, end = max(word.start_ms, source_start), min(word.end_ms, source_end)
            if end > start:
                pieces.append((timeline_start + start - source_start, timeline_start + end - source_start))
        retained = sum(end - start for start, end in pieces)
        if not pieces:
            return "removed", []
        return ("fully_retained" if retained == word.end_ms - word.start_ms else "partially_cut"), pieces

    ordered = sorted(aligned_words, key=lambda word: (word.start_ms, word.end_ms, word.word_id))
    boundaries = []
    for left, right in zip(ordered, ordered[1:]):
        delta = right.start_ms - left.end_ms
        source_relation = "separated" if delta > 0 else "touching" if delta == 0 else "overlapping"
        left_status, left_fragments = fragments(left)
        right_status, right_fragments = fragments(right)
        if not left_fragments or not right_fragments:
            final_relation, final_gap, final_overlap = "cut", 0, 0
        else:
            rendered_delta = right_fragments[0][0] - left_fragments[-1][1]
            final_relation = "separated" if rendered_delta > 0 else "touching" if rendered_delta == 0 else "overlapping"
            final_gap, final_overlap = max(0, rendered_delta), max(0, -rendered_delta)
        related_gaps = [
            gap for gap in gaps
            if gap.start_ms < max(left.end_ms, right.end_ms)
            and gap.end_ms > min(left.start_ms, right.start_ms)
        ]
        safe_values = [gap.safe_edit_boundary for gap in related_gaps if gap.safe_edit_boundary is not None]
        boundaries.append(DubbingSemanticBoundaryEvidence(
            boundary_id=f"{left.word_id}:{right.word_id}",
            left_word_id=left.word_id,
            right_word_id=right.word_id,
            left_text=left.text,
            right_text=right.text,
            left_source_start_ms=left.start_ms,
            left_source_end_ms=left.end_ms,
            right_source_start_ms=right.start_ms,
            right_source_end_ms=right.end_ms,
            source_relation=source_relation,
            source_gap_ms=max(0, delta),
            source_overlap_ms=max(0, -delta),
            final_relation=final_relation,
            final_gap_ms=final_gap,
            final_overlap_ms=final_overlap,
            left_render_status=left_status,
            right_render_status=right_status,
            left_final_fragments=left_fragments,
            right_final_fragments=right_fragments,
            low_energy_evidence=related_gaps,
            safe_edit_boundary=(all(safe_values) if safe_values else None),
        ))
    return DubbingSemanticBoundaryAudit(
        source_revision=source_revision,
        plan_revision=plan_revision,
        candidate_id=candidate_id,
        audio_sha256=audio_sha256,
        candidate_evidence_fingerprint=candidate_evidence_fingerprint,
        candidate_clip_projection_fingerprint=candidate_clip_projection_fingerprint,
        expected_spoken_text=expected_spoken_text,
        aligned_words=ordered,
        audio_gap_evidence=list(gaps),
        boundaries=boundaries,
    )


def reuse_unchanged_boundary_reviews(
    previous: DubbingSemanticBoundaryAudit | None,
    current: DubbingSemanticBoundaryAudit,
) -> DubbingSemanticBoundaryAudit:
    """Keep decisions only for unchanged words and their rendered relationship."""
    if previous is None or any(
        getattr(previous, key) != getattr(current, key)
        for key in ("source_revision", "plan_revision", "candidate_id", "audio_sha256",
                    "expected_spoken_text", "aligned_words")
    ):
        return current
    fields = {
        "boundary_id", "left_word_id", "right_word_id", "left_text", "right_text",
        "left_source_start_ms", "left_source_end_ms", "right_source_start_ms",
        "right_source_end_ms", "source_relation", "source_gap_ms", "source_overlap_ms",
        "final_relation", "final_gap_ms", "final_overlap_ms", "left_render_status",
        "right_render_status", "safe_edit_boundary",
    }
    old = {boundary.boundary_id: boundary for boundary in previous.boundaries}
    unchanged = {
        boundary.boundary_id for boundary in current.boundaries
        if boundary.boundary_id in old
        and boundary.model_dump(include=fields) == old[boundary.boundary_id].model_dump(include=fields)
    }
    reviews = [review for review in previous.agent_reviews if review.boundary_id in unchanged]
    complete = bool(current.boundaries) and len(reviews) == len(current.boundaries)
    status = "pending_agent"
    if complete and all(review.disposition == "acceptable" for review in reviews):
        status = "accepted"
    elif any(review.disposition == "recover" for review in reviews):
        status = "recovery_required"
    return current.model_copy(update={"agent_reviews": reviews, "status": status})


__all__ = [
    "reuse_unchanged_boundary_reviews",
    "GAP_PROCESSING_EVIDENCE_ID",
    "OUTER_SAFETY_PADDING_MS",
    "SourcePauseRhythmEvidence",
    "adjacent_words",
    "build_semantic_boundary_audit",
    "map_source_pause_rhythm",
    "needs_single_regeneration",
    "process_gap_evidence",
    "semantic_boundary_word_pairs",
]
