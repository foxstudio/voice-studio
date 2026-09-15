"""Build independent spoken-script and display-subtitle tracks.

The spoken track preserves the approved localized wording for dubbing.  The
display track only derives readable cards from the same paragraphs and their
adjudicated semantic windows; it never re-translates the English cue track.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization import quality_gate, subtitle_punctuation
from app.domains.video_localization.localization_alignment_adjudication import (
    LocalizationAlignmentAdjudicationResult,
)
from app.domains.video_localization.localization_spoken_script import (
    LocalizationSpokenScriptFinalResult,
)
from app.domains.video_localization.localization_semantic_alignment import (
    LocalizationAlignmentSourceCue,
    LocalizationAlignmentTargetParagraph,
    LocalizationSemanticAlignmentInput,
    LocalizationSemanticAlignmentPolicy,
    MultilingualTextEncoder,
    align_localized_script,
    build_alignment_target_paragraphs,
)
from app.domains.video_localization.localization_source import (
    LocalizationSourceWord,
)
from app.domains.video_localization.source_boundary_evidence import (
    SourceBoundaryEvidence,
    join_source_words,
)
from app.services import text_normalizer


DUAL_TRACK_VERSION = "localization-dual-tracks-v7"
DEFAULT_STRONG_SOURCE_PAUSE_MS = 1_200


class LocalizationDualTrackPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maximum_display_characters: int = Field(
        # Leave one unit of layout headroom so punctuation never forces a
        # nominal 28-unit card into an unbreakable 15/13 line pair.
        default=quality_gate.LOCALIZED_SUBTITLE_MAX_TOTAL_UNITS - 1,
        ge=12,
        le=42,
    )
    strong_source_pause_ms: int = Field(
        default=DEFAULT_STRONG_SOURCE_PAUSE_MS,
        ge=700,
        le=5_000,
    )


class LocalizationDualTrackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-dual-tracks-input-v5"
    ] = "localization-dual-tracks-input-v5"
    spoken_script_operation_id: str = Field(min_length=1)
    alignment_operation_id: str = Field(min_length=1)
    spoken_script: LocalizationSpokenScriptFinalResult
    alignment: LocalizationAlignmentAdjudicationResult
    source_cues: list[LocalizationAlignmentSourceCue] = Field(
        default_factory=list
    )
    source_words: list[LocalizationSourceWord] = Field(default_factory=list)
    source_boundaries: list[SourceBoundaryEvidence] = Field(
        default_factory=list
    )
    policy: LocalizationDualTrackPolicy = Field(
        default_factory=LocalizationDualTrackPolicy
    )


class LocalizationSpokenTrackSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(pattern=r"^spoken_segment_\d{4}$")
    paragraph_id: str = Field(pattern=r"^paragraph_\d{4}$")
    text: str = Field(min_length=1)
    tts_text: str = Field(min_length=1)
    semantic_start_ms: int = Field(ge=0)
    semantic_end_ms: int = Field(gt=0)
    source_cue_ids: list[str] = Field(min_length=1)
    source_word_ids: list[str] = Field(min_length=1)


class LocalizationDisplaySubtitleCue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cue_id: str = Field(pattern=r"^localized_cue_\d{4}$")
    paragraph_id: str = Field(pattern=r"^paragraph_\d{4}$")
    text: str = Field(min_length=1)
    tts_text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_cue_ids: list[str] = Field(min_length=1)
    source_word_ids: list[str] = Field(min_length=1)
    quality_flags: list[str] = Field(default_factory=list)


class LocalizationDualTrackQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning"]
    spoken_segment_count: int = Field(ge=1)
    display_cue_count: int = Field(ge=1)
    paragraph_coverage_complete: bool
    source_order_preserved: bool
    display_timing_ordered: bool
    overlong_cue_count: int = Field(ge=0)
    manual_review_cue_count: int = Field(default=0, ge=0)
    model_call_count: Literal[0] = 0


class LocalizationDualTrackResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-dual-tracks-v7"
    ] = DUAL_TRACK_VERSION
    spoken_script_fingerprint: str = Field(min_length=64, max_length=64)
    alignment_fingerprint: str = Field(min_length=64, max_length=64)
    result_fingerprint: str = Field(min_length=64, max_length=64)
    spoken_segments: list[LocalizationSpokenTrackSegment] = Field(
        min_length=1,
    )
    display_cues: list[LocalizationDisplaySubtitleCue] = Field(min_length=1)
    quality_summary: LocalizationDualTrackQualitySummary


class LocalizationParagraphWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraph_id: str = Field(pattern=r"^paragraph_\d{4}$")
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_cue_ids: list[str] = Field(min_length=1)
    source_word_ids: list[str] = Field(min_length=1)


def build_localization_dual_tracks(
    request: LocalizationDualTrackInput,
    *,
    encoder: MultilingualTextEncoder | None = None,
) -> LocalizationDualTrackResult:
    if (
        request.alignment.spoken_script_fingerprint
        != request.spoken_script.result_fingerprint
    ):
        raise ValueError("台词与语义时间映射不是同一版本。")
    spoken_segments: list[LocalizationSpokenTrackSegment] = []
    display_cues: list[LocalizationDisplaySubtitleCue] = []
    cue_sequence = 1
    word_by_id = {item.word_id: item for item in request.source_words}
    cue_by_word = {
        word_id: cue.cue_id
        for cue in request.source_cues
        for word_id in cue.source_word_ids
    }
    evidence_by_left_word = {
        item.left_word_id: item for item in request.source_boundaries
    }
    total_units = sum(
        quality_gate.visible_subtitle_units(text)
        for _, text in _script_paragraphs(request.spoken_script)
    )
    total_duration_seconds = max(
        sum(
            item.source_end_ms - item.source_start_ms
            for item in request.alignment.blocks
        )
        / 1_000,
        0.001,
    )
    baseline_density = total_units / total_duration_seconds
    paragraph_windows = build_localization_paragraph_windows(
        request.spoken_script,
        request.alignment,
        word_by_id=word_by_id,
        cue_by_word=cue_by_word,
        evidence_by_left_word=evidence_by_left_word,
        baseline_density=baseline_density,
        encoder=encoder,
    )
    source_speaker_by_cue = {
        item.cue_id: item.speaker_id
        for item in request.source_cues
        if item.speaker_id
    }
    for window in paragraph_windows:
        paragraph_id = window.paragraph_id
        text = window.text
        start_ms = window.start_ms
        end_ms = window.end_ms
        window_speakers = {
            source_speaker_by_cue[cue_id]
            for cue_id in window.source_cue_ids
            if cue_id in source_speaker_by_cue
        }
        if len(window_speakers) > 1:
            raise ValueError(
                "同一中文台词段不能跨说话人；请让上游按人物回合分段后重试。"
            )
        spoken_segments.append(
            LocalizationSpokenTrackSegment(
                segment_id=(
                    f"spoken_segment_{len(spoken_segments) + 1:04d}"
                ),
                paragraph_id=paragraph_id,
                text=text,
                tts_text=text_normalizer.normalize_tts_pronunciation(text),
                semantic_start_ms=start_ms,
                semantic_end_ms=end_ms,
                source_cue_ids=list(window.source_cue_ids),
                source_word_ids=list(window.source_word_ids),
            )
        )
        display_source_text = text
        display_parts = split_localization_display_text(
            display_source_text,
            maximum_characters=(
                request.policy.maximum_display_characters
            ),
            preserve_sentence_boundaries=(
                _window_has_strong_source_pause(
                    window,
                    evidence_by_left_word=evidence_by_left_word,
                    minimum_pause_ms=(
                        request.policy.strong_source_pause_ms
                    ),
                )
            ),
        )
        display_parts = _coalesce_display_parts_to_source_capacity(
            display_parts,
            source_word_capacity=sum(
                word_id in word_by_id
                for word_id in window.source_word_ids
            ),
        )
        display_windows = _plan_display_windows(
            display_parts,
            window=window,
            word_by_id=word_by_id,
            cue_by_word=cue_by_word,
            evidence_by_left_word=evidence_by_left_word,
            baseline_density=baseline_density,
            encoder=encoder,
        )
        for (
            display_text,
            cue_start,
            cue_end,
            display_source_cue_ids,
            display_source_word_ids,
        ) in display_windows:
            tts_text = _display_tts_text(display_text)
            display_text = (
                subtitle_punctuation
                .normalize_display_subtitle_punctuation(display_text)
            )
            formatted_display_text = format_localization_display_lines(
                display_text
            )
            flags = _display_quality_flags(
                formatted_display_text,
                request.policy,
            )
            if source_word_ids_span_strong_pause(
                display_source_word_ids,
                evidence_by_left_word=evidence_by_left_word,
                minimum_pause_ms=(
                    request.policy.strong_source_pause_ms
                ),
            ):
                flags.append(
                    "display_strong_pause_review_required"
                )
            display_cues.append(
                LocalizationDisplaySubtitleCue(
                    cue_id=f"localized_cue_{cue_sequence:04d}",
                    paragraph_id=paragraph_id,
                    text=formatted_display_text,
                    tts_text=tts_text,
                    start_ms=cue_start,
                    end_ms=cue_end,
                    source_cue_ids=display_source_cue_ids,
                    source_word_ids=display_source_word_ids,
                    quality_flags=flags,
                )
            )
            cue_sequence += 1
    _validate_timing(display_cues)
    overlong = sum(
        "display_text_overlong" in item.quality_flags
        for item in display_cues
    )
    manual_review = sum(
        "display_strong_pause_review_required"
        in item.quality_flags
        for item in display_cues
    )
    payload = {
        "spoken_script": request.spoken_script.result_fingerprint,
        "alignment": request.alignment.result_fingerprint,
        "policy": request.policy.model_dump(mode="json"),
        "spoken_segments": [
            item.model_dump(mode="json") for item in spoken_segments
        ],
        "display_cues": [
            item.model_dump(mode="json") for item in display_cues
        ],
    }
    return LocalizationDualTrackResult(
        spoken_script_fingerprint=request.spoken_script.result_fingerprint,
        alignment_fingerprint=request.alignment.result_fingerprint,
        result_fingerprint=_fingerprint(payload),
        spoken_segments=spoken_segments,
        display_cues=display_cues,
        quality_summary=LocalizationDualTrackQualitySummary(
            status=(
                "warning"
                if overlong or manual_review
                else "passed"
            ),
            spoken_segment_count=len(spoken_segments),
            display_cue_count=len(display_cues),
            paragraph_coverage_complete=True,
            source_order_preserved=True,
            display_timing_ordered=True,
            overlong_cue_count=overlong,
            manual_review_cue_count=manual_review,
        ),
    )


def project_localization_dual_tracks_result(
    result: LocalizationDualTrackResult,
) -> dict:
    quality = result.quality_summary
    return {
        "label": "生成台词轨与上屏字幕",
        "order": 110,
        "status": (
            "warning"
            if quality.status == "warning"
            else "success"
        ),
        "purpose": (
            "完整中文段落用于配音；上屏字幕只在同一语义时间窗内按中文阅读习惯拆分。"
        ),
        "summary": (
            f"已生成 {quality.spoken_segment_count} 段中文台词和 "
            f"{quality.display_cue_count} 条上屏字幕。"
        ),
        "metrics": [
            {
                "label": "中文台词",
                "value": str(quality.spoken_segment_count),
            },
            {
                "label": "上屏字幕",
                "value": str(quality.display_cue_count),
            },
            {
                "label": "过长",
                "value": str(quality.overlong_cue_count),
            },
            {
                "label": "需人工核对",
                "value": str(quality.manual_review_cue_count),
            },
        ],
        "sections": [],
        "notes": ["本步骤不翻译，也不调用模型。"],
    }


def split_localization_display_text(
    text: str,
    *,
    maximum_characters: int,
    preserve_sentence_boundaries: bool = False,
) -> list[str]:
    source_lines = [
        " ".join(line.split()).strip()
        for line in text.splitlines()
        if line.strip()
    ]
    if not source_lines:
        return []
    return [
        card
        for line in source_lines
        for card in _split_localization_display_line(
            line,
            maximum_characters=maximum_characters,
            preserve_sentence_boundaries=(
                preserve_sentence_boundaries
            ),
        )
    ]


def _split_localization_display_line(
    normalized: str,
    *,
    maximum_characters: int,
    preserve_sentence_boundaries: bool = False,
) -> list[str]:
    """Split one dialogue turn without merging it with another speaker."""

    sentence_parts = [
        item.strip()
        for item in re.findall(
            r"[^。！？!?；;]+[。！？!?；;]?[”’」』）》】\]]*",
            normalized,
        )
        if item.strip()
    ]
    clauses = []
    for sentence in sentence_parts:
        if _display_size(sentence) <= maximum_characters:
            clauses.append(sentence)
            continue
        pieces = [
            item.strip()
            for item in re.findall(
                r"[^，,：:、]+[，,：:、]?",
                sentence,
            )
            if item.strip()
        ]
        for piece in pieces or [sentence]:
            clauses.extend(
                _split_oversized_display_clause(
                    piece,
                    maximum_characters=maximum_characters,
                )
            )
    cards: list[str] = []
    current = ""
    for clause in clauses:
        candidate = (
            _join_display_parts(current, clause)
            if current
            else clause
        )
        keep_sentence_boundary = bool(
            current
            and re.search(r"[。！？!?]$", current)
            and (
                preserve_sentence_boundaries
                or _display_size(current) >= maximum_characters * 0.55
                or _display_size(clause) <= maximum_characters * 0.35
                or re.search(r"[！？!?]$", clause)
            )
        )
        if current and (
            _display_size(candidate) > maximum_characters
            or keep_sentence_boundary
        ):
            cards.append(current)
            current = clause
        else:
            current = candidate
    if current:
        cards.append(current)
    if preserve_sentence_boundaries:
        return cards or [normalized]
    return _merge_orphan_display_cards(
        cards or [normalized],
        maximum_characters=maximum_characters,
    )


def _window_has_strong_source_pause(
    window: LocalizationParagraphWindow,
    *,
    evidence_by_left_word: dict[str, SourceBoundaryEvidence],
    minimum_pause_ms: int,
) -> bool:
    """Keep target sentences separate only for objective source silence."""

    return source_word_ids_span_strong_pause(
        window.source_word_ids,
        evidence_by_left_word=evidence_by_left_word,
        minimum_pause_ms=minimum_pause_ms,
    )


def source_word_ids_span_strong_pause(
    source_word_ids: list[str],
    *,
    evidence_by_left_word: dict[str, SourceBoundaryEvidence],
    minimum_pause_ms: int = DEFAULT_STRONG_SOURCE_PAUSE_MS,
) -> bool:
    """Return only objective pauses fully owned by one display cue."""

    return any(
        evidence is not None
        and evidence.right_word_id == right_word_id
        and evidence.objective_support
        and evidence.pause_ms >= minimum_pause_ms
        for left_word_id, right_word_id in zip(
            source_word_ids,
            source_word_ids[1:],
        )
        if (
            evidence := evidence_by_left_word.get(left_word_id)
        ) is not None
    )


def _display_tts_text(text: str) -> str:
    return text_normalizer.normalize_tts_pronunciation(
        re.sub(r'["“”「」『』]', "", text)
    )


def _script_paragraphs(
    script: LocalizationSpokenScriptFinalResult,
) -> list[tuple[str, str]]:
    return [
        (item.paragraph_id, item.text)
        for item in build_alignment_target_paragraphs(
            [
                paragraph
                for section in script.content.sections
                for paragraph in section.paragraphs
            ]
        )
    ]


def build_localization_paragraph_windows(
    script: LocalizationSpokenScriptFinalResult,
    alignment: LocalizationAlignmentAdjudicationResult,
    *,
    word_by_id: dict[str, LocalizationSourceWord] | None = None,
    cue_by_word: dict[str, str] | None = None,
    evidence_by_left_word: dict[str, SourceBoundaryEvidence] | None = None,
    baseline_density: float = 1.0,
    encoder: MultilingualTextEncoder | None = None,
) -> list[LocalizationParagraphWindow]:
    word_by_id = word_by_id or {}
    cue_by_word = cue_by_word or {}
    evidence_by_left_word = evidence_by_left_word or {}
    paragraphs = _script_paragraphs(script)
    paragraph_by_id = {item[0]: item[1] for item in paragraphs}
    aligned_ids = [
        paragraph_id
        for block in alignment.blocks
        for paragraph_id in block.paragraph_ids
    ]
    if aligned_ids != [item[0] for item in paragraphs]:
        raise ValueError("语义时间映射没有完整覆盖中文台词段落。")
    windows = []
    for block in alignment.blocks:
        block_paragraphs = [
            (paragraph_id, paragraph_by_id[paragraph_id])
            for paragraph_id in block.paragraph_ids
        ]
        block_source_words = [
            word_by_id[word_id]
            for word_id in block.source_word_ids
            if word_id in word_by_id
        ]
        can_partition_by_source_words = (
            len(block_paragraphs) > 1
            and encoder is not None
            and len(block_source_words) >= len(block_paragraphs)
            and len(block_source_words) == len(block.source_word_ids)
        )
        if can_partition_by_source_words:
            planned = _plan_display_windows(
                [text for _, text in block_paragraphs],
                window=LocalizationParagraphWindow(
                    paragraph_id=block_paragraphs[0][0],
                    text="".join(text for _, text in block_paragraphs),
                    start_ms=block.source_start_ms,
                    end_ms=block.source_end_ms,
                    source_cue_ids=list(block.source_cue_ids),
                    source_word_ids=list(block.source_word_ids),
                ),
                word_by_id=word_by_id,
                cue_by_word=cue_by_word,
                evidence_by_left_word=evidence_by_left_word,
                baseline_density=baseline_density,
                encoder=encoder,
                apply_display_entry=False,
            )
            windows.extend(
                LocalizationParagraphWindow(
                    paragraph_id=paragraph_id,
                    text=text,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    source_cue_ids=source_cue_ids,
                    source_word_ids=source_word_ids,
                )
                for (
                    paragraph_id,
                    text,
                ), (
                    _planned_text,
                    start_ms,
                    end_ms,
                    source_cue_ids,
                    source_word_ids,
                ) in zip(block_paragraphs, planned)
            )
            continue
        timings = _allocate_windows(
            [text for _, text in block_paragraphs],
            start_ms=block.source_start_ms,
            end_ms=block.source_end_ms,
        )
        windows.extend(
            LocalizationParagraphWindow(
                paragraph_id=paragraph_id,
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                source_cue_ids=list(block.source_cue_ids),
                source_word_ids=list(block.source_word_ids),
            )
            for (paragraph_id, text), (start_ms, end_ms) in zip(
                block_paragraphs,
                timings,
            )
        )
    return windows


def _allocate_windows(
    texts: list[str],
    *,
    start_ms: int,
    end_ms: int,
    minimum_duration_ms: int = 1,
    weight_provider: Callable[[str], float] | None = None,
) -> list[tuple[int, int]]:
    if not texts or end_ms <= start_ms:
        raise ValueError("语义时间窗无效。")
    resolve_weight = weight_provider or _display_size
    weights = [max(resolve_weight(item), 1) for item in texts]
    total = sum(weights)
    duration = end_ms - start_ms
    boundaries = [start_ms]
    accumulated = 0
    for index, weight in enumerate(weights[:-1], start=1):
        accumulated += weight
        ideal = start_ms + round(duration * accumulated / total)
        minimum = boundaries[-1] + minimum_duration_ms
        remaining_count = len(weights) - index
        maximum = end_ms - remaining_count * minimum_duration_ms
        boundaries.append(
            min(maximum, max(minimum, ideal))
            if minimum <= maximum
            else ideal
        )
    boundaries.append(end_ms)
    return [
        (left, max(left + 1, right))
        for left, right in zip(boundaries, boundaries[1:])
    ]


def _plan_display_windows(
    texts: list[str],
    *,
    window: LocalizationParagraphWindow,
    word_by_id: dict[str, LocalizationSourceWord],
    cue_by_word: dict[str, str],
    evidence_by_left_word: dict[str, SourceBoundaryEvidence],
    baseline_density: float,
    encoder: MultilingualTextEncoder | None,
    apply_display_entry: bool = True,
) -> list[tuple[str, int, int, list[str], list[str]]]:
    """Map readable target-language cards to source-word spans.

    Semantic similarity owns the split.  Pauses and source punctuation are
    supporting evidence.  Relative target-language density is a deliberately
    weak tie-breaker, so an unusual but correct localization is not forced into
    an artificial speed template.
    """

    source_words = [
        word_by_id[word_id]
        for word_id in window.source_word_ids
        if word_id in word_by_id
    ]
    if len(texts) == 1:
        display_start_ms = window.start_ms
        if apply_display_entry and source_words:
            display_start_ms = max(
                window.start_ms,
                _display_entry_ms(source_words[0]),
            )
        return [
            (
                texts[0],
                display_start_ms,
                window.end_ms,
                list(window.source_cue_ids),
                list(window.source_word_ids),
            )
        ]
    if (
        encoder is None
        or len(source_words) < len(texts)
        or len(source_words) != len(window.source_word_ids)
    ):
        timings = _allocate_windows(
            texts,
            start_ms=window.start_ms,
            end_ms=window.end_ms,
            weight_provider=lambda _text: 1,
        )
        return [
            (
                text,
                start_ms,
                end_ms,
                list(window.source_cue_ids),
                list(window.source_word_ids),
            )
            for text, (start_ms, end_ms) in zip(texts, timings)
        ]

    selected_positions = _select_joint_display_positions(
        texts,
        source_words=source_words,
        cue_by_word=cue_by_word,
        evidence_by_left_word=evidence_by_left_word,
        encoder=encoder,
    )

    boundaries = [0, *selected_positions, len(source_words)]
    planned = []
    for text, left, right in zip(texts, boundaries, boundaries[1:]):
        part_words = source_words[left:right]
        part_word_ids = [item.word_id for item in part_words]
        part_cue_ids = list(
            dict.fromkeys(
                cue_by_word[word_id]
                for word_id in part_word_ids
                if word_id in cue_by_word
            )
        )
        if not part_cue_ids:
            part_cue_ids = list(window.source_cue_ids)
        planned.append(
            (
                text,
                (
                    _display_entry_ms(part_words[0])
                    if apply_display_entry
                    else part_words[0].start_ms
                ),
                part_words[-1].end_ms,
                part_cue_ids,
                part_word_ids,
            )
        )
    return planned


def _select_joint_display_positions(
    texts: list[str],
    *,
    source_words: list[LocalizationSourceWord],
    cue_by_word: dict[str, str],
    evidence_by_left_word: dict[str, SourceBoundaryEvidence],
    encoder: MultilingualTextEncoder,
) -> list[int]:
    """Choose one complete local path instead of greedy card-by-card splits."""

    if len(texts) < 2 or len(source_words) < len(texts):
        return []
    word_index = {
        item.word_id: index for index, item in enumerate(source_words)
    }
    grouped_word_ids: list[list[str]] = []
    grouped_cue_ids: list[str] = []
    for word in source_words:
        cue_id = cue_by_word.get(word.word_id)
        if not cue_id:
            cue_id = f"local_word_{len(grouped_word_ids) + 1:04d}"
        if not grouped_cue_ids or grouped_cue_ids[-1] != cue_id:
            grouped_cue_ids.append(cue_id)
            grouped_word_ids.append([])
        grouped_word_ids[-1].append(word.word_id)

    # A very short source cue may contain several independently readable
    # target cards.  In that case words are the only truthful local units.
    if len(grouped_word_ids) < len(texts):
        grouped_cue_ids = [
            f"local_word_{index:04d}"
            for index in range(1, len(source_words) + 1)
        ]
        grouped_word_ids = [[item.word_id] for item in source_words]

    local_cues = []
    for cue_id, word_ids in zip(grouped_cue_ids, grouped_word_ids):
        items = [source_words[word_index[word_id]] for word_id in word_ids]
        local_cues.append(
            LocalizationAlignmentSourceCue(
                cue_id=cue_id,
                text=join_source_words([item.text for item in items]),
                start_ms=items[0].start_ms,
                end_ms=items[-1].end_ms,
                source_word_ids=word_ids,
            )
        )

    local_boundaries = []
    for position, (left, right) in enumerate(
        zip(source_words, source_words[1:]),
        start=1,
    ):
        evidence = evidence_by_left_word.get(left.word_id)
        if evidence is not None:
            local_boundaries.append(
                evidence.model_copy(update={"position_after_word": position})
            )
            continue
        left_cue_id = cue_by_word.get(left.word_id) or grouped_cue_ids[0]
        right_cue_id = cue_by_word.get(right.word_id) or left_cue_id
        local_boundaries.append(
            SourceBoundaryEvidence(
                boundary_id=f"{left.word_id}:{right.word_id}",
                left_word_id=left.word_id,
                right_word_id=right.word_id,
                left_cue_id=left_cue_id,
                right_cue_id=right_cue_id,
                position_after_word=position,
                cue_boundary=left_cue_id != right_cue_id,
                eligible=False,
                punctuation="none",
                pause_ms=max(0, right.start_ms - left.end_ms),
                pause_confidence="none",
                segment_change=left.segment_id != right.segment_id,
                speaker_change=False,
                overlap_speech=(
                    left.has_speaker_overlap or right.has_speaker_overlap
                ),
                objective_support=False,
                classification="internal_cue",
                score=0,
                reason_codes=[],
            )
        )
    result = align_localized_script(
        LocalizationSemanticAlignmentInput(
            source_fingerprint="0" * 64,
            spoken_script_fingerprint="0" * 64,
            source_cues=local_cues,
            source_words=source_words,
            source_boundaries=local_boundaries,
            target_paragraphs=[
                LocalizationAlignmentTargetParagraph(
                    paragraph_id=f"paragraph_{index:04d}",
                    text=text,
                )
                for index, text in enumerate(texts, start=1)
            ],
            policy=LocalizationSemanticAlignmentPolicy(
                insertion_cost=1_000.0,
                deletion_cost=1_000.0,
            ),
        ),
        encoder=encoder,
    )
    expected_ids = [
        f"paragraph_{index:04d}"
        for index in range(1, len(texts) + 1)
    ]
    actual_ids = [
        paragraph_id
        for block in result.blocks
        for paragraph_id in block.paragraph_ids
    ]
    if actual_ids != expected_ids or len(result.blocks) != len(texts):
        raise ValueError("上屏字幕联合对齐没有为每张卡片保留唯一语义窗口。")
    selected_positions = [
        word_index[block.source_word_ids[-1]] + 1
        for block in result.blocks[:-1]
    ]
    if any(
        left >= right
        for left, right in zip(
            [0, *selected_positions],
            [*selected_positions, len(source_words)],
        )
    ):
        raise ValueError("上屏字幕联合对齐产生了空窗口或倒序边界。")
    return selected_positions


def _display_entry_ms(word: LocalizationSourceWord) -> int:
    candidate = word.display_entry_ms
    if candidate is None or not word.start_ms <= candidate < word.end_ms:
        return word.start_ms
    return candidate


def _split_oversized_display_clause(
    text: str,
    *,
    maximum_characters: int,
) -> list[str]:
    if _display_size(text) <= maximum_characters:
        return [text]
    tokens = _display_token_groups(re.findall(r"\S+", text))
    if len(tokens) > 1:
        total_size = sum(_display_size(item) for item in tokens)
        balanced_candidates = []
        if total_size <= maximum_characters * 2:
            for index in range(1, len(tokens)):
                left = " ".join(tokens[:index])
                right = " ".join(tokens[index:])
                left_size = _display_size(left)
                right_size = _display_size(right)
                if (
                    left_size <= maximum_characters
                    and right_size <= maximum_characters
                ):
                    balanced_candidates.append(
                        (abs(left_size - right_size), left, right)
                    )
        if balanced_candidates:
            _, left, right = min(balanced_candidates)
            return [f"{left} ", right]
        parts = []
        current_tokens: list[str] = []
        for token in tokens:
            candidate = " ".join([*current_tokens, token])
            if (
                current_tokens
                and _display_size(candidate) > maximum_characters
            ):
                parts.append(f"{' '.join(current_tokens)} ")
                current_tokens = [token]
            else:
                current_tokens.append(token)
        if current_tokens:
            parts.append(" ".join(current_tokens))
        if all(
            _display_size(item) <= maximum_characters for item in parts
        ):
            return parts
    compact = text.strip()
    punctuation_candidates = []
    for match in re.finditer(r"(?:——|—|…{2}|）|】|》|」|』)", compact):
        index = match.end()
        left = compact[:index].strip()
        right = compact[index:].strip()
        if not left or not right:
            continue
        left_size = _display_size(left)
        right_size = _display_size(right)
        if (
            left_size <= maximum_characters
            and right_size <= maximum_characters
        ):
            punctuation_candidates.append(
                (abs(left_size - right_size), left, right)
            )
    if punctuation_candidates:
        _balance, left, right = min(punctuation_candidates)
        return [left, right]
    # Chinese has no reliable programmatic word boundary.  Keeping one
    # overlong clause lets the quality gate or the upstream display adapter
    # request a semantic rewrite; slicing every N characters can split complete
    # words such as “镜头” and is never an acceptable fallback.
    return [compact]


def _merge_orphan_display_cards(
    cards: list[str],
    *,
    maximum_characters: int,
) -> list[str]:
    """Attach tiny connective fragments without swallowing real reactions."""

    merged: list[str] = []
    index = 0
    hard_limit = max(
        maximum_characters,
        quality_gate.LOCALIZED_SUBTITLE_MAX_TOTAL_UNITS,
    )
    while index < len(cards):
        current = cards[index]
        if (
            index + 1 < len(cards)
            and _display_size(current) <= 5
            and re.search(r"[，,：:、]$", current)
        ):
            candidate = _join_display_parts(current, cards[index + 1])
            if _display_size(candidate) <= hard_limit:
                merged.append(candidate)
                index += 2
                continue
        if (
            merged
            and _display_size(current) <= 3
            and not re.search(r"[！？!?]$", current)
        ):
            candidate = _join_display_parts(merged[-1], current)
            if _display_size(candidate) <= hard_limit:
                merged[-1] = candidate
                index += 1
                continue
        merged.append(current)
        index += 1
    return merged


def _join_display_parts(left: str, right: str) -> str:
    if not left or not right:
        return f"{left}{right}"
    needs_space = (
        left[-1].isascii()
        and left[-1].isalnum()
        or right[0].isascii()
        and right[0].isalnum()
    )
    return f"{left}{' ' if needs_space else ''}{right}"


def _coalesce_display_parts_to_source_capacity(
    parts: list[str],
    *,
    source_word_capacity: int,
) -> list[str]:
    """Keep every display card backed by a unique source-word span."""

    if source_word_capacity <= 0 or len(parts) <= source_word_capacity:
        return list(parts)
    merged = list(parts)
    while len(merged) > source_word_capacity:
        index = min(
            range(len(merged) - 1),
            key=lambda item: (
                _display_size(
                    _join_display_parts(merged[item], merged[item + 1])
                ),
                item,
            ),
        )
        merged[index : index + 2] = [
            _join_display_parts(merged[index], merged[index + 1])
        ]
    return merged


def format_localization_display_lines(text: str) -> str:
    """Balance one display card into at most two release-safe lines."""

    maximum = quality_gate.LOCALIZED_SUBTITLE_MAX_LINE_UNITS
    if quality_gate.visible_subtitle_units(text) <= maximum:
        return text
    opening = "（([【“‘《〈「『"
    closing = "，。！？；：、,.!?;:）)]】”’》〉」』"
    candidates = []
    for index in range(1, len(text)):
        left = text[:index].rstrip()
        right = text[index:].lstrip()
        if not left or not right:
            continue
        if left[-1] in opening or right[0] in closing:
            continue
        if (
            left[-1].isascii()
            and right[0].isascii()
            and left[-1] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.+-"
            and right[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.+-"
        ):
            continue
        left_units = quality_gate.visible_subtitle_units(left)
        right_units = quality_gate.visible_subtitle_units(right)
        if left_units > maximum or right_units > maximum:
            continue
        semantic_break_priority = (
            0
            if left[-1] in "，。！？；：、,.!?;:"
            else 1
            if text[index - 1 : index].isspace()
            or text[index : index + 1].isspace()
            else 2
        )
        candidates.append(
            (
                semantic_break_priority,
                abs(left_units - right_units),
                max(left_units, right_units),
                index,
                left,
                right,
            )
        )
    if not candidates:
        return text
    _priority, _balance, _maximum, _index, left, right = min(candidates)
    return f"{left}\n{right}"


def _display_token_groups(tokens: list[str]) -> list[str]:
    groups: list[str] = []
    previous_was_ascii = False
    for token in tokens:
        is_ascii_token = bool(
            re.fullmatch(
                r"[A-Za-z0-9_.+\-]+[，,：:。！？!?；;]?",
                token,
            )
        )
        if groups and previous_was_ascii and is_ascii_token:
            groups[-1] = f"{groups[-1]} {token}"
        else:
            groups.append(token)
        previous_was_ascii = is_ascii_token
    return groups


def _display_quality_flags(
    text: str,
    policy: LocalizationDualTrackPolicy,
) -> list[str]:
    flags = []
    size = quality_gate.visible_subtitle_units(text)
    if size > policy.maximum_display_characters:
        flags.append("display_text_overlong")
    return flags


def _validate_timing(
    cues: list[LocalizationDisplaySubtitleCue],
) -> None:
    if any(
        right.start_ms < left.end_ms
        for left, right in zip(cues, cues[1:])
    ):
        raise ValueError("中文字幕时间出现重叠。")


def _display_size(text: str) -> float:
    """Measure cards with the same release-width units as the quality gate."""

    return quality_gate.visible_subtitle_units(text)


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
    "LocalizationDualTrackInput",
    "LocalizationDualTrackResult",
    "LocalizationParagraphWindow",
    "build_localization_dual_tracks",
    "build_localization_paragraph_windows",
    "format_localization_display_lines",
    "project_localization_dual_tracks_result",
    "split_localization_display_text",
]
