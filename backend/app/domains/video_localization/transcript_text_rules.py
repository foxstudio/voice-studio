"""Conservative local text rules for reviewed ASR transcripts.

Formatting rules may change only whitespace and punctuation placement. Content
anomalies are emitted as review candidates and are never applied here.
"""

from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization.schemas import (
    VideoLocalizationTranscriptSegment,
)


_CURRENCY_MARKS = "$£€¥"
_WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_REDUNDANT_RELATIVE_PATTERN = re.compile(
    r"\b(?P<lead>which|that|who)\s+both\s+of\s+(?P=lead)\b",
    re.IGNORECASE,
)
_PARALLEL_BETTER_PATTERN = re.compile(
    r"\bbetter\s+[A-Za-z'-]+\s+or\s+better\s+[A-Za-z'-]+\b",
    re.IGNORECASE,
)
_INTERNAL_TITLE_PATTERN = re.compile(r"\b[A-Z][a-z]{2,}\b")
_POSSIBLE_SENTENCE_STARTERS = frozenset(
    {
        "and",
        "because",
        "but",
        "how",
        "so",
        "that",
        "then",
        "there",
        "these",
        "this",
        "those",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
    }
)


class AsrTranscriptTextCandidate(BaseModel):
    """Grounded local signal that the LLM review must explicitly consider."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    code: Literal[
        "adjacent_repetition",
        "redundant_relative_phrase",
        "inconsistent_internal_capitalization",
        "parallel_wording",
        "phonetic_term_outlier",
    ]
    segment_id: str = Field(min_length=1)
    current_excerpt: str = Field(min_length=1)
    proposed_replacement: str = ""
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class AsrTranscriptOrthographyChange(BaseModel):
    """One deterministic display-format change with immutable timing."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1)
    before: str = Field(min_length=1)
    after: str = Field(min_length=1)
    reason: str = Field(min_length=1)


def normalize_transcript_orthography(
    segments: list[VideoLocalizationTranscriptSegment],
) -> tuple[
    list[VideoLocalizationTranscriptSegment],
    list[AsrTranscriptOrthographyChange],
]:
    """Normalize safe display spacing without changing lexical content."""

    output: list[VideoLocalizationTranscriptSegment] = []
    changes: list[AsrTranscriptOrthographyChange] = []
    for segment in segments:
        before = segment.corrected_text or segment.raw_text
        after = normalize_orthography_text(before)
        if after == before:
            output.append(segment.model_copy(deep=True))
            continue
        output.append(
            segment.model_copy(
                update={
                    "corrected_text": after,
                    "review_flags": sorted(
                        {
                            *segment.review_flags,
                            "asr_orthography_normalized",
                        }
                    ),
                },
                deep=True,
            )
        )
        changes.append(
            AsrTranscriptOrthographyChange(
                segment_id=segment.segment_id,
                before=before,
                after=after,
                reason=(
                    "统一金额、百分号和标点附近的空格；"
                    "没有增删词语，也没有改变大小写。"
                ),
            )
        )
    return output, changes


def normalize_orthography_text(value: str) -> str:
    """Return conservative English transcript display formatting."""

    source = str(value or "")
    text = re.sub(r"[ \t]+", " ", source).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(
        rf"(?<=[A-Za-z0-9,.;:!?])([{re.escape(_CURRENCY_MARKS)}])"
        r"\s*(?=\d)",
        r" \1",
        text,
    )
    text = re.sub(
        rf"([{re.escape(_CURRENCY_MARKS)}])\s+(?=\d)",
        r"\1",
        text,
    )
    text = re.sub(r"(?<=\d)\s+%", "%", text)
    text = re.sub(
        rf"([,;:!?])(?=[A-Za-z{re.escape(_CURRENCY_MARKS)}])",
        r"\1 ",
        text,
    )
    return (
        text
        if _alphanumeric_signature(text)
        == _alphanumeric_signature(source)
        else source
    )


