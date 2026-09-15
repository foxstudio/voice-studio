"""Deterministic editable chunks for long-form localization generation.

The document brief owns whole-video understanding and macro sections.  This
module turns those exhaustive section ranges into smaller, stable model-edit
units without asking a model to rediscover order or coverage.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization.localization_brief_contracts import (
    LocalizationDocumentSection,
)
from app.domains.video_localization.localization_source import (
    LocalizationSourceCue,
    LocalizationSourcePause,
)


CHUNK_MANIFEST_VERSION = "localization-generation-chunks-v1"
TARGET_SOURCE_CHARACTERS = 2_400
MIN_SOURCE_CHARACTERS = 1_200
MAX_SOURCE_CHARACTERS = 3_600
CONTEXT_CUE_COUNT = 3


class LocalizationGenerationChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-generation-chunk-v1"
    ] = "localization-generation-chunk-v1"
    chunk_id: str = Field(pattern=r"^chunk_\d{4}$")
    section_id: str = Field(pattern=r"^section_\d{4}$")
    source_cue_ids: list[str] = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_text: str = Field(min_length=1)
    readonly_context_before: list[str] = Field(default_factory=list)
    readonly_context_after: list[str] = Field(default_factory=list)


class LocalizationGenerationChunkManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-generation-chunks-v1"
    ] = CHUNK_MANIFEST_VERSION
    source_fingerprint: str = Field(min_length=64, max_length=64)
    manifest_fingerprint: str = Field(min_length=64, max_length=64)
    chunks: list[LocalizationGenerationChunk] = Field(min_length=1)


def plan_localization_generation_chunks(
    *,
    source_fingerprint: str,
    cues: list[LocalizationSourceCue],
    sections: list[LocalizationDocumentSection],
    pauses: list[LocalizationSourcePause] = (),
) -> LocalizationGenerationChunkManifest:
    """Split exhaustive macro sections while preserving every cue exactly once."""

    if not cues:
        raise ValueError("本土化分块缺少英文字幕。")
    cue_by_id = {cue.cue_id: cue for cue in cues}
    ordered_ids = [cue.cue_id for cue in cues]
    section_ids = [cue_id for section in sections for cue_id in section.source_cue_ids]
    if section_ids != ordered_ids:
        raise ValueError("本土化分块要求全文提纲完整、唯一、按顺序覆盖英文字幕。")

    pause_by_boundary = {
        (pause.left_word_id, pause.right_word_id): pause
        for pause in pauses
    }
    chunks: list[LocalizationGenerationChunk] = []
    for section in sections:
        section_cues = [cue_by_id[cue_id] for cue_id in section.source_cue_ids]
        for start, end in _section_chunk_ranges(
            section_cues,
            pause_by_boundary=pause_by_boundary,
        ):
            selected = section_cues[start:end]
            global_start = ordered_ids.index(selected[0].cue_id)
            global_end = ordered_ids.index(selected[-1].cue_id) + 1
            chunks.append(
                LocalizationGenerationChunk(
                    chunk_id=f"chunk_{len(chunks) + 1:04d}",
                    section_id=section.section_id,
                    source_cue_ids=[cue.cue_id for cue in selected],
                    start_ms=selected[0].start_ms,
                    end_ms=selected[-1].end_ms,
                    source_text="\n".join(cue.text.strip() for cue in selected),
                    readonly_context_before=[
                        cue.text.strip()
                        for cue in cues[
                            max(0, global_start - CONTEXT_CUE_COUNT):global_start
                        ]
                    ],
                    readonly_context_after=[
                        cue.text.strip()
                        for cue in cues[
                            global_end:global_end + CONTEXT_CUE_COUNT
                        ]
                    ],
                )
            )

    flattened = [cue_id for chunk in chunks for cue_id in chunk.source_cue_ids]
    if flattened != ordered_ids:
        raise ValueError("本土化分块没有完整、唯一、按顺序覆盖英文字幕。")
    fingerprint_payload = {
        "contract_version": CHUNK_MANIFEST_VERSION,
        "source_fingerprint": source_fingerprint,
        "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
    }
    return LocalizationGenerationChunkManifest(
        source_fingerprint=source_fingerprint,
        manifest_fingerprint=_fingerprint(fingerprint_payload),
        chunks=chunks,
    )


def _section_chunk_ranges(
    cues: list[LocalizationSourceCue],
    *,
    pause_by_boundary: dict[tuple[str, str], LocalizationSourcePause],
) -> list[tuple[int, int]]:
    if not cues:
        return []
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(cues):
        remaining_chars = _cue_characters(cues[start:])
        if remaining_chars <= MAX_SOURCE_CHARACTERS:
            ranges.append((start, len(cues)))
            break

        candidates: list[tuple[float, int]] = []
        for end in range(start + 1, len(cues)):
            characters = _cue_characters(cues[start:end])
            if characters > MAX_SOURCE_CHARACTERS:
                break
            if characters < MIN_SOURCE_CHARACTERS:
                continue
            candidates.append(
                (
                    _boundary_score(
                        cues,
                        end=end,
                        characters=characters,
                        pause_by_boundary=pause_by_boundary,
                    ),
                    end,
                )
            )
        if not candidates:
            end = min(start + 1, len(cues))
        else:
            _, end = max(candidates, key=lambda item: (item[0], item[1]))
        ranges.append((start, end))
        start = end
    return ranges


def _boundary_score(
    cues: list[LocalizationSourceCue],
    *,
    end: int,
    characters: int,
    pause_by_boundary: dict[tuple[str, str], LocalizationSourcePause],
) -> float:
    left = cues[end - 1]
    right = cues[end]
    distance_penalty = abs(characters - TARGET_SOURCE_CHARACTERS) / 20
    score = -distance_penalty
    pause = pause_by_boundary.get(
        (left.source_word_ids[-1], right.source_word_ids[0])
    )
    if pause is not None:
        score += min(pause.gap_ms, 2_000) / 8
        if pause.confidence in {"medium", "high"}:
            score += 80
    left_speaker = left.speaker_id or left.speaker_cluster_id
    right_speaker = right.speaker_id or right.speaker_cluster_id
    if left_speaker and right_speaker and left_speaker != right_speaker:
        score += 90
    if left.text.rstrip().endswith((".", "?", "!", "。", "？", "！")):
        score += 35
    return score


def _cue_characters(cues: list[LocalizationSourceCue]) -> int:
    return sum(len(cue.text.strip()) for cue in cues)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CHUNK_MANIFEST_VERSION",
    "LocalizationGenerationChunk",
    "LocalizationGenerationChunkManifest",
    "plan_localization_generation_chunks",
]
