"""Candidate forced-alignment evidence and acoustic-gap projection."""

from __future__ import annotations

import re
from typing import Any

from app.schemas.video_localization_dubbing_production import (
    DubbingAudioGapEvidence,
    DubbingAutomaticAudioEvidence,
    DubbingCandidateAlignedWord,
)


SAFE_CUT_PADDING_MS = 80


def safe_internal_gap_cut(
    gap: DubbingAudioGapEvidence,
    words: list[DubbingCandidateAlignedWord],
) -> tuple[int, int] | None:
    """Return only the removable core between adjacent aligned words."""

    if gap.kind != "internal":
        return None
    left = [word for word in words if word.end_ms <= gap.start_ms + 80]
    right = [word for word in words if word.start_ms >= gap.end_ms - 80]
    if not left or not right:
        return None
    cut_start_ms = max(
        gap.start_ms,
        left[-1].end_ms + SAFE_CUT_PADDING_MS,
    )
    cut_end_ms = min(
        gap.end_ms,
        right[0].start_ms - SAFE_CUT_PADDING_MS,
    )
    return (
        (cut_start_ms, cut_end_ms)
        if cut_end_ms > cut_start_ms
        else None
    )


def alignment_language(target_language: str, text: str) -> str:
    """Map the public language contract to the aligner's small name surface."""

    normalized = target_language.strip().lower()
    if normalized.startswith("zh"):
        return "Chinese"
    if normalized.startswith("en"):
        return "English"
    ascii_letters = len(re.findall(r"[A-Za-z]", text))
    cjk_characters = len(re.findall(r"[\u4e00-\u9fff]", text))
    return "English" if ascii_letters > cjk_characters else "Chinese"


