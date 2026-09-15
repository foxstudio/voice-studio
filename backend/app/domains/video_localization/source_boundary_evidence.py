"""Shared, deterministic evidence for source-language semantic boundaries.

The source lock owns immutable ASR input.  This module only derives boundary
facts from that input; it does not call models, persist state, or apply
display/TTS sizing rules.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization.localization_source import (
    LocalizationSourceLockResult,
    LocalizationSourceWord,
)


BoundaryClassification = Literal[
    "internal_cue",
    "hard",
    "preferred",
    "ambiguous",
    "forbidden",
]
BoundaryPunctuation = Literal["none", "clause", "terminal"]
BoundaryReasonCode = Literal[
    "cue_boundary",
    "terminal_punctuation",
    "clause_punctuation",
    "confirmed_pause",
    "timing_gap",
    "speaker_change",
    "overlap_speech",
    "segment_change",
    "atomic_token",
    "dependent_phrase",
]


TERMINAL_PUNCTUATION = re.compile(r"[.!?。！？][\"'”’)]*$")
CLAUSE_PUNCTUATION = re.compile(
    r"(?:[,;:，；：]|[—–－―]+)[\"'”’)]*$"
)

# These lists are deliberately conservative.  They prevent an automatic split
# at a word that normally requires its neighbour to complete the phrase.
DEPENDENT_LEFT_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "because",
        "but",
        "by",
        "can",
        "could",
        "for",
        "from",
        "had",
        "has",
        "have",
        "her",
        "his",
        "if",
        "include",
        "included",
        "includes",
        "indicate",
        "indicated",
        "indicates",
        "in",
        "is",
        "its",
        "may",
        "might",
        "mean",
        "means",
        "meant",
        "more",
        "my",
        "never",
        "no",
        "not",
        "of",
        "on",
        "or",
        "should",
        "show",
        "showed",
        "shows",
        "so",
        "suggest",
        "suggested",
        "suggests",
        "than",
        "that",
        "the",
        "their",
        "these",
        "this",
        "those",
        "to",
        "was",
        "were",
        "will",
        "with",
        "without",
        "would",
        "which",
        "who",
        "whom",
        "whose",
        "where",
        "when",
        "your",
    }
)
DEPENDENT_RIGHT_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "be",
        "because",
        "but",
        "can",
        "could",
        "did",
        "do",
        "does",
        "etc",
        "for",
        "he",
        "had",
        "has",
        "have",
        "if",
        "i",
        "is",
        "it",
        "may",
        "might",
        "more",
        "must",
        "of",
        "or",
        "should",
        "she",
        "than",
        "that",
        "the",
        "then",
        "they",
        "to",
        "up",
        "was",
        "we",
        "were",
        "when",
        "where",
        "which",
        "who",
        "will",
        "would",
        "with",
        "i'm",
        "it's",
        "that's",
        "they're",
        "they've",
        "we're",
        "we've",
        "you're",
        "you've",
        "you",
        "yes",
    }
)
NON_TERMINAL_ABBREVIATIONS = frozenset(
    {
        "dr.",
        "e.g.",
        "etc.",
        "i.e.",
        "jr.",
        "mr.",
        "mrs.",
        "ms.",
        "prof.",
        "sr.",
        "st.",
        "u.k.",
        "u.s.",
        "vs.",
    }
)
CONTEXTUAL_ABBREVIATIONS = frozenset({"e.g.", "etc.", "i.e."})


class SourceBoundaryEvidence(BaseModel):
    """One fact-only boundary between two adjacent source words."""

    model_config = ConfigDict(extra="forbid")

    boundary_id: str = Field(min_length=3)
    left_word_id: str = Field(min_length=1)
    right_word_id: str = Field(min_length=1)
    left_cue_id: str = Field(min_length=1)
    right_cue_id: str = Field(min_length=1)
    position_after_word: int = Field(ge=1)
    cue_boundary: bool
    eligible: bool
    punctuation: BoundaryPunctuation
    pause_ms: int = Field(ge=0)
    pause_confidence: Literal["none", "low", "medium", "high"]
    segment_change: bool
    speaker_change: bool
    overlap_speech: bool
    objective_support: bool
    classification: BoundaryClassification
    score: float
    reason_codes: list[BoundaryReasonCode] = Field(default_factory=list)
    selected: bool = False


def build_source_boundary_evidence(
    source_lock: LocalizationSourceLockResult,
) -> list[SourceBoundaryEvidence]:
    """Derive ordered boundary evidence without changing the source lock."""

    words = source_lock.input.words
    word_to_cue: dict[str, str] = {}
    cue_has_overlap: dict[str, bool] = {}
    cue_speakers: dict[str, set[str]] = {}
    word_by_id = {item.word_id: item for item in words}
    for cue in source_lock.input.cues:
        overlap = False
        speakers: set[str] = set()
        for word_id in cue.source_word_ids:
            word_to_cue[word_id] = cue.cue_id
            word = word_by_id[word_id]
            overlap = overlap or word.has_speaker_overlap
            if word.speaker_cluster_id:
                speakers.add(word.speaker_cluster_id)
        cue_has_overlap[cue.cue_id] = overlap
        cue_speakers[cue.cue_id] = speakers

    pause_by_words = {
        (item.left_word_id, item.right_word_id): item
        for item in source_lock.input.pauses
    }
    evidence: list[SourceBoundaryEvidence] = []
    for position, (left, right) in enumerate(
        zip(words, words[1:]),
        start=1,
    ):
        left_cue_id = word_to_cue[left.word_id]
        right_cue_id = word_to_cue[right.word_id]
        cue_boundary = left_cue_id != right_cue_id
        pause = pause_by_words.get((left.word_id, right.word_id))
        pause_ms = (
            pause.gap_ms
            if pause is not None
            else max(0, right.start_ms - left.end_ms)
        )
        pause_confidence = pause.confidence if pause is not None else "none"
        punctuation = _punctuation(left.text)
        left_speakers = cue_speakers[left_cue_id] or (
            {left.speaker_cluster_id} if left.speaker_cluster_id else set()
        )
        right_speakers = cue_speakers[right_cue_id] or (
            {right.speaker_cluster_id} if right.speaker_cluster_id else set()
        )
        speaker_change = bool(
            left_speakers
            and right_speakers
            and left_speakers != right_speakers
        )
        overlap_speech = bool(
            cue_has_overlap[left_cue_id]
            or cue_has_overlap[right_cue_id]
        )
        segment_change = bool(
            left.segment_id
            and right.segment_id
            and left.segment_id != right.segment_id
        )
        atomic_token = boundary_splits_atomic_token(words, position)
        dependent_phrase = (
            punctuation != "terminal"
            and boundary_splits_dependent_phrase(
                left.text,
                right.text,
                allow_lowercase_start=(punctuation == "clause"),
            )
        )

        reason_codes: list[BoundaryReasonCode] = []
        score = 0.0
        if cue_boundary:
            reason_codes.append("cue_boundary")
        if punctuation == "terminal":
            reason_codes.append("terminal_punctuation")
            score += 8.0
        elif punctuation == "clause":
            reason_codes.append("clause_punctuation")
            score += 3.0
        if pause is not None and pause.confidence in {"medium", "high"}:
            reason_codes.append("confirmed_pause")
            score += 7.0 if pause.confidence == "high" else 4.0
        elif pause_ms >= 700:
            reason_codes.append("timing_gap")
            score += 4.0
        elif pause_ms >= 350:
            reason_codes.append("timing_gap")
            score += 2.0
        if segment_change:
            reason_codes.append("segment_change")
            score += 1.5
        if speaker_change:
            reason_codes.append("speaker_change")
            score += 100.0
        if overlap_speech:
            reason_codes.append("overlap_speech")
            score += 6.0
        if atomic_token:
            reason_codes.append("atomic_token")
            score -= 100.0
        if dependent_phrase:
            reason_codes.append("dependent_phrase")
            score -= 100.0

        objective_support = bool(
            punctuation != "none"
            or pause_ms >= 250
            or pause_confidence in {"medium", "high"}
            or speaker_change
            or overlap_speech
        )
        if speaker_change and not overlap_speech:
            classification = "hard"
        elif atomic_token or dependent_phrase:
            classification = "forbidden"
        elif cue_boundary or objective_support:
            classification = "preferred" if score >= 6 else "ambiguous"
        else:
            classification = "internal_cue"
        evidence.append(
            SourceBoundaryEvidence(
                boundary_id=f"{left.word_id}:{right.word_id}",
                left_word_id=left.word_id,
                right_word_id=right.word_id,
                left_cue_id=left_cue_id,
                right_cue_id=right_cue_id,
                position_after_word=position,
                cue_boundary=cue_boundary,
                eligible=classification not in {
                    "forbidden",
                    "internal_cue",
                },
                punctuation=punctuation,
                pause_ms=pause_ms,
                pause_confidence=pause_confidence,
                segment_change=segment_change,
                speaker_change=speaker_change,
                overlap_speech=overlap_speech,
                objective_support=objective_support,
                classification=classification,
                score=score,
                reason_codes=reason_codes,
            )
        )
    return evidence


def boundary_splits_atomic_token(
    words: list[LocalizationSourceWord],
    position: int,
) -> bool:
    """Return true for decimals, abbreviations, and initialisms."""

    if position <= 0 or position >= len(words):
        return False
    left = words[position - 1].text.strip()
    right = words[position].text.strip()
    previous = words[position - 2].text.strip() if position >= 2 else ""
    if right and right[0].isdigit():
        if re.search(r"\d\.$", left):
            return True
        if left == "." and previous and previous[-1].isdigit():
            return True
    joined_left = join_source_words(
        [item.text for item in words[max(0, position - 4) : position]]
    ).casefold()
    last_token = joined_left.rsplit(" ", 1)[-1]
    if last_token in CONTEXTUAL_ABBREVIATIONS:
        return bool(right[:1].islower())
    if last_token in NON_TERMINAL_ABBREVIATIONS:
        return True
    return bool(
        re.search(r"(?:\b[A-Za-z]\.){2,}$", joined_left)
        and re.match(r"^[A-Za-z]", right)
    )


def boundary_splits_dependent_phrase(
    left: str,
    right: str,
    *,
    allow_lowercase_start: bool = False,
) -> bool:
    stripped_left = left.strip()
    stripped_right = right.strip()
    left_token = _plain_token(stripped_left)
    right_token = _plain_token(stripped_right)
    return (
        left_token.endswith(("'s", "’s"))
        or (
            stripped_right[:1].islower()
            and not allow_lowercase_start
        )
        or left_token in DEPENDENT_LEFT_WORDS
        or right_token in DEPENDENT_RIGHT_WORDS
    )


def join_source_words(values: list[str]) -> str:
    """Join tokenized source words into readable text."""

    text = " ".join(value.strip() for value in values if value.strip())
    text = re.sub(r"\s+([,.;:!?%。，；：！？])", r"\1", text)
    text = re.sub(r"([(\[“‘])\s+", r"\1", text)
    text = re.sub(r"\s+([)\]”’])", r"\1", text)
    return text.strip()


def _punctuation(text: str) -> BoundaryPunctuation:
    if TERMINAL_PUNCTUATION.search(text.strip()):
        return "terminal"
    if CLAUSE_PUNCTUATION.search(text.strip()):
        return "clause"
    return "none"


def _plain_token(value: str) -> str:
    return value.casefold().strip(".,!?;:，。！？；：\"'“”‘’()[]{}")


__all__ = [
    "BoundaryClassification",
    "BoundaryPunctuation",
    "BoundaryReasonCode",
    "SourceBoundaryEvidence",
    "boundary_splits_atomic_token",
    "boundary_splits_dependent_phrase",
    "build_source_boundary_evidence",
    "join_source_words",
]
