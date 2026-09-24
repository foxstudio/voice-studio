"""Prosody repair for a generated dubbing take.

A take can come back with two defects that make a line hard to follow:

* a pause inside a phrase that should run on (the evidence shows a long
  low-energy hole where the text has no punctuation), and
* a missing pause where a new clause starts (the text has a clause or comma
  mark but the words touch).

The Skill's first recovery level is a safe gap edit.  This module only plans and
applies that edit: every cut is taken from the take's own aligned words, both
sides keep their safety margin, and a boundary without a proven cut point is
left for the next level (regeneration, then phrase-by-phrase generation).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

CLAUSE_PUNCTUATION = "。！？!?；;"
COMMA_PUNCTUATION = "，,、：:…—"
ALL_PUNCTUATION = CLAUSE_PUNCTUATION + COMMA_PUNCTUATION + "「」『』（）()《》〈〉\"' "

# Minimum breath a listener needs to hear the boundary.  A sentence carries a
# longer one than a comma clause; a phrase with no mark at all must not be
# interrupted.
CLAUSE_GAP_MS = 260
COMMA_GAP_MS = 150
RUN_ON_GAP_MS = 0

SAFETY_MARGIN_MS = 80
DEFAULT_PHRASE_GAP_MS = 300


@dataclass(frozen=True)
class ProsodyCut:
    """One planned cut between two words of the same take."""

    left_text: str
    right_text: str
    punctuation_class: str
    cut_ms: int
    resume_ms: int
    gap_ms: int
    left_word_id: str
    right_word_id: str


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def punctuation_class(text: str, left: str, cursor: int) -> tuple[str, int]:
    """Classify what follows ``left`` inside the frozen line."""

    index = text.find(left, cursor)
    if index < 0:
        return "none", cursor
    position = index + len(left)
    while position < len(text) and text[position] in " \t":
        position += 1
    if position < len(text):
        mark = text[position]
        if mark in CLAUSE_PUNCTUATION:
            return "clause", position
        if mark in COMMA_PUNCTUATION:
            return "comma", position
    return "none", position


def gap_for_class(punctuation_class_name: str) -> int:
    if punctuation_class_name == "clause":
        return CLAUSE_GAP_MS
    if punctuation_class_name == "comma":
        return COMMA_GAP_MS
    return RUN_ON_GAP_MS


def plan_prosody_cuts(
    *,
    boundaries: Sequence[Any],
    aligned_words: Sequence[Any],
    expected_spoken_text: str,
    min_phrase_gap_ms: int = DEFAULT_PHRASE_GAP_MS,
    max_join_gap_ms: int = 100,
) -> list[ProsodyCut]:
    """Cuts the fixed rules can prove safe, in playback order."""

    words = {
        str(_value(item, "word_id", "")): {
            "start_ms": int(_value(item, "start_ms", 0) or 0),
            "end_ms": int(_value(item, "end_ms", 0) or 0),
        }
        for item in aligned_words
        if _value(item, "word_id")
    }
    cuts: list[ProsodyCut] = []
    cursor = 0
    for boundary in boundaries:
        left = str(_value(boundary, "left_text", "") or "")
        right = str(_value(boundary, "right_text", "") or "")
        gap = int(_value(boundary, "final_gap_ms", 0) or 0)
        overlap = int(_value(boundary, "final_overlap_ms", 0) or 0)
        if overlap > 0:
            continue
        retained = {
            str(_value(boundary, side, "fully_retained") or "")
            for side in ("left_render_status", "right_render_status")
        }
        if not retained <= {"fully_retained", "zero_width_anchor"}:
            continue
        name, cursor = punctuation_class(expected_spoken_text, left, cursor)
        if name == "none":
            if gap < min_phrase_gap_ms:
                continue
        elif gap > max_join_gap_ms:
            continue
        left_word = words.get(str(_value(boundary, "left_word_id", "") or ""))
        right_word = words.get(str(_value(boundary, "right_word_id", "") or ""))
        if left_word is None or right_word is None:
            continue
        # The service requires both sides of a cut to keep their own safety
        # margin, so only a pause wide enough for both is cuttable.
        earliest = left_word["end_ms"] + SAFETY_MARGIN_MS
        latest = right_word["start_ms"] - SAFETY_MARGIN_MS
        if earliest > latest:
            continue
        cut_ms = min(latest, max(earliest, (left_word["end_ms"] + right_word["start_ms"]) // 2))
        cuts.append(
            ProsodyCut(
                left_text=left,
                right_text=right,
                punctuation_class=name,
                cut_ms=cut_ms,
                resume_ms=cut_ms,
                gap_ms=gap_for_class(name),
                left_word_id=str(_value(boundary, "left_word_id", "") or ""),
                right_word_id=str(_value(boundary, "right_word_id", "") or ""),
            )
        )
    cuts.sort(key=lambda item: item.cut_ms)
    return cuts


def build_slices(
    *,
    clip: dict[str, Any],
    cuts: Sequence[ProsodyCut],
    aligned_words: Sequence[Any],
) -> list[dict[str, Any]]:
    """Turn planned cuts into split slices that tile the whole candidate crop."""

    if not cuts:
        return []
    source_start_ms = int(clip.get("source_start_ms") or 0)
    source_end_ms = int(clip.get("source_end_ms") or 0)
    words = [
        {
            "word_id": str(_value(item, "word_id", "") or ""),
            "start_ms": int(_value(item, "start_ms", 0) or 0),
            "end_ms": int(_value(item, "end_ms", 0) or 0),
        }
        for item in aligned_words
        if _value(item, "word_id")
    ]
    pieces: list[dict[str, int]] = []
    previous_start = source_start_ms
    previous_gap = 0
    for cut in cuts:
        if cut.cut_ms <= previous_start or cut.resume_ms >= source_end_ms:
            return []
        pieces.append(
            {
                "source_start_ms": previous_start,
                "source_end_ms": cut.cut_ms,
                "timeline_gap_before_ms": previous_gap,
            }
        )
        previous_start = cut.resume_ms
        previous_gap = cut.gap_ms
    pieces.append(
        {
            "source_start_ms": previous_start,
            "source_end_ms": source_end_ms,
            "timeline_gap_before_ms": previous_gap,
        }
    )
    subject_ids = [
        str(item) for item in (clip.get("target_subtitle_ids") or [])
    ]
    slices: list[dict[str, Any]] = []
    for index, piece in enumerate(pieces):
        last = index == len(pieces) - 1
        covered = [
            word
            for word in words
            if word["start_ms"] >= piece["source_start_ms"]
            and (
                word["start_ms"] < piece["source_end_ms"]
                or (last and word["start_ms"] <= piece["source_end_ms"])
            )
        ]
        if not covered:
            return []
        slices.append(
            {
                "target_subtitle_ids": subject_ids,
                "source_start_ms": piece["source_start_ms"],
                "source_end_ms": piece["source_end_ms"],
                "speech_start_ms": min(word["start_ms"] for word in covered),
                "speech_end_ms": max(word["end_ms"] for word in covered),
                "alignment_word_ids": [word["word_id"] for word in covered],
                "timeline_gap_before_ms": piece["timeline_gap_before_ms"],
            }
        )
    word_ids = [word for item in slices for word in item["alignment_word_ids"]]
    if len(word_ids) != len(set(word_ids)) or len(slices) < 2:
        return []
    return slices


def repair_clips(
    clips: Sequence[dict[str, Any]],
    *,
    boundaries: Sequence[Any],
    aligned_words: Sequence[Any],
    expected_spoken_text: str,
    min_phrase_gap_ms: int = DEFAULT_PHRASE_GAP_MS,
    max_join_gap_ms: int = 100,
) -> list[dict[str, Any]]:
    """Rewritten slices for a single-clip take, or the clips unchanged.

    Only one terminal split is produced.  Anything the rules cannot prove (no
    safe cut point, several clips already in place, text that does not line up
    with the words) comes back untouched so the caller keeps its current,
    playable arrangement instead of guessing.
    """

    if len(clips) != 1:
        return list(clips)
    clip = dict(clips[0])
    cuts = plan_prosody_cuts(
        boundaries=boundaries,
        aligned_words=aligned_words,
        expected_spoken_text=expected_spoken_text,
        min_phrase_gap_ms=min_phrase_gap_ms,
        max_join_gap_ms=max_join_gap_ms,
    )
    slices = build_slices(clip=clip, cuts=cuts, aligned_words=aligned_words)
    if not slices:
        return list(clips)
    return _apply_slices(clip, slices)


def _apply_slices(
    clip: dict[str, Any],
    slices: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    clip_id = str(clip.get("clip_id") or "")
    original_source_start_ms = int(clip.get("source_start_ms") or 0)
    original_timeline_start_ms = int(clip.get("start_ms") or 0)
    slice_count = len(slices)
    repaired: list[dict[str, Any]] = []
    for index, item in enumerate(slices, start=1):
        duration_ms = int(item["source_end_ms"]) - int(item["source_start_ms"])
        timeline_start_ms = (
            original_timeline_start_ms
            + int(item["source_start_ms"])
            - original_source_start_ms
            + int(item.get("timeline_gap_before_ms") or 0)
        )
        slice_id = clip_id
        if index > 1:
            slice_id = f"{clip_id}__part_{index:03d}"
        repaired.append(
            {
                **clip,
                "clip_id": slice_id,
                "media_source_clip_id": str(
                    clip.get("media_source_clip_id") or clip_id
                ),
                "start_ms": timeline_start_ms,
                "end_ms": timeline_start_ms + duration_ms,
                "source_start_ms": int(item["source_start_ms"]),
                "source_end_ms": int(item["source_end_ms"]),
                "dubbing_slice_index": index,
                "dubbing_slice_count": slice_count,
                "dubbing_alignment_word_ids": list(item["alignment_word_ids"]),
                "dubbing_timeline_gap_before_ms": int(
                    item.get("timeline_gap_before_ms") or 0
                ),
            }
        )
    return repaired


__all__ = [
    "CLAUSE_GAP_MS",
    "COMMA_GAP_MS",
    "ProsodyCut",
    "RUN_ON_GAP_MS",
    "SAFETY_MARGIN_MS",
    "build_slices",
    "gap_for_class",
    "plan_prosody_cuts",
    "punctuation_class",
    "repair_clips",
]
