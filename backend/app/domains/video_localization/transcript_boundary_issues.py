"""Deterministic discovery of high-confidence cross-segment ASR issues."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization.review_contracts import (
    AsrTranscriptEditPatch,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationTranscriptSegment,
)


_QUESTION_TAIL = re.compile(
    r"(?P<question>\b(?:what|how)\s+about\b.+?)"
    r"\s+(?P<connector>because)\s*[,.!?;:]*$",
    re.IGNORECASE,
)
_SUBORDINATE_HEAD = re.compile(
    r"^(?P<leading>\s*)(?P<connector>although|though)\b",
    re.IGNORECASE,
)
_DECIMAL_TAIL = re.compile(r"(?P<integer>\d+)\.\s*$")
_DECIMAL_HEAD = re.compile(
    r"^(?P<leading>\s*)(?P<fraction>\d+)"
    r"(?=\s+(?:trillion|billion|million|times|percent)\b|\s*%)",
    re.IGNORECASE,
)


class AsrTranscriptBoundaryIssue(BaseModel):
    """One structural issue spanning two adjacent ASR segments."""

    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    primary_segment_id: str = Field(min_length=1)
    related_segment_ids: list[str] = Field(min_length=2)
    current_excerpt: str = Field(min_length=1)
    proposed_replacement: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    patches: list[AsrTranscriptEditPatch] = Field(min_length=2)


def find_transcript_boundary_issues(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    language: str,
) -> list[AsrTranscriptBoundaryIssue]:
    """Return conservative, directly repairable boundary issues.

    These rules intentionally cover only structures whose word sequence can be
    preserved exactly. Broader grammar judgment remains the LLM review task.
    """

    if not str(language or "").lower().startswith("en"):
        return []
    output: list[AsrTranscriptBoundaryIssue] = []
    for left, right in zip(segments, segments[1:]):
        left_text = (left.corrected_text or left.raw_text).strip()
        right_text = (right.corrected_text or right.raw_text).strip()
        question_match = _QUESTION_TAIL.search(left_text)
        subordinate_match = _SUBORDINATE_HEAD.search(right_text)
        if question_match is not None and subordinate_match is not None:
            question = question_match.group("question").strip()
            if re.search(r"[.!?]\s*$", question):
                question_match = None
        if question_match is not None and subordinate_match is not None:
            question = question_match.group("question").strip()
            connector = question_match.group("connector")
            subordinate = subordinate_match.group("connector")
            left_excerpt = left_text[question_match.start() :].strip()
            left_replacement = (
                f"{question.rstrip(' ,.!?;:')}? {connector.capitalize()}"
            )
            right_excerpt = right_text[
                subordinate_match.start("connector") :
                subordinate_match.end("connector")
            ]
            right_replacement = subordinate.lower()
            output.append(
                AsrTranscriptBoundaryIssue(
                    issue_id=(
                        "boundary-"
                        f"{left.segment_id}-{right.segment_id}-question-connector"
                    ),
                    code="question_boundary_before_connector",
                    primary_segment_id=left.segment_id,
                    related_segment_ids=[
                        left.segment_id,
                        right.segment_id,
                    ],
                    current_excerpt=f"{left_excerpt} | {right_excerpt}",
                    proposed_replacement=(
                        f"{left_replacement} | {right_replacement}"
                    ),
                    reason=(
                        "疑问结构在 because 前没有结束，下一片段又以让步从句"
                        "开头；这里更可能是问句结束后继续解释原因。"
                    ),
                    confidence=0.96,
                    patches=[
                        AsrTranscriptEditPatch(
                            segment_id=left.segment_id,
                            current_excerpt=left_excerpt,
                            proposed_replacement=left_replacement,
                        ),
                        AsrTranscriptEditPatch(
                            segment_id=right.segment_id,
                            current_excerpt=right_excerpt,
                            proposed_replacement=right_replacement,
                        ),
                    ],
                )
            )
        decimal_tail = _DECIMAL_TAIL.search(left_text)
        decimal_head = _DECIMAL_HEAD.search(right_text)
        if decimal_tail is None or decimal_head is None:
            continue
        integer = decimal_tail.group("integer")
        fraction = decimal_head.group("fraction")
        output.append(
            AsrTranscriptBoundaryIssue(
                issue_id=(
                    "boundary-"
                    f"{left.segment_id}-{right.segment_id}-decimal"
                ),
                code="decimal_split_across_segments",
                primary_segment_id=left.segment_id,
                related_segment_ids=[left.segment_id, right.segment_id],
                current_excerpt=f"{integer}. | {fraction}",
                proposed_replacement=f"{integer}.{fraction} |",
                reason=(
                    "数字的小数点被 ASR 片段边界拆开，右侧数字后紧跟数量"
                    "单位；合并后更符合完整数值结构。"
                ),
                confidence=0.97,
                patches=[
                    AsrTranscriptEditPatch(
                        segment_id=left.segment_id,
                        current_excerpt=f"{integer}.",
                        proposed_replacement=f"{integer}.{fraction}",
                    ),
                    AsrTranscriptEditPatch(
                        segment_id=right.segment_id,
                        current_excerpt=fraction,
                        proposed_replacement="",
                    ),
                ],
            )
        )
    return output


__all__ = [
    "AsrTranscriptBoundaryIssue",
    "find_transcript_boundary_issues",
]
