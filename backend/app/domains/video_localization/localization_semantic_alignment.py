"""Cross-lingual semantic block alignment for localized spoken scripts.

The localized document is intentionally written without source-cue IDs.  This
module maps its independently written Chinese semantic paragraphs back to the
immutable ASR cue/word timeline.  The path search follows the core design used
by Vecalign and SentAlign: multilingual sentence embeddings score candidate
blocks, while a deterministic monotonic dynamic program owns coverage and
ordering.  A language model may later adjudicate bounded ambiguous regions,
but it never owns the full alignment path.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.video_localization.localization_source import (
    LocalizationSourceWord,
)
from app.domains.video_localization.source_boundary_evidence import (
    SourceBoundaryEvidence,
    join_source_words,
)

ALIGNER_VERSION = "localization-semantic-alignment-v14"
MODEL_ID = "sentence-transformers/LaBSE"


class LocalizationAlignmentSourceCue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cue_id: str = Field(min_length=1)
    speaker_id: str | None = None
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_word_ids: list[str] = Field(min_length=1)


class LocalizationAlignmentTargetParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraph_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class LocalizationSemanticAlignmentAnchor(BaseModel):
    """Verified target/source ownership that semantic similarity may not undo."""

    model_config = ConfigDict(extra="forbid")

    anchor_id: str = Field(min_length=1)
    target_paragraph_id: str = Field(min_length=1)
    source_cue_ids: list[str] = Field(min_length=1)


class LocalizationSemanticAlignmentEvidenceHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_id: str = Field(min_length=1)
    target_text_zh: str = Field(min_length=1, max_length=500)
    source_cue_ids: list[str] = Field(min_length=1)


def build_alignment_evidence_anchors(
    hints: list[LocalizationSemanticAlignmentEvidenceHint],
    target_paragraphs: list[LocalizationAlignmentTargetParagraph],
    source_cues: list[LocalizationAlignmentSourceCue],
) -> list[LocalizationSemanticAlignmentAnchor]:
    """Resolve and canonicalize exact evidence targets for monotonic alignment."""

    resolved = []
    paragraph_order = {
        item.paragraph_id: index
        for index, item in enumerate(target_paragraphs)
    }
    source_order = {
        item.cue_id: index
        for index, item in enumerate(source_cues)
    }
    for hint in hints:
        needle = _normalize_anchor_text(hint.target_text_zh)
        matches = [
            item.paragraph_id
            for item in target_paragraphs
            if needle and needle in _normalize_anchor_text(item.text)
        ]
        if len(matches) != 1:
            continue
        ordered_source_cue_ids = sorted(
            {
                cue_id
                for cue_id in hint.source_cue_ids
                if cue_id in source_order
            },
            key=source_order.__getitem__,
        )
        if not ordered_source_cue_ids:
            continue
        first_source_index = source_order[ordered_source_cue_ids[0]]
        last_source_index = source_order[ordered_source_cue_ids[-1]]
        if not source_window_is_single_speaker(
            source_cues[first_source_index : last_source_index + 1]
        ):
            continue
        resolved.append(
            LocalizationSemanticAlignmentAnchor(
                anchor_id=hint.anchor_id,
                target_paragraph_id=matches[0],
                source_cue_ids=ordered_source_cue_ids,
            )
        )
    return sorted(
        resolved,
        key=lambda item: paragraph_order[item.target_paragraph_id],
    )


def _normalize_anchor_text(value: str) -> str:
    without_label = re.sub(
        r"^\s*[（(\[][\w\u3400-\u9fff\s-]{1,16}[）)\]]\s*",
        "",
        value,
        count=1,
    )
    return re.sub(r"[\W_]+", "", without_label.casefold())


def build_alignment_target_paragraphs(
    paragraphs: list[str],
) -> list[LocalizationAlignmentTargetParagraph]:
    """Keep a short lead-in and the content it introduces in one time unit."""

    normalized_paragraphs = [
        part.strip()
        for paragraph in paragraphs
        for part in re.split(r"\n\s*\n+", paragraph.strip())
        if part.strip()
    ]
    units: list[str] = []
    index = 0
    while index < len(normalized_paragraphs):
        text = normalized_paragraphs[index]
        if (
            text.endswith(("：", ":"))
            and index + 1 < len(normalized_paragraphs)
        ):
            text = f"{text} {normalized_paragraphs[index + 1]}"
            index += 2
            while (
                index + 1 < len(normalized_paragraphs)
                and normalized_paragraphs[index].endswith(("：", ":"))
            ):
                text = (
                    f"{text} {normalized_paragraphs[index]} "
                    f"{normalized_paragraphs[index + 1]}"
                )
                index += 2
            units.append(text)
            continue
        units.extend(_standalone_dialogue_turns(text))
        index += 1
    return [
        LocalizationAlignmentTargetParagraph(
            paragraph_id=f"paragraph_{index:04d}",
            text=text,
        )
        for index, text in enumerate(units, start=1)
    ]


def _standalone_dialogue_turns(text: str) -> list[str]:
    """Split a paragraph made only of quoted turns into independent units.

    A screenplay excerpt may contain several short utterances in one Markdown
    paragraph even though the source audio places them at distinct times.  The
    semantic aligner needs one target unit per turn so an otherwise unrelated
    following paragraph cannot absorb a late source utterance.  Mixed prose
    with quoted examples remains intact.
    """

    turns = re.findall(r"“[^”\n]+”", text)
    if len(turns) >= 2:
        remainder = re.sub(r"“[^”\n]+”", "", text)
        if not remainder.strip():
            return turns

    sentence_turns = [
        item.strip()
        for item in re.findall(
            r"[^。！？!?]+[。！？!?]+[”’」』）》】]*|[^。！？!?]+$",
            text,
        )
        if item.strip()
    ]
    short_turn_count = sum(
        len(re.sub(r"[\W_]+", "", item)) <= 16
        for item in sentence_turns
    )
    if (
        len(sentence_turns) >= 3
        and short_turn_count >= 2
        and short_turn_count / len(sentence_turns) >= 0.6
    ):
        return sentence_turns
    return [text]


class LocalizationSemanticAlignmentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aligner_version: Literal[
        "localization-semantic-alignment-v14"
    ] = ALIGNER_VERSION
    embedding_model_id: Literal["sentence-transformers/LaBSE"] = MODEL_ID
    maximum_source_group: int = Field(default=128, ge=1, le=128)
    insertion_cost: float = Field(default=0.85, gt=0)
    deletion_cost: float = Field(default=0.85, gt=0)
    low_similarity_threshold: float = Field(default=0.30, ge=-1, le=1)
    broad_source_group_threshold: int = Field(default=8, ge=2, le=24)
    word_boundary_search_radius: int = Field(default=18, ge=4, le=48)
    word_boundary_minimum_gain: float = Field(default=0.025, ge=0, le=0.2)
    supported_boundary_minimum_gain: float = Field(
        default=0.005,
        ge=0,
        le=0.1,
    )
    density_tiebreak_weight: float = Field(default=0.012, ge=0, le=0.05)
    boundary_edge_word_window: int = Field(default=12, ge=4, le=24)
    boundary_edge_adjudication_minimum_gain: float = Field(
        default=0.08,
        ge=0,
        le=0.5,
    )


class LocalizationSemanticAlignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-semantic-alignment-input-v2"
    ] = "localization-semantic-alignment-input-v2"
    source_fingerprint: str = Field(min_length=64, max_length=64)
    spoken_script_fingerprint: str = Field(min_length=64, max_length=64)
    source_cues: list[LocalizationAlignmentSourceCue] = Field(min_length=1)
    source_words: list[LocalizationSourceWord] = Field(default_factory=list)
    source_boundaries: list[SourceBoundaryEvidence] = Field(
        default_factory=list
    )
    target_paragraphs: list[
        LocalizationAlignmentTargetParagraph
    ] = Field(min_length=1)
    evidence_anchors: list[LocalizationSemanticAlignmentAnchor] = Field(
        default_factory=list,
    )
    policy: LocalizationSemanticAlignmentPolicy = Field(
        default_factory=LocalizationSemanticAlignmentPolicy
    )

    @model_validator(mode="after")
    def validate_ids(self) -> "LocalizationSemanticAlignmentInput":
        cue_ids = [item.cue_id for item in self.source_cues]
        paragraph_ids = [
            item.paragraph_id for item in self.target_paragraphs
        ]
        if len(cue_ids) != len(set(cue_ids)):
            raise ValueError("英文源字幕 ID 不唯一。")
        if len(paragraph_ids) != len(set(paragraph_ids)):
            raise ValueError("中文语义段 ID 不唯一。")
        anchor_ids = [item.anchor_id for item in self.evidence_anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("已确认语义锚点 ID 不唯一。")
        cue_index = {cue_id: index for index, cue_id in enumerate(cue_ids)}
        paragraph_index = {
            paragraph_id: index
            for index, paragraph_id in enumerate(paragraph_ids)
        }
        anchor_ranges = []
        for anchor in self.evidence_anchors:
            if anchor.target_paragraph_id not in paragraph_index:
                raise ValueError("已确认语义锚点引用了不存在的中文段落。")
            try:
                indexes = [cue_index[item] for item in anchor.source_cue_ids]
            except KeyError as exc:
                raise ValueError(
                    "已确认语义锚点引用了不存在的英文字幕。"
                ) from exc
            if indexes != list(range(indexes[0], indexes[-1] + 1)):
                raise ValueError("已确认语义锚点的英文字幕必须连续且有序。")
            if len(indexes) > self.policy.maximum_source_group:
                raise ValueError("已确认语义锚点超过单段允许的英文范围。")
            anchor_ranges.append(
                (
                    paragraph_index[anchor.target_paragraph_id],
                    indexes[0],
                    indexes[-1],
                )
            )
        for left, right in zip(anchor_ranges, anchor_ranges[1:]):
            if left[0] > right[0] or (
                left[0] < right[0] and left[2] >= right[1]
            ):
                raise ValueError("已确认语义锚点的中英文顺序发生冲突。")
        if any(
            right.start_ms < left.start_ms
            for left, right in zip(
                self.source_cues,
                self.source_cues[1:],
            )
        ):
            raise ValueError("英文源字幕时间顺序无效。")
        if self.source_words:
            expected_word_ids = [
                word_id
                for cue in self.source_cues
                for word_id in cue.source_word_ids
            ]
            actual_word_ids = [item.word_id for item in self.source_words]
            if actual_word_ids != expected_word_ids:
                raise ValueError("英文逐词时间没有完整且按字幕顺序覆盖。")
            if self.source_boundaries and [
                item.position_after_word for item in self.source_boundaries
            ] != list(range(1, len(self.source_words))):
                raise ValueError("英文逐词边界证据不完整或顺序无效。")
        return self


AlignmentConfidence = Literal["high", "medium", "low"]
AlignmentBlockKind = Literal[
    "semantic_match",
    "ambiguous_semantic_group",
]


class LocalizationSemanticAlignmentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=1)
    kind: AlignmentBlockKind
    paragraph_ids: list[str] = Field(min_length=1)
    source_cue_ids: list[str] = Field(min_length=1)
    source_word_ids: list[str] = Field(min_length=1)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    similarity: float = Field(ge=-1, le=1)
    confidence: AlignmentConfidence
    needs_adjudication: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    evidence_anchor_ids: list[str] = Field(default_factory=list)


class LocalizationSemanticAlignmentQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning"]
    block_count: int = Field(ge=1)
    paragraph_count: int = Field(ge=1)
    source_cue_count: int = Field(ge=1)
    high_confidence_count: int = Field(ge=0)
    medium_confidence_count: int = Field(ge=0)
    low_confidence_count: int = Field(ge=0)
    adjudication_block_count: int = Field(ge=0)
    evidence_anchor_count: int = Field(default=0, ge=0)
    target_coverage_complete: bool
    source_coverage_complete: bool
    source_order_preserved: bool
    semantic_starts_source_anchored: bool
    semantic_windows_non_overlapping: bool
    model_call_count: Literal[0] = 0


class LocalizationSemanticAlignmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-semantic-alignment-v14"
    ] = ALIGNER_VERSION
    source_fingerprint: str = Field(min_length=64, max_length=64)
    spoken_script_fingerprint: str = Field(min_length=64, max_length=64)
    result_fingerprint: str = Field(min_length=64, max_length=64)
    embedding_model_id: Literal["sentence-transformers/LaBSE"] = MODEL_ID
    embedding_model_fingerprint: str = Field(min_length=1)
    evidence_anchors: list[LocalizationSemanticAlignmentAnchor] = Field(
        default_factory=list,
    )
    blocks: list[LocalizationSemanticAlignmentBlock] = Field(min_length=1)
    quality_summary: LocalizationSemanticAlignmentQualitySummary


class MultilingualTextEncoder(Protocol):
    model_id: str
    model_fingerprint: str

    def encode(self, texts: list[str]) -> np.ndarray: ...


@dataclass(frozen=True)
class _Transition:
    previous_target: int
    previous_source: int
    target_count: int
    source_count: int
    similarity: float
    cost: float


def align_localized_script(
    request: LocalizationSemanticAlignmentInput,
    *,
    encoder: MultilingualTextEncoder,
) -> LocalizationSemanticAlignmentResult:
    """Align one full localized script with deterministic path guarantees."""

    if encoder.model_id != request.policy.embedding_model_id:
        raise ValueError("语义映射模型与任务策略不一致。")
    targets = request.target_paragraphs
    sources = request.source_cues
    target_vectors = encoder.encode([item.text for item in targets])
    source_vectors = encoder.encode([item.text for item in sources])
    if target_vectors.shape[0] != len(targets):
        raise ValueError("中文语义向量数量不完整。")
    if source_vectors.shape[0] != len(sources):
        raise ValueError("英文语义向量数量不完整。")
    transitions = _best_path(
        targets,
        sources,
        target_vectors,
        source_vectors,
        request.policy,
        evidence_anchors=request.evidence_anchors,
    )
    grouped = _consolidate_unmatched(
        transitions,
        target_vectors=target_vectors,
        source_vectors=source_vectors,
    )
    blocks = _project_blocks(
        grouped,
        targets,
        sources,
        request.policy,
    )
    blocks = _attach_evidence_anchors(request, blocks)
    if request.source_words:
        blocks = _refine_word_boundaries(
            request,
            blocks,
            target_vectors=target_vectors,
            encoder=encoder,
        )
        blocks = _flag_boundary_edge_semantic_ambiguities(
            request,
            blocks,
            encoder=encoder,
        )
    validate_speaker_safe_alignment(request.source_cues, blocks)
    blocks = enforce_semantic_time_invariants(blocks)
    blocks = _flag_extreme_adjacent_density_imbalances(request, blocks)
    _validate_complete_path(request, blocks)
    _validate_evidence_anchor_ownership(request, blocks)
    counts = {
        level: sum(item.confidence == level for item in blocks)
        for level in ("high", "medium", "low")
    }
    quality = LocalizationSemanticAlignmentQualitySummary(
        status=(
            "warning"
            if any(item.needs_adjudication for item in blocks)
            else "passed"
        ),
        block_count=len(blocks),
        paragraph_count=len(targets),
        source_cue_count=len(sources),
        high_confidence_count=counts["high"],
        medium_confidence_count=counts["medium"],
        low_confidence_count=counts["low"],
        adjudication_block_count=sum(
            item.needs_adjudication for item in blocks
        ),
        evidence_anchor_count=len(request.evidence_anchors),
        target_coverage_complete=True,
        source_coverage_complete=True,
        source_order_preserved=True,
        semantic_starts_source_anchored=True,
        semantic_windows_non_overlapping=True,
    )
    fingerprint_payload = {
        "source_fingerprint": request.source_fingerprint,
        "spoken_script_fingerprint": request.spoken_script_fingerprint,
        "embedding_model_fingerprint": encoder.model_fingerprint,
        "policy": request.policy.model_dump(mode="json"),
        "evidence_anchors": [
            item.model_dump(mode="json")
            for item in request.evidence_anchors
        ],
        "blocks": [item.model_dump(mode="json") for item in blocks],
    }
    return LocalizationSemanticAlignmentResult(
        source_fingerprint=request.source_fingerprint,
        spoken_script_fingerprint=request.spoken_script_fingerprint,
        result_fingerprint=_fingerprint(fingerprint_payload),
        embedding_model_fingerprint=encoder.model_fingerprint,
        evidence_anchors=list(request.evidence_anchors),
        blocks=blocks,
        quality_summary=quality,
    )


def project_localization_semantic_alignment_result(
    result: LocalizationSemanticAlignmentResult,
    *,
    source_cues: list[LocalizationAlignmentSourceCue],
    target_paragraphs: list[LocalizationAlignmentTargetParagraph],
    source_words: list[LocalizationSourceWord] | None = None,
) -> dict:
    quality = result.quality_summary
    source_by_id = {item.cue_id: item for item in source_cues}
    target_by_id = {
        item.paragraph_id: item for item in target_paragraphs
    }
    source_word_by_id = {
        item.word_id: item for item in (source_words or [])
    }
    items = [
        _project_alignment_block(
            block,
            source_by_id=source_by_id,
            target_by_id=target_by_id,
            source_word_by_id=source_word_by_id,
        )
        for block in result.blocks
    ]
    debug_items = [
        _project_alignment_debug_block(block)
        for block in result.blocks
    ]
    review_items = [
        item
        for block, item in zip(result.blocks, items)
        if block.needs_adjudication
    ]
    sections = []
    if review_items:
        sections.append(
            {
                "title": "优先查看：低把握映射",
                "open_by_default": True,
                "items": review_items,
            }
        )
    sections.append(
        {
            "title": "全部语义映射",
            "open_by_default": False,
            "items": items,
        }
    )
    return {
        "label": "本地映射语义时间",
        "order": 90,
        "status": (
            "warning"
            if quality.status == "warning"
            else "success"
        ),
        "purpose": (
            "用本地跨语言向量和单调路径，把完整中文台词映射回英文逐词时间；"
            "不调用 LLM，也不修改中文。"
        ),
        "summary": (
            f"已映射 {quality.paragraph_count} 个中文语义段；"
            f"{quality.adjudication_block_count} 个边界需要进一步复核。"
        ),
        "metrics": [
            {"label": "中文语义段", "value": str(quality.paragraph_count)},
            {"label": "英文字幕", "value": str(quality.source_cue_count)},
            {
                "label": "低把握边界",
                "value": str(quality.adjudication_block_count),
            },
        ],
        "sections": sections,
        "coverage": {
            "mode": "complete",
            "shown_count": len(items),
            "total_count": len(items),
            "unit": "组语义映射",
        },
        "notes": [
            "程序保证英文与中文都完整覆盖，并保持原顺序。"
        ],
        "debug": {
            "description": (
                "用于核对本地向量模型、对齐器版本和时间映射硬校验；"
                "本步骤不调用 LLM。"
            ),
            "metrics": [
                {
                    "label": "本地向量模型",
                    "value": result.embedding_model_id,
                },
                {
                    "label": "对齐器版本",
                    "value": result.contract_version,
                },
                {
                    "label": "模型调用",
                    "value": str(quality.model_call_count),
                },
                {
                    "label": "中英文完整覆盖",
                    "value": (
                        "通过"
                        if (
                            quality.target_coverage_complete
                            and quality.source_coverage_complete
                        )
                        else "未通过"
                    ),
                },
                {
                    "label": "英文顺序保持",
                    "value": (
                        "通过"
                        if quality.source_order_preserved
                        else "未通过"
                    ),
                },
                {
                    "label": "时间窗无重叠",
                    "value": (
                        "通过"
                        if quality.semantic_windows_non_overlapping
                        else "未通过"
                    ),
                },
            ],
            "sections": [
                {
                    "title": "语义映射内部依据",
                    "items": debug_items,
                }
            ],
        },
    }


def _project_alignment_block(
    block: LocalizationSemanticAlignmentBlock,
    *,
    source_by_id: dict[str, LocalizationAlignmentSourceCue],
    target_by_id: dict[str, LocalizationAlignmentTargetParagraph],
    source_word_by_id: dict[str, LocalizationSourceWord],
) -> dict:
    target_text = "\n\n".join(
        target_by_id[item].text for item in block.paragraph_ids
    )
    source_text = (
        join_source_words(
            [
                source_word_by_id[item].text
                for item in block.source_word_ids
                if item in source_word_by_id
            ]
        )
        if source_word_by_id
        else " ".join(
            source_by_id[item].text for item in block.source_cue_ids
        )
    )
    paragraph_label = _id_range_label(
        block.paragraph_ids,
        prefix="paragraph_",
    )
    confidence_label = {
        "high": "高把握",
        "medium": "中等把握",
        "low": "低把握",
    }[block.confidence]
    facts = []
    if block.needs_adjudication:
        facts.append(
            {
                "label": "后续处理",
                "value": "交给“复核时间歧义”从附近候选中选择",
            }
        )
    return {
        "title": f"语义段 {paragraph_label}",
        "before_label": "本土化内容",
        "before": target_text,
        "after_label": "对应英文",
        "after": source_text,
        "text": (
            "已交给下一步“复核时间歧义”自动确认，不会阻塞后续流程。"
            if block.needs_adjudication
            else ""
        ),
        "meta": (
            f"{_format_timestamp(block.source_start_ms)}–"
            f"{_format_timestamp(block.source_end_ms)} · "
            f"{confidence_label}"
        ),
        "facts": facts,
        "links": [],
        "tone": (
            "warning"
            if block.needs_adjudication
            else "positive"
            if block.confidence == "high"
            else "neutral"
        ),
    }


def _project_alignment_debug_block(
    block: LocalizationSemanticAlignmentBlock,
) -> dict:
    return {
        "title": (
            f"语义段 {_id_range_label(block.paragraph_ids, prefix='paragraph_')}"
        ),
        "meta": (
            f"{_format_timestamp(block.source_start_ms)}–"
            f"{_format_timestamp(block.source_end_ms)}"
        ),
        "facts": [
            {
                "label": "英文字幕编号",
                "value": _id_range_label(block.source_cue_ids),
            },
            {
                "label": "向量相似度",
                "value": f"{block.similarity:.3f}",
            },
        ],
        "links": [],
        "tone": "neutral",
    }


def _id_range_label(ids: list[str], *, prefix: str = "") -> str:
    def display(value: str) -> str:
        return value.removeprefix(prefix) if prefix else value

    first = display(ids[0])
    last = display(ids[-1])
    return first if first == last else f"{first}–{last}"


def _format_timestamp(milliseconds: int) -> str:
    total_seconds, millis = divmod(max(0, milliseconds), 1_000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def _best_path(
    targets: list[LocalizationAlignmentTargetParagraph],
    sources: list[LocalizationAlignmentSourceCue],
    target_vectors: np.ndarray,
    source_vectors: np.ndarray,
    policy: LocalizationSemanticAlignmentPolicy,
    *,
    evidence_anchors: (
        list[LocalizationSemanticAlignmentAnchor]
        | tuple[LocalizationSemanticAlignmentAnchor, ...]
    ) = (),
) -> list[_Transition]:
    target_count = len(targets)
    source_count = len(sources)
    target_sizes = [_text_size(item.text, chinese=True) for item in targets]
    source_sizes = [_text_size(item.text, chinese=False) for item in sources]
    global_ratio = sum(target_sizes) / max(sum(source_sizes), 1)
    target_prefix = np.concatenate([[0], np.cumsum(target_sizes)])
    source_prefix = np.concatenate([[0], np.cumsum(source_sizes)])
    grouped_source_vectors = _precompute_group_vectors(
        source_vectors,
        maximum_source_group=policy.maximum_source_group,
    )
    target_index = {
        item.paragraph_id: index for index, item in enumerate(targets)
    }
    source_index = {
        item.cue_id: index for index, item in enumerate(sources)
    }
    anchor_bounds: dict[int, tuple[int, int]] = {}
    preserve_speaker_turns = len(
        {
            item.speaker_id
            for item in sources
            if item.speaker_id is not None
        }
    ) > 1
    speaker_run_starts = {
        index
        for index, item in enumerate(sources)
        if index == 0
        or item.speaker_id != sources[index - 1].speaker_id
    }
    for anchor in evidence_anchors:
        paragraph_position = target_index[anchor.target_paragraph_id]
        positions = [source_index[item] for item in anchor.source_cue_ids]
        current = anchor_bounds.get(paragraph_position)
        anchor_bounds[paragraph_position] = (
            min(positions[0], current[0]) if current else positions[0],
            max(positions[-1], current[1]) if current else positions[-1],
        )
    scores = np.full((target_count + 1, source_count + 1), np.inf)
    scores[0, 0] = 0.0
    back: dict[tuple[int, int], _Transition] = {}

    for target_end in range(target_count + 1):
        for source_end in range(source_count + 1):
            base = float(scores[target_end, source_end])
            if not math.isfinite(base):
                continue
            if (
                target_end < target_count
                and target_end not in anchor_bounds
            ):
                _offer(
                    scores,
                    back,
                    target_end + 1,
                    source_end,
                    base + policy.insertion_cost,
                    _Transition(
                        target_end,
                        source_end,
                        1,
                        0,
                        0.0,
                        policy.insertion_cost,
                    ),
                )
            if (
                source_end < source_count
                and (
                    not preserve_speaker_turns
                    or source_end not in speaker_run_starts
                )
            ):
                _offer(
                    scores,
                    back,
                    target_end,
                    source_end + 1,
                    base + policy.deletion_cost,
                    _Transition(
                        target_end,
                        source_end,
                        0,
                        1,
                        0.0,
                        policy.deletion_cost,
                    ),
                )
            if target_end >= target_count:
                continue
            target_size = (
                target_prefix[target_end + 1] - target_prefix[target_end]
            )
            for source_group in range(
                1,
                min(
                    policy.maximum_source_group,
                    source_count - source_end,
                )
                + 1,
            ):
                source_items = sources[
                    source_end : source_end + source_group
                ]
                if (
                    preserve_speaker_turns
                    and not source_window_is_single_speaker(source_items)
                ):
                    continue
                required = anchor_bounds.get(target_end)
                if required is not None and not (
                    source_end <= required[0]
                    and source_end + source_group - 1 >= required[1]
                ):
                    continue
                similarity = float(
                    target_vectors[target_end]
                    @ grouped_source_vectors[source_group - 1][source_end]
                )
                source_size = (
                    source_prefix[source_end + source_group]
                    - source_prefix[source_end]
                )
                expected_target_size = max(
                    source_size * global_ratio,
                    1.0,
                )
                length_penalty = min(
                    abs(
                        math.log(
                            max(target_size, 1)
                            / expected_target_size
                        )
                    ),
                    3.0,
                ) * 0.30
                structure_penalty = 0.01 * max(source_group - 1, 0)
                step_cost = (
                    max(0.0, 1.0 - similarity)
                    + length_penalty
                    + structure_penalty
                )
                _offer(
                    scores,
                    back,
                    target_end + 1,
                    source_end + source_group,
                    base + step_cost,
                    _Transition(
                        target_end,
                        source_end,
                        1,
                        source_group,
                        similarity,
                        step_cost,
                    ),
                )

    cursor = (target_count, source_count)
    transitions = []
    while cursor != (0, 0):
        transition = back.get(cursor)
        if transition is None:
            if preserve_speaker_turns:
                raise ValueError(
                    "无法在保持完整内容和说话人边界的前提下完成语义映射；"
                    "请检查上游人物回合与中文分段。"
                )
            raise ValueError(f"语义映射路径在 {cursor} 中断。")
        transitions.append(transition)
        cursor = (
            transition.previous_target,
            transition.previous_source,
        )
    transitions.reverse()
    return transitions


def _precompute_group_vectors(
    source_vectors: np.ndarray,
    *,
    maximum_source_group: int,
) -> list[np.ndarray]:
    """Build each source-group embedding once before path search.

    Every source-group vector used to be re-created for every target
    paragraph. The vector depends only on the source start and group size.
    Keeping the original scalar target/vector dot product preserves exact path
    and fingerprint semantics while eliminating repeated mean/normalization
    work. Tables stay grouped by window size to avoid invalid padded starts.
    """

    source_count = source_vectors.shape[0]
    tables: list[np.ndarray] = []
    for source_group in range(
        1,
        min(maximum_source_group, source_count) + 1,
    ):
        tables.append(
            np.stack(
                [
                    _normalized_mean(
                        source_vectors,
                        source_start,
                        source_group,
                    )
                    for source_start in range(
                        source_count - source_group + 1
                    )
                ]
            )
        )
    return tables


def _consolidate_unmatched(
    transitions: list[_Transition],
    *,
    target_vectors: np.ndarray,
    source_vectors: np.ndarray,
) -> list[list[_Transition]]:
    """Attach unmatched content to the semantically closer neighbour."""

    groups: list[list[_Transition]] = []
    pending: list[_Transition] = []
    for transition in transitions:
        if transition.target_count and transition.source_count:
            if pending:
                if groups:
                    previous_score = _unmatched_neighbour_affinity(
                        pending,
                        groups[-1],
                        target_vectors=target_vectors,
                        source_vectors=source_vectors,
                    )
                    next_score = _unmatched_neighbour_affinity(
                        pending,
                        [transition],
                        target_vectors=target_vectors,
                        source_vectors=source_vectors,
                    )
                    if next_score > previous_score + 0.02:
                        groups.append([*pending, transition])
                        pending = []
                        continue
                    groups[-1].extend(pending)
                else:
                    pending.append(transition)
                    groups.append(pending)
                    pending = []
                    continue
                pending = []
            groups.append([transition])
        else:
            pending.append(transition)
    if pending:
        if not groups:
            raise ValueError("语义映射没有任何可用的中英匹配。")
        groups[-1].extend(pending)
    return groups


def _unmatched_neighbour_affinity(
    pending: list[_Transition],
    neighbour: list[_Transition],
    *,
    target_vectors: np.ndarray,
    source_vectors: np.ndarray,
) -> float:
    pending_target_indexes = [
        index
        for item in pending
        for index in range(
            item.previous_target,
            item.previous_target + item.target_count,
        )
    ]
    pending_source_indexes = [
        index
        for item in pending
        for index in range(
            item.previous_source,
            item.previous_source + item.source_count,
        )
    ]
    neighbour_target_indexes = [
        index
        for item in neighbour
        for index in range(
            item.previous_target,
            item.previous_target + item.target_count,
        )
    ]
    neighbour_source_indexes = [
        index
        for item in neighbour
        for index in range(
            item.previous_source,
            item.previous_source + item.source_count,
        )
    ]
    scores = []
    if pending_target_indexes and neighbour_source_indexes:
        scores.append(
            float(
                np.dot(
                    _normalized_vector_mean(
                        target_vectors,
                        pending_target_indexes,
                    ),
                    _normalized_vector_mean(
                        source_vectors,
                        neighbour_source_indexes,
                    ),
                )
            )
        )
    if pending_source_indexes and neighbour_target_indexes:
        scores.append(
            float(
                np.dot(
                    _normalized_vector_mean(
                        source_vectors,
                        pending_source_indexes,
                    ),
                    _normalized_vector_mean(
                        target_vectors,
                        neighbour_target_indexes,
                    ),
                )
            )
        )
    return sum(scores) / len(scores) if scores else -1.0


def _normalized_vector_mean(
    vectors: np.ndarray,
    indexes: list[int],
) -> np.ndarray:
    vector = vectors[indexes].mean(axis=0)
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def _project_blocks(
    groups: list[list[_Transition]],
    targets: list[LocalizationAlignmentTargetParagraph],
    sources: list[LocalizationAlignmentSourceCue],
    policy: LocalizationSemanticAlignmentPolicy,
) -> list[LocalizationSemanticAlignmentBlock]:
    blocks = []
    for sequence, group in enumerate(groups, start=1):
        target_start = min(item.previous_target for item in group)
        target_end = max(
            item.previous_target + item.target_count for item in group
        )
        source_start = min(item.previous_source for item in group)
        source_end = max(
            item.previous_source + item.source_count for item in group
        )
        target_items = targets[target_start:target_end]
        source_items = sources[source_start:source_end]
        if not target_items or not source_items:
            raise ValueError("合并后的语义块缺少中英文内容。")
        matched = [
            item
            for item in group
            if item.target_count and item.source_count
        ]
        similarity = (
            sum(item.similarity for item in matched) / len(matched)
            if matched
            else 0.0
        )
        reason_codes = []
        if any(
            not item.target_count or not item.source_count
            for item in group
        ):
            reason_codes.append("unmatched_content_consolidated")
        if similarity < policy.low_similarity_threshold:
            reason_codes.append("low_crosslingual_similarity")
        if len(source_items) >= policy.broad_source_group_threshold:
            reason_codes.append("broad_source_window")
        needs_adjudication = any(
            code
            in {
                "unmatched_content_consolidated",
                "low_crosslingual_similarity",
            }
            for code in reason_codes
        )
        confidence: AlignmentConfidence = (
            "low"
            if needs_adjudication
            else "high"
            if similarity >= 0.55
            else "medium"
        )
        blocks.append(
            LocalizationSemanticAlignmentBlock(
                block_id=f"alignment_block_{sequence:04d}",
                kind=(
                    "ambiguous_semantic_group"
                    if needs_adjudication
                    else "semantic_match"
                ),
                paragraph_ids=[
                    item.paragraph_id for item in target_items
                ],
                source_cue_ids=[
                    item.cue_id for item in source_items
                ],
                source_word_ids=[
                    word_id
                    for item in source_items
                    for word_id in item.source_word_ids
                ],
                source_start_ms=source_items[0].start_ms,
                source_end_ms=source_items[-1].end_ms,
                similarity=round(similarity, 6),
                confidence=confidence,
                needs_adjudication=needs_adjudication,
                reason_codes=reason_codes,
            )
        )
    return blocks


def _attach_evidence_anchors(
    request: LocalizationSemanticAlignmentInput,
    blocks: list[LocalizationSemanticAlignmentBlock],
) -> list[LocalizationSemanticAlignmentBlock]:
    anchors_by_paragraph: dict[str, list[str]] = {}
    for anchor in request.evidence_anchors:
        anchors_by_paragraph.setdefault(
            anchor.target_paragraph_id,
            [],
        ).append(anchor.anchor_id)
    return [
        block.model_copy(
            update={
                "evidence_anchor_ids": [
                    anchor_id
                    for paragraph_id in block.paragraph_ids
                    for anchor_id in anchors_by_paragraph.get(
                        paragraph_id,
                        [],
                    )
                ],
                "reason_codes": list(
                    dict.fromkeys(
                        [
                            *block.reason_codes,
                            *(
                                ["verified_evidence_anchor"]
                                if any(
                                    paragraph_id in anchors_by_paragraph
                                    for paragraph_id in block.paragraph_ids
                                )
                                else []
                            ),
                        ]
                    )
                ),
            }
        )
        for block in blocks
    ]


def _validate_evidence_anchor_ownership(
    request: LocalizationSemanticAlignmentInput,
    blocks: list[LocalizationSemanticAlignmentBlock],
) -> None:
    block_by_paragraph = {
        paragraph_id: block
        for block in blocks
        for paragraph_id in block.paragraph_ids
    }
    for anchor in request.evidence_anchors:
        block = block_by_paragraph.get(anchor.target_paragraph_id)
        if block is None or not set(anchor.source_cue_ids).issubset(
            block.source_cue_ids
        ):
            raise ValueError("语义映射没有保持已确认的中英文归属。")


def _refine_word_boundaries(
    request: LocalizationSemanticAlignmentInput,
    blocks: list[LocalizationSemanticAlignmentBlock],
    *,
    target_vectors: np.ndarray,
    encoder: MultilingualTextEncoder,
) -> list[LocalizationSemanticAlignmentBlock]:
    """Refine coarse cue boundaries without changing target-language order.

    Cue-level alignment remains the global path owner.  This pass only moves
    adjacent boundaries to an eligible source-word boundary when the bilingual
    semantic score improves.  Pause evidence lowers the required gain; target
    text density is deliberately only a small tie-breaker.
    """

    if len(blocks) < 2:
        return blocks
    words = request.source_words
    word_index = {item.word_id: index for index, item in enumerate(words)}
    target_index = {
        item.paragraph_id: index
        for index, item in enumerate(request.target_paragraphs)
    }
    evidence_by_position = {
        item.position_after_word: item
        for item in request.source_boundaries
    }
    anchor_by_id = {
        item.anchor_id: item for item in request.evidence_anchors
    }
    cue_by_id = {item.cue_id: item for item in request.source_cues}
    speaker_by_word = {
        word_id: cue.speaker_id
        for cue in request.source_cues
        for word_id in cue.source_word_ids
    }
    original_boundaries = [
        word_index[block.source_word_ids[-1]] + 1
        for block in blocks[:-1]
    ]
    outer_boundaries = [0, *original_boundaries, len(words)]
    total_target_units = sum(
        _text_size(item.text, chinese=True)
        for item in request.target_paragraphs
    )
    total_duration_seconds = max(
        (words[-1].end_ms - words[0].start_ms) / 1_000,
        0.001,
    )
    baseline_density = total_target_units / total_duration_seconds
    selected_boundaries: list[int] = []
    refined_reasons: dict[int, list[str]] = {}

    for boundary_index, (left_block, right_block) in enumerate(
        zip(blocks, blocks[1:])
    ):
        original = original_boundaries[boundary_index]
        lower = max(
            outer_boundaries[boundary_index] + 1,
            original - request.policy.word_boundary_search_radius,
        )
        upper = min(
            outer_boundaries[boundary_index + 2] - 1,
            original + request.policy.word_boundary_search_radius,
        )
        left_anchor_ends = [
            word_index[
                cue_by_id[cue_id].source_word_ids[-1]
            ]
            + 1
            for anchor_id in left_block.evidence_anchor_ids
            for cue_id in anchor_by_id[anchor_id].source_cue_ids
        ]
        right_anchor_starts = [
            word_index[cue_by_id[cue_id].source_word_ids[0]]
            for anchor_id in right_block.evidence_anchor_ids
            for cue_id in anchor_by_id[anchor_id].source_cue_ids
        ]
        if left_anchor_ends:
            lower = max(lower, max(left_anchor_ends))
        if right_anchor_starts:
            upper = min(upper, min(right_anchor_starts))
        if lower > upper:
            raise ValueError("逐词边界无法保持已确认的中英文归属。")
        left_outer = outer_boundaries[boundary_index]
        right_outer = outer_boundaries[boundary_index + 2]
        positions = [
            position
            for position in range(lower, upper + 1)
            if position == original
            or (
                (evidence := evidence_by_position.get(position)) is not None
                and evidence.eligible
            )
        ]
        positions = [
            position
            for position in positions
            if _word_window_is_single_speaker(
                words[left_outer:position],
                speaker_by_word=speaker_by_word,
            )
            and _word_window_is_single_speaker(
                words[position:right_outer],
                speaker_by_word=speaker_by_word,
            )
        ]
        if len(positions) < 2:
            selected_boundaries.append(original)
            continue

        source_texts: list[str] = []
        for position in positions:
            source_texts.extend(
                [
                    join_source_words(
                        [item.text for item in words[left_outer:position]]
                    ),
                    join_source_words(
                        [item.text for item in words[position:right_outer]]
                    ),
                ]
            )
        source_vectors = encoder.encode(source_texts)
        left_target_vector = _block_target_vector(
            left_block,
            target_vectors=target_vectors,
            target_index=target_index,
        )
        right_target_vector = _block_target_vector(
            right_block,
            target_vectors=target_vectors,
            target_index=target_index,
        )
        left_units = sum(
            _text_size(
                request.target_paragraphs[target_index[item]].text,
                chinese=True,
            )
            for item in left_block.paragraph_ids
        )
        right_units = sum(
            _text_size(
                request.target_paragraphs[target_index[item]].text,
                chinese=True,
            )
            for item in right_block.paragraph_ids
        )
        candidates: list[tuple[float, int, float, SourceBoundaryEvidence | None]] = []
        for candidate_index, position in enumerate(positions):
            semantic_score = (
                float(left_target_vector @ source_vectors[candidate_index * 2])
                + float(
                    right_target_vector
                    @ source_vectors[candidate_index * 2 + 1]
                )
            ) / 2
            evidence = evidence_by_position.get(position)
            evidence_bonus = (
                min(max(evidence.score, 0.0), 12.0) * 0.004
                if evidence is not None
                else 0.0
            )
            density_penalty = _boundary_density_penalty(
                words,
                left_outer=left_outer,
                position=position,
                right_outer=right_outer,
                left_units=left_units,
                right_units=right_units,
                baseline_density=baseline_density,
            )
            score = (
                semantic_score
                + evidence_bonus
                - request.policy.density_tiebreak_weight * density_penalty
            )
            candidates.append((score, position, semantic_score, evidence))
        original_candidate = next(
            item for item in candidates if item[1] == original
        )
        best = max(
            candidates,
            key=lambda item: (item[0], -abs(item[1] - original)),
        )
        gain = best[0] - original_candidate[0]
        supported = bool(
            best[3] is not None
            and (
                best[3].objective_support
                or best[3].classification in {"hard", "preferred"}
            )
        )
        required_gain = (
            request.policy.supported_boundary_minimum_gain
            if supported
            else request.policy.word_boundary_minimum_gain
        )
        chosen = best[1] if best[1] != original and gain >= required_gain else original
        selected_boundaries.append(chosen)
        if chosen != original:
            reasons = ["word_boundary_refined"]
            chosen_evidence = evidence_by_position.get(chosen)
            if chosen_evidence is not None and (
                chosen_evidence.pause_ms >= 250
                or chosen_evidence.pause_confidence in {"medium", "high"}
            ):
                reasons.append("audio_pause_supported")
            refined_reasons[boundary_index] = reasons

    if any(
        left >= right
        for left, right in zip(selected_boundaries, selected_boundaries[1:])
    ):
        selected_boundaries = original_boundaries
        refined_reasons = {}
    boundaries = [0, *selected_boundaries, len(words)]
    cue_by_word = {
        word_id: cue.cue_id
        for cue in request.source_cues
        for word_id in cue.source_word_ids
    }
    rebuilt = []
    for block_index, block in enumerate(blocks):
        block_words = words[boundaries[block_index] : boundaries[block_index + 1]]
        if not block_words:
            raise ValueError("逐词语义细化产生了空时间块。")
        cue_ids = list(
            dict.fromkeys(cue_by_word[item.word_id] for item in block_words)
        )
        reasons = list(block.reason_codes)
        if block_index - 1 in refined_reasons:
            reasons.extend(refined_reasons[block_index - 1])
        if block_index in refined_reasons:
            reasons.extend(refined_reasons[block_index])
        rebuilt.append(
            block.model_copy(
                update={
                    "source_cue_ids": cue_ids,
                    "source_word_ids": [item.word_id for item in block_words],
                    "source_start_ms": block_words[0].start_ms,
                    "source_end_ms": block_words[-1].end_ms,
                    "reason_codes": list(dict.fromkeys(reasons)),
                }
            )
        )
    return rebuilt


def _flag_boundary_edge_semantic_ambiguities(
    request: LocalizationSemanticAlignmentInput,
    blocks: list[LocalizationSemanticAlignmentBlock],
    *,
    encoder: MultilingualTextEncoder,
) -> list[LocalizationSemanticAlignmentBlock]:
    """Route locally suspicious edges to bounded review.

    Whole-paragraph embeddings can hide a short phrase assigned to the wrong
    neighbour.  This pass compares only the target tail/head with nearby
    source-word tails/heads and never moves a boundary by itself.
    """

    if len(blocks) < 2 or not request.source_words:
        return blocks
    words = request.source_words
    word_index = {
        item.word_id: index for index, item in enumerate(words)
    }
    target_by_id = {
        item.paragraph_id: item.text
        for item in request.target_paragraphs
    }
    cue_start_positions = {
        word_index[item.source_word_ids[0]]
        for item in request.source_cues
        if item.source_word_ids
        and item.source_word_ids[0] in word_index
    }
    eligible_positions = {
        item.position_after_word
        for item in request.source_boundaries
        if item.eligible
    }
    speaker_by_word = {
        word_id: cue.speaker_id
        for cue in request.source_cues
        for word_id in cue.source_word_ids
    }
    anchor_word_ranges: dict[str, tuple[int, int]] = {}
    cue_by_id = {
        item.cue_id: item for item in request.source_cues
    }
    for anchor in request.evidence_anchors:
        anchor_words = [
            word_id
            for cue_id in anchor.source_cue_ids
            for word_id in cue_by_id[cue_id].source_word_ids
            if word_id in word_index
        ]
        if anchor_words:
            indexes = [word_index[word_id] for word_id in anchor_words]
            anchor_word_ranges[anchor.anchor_id] = (
                min(indexes),
                max(indexes) + 1,
            )

    flagged_indexes: set[int] = set()
    for boundary_index, (left, right) in enumerate(
        zip(blocks, blocks[1:])
    ):
        if left.needs_adjudication or right.needs_adjudication:
            continue
        # Whole-paragraph similarity is not evidence that its first/last
        # phrase belongs to that paragraph.  Even a high-confidence match
        # must compare local ownership before bypassing bounded adjudication.
        original = word_index[left.source_word_ids[-1]] + 1
        left_outer = word_index[left.source_word_ids[0]]
        right_outer = word_index[right.source_word_ids[-1]] + 1
        lower = max(
            left_outer + 1,
            original - request.policy.word_boundary_search_radius,
        )
        upper = min(
            right_outer - 1,
            original + request.policy.word_boundary_search_radius,
        )
        candidate_positions = [
            position
            for position in range(lower, upper + 1)
            if position == original
            or position in cue_start_positions
            or position in eligible_positions
        ]
        candidate_positions = [
            position
            for position in candidate_positions
            if _boundary_position_preserves_alignment_anchors(
                position,
                left=left,
                right=right,
                anchor_word_ranges=anchor_word_ranges,
            )
            and _word_window_is_single_speaker(
                words[left_outer:position],
                speaker_by_word=speaker_by_word,
            )
            and _word_window_is_single_speaker(
                words[position:right_outer],
                speaker_by_word=speaker_by_word,
            )
        ]
        if original not in candidate_positions or len(candidate_positions) < 2:
            continue
        left_target = _boundary_target_edge_text(
            " ".join(target_by_id[item] for item in left.paragraph_ids),
            side="left",
        )
        right_target = _boundary_target_edge_text(
            " ".join(target_by_id[item] for item in right.paragraph_ids),
            side="right",
        )
        source_pairs = [
            (
                join_source_words(
                    [
                        item.text
                        for item in words[
                            max(
                                left_outer,
                                position
                                - request.policy.boundary_edge_word_window,
                            ) : position
                        ]
                    ]
                ),
                join_source_words(
                    [
                        item.text
                        for item in words[
                            position : min(
                                right_outer,
                                position
                                + request.policy.boundary_edge_word_window,
                            )
                        ]
                    ]
                ),
            )
            for position in candidate_positions
        ]
        texts = [left_target, right_target]
        for left_source, right_source in source_pairs:
            texts.extend([left_source, right_source])
        unique_texts = list(dict.fromkeys(texts))
        vectors = np.asarray(
            encoder.encode(unique_texts),
            dtype=np.float32,
        )
        if vectors.ndim != 2 or vectors.shape[0] != len(unique_texts):
            raise ValueError("跨语言向量模型返回了无效局部边界结果。")
        vectors /= np.maximum(
            np.linalg.norm(vectors, axis=1, keepdims=True),
            1e-12,
        )
        vector_by_text = dict(zip(unique_texts, vectors))
        scores = [
            (
                float(
                    np.dot(
                        vector_by_text[left_target],
                        vector_by_text[left_source],
                    )
                    + np.dot(
                        vector_by_text[right_target],
                        vector_by_text[right_source],
                    )
                )
                / 2.0,
                position,
            )
            for position, (left_source, right_source) in zip(
                candidate_positions,
                source_pairs,
            )
        ]
        original_score = next(
            score for score, position in scores if position == original
        )
        best_score, best_position = max(
            scores,
            key=lambda item: (
                item[0],
                -abs(item[1] - original),
            ),
        )
        if (
            best_position != original
            and best_score - original_score
            >= request.policy.boundary_edge_adjudication_minimum_gain
        ):
            flagged_indexes.add(boundary_index + 1)

    return [
        (
            block.model_copy(
                update={
                    "kind": "ambiguous_semantic_group",
                    "confidence": "low",
                    "needs_adjudication": True,
                    "reason_codes": list(
                        dict.fromkeys(
                            [
                                *block.reason_codes,
                                "boundary_edge_semantic_ambiguity",
                            ]
                        )
                    ),
                }
            )
            if index in flagged_indexes
            else block
        )
        for index, block in enumerate(blocks)
    ]


def _boundary_position_preserves_alignment_anchors(
    position: int,
    *,
    left: LocalizationSemanticAlignmentBlock,
    right: LocalizationSemanticAlignmentBlock,
    anchor_word_ranges: dict[str, tuple[int, int]],
) -> bool:
    return all(
        anchor_id not in anchor_word_ranges
        or anchor_word_ranges[anchor_id][1] <= position
        for anchor_id in left.evidence_anchor_ids
    ) and all(
        anchor_id not in anchor_word_ranges
        or anchor_word_ranges[anchor_id][0] >= position
        for anchor_id in right.evidence_anchor_ids
    )


def _boundary_target_edge_text(
    text: str,
    *,
    side: Literal["left", "right"],
) -> str:
    clauses = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?；;])\s*", text)
        if item.strip()
    ]
    selected = clauses[-1] if side == "left" else clauses[0]
    return selected[-64:] if side == "left" else selected[:64]


def source_window_is_single_speaker(
    source_cues: list[LocalizationAlignmentSourceCue],
) -> bool:
    """Return whether a source window contains at most one known speaker."""

    return len(
        {
            item.speaker_id
            for item in source_cues
            if item.speaker_id is not None
        }
    ) <= 1


def validate_speaker_safe_alignment(
    source_cues: list[LocalizationAlignmentSourceCue],
    blocks: list[LocalizationSemanticAlignmentBlock],
) -> None:
    """Keep every localized paragraph inside one known speaker turn."""

    source_by_id = {item.cue_id: item for item in source_cues}
    for block in blocks:
        block_sources = [
            source_by_id[cue_id]
            for cue_id in block.source_cue_ids
            if cue_id in source_by_id
        ]
        if not source_window_is_single_speaker(block_sources):
            raise ValueError(
                "同一中文语义段不能跨说话人；"
                "请让上游按人物回合分段后重试。"
            )


def _word_window_is_single_speaker(
    words: list[LocalizationSourceWord],
    *,
    speaker_by_word: dict[str, str | None],
) -> bool:
    return len(
        {
            speaker_by_word.get(item.word_id)
            for item in words
            if speaker_by_word.get(item.word_id) is not None
        }
    ) <= 1


def _block_target_vector(
    block: LocalizationSemanticAlignmentBlock,
    *,
    target_vectors: np.ndarray,
    target_index: dict[str, int],
) -> np.ndarray:
    indexes = [target_index[item] for item in block.paragraph_ids]
    vector = target_vectors[indexes].mean(axis=0)
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def _boundary_density_penalty(
    words: list[LocalizationSourceWord],
    *,
    left_outer: int,
    position: int,
    right_outer: int,
    left_units: int,
    right_units: int,
    baseline_density: float,
) -> float:
    penalties = []
    for units, first, last in (
        (left_units, left_outer, position),
        (right_units, position, right_outer),
    ):
        duration = max(
            (words[last - 1].end_ms - words[first].start_ms) / 1_000,
            0.15,
        )
        density = max(units / duration, 0.01)
        penalties.append(
            min(abs(math.log(density / max(baseline_density, 0.01))), 1.5)
        )
    return sum(penalties) / len(penalties)


def _flag_extreme_adjacent_density_imbalances(
    request: LocalizationSemanticAlignmentInput,
    blocks: list[LocalizationSemanticAlignmentBlock],
) -> list[LocalizationSemanticAlignmentBlock]:
    """Send only ambiguous, severely imbalanced neighbours to review."""

    if len(blocks) < 2:
        return blocks
    target_by_id = {
        item.paragraph_id: item for item in request.target_paragraphs
    }
    units = [
        sum(
            _text_size(target_by_id[item].text, chinese=True)
            for item in block.paragraph_ids
        )
        for block in blocks
    ]
    durations = [
        max((block.source_end_ms - block.source_start_ms) / 1_000, 0.15)
        for block in blocks
    ]
    densities = [
        block_units / duration
        for block_units, duration in zip(units, durations)
    ]
    flagged_indexes: set[int] = set()
    for left_index, (left_density, right_density) in enumerate(
        zip(densities, densities[1:])
    ):
        dense_index = (
            left_index
            if left_density >= right_density
            else left_index + 1
        )
        dense = max(left_density, right_density)
        sparse = max(min(left_density, right_density), 0.01)
        if (
            dense / sparse >= 4.0
        ):
            flagged_indexes.add(dense_index)
    return [
        (
            block.model_copy(
                update={
                    "kind": (
                        block.kind
                        if block.confidence == "high"
                        else "ambiguous_semantic_group"
                    ),
                    "confidence": (
                        block.confidence
                        if block.confidence == "high"
                        else "low"
                    ),
                    "needs_adjudication": True,
                    "reason_codes": list(
                        dict.fromkeys(
                            [
                                *block.reason_codes,
                                "adjacent_target_density_imbalance",
                            ]
                        )
                    ),
                }
            )
            if index in flagged_indexes
            else block
        )
        for index, block in enumerate(blocks)
    ]


def _validate_complete_path(
    request: LocalizationSemanticAlignmentInput,
    blocks: list[LocalizationSemanticAlignmentBlock],
) -> None:
    actual_targets = [
        item for block in blocks for item in block.paragraph_ids
    ]
    expected_targets = [
        item.paragraph_id for item in request.target_paragraphs
    ]
    if actual_targets != expected_targets:
        raise ValueError("中文语义段没有被完整且按顺序覆盖。")
    actual_sources = list(
        dict.fromkeys(
            item for block in blocks for item in block.source_cue_ids
        )
    )
    expected_sources = [item.cue_id for item in request.source_cues]
    if actual_sources != expected_sources:
        raise ValueError("英文源字幕没有被完整且按顺序覆盖。")
    if request.source_words:
        actual_words = [
            item for block in blocks for item in block.source_word_ids
        ]
        expected_words = [item.word_id for item in request.source_words]
        if actual_words != expected_words:
            raise ValueError("英文逐词时间没有被完整且按顺序覆盖。")


def enforce_semantic_time_invariants(
    blocks: list[LocalizationSemanticAlignmentBlock],
) -> list[LocalizationSemanticAlignmentBlock]:
    """Keep each semantic start anchored and its end before the next start."""

    normalized = []
    for index, block in enumerate(blocks):
        next_start = (
            blocks[index + 1].source_start_ms
            if index + 1 < len(blocks)
            else None
        )
        end_ms = (
            min(block.source_end_ms, next_start)
            if next_start is not None
            else block.source_end_ms
        )
        if end_ms <= block.source_start_ms:
            raise ValueError("相邻语义时间窗无法同时保持正时长和不重叠。")
        reason_codes = list(block.reason_codes)
        if end_ms != block.source_end_ms:
            reason_codes = list(
                dict.fromkeys(
                    [*reason_codes, "end_clamped_to_next_semantic_start"]
                )
            )
        normalized.append(
            block.model_copy(
                update={
                    "source_end_ms": end_ms,
                    "reason_codes": reason_codes,
                }
            )
        )
    if any(
        left.source_end_ms > right.source_start_ms
        for left, right in zip(normalized, normalized[1:])
    ):
        raise ValueError("中文语义时间窗发生重叠。")
    return normalized


def _offer(
    scores: np.ndarray,
    back: dict[tuple[int, int], _Transition],
    target: int,
    source: int,
    candidate: float,
    transition: _Transition,
) -> None:
    if candidate < scores[target, source]:
        scores[target, source] = candidate
        back[target, source] = transition


def _normalized_mean(
    vectors: np.ndarray,
    start: int,
    count: int,
) -> np.ndarray:
    vector = vectors[start : start + count].mean(axis=0)
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def _text_size(text: str, *, chinese: bool) -> int:
    if chinese:
        return len(
            [
                char
                for char in text
                if not char.isspace()
                and char not in "，。！？；：、“”‘’（）《》"
            ]
        )
    return len(text.split())


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
    "ALIGNER_VERSION",
    "MODEL_ID",
    "LocalizationAlignmentSourceCue",
    "LocalizationAlignmentTargetParagraph",
    "LocalizationSemanticAlignmentInput",
    "LocalizationSemanticAlignmentPolicy",
    "LocalizationSemanticAlignmentResult",
    "MultilingualTextEncoder",
    "align_localized_script",
    "enforce_semantic_time_invariants",
    "project_localization_semantic_alignment_result",
    "source_window_is_single_speaker",
    "validate_speaker_safe_alignment",
]