def find_transcript_text_candidates(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    language: str,
) -> list[AsrTranscriptTextCandidate]:
    """Find grounded text anomalies; callers decide whether to edit them."""

    if not str(language or "").lower().startswith("en"):
        return []
    texts = {
        segment.segment_id: segment.corrected_text or segment.raw_text
        for segment in segments
    }
    lowercase_forms = {
        match.group(0)
        for text in texts.values()
        for match in _WORD_PATTERN.finditer(text)
        if match.group(0).islower()
    }
    output: list[AsrTranscriptTextCandidate] = []
    seen: set[tuple[str, str, str]] = set()

    def add(
        *,
        code: Literal[
            "adjacent_repetition",
            "redundant_relative_phrase",
            "inconsistent_internal_capitalization",
            "parallel_wording",
            "phonetic_term_outlier",
        ],
        segment_id: str,
        excerpt: str,
        replacement: str,
        reason: str,
        confidence: float,
    ) -> None:
        key = (code, segment_id, excerpt.casefold())
        if key in seen:
            return
        seen.add(key)
        output.append(
            AsrTranscriptTextCandidate(
                candidate_id=(
                    f"text-{segment_id}-{code}-"
                    f"{sum(item.segment_id == segment_id for item in output) + 1:02d}"
                ),
                code=code,
                segment_id=segment_id,
                current_excerpt=excerpt,
                proposed_replacement=replacement,
                reason=reason,
                confidence=confidence,
            )
        )

    for segment in segments:
        text = texts[segment.segment_id]
        word_matches = list(_WORD_PATTERN.finditer(text))
        for left_match, right_match in zip(
            word_matches,
            word_matches[1:],
        ):
            left = left_match.group(0)
            right = right_match.group(0)
            if left.casefold() != right.casefold():
                continue
            separator = text[left_match.end() : right_match.start()]
            if not separator.isspace():
                continue
            add(
                code="adjacent_repetition",
                segment_id=segment.segment_id,
                excerpt=text[left_match.start() : right_match.end()],
                replacement=left,
                reason=(
                    "检测到相邻两个词完全相同；这可能是真实口语重复，"
                    "也可能是听写冗余，需要结合原音和上下文判断。"
                ),
                confidence=0.82,
            )
        for match in _REDUNDANT_RELATIVE_PATTERN.finditer(text):
            relative = match.group("lead")
            replacement = (
                f"both of {relative.lower()}"
                if relative[0].islower()
                else f"Both of {relative.lower()}"
            )
            add(
                code="redundant_relative_phrase",
                segment_id=segment.segment_id,
                excerpt=match.group(0),
                replacement=replacement,
                reason=(
                    "关系词在同一短语里重复出现，可能是听写把口语回撤"
                    "保留了两次；需要结合原音确认是否删去前一个关系词。"
                ),
                confidence=0.84,
            )
        for match in _PARALLEL_BETTER_PATTERN.finditer(text):
            add(
                code="parallel_wording",
                segment_id=segment.segment_id,
                excerpt=match.group(0),
                replacement="",
                reason=(
                    "并列表达两边的搭配可能不对称，可能有一个词听错；"
                    "本地规则无法可靠给出替换词。"
                ),
                confidence=0.58,
            )
        for match in _INTERNAL_TITLE_PATTERN.finditer(text):
            token = match.group(0)
            if (
                token.casefold() not in lowercase_forms
                or token.casefold() in _POSSIBLE_SENTENCE_STARTERS
                or text[match.end() : match.end() + 1] in {"'", "’"}
            ):
                continue
            prefix = text[: match.start()].rstrip()
            if not prefix or prefix[-1:] in ".?!":
                continue
            add(
                code="inconsistent_internal_capitalization",
                segment_id=segment.segment_id,
                excerpt=token,
                replacement=token.lower(),
                reason=(
                    "同一个普通词在全文其他位置使用小写，这里却在句中"
                    "大写；需要确认它不是专名后再统一。"
                ),
                confidence=0.78,
            )

    occurrences: dict[
        str,
        list[tuple[str, str, str, str]],
    ] = defaultdict(list)
    for segment in segments:
        matches = list(_WORD_PATTERN.finditer(texts[segment.segment_id]))
        for index, match in enumerate(matches):
            token = match.group(0)
            if token.islower() and len(token) >= 4:
                occurrences[token.casefold()].append(
                    (
                        segment.segment_id,
                        token,
                        (
                            matches[index - 1].group(0).casefold()
                            if index > 0
                            else ""
                        ),
                        (
                            matches[index + 1].group(0).casefold()
                            if index + 1 < len(matches)
                            else ""
                        ),
                    )
                )
    phonetic_groups: dict[str, list[str]] = defaultdict(list)
    for token in occurrences:
        key = _phonetic_skeleton(token)
        if len(key) >= 3:
            phonetic_groups[key].append(token)
    for variants in phonetic_groups.values():
        if len(variants) < 2:
            continue
        common = max(
            variants,
            key=lambda item: (len(occurrences[item]), item),
        )
        common_count = len(occurrences[common])
        if common_count < 4:
            continue
        common_left = {
            left
            for _segment_id, _excerpt, left, _right in occurrences[common]
            if left
        }
        for variant in variants:
            if (
                variant == common
                or len(occurrences[variant]) * 2 > common_count
                or abs(len(variant) - len(common)) > 1
                or SequenceMatcher(None, variant, common).ratio() < 0.4
            ):
                continue
            for segment_id, excerpt, left, right in occurrences[variant]:
                if (
                    left not in {"a", "an", "the"}
                    or left not in common_left
                ):
                    continue
                add(
                    code="phonetic_term_outlier",
                    segment_id=segment_id,
                    excerpt=excerpt,
                    replacement=common,
                    reason=(
                        f"全文更常出现近音术语“{common}”，当前“{excerpt}”"
                        "可能是听写近音词；必须结合本句含义、原音或画面文字判断，"
                        "不能仅凭词频替换。"
                    ),
                    confidence=0.64,
                )
    return output


def _phonetic_skeleton(value: str) -> str:
    token = str(value or "").casefold()
    token = re.sub(r"c(?=[aou])", "k", token)
    token = re.sub(r"c(?=[eiy])", "s", token)
    token = token.replace("q", "k").replace("x", "ks")
    token = re.sub(r"[aeiouy]", "", token)
    return re.sub(r"(.)\1+", r"\1", token)


def _alphanumeric_signature(value: str) -> str:
    return "".join(
        re.findall(r"[a-z0-9]+", str(value or "").casefold())
    )


__all__ = [
    "AsrTranscriptOrthographyChange",
    "AsrTranscriptTextCandidate",
    "find_transcript_text_candidates",
    "normalize_orthography_text",
    "normalize_transcript_orthography",
]
