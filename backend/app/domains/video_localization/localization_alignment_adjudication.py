"""Bounded LLM adjudication for low-confidence semantic time boundaries.

The deterministic aligner owns paragraph order and complete source coverage.
The model may only choose one program-generated split candidate around an
ambiguous boundary; missing, invalid, or non-monotonic choices fail closed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domains.video_localization.development_llm_batches import DevelopmentLlmBatchReplay
from app.domains.video_localization.llm_observability import (
    VideoLocalizationLlmCallRecord,
    VideoLocalizationLlmTraceCollector,
)
from app.domains.video_localization.localization_alignment_request import (
    ALIGNMENT_ADJUDICATION_PROMPT_VERSION,
    ALIGNMENT_ADJUDICATION_REQUEST_CONTRACT_VERSION,
    ALIGNMENT_ADJUDICATION_SYSTEM_PROMPT,
    build_alignment_adjudication_model_request,
)
from app.domains.video_localization.localization_semantic_alignment import (
    LocalizationAlignmentSourceCue,
    LocalizationAlignmentTargetParagraph,
    LocalizationSemanticAlignmentBlock,
    LocalizationSemanticAlignmentResult,
    MultilingualTextEncoder,
    enforce_semantic_time_invariants,
    source_window_is_single_speaker,
    validate_speaker_safe_alignment,
)
from app.domains.video_localization.localization_source import (
    LocalizationSourceWord,
)
from app.domains.video_localization.source_boundary_evidence import (
    SourceBoundaryEvidence,
    join_source_words,
)
from app.services import llm_runtime
from app.services.localization_ai_policy import LocalizationAiPhaseRoute


ADJUDICATION_VERSION = "localization-alignment-adjudication-v1"


class LocalizationAlignmentBoundaryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^candidate_\d{4}_\d{2}$")
    split_after_cue_id: str | None = Field(default=None, min_length=1)
    split_after_word_id: str | None = Field(default=None, min_length=1)
    left_tail_source: str = Field(min_length=1)
    right_head_source: str = Field(min_length=1)
    boundary_hint: str = ""
    is_original: bool = False

    @model_validator(mode="after")
    def validate_split_anchor(
        self,
    ) -> "LocalizationAlignmentBoundaryCandidate":
        if bool(self.split_after_cue_id) == bool(self.split_after_word_id):
            raise ValueError("边界候选必须且只能提供一种分界锚点。")
        return self


class LocalizationAlignmentBoundaryPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boundary_id: str = Field(pattern=r"^boundary_\d{4}$")
    left_block_id: str = Field(min_length=1)
    right_block_id: str = Field(min_length=1)
    left_target_text: str = Field(min_length=1)
    right_target_text: str = Field(min_length=1)
    candidates: list[LocalizationAlignmentBoundaryCandidate] = Field(
        min_length=2,
        max_length=5,
    )


class LocalizationAlignmentAdjudicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["localization-alignment-adjudication-input-v1"] = (
        "localization-alignment-adjudication-input-v1"
    )
    alignment_operation_id: str = Field(min_length=1)
    alignment: LocalizationSemanticAlignmentResult
    source_cues: list[LocalizationAlignmentSourceCue] = Field(min_length=1)
    source_words: list[LocalizationSourceWord] = Field(default_factory=list)
    source_boundaries: list[SourceBoundaryEvidence] = Field(default_factory=list)
    target_paragraphs: list[LocalizationAlignmentTargetParagraph] = Field(min_length=1)
    route: LocalizationAiPhaseRoute
    candidate_radius: int = Field(default=2, ge=1, le=2)
    candidate_word_radius: int = Field(default=18, ge=4, le=48)


class LocalizationAlignmentBoundaryChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boundary_id: str = Field(pattern=r"^boundary_\d{4}$")
    candidate_id: str = Field(pattern=r"^candidate_\d{4}_\d{2}$")
    reason_zh: str = Field(min_length=1, max_length=500)
    applied: bool = True
    fallback_reason: str | None = None


class LocalizationAlignmentAdjudicationQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["not_needed", "passed", "warning"]
    candidate_boundary_count: int = Field(ge=0)
    applied_choice_count: int = Field(ge=0)
    fallback_choice_count: int = Field(ge=0)
    target_coverage_complete: bool
    source_coverage_complete: bool
    source_order_preserved: bool
    model_call_count: int = Field(ge=0)


class LocalizationAlignmentAdjudicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["localization-alignment-adjudication-v1"] = ADJUDICATION_VERSION
    alignment_fingerprint: str = Field(min_length=64, max_length=64)
    spoken_script_fingerprint: str = Field(min_length=64, max_length=64)
    result_fingerprint: str = Field(min_length=64, max_length=64)
    packets: list[LocalizationAlignmentBoundaryPacket] = Field(
        default_factory=list,
    )
    choices: list[LocalizationAlignmentBoundaryChoice] = Field(
        default_factory=list,
    )
    blocks: list[LocalizationSemanticAlignmentBlock] = Field(min_length=1)
    route: LocalizationAiPhaseRoute
    llm_calls: list[VideoLocalizationLlmCallRecord] = Field(
        default_factory=list,
    )
    quality_summary: LocalizationAlignmentAdjudicationQualitySummary


def adjudicate_localization_alignment(
    request: LocalizationAlignmentAdjudicationInput,
    *,
    encoder: MultilingualTextEncoder | None = None,
    batch_journal: DevelopmentLlmBatchReplay | None = None,
) -> LocalizationAlignmentAdjudicationResult:
    if request.route.phase != "alignment_adjudication":
        raise ValueError("语义时间裁决收到的模型路由阶段不正确。")
    _validate_input_coverage(request)
    packets = build_alignment_boundary_packets(request, encoder=encoder)
    if not packets:
        return _build_result(
            request,
            packets=[],
            choices=[],
            blocks=request.alignment.blocks,
            calls=[],
            status="not_needed",
        )
    choices, calls = adjudicate_alignment_boundary_packets(
        packets,
        route=request.route,
        call_id_prefix="localization-alignment-adjudication",
        batch_journal=batch_journal,
    )
    blocks, applied_choices = _apply_choices(request, packets, choices)
    _validate_output_coverage(request, blocks)
    return _build_result(
        request,
        packets=packets,
        choices=applied_choices,
        blocks=blocks,
        calls=calls,
        status=("warning" if any(not item.applied for item in applied_choices) else "passed"),
    )


def adjudicate_alignment_boundary_packets(
    packets: list[LocalizationAlignmentBoundaryPacket],
    *,
    route: LocalizationAiPhaseRoute,
    call_id_prefix: str,
    maximum_packets_per_call: int = 20,
    batch_journal: DevelopmentLlmBatchReplay | None = None,
) -> tuple[
    list[LocalizationAlignmentBoundaryChoice],
    list[VideoLocalizationLlmCallRecord],
]:
    """Run the shared minimal boundary-choice protocol in bounded batches."""

    if maximum_packets_per_call < 1:
        raise ValueError("语义边界裁决的批次大小必须大于零。")
    collector = VideoLocalizationLlmTraceCollector()
    replayed_calls: list[VideoLocalizationLlmCallRecord] = []
    choices: list[LocalizationAlignmentBoundaryChoice] = []
    for batch_index, start in enumerate(
        range(0, len(packets), maximum_packets_per_call),
        start=1,
    ):
        batch = packets[start : start + maximum_packets_per_call]
        model_request = build_alignment_adjudication_model_request(batch)
        call_id = f"{call_id_prefix}-b{batch_index:03d}"
        attempt = batch_journal.attempt(
            batch_id=call_id, attempt=0, model_id=route.model_id,
            call_id=call_id, purpose="localization_alignment_adjudication", round_index=batch_index,
        ) if batch_journal is not None else None
        complete = attempt.complete_json if attempt is not None else llm_runtime.complete_json
        raw = complete(
            ALIGNMENT_ADJUDICATION_SYSTEM_PROMPT,
            model_request.model_payload(),
            profile_id=route.profile_id,
            temperature=0.0,
            max_tokens=4_000,
            timeout=300,
            reasoning_effort=route.reasoning_effort,
            trace_sink=collector.sink(
                call_id=call_id,
                purpose="localization_alignment_adjudication",
                round_index=batch_index,
            ),
        )
        if attempt is not None:
            replayed_calls.extend(attempt.reused_calls)
        try:
            parsed = _parse_choices(raw, batch)
        except Exception as error:
            if attempt is not None:
                attempt.record_validation(validator_version=ADJUDICATION_VERSION, error=error)
            raise
        if attempt is not None:
            attempt.record_validation(validator_version=ADJUDICATION_VERSION)
        choices.extend(parsed)
    return choices, sorted([*replayed_calls, *collector.records()], key=lambda item: item.call_id)


def build_alignment_boundary_packets(
    request: LocalizationAlignmentAdjudicationInput,
    *,
    encoder: MultilingualTextEncoder | None = None,
) -> list[LocalizationAlignmentBoundaryPacket]:
    if request.source_words:
        return _build_word_boundary_packets(request, encoder=encoder)
    source_index = {item.cue_id: index for index, item in enumerate(request.source_cues)}
    source_by_id = {item.cue_id: item for item in request.source_cues}
    target_by_id = {item.paragraph_id: item.text for item in request.target_paragraphs}
    blocks = request.alignment.blocks
    packets = []
    for boundary_index, (left, right) in enumerate(
        zip(blocks, blocks[1:]),
        start=1,
    ):
        if not (left.needs_adjudication or right.needs_adjudication):
            continue
        original = source_index[left.source_cue_ids[-1]]
        lower = max(
            source_index[left.source_cue_ids[0]],
            original - request.candidate_radius,
        )
        upper = min(
            source_index[right.source_cue_ids[-1]] - 1,
            original + request.candidate_radius,
        )
        candidate_indexes = list(range(lower, upper + 1))
        candidate_indexes = [
            split_index
            for split_index in candidate_indexes
            if _cue_split_preserves_evidence_anchors(
                request,
                left=left,
                right=right,
                split_index=split_index,
                source_index=source_index,
            )
            and source_window_is_single_speaker(
                request.source_cues[source_index[left.source_cue_ids[0]] : split_index + 1]
            )
            and source_window_is_single_speaker(
                request.source_cues[split_index + 1 : source_index[right.source_cue_ids[-1]] + 1]
            )
        ]
        if len(candidate_indexes) < 2:
            continue
        candidates = []
        for candidate_number, split_index in enumerate(
            candidate_indexes,
            start=1,
        ):
            left_ids = [item.cue_id for item in request.source_cues[max(0, split_index - 1) : split_index + 1]]
            right_ids = [item.cue_id for item in request.source_cues[split_index + 1 : split_index + 3]]
            candidates.append(
                LocalizationAlignmentBoundaryCandidate(
                    candidate_id=(f"candidate_{boundary_index:04d}_{candidate_number:02d}"),
                    split_after_cue_id=request.source_cues[split_index].cue_id,
                    left_tail_source=" ".join(source_by_id[item].text for item in left_ids),
                    right_head_source=" ".join(source_by_id[item].text for item in right_ids),
                    boundary_hint=("原 ASR 字幕边界" if split_index == original else ""),
                    is_original=split_index == original,
                )
            )
        packets.append(
            LocalizationAlignmentBoundaryPacket(
                boundary_id=f"boundary_{boundary_index:04d}",
                left_block_id=left.block_id,
                right_block_id=right.block_id,
                left_target_text=" ".join(target_by_id[item] for item in left.paragraph_ids),
                right_target_text=" ".join(target_by_id[item] for item in right.paragraph_ids),
                candidates=candidates,
            )
        )
    return packets


def _build_word_boundary_packets(
    request: LocalizationAlignmentAdjudicationInput,
    *,
    encoder: MultilingualTextEncoder | None,
) -> list[LocalizationAlignmentBoundaryPacket]:
    words = request.source_words
    word_index = {item.word_id: index for index, item in enumerate(words)}
    evidence_by_position = {item.position_after_word: item for item in request.source_boundaries}
    cue_by_word = {word_id: cue for cue in request.source_cues for word_id in cue.source_word_ids}
    source_by_id = {item.cue_id: item for item in request.source_cues}
    target_by_id = {item.paragraph_id: item.text for item in request.target_paragraphs}
    packets = []
    for boundary_index, (left, right) in enumerate(
        zip(request.alignment.blocks, request.alignment.blocks[1:]),
        start=1,
    ):
        if not (left.needs_adjudication or right.needs_adjudication):
            continue
        original_position = word_index[left.source_word_ids[-1]] + 1
        full_lower = word_index[left.source_word_ids[0]] + 1
        full_upper = word_index[right.source_word_ids[-1]]
        legal_positions = [
            position
            for position in range(full_lower, full_upper + 1)
            if _word_split_preserves_evidence_anchors(
                request,
                left=left,
                right=right,
                position=position,
                word_index=word_index,
            )
            and source_window_is_single_speaker(
                list(
                    source_by_id[cue_id]
                    for cue_id in dict.fromkeys(
                        cue_by_word[item.word_id].cue_id
                        for item in words[word_index[left.source_word_ids[0]] : position]
                    )
                )
            )
            and source_window_is_single_speaker(
                list(
                    source_by_id[cue_id]
                    for cue_id in dict.fromkeys(
                        cue_by_word[item.word_id].cue_id
                        for item in words[position : word_index[right.source_word_ids[-1]] + 1]
                    )
                )
            )
        ]
        supported = _select_word_boundary_candidates(
            request,
            left=left,
            right=right,
            legal_positions=legal_positions,
            original_position=original_position,
            word_index=word_index,
            evidence_by_position=evidence_by_position,
            target_by_id=target_by_id,
            encoder=encoder,
        )
        supported.sort()
        if len(supported) < 2:
            continue
        candidates = []
        for candidate_number, position in enumerate(supported, start=1):
            evidence = evidence_by_position.get(position)
            candidates.append(
                LocalizationAlignmentBoundaryCandidate(
                    candidate_id=(f"candidate_{boundary_index:04d}_{candidate_number:02d}"),
                    split_after_word_id=words[position - 1].word_id,
                    left_tail_source=join_source_words([item.text for item in words[max(0, position - 8) : position]]),
                    right_head_source=join_source_words(
                        [item.text for item in words[position : min(len(words), position + 8)]]
                    ),
                    boundary_hint=_boundary_hint(evidence),
                    is_original=position == original_position,
                )
            )
        packets.append(
            LocalizationAlignmentBoundaryPacket(
                boundary_id=f"boundary_{boundary_index:04d}",
                left_block_id=left.block_id,
                right_block_id=right.block_id,
                left_target_text=" ".join(target_by_id[item] for item in left.paragraph_ids),
                right_target_text=" ".join(target_by_id[item] for item in right.paragraph_ids),
                candidates=candidates,
            )
        )
    return packets


def _select_word_boundary_candidates(
    request: LocalizationAlignmentAdjudicationInput,
    *,
    left: LocalizationSemanticAlignmentBlock,
    right: LocalizationSemanticAlignmentBlock,
    legal_positions: list[int],
    original_position: int,
    word_index: dict[str, int],
    evidence_by_position: dict[int, SourceBoundaryEvidence],
    target_by_id: dict[str, str],
    encoder: MultilingualTextEncoder | None,
) -> list[int]:
    if len(legal_positions) < 2:
        return legal_positions
    selected = [original_position]
    if encoder is not None:
        start = word_index[left.source_word_ids[0]]
        end = word_index[right.source_word_ids[-1]] + 1
        left_target = " ".join(target_by_id[item] for item in left.paragraph_ids)
        right_target = " ".join(target_by_id[item] for item in right.paragraph_ids)
        candidate_pairs = [
            (
                join_source_words([item.text for item in request.source_words[start:position]]),
                join_source_words([item.text for item in request.source_words[position:end]]),
            )
            for position in legal_positions
        ]
        texts = [left_target, right_target]
        for left_text, right_text in candidate_pairs:
            texts.extend([left_text, right_text])
        unique = list(dict.fromkeys(texts))
        vectors = np.asarray(encoder.encode(unique), dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(unique):
            raise ValueError("跨语言向量模型返回了无效边界候选结果。")
        vectors /= np.maximum(
            np.linalg.norm(vectors, axis=1, keepdims=True),
            1e-12,
        )
        vector_by_text = dict(zip(unique, vectors))
        scored = sorted(
            (
                float(
                    np.dot(vector_by_text[left_target], vector_by_text[left_text])
                    + np.dot(
                        vector_by_text[right_target],
                        vector_by_text[right_text],
                    )
                ),
                position,
            )
            for position, (left_text, right_text) in zip(
                legal_positions,
                candidate_pairs,
            )
        )
        for _score, position in reversed(scored[-3:]):
            if position not in selected:
                selected.append(position)
    else:
        nearby = [
            position
            for position in legal_positions
            if abs(position - original_position) <= request.candidate_word_radius
        ]
        selected.extend(
            position
            for position in nearby
            if position != original_position
            and ((evidence := evidence_by_position.get(position)) is not None and evidence.eligible)
        )
    supported = sorted(
        (
            evidence
            for position in legal_positions
            if (evidence := evidence_by_position.get(position)) is not None and evidence.eligible
        ),
        key=lambda item: item.score,
        reverse=True,
    )
    for evidence in supported:
        if evidence.position_after_word not in selected:
            selected.append(evidence.position_after_word)
        if len(selected) >= 5:
            break
    return selected[:5]


def _boundary_hint(evidence: SourceBoundaryEvidence | None) -> str:
    if evidence is None:
        return "无额外停顿证据"
    parts = []
    if evidence.pause_ms >= 250:
        parts.append(f"约 {evidence.pause_ms}ms 停顿")
    if evidence.punctuation == "terminal":
        parts.append("源文句末")
    elif evidence.punctuation == "clause":
        parts.append("源文分句")
    if evidence.cue_boundary:
        parts.append("ASR 字幕边界")
    return "；".join(parts) or "无额外停顿证据"


def _cue_split_preserves_evidence_anchors(
    request: LocalizationAlignmentAdjudicationInput,
    *,
    left: LocalizationSemanticAlignmentBlock,
    right: LocalizationSemanticAlignmentBlock,
    split_index: int,
    source_index: dict[str, int],
) -> bool:
    left_paragraphs = set(left.paragraph_ids)
    right_paragraphs = set(right.paragraph_ids)
    for anchor in request.alignment.evidence_anchors:
        indexes = [source_index[item] for item in anchor.source_cue_ids]
        if (anchor.target_paragraph_id in left_paragraphs and max(indexes) > split_index) or (
            anchor.target_paragraph_id in right_paragraphs and min(indexes) <= split_index
        ):
            return False
    return True


def _word_split_preserves_evidence_anchors(
    request: LocalizationAlignmentAdjudicationInput,
    *,
    left: LocalizationSemanticAlignmentBlock,
    right: LocalizationSemanticAlignmentBlock,
    position: int,
    word_index: dict[str, int],
) -> bool:
    cue_by_id = {item.cue_id: item for item in request.source_cues}
    left_paragraphs = set(left.paragraph_ids)
    right_paragraphs = set(right.paragraph_ids)
    for anchor in request.alignment.evidence_anchors:
        first_word = cue_by_id[anchor.source_cue_ids[0]].source_word_ids[0]
        last_word = cue_by_id[anchor.source_cue_ids[-1]].source_word_ids[-1]
        if (anchor.target_paragraph_id in left_paragraphs and word_index[last_word] >= position) or (
            anchor.target_paragraph_id in right_paragraphs and word_index[first_word] < position
        ):
            return False
    return True


def _parse_choices(
    raw: dict,
    packets: list[LocalizationAlignmentBoundaryPacket],
) -> list[LocalizationAlignmentBoundaryChoice]:
    packet_by_id = {item.boundary_id: item for item in packets}
    raw_choices = raw.get("choices")
    if not isinstance(raw_choices, list):
        raise ValueError("语义时间裁决没有返回 choices。")
    choices = []
    for value in raw_choices:
        try:
            choice = LocalizationAlignmentBoundaryChoice.model_validate(value)
        except ValidationError as exc:
            raise ValueError("语义时间裁决返回了无效选择。") from exc
        packet = packet_by_id.get(choice.boundary_id)
        if packet is None or choice.candidate_id not in {item.candidate_id for item in packet.candidates}:
            raise ValueError("语义时间裁决选择了程序未提供的候选。")
        if not choice.applied or choice.fallback_reason is not None:
            raise ValueError("语义时间裁决不能自行声明回退或跳过选择。")
        choices.append(choice)
    if {item.boundary_id for item in choices} != set(packet_by_id):
        raise ValueError("语义时间裁决没有覆盖全部待判断边界。")
    return choices


def _apply_choices(
    request: LocalizationAlignmentAdjudicationInput,
    packets: list[LocalizationAlignmentBoundaryPacket],
    choices: list[LocalizationAlignmentBoundaryChoice],
) -> tuple[
    list[LocalizationSemanticAlignmentBlock],
    list[LocalizationAlignmentBoundaryChoice],
]:
    if request.source_words:
        return _apply_word_choices(request, packets, choices)
    source_ids = [item.cue_id for item in request.source_cues]
    source_index = {item: index for index, item in enumerate(source_ids)}
    packet_by_left = {item.left_block_id: item for item in packets}
    choice_by_boundary = {item.boundary_id: item for item in choices}
    split_indexes = []
    applied = []
    previous = -1
    for block in request.alignment.blocks[:-1]:
        packet = packet_by_left.get(block.block_id)
        original = source_index[block.source_cue_ids[-1]]
        if packet is None:
            chosen = original
        else:
            choice = choice_by_boundary[packet.boundary_id]
            candidate = next(item for item in packet.candidates if item.candidate_id == choice.candidate_id)
            proposed = source_index[candidate.split_after_cue_id]
            if proposed <= previous:
                raise ValueError("语义时间裁决选择的候选会破坏时间顺序。")
            else:
                chosen = proposed
                applied.append(choice)
        split_indexes.append(chosen)
        previous = chosen
    boundaries = [-1, *split_indexes, len(source_ids) - 1]
    source_by_id = {item.cue_id: item for item in request.source_cues}
    rebuilt = []
    for index, block in enumerate(request.alignment.blocks):
        cue_ids = source_ids[boundaries[index] + 1 : boundaries[index + 1] + 1]
        if not cue_ids:
            raise ValueError("语义时间裁决产生了空的英文时间块。")
        words = [word_id for cue_id in cue_ids for word_id in source_by_id[cue_id].source_word_ids]
        rebuilt.append(
            block.model_copy(
                update={
                    "source_cue_ids": cue_ids,
                    "source_word_ids": words,
                    "source_start_ms": source_by_id[cue_ids[0]].start_ms,
                    "source_end_ms": source_by_id[cue_ids[-1]].end_ms,
                    "reason_codes": list(dict.fromkeys([*block.reason_codes, "llm_boundary_adjudicated"])),
                }
            )
        )
    return enforce_semantic_time_invariants(rebuilt), applied


def _apply_word_choices(
    request: LocalizationAlignmentAdjudicationInput,
    packets: list[LocalizationAlignmentBoundaryPacket],
    choices: list[LocalizationAlignmentBoundaryChoice],
) -> tuple[
    list[LocalizationSemanticAlignmentBlock],
    list[LocalizationAlignmentBoundaryChoice],
]:
    words = request.source_words
    word_index = {item.word_id: index for index, item in enumerate(words)}
    packet_by_left = {item.left_block_id: item for item in packets}
    choice_by_boundary = {item.boundary_id: item for item in choices}
    split_positions = []
    applied = []
    previous = 0
    for block in request.alignment.blocks[:-1]:
        packet = packet_by_left.get(block.block_id)
        original = word_index[block.source_word_ids[-1]] + 1
        chosen = original
        if packet is not None:
            choice = choice_by_boundary[packet.boundary_id]
            candidate = next(item for item in packet.candidates if item.candidate_id == choice.candidate_id)
            proposed = word_index[str(candidate.split_after_word_id)] + 1
            if proposed <= previous:
                raise ValueError("语义时间裁决选择的候选会破坏逐词时间顺序。")
            else:
                chosen = proposed
                applied.append(choice)
        split_positions.append(chosen)
        previous = chosen
    if any(left >= right for left, right in zip(split_positions, split_positions[1:])):
        raise ValueError("语义时间裁决产生了倒序逐词边界。")
    boundaries = [0, *split_positions, len(words)]
    cue_by_word = {word_id: cue.cue_id for cue in request.source_cues for word_id in cue.source_word_ids}
    rebuilt = []
    for index, block in enumerate(request.alignment.blocks):
        block_words = words[boundaries[index] : boundaries[index + 1]]
        if not block_words:
            raise ValueError("语义时间裁决产生了空的英文逐词时间块。")
        rebuilt.append(
            block.model_copy(
                update={
                    "source_cue_ids": list(dict.fromkeys(cue_by_word[item.word_id] for item in block_words)),
                    "source_word_ids": [item.word_id for item in block_words],
                    "source_start_ms": block_words[0].start_ms,
                    "source_end_ms": block_words[-1].end_ms,
                    "reason_codes": list(dict.fromkeys([*block.reason_codes, "llm_boundary_adjudicated"])),
                }
            )
        )
    return enforce_semantic_time_invariants(rebuilt), applied


def _validate_input_coverage(
    request: LocalizationAlignmentAdjudicationInput,
) -> None:
    _validate_output_coverage(request, request.alignment.blocks)


def _validate_output_coverage(
    request: LocalizationAlignmentAdjudicationInput,
    blocks: list[LocalizationSemanticAlignmentBlock],
) -> None:
    expected_sources = [item.cue_id for item in request.source_cues]
    actual_sources = list(dict.fromkeys(item for block in blocks for item in block.source_cue_ids))
    expected_targets = [item.paragraph_id for item in request.target_paragraphs]
    actual_targets = [item for block in blocks for item in block.paragraph_ids]
    if actual_sources != expected_sources:
        raise ValueError("语义时间裁决破坏了英文时间的完整覆盖或顺序。")
    validate_speaker_safe_alignment(
        request.source_cues,
        blocks,
    )
    if request.source_words:
        expected_words = [item.word_id for item in request.source_words]
        actual_words = [item for block in blocks for item in block.source_word_ids]
        if actual_words != expected_words:
            raise ValueError("语义时间裁决破坏了英文逐词完整覆盖或顺序。")
    if actual_targets != expected_targets:
        raise ValueError("语义时间裁决破坏了中文段落的完整覆盖或顺序。")
    block_by_paragraph = {paragraph_id: block for block in blocks for paragraph_id in block.paragraph_ids}
    for anchor in request.alignment.evidence_anchors:
        block = block_by_paragraph.get(anchor.target_paragraph_id)
        if block is None or not set(anchor.source_cue_ids).issubset(block.source_cue_ids):
            raise ValueError("语义时间裁决破坏了已确认的中英文归属。")


def _build_result(
    request: LocalizationAlignmentAdjudicationInput,
    *,
    packets: list[LocalizationAlignmentBoundaryPacket],
    choices: list[LocalizationAlignmentBoundaryChoice],
    blocks: list[LocalizationSemanticAlignmentBlock],
    calls: list[VideoLocalizationLlmCallRecord],
    status: Literal["not_needed", "passed", "warning"],
) -> LocalizationAlignmentAdjudicationResult:
    payload = {
        "alignment": request.alignment.result_fingerprint,
        "packets": [item.model_dump(mode="json") for item in packets],
        "choices": [item.model_dump(mode="json") for item in choices],
        "blocks": [item.model_dump(mode="json") for item in blocks],
    }
    return LocalizationAlignmentAdjudicationResult(
        alignment_fingerprint=request.alignment.result_fingerprint,
        spoken_script_fingerprint=(request.alignment.spoken_script_fingerprint),
        result_fingerprint=_fingerprint(payload),
        packets=packets,
        choices=choices,
        blocks=blocks,
        route=request.route,
        llm_calls=calls,
        quality_summary=LocalizationAlignmentAdjudicationQualitySummary(
            status=status,
            candidate_boundary_count=len(packets),
            applied_choice_count=sum(item.applied for item in choices),
            fallback_choice_count=sum(not item.applied for item in choices),
            target_coverage_complete=True,
            source_coverage_complete=True,
            source_order_preserved=True,
            model_call_count=len(calls),
        ),
    )


def project_localization_alignment_adjudication_result(
    result: LocalizationAlignmentAdjudicationResult,
) -> dict:
    quality = result.quality_summary
    choice_by_boundary = {item.boundary_id: item for item in result.choices}
    result_items = []
    for packet in result.packets:
        choice = choice_by_boundary.get(packet.boundary_id)
        if choice is None:
            continue
        selected = next(
            (item for item in packet.candidates if item.candidate_id == choice.candidate_id),
            None,
        )
        if not choice.applied:
            selected = next(
                (item for item in packet.candidates if item.is_original),
                selected,
            )
        if selected is None:
            continue
        reason = choice.reason_zh
        if choice.fallback_reason:
            reason = f"{reason}\n\n{choice.fallback_reason}"
        result_items.append(
            {
                "title": (f"语义边界 {packet.boundary_id.removeprefix('boundary_')}"),
                "text": reason,
                "before_label": "左侧语义及对应英文结尾",
                "before": (f"{packet.left_target_text}\n\n{selected.left_tail_source}"),
                "after_label": "右侧语义及对应英文开头",
                "after": (f"{packet.right_target_text}\n\n{selected.right_head_source}"),
                "meta": ("已采用模型选择" if choice.applied else "模型建议未通过程序校验，已保留本地结果"),
                "facts": [],
                "links": [],
                "tone": "positive" if choice.applied else "warning",
            }
        )
    return {
        "label": "复核时间歧义",
        "order": 100,
        "status": ("warning" if quality.status == "warning" else "success"),
        "purpose": ("只在低把握边界中选择程序给出的候选；不能重写中文、添加时间或破坏完整覆盖。"),
        "summary": (
            "没有低把握边界，无需调用模型。"
            if quality.status == "not_needed"
            else (f"复核 {quality.candidate_boundary_count} 个边界，采用 {quality.applied_choice_count} 个调整。")
        ),
        "metrics": [
            {
                "label": "候选边界",
                "value": str(quality.candidate_boundary_count),
            },
            {
                "label": "采用调整",
                "value": str(quality.applied_choice_count),
            },
            {
                "label": "保守回退",
                "value": str(quality.fallback_choice_count),
            },
        ],
        "sections": (
            [
                {
                    "title": "边界复核结果",
                    "open_by_default": True,
                    "items": result_items,
                }
            ]
            if result_items
            else []
        ),
        "debug": {
            "description": ("用于核对歧义边界是否只发送了必要文字和候选；模型身份与调用用量显示在下方统一调用记录中。"),
            "metrics": [
                {
                    "label": "提示词版本",
                    "value": ALIGNMENT_ADJUDICATION_PROMPT_VERSION,
                },
                {
                    "label": "请求契约",
                    "value": (ALIGNMENT_ADJUDICATION_REQUEST_CONTRACT_VERSION),
                },
                {
                    "label": "待裁决边界",
                    "value": str(quality.candidate_boundary_count),
                },
            ],
            "sections": [
                {
                    "title": "请求边界",
                    "items": [
                        {
                            "title": "只发送必要文字",
                            "text": ("每个歧义边界只发送相邻两段目标语言语义，以及 2 至 5 个附近源语言文字候选。"),
                            "meta": ("不发送全文、时间戳、逐词时间、cue ID、相似度、内部块 ID、指纹或原候选标记"),
                            "tone": "neutral",
                            "facts": [],
                            "links": [],
                        }
                    ],
                }
            ],
        },
    }


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
    "LocalizationAlignmentBoundaryCandidate",
    "LocalizationAlignmentBoundaryChoice",
    "LocalizationAlignmentBoundaryPacket",
    "LocalizationAlignmentAdjudicationInput",
    "LocalizationAlignmentAdjudicationResult",
    "adjudicate_localization_alignment",
    "adjudicate_alignment_boundary_packets",
    "build_alignment_boundary_packets",
    "project_localization_alignment_adjudication_result",
]
