"""Deterministic, candidate-bound name claims from saved visual text."""

from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization import (
    entity_variant_safety,
    research_evidence,
)


class AsrVisualNameEvidence(BaseModel):
    """One exact screen spelling linked to one transcript name variant."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["visual-name-evidence-v1"] = (
        "visual-name-evidence-v1"
    )
    evidence_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    source_segment_ids: list[str] = Field(min_length=1)
    transcript_variant: str = Field(min_length=1)
    visible_name: str = Field(min_length=1)
    frame_ids: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0.9, le=1)
    match_basis: Literal["shared_name_token", "spelling_similarity"]


def build_visual_name_evidence(
    *,
    segments: list[research_evidence.AsrResearchEvidenceSegment],
    candidates: list[research_evidence.AsrResearchEvidenceCandidate],
    visual_hints: list[research_evidence.AsrResearchVisualHint],
) -> list[AsrVisualNameEvidence]:
    """Build conservative claims without asking a model or reading files."""

    candidates_by_id = {
        item.candidate_id: item
        for item in candidates
        if item.category == "proper_noun"
    }
    claims: list[AsrVisualNameEvidence] = []
    for hint in visual_hints:
        candidate = candidates_by_id.get(hint.candidate_id)
        frame_ids = _unique_strings(hint.frame_ids)
        if (
            candidate is None
            or hint.confidence < 0.9
            or not frame_ids
        ):
            continue
        for variant in _unique_strings(candidate.target_terms):
            source_segment_ids = [
                item.segment_id
                for item in segments
                if _contains_exact_phrase(item.text, variant)
            ]
            if (
                not source_segment_ids
                or not entity_variant_safety
                .looks_like_proper_name_candidate(variant)
            ):
                continue
            for visible_name in _unique_strings(hint.visible_text):
                match_basis = _name_match_basis(
                    variant,
                    visible_name,
                )
                if match_basis is None:
                    continue
                claims.append(
                    AsrVisualNameEvidence(
                        evidence_id=(
                            f"visual_name:{hint.hint_id}:"
                            f"{len(claims) + 1:02d}"
                        ),
                        candidate_id=candidate.candidate_id,
                        question_id=hint.question_id,
                        source_segment_ids=source_segment_ids,
                        transcript_variant=variant,
                        visible_name=visible_name,
                        frame_ids=frame_ids,
                        confidence=hint.confidence,
                        match_basis=match_basis,
                    )
                )
    return _dedupe_claims(
        _remove_ambiguous_claims(claims)
    )[:24]


def _name_match_basis(
    transcript_variant: str,
    visible_name: str,
) -> Literal["shared_name_token", "spelling_similarity"] | None:
    if _number_tokens(transcript_variant) != _number_tokens(
        visible_name
    ):
        return None
    if not entity_variant_safety.is_safe_proper_name_replacement(
        visible_name,
        transcript_variant,
    ):
        return None
    similarity = SequenceMatcher(
        None,
        transcript_variant.casefold(),
        visible_name.casefold(),
    ).ratio()
    if similarity < 0.68:
        return None
    left_tokens = _name_tokens(transcript_variant)
    right_tokens = _name_tokens(visible_name)
    return (
        "shared_name_token"
        if any(
            token in right_tokens
            for token in left_tokens
            if len(token) >= 4
        )
        else "spelling_similarity"
    )


def _number_tokens(value: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", value))


def _remove_ambiguous_claims(
    claims: list[AsrVisualNameEvidence],
) -> list[AsrVisualNameEvidence]:
    names_by_variant: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in claims:
        names_by_variant[
            (
                item.candidate_id,
                item.transcript_variant.casefold(),
            )
        ].add(item.visible_name.casefold())
    return [
        item
        for item in claims
        if len(
            names_by_variant[
                (
                    item.candidate_id,
                    item.transcript_variant.casefold(),
                )
            ]
        )
        == 1
    ]


def _dedupe_claims(
    claims: list[AsrVisualNameEvidence],
) -> list[AsrVisualNameEvidence]:
    output: list[AsrVisualNameEvidence] = []
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    for item in claims:
        key = (
            item.candidate_id,
            item.transcript_variant.casefold(),
            item.visible_name.casefold(),
            tuple(item.frame_ids),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _contains_exact_phrase(text: str, phrase: str) -> bool:
    return bool(
        re.search(
            rf"(?<!\w){re.escape(phrase.strip())}(?!\w)",
            text,
            flags=re.IGNORECASE,
        )
    )


def _name_tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(
            r"[^\W\d_]+",
            value,
            flags=re.UNICODE,
        )
        if len(token) >= 2
    }


def _unique_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return output


__all__ = [
    "AsrVisualNameEvidence",
    "build_visual_name_evidence",
]
