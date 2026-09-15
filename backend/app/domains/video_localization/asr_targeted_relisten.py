"""Bounded short-audio ASR evidence for the targeted review round."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.services import asr_service, audio_tools


MAX_TARGETED_RELISTEN_SECTIONS = 8
MAX_TARGETED_RELISTEN_ISSUES = 12
RELISTEN_PADDING_MS = 800


class ReviewSection(Protocol):
    section_id: str
    start_ordinal: int
    end_ordinal: int


class TranscriptSegment(Protocol):
    segment_id: str
    start_ms: int
    end_ms: int


class ReviewIssue(Protocol):
    issue_id: str
    section_id: str
    segment_id: str
    target_segment_ids: list[str]
    confidence: float
    needs_confirmation: bool
    evidence_source_ids: list[str]


class AsrAcousticCandidate(BaseModel):
    """One immutable transcript candidate generated from a short audio slice."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-acoustic-candidate-v1"] = (
        "asr-acoustic-candidate-v1"
    )
    section_id: str = Field(min_length=1)
    issue_ids: list[str] = Field(default_factory=list)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)


def relisten_targeted_sections(
    *,
    audio_path: str,
    engine_id: str,
    language: str,
    sections: list[ReviewSection],
    segments: list[TranscriptSegment],
    context_terms: list[str] | tuple[str, ...] = (),
    is_cancelled: Callable[[], bool] | None = None,
) -> list[AsrAcousticCandidate]:
    """Re-run ASR only on the small ranges selected by round one."""

    if not sections or not segments:
        return []
    audio, sample_rate = audio_tools.read_audio(audio_path)
    if sample_rate <= 0 or len(audio) == 0:
        return []
    duration_ms = round(len(audio) / sample_rate * 1000)
    hotwords = tuple(
        str(item).strip()
        for item in context_terms[:8]
        if str(item).strip()
    )
    candidates: list[AsrAcousticCandidate] = []
    with tempfile.TemporaryDirectory(
        prefix="video-localization-relisten-"
    ) as temp_dir:
        for index, section in enumerate(
            sections[:MAX_TARGETED_RELISTEN_SECTIONS],
            start=1,
        ):
            if is_cancelled and is_cancelled():
                break
            first = segments[section.start_ordinal - 1]
            last = segments[section.end_ordinal - 1]
            start_ms = max(
                0,
                int(first.start_ms) - RELISTEN_PADDING_MS,
            )
            end_ms = min(
                duration_ms,
                int(last.end_ms) + RELISTEN_PADDING_MS,
            )
            if end_ms <= start_ms:
                continue
            start_frame = max(
                0,
                int(sample_rate * start_ms / 1000),
            )
            end_frame = min(
                len(audio),
                int(sample_rate * end_ms / 1000),
            )
            if end_frame <= start_frame:
                continue
            clip_path = (
                Path(temp_dir)
                / f"target-{index:02d}.wav"
            )
            try:
                audio_tools.write_audio(
                    clip_path,
                    audio[start_frame:end_frame],
                    sample_rate,
                    fmt="wav",
                )
                result = asr_service.transcribe(
                    engine_id=engine_id,
                    audio_path=str(clip_path),
                    language=language,
                    hotwords=hotwords,
                )
            except Exception:
                continue
            text = str(result.get("text") or "").strip()
            if not text:
                continue
            candidates.append(
                AsrAcousticCandidate(
                    section_id=section.section_id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                )
            )
    return candidates


def relisten_review_issues(
    *,
    audio_path: str,
    engine_id: str,
    language: str,
    issues: list[ReviewIssue],
    segments: list[TranscriptSegment],
    context_terms: list[str] | tuple[str, ...] = (),
    is_cancelled: Callable[[], bool] | None = None,
) -> list[AsrAcousticCandidate]:
    """Re-run ASR only for the highest-value issue windows."""

    segment_by_id = {
        segment.segment_id: segment for segment in segments
    }
    ranked = sorted(
        issues,
        key=lambda item: (
            not item.needs_confirmation,
            bool(item.evidence_source_ids),
            float(item.confidence),
            item.issue_id,
        ),
    )
    targets: list[tuple[ReviewIssue, list[TranscriptSegment]]] = []
    seen_ranges: set[tuple[str, ...]] = set()
    for issue in ranked:
        target_ids = tuple(
            issue.target_segment_ids or [issue.segment_id]
        )
        if target_ids in seen_ranges:
            continue
        target_segments = [
            segment_by_id[segment_id]
            for segment_id in target_ids
            if segment_id in segment_by_id
        ]
        if len(target_segments) != len(target_ids):
            continue
        seen_ranges.add(target_ids)
        targets.append((issue, target_segments))
        if len(targets) >= MAX_TARGETED_RELISTEN_ISSUES:
            break
    if not targets:
        return []

    audio, sample_rate = audio_tools.read_audio(audio_path)
    if sample_rate <= 0 or len(audio) == 0:
        return []
    duration_ms = round(len(audio) / sample_rate * 1000)
    hotwords = tuple(
        str(item).strip()
        for item in context_terms[:8]
        if str(item).strip()
    )
    candidates: list[AsrAcousticCandidate] = []
    with tempfile.TemporaryDirectory(
        prefix="video-localization-relisten-"
    ) as temp_dir:
        for index, (issue, target_segments) in enumerate(
            targets,
            start=1,
        ):
            if is_cancelled and is_cancelled():
                break
            start_ms = max(
                0,
                int(target_segments[0].start_ms)
                - RELISTEN_PADDING_MS,
            )
            end_ms = min(
                duration_ms,
                int(target_segments[-1].end_ms)
                + RELISTEN_PADDING_MS,
            )
            if end_ms <= start_ms:
                continue
            start_frame = int(sample_rate * start_ms / 1000)
            end_frame = min(
                len(audio),
                int(sample_rate * end_ms / 1000),
            )
            clip_path = Path(temp_dir) / f"issue-{index:02d}.wav"
            try:
                audio_tools.write_audio(
                    clip_path,
                    audio[start_frame:end_frame],
                    sample_rate,
                    fmt="wav",
                )
                result = asr_service.transcribe(
                    engine_id=engine_id,
                    audio_path=str(clip_path),
                    language=language,
                    hotwords=hotwords,
                )
            except Exception:
                continue
            text = str(result.get("text") or "").strip()
            if not text:
                continue
            candidates.append(
                AsrAcousticCandidate(
                    section_id=issue.section_id,
                    issue_ids=[issue.issue_id],
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                )
            )
    return candidates