def normalize_aligned_words(
    items: list[dict[str, Any]],
    *,
    duration_ms: int,
) -> list[DubbingCandidateAlignedWord]:
    """Convert provider seconds to stable, bounded candidate word evidence."""

    words: list[DubbingCandidateAlignedWord] = []
    previous_end_ms = 0
    for raw in items:
        text = str(raw.get("text") or "").strip()
        start_ms = max(0, round(float(raw.get("start_time") or 0) * 1000))
        end_ms = min(
            duration_ms,
            round(float(raw.get("end_time") or 0) * 1000),
        )
        # Qwen's timestamp grid can represent an isolated short token as one
        # exact point. Preserve that real provider anchor; VAD owns the
        # positive whole-speech interval and no internal span is invented.
        if not text or end_ms < start_ms or start_ms < previous_end_ms:
            continue
        words.append(
            DubbingCandidateAlignedWord(
                word_id=f"candidate_word_{len(words) + 1:04d}",
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )
        previous_end_ms = end_ms
    return words


def with_alignment_evidence(
    audio: DubbingAutomaticAudioEvidence,
    words: list[DubbingCandidateAlignedWord],
) -> DubbingAutomaticAudioEvidence:
    """Bind real aligned tokens to gaps without making semantic decisions."""

    if not words:
        return audio.model_copy(update={"aligned_words": []})
    reviewed_gaps = []
    for gap in audio.gap_evidence:
        tolerance_ms = SAFE_CUT_PADDING_MS
        boundary_slop_ms = min(tolerance_ms, max(0, gap.duration_ms // 4))
        word_overlaps_gap_interior = any(
            word.start_ms < gap.end_ms - boundary_slop_ms
            and word.end_ms > gap.start_ms + boundary_slop_ms
            for word in words
        )
        left = [word for word in words if word.end_ms <= gap.start_ms + tolerance_ms]
        right = [word for word in words if word.start_ms >= gap.end_ms - tolerance_ms]
        supported = (
            not word_overlaps_gap_interior
            and (
                gap.kind == "leading" and bool(right)
                or gap.kind == "trailing" and bool(left)
                or gap.kind == "internal"
                and safe_internal_gap_cut(gap, words) is not None
            )
        )
        if not supported:
            reviewed_gaps.append(
                gap.model_copy(
                    update={
                        "safe_edit_boundary": (
                            None
                            if gap.kind == "internal"
                            else gap.safe_edit_boundary
                        )
                    }
                )
            )
            continue
        evidence_sources = list(
            dict.fromkeys([*gap.evidence_sources, "word_alignment"])
        )
        evidence_ids = list(
            dict.fromkeys(
                [
                    *gap.evidence_ids,
                    *(
                        [f"word_alignment:{left[-1].word_id}:end"]
                        if left
                        else []
                    ),
                    *(
                        [f"word_alignment:{right[0].word_id}:start"]
                        if right
                        else []
                    ),
                ]
            )
        )
        reviewed_gaps.append(
            gap.model_copy(
                update={
                    "evidence_sources": evidence_sources,
                    "evidence_ids": evidence_ids,
                    "safe_edit_boundary": True,
                }
            )
        )
    return audio.model_copy(
        update={
            "aligned_words": words,
            "gap_evidence": reviewed_gaps,
        }
    )


def validate_slice_alignment(
    *,
    slices: list[object],
    words: list[DubbingCandidateAlignedWord],
    source_start_ms: int,
    source_end_ms: int,
) -> None:
    """Require a split to partition the candidate's real aligned words exactly."""

    relevant_words = [word for word in words if word.end_ms > word.start_ms]
    if not relevant_words:
        raise ValueError("候选缺少有效的逐词强制对齐证据。")
    if (
        source_start_ms > relevant_words[0].start_ms
        or source_end_ms < relevant_words[-1].end_ms
    ):
        raise ValueError("切片源范围必须完整覆盖候选的全部发音字词。")
    words_by_id = {word.word_id: word for word in relevant_words}
    expected_ids = [word.word_id for word in relevant_words]
    actual_ids = [
        word_id
        for item in slices
        for word_id in getattr(item, "alignment_word_ids")
    ]
    if actual_ids != expected_ids:
        raise ValueError("切片必须按原顺序完整且唯一覆盖候选的逐词对齐证据。")
    for item in slices:
        aligned = [
            words_by_id[word_id]
            for word_id in getattr(item, "alignment_word_ids")
        ]
        slice_start_ms = int(getattr(item, "source_start_ms"))
        slice_end_ms = int(getattr(item, "source_end_ms"))
        if any(
            word.start_ms < slice_start_ms or word.end_ms > slice_end_ms
            for word in aligned
        ):
            raise ValueError("切片源范围不能切入发音字词。")
        if (
            getattr(item, "speech_start_ms") != aligned[0].start_ms
            or getattr(item, "speech_end_ms") != aligned[-1].end_ms
        ):
            raise ValueError("切片的真实发声边界必须与逐词强制对齐结果一致。")


def validate_candidate_clip_coverage(
    *,
    clips: list[dict[str, Any]],
    words: list[DubbingCandidateAlignedWord],
    speech_start_ms: int | None,
    speech_end_ms: int | None,
) -> None:
    """Reject any final projection that cuts into or drops generated speech."""

    ranges = sorted(
        (
            int(clip.get("source_start_ms") or 0),
            int(
                clip.get("source_end_ms")
                or int(clip.get("source_start_ms") or 0)
                + int(clip.get("end_ms") or 0)
                - int(clip.get("start_ms") or 0)
            ),
        )
        for clip in clips
    )
    if not ranges or any(end_ms <= start_ms for start_ms, end_ms in ranges):
        raise ValueError("候选没有完整、有效的最终音频范围。")
    positive_words = [word for word in words if word.end_ms > word.start_ms]
    if not positive_words:
        raise ValueError("候选缺少有效的逐词强制对齐证据。")
    for word in positive_words:
        fully_covered = sum(
            start_ms <= word.start_ms and end_ms >= word.end_ms
            for start_ms, end_ms in ranges
        )
        if fully_covered != 1:
            raise ValueError(
                f"最终音频范围切入了发音字词：{word.text}。"
            )
    protected_start_ms = min(
        positive_words[0].start_ms,
        int(speech_start_ms)
        if speech_start_ms is not None
        else positive_words[0].start_ms,
    )
    protected_end_ms = max(
        positive_words[-1].end_ms,
        int(speech_end_ms)
        if speech_end_ms is not None
        else positive_words[-1].end_ms,
    )
    if ranges[0][0] > protected_start_ms or ranges[-1][1] < protected_end_ms:
        raise ValueError("最终音频范围没有完整覆盖真实发声头尾。")


__all__ = [
    "alignment_language",
    "normalize_aligned_words",
    "safe_internal_gap_cut",
    "validate_candidate_clip_coverage",
    "validate_slice_alignment",
    "with_alignment_evidence",
]
