"""Deterministic planning and CQC rules for localized dubbing.

This module contains no provider calls, persistence or UI policy.  It accepts
versioned evidence, returns versioned decisions, and leaves semantic listening
judgements explicitly unreviewed until an Agent or human supplies evidence.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
import re
import statistics
import unicodedata

from pypinyin import Style, lazy_pinyin

from app.domains.video_localization import timeline_clip_timing
from app.domains.video_localization import dubbing_content_integrity
from app.services import text_normalizer
from app.schemas.video_localization_dubbing_production import (
    DubbingBoundaryEvidence,
    DubbingCandidateCqcInput,
    DubbingCandidateCqcReport,
    DubbingGenerationGroup,
    DubbingGenerationPlan,
    DubbingGenerationPlanInput,
    DubbingQualityFinding,
    DubbingProductionSnapshot,
    DubbingSemanticUnit,
    DubbingSpeechIsland,
    DubbingTimelineAuditInput,
    DubbingTimelineAuditReport,
    DubbingTimelineClipSplitCommand,
    DubbingTimelineClip,
    DubbingTimelineExpectedUnit,
    DubbingTranscriptComparison,
    ReviewStatus,
)




_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[a-z0-9]+(?:['’-][a-z0-9]+)*")
_SUBJECTIVE_DIMENSIONS = {
    "meaning",
    "pronunciation",
    "prosody_parse",
    "voice_match",
    "naturalness",
}
_DEFAULT_MAXIMUM_GROUP_CHARACTERS = 180
_DEFAULT_MAXIMUM_GROUP_SUBTITLES = 3
_MINIMUM_LONG_SILENCE_MS = 600
_STRONG_SENTENCE_END = re.compile(r"[。！？!?；;][”’\"']?$")
_UNNATURAL_CARDINAL_TEN = re.compile(
    r"百分之一十|(?<![零〇一二两三四五六七八九十百千万亿兆点周期拜])"
    r"一十(?=$|[一二三四五六七八九百千万亿兆元人个次年月日号倍股吨米厘斤岁件项家台套份笔场章页集期条张座辆所名位])"
)


@dataclass(frozen=True)
class DubbingContinuousBoundaryUnderfill:
    """One source-continuous boundary whose dub leaves excess silence."""

    left_group_id: str
    left_clip_id: str
    right_clip_id: str
    source_gap_ms: int
    timeline_gap_ms: int
    allowed_gap_ms: int

    @property
    def excess_gap_ms(self) -> int:
        return max(0, self.timeline_gap_ms - self.allowed_gap_ms)


def split_planned_timeline_clips(
    *,
    timeline_clips: list[dict],
    commands: list[DubbingTimelineClipSplitCommand],
    groups: list[DubbingGenerationGroup | dict],
    localized_subtitles: list[object],
    boundaries: list[DubbingBoundaryEvidence | dict],
) -> list[dict]:
    """Partition frozen candidate audio at verified acoustic boundaries."""

    def value(item, key: str, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    groups_by_id = {str(value(group, "group_id", "")): group for group in groups}
    groups_by_subtitles = {tuple(str(item) for item in value(group, "subtitle_ids", [])): group for group in groups}
    subtitles_by_id = {str(value(item, "subtitle_id", "")): item for item in localized_subtitles}
    commands_by_clip_id = {command.clip_id: command for command in commands}
    existing_clip_ids = {str(clip.get("clip_id") or "") for clip in timeline_clips}
    original_clip_by_id = {
        str(clip.get("clip_id") or ""): dict(clip)
        for clip in timeline_clips
        if str(clip.get("clip_id") or "")
    }
    affected_group_ids: set[str] = set()
    output: list[dict] = []
    handled: set[str] = set()

    for raw in timeline_clips:
        clip = dict(raw)
        clip_id = str(clip.get("clip_id") or "")
        command = commands_by_clip_id.get(clip_id)
        if command is None:
            output.append(clip)
            continue
        handled.add(clip_id)
        candidate_id = str(clip.get("candidate_id") or clip.get("result_id") or "")
        if candidate_id != command.candidate_id:
            raise ValueError(f"配音片段 {clip_id} 的候选身份已经变化。")
        group = groups_by_id.get(str(clip.get("dubbing_group_id") or ""))
        original_targets = tuple(str(item) for item in clip.get("target_subtitle_ids") or [])
        if group is None:
            group = groups_by_subtitles.get(original_targets)
        if group is None:
            raise ValueError(f"配音片段 {clip_id} 不属于当前生成计划。")
        group_id = str(value(group, "group_id", ""))
        affected_group_ids.add(group_id)
        if not original_targets:
            original_targets = tuple(str(item) for item in value(group, "subtitle_ids", []))
        flattened_targets = tuple(
            subtitle_id
            for item in command.slices
            for subtitle_id in item.target_subtitle_ids
        )
        repeated_group_binding = all(
            tuple(str(item) for item in slice_item.target_subtitle_ids)
            == original_targets
            for slice_item in command.slices
        )
        if flattened_targets != original_targets and not repeated_group_binding:
            raise ValueError(
                f"配音片段 {clip_id} 的切片没有按原顺序完整覆盖目标字幕，"
                "也没有在每个声学切片上保留同一组级字幕绑定。"
            )
        flattened_word_ids = [word_id for item in command.slices for word_id in item.alignment_word_ids]
        if len(flattened_word_ids) != len(set(flattened_word_ids)):
            raise ValueError(f"配音片段 {clip_id} 的声学字词证据发生重复。")

        original_source_start_ms = int(clip.get("source_start_ms") or 0)
        original_source_end_ms = int(
            clip.get("source_end_ms")
            or original_source_start_ms + int(clip.get("end_ms") or 0) - int(clip.get("start_ms") or 0)
        )
        first_slice = command.slices[0]
        if first_slice.source_start_ms < original_source_start_ms:
            raise ValueError(f"配音片段 {clip_id} 的源裁切范围超出候选开头。")
        if (
            first_slice.source_start_ms > original_source_start_ms
            and first_slice.speech_start_ms - first_slice.source_start_ms < 80
        ):
            raise ValueError(
                f"配音片段 {clip_id} 的开头只能删除已验证静音，必须保留至少 80 毫秒安全余量。"
            )
        for left, right in zip(command.slices, command.slices[1:]):
            if right.source_start_ms < left.source_end_ms:
                raise ValueError(f"配音片段 {clip_id} 的源裁切范围发生重叠。")
            left_safety_ms = left.source_end_ms - left.speech_end_ms
            right_safety_ms = right.speech_start_ms - right.source_start_ms
            if left_safety_ms < 80 or right_safety_ms < 80:
                raise ValueError(
                    f"配音片段 {clip_id} 只能删除已验证的内部静音，切点两侧必须各自保留至少 80 毫秒安全余量。"
                )
        if command.slices[-1].source_end_ms != original_source_end_ms:
            raise ValueError(f"配音片段 {clip_id} 的源裁切范围没有完整覆盖候选。")

        media_source_clip_id = str(clip.get("media_source_clip_id") or clip_id)
        original_timeline_start_ms = int(clip.get("start_ms") or 0)
        group_target_start_ms = int(value(group, "target_start_ms", 0))
        group_target_end_ms = int(value(group, "target_end_ms", 0))
        original_target_subtitles = [subtitles_by_id.get(subtitle_id) for subtitle_id in original_targets]
        if any(item is None for item in original_target_subtitles):
            raise ValueError(f"配音片段 {clip_id} 引用了不存在的目标字幕。")
        raw_target_start_ms = min(
            int(value(item, "start_ms", 0)) for item in original_target_subtitles if item is not None
        )
        raw_target_end_ms = max(
            int(value(item, "end_ms", raw_target_start_ms + 1))
            for item in original_target_subtitles
            if item is not None
        )
        raw_target_duration_ms = max(
            1,
            raw_target_end_ms - raw_target_start_ms,
        )
        group_target_duration_ms = max(
            1,
            group_target_end_ms - group_target_start_ms,
        )

        def scaled_target_ms(raw_ms: int) -> int:
            ratio = (raw_ms - raw_target_start_ms) / raw_target_duration_ms
            return round(group_target_start_ms + ratio * group_target_duration_ms)

        slice_count = len(command.slices)
        for index, item in enumerate(command.slices, start=1):
            target_subtitles = [subtitles_by_id.get(subtitle_id) for subtitle_id in item.target_subtitle_ids]
            if any(value is None for value in target_subtitles):
                raise ValueError(f"配音片段 {clip_id} 的切片引用了不存在的目标字幕。")
            slice_clip_id = clip_id
            if index > 1:
                base_id = f"{clip_id}__part_{index:03d}"
                slice_clip_id = base_id
                suffix = 2
                while slice_clip_id in existing_clip_ids:
                    slice_clip_id = f"{base_id}_{suffix}"
                    suffix += 1
                existing_clip_ids.add(slice_clip_id)
            duration_ms = item.source_end_ms - item.source_start_ms
            timeline_start_ms = original_timeline_start_ms + item.source_start_ms - original_source_start_ms
            raw_slice_start_ms = min(
                int(value(subtitle, "start_ms", 0)) for subtitle in target_subtitles if subtitle is not None
            )
            raw_slice_end_ms = max(
                int(value(subtitle, "end_ms", raw_slice_start_ms + 1))
                for subtitle in target_subtitles
                if subtitle is not None
            )
            target_start_ms = scaled_target_ms(raw_slice_start_ms)
            target_end_ms = max(
                target_start_ms + 1,
                scaled_target_ms(raw_slice_end_ms),
            )
            # Timeline targets belong to the versioned dubbing plan; switching
            # back to raw subtitle times can move speech away from its semantic
            # anchor or reopen a continuous-speech gap.
            spoken_text = "".join(
                str(value(subtitle, "tts_text", None) or value(subtitle, "text", ""))
                for subtitle in target_subtitles
                if subtitle is not None
            ).strip()
            sliced = dict(clip)
            sliced.update(
                {
                    "clip_id": slice_clip_id,
                    "media_source_clip_id": media_source_clip_id,
                    "subtitle_id": item.target_subtitle_ids[0],
                    "target_subtitle_ids": list(item.target_subtitle_ids),
                    "target_start_ms": target_start_ms,
                    "target_end_ms": target_end_ms,
                    "tts_target_text": spoken_text,
                    "dubbing_group_id": group_id,
                    "dubbing_slice_index": index,
                    "dubbing_slice_count": slice_count,
                    "dubbing_alignment_word_ids": list(item.alignment_word_ids),
                    "dubbing_timeline_gap_before_ms": item.timeline_gap_before_ms,
                    "source_start_ms": item.source_start_ms,
                    "source_end_ms": item.source_end_ms,
                    "speech_onset_ms": item.speech_start_ms,
                    "alignment_lead_ms": (item.speech_start_ms - item.source_start_ms),
                    "alignment_trail_ms": (item.source_end_ms - item.speech_end_ms),
                    "start_ms": timeline_start_ms,
                    "end_ms": timeline_start_ms + duration_ms,
                }
            )
            output.append(sliced)

    missing_commands = sorted(set(commands_by_clip_id) - handled)
    if missing_commands:
        raise ValueError("没有找到要切分的正式配音片段：" + ", ".join(missing_commands))
    rebalanced = rebalance_planned_timeline_clips(
        timeline_clips=output,
        groups=groups,
        boundaries=boundaries,
    )
    for clip in rebalanced:
        clip_id = str(clip.get("clip_id") or "")
        original_clip = original_clip_by_id.get(clip_id)
        if (
            original_clip is None
            or clip_id in commands_by_clip_id
        ):
            continue
        for field in (
            "start_ms",
            "end_ms",
            "source_start_ms",
            "source_end_ms",
            "alignment_lead_ms",
            "alignment_trail_ms",
            "speech_onset_ms",
            "dub_lane",
        ):
            if field in original_clip:
                clip[field] = original_clip[field]
            else:
                clip.pop(field, None)

    # Splitting owns only the selected candidate.  Rebalancing may model a
    # continuous chain with adjacent accepted groups, but restoring those
    # fixed neighbours can otherwise leave the newly split candidate at the
    # chain's provisional position and create an overlap.  Acoustic slices
    # that still represent one subtitle are compacted at the candidate start.
    # Slices bound to different subtitles instead keep their planned semantic
    # starts (clamped only to prevent overlap).  Blindly compacting both kinds
    # made later sentences sound early and erased intentional phrase breaks.
    rebalanced_by_group: dict[str, list[dict]] = {}
    for clip in rebalanced:
        group_id = str(clip.get("dubbing_group_id") or "")
        if group_id in affected_group_ids:
            rebalanced_by_group.setdefault(group_id, []).append(clip)
    for command in commands:
        original = original_clip_by_id[command.clip_id]
        original_source_start_ms = int(original.get("source_start_ms") or 0)
        original_source_end_ms = int(
            original.get("source_end_ms")
            or original_source_start_ms
            + int(original.get("end_ms") or 0)
            - int(original.get("start_ms") or 0)
        )
        group_id = str(original.get("dubbing_group_id") or "")
        if not group_id:
            original_group = groups_by_subtitles.get(
                tuple(
                    str(item)
                    for item in original.get("target_subtitle_ids") or []
                )
            )
            group_id = str(value(original_group, "group_id", ""))
        slices = sorted(
            (
                clip
                for clip in rebalanced_by_group.get(group_id, [])
                if str(
                    clip.get("candidate_id")
                    or clip.get("result_id")
                    or ""
                )
                == command.candidate_id
                and int(clip.get("source_start_ms") or 0)
                >= original_source_start_ms
                and int(
                    clip.get("source_end_ms")
                    or int(clip.get("source_start_ms") or 0)
                    + int(clip.get("end_ms") or 0)
                    - int(clip.get("start_ms") or 0)
                )
                <= original_source_end_ms
            ),
            key=lambda item: (
                int(item.get("source_start_ms") or 0),
                int(item.get("dubbing_slice_index") or 0),
            ),
        )
        repeated_group_binding = all(
            tuple(str(item) for item in slice_item.target_subtitle_ids)
            == tuple(str(item) for item in command.slices[0].target_subtitle_ids)
            for slice_item in command.slices
        )
        # A leading safe crop removes pre-speech padding.  Its first slice must
        # move right by the same source amount so the first spoken word keeps
        # its existing timeline anchor.  Internal cuts, unlike this edge crop,
        # intentionally compact later slices.
        cursor = int(original.get("start_ms") or 0) + max(
            0,
            int(command.slices[0].source_start_ms) - original_source_start_ms,
        )
        for slice_index, clip in enumerate(slices):
            duration_ms = int(clip.get("source_end_ms") or 0) - int(
                clip.get("source_start_ms") or 0
            )
            requested_gap_ms = (
                int(command.slices[slice_index].timeline_gap_before_ms)
                if repeated_group_binding and slice_index < len(command.slices)
                else 0
            )
            desired_start_ms = (
                cursor + requested_gap_ms
                if repeated_group_binding
                else int(clip.get("target_start_ms") or cursor)
                - max(0, int(clip.get("alignment_lead_ms") or 0))
            )
            start_ms = max(cursor, desired_start_ms)
            clip["start_ms"] = start_ms
            clip["end_ms"] = start_ms + duration_ms
            if repeated_group_binding:
                clip["dub_lane"] = int(original.get("dub_lane") or 0)
            cursor = start_ms + duration_ms

    review_group_ids = {
        str(clip.get("dubbing_group_id") or "")
        for clip in rebalanced
        if clip.get("manual_review_reason_codes")
        and str(clip.get("dubbing_group_id") or "") in affected_group_ids
    }
    for group_id in review_group_ids:
        slices = sorted(
            (
                clip
                for clip in rebalanced
                if str(clip.get("dubbing_group_id") or "") == group_id
            ),
            key=lambda item: int(item.get("dubbing_slice_index") or 0),
        )
        if not slices:
            continue
        block_end_ms = max(int(clip.get("end_ms") or 0) for clip in slices)
        next_start_ms = min(
            (
                int(other.get("start_ms") or 0)
                for other in rebalanced
                if str(other.get("dubbing_group_id") or "") != group_id
                and str(other.get("track_id") or "dub")
                == str(slices[0].get("track_id") or "dub")
                and int(other.get("dub_lane") or 0)
                == int(slices[0].get("dub_lane") or 0)
                and int(other.get("start_ms") or 0) >= block_end_ms
            ),
            default=None,
        )
        shift_limits = [
            int(clip.get("target_end_ms") or 0)
            - max(0, int(clip.get("alignment_lead_ms") or 0))
            - 1
            - int(clip.get("start_ms") or 0)
            for clip in slices
        ]
        if next_start_ms is not None:
            shift_limits.append(next_start_ms - block_end_ms)
        shift_ms = max(0, min(shift_limits, default=0))
        if not shift_ms:
            continue
        for clip in slices:
            clip["start_ms"] = int(clip.get("start_ms") or 0) + shift_ms
            clip["end_ms"] = int(clip.get("end_ms") or 0) + shift_ms
    return rebalanced


def rebalance_planned_timeline_clips(
    *,
    timeline_clips: list[dict],
    groups: list[DubbingGenerationGroup | dict],
    boundaries: list[DubbingBoundaryEvidence | dict],
    maximum_block_size: int | None = None,
) -> list[dict]:
    """Compact continuous planned dubbing clips around source-backed pauses.

    Generated audio and source crops stay unchanged. Only timeline placement
    moves, and each audible span must continue to overlap its semantic target.
    Source-backed pauses and speaker or scene changes split the edit chain.
    """

    def value(item, key: str, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    group_by_id = {str(value(group, "group_id", "")): group for group in groups}
    group_by_subtitles = {frozenset(str(item) for item in value(group, "subtitle_ids", [])): group for group in groups}
    boundary_by_units = {
        (
            str(value(boundary, "left_unit_id", "")),
            str(value(boundary, "right_unit_id", "")),
        ): boundary
        for boundary in boundaries
    }
    updated = [dict(clip) for clip in timeline_clips]
    entries: list[dict] = []
    for index, clip in enumerate(updated):
        group = group_by_id.get(str(clip.get("dubbing_group_id") or ""))
        if group is None:
            group = group_by_subtitles.get(frozenset(str(item) for item in clip.get("target_subtitle_ids", [])))
        if group is None:
            continue
        entries.append(
            {
                "index": index,
                "clip": clip,
                "group": group,
                "target_start_ms": int(
                    clip.get("target_start_ms")
                    if clip.get("target_start_ms") is not None
                    else value(group, "target_start_ms", 0)
                ),
                "target_end_ms": int(
                    clip.get("target_end_ms")
                    if clip.get("target_end_ms") is not None
                    else value(group, "target_end_ms", 0)
                ),
            }
        )
    entries.sort(
        key=lambda entry: (
            entry["target_start_ms"],
            entry["target_end_ms"],
            str(entry["clip"].get("clip_id") or ""),
        )
    )
    if not entries:
        return updated

    def same_candidate_slice_block(left: dict, right: dict) -> bool:
        left_clip = left["clip"]
        right_clip = right["clip"]
        return (
            int(left_clip.get("dubbing_slice_count") or 1) > 1
            and int(right_clip.get("dubbing_slice_count") or 1) > 1
            and str(left_clip.get("media_source_clip_id") or "")
            == str(right_clip.get("media_source_clip_id") or "")
            and str(left_clip.get("dubbing_group_id") or "")
            == str(right_clip.get("dubbing_group_id") or "")
        )

    def starts_new_source_chain(left: dict, right: dict) -> bool:
        if same_candidate_slice_block(left, right):
            return False
        left_group = left["group"]
        right_group = right["group"]
        if str(value(left_group, "group_id", "")) == str(value(right_group, "group_id", "")):
            return False
        left_units = [str(item) for item in value(left_group, "unit_ids", [])]
        right_units = [str(item) for item in value(right_group, "unit_ids", [])]
        boundary = (
            boundary_by_units.get((left_units[-1], right_units[0]))
            if left_units and right_units
            else None
        )
        if boundary is None:
            return True
        if _source_backed_continuous_boundary(
            left_group,
            right_group,
            boundary,
        ):
            return False
        return True

    source_chains: list[list[dict]] = []
    current_chain: list[dict] = []
    for entry in entries:
        if current_chain and starts_new_source_chain(current_chain[-1], entry):
            source_chains.append(current_chain)
            current_chain = []
        if current_chain:
            left_group = current_chain[-1]["group"]
            right_group = entry["group"]
            left_units = [str(item) for item in value(left_group, "unit_ids", [])]
            right_units = [str(item) for item in value(right_group, "unit_ids", [])]
            boundary = (
                boundary_by_units.get((left_units[-1], right_units[0]))
                if left_units and right_units
                else None
            )
            entry["required_pause_before_ms"] = (
                max(0, int(value(boundary, "gap_ms", 0)))
                if boundary is not None
                and value(boundary, "pause_classification", "continuous")
                == "natural_pause"
                else 0
            )
        else:
            entry["required_pause_before_ms"] = 0
        current_chain.append(entry)
    if current_chain:
        source_chains.append(current_chain)

    def safe_chain_end(chain: list[dict], end: int) -> bool:
        return end == len(chain) or not same_candidate_slice_block(
            chain[end - 1],
            chain[end],
        )

    scheduled_chains: list[tuple[list[dict], list[dict]]] = []
    for source_chain in source_chains:
        cursor = 0
        while cursor < len(source_chain):
            scheduled: list[dict] | None = None
            chosen_end = cursor
            maximum_end = (
                len(source_chain)
                if maximum_block_size is None
                else min(
                    len(source_chain),
                    cursor + max(2, int(maximum_block_size)),
                )
            )
            for end in range(maximum_end, cursor, -1):
                if not safe_chain_end(source_chain, end):
                    continue
                scheduled = _schedule_timeline_rebalance_chain(
                    source_chain[cursor:end]
                )
                if scheduled is not None:
                    chosen_end = end
                    break
            if scheduled is None:
                chosen_end = cursor + 1
                while not safe_chain_end(source_chain, chosen_end):
                    chosen_end += 1
                scheduled = [
                    dict(entry["clip"])
                    for entry in source_chain[cursor:chosen_end]
                ]
            scheduled_chains.append(
                (source_chain[cursor:chosen_end], scheduled)
            )
            cursor = chosen_end

    def build_chain_blocks() -> list[dict]:
        result: list[dict] = []
        for chain_entries, scheduled in scheduled_chains:
            base_start_ms = int(scheduled[0].get("start_ms") or 0)
            lower_shift_ms = -10**15
            upper_shift_ms = 10**15
            for entry, clip in zip(chain_entries, scheduled):
                duration_ms = int(clip.get("end_ms") or 0) - int(
                    clip.get("start_ms") or 0
                )
                lead_ms = max(0, int(clip.get("alignment_lead_ms") or 0))
                trail_ms = max(0, int(clip.get("alignment_trail_ms") or 0))
                lower_ms = max(
                    0,
                    int(entry["target_start_ms"])
                    - max(1, duration_ms - trail_ms)
                    + 1,
                )
                upper_ms = int(entry["target_end_ms"]) - lead_ms - 1
                lower_shift_ms = max(
                    lower_shift_ms,
                    lower_ms - int(clip.get("start_ms") or 0),
                )
                upper_shift_ms = min(
                    upper_shift_ms,
                    upper_ms - int(clip.get("start_ms") or 0),
                )
            result.append(
                {
                    "entries": chain_entries,
                    "scheduled": scheduled,
                    "duration_ms": int(scheduled[-1].get("end_ms") or 0)
                    - base_start_ms,
                    "lower": base_start_ms + lower_shift_ms,
                    "upper": base_start_ms + upper_shift_ms,
                    "desired": base_start_ms,
                    "base_start_ms": base_start_ms,
                }
            )
        return result

    def schedule_chain_blocks(blocks: list[dict]) -> list[int] | None:
        if not blocks:
            return []
        earliest: list[int] = []
        for index, block in enumerate(blocks):
            start_ms = int(block["lower"])
            if index:
                start_ms = max(
                    start_ms,
                    earliest[index - 1]
                    + int(blocks[index - 1]["duration_ms"]),
                )
            if start_ms > int(block["upper"]):
                return None
            earliest.append(start_ms)
        latest = [0] * len(blocks)
        for index in range(len(blocks) - 1, -1, -1):
            block = blocks[index]
            start_ms = int(block["upper"])
            if index < len(blocks) - 1:
                start_ms = min(
                    start_ms,
                    latest[index + 1] - int(block["duration_ms"]),
                )
            if start_ms < int(block["lower"]):
                return None
            latest[index] = start_ms
        starts: list[int] = []
        for index, block in enumerate(blocks):
            start_ms = min(
                latest[index],
                max(earliest[index], int(block["desired"])),
            )
            if index:
                start_ms = max(
                    start_ms,
                    starts[index - 1]
                    + int(blocks[index - 1]["duration_ms"]),
                )
            starts.append(start_ms)
        return starts

    chain_blocks = build_chain_blocks()
    if schedule_chain_blocks(chain_blocks) is None:
        for _chain_entries, scheduled in scheduled_chains:
            last = scheduled[-1]
            trail_ms = max(0, int(last.get("alignment_trail_ms") or 0))
            trim_ms = max(0, trail_ms - 20)
            if not trim_ms:
                continue
            last["end_ms"] = int(last.get("end_ms") or 0) - trim_ms
            last["source_end_ms"] = int(last.get("source_end_ms") or 0) - trim_ms
            last["alignment_trail_ms"] = trail_ms - trim_ms
        chain_blocks = build_chain_blocks()

    block_segments: list[tuple[list[dict], list[int]]] = []
    cursor = 0
    while cursor < len(chain_blocks):
        chosen_end = cursor
        chosen_starts: list[int] | None = None
        for end in range(len(chain_blocks), cursor, -1):
            starts = schedule_chain_blocks(chain_blocks[cursor:end])
            if starts is not None:
                chosen_end = end
                chosen_starts = starts
                break
        if chosen_starts is None:
            chosen_end = cursor + 1
            chosen_starts = [int(chain_blocks[cursor]["desired"])]
        block_segments.append(
            (chain_blocks[cursor:chosen_end], chosen_starts)
        )
        cursor = chosen_end

    rendered_segments: list[list[dict]] = []
    for blocks, starts in block_segments:
        rendered: list[dict] = []
        for block, start_ms in zip(blocks, starts):
            shift_ms = start_ms - int(block["base_start_ms"])
            rendered.extend(
                {
                    **clip,
                    "start_ms": int(clip.get("start_ms") or 0) + shift_ms,
                    "end_ms": int(clip.get("end_ms") or 0) + shift_ms,
                    "dub_lane": 0,
                }
                for clip in block["scheduled"]
            )
        rendered_segments.append(rendered)

    entry_by_clip_id = {
        str(entry["clip"].get("clip_id") or ""): entry
        for entry in entries
    }
    previous_scheduled: list[dict] | None = None
    for scheduled in rendered_segments:
        if previous_scheduled and scheduled:
            left = previous_scheduled[-1]
            right = scheduled[0]
            overlap_ms = max(
                0,
                int(left.get("end_ms") or 0)
                - int(right.get("start_ms") or 0),
            )
            removable_trail_ms = min(
                overlap_ms,
                max(0, int(left.get("alignment_trail_ms") or 0)),
            )
            if removable_trail_ms:
                left["end_ms"] = int(left.get("end_ms") or 0) - removable_trail_ms
                left["source_end_ms"] = int(left.get("source_end_ms") or 0) - removable_trail_ms
                left["alignment_trail_ms"] = int(left.get("alignment_trail_ms") or 0) - removable_trail_ms
                overlap_ms -= removable_trail_ms
            removable_lead_ms = min(
                overlap_ms,
                max(0, int(right.get("alignment_lead_ms") or 0)),
            )
            if removable_lead_ms:
                right["start_ms"] = int(right.get("start_ms") or 0) + removable_lead_ms
                right["source_start_ms"] = int(right.get("source_start_ms") or 0) + removable_lead_ms
                right["alignment_lead_ms"] = int(right.get("alignment_lead_ms") or 0) - removable_lead_ms

        for left, right in zip(scheduled, scheduled[1:]):
            overlap_ms = max(
                0,
                int(left.get("end_ms") or 0) - int(right.get("start_ms") or 0),
            )
            if not overlap_ms:
                continue
            removable_lead_ms = min(
                overlap_ms,
                max(0, int(right.get("alignment_lead_ms") or 0)),
            )
            if removable_lead_ms:
                right["start_ms"] = int(right.get("start_ms") or 0) + removable_lead_ms
                right["source_start_ms"] = int(right.get("source_start_ms") or 0) + removable_lead_ms
                right["alignment_lead_ms"] = int(right.get("alignment_lead_ms") or 0) - removable_lead_ms

        for clip in scheduled:
            matching_entry = entry_by_clip_id[
                str(clip.get("clip_id") or "")
            ]
            updated[int(matching_entry["index"])] = clip
        previous_scheduled = scheduled
    return _forward_shift_overlapping_group_blocks(updated)


def rebalance_selected_group_timeline_clips(
    *,
    timeline_clips: list[dict],
    groups: list[DubbingGenerationGroup | dict],
    boundaries: list[DubbingBoundaryEvidence | dict],
    group_id: str,
    selected_start_anchor_ms: int | None = None,
) -> list[dict]:
    """Rebalance one generated group without moving any neighboring group.

    ``selected_start_anchor_ms`` pins the selected projection's first clip
    and preserves its checked source crops and relative slice positions.
    Close-out uses it after projecting measured source speech onto the target
    onset: a scheduler may reject an overlong take, but may not advance its
    first audible word merely to make the tail fit.
    """

    original = [dict(clip) for clip in timeline_clips]
    proposed = rebalance_planned_timeline_clips(
        timeline_clips=original,
        groups=groups,
        boundaries=boundaries,
    )
    proposed_target = [clip for clip in proposed if str(clip.get("dubbing_group_id") or "") == group_id]
    if not proposed_target:
        return original

    original_target = [clip for clip in original if str(clip.get("dubbing_group_id") or "") == group_id]
    desired_start_ms = min(int(clip.get("start_ms") or 0) for clip in (original_target or proposed_target))
    proposed_start_ms = min(int(clip.get("start_ms") or 0) for clip in proposed_target)
    proposed_end_ms = max(int(clip.get("end_ms") or 0) for clip in proposed_target)
    duration_ms = proposed_end_ms - proposed_start_ms

    lower_ms = 0
    upper_ms = 10**15
    for clip in proposed_target:
        clip_start_ms = int(clip.get("start_ms") or 0)
        clip_duration_ms = int(clip.get("end_ms") or 0) - clip_start_ms
        offset_ms = clip_start_ms - proposed_start_ms
        lead_ms = max(0, int(clip.get("alignment_lead_ms") or 0))
        trail_ms = max(0, int(clip.get("alignment_trail_ms") or 0))
        target_start_ms = int(clip.get("target_start_ms") or 0)
        target_end_ms = int(clip.get("target_end_ms") or 0)
        lower_ms = max(
            lower_ms,
            target_start_ms - max(1, clip_duration_ms - trail_ms) + 1 - offset_ms,
        )
        upper_ms = min(
            upper_ms,
            target_end_ms - lead_ms - 1 - offset_ms,
        )

    selected_target_start_ms = min(int(clip.get("target_start_ms") or 0) for clip in proposed_target)
    selected_target_end_ms = max(int(clip.get("target_end_ms") or 0) for clip in proposed_target)
    first_target_clip = min(
        proposed_target,
        key=lambda clip: int(clip.get("start_ms") or 0),
    )
    last_target_clip = max(
        proposed_target,
        key=lambda clip: int(clip.get("end_ms") or 0),
    )
    audible_duration_ms = max(
        0,
        int(last_target_clip.get("end_ms") or 0)
        - max(0, int(last_target_clip.get("alignment_trail_ms") or 0))
        - int(first_target_clip.get("start_ms") or 0)
        - max(0, int(first_target_clip.get("alignment_lead_ms") or 0)),
    )
    if audible_duration_ms <= selected_target_end_ms - selected_target_start_ms:
        desired_start_ms = max(
            desired_start_ms,
            selected_target_start_ms - max(0, int(first_target_clip.get("alignment_lead_ms") or 0)),
        )
    fixed_lane_clips = [
        clip
        for clip in original
        if str(clip.get("dubbing_group_id") or "") != group_id
        and int(clip.get("dub_lane") or 0) == 0
        and clip.get("target_start_ms") is not None
    ]
    previous_target_starts = [
        int(clip["target_start_ms"])
        for clip in fixed_lane_clips
        if int(clip["target_start_ms"]) < selected_target_start_ms
    ]
    next_target_starts = [
        int(clip["target_start_ms"])
        for clip in fixed_lane_clips
        if int(clip["target_start_ms"]) > selected_target_start_ms
    ]
    if previous_target_starts:
        previous_target_start_ms = max(previous_target_starts)
        lower_ms = max(
            lower_ms,
            max(
                int(clip.get("end_ms") or 0)
                for clip in fixed_lane_clips
                if int(clip["target_start_ms"]) == previous_target_start_ms
            ),
        )
    if next_target_starts:
        next_target_start_ms = min(next_target_starts)
        upper_ms = min(
            upper_ms,
            min(
                int(clip.get("start_ms") or 0)
                for clip in fixed_lane_clips
                if int(clip["target_start_ms"]) == next_target_start_ms
            )
            - duration_ms,
        )

    if selected_start_anchor_ms is not None:
        # The caller has already projected the measured source onset.  Keep
        # that mapping exact even when the surrounding capacity is impossible;
        # the public close-out path turns the resulting tail/overlap into a
        # recoverable replacement rather than reflowing accepted neighbours.
        # Global scheduling may trim padding to fit unrelated chain blocks.
        # Reusing those crops at the old start would move the audible onset.
        # A locked projection is only translated; close-out owns safe edits.
        anchored_target = original_target or proposed_target
        anchor_source_start_ms = min(
            int(clip.get("start_ms") or 0) for clip in anchored_target
        )
        start_ms = int(selected_start_anchor_ms)
        shift_ms = start_ms - anchor_source_start_ms
        selected = [
            {
                **clip,
                "start_ms": int(clip.get("start_ms") or 0) + shift_ms,
                "end_ms": int(clip.get("end_ms") or 0) + shift_ms,
                "dub_lane": 0,
            }
            for clip in anchored_target
        ]
    elif upper_ms < lower_ms:
        # Keep neighbors immutable. The caller's timeline audit will reject an
        # impossible replacement instead of silently moving accepted audio.
        selected = proposed_target
    else:
        start_ms = min(upper_ms, max(lower_ms, desired_start_ms))
        shift_ms = start_ms - proposed_start_ms
        selected = [
            {
                **clip,
                "start_ms": int(clip.get("start_ms") or 0) + shift_ms,
                "end_ms": int(clip.get("end_ms") or 0) + shift_ms,
                "dub_lane": 0,
            }
            for clip in proposed_target
        ]

    selected_by_id = {str(clip.get("clip_id") or ""): clip for clip in selected}
    return [dict(selected_by_id.get(str(clip.get("clip_id") or ""), clip)) for clip in original]


def rebalance_selected_group_with_adjacent_window_timeline_clips(
    *,
    timeline_clips: list[dict],
    groups: list[DubbingGenerationGroup | dict],
    boundaries: list[DubbingBoundaryEvidence | dict],
    group_id: str,
) -> list[dict]:
    """Reflow at most the selected group and its immediate plan neighbours.

    This is the bounded capacity fallback for a complete candidate that cannot
    fit only because nearby groups were placed late. The full scheduler models
    the local rhythm, but only this three-group window is returned; every clip
    outside it remains byte-for-byte unchanged.
    """

    def value(item, key: str, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    ordered_group_ids = [str(value(group, "group_id", "")) for group in groups]
    try:
        selected_index = ordered_group_ids.index(group_id)
    except ValueError:
        return [dict(clip) for clip in timeline_clips]
    window_group_ids = set(
        ordered_group_ids[
            max(0, selected_index - 1) : min(
                len(ordered_group_ids), selected_index + 2
            )
        ]
    )
    original = [dict(clip) for clip in timeline_clips]
    proposed = rebalance_planned_timeline_clips(
        timeline_clips=original,
        groups=groups,
        boundaries=boundaries,
    )
    proposed_window = [
        dict(clip)
        for clip in proposed
        if str(clip.get("dubbing_group_id") or "") in window_group_ids
        and str(clip.get("track_id") or "dub") == "dub"
        and int(clip.get("dub_lane") or 0) == 0
    ]
    original_window_ids = {
        str(clip.get("clip_id") or "")
        for clip in original
        if str(clip.get("dubbing_group_id") or "") in window_group_ids
        and str(clip.get("track_id") or "dub") == "dub"
        and int(clip.get("dub_lane") or 0) == 0
    }
    proposed_window_ids = {
        str(clip.get("clip_id") or "") for clip in proposed_window
    }
    if (
        not proposed_window
        or "" in proposed_window_ids
        or proposed_window_ids != original_window_ids
    ):
        return original

    block_start_ms = min(int(clip.get("start_ms") or 0) for clip in proposed_window)
    block_end_ms = max(int(clip.get("end_ms") or 0) for clip in proposed_window)
    order_by_group = {
        current_group_id: index
        for index, current_group_id in enumerate(ordered_group_ids)
    }
    window_indexes = [order_by_group[item] for item in window_group_ids]
    first_window_index = min(window_indexes)
    last_window_index = max(window_indexes)
    fixed_main_lane = [
        clip
        for clip in original
        if str(clip.get("track_id") or "dub") == "dub"
        and int(clip.get("dub_lane") or 0) == 0
        and str(clip.get("dubbing_group_id") or "") in order_by_group
        and str(clip.get("dubbing_group_id") or "") not in window_group_ids
    ]
    left_end_ms = max(
        (
            int(clip.get("end_ms") or 0)
            for clip in fixed_main_lane
            if order_by_group[str(clip.get("dubbing_group_id") or "")]
            < first_window_index
        ),
        default=0,
    )
    right_start_ms = min(
        (
            int(clip.get("start_ms") or 0)
            for clip in fixed_main_lane
            if order_by_group[str(clip.get("dubbing_group_id") or "")]
            > last_window_index
        ),
        default=10**15,
    )
    lower_shift_ms = left_end_ms - block_start_ms
    upper_shift_ms = right_start_ms - block_end_ms
    for clip in proposed_window:
        audible_start_ms = int(clip.get("start_ms") or 0) + max(
            0, int(clip.get("alignment_lead_ms") or 0)
        )
        audible_end_ms = int(clip.get("end_ms") or 0) - max(
            0, int(clip.get("alignment_trail_ms") or 0)
        )
        target_start_ms = int(clip.get("target_start_ms") or 0)
        target_end_ms = int(clip.get("target_end_ms") or 0)
        lower_shift_ms = max(
            lower_shift_ms,
            target_start_ms - audible_end_ms + 1,
        )
        upper_shift_ms = min(
            upper_shift_ms,
            target_end_ms - audible_start_ms - 1,
        )
    if lower_shift_ms > upper_shift_ms:
        return original
    shift_ms = min(upper_shift_ms, max(lower_shift_ms, 0))
    replacements = {
        str(clip.get("clip_id") or ""): {
            **clip,
            "start_ms": int(clip.get("start_ms") or 0) + shift_ms,
            "end_ms": int(clip.get("end_ms") or 0) + shift_ms,
            "dub_lane": 0,
        }
        for clip in proposed_window
    }
    return [
        dict(replacements.get(str(clip.get("clip_id") or ""), clip))
        for clip in original
    ]


def _forward_shift_overlapping_group_blocks(
    timeline_clips: list[dict],
) -> list[dict]:
    """Remove residual lane overlap when later semantic windows have room.

    Hard boundaries split the main acoustic scheduler into independent
    segments. A long clip can still cross such a boundary even though the
    following groups can safely move later. Shift whole dubbing groups as one
    block, cascade the displacement forward, and never move audible speech
    beyond its own target window.
    """

    updated = [dict(clip) for clip in timeline_clips]
    lanes: dict[tuple[str, int], dict[str, list[int]]] = {}
    for index, clip in enumerate(updated):
        if str(clip.get("track_id") or "dub") != "dub":
            continue
        lane_key = ("dub", int(clip.get("dub_lane") or 0))
        group_id = str(clip.get("dubbing_group_id") or "")
        block_id = group_id or str(clip.get("clip_id") or f"clip_{index}")
        lanes.setdefault(lane_key, {}).setdefault(block_id, []).append(index)

    for blocks_by_id in lanes.values():
        ordered_blocks = sorted(
            blocks_by_id.values(),
            key=lambda indices: (
                min(
                    int(
                        updated[index].get("target_start_ms")
                        if updated[index].get("target_start_ms") is not None
                        else updated[index].get("start_ms")
                        or 0
                    )
                    for index in indices
                ),
                min(int(updated[index].get("start_ms") or 0) for index in indices),
            ),
        )
        previous_end_ms = 0
        for indices in ordered_blocks:
            block_start_ms = min(
                int(updated[index].get("start_ms") or 0)
                for index in indices
            )
            block_end_ms = max(
                int(updated[index].get("end_ms") or 0)
                for index in indices
            )
            required_shift_ms = max(0, previous_end_ms - block_start_ms)
            if required_shift_ms:
                shift_limits = []
                for index in indices:
                    clip = updated[index]
                    if clip.get("target_end_ms") is None:
                        shift_limits = []
                        break
                    audible_start_limit_ms = (
                        int(clip["target_end_ms"])
                        - max(0, int(clip.get("alignment_lead_ms") or 0))
                        - 1
                    )
                    shift_limits.append(
                        audible_start_limit_ms
                        - int(clip.get("start_ms") or 0)
                    )
                maximum_shift_ms = min(shift_limits) if shift_limits else -1
                if maximum_shift_ms >= required_shift_ms:
                    for index in indices:
                        clip = updated[index]
                        clip["start_ms"] = (
                            int(clip.get("start_ms") or 0)
                            + required_shift_ms
                        )
                        clip["end_ms"] = (
                            int(clip.get("end_ms") or 0)
                            + required_shift_ms
                        )
                    block_start_ms += required_shift_ms
                    block_end_ms += required_shift_ms
            previous_end_ms = max(previous_end_ms, block_end_ms)
    return updated


def restore_rebalance_groups(
    *,
    original_timeline_clips: list[dict],
    proposed_timeline_clips: list[dict],
    group_ids: set[str],
) -> list[dict]:
    """Restore complete dubbing groups after a proposed move fails audit.

    Candidate slices are one evidence block, so a conservative fallback must
    restore every slice in the affected group instead of mixing old and new
    placements inside one candidate.
    """

    original_by_id = {
        str(item.get("clip_id") or ""): dict(item)
        for item in original_timeline_clips
    }
    return [
        dict(original_by_id.get(str(item.get("clip_id") or ""), item))
        if str(item.get("dubbing_group_id") or "") in group_ids
        else dict(item)
        for item in proposed_timeline_clips
    ]


def _schedule_timeline_rebalance_chain(
    entries: list[dict],
) -> list[dict] | None:
    """Find a non-overlapping schedule without distorting candidate audio."""

    if not entries:
        return []
    blocks: list[list[dict]] = []
    for entry in entries:
        clip = entry["clip"]
        media_source_clip_id = str(clip.get("media_source_clip_id") or "")
        is_candidate_slice = bool(media_source_clip_id) and int(clip.get("dubbing_slice_count") or 1) > 1
        block_key = (
            (
                media_source_clip_id,
                str(clip.get("dubbing_group_id") or ""),
            )
            if is_candidate_slice
            else (
                str(clip.get("clip_id") or ""),
                "",
            )
        )
        if blocks and blocks[-1][0]["block_key"] == block_key:
            blocks[-1].append({**entry, "block_key": block_key})
        else:
            blocks.append([{**entry, "block_key": block_key}])

    scheduled_blocks: list[dict] = []
    for members in blocks:
        clips = [dict(member["clip"]) for member in members]
        durations = [int(clip.get("end_ms") or 0) - int(clip.get("start_ms") or 0) for clip in clips]
        if any(duration <= 0 for duration in durations):
            return None
        offsets: list[int] = []
        offset_ms = 0
        member_lowers: list[int] = []
        member_uppers: list[int] = []
        member_desired: list[int] = []
        for member, clip, duration_ms in zip(members, clips, durations):
            if offsets:
                offset_ms += max(
                    0,
                    int(clip.get("dubbing_timeline_gap_before_ms") or 0),
                )
            offsets.append(offset_ms)
            lead_ms = max(0, int(clip.get("alignment_lead_ms") or 0))
            trail_ms = max(0, int(clip.get("alignment_trail_ms") or 0))
            target_start_ms = int(member["target_start_ms"])
            target_end_ms = int(member["target_end_ms"])
            lower = max(
                0,
                target_start_ms - max(1, duration_ms - trail_ms) + 1,
            )
            upper = target_end_ms - lead_ms - 1
            if upper < lower:
                return None
            member_lowers.append(lower - offset_ms)
            member_uppers.append(upper - offset_ms)
            member_desired.append(min(upper, max(lower, target_start_ms - lead_ms)) - offset_ms)
            offset_ms += duration_ms

        lower = max(0, max(member_lowers))
        upper = min(member_uppers)
        if upper < lower:
            combined_target_start_ms = min(int(member["target_start_ms"]) for member in members)
            combined_target_end_ms = max(int(member["target_end_ms"]) for member in members)
            lower = max(
                0,
                max(
                    combined_target_start_ms
                    - max(
                        1,
                        member_offset
                        + duration_ms
                        - max(
                            0,
                            int(clip.get("alignment_trail_ms") or 0),
                        ),
                    )
                    + 1
                    for clip, member_offset, duration_ms in zip(
                        clips,
                        offsets,
                        durations,
                    )
                ),
            )
            upper = min(
                combined_target_end_ms - member_offset - max(0, int(clip.get("alignment_lead_ms") or 0)) - 1
                for clip, member_offset in zip(clips, offsets)
            )
            if upper < lower:
                return None
        desired = round(statistics.median(member_desired))
        scheduled_blocks.append(
            {
                "clips": clips,
                "durations": durations,
                "offsets": offsets,
                "duration_ms": offset_ms,
                "lower": lower,
                "upper": upper,
                "desired": min(upper, max(lower, desired)),
                "target_start_ms": min(int(member["target_start_ms"]) for member in members),
                "target_end_ms": max(int(member["target_end_ms"]) for member in members),
                "required_pause_before_ms": int(
                    members[0].get("required_pause_before_ms") or 0
                ),
            }
        )

    if len(scheduled_blocks) == 1:
        block_starts = [int(scheduled_blocks[0]["desired"])]
    else:
        required_gaps = [
            max(0, int(right["required_pause_before_ms"]))
            for right in scheduled_blocks[1:]
        ]
        offsets = [0]
        for index, gap_ms in enumerate(required_gaps):
            offsets.append(
                offsets[-1]
                + int(scheduled_blocks[index]["duration_ms"])
                + gap_ms
            )
        chain_duration_ms = (
            offsets[-1] + int(scheduled_blocks[-1]["duration_ms"])
        )
        chain_lower = max(
            int(block["lower"]) - offset
            for block, offset in zip(scheduled_blocks, offsets)
        )
        chain_upper = min(
            int(block["upper"]) - offset
            for block, offset in zip(scheduled_blocks, offsets)
        )
        if chain_upper >= chain_lower:
            island_target_start_ms = int(scheduled_blocks[0]["target_start_ms"])
            island_target_end_ms = int(scheduled_blocks[-1]["target_end_ms"])
            desired_start_ms = island_target_start_ms
            tail_overrun_ms = (
                desired_start_ms
                + chain_duration_ms
                - island_target_end_ms
            )
            if tail_overrun_ms > 300:
                desired_start_ms -= tail_overrun_ms - 300
            chain_start_ms = min(
                chain_upper,
                max(chain_lower, desired_start_ms),
            )
            block_starts = [
                chain_start_ms + offset for offset in offsets
            ]
        else:
            block_starts = []
            for index, block in enumerate(scheduled_blocks):
                start_ms = int(block["lower"])
                if index:
                    start_ms = max(
                        start_ms,
                        block_starts[index - 1]
                        + int(scheduled_blocks[index - 1]["duration_ms"])
                        + required_gaps[index - 1],
                    )
                if start_ms > int(block["upper"]):
                    return None
                block_starts.append(start_ms)

        # Keep only the source-backed inter-group gap.  If the individual
        # overlap constraints force extra idle time, move the already placed
        # prefix later as far as its target windows allow.  Any still-unavoidable
        # gap is left visible for the timeline audit to reject, so the caller
        # regenerates fuller or more naturally paced speech instead of hiding
        # underfill by spreading it uniformly through the sentence chain.
        for index, required_gap_ms in enumerate(required_gaps):
            actual_gap_ms = (
                block_starts[index + 1]
                - block_starts[index]
                - int(scheduled_blocks[index]["duration_ms"])
            )
            extra_gap_ms = max(0, actual_gap_ms - required_gap_ms)
            if not extra_gap_ms:
                continue
            prefix_slack_ms = min(
                int(scheduled_blocks[item]["upper"])
                - block_starts[item]
                for item in range(index + 1)
            )
            shift_ms = min(extra_gap_ms, max(0, prefix_slack_ms))
            for item in range(index + 1):
                block_starts[item] += shift_ms

    output: list[dict] = []
    for block, start_ms in zip(scheduled_blocks, block_starts):
        for clip, offset_ms, duration_ms in zip(
            block["clips"],
            block["offsets"],
            block["durations"],
        ):
            clip["start_ms"] = start_ms + offset_ms
            clip["end_ms"] = clip["start_ms"] + duration_ms
            output.append(clip)
    return output


def _source_backed_continuous_boundary(
    left: object,
    right: object,
    boundary: object | None,
) -> bool:
    """Return true only for an affirmatively continuous source boundary."""

    def value(item: object, key: str, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    if boundary is None:
        return False
    if bool(value(boundary, "no_break_with_next", False)):
        return True
    if bool(value(boundary, "hard_boundary", False)):
        return False
    if value(left, "speaker_id", None) != value(right, "speaker_id", None):
        return False
    left_scene = value(left, "scene_id", None)
    right_scene = value(right, "scene_id", None)
    same_planned_scene = (
        left_scene is not None
        and right_scene is not None
        and left_scene == right_scene
    )
    if not same_planned_scene and value(boundary, "same_scene", None) is not True:
        return False
    if value(boundary, "speech_between", None) is not False:
        return False
    if value(boundary, "pause_classification", "unknown") not in {
        "continuous",
        "natural_pause",
    }:
        return False
    semantic_relation = value(boundary, "semantic_relation", "unknown")
    if semantic_relation == "break":
        return False
    return bool(
        semantic_relation == "continuous"
        and value(boundary, "same_scene", None) is True
        or value(boundary, "low_energy_confidence", "none")
        in {"medium", "high"}
    )


def dubbing_source_revision(draft) -> str:
    """Fingerprint every project field that can change dubbing decisions."""

    transcription = draft.transcription
    payload = {
        "source_fingerprint": draft.localization_state.get("source_fingerprint"),
        "scene_context": draft.scene_context,
        "transcription_revision": (transcription.revision_id if transcription is not None else None),
        "transcription_words": (
            [word.model_dump(mode="json") for word in transcription.words] if transcription is not None else []
        ),
        "audio_boundary_features": (
            [feature.model_dump(mode="json") for feature in transcription.audio_boundary_features]
            if transcription is not None
            else []
        ),
        "cues": [
            {
                "cue_id": cue.cue_id,
                "speaker_id": cue.speaker_id,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "audio_route": cue.audio_route,
                "review_status": cue.review_status,
                "source_word_ids": cue.source_word_ids,
                "source_text": cue.en_subtitle_text,
            }
            for cue in draft.cues
        ],
        "localized_subtitles": [
            {
                "subtitle_id": subtitle.subtitle_id,
                "start_ms": subtitle.start_ms,
                "end_ms": subtitle.end_ms,
                "text": subtitle.text,
                "tts_text": subtitle.tts_text,
                "source_cue_ids": subtitle.source_cue_ids,
                "source_word_ids": subtitle.source_word_ids,
                "spoken_segment_id": subtitle.spoken_segment_id,
            }
            for subtitle in draft.localized_subtitles
        ],
        "localized_spoken_segments": [
            {
                "segment_id": segment.segment_id,
                "text": segment.text,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "source_cue_ids": segment.source_cue_ids,
                "source_word_ids": segment.source_word_ids,
            }
            for segment in draft.localized_spoken_segments
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def dubbing_timeline_projection_revision(draft) -> str:
    """Hash current editable dub placement without requiring renderable audio."""

    payload = [
        {
            key: dict(raw).get(key)
            for key in (
                "clip_id",
                "candidate_id",
                "result_id",
                "target_subtitle_ids",
                "subtitle_id",
                "dub_lane",
                "start_ms",
                "end_ms",
                "source_start_ms",
                "source_end_ms",
                "intentional_overlap",
                "cqc_status",
                "dubbing_timeline_gap_before_ms",
            )
        }
        for raw in draft.timeline_clips
        if dict(raw).get("track_id") == "dub"
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def group_evidence_context_fingerprint(draft, plan, group) -> str:
    """Fingerprint source dependencies of one group and its adjacent joins.

    Excludes project/plan revision and generated placement metadata. The current
    processed clip projection is independently checked before accepting a take.
    """
    units = list(plan.semantic_units)
    indexes = [i for i, unit in enumerate(units) if unit.unit_id in group.unit_ids]
    selected = units[max(0, min(indexes) - 1):max(indexes) + 2] if indexes else []
    cue_ids = {value for unit in selected for value in unit.source_cue_ids}
    word_ids = {value for unit in selected for value in unit.source_word_ids}
    source_cues = [cue for cue in draft.cues if cue.cue_id in cue_ids]
    word_ids.update(value for cue in source_cues for value in cue.source_word_ids)
    transcription = draft.transcription
    payload = {
        "contract": "dubbing-group-source-context-v1",
        "group": group.model_dump(mode="json", exclude={"group_id", "island_id"}),
        "adjacent_units": [unit.model_dump(mode="json", exclude={"display_text"}) for unit in selected],
        "source_media": draft.source_media.model_dump(mode="json"),
        "stems": draft.stems.model_dump(mode="json"),
        "source_fingerprint": draft.localization_state.get("source_fingerprint"),
        "scene_context": draft.scene_context,
        "language": draft.language_config.model_dump(mode="json"),
        "cues": [{key: getattr(cue, key) for key in (
            "cue_id", "speaker_id", "start_ms", "end_ms", "audio_route",
            "review_status", "source_word_ids", "en_subtitle_text",
        )} for cue in source_cues],
        "words": [word.model_dump(mode="json") for word in transcription.words
                  if word.word_id in word_ids] if transcription else [],
        "audio_boundaries": [feature.model_dump(mode="json") for feature in transcription.audio_boundary_features
                             if feature.left_word_id in word_ids or feature.right_word_id in word_ids]
                            if transcription else [],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def build_project_snapshot(draft) -> DubbingProductionSnapshot:
    """Project current text/timing facts before semantic Agent review."""

    cues_by_id = {cue.cue_id: cue for cue in draft.cues}
    units = _snapshot_units(draft, cues_by_id)
    boundaries = _snapshot_boundaries(draft, units, cues_by_id)
    warnings = [
        "scene_boundaries_require_visual_review",
        "semantic_relations_require_agent_review",
    ]
    speaker_gap_counts = Counter(
        unit.speaker_id
        for left, unit in zip(units, units[1:])
        if left.speaker_id == unit.speaker_id and unit.start_ms > left.end_ms
    )
    if not any(count >= 4 for count in speaker_gap_counts.values()):
        warnings.append("speech_gap_baseline_insufficient")
    if any(unit.speech_policy == "needs_review" for unit in units):
        warnings.append("speech_eligibility_requires_review")
    return DubbingProductionSnapshot(
        source_revision=dubbing_source_revision(draft),
        semantic_units=units,
        boundaries=boundaries,
        evidence_warnings=warnings,
    )


def rebase_compatible_generation_plan(
    snapshot: DubbingProductionSnapshot,
    previous_plan: DubbingGenerationPlan,
) -> DubbingGenerationPlan | None:
    """Carry still-valid editorial decisions across a localized-track edit."""

    previous_by_identity = {
        _semantic_unit_rebase_identity(unit): unit
        for unit in previous_plan.semantic_units
    }

    rebound_units: list[DubbingSemanticUnit] = []
    matched_count = 0
    for current in snapshot.semantic_units:
        previous = previous_by_identity.get(
            _semantic_unit_rebase_identity(current)
        )
        if previous is None:
            previous = _compatible_previous_unit_decision(
                current,
                previous_plan.semantic_units,
            )
        if previous is None:
            rebound_units.append(current)
            continue
        matched_count += 1
        rebound_units.append(
            current.model_copy(
                update={
                    "scene_id": previous.scene_id,
                    "scene_end_ms": previous.scene_end_ms,
                    "speech_policy": previous.speech_policy,
                    "decision_reason_codes": list(
                        previous.decision_reason_codes
                    ),
                }
            )
        )

    if not matched_count:
        return None

    scene_by_unit_id = {
        unit.unit_id: unit.scene_id for unit in rebound_units
    }
    rebound_boundaries = [
        boundary.model_copy(
            update={
                "same_scene": (
                    scene_by_unit_id[boundary.left_unit_id]
                    == scene_by_unit_id[boundary.right_unit_id]
                    if (
                        scene_by_unit_id[boundary.left_unit_id] is not None
                        and scene_by_unit_id[boundary.right_unit_id] is not None
                    )
                    else boundary.same_scene
                )
            }
        )
        for boundary in snapshot.boundaries
    ]
    return build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=snapshot.source_revision,
            semantic_units=rebound_units,
            boundaries=rebound_boundaries,
        )
    )


def refresh_generation_plan_timings(
    snapshot: DubbingProductionSnapshot,
    previous_plan: DubbingGenerationPlan,
) -> DubbingGenerationPlan:
    """Refresh deterministic word-backed timing without changing reviewed grouping.

    This is intentionally narrower than replanning.  It preserves every
    reviewed unit, island, group, text and speech-policy decision, and rejects
    the refresh if current localization membership or wording changed.
    """

    if snapshot.source_revision != previous_plan.source_revision:
        raise ValueError("current localization revision differs from the active plan")
    current_by_id = {item.unit_id: item for item in snapshot.semantic_units}
    if [item.unit_id for item in snapshot.semantic_units] != [
        item.unit_id for item in previous_plan.semantic_units
    ]:
        raise ValueError("current semantic-unit order differs from the active plan")

    immutable_fields = (
        "unit_id",
        "subtitle_ids",
        "source_cue_ids",
        "source_word_ids",
        "speaker_id",
        "display_text",
        "spoken_text",
    )
    refreshed_units = []
    for previous in previous_plan.semantic_units:
        current = current_by_id[previous.unit_id]
        if any(
            getattr(previous, field) != getattr(current, field)
            for field in immutable_fields
        ):
            raise ValueError(
                f"semantic unit changed beyond timing: {previous.unit_id}"
            )
        refreshed_units.append(
            previous.model_copy(
                update={
                    "start_ms": current.start_ms,
                    "end_ms": current.end_ms,
                    "source_anchor_start_ms": current.source_anchor_start_ms,
                    "source_anchor_end_ms": current.source_anchor_end_ms,
                }
            )
        )

    units_by_id = {item.unit_id: item for item in refreshed_units}
    refreshed_groups = []
    for group in previous_plan.groups:
        units = [units_by_id[unit_id] for unit_id in group.unit_ids]
        refreshed_groups.append(
            group.model_copy(
                update={
                    "target_start_ms": units[0].start_ms,
                    "target_end_ms": max(
                        item.scene_end_ms or item.end_ms for item in units
                    ),
                    "source_reference_start_ms": min(
                        item.source_anchor_start_ms for item in units
                    ),
                    "source_reference_end_ms": max(
                        item.source_anchor_end_ms for item in units
                    ),
                }
            )
        )

    refreshed_islands = []
    for island in previous_plan.speech_islands:
        units = [units_by_id[unit_id] for unit_id in island.unit_ids]
        refreshed_islands.append(
            island.model_copy(
                update={
                    "start_ms": units[0].start_ms,
                    "end_ms": max(
                        item.scene_end_ms or item.end_ms for item in units
                    ),
                }
            )
        )
    return previous_plan.model_copy(
        update={
            "semantic_units": refreshed_units,
            "speech_islands": refreshed_islands,
            "groups": refreshed_groups,
        }
    )


def _compatible_previous_unit_decision(
    current: DubbingSemanticUnit,
    previous_units: list[DubbingSemanticUnit],
) -> DubbingSemanticUnit | None:
    """Carry one reviewed policy through safe target-unit repartitioning."""

    if current.speech_policy != "needs_review":
        return None
    current_subtitles = set(current.subtitle_ids)
    overlapping = [
        unit
        for unit in previous_units
        if current_subtitles.intersection(unit.subtitle_ids)
    ]
    if not overlapping:
        return None
    if not current_subtitles.issubset(
        {
            subtitle_id
            for unit in overlapping
            for subtitle_id in unit.subtitle_ids
        }
    ):
        return None
    if any(unit.speaker_id != current.speaker_id for unit in overlapping):
        return None
    previous_source_cues = {
        cue_id
        for unit in overlapping
        for cue_id in unit.source_cue_ids
    }
    if not set(current.source_cue_ids).issubset(previous_source_cues):
        return None
    previous_source_words = {
        word_id
        for unit in overlapping
        for word_id in unit.source_word_ids
    }
    if (
        current.source_word_ids
        and previous_source_words
        and not set(current.source_word_ids).issubset(previous_source_words)
    ):
        return None
    policies = {unit.speech_policy for unit in overlapping}
    if len(policies) != 1:
        return None
    scene_ids = {unit.scene_id for unit in overlapping}
    decision_reason_codes = list(
        dict.fromkeys(
            reason
            for unit in overlapping
            for reason in unit.decision_reason_codes
        )
    )
    previous_scene_end_ms = (
        overlapping[-1].scene_end_ms
        if len(scene_ids) == 1
        else current.scene_end_ms
    )
    rebound_scene_end_ms = (
        max(previous_scene_end_ms, current.source_anchor_end_ms)
        if previous_scene_end_ms is not None
        else None
    )
    return current.model_copy(
        update={
            "scene_id": next(iter(scene_ids)) if len(scene_ids) == 1 else current.scene_id,
            "scene_end_ms": rebound_scene_end_ms,
            "speech_policy": next(iter(policies)),
            "decision_reason_codes": decision_reason_codes,
        }
    )


def _semantic_unit_rebase_identity(unit: DubbingSemanticUnit) -> tuple:
    return (
        unit.unit_id,
        tuple(unit.subtitle_ids),
        tuple(unit.source_cue_ids),
        tuple(unit.source_word_ids),
        unit.speaker_id,
        unit.source_anchor_start_ms,
        unit.source_anchor_end_ms,
    )


def build_generation_plan(
    payload: DubbingGenerationPlanInput,
) -> DubbingGenerationPlan:
    """Create speech islands and small TTS groups from reviewed evidence."""

    findings: list[DubbingQualityFinding] = []
    boundary_by_left = {boundary.left_unit_id: boundary for boundary in payload.boundaries}
    islands: list[list[DubbingSemanticUnit]] = []
    current: list[DubbingSemanticUnit] = []

    for unit in payload.semantic_units:
        if unit.speech_policy != "translate":
            if current:
                islands.append(current)
                current = []
            if unit.speech_policy == "needs_review":
                findings.append(
                    _finding(
                        "SPEECH_ELIGIBILITY_REVIEW_REQUIRED",
                        "blocking",
                        "该语义单元尚未确认是否需要中文配音。",
                        [unit.unit_id],
                        "确认翻译、保留原声或跳过后再生成。",
                    )
                )
            continue

        if current:
            boundary = boundary_by_left[current[-1].unit_id]
            if _starts_new_island(boundary):
                islands.append(current)
                current = []
                if _boundary_is_ambiguous(boundary):
                    findings.append(
                        _finding(
                            "AMBIGUOUS_SPEECH_BOUNDARY",
                            "warning",
                            "相邻内容缺少足够证据证明可以连续合成，已保守拆组。",
                            [boundary.left_unit_id, boundary.right_unit_id],
                            "结合画面、原声和语义确认后可重新规划。",
                        )
                    )
        current.append(unit)
    if current:
        islands.append(current)

    speech_islands: list[DubbingSpeechIsland] = []
    groups: list[DubbingGenerationGroup] = []
    for island_index, units in enumerate(islands, start=1):
        island_id = f"speech_island_{island_index:04d}"
        speech_islands.append(
            DubbingSpeechIsland(
                island_id=island_id,
                unit_ids=[unit.unit_id for unit in units],
                speaker_id=units[0].speaker_id,
                scene_id=units[0].scene_id,
                start_ms=units[0].start_ms,
                end_ms=units[-1].end_ms,
            )
        )
        partition = _partition_island(
            units,
            boundary_by_left,
            maximum_effective_speech_ms=(
                payload.policy.maximum_effective_speech_ms
            ),
            maximum_text_pressure=payload.policy.maximum_text_pressure,
        )
        for grouped_units in partition["groups"]:
            group_index = len(groups) + 1
            group = DubbingGenerationGroup(
                group_id=f"dubbing_group_{group_index:04d}",
                island_id=island_id,
                unit_ids=[unit.unit_id for unit in grouped_units],
                subtitle_ids=list(
                    dict.fromkeys(subtitle_id for unit in grouped_units for subtitle_id in unit.subtitle_ids)
                ),
                speaker_id=grouped_units[0].speaker_id,
                scene_id=grouped_units[0].scene_id,
                spoken_text="\n".join(unit.spoken_text for unit in grouped_units),
                target_start_ms=grouped_units[0].start_ms,
                target_end_ms=max(
                    unit.scene_end_ms or unit.end_ms
                    for unit in grouped_units
                ),
                source_reference_start_ms=min(unit.source_anchor_start_ms for unit in grouped_units),
                source_reference_end_ms=max(unit.source_anchor_end_ms for unit in grouped_units),
            )
            groups.append(group)
            if _UNNATURAL_CARDINAL_TEN.search(group.spoken_text):
                findings.append(
                    _finding(
                        "GENERATION_GROUP_UNNATURAL_CARDINAL",
                        "blocking",
                        "配音台词含有不自然的中文数字读法。",
                        group.unit_ids,
                        "先核对上屏数字与配音读法，再重新建立生成计划。",
                    )
                )
            group_speakers = {unit.speaker_id for unit in grouped_units}
            if len(group_speakers) != 1 or "mixed" in group_speakers:
                findings.append(
                    _finding(
                        "GENERATION_GROUP_CROSSES_SPEAKERS",
                        "blocking",
                        "该配音组跨越不同说话人，不能合成为一条音频。",
                        group.unit_ids,
                        "按说话人边界拆组。",
                    )
                )
        if partition["needs_review_unit_ids"]:
            findings.append(
                _finding(
                    "GENERATION_GROUP_CAPACITY_ESTIMATE",
                    "warning",
                    "估算这段台词可能超出可用时长，当前没有可安全拆开的完整自然边界。",
                    partition["needs_review_unit_ids"],
                    "保留完整台词生成后，按实际字音、必要停顿和可用窗口判断是否需要容量恢复。",
                )
            )

    status = _status_from_findings(findings)
    if status == "passed" and any(unit.speech_policy == "needs_review" for unit in payload.semantic_units):
        status = "needs_review"
    return DubbingGenerationPlan(
        source_revision=payload.source_revision,
        status=status,
        semantic_units=payload.semantic_units,
        speech_islands=speech_islands,
        groups=groups,
        findings=findings,
    )


def evaluate_candidate(
    payload: DubbingCandidateCqcInput,
) -> DubbingCandidateCqcReport:
    """Evaluate objective candidate evidence without pretending to have listened."""

    findings: list[DubbingQualityFinding] = []
    comparison_text = payload.candidate_transcript.strip()
    used_alignment_fallback = False
    if (
        not comparison_text
        and payload.audio is not None
        and payload.audio.aligned_words
    ):
        comparison_text = " ".join(
            word.text for word in payload.audio.aligned_words
        )
        used_alignment_fallback = True
    transcript = compare_transcripts(
        payload.expected_spoken_text,
        comparison_text,
        reference_text=payload.reference_transcript,
    )

    if used_alignment_fallback:
        findings.append(
            _finding(
                "CANDIDATE_TRANSCRIPT_ALIGNMENT_FALLBACK_USED",
                "warning",
                "候选没有独立 ASR 文本，内容覆盖暂按逐词对齐证据判断。",
                [payload.candidate_id],
                "保留轻量听审；只有实际发音不对时才重新生成。",
            )
        )

    if payload.task_status != "success":
        findings.append(
            _finding(
                "CANDIDATE_TASK_NOT_SUCCESSFUL",
                "blocking",
                "合成任务尚未成功完成。",
                [payload.candidate_id],
                "等待成功结果或重新生成。",
            )
        )
    if not payload.artifact_id or not payload.audio_sha256 or payload.audio is None:
        findings.append(
            _finding(
                "CANDIDATE_AUDIO_EVIDENCE_MISSING",
                "blocking",
                "候选音频或其可验证证据不完整。",
                [payload.candidate_id],
                "重新读取受管音频并完成自动检测。",
            )
        )

    missing_ratio = 1.0 - transcript.coverage_ratio
    reviewable_transcript_variation = _is_reviewable_small_token_substitution(
        transcript
    ) or _is_reviewable_optional_erhua(
        transcript,
        expected_text=payload.expected_spoken_text,
        actual_text=comparison_text,
    )
    if missing_ratio > payload.policy.maximum_missing_token_ratio and not reviewable_transcript_variation:
        findings.append(
            _finding(
                "CANDIDATE_TRANSCRIPT_MISSING_CONTENT",
                "warning",
                "原素材转写疑似缺少目标台词，需核对实际保留内容。",
                [payload.candidate_id],
                "先复听保留部分；确有缺字时再补齐目标表达。",
            )
        )
    elif transcript.missing_tokens:
        findings.append(
            _finding(
                "CANDIDATE_TRANSCRIPT_MINOR_MISMATCH",
                "warning",
                "候选识别文本与目标台词有少量差异，需要听审确认。",
                [payload.candidate_id],
                "听清对应位置并确认是否影响理解。",
            )
        )

    if transcript.extra_ratio > payload.policy.maximum_extra_token_ratio and not reviewable_transcript_variation:
        findings.append(
            _finding(
                "CANDIDATE_TRANSCRIPT_EXTRA_CONTENT",
                "warning",
                "原素材转写包含多余内容，可剪辑后核对。",
                [payload.candidate_id],
                "先剪掉多余表达，再检查最终保留部分。",
            )
        )
    elif transcript.extra_tokens:
        findings.append(
            _finding(
                "CANDIDATE_TRANSCRIPT_MINOR_EXTRA",
                "warning",
                "候选配音可能有少量额外发音，需要听审确认。",
                [payload.candidate_id],
                "检查开头、结尾及重复词。",
            )
        )
    if transcript.reference_only_extra_tokens:
        findings.append(
            _finding(
                "REFERENCE_AUDIO_CONTAMINATION_SUSPECTED",
                "warning",
                "原素材转写包含参考音内容，可先剪掉再检查保留部分。",
                [payload.candidate_id],
                "优先保留可用表达；没有安全切点时再考虑重新生成。",
            )
        )

    if payload.audio is not None:
        _append_acoustic_evidence_findings(payload, findings)
        if payload.audio.clipping_ratio > payload.policy.maximum_clipping_ratio:
            findings.append(
                _finding(
                    "CANDIDATE_AUDIO_CLIPPING",
                    "blocking",
                    "候选音频存在超出容许比例的削波。",
                    [payload.candidate_id],
                    "降低增益或重新生成。",
                )
            )
        if (
            payload.audio.max_leading_silence_ms is not None
            and payload.audio.leading_silence_ms > payload.audio.max_leading_silence_ms
        ):
            findings.append(
                _finding(
                    "CANDIDATE_HEAD_SILENCE_TOO_LONG",
                    "warning",
                    "候选开头静音超过本组允许范围。",
                    [payload.candidate_id],
                    "在不切到首个音素的前提下掐头。",
                )
            )
        if (
            payload.audio.max_trailing_silence_ms is not None
            and payload.audio.trailing_silence_ms > payload.audio.max_trailing_silence_ms
        ):
            findings.append(
                _finding(
                    "CANDIDATE_TAIL_SILENCE_TOO_LONG",
                    "warning",
                    "候选结尾静音超过本组允许范围。",
                    [payload.candidate_id],
                    "在不切到尾音的前提下去尾。",
                )
            )
        _append_pause_findings(payload, findings)

    if (
        payload.planned_scene_end_ms is not None
        and payload.placement_end_ms is not None
        and payload.placement_end_ms > payload.planned_scene_end_ms + payload.frame_tolerance_ms
    ):
        findings.append(
            _finding(
                "CANDIDATE_OVERRUNS_SCENE",
                "blocking",
                "候选配音越过了当前场景的语义结束边界。",
                [payload.candidate_id],
                "优先优化台词或重新生成，必要时再做安全剪辑与轻微变速。",
            )
        )

    review_by_dimension = {review.dimension: review for review in payload.subjective_reviews}
    if _evidence_backed_rendered_timeline_listening(review_by_dimension):
        findings = [
            (
                finding.model_copy(
                    update={
                        "severity": "warning",
                        "message": (
                            "连续话术包含多处安全剪辑，但当前渲染时间线"
                            "已经实际复听并确认连贯自然。"
                        ),
                        "recommended_action": (
                            "保留当前剪辑；位置或裁切变化后必须重新复听。"
                        ),
                    }
                )
                if finding.code == "CANDIDATE_OVER_FRAGMENTED_PHRASE"
                and finding.severity == "blocking"
                else finding
            )
            for finding in findings
        ]
    automatic_status = _status_from_findings(findings)
    failed_dimensions = [dimension for dimension, review in review_by_dimension.items() if review.status == "failed"]
    missing_dimensions = sorted(
        _SUBJECTIVE_DIMENSIONS
        - {dimension for dimension, review in review_by_dimension.items() if review.status != "not_reviewed"}
    )
    if failed_dimensions:
        subjective_status: ReviewStatus = "failed"
        findings.append(
            _finding(
                "SUBJECTIVE_LISTENING_REVIEW_FAILED",
                "blocking",
                "主观听审发现语义、发音、断句、音色或自然度问题。",
                failed_dimensions,
                "根据听审记录选择改台词、缩组、重抽或安全剪辑。",
            )
        )
    elif missing_dimensions:
        subjective_status = "not_reviewed"
        findings.append(
            _finding(
                "SUBJECTIVE_LISTENING_REVIEW_REQUIRED",
                "warning",
                "自动检测不能证明发音、语义断句和自然度，仍需实际听审。",
                missing_dimensions,
                "由 Agent 或人工逐项听审并提交证据。",
            )
        )
    else:
        subjective_status = "passed"

    if automatic_status == "failed" or subjective_status == "failed":
        overall_status: ReviewStatus = "failed"
    elif subjective_status != "passed":
        overall_status = "needs_review"
    else:
        # Automatic warnings are deliberately non-blocking review prompts.
        # Once all required listening dimensions have explicitly passed, the
        # warning has been adjudicated and the candidate can be accepted.
        # Blocking automatic evidence already maps to ``failed`` above.
        overall_status = "passed"

    if overall_status == "passed":
        action = "accept"
    elif overall_status == "needs_review" or (overall_status == "warning" and automatic_status != "failed"):
        action = "listen_and_review"
    elif any(finding.code == "CANDIDATE_OVERRUNS_SCENE" for finding in findings):
        action = "repair_text_or_grouping"
    else:
        action = "regenerate"

    return DubbingCandidateCqcReport(
        source_revision=payload.source_revision,
        plan_revision=payload.plan_revision,
        group_id=payload.group_id,
        candidate_id=payload.candidate_id,
        evidence_fingerprint=candidate_evidence_fingerprint(payload),
        automatic_status=automatic_status,
        subjective_status=subjective_status,
        overall_status=overall_status,
        transcript=transcript,
        audio_evidence=payload.audio,
        findings=findings,
        recommended_action=action,
    )


def build_candidate_gap_processing_report(
    payload: DubbingCandidateCqcInput,
) -> DubbingCandidateCqcReport:
    """Check editable media; raw transcription is advisory, not final-cut proof."""

    transcript = DubbingTranscriptComparison(
        expected_tokens=0,
        matched_tokens=0,
        coverage_ratio=0.0,
        extra_ratio=0.0,
    )
    findings: list[DubbingQualityFinding] = []
    if (
        payload.task_status != "success"
        or not payload.artifact_id
        or not payload.audio_sha256
        or payload.audio is None
    ):
        findings.append(
            _finding(
                "CANDIDATE_AUDIO_EVIDENCE_MISSING",
                "blocking",
                "生成音频缺失、不可读或没有可验证文件指纹。",
                [payload.candidate_id],
                "保留失败记录；不要把无效文件放入时间线。",
            )
        )
    content = payload.content_evidence
    if dubbing_content_integrity.reusable_transcript(content, payload.audio_sha256 or ""):
        transcript = compare_transcripts(payload.expected_spoken_text, content.transcript,
                                        reference_text=payload.reference_transcript)
        finding = dubbing_content_integrity.content_finding(transcript)
        if finding:
            code, severity, message = finding
            findings.append(_finding(code, severity, message, [payload.candidate_id],
                                     "可先剪掉不需要的内容，再复听最终保留范围。"))
    blocked = any(item.severity == "blocking" for item in findings)
    status: ReviewStatus = "failed" if blocked else ("warning" if findings else "passed")
    return DubbingCandidateCqcReport(
        source_revision=payload.source_revision,
        plan_revision=payload.plan_revision,
        group_id=payload.group_id,
        candidate_id=payload.candidate_id,
        evidence_fingerprint=candidate_evidence_fingerprint(payload),
        automatic_status=status,
        subjective_status="not_reviewed",
        overall_status=status,
        transcript=transcript,
        audio_evidence=payload.audio,
        findings=findings,
        recommended_action=("regenerate" if blocked
                            else "listen_and_review" if findings else "accept"),
    )


def candidate_evidence_fingerprint(
    payload: DubbingCandidateCqcInput,
) -> str:
    fingerprint_payload = payload.model_dump(mode="json")
    # Reuse provenance is not new acoustic evidence; omit absent extension
    # fields to retain fingerprints for legacy frozen inputs.
    for field in ("source_context_fingerprint", "evidence_origin_source_revision",
                  "evidence_origin_plan_revision"):
        fingerprint_payload.pop(field, None)
    if fingerprint_payload.get("retained_content_evidence") is None:
        fingerprint_payload.pop("retained_content_evidence", None)
    for gap in ((fingerprint_payload.get("audio") or {}).get("gap_evidence") or []):
        for field in ("semantic_role", "semantic_pause_scale"):
            if gap.get(field) is None:
                gap.pop(field, None)
    return hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


_CRITICAL_SINGLE_SUBSTITUTION_TOKENS = set("不没无未非别莫勿0123456789零〇一二两三四五六七八九十百千万亿兆点百分之")
_OPTIONAL_ERHUA_BASES = set("哪这那事点会玩味块门地面空缝根劲头活伴样号座弯角圈边片串个")


def _is_reviewable_small_token_substitution(
    transcript: DubbingTranscriptComparison,
) -> bool:
    """Keep a small non-critical ASR substitution reviewable, not accepted.

    Character error ratios become brittle on short lines: one ordinary lexical
    substitution can exceed an 8% limit even when every other token is present.
    A line of at least twenty tokens may similarly contain one two-token lexical
    span that an ASR writes phonetically.  The mismatch must remain a balanced
    substitution; insertions and omissions do not receive this treatment.
    Numbers and negation remain blocking because a one-character error there can
    reverse meaning or change a material value.  This only downgrades the
    automatic result to a warning; all subjective dimensions are still required.
    """

    if transcript.expected_tokens < 8:
        return False
    maximum_span = 2 if transcript.expected_tokens >= 20 else 1
    missing_count = len(transcript.missing_tokens)
    extra_count = len(transcript.extra_tokens)
    if not (1 <= missing_count == extra_count <= maximum_span):
        return False
    return not (set(transcript.missing_tokens) | set(transcript.extra_tokens)) & _CRITICAL_SINGLE_SUBSTITUTION_TOKENS


def _evidence_backed_rendered_timeline_listening(
    review_by_dimension: dict[str, object],
) -> bool:
    """Return whether the actual edited timeline was explicitly listened to."""

    for dimension in ("prosody_parse", "naturalness"):
        review = review_by_dimension.get(dimension)
        evidence_id = str(getattr(review, "evidence_id", None) or "").strip()
        if (
            review is None
            or getattr(review, "status", None) != "passed"
            or not evidence_id.startswith(
                ("user-listening:timeline:", "agent-listening:timeline:")
            )
        ):
            return False
    return True


def _is_reviewable_optional_erhua(
    transcript: DubbingTranscriptComparison,
    *,
    expected_text: str,
    actual_text: str,
) -> bool:
    """Keep a conservative Mandarin erhua spelling variant reviewable.

    Some speakers realize an erhua suffix weakly enough that ASR alternates
    between forms such as ``哪儿来``/``哪来`` and ``事``/``事儿``.  Only one
    ``儿`` attached to a known erhua-capable base may receive this treatment,
    and removing it must make every other comparison token identical.  This
    remains a warning that needs evidence-based review; semantic uses such as
    ``儿子`` stay blocking.
    """

    if transcript.expected_tokens < 8:
        return False
    if transcript.missing_tokens == ["儿"] and not transcript.extra_tokens:
        longer_text, shorter_tokens = expected_text, _comparison_tokens(actual_text)
    elif transcript.extra_tokens == ["儿"] and not transcript.missing_tokens:
        longer_text, shorter_tokens = actual_text, _comparison_tokens(expected_text)
    else:
        return False
    normalized = unicodedata.normalize("NFKC", str(longer_text or ""))
    for match in re.finditer("儿", normalized):
        index = match.start()
        if index == 0 or normalized[index - 1] not in _OPTIONAL_ERHUA_BASES:
            continue
        without_suffix = normalized[:index] + normalized[index + 1 :]
        if _comparison_tokens(without_suffix) == shorter_tokens:
            return True
    return False


def compare_transcripts(
    expected_text: str,
    actual_text: str,
    *,
    reference_text: str = "",
) -> DubbingTranscriptComparison:
    expected = _comparison_tokens(expected_text)
    actual = _reconcile_latin_asr_tokens(
        expected,
        _comparison_tokens(actual_text),
    )
    matcher = SequenceMatcher(a=expected, b=actual, autojunk=False)
    missing: list[str] = []
    extra: list[str] = []
    matched = 0
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag == "equal":
            matched += left_end - left_start
        elif tag == "delete":
            missing.extend(expected[left_start:left_end])
        elif tag == "insert":
            extra.extend(actual[right_start:right_end])
        elif tag == "replace":
            missing.extend(expected[left_start:left_end])
            extra.extend(actual[right_start:right_end])
    if (missing or extra) and (
        transcript_pronunciation_tokens(expected_text) == transcript_pronunciation_tokens(actual_text)
    ):
        matched = len(expected)
        missing = []
        extra = []
    # Reference leakage is a word-level signal.  Character-level comparison
    # is intentionally tolerant of ASR spacing, but using it here would make
    # ordinary shared letters (for example the ``e`` in two unrelated English
    # words) look like leaked reference speech.
    expected_words = Counter(expected)
    reference_only = Counter(_comparison_tokens(reference_text)) - expected_words
    candidate_word_extra = Counter(actual) - expected_words
    contaminated = list((reference_only & candidate_word_extra).elements())
    expected_count = len(expected)
    return DubbingTranscriptComparison(
        expected_tokens=expected_count,
        matched_tokens=matched,
        missing_tokens=missing,
        extra_tokens=extra,
        reference_only_extra_tokens=contaminated,
        coverage_ratio=(matched / expected_count if expected_count else 1.0),
        extra_ratio=(len(extra) / max(1, expected_count)),
    )


def _reconcile_latin_asr_tokens(
    expected: list[str],
    actual: list[str],
) -> list[str]:
    """Reconcile English brand speech with ordinary ASR spellings.

    Mixed-language TTS often pronounces an official English name correctly
    while ASR writes the same sound as separate words (``Sea Dance``), a
    spoken letter plus a word (``C Dream``), or a close phonetic spelling.
    Reconcile only alphabetic expected words of meaningful length, in order,
    and require either a split/join spelling or the same conservative English
    soundex key.  This does not turn a different single product word into an
    automatic match merely because its edit distance is small.
    """

    reconciled = list(actual)
    cursor = 0
    for expected_token in expected:
        if not (
            expected_token.isascii()
            and expected_token.isalpha()
            and len(expected_token) >= 5
        ):
            continue
        best: tuple[float, int, int] | None = None
        latin_seen = 0
        for start in range(cursor, len(reconciled)):
            first = reconciled[start]
            if not (first.isascii() and first.isalpha()):
                continue
            latin_seen += 1
            if latin_seen > 3:
                break
            combined = ""
            for width in (1, 2):
                end = start + width
                if end > len(reconciled):
                    continue
                tokens = reconciled[start:end]
                if not all(token.isascii() and token.isalpha() for token in tokens):
                    continue
                spoken_tokens = [
                    _SPOKEN_LATIN_LETTERS.get(token, token)
                    if len(token) == 1
                    else token
                    for token in tokens
                ]
                combined = "".join(spoken_tokens)
                ratio = SequenceMatcher(
                    a=expected_token,
                    b=combined,
                    autojunk=False,
                ).ratio()
                phonetic_match = (
                    _english_soundex(expected_token)
                    == _english_soundex(combined)
                )
                if ratio < 0.72 or not phonetic_match:
                    continue
                candidate = (ratio, start, end)
                if best is None or candidate[0] > best[0]:
                    best = candidate
        if best is None:
            continue
        _, start, end = best
        reconciled[start:end] = [expected_token]
        cursor = start + 1
    return reconciled


_SPOKEN_LATIN_LETTERS = {
    "a": "ay",
    "b": "bee",
    "c": "see",
    "d": "dee",
    "e": "ee",
    "g": "gee",
    "i": "eye",
    "j": "jay",
    "k": "kay",
    "o": "oh",
    "p": "pee",
    "q": "cue",
    "r": "ar",
    "s": "ess",
    "t": "tee",
    "u": "you",
    "v": "vee",
    "w": "doubleyou",
    "x": "ex",
    "y": "why",
    "z": "zee",
}


def _english_soundex(value: str) -> str:
    letters = "".join(character for character in value.casefold() if character.isalpha())
    if not letters:
        return ""
    if letters.startswith("c") and len(letters) > 1 and letters[1] in "eiy":
        letters = "s" + letters[1:]
    groups = {
        **dict.fromkeys("bfpv", "1"),
        **dict.fromkeys("cgjkqsxz", "2"),
        **dict.fromkeys("dt", "3"),
        "l": "4",
        **dict.fromkeys("mn", "5"),
        "r": "6",
    }
    output = [letters[0]]
    previous = groups.get(letters[0], "")
    for character in letters[1:]:
        code = groups.get(character, "")
        if code and code != previous:
            output.append(code)
        previous = code
    return "".join(output)


def audit_timeline(
    payload: DubbingTimelineAuditInput,
) -> DubbingTimelineAuditReport:
    """Audit final dub coverage, placement, lanes and subtitle freshness."""

    findings: list[DubbingQualityFinding] = []
    expected_by_id = {unit.unit_id: unit for unit in payload.expected_units}
    expected_index_by_id = {
        unit.unit_id: index
        for index, unit in enumerate(payload.expected_units)
    }
    target_ids = {unit.unit_id for unit in payload.expected_units if unit.speech_policy == "translate"}
    forbidden_ids = {
        unit.unit_id
        for unit in payload.expected_units
        if unit.speech_policy in {"preserve_original", "omit_non_speech"}
    }
    subtitle_owner: dict[str, str] = {}
    for unit in payload.expected_units:
        for subtitle_id in unit.subtitle_ids:
            subtitle_owner[subtitle_id] = unit.unit_id
    # One accepted candidate may be cut into several timeline clips while the
    # editor keeps the original group-level subtitle binding on every piece.
    # Count that candidate/group binding once.  Genuine duplicate candidates
    # still count twice, while duplicate source crops from the same candidate
    # remain guarded independently below.
    subtitle_coverage = {
        (clip.candidate_id, clip.group_id, subtitle_id)
        for clip in payload.clips
        for subtitle_id in clip.target_subtitle_ids
    }
    subtitle_counts: Counter[str] = Counter(
        subtitle_id
        for _candidate_id, _group_id, subtitle_id in subtitle_coverage
    )
    legacy_coverage = {
        (clip.candidate_id, clip.group_id, unit_id)
        for clip in payload.clips
        if not clip.target_subtitle_ids
        for unit_id in clip.unit_ids
    }
    legacy_counts: Counter[str] = Counter(
        unit_id
        for _candidate_id, _group_id, unit_id in legacy_coverage
    )

    covered: list[str] = []
    missing: list[str] = []
    duplicate: list[str] = []
    unexpected_set: set[str] = set()
    for unit in payload.expected_units:
        if unit.speech_policy != "translate":
            if any(subtitle_counts[item] for item in unit.subtitle_ids):
                unexpected_set.add(unit.unit_id)
            if legacy_counts[unit.unit_id]:
                unexpected_set.add(unit.unit_id)
            continue
        if unit.subtitle_ids:
            unit_counts = [subtitle_counts[item] for item in unit.subtitle_ids]
            if all(count >= 1 for count in unit_counts):
                covered.append(unit.unit_id)
            if any(count == 0 for count in unit_counts):
                missing.append(unit.unit_id)
            if any(count > 1 for count in unit_counts):
                duplicate.append(unit.unit_id)
        else:
            count = legacy_counts[unit.unit_id]
            if count:
                covered.append(unit.unit_id)
            if count == 0:
                missing.append(unit.unit_id)
            if count > 1:
                duplicate.append(unit.unit_id)
    unexpected_set.update(
        f"subtitle:{subtitle_id}" for subtitle_id in subtitle_counts if subtitle_id not in subtitle_owner
    )
    unexpected_set.update(unit_id for unit_id in legacy_counts if unit_id not in target_ids)
    covered = sorted(set(covered))
    missing = sorted(set(missing))
    duplicate = sorted(set(duplicate))
    unexpected = sorted(unexpected_set)

    if missing:
        findings.append(
            _finding(
                "TIMELINE_DUB_COVERAGE_MISSING",
                "blocking",
                "应有中文配音的语义单元尚未覆盖。",
                missing,
                "补齐通过 CQC 的候选片段。",
            )
        )
    if duplicate:
        findings.append(
            _finding(
                "TIMELINE_DUB_COVERAGE_DUPLICATED",
                "blocking",
                "同一语义单元被多个配音片段重复覆盖。",
                duplicate,
                "保留唯一正确片段并移除重复项。",
            )
        )
    if unexpected:
        findings.append(
            _finding(
                "TIMELINE_UNEXPECTED_DUB_CONTENT",
                "blocking",
                "时间线含有应保留原声、跳过或不属于当前计划的配音。",
                unexpected,
                "移除错误片段并复核语音资格。",
            )
        )

    if payload.phase == "delivery":
        clip_ids = {clip.clip_id for clip in payload.clips}
        if set(payload.dub_subtitle_clip_ids) != clip_ids:
            findings.append(
                _finding(
                    "DUB_SUBTITLE_CLIP_COVERAGE_STALE",
                    "blocking",
                    "配音字幕与当前可听配音片段不一致。",
                    sorted(clip_ids ^ set(payload.dub_subtitle_clip_ids)),
                    "从当前可听时间线重新生成或同步配音字幕。",
                )
            )
        if payload.dub_subtitle_source_revision != payload.current_timeline_revision:
            findings.append(
                _finding(
                    "DUB_SUBTITLE_SOURCE_REVISION_STALE",
                    "blocking",
                    "配音字幕不是基于当前时间线修订生成的。",
                    [],
                    "使用当前修订重新生成配音字幕。",
                )
            )

    repeated_binding_ranges: dict[tuple[str, str], tuple[int, int]] = {}
    repeated_binding_representatives: dict[tuple[str, str], str] = {}
    clips_by_candidate_group: dict[
        tuple[str, str],
        list[DubbingTimelineClip],
    ] = {}
    for clip in payload.clips:
        clips_by_candidate_group.setdefault(
            (clip.candidate_id, clip.group_id),
            [],
        ).append(clip)
    for key, slices in clips_by_candidate_group.items():
        target_bindings = {
            tuple(item.target_subtitle_ids)
            for item in slices
        }
        if len(slices) > 1 and len(target_bindings) == 1:
            repeated_binding_ranges[key] = (
                min(item.timeline_start_ms for item in slices),
                max(item.timeline_end_ms for item in slices),
            )
            repeated_binding_representatives[key] = min(
                slices,
                key=lambda item: (
                    item.timeline_start_ms,
                    int(item.source_start_ms or 0),
                    item.clip_id,
                ),
            ).clip_id

    for clip in payload.clips:
        if clip.source_revision != payload.current_source_revision:
            findings.append(
                _finding(
                    "TIMELINE_CLIP_SOURCE_REVISION_STALE",
                    "blocking",
                    "配音片段引用了过期计划或字幕修订。",
                    [clip.clip_id],
                    "重新规划或重新采用当前修订的候选。",
                )
            )

    for clip in payload.clips:
        if clip.cqc_status != "passed" and clip.manual_review_reason_codes:
            findings.append(
                _finding(
                    "TIMELINE_CLIP_MANUAL_REVIEW_MARKED",
                    "warning",
                    "该配音候选未完整通过 CQC，已保留可听结果供人工复核。",
                    [clip.clip_id],
                    "在配音分组中试听；可接受则完成，否则重生成该片段。",
                )
            )
        elif clip.cqc_status == "needs_review":
            findings.append(
                _finding(
                    "TIMELINE_CLIP_CQC_REVIEW_RECOMMENDED",
                    "warning",
                    "自动质检未发现阻断问题，仍建议抽听确认主观音质。",
                    [clip.clip_id],
                    "批量流程可继续；最终交付前抽听音色、情绪和自然度。",
                )
            )
        elif clip.cqc_status != "passed":
            findings.append(
                _finding(
                    "TIMELINE_CLIP_CQC_NOT_PASSED",
                    "blocking",
                    "时间线含有未完整通过 CQC 的候选。",
                    [clip.clip_id],
                    "完成自动检测与实际听审后再采用。",
                )
            )
        if (
            clip.expected_audio_sha256 is not None or clip.current_audio_sha256 is not None
        ) and clip.expected_audio_sha256 != clip.current_audio_sha256:
            findings.append(
                _finding(
                    "TIMELINE_CLIP_AUDIO_FINGERPRINT_MISMATCH",
                    "blocking",
                    "时间线音频文件与通过 CQC 时冻结的文件不一致。",
                    [clip.clip_id],
                    "对当前实际音频重新完成自动检测与听审。",
                )
            )
        expected_speakers = {
            expected_by_id[unit_id].speaker_id for unit_id in clip.unit_ids if unit_id in expected_by_id
        }
        if expected_speakers != {clip.speaker_id}:
            findings.append(
                _finding(
                    "TIMELINE_CLIP_SPEAKER_MISMATCH",
                    "blocking",
                    "配音片段的说话人与当前计划不一致。",
                    [clip.clip_id],
                    "按当前生成分组和说话人重新采用候选。",
                )
            )
        repeated_binding_range = repeated_binding_ranges.get(
            (clip.candidate_id, clip.group_id)
        )
        if (
            repeated_binding_range is not None
            and repeated_binding_representatives.get(
                (clip.candidate_id, clip.group_id)
            )
            != clip.clip_id
        ):
            continue
        placement_start_ms, placement_end_ms = repeated_binding_range or (
            clip.timeline_start_ms,
            clip.timeline_end_ms,
        )
        placement_units = [
            expected_by_id[unit_id]
            for unit_id in clip.unit_ids
            if unit_id in expected_by_id
        ]
        if len(placement_units) > 1:
            group_anchor_start_ms = min(
                unit.source_anchor_start_ms for unit in placement_units
            )
            group_anchor_end_ms = max(
                unit.source_anchor_end_ms for unit in placement_units
            )
            if (
                placement_end_ms
                < group_anchor_start_ms - payload.frame_tolerance_ms
                or placement_start_ms
                > group_anchor_end_ms + payload.frame_tolerance_ms
            ):
                code = "TIMELINE_CLIP_MISSES_SEMANTIC_ANCHOR"
                findings.append(
                    _finding(
                        code,
                        (
                            "warning"
                            if code in clip.manual_review_reason_codes
                            else "blocking"
                        ),
                        "整组配音没有落在对应原语义范围附近。",
                        [clip.clip_id, *[unit.unit_id for unit in placement_units]],
                        "按整组原语义起止范围重新放置，不要只按空位排列。",
                    )
                )
            group_scene_ends = [
                int(unit.scene_end_ms)
                for unit in placement_units
                if unit.scene_end_ms is not None
            ]
            if (
                group_scene_ends
                and placement_end_ms
                > max(group_scene_ends) + payload.frame_tolerance_ms
            ):
                code = "TIMELINE_CLIP_OVERRUNS_SCENE"
                findings.append(
                    _finding(
                        code,
                        (
                            "warning"
                            if code in clip.manual_review_reason_codes
                            else "blocking"
                        ),
                        "整组配音越过了最后的场景或演示边界。",
                        [clip.clip_id, placement_units[-1].unit_id],
                        "优先压缩表达、重生成或安全剪辑后重新落位。",
                    )
                )
            continue
        repeated_binding_scene_end_ms = None
        repeated_binding_last_unit_id = None
        if repeated_binding_range is not None:
            repeated_binding_units = [
                expected_by_id[unit_id]
                for unit_id in clip.unit_ids
                if unit_id in expected_by_id
            ]
            scene_units = [
                unit
                for unit in repeated_binding_units
                if unit.scene_end_ms is not None
            ]
            if scene_units:
                repeated_binding_scene_end_ms = max(
                    int(unit.scene_end_ms) for unit in scene_units
                )
                repeated_binding_last_unit_id = max(
                    scene_units,
                    key=lambda unit: (
                        int(unit.scene_end_ms or 0),
                        int(unit.source_anchor_end_ms),
                        unit.unit_id,
                    ),
                ).unit_id
        for unit_id in clip.unit_ids:
            unit = expected_by_id.get(unit_id)
            if unit is None:
                continue
            tolerance = payload.frame_tolerance_ms
            if (
                placement_end_ms < unit.source_anchor_start_ms - tolerance
                or placement_start_ms > unit.source_anchor_end_ms + tolerance
            ):
                code = "TIMELINE_CLIP_MISSES_SEMANTIC_ANCHOR"
                findings.append(
                    _finding(
                        code,
                        (
                            "warning"
                            if code in clip.manual_review_reason_codes
                            else "blocking"
                        ),
                        "配音片段没有落在对应原语义位置附近。",
                        [clip.clip_id, unit_id],
                        "按原语义锚点重新放置，不要只按空位排列。",
                    )
                )
            scene_check_end_ms = clip.timeline_end_ms
            scene_limit_ms = unit.scene_end_ms
            if repeated_binding_range is not None:
                if unit_id != repeated_binding_last_unit_id:
                    continue
                scene_check_end_ms = placement_end_ms
                scene_limit_ms = repeated_binding_scene_end_ms
            if (
                scene_limit_ms is not None
                and scene_check_end_ms > scene_limit_ms + tolerance
            ):
                code = "TIMELINE_CLIP_OVERRUNS_SCENE"
                findings.append(
                    _finding(
                        code,
                        (
                            "warning"
                            if code in clip.manual_review_reason_codes
                            else "blocking"
                        ),
                        "配音片段越过了场景或演示开始边界。",
                        [clip.clip_id, unit_id],
                        "优先压缩表达、重生成或安全剪辑后重新落位。",
                    )
                )

    slices_by_candidate: dict[
        tuple[str, str],
        list[DubbingTimelineClip],
    ] = {}
    for clip in payload.clips:
        if clip.source_start_ms is None or clip.source_end_ms is None:
            continue
        slices_by_candidate.setdefault(
            (clip.candidate_id, clip.group_id),
            [],
        ).append(clip)
    for slices in slices_by_candidate.values():
        ordered_slices = sorted(
            slices,
            key=lambda item: (
                int(item.source_start_ms or 0),
                int(item.source_end_ms or 0),
                item.clip_id,
            ),
        )
        for left, right in zip(ordered_slices, ordered_slices[1:]):
            if int(right.source_start_ms or 0) < int(left.source_end_ms or 0):
                findings.append(
                    _finding(
                        "TIMELINE_DUB_SOURCE_CROP_OVERLAP",
                        "blocking",
                        "同一候选的正式切片重复使用了同一段源音频。",
                        [left.clip_id, right.clip_id],
                        "按声学切点重新分区，确保有效发音只覆盖一次。",
                    )
                )
                continue
            if (
                int(right.source_start_ms or 0) == int(left.source_end_ms or 0)
                and abs(
                    right.timeline_start_ms
                    - left.timeline_end_ms
                    - right.timeline_gap_before_ms
                )
                > payload.frame_tolerance_ms
            ):
                findings.append(
                    _finding(
                        "TIMELINE_DUB_INTERNAL_PAUSE_DISTORTED",
                        "blocking",
                        "同一候选的连续切片被额外拉开，内部停顿已经失真。",
                        [left.clip_id, right.clip_id],
                        "保持相邻源裁切连续，只在候选之间分配剩余时间。",
                    )
                )

    for underfill in continuous_speech_underfill_assessments(payload):
        findings.append(
            _finding(
                "TIMELINE_DUB_CONTINUOUS_SPEECH_UNDERFILLED",
                "warning",
                "原声仍在连续说话，但合成配音在对应位置留下了过长空洞。",
                [underfill.left_clip_id, underfill.right_clip_id],
                "先恢复附近稳定语速；若当前组存在词级安全的语义分句，"
                "把多余外部空档按原人声停顿层级分配到组内，否则定点重生成。",
            )
        )

    ordered = sorted(
        payload.clips,
        key=lambda clip: (
            clip.timeline_start_ms,
            clip.timeline_end_ms,
            clip.dub_lane,
            clip.clip_id,
        ),
    )
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if right.timeline_start_ms >= left.timeline_end_ms:
                break
            if _overlap_has_source_dialogue_evidence(
                left,
                right,
                expected_by_id,
                tolerance_ms=payload.frame_tolerance_ms,
            ):
                continue
            findings.append(
                _finding(
                    "TIMELINE_UNEXPLAINED_DUB_OVERLAP",
                    "blocking",
                    "配音片段发生未声明的重叠。",
                    [left.clip_id, right.clip_id],
                    "单人对白放在同一轨道顺排；只有真实同时说话才声明重叠。",
                )
            )

    non_overlap_lanes = {clip.dub_lane for clip in payload.clips if not clip.intentional_overlap}
    if len(non_overlap_lanes) > 1:
        findings.append(
            _finding(
                "TIMELINE_UNNECESSARY_DUB_LANES",
                "warning",
                "非重叠对白分散在多条合成配音轨道。",
                sorted(str(lane) for lane in non_overlap_lanes),
                "将非同时说话的片段归并到主配音轨。",
            )
        )

    return DubbingTimelineAuditReport(
        current_source_revision=payload.current_source_revision,
        current_timeline_revision=payload.current_timeline_revision,
        phase=payload.phase,
        status=_status_from_findings(findings),
        covered_unit_ids=covered,
        missing_unit_ids=missing,
        duplicate_unit_ids=duplicate,
        unexpected_unit_ids=unexpected,
        findings=findings,
    )


def continuous_speech_underfill_assessments(
    payload: DubbingTimelineAuditInput,
    *,
    eligible_left_group_ids: set[str] | None = None,
    eligible_left_unit_ids: set[str] | None = None,
    main_lane_only: bool = False,
) -> list[DubbingContinuousBoundaryUnderfill]:
    """Check only source-continuous boundaries, without running full CQC."""

    expected_by_id = {unit.unit_id: unit for unit in payload.expected_units}
    expected_index_by_id = {
        unit.unit_id: index for index, unit in enumerate(payload.expected_units)
    }
    candidate_blocks: list[list[DubbingTimelineClip]] = []
    for clip in sorted(
        (
            item
            for item in payload.clips
            if not main_lane_only or item.dub_lane == 0
        ),
        key=lambda item: (
            item.timeline_start_ms,
            item.timeline_end_ms,
            item.dub_lane,
            item.clip_id,
        ),
    ):
        block_key = (clip.candidate_id, clip.group_id, clip.dub_lane)
        if candidate_blocks and (
            candidate_blocks[-1][0].candidate_id,
            candidate_blocks[-1][0].group_id,
            candidate_blocks[-1][0].dub_lane,
        ) == block_key:
            candidate_blocks[-1].append(clip)
        else:
            candidate_blocks.append([clip])

    assessments: list[DubbingContinuousBoundaryUnderfill] = []
    for left_block, right_block in zip(candidate_blocks, candidate_blocks[1:]):
        left = left_block[-1]
        right = right_block[0]
        left_block_unit_ids = {
            unit_id for clip in left_block for unit_id in clip.unit_ids
        }
        if (
            left.dub_lane != right.dub_lane
            or (
                eligible_left_group_ids is not None
                and left.group_id not in eligible_left_group_ids
            )
            or (
                eligible_left_unit_ids is not None
                and not left_block_unit_ids.intersection(eligible_left_unit_ids)
            )
        ):
            continue
        left_units = [
            expected_by_id[unit_id]
            for clip in left_block
            for unit_id in clip.unit_ids
            if unit_id in expected_by_id
        ]
        right_units = [
            expected_by_id[unit_id]
            for clip in right_block
            for unit_id in clip.unit_ids
            if unit_id in expected_by_id
        ]
        if not left_units or not right_units:
            continue
        left_tail = max(
            left_units,
            key=lambda item: expected_index_by_id[item.unit_id],
        )
        right_head = min(
            right_units,
            key=lambda item: expected_index_by_id[item.unit_id],
        )
        if (
            expected_index_by_id[right_head.unit_id]
            != expected_index_by_id[left_tail.unit_id] + 1
            or not left_tail.source_continuous_with_next
        ):
            continue
        left_speakers = {item.speaker_id for item in left_units}
        right_speakers = {item.speaker_id for item in right_units}
        left_scenes = {item.scene_id for item in left_units}
        right_scenes = {item.scene_id for item in right_units}
        if (
            len(left_speakers) != 1
            or left_speakers != right_speakers
            or len(left_scenes) != 1
            or left_scenes != right_scenes
        ):
            continue
        source_gap_ms = max(
            0,
            min(item.source_anchor_start_ms for item in right_units)
            - max(item.source_anchor_end_ms for item in left_units),
        )
        timeline_gap_ms = max(
            0,
            min(item.timeline_start_ms for item in right_block)
            - max(item.timeline_end_ms for item in left_block),
        )
        allowed_gap_ms = max(
            500,
            source_gap_ms + max(250, payload.frame_tolerance_ms),
        )
        if source_gap_ms <= 250 and timeline_gap_ms > allowed_gap_ms:
            assessments.append(
                DubbingContinuousBoundaryUnderfill(
                    left_group_id=left.group_id,
                    left_clip_id=left.clip_id,
                    right_clip_id=right.clip_id,
                    source_gap_ms=source_gap_ms,
                    timeline_gap_ms=timeline_gap_ms,
                    allowed_gap_ms=allowed_gap_ms,
                )
            )
    return assessments


def build_current_timeline_audit_input(
    draft,
    *,
    current_timeline_revision: str,
    phase: str = "delivery",
    current_audio_sha256_by_clip_id: dict[str, str | None] | None = None,
) -> DubbingTimelineAuditInput:
    """Project current timeline projected into the strict audit contract."""

    plan = draft.dubbing_production.active_plan
    if plan is None:
        raise ValueError("current project has no active dubbing generation plan")
    snapshot = build_project_snapshot(draft)
    boundary_by_units = {
        (boundary.left_unit_id, boundary.right_unit_id): boundary
        for boundary in snapshot.boundaries
    }
    semantic_units = list(plan.semantic_units)
    expected_units = [
        DubbingTimelineExpectedUnit(
            unit_id=unit.unit_id,
            subtitle_ids=list(unit.subtitle_ids),
            speaker_id=unit.speaker_id,
            scene_id=unit.scene_id,
            speech_policy=unit.speech_policy,
            source_anchor_start_ms=unit.source_anchor_start_ms,
            source_anchor_end_ms=unit.source_anchor_end_ms,
            scene_end_ms=unit.scene_end_ms,
            source_continuous_with_next=(
                index + 1 < len(semantic_units)
                and _source_backed_continuous_boundary(
                    unit,
                    semantic_units[index + 1],
                    boundary_by_units.get(
                        (
                            unit.unit_id,
                            semantic_units[index + 1].unit_id,
                        )
                    ),
                )
            ),
        )
        for index, unit in enumerate(semantic_units)
    ]
    audio_hashes_provided = current_audio_sha256_by_clip_id is not None
    audio_sha256_by_clip_id = current_audio_sha256_by_clip_id or {}
    reports = {report.candidate_id: report for report in draft.dubbing_production.candidate_reports}
    frozen_inputs = {item.candidate_id: item for item in draft.dubbing_production.candidate_inputs}
    groups_by_id = {group.group_id: group for group in plan.groups}
    groups_by_subtitles = {tuple(group.subtitle_ids): group for group in plan.groups}
    units_by_id = {unit.unit_id: unit for unit in plan.semantic_units}
    timeline_clips: list[DubbingTimelineClip] = []
    group_reviews: dict[tuple[str, str], list] = {}
    for review in draft.dubbing_production.group_reviews:
        if (
            review.source_revision == plan.source_revision
            and review.plan_revision == plan.plan_revision
        ):
            group_reviews.setdefault(
                (review.group_id, review.candidate_id),
                [],
            ).append(review)
    for raw in draft.timeline_clips:
        clip = timeline_clip_timing.normalize_audio_clip(
            dict(raw),
            frame_rate=draft.source_media.frame_rate,
            timeline_duration_ms=draft.source_media.duration_ms,
        )
        if clip.get("track_id") != "dub":
            continue
        clip_id = str(clip.get("clip_id") or "")
        start_ms = _integer_or_none(clip.get("start_ms"))
        end_ms = _integer_or_none(clip.get("end_ms"))
        if not clip_id or start_ms is None or end_ms is None or end_ms <= start_ms:
            continue
        target_subtitle_ids = tuple(str(value) for value in clip.get("target_subtitle_ids") or [] if str(value))
        if not target_subtitle_ids and clip.get("subtitle_id"):
            target_subtitle_ids = (str(clip["subtitle_id"]),)
        group = groups_by_id.get(str(clip.get("dubbing_group_id") or ""))
        if group is None:
            group = groups_by_subtitles.get(target_subtitle_ids)
        if group is None:
            target_set = set(target_subtitle_ids)
            group = next(
                (candidate for candidate in plan.groups if target_set and target_set == set(candidate.subtitle_ids)),
                None,
            )
        target_subtitle_set = set(target_subtitle_ids)
        unit_ids = (
            [
                unit_id
                for unit_id in group.unit_ids
                if unit_id in units_by_id
                and set(units_by_id[unit_id].subtitle_ids) & target_subtitle_set
            ]
            if group is not None
            else [
                unit.unit_id
                for unit in plan.semantic_units
                if set(unit.subtitle_ids) & target_subtitle_set
            ]
        )
        identity = str(clip.get("candidate_id") or clip.get("result_id") or clip_id)
        report = reports.get(identity) or reports.get(str(clip.get("result_id") or ""))
        frozen = frozen_inputs.get(identity) or frozen_inputs.get(str(clip.get("result_id") or ""))
        authoritative_cqc = bool(
            report is not None
            and frozen is not None
            and group is not None
            and report.candidate_id == frozen.candidate_id
            and report.source_revision == plan.source_revision
            and frozen.source_revision == plan.source_revision
            and report.plan_revision == plan.plan_revision
            and frozen.plan_revision == plan.plan_revision
            and report.group_id == group.group_id
            and frozen.group_id == group.group_id
            and (
                not audio_hashes_provided
                or (frozen.audio_sha256 is not None and frozen.audio_sha256 == audio_sha256_by_clip_id.get(clip_id))
            )
        )
        cqc_status = report.overall_status if authoritative_cqc and report is not None else "not_reviewed"
        if cqc_status not in {
            "passed",
            "warning",
            "failed",
            "needs_review",
            "not_reviewed",
        }:
            cqc_status = "not_reviewed"
        matching_reviews = (
            group_reviews.get((group.group_id, identity), [])
            if group is not None
            else []
        )
        timeline_clips.append(
            DubbingTimelineClip(
                clip_id=clip_id,
                candidate_id=identity,
                group_id=(group.group_id if group is not None else str(clip.get("dubbing_group_id") or "unplanned")),
                unit_ids=unit_ids or [f"unplanned:{clip_id}"],
                target_subtitle_ids=list(target_subtitle_ids),
                speaker_id=(group.speaker_id if group is not None else "unknown"),
                scene_id=(group.scene_id if group is not None else None),
                dub_lane=max(0, int(clip.get("dub_lane") or 0)),
                timeline_start_ms=start_ms,
                timeline_end_ms=end_ms,
                source_start_ms=_integer_or_none(clip.get("source_start_ms")),
                source_end_ms=_integer_or_none(clip.get("source_end_ms")),
                timeline_gap_before_ms=max(
                    0,
                    int(clip.get("dubbing_timeline_gap_before_ms") or 0),
                ),
                source_revision=plan.source_revision,
                plan_revision=plan.plan_revision,
                cqc_status=cqc_status,
                manual_review_reason_codes=(
                    list(
                        dict.fromkeys(
                            review.reason_code
                            for review in matching_reviews
                            if review.reason_code
                        )
                    )
                ),
                expected_audio_sha256=(frozen.audio_sha256 if audio_hashes_provided and frozen is not None else None),
                current_audio_sha256=(audio_sha256_by_clip_id.get(clip_id) if audio_hashes_provided else None),
                intentional_overlap=bool(clip.get("intentional_overlap")),
            )
        )
    subtitle_clip_ids = list(
        dict.fromkeys(clip_id for subtitle in draft.dub_subtitles for clip_id in subtitle.source_clip_ids)
    )
    return DubbingTimelineAuditInput(
        current_source_revision=dubbing_source_revision(draft),
        current_timeline_revision=current_timeline_revision,
        phase=phase,
        dub_subtitle_source_revision=draft.dub_subtitle_source_revision,
        expected_units=expected_units,
        clips=timeline_clips,
        dub_subtitle_clip_ids=subtitle_clip_ids,
    )


def _overlap_has_source_dialogue_evidence(
    left: DubbingTimelineClip,
    right: DubbingTimelineClip,
    expected_by_id: dict[str, DubbingTimelineExpectedUnit],
    *,
    tolerance_ms: int,
) -> bool:
    if not (left.intentional_overlap and right.intentional_overlap):
        return False
    if left.speaker_id == right.speaker_id:
        return False
    left_units = [expected_by_id[unit_id] for unit_id in left.unit_ids if unit_id in expected_by_id]
    right_units = [expected_by_id[unit_id] for unit_id in right.unit_ids if unit_id in expected_by_id]
    return any(
        min(left_unit.source_anchor_end_ms, right_unit.source_anchor_end_ms)
        > max(
            left_unit.source_anchor_start_ms,
            right_unit.source_anchor_start_ms,
        )
        + tolerance_ms
        for left_unit in left_units
        for right_unit in right_units
    )


def _snapshot_units(draft, cues_by_id: dict) -> list[DubbingSemanticUnit]:
    subtitles_by_spoken: dict[str, list] = {}
    for subtitle in draft.localized_subtitles:
        if subtitle.spoken_segment_id:
            subtitles_by_spoken.setdefault(
                subtitle.spoken_segment_id,
                [],
            ).append(subtitle)
    raw_units: list[tuple] = []
    if draft.localized_spoken_segments:
        for segment in draft.localized_spoken_segments:
            display_subtitles = sorted(
                subtitles_by_spoken.get(segment.segment_id, []),
                key=lambda item: (
                    item.start_ms,
                    item.end_ms,
                    item.subtitle_id,
                ),
            )
            # Once the display track exists it owns the current editable
            # subtitle membership.  A timeline merge can legitimately move
            # all display subtitles away from an older spoken segment; do not
            # resurrect that orphan as a synthetic dubbing unit.
            if draft.localized_subtitles and not display_subtitles:
                continue
            raw_units.extend(
                _spoken_segment_raw_units(
                    segment,
                    display_subtitles,
                    cues_by_id,
                )
            )
    else:
        for subtitle in draft.localized_subtitles:
            raw_units.append(
                (
                    subtitle.subtitle_id,
                    [subtitle.subtitle_id],
                    subtitle.source_cue_ids or ([subtitle.linked_cue_id] if subtitle.linked_cue_id else []),
                    subtitle.source_word_ids,
                    subtitle.start_ms,
                    subtitle.end_ms,
                    subtitle.text,
                    subtitle.tts_text or subtitle.text,
                    None,
                    None,
                )
            )
    raw_units.sort(key=lambda item: (item[4], item[5], item[0]))
    units: list[DubbingSemanticUnit] = []
    for (
        unit_id,
        subtitle_ids,
        source_cue_ids,
        source_word_ids,
        start_ms,
        end_ms,
        display_text,
        spoken_text,
        explicit_anchor_start_ms,
        explicit_anchor_end_ms,
    ) in raw_units:
        source_cues = [cues_by_id[cue_id] for cue_id in source_cue_ids if cue_id in cues_by_id]
        known_speaker_ids = {
            str(cue.speaker_id)
            for cue in source_cues
            if cue.speaker_id
        }
        if len(known_speaker_ids) == 1:
            speaker_id = next(iter(known_speaker_ids))
        elif known_speaker_ids:
            speaker_id = "mixed"
        else:
            speaker_id = "unknown"
        routes = {cue.audio_route for cue in source_cues}
        if routes and routes <= {"preserve_original_audio"}:
            speech_policy = "preserve_original"
        elif routes & {"manual_review"} or not source_cues:
            speech_policy = "needs_review"
        else:
            speech_policy = "translate"
        valid_starts = [int(cue.start_ms) for cue in source_cues if cue.start_ms is not None]
        valid_ends = [int(cue.end_ms) for cue in source_cues if cue.end_ms is not None]
        anchor_start = (
            int(explicit_anchor_start_ms)
            if explicit_anchor_start_ms is not None
            else min(valid_starts)
            if valid_starts
            else start_ms
        )
        anchor_end = (
            int(explicit_anchor_end_ms)
            if explicit_anchor_end_ms is not None
            else max(valid_ends)
            if valid_ends
            else end_ms
        )
        if anchor_end <= anchor_start:
            anchor_start, anchor_end = start_ms, end_ms
        units.append(
            DubbingSemanticUnit(
                unit_id=unit_id,
                subtitle_ids=list(dict.fromkeys(subtitle_ids)),
                source_cue_ids=list(dict.fromkeys(source_cue_ids)),
                source_word_ids=list(dict.fromkeys(source_word_ids)),
                speaker_id=speaker_id,
                start_ms=start_ms,
                end_ms=end_ms,
                source_anchor_start_ms=anchor_start,
                source_anchor_end_ms=anchor_end,
                display_text=display_text,
                spoken_text=spoken_text,
                speech_policy=speech_policy,
                decision_reason_codes=["derived_from_current_localization"],
            )
        )
    return units


def _snapshot_boundaries(
    draft,
    units: list[DubbingSemanticUnit],
    cues_by_id: dict,
) -> list[DubbingBoundaryEvidence]:
    ordered_cues = sorted(
        draft.cues,
        key=lambda cue: (
            cue.start_ms if cue.start_ms is not None else 2**62,
            cue.end_ms if cue.end_ms is not None else 2**62,
            cue.cue_id,
        ),
    )
    gap_samples: dict[str, list[int]] = {}
    for left, right in zip(units, units[1:]):
        if left.speaker_id != right.speaker_id:
            continue
        gap = max(0, right.source_anchor_start_ms - left.source_anchor_end_ms)
        if gap:
            gap_samples.setdefault(left.speaker_id, []).append(gap)
    gap_thresholds = {
        speaker: _adaptive_long_gap_threshold(samples) for speaker, samples in gap_samples.items() if len(samples) >= 4
    }
    audio_features = draft.transcription.audio_boundary_features if draft.transcription is not None else []
    audio_by_words = {(feature.left_word_id, feature.right_word_id): feature for feature in audio_features}
    word_order = (
        {word.word_id: (word.start_ms, word.end_ms, word.word_id) for word in draft.transcription.words}
        if draft.transcription is not None
        else {}
    )
    boundaries: list[DubbingBoundaryEvidence] = []
    for left, right in zip(units, units[1:]):
        gap = max(0, right.source_anchor_start_ms - left.source_anchor_end_ms)
        selected_cues = set(left.source_cue_ids) | set(right.source_cue_ids)
        intervening = [
            cue
            for cue in ordered_cues
            if cue.cue_id not in selected_cues
            and cue.start_ms is not None
            and cue.end_ms is not None
            and cue.end_ms > left.source_anchor_end_ms
            and cue.start_ms < right.source_anchor_start_ms
        ]
        left_words = sorted(
            left.source_word_ids,
            key=lambda word_id: word_order.get(
                word_id,
                (2**62, 2**62, word_id),
            ),
        )
        right_words = sorted(
            right.source_word_ids,
            key=lambda word_id: word_order.get(
                word_id,
                (2**62, 2**62, word_id),
            ),
        )
        audio_feature = audio_by_words.get((left_words[-1], right_words[0])) if left_words and right_words else None
        confidence = audio_feature.confidence if audio_feature is not None else "none"
        speech_between = (
            True
            if intervening
            else False
            if gap == 0 or confidence in {"medium", "high"}
            else None
        )
        threshold = gap_thresholds.get(left.speaker_id)
        if gap == 0:
            pause_classification = "continuous"
        elif (
            threshold is not None
            and gap >= _MINIMUM_LONG_SILENCE_MS
            and gap > threshold
            and confidence in {"medium", "high"}
        ):
            pause_classification = "long_silence"
        elif confidence in {"medium", "high"}:
            pause_classification = "natural_pause"
        else:
            pause_classification = "unknown"
        evidence_codes = ["subtitle_timing_gap"]
        if intervening:
            evidence_codes.append("intervening_source_speech")
        if audio_feature is not None:
            evidence_codes.append("aligned_word_audio_boundary")
        if threshold is not None:
            evidence_codes.append("speaker_adaptive_gap_baseline")
        boundaries.append(
            DubbingBoundaryEvidence(
                boundary_id=f"{left.unit_id}:{right.unit_id}",
                left_unit_id=left.unit_id,
                right_unit_id=right.unit_id,
                gap_ms=gap,
                same_speaker=left.speaker_id == right.speaker_id,
                same_scene=None,
                speech_between=speech_between,
                pause_classification=pause_classification,
                low_energy_confidence=confidence,
                semantic_relation="unknown",
                hard_boundary=(
                    left.speaker_id != right.speaker_id or bool(intervening) or pause_classification == "long_silence"
                ),
                evidence_codes=evidence_codes,
            )
        )
    return boundaries


def _spoken_segment_raw_units(
    segment,
    display_subtitles: list,
    cues_by_id: dict,
) -> list[tuple]:
    display_source_cue_ids = list(
        dict.fromkeys(
            cue_id
            for item in display_subtitles
            for cue_id in item.source_cue_ids
        )
    )
    display_source_word_ids = list(
        dict.fromkeys(
            word_id
            for item in display_subtitles
            for word_id in item.source_word_ids
        )
    )
    fallback = (
        segment.segment_id,
        [item.subtitle_id for item in display_subtitles] or [segment.segment_id],
        display_source_cue_ids or segment.source_cue_ids,
        display_source_word_ids or segment.source_word_ids,
        display_subtitles[0].start_ms if display_subtitles else segment.start_ms,
        display_subtitles[-1].end_ms if display_subtitles else segment.end_ms,
        " ".join(item.text for item in display_subtitles).strip() or segment.text,
        segment.text,
        None if display_subtitles else segment.start_ms,
        None if display_subtitles else segment.end_ms,
    )
    if not display_subtitles:
        return [fallback]

    subtitle_speakers = [_speaker_id_for_source_cues(item.source_cue_ids, cues_by_id) for item in display_subtitles]
    needs_split = (
        len(display_subtitles) > _DEFAULT_MAXIMUM_GROUP_SUBTITLES
        or len(segment.text) > _DEFAULT_MAXIMUM_GROUP_CHARACTERS
        or len(set(subtitle_speakers)) > 1
        or any(
            _STRONG_SENTENCE_END.search(
                (item.tts_text or item.text).strip()
            )
            for item in display_subtitles[:-1]
        )
    )
    if not needs_split:
        return [fallback]

    joined_spoken = "".join((item.tts_text or item.text).strip() for item in display_subtitles)
    if _split_equivalence_text(joined_spoken) != _split_equivalence_text(segment.text):
        return [fallback]

    chunks: list[list] = []
    current: list = []
    current_speaker: str | None = None
    current_characters = 0
    for subtitle, speaker_id in zip(
        display_subtitles,
        subtitle_speakers,
    ):
        spoken_text = (subtitle.tts_text or subtitle.text).strip()
        crosses_speaker = bool(current and current_speaker != speaker_id)
        exceeds_size = bool(
            current
            and (
                len(current) >= _DEFAULT_MAXIMUM_GROUP_SUBTITLES
                or current_characters + len(spoken_text) > _DEFAULT_MAXIMUM_GROUP_CHARACTERS
            )
        )
        if crosses_speaker or exceeds_size:
            chunks.append(current)
            current = []
            current_characters = 0
        if not current:
            current_speaker = speaker_id
        current.append(subtitle)
        current_characters += len(spoken_text)
        if len(current) >= _DEFAULT_MAXIMUM_GROUP_SUBTITLES or (
            _STRONG_SENTENCE_END.search(spoken_text)
        ):
            chunks.append(current)
            current = []
            current_speaker = None
            current_characters = 0
    if current:
        chunks.append(current)

    if len(chunks) <= 1:
        return [fallback]
    # Display subtitles already carry the current word-backed semantic
    # windows.  A dense translated chunk is a generation/planning concern;
    # redistributing the complete spoken turn by character count would move
    # later clauses away from their source words and manufacture false gaps.
    chunk_windows = [
        (chunk[0].start_ms, chunk[-1].end_ms)
        for chunk in chunks
    ]

    raw_units: list[tuple] = []
    for index, (chunk, (start_ms, end_ms)) in enumerate(
        zip(chunks, chunk_windows),
        start=1,
    ):
        raw_units.append(
            (
                f"{segment.segment_id}__part_{index:03d}",
                [item.subtitle_id for item in chunk],
                list(dict.fromkeys(cue_id for item in chunk for cue_id in item.source_cue_ids)),
                list(dict.fromkeys(word_id for item in chunk for word_id in item.source_word_ids)),
                start_ms,
                end_ms,
                " ".join(item.text for item in chunk).strip(),
                "".join((item.tts_text or item.text).strip() for item in chunk),
                start_ms,
                end_ms,
            )
        )
    return raw_units


def _speaker_id_for_source_cues(
    source_cue_ids: list[str],
    cues_by_id: dict,
) -> str:
    speakers = {str(cues_by_id[cue_id].speaker_id or "unknown") for cue_id in source_cue_ids if cue_id in cues_by_id}
    return next(iter(speakers)) if len(speakers) == 1 else "mixed"


def _split_equivalence_text(text: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", text)
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def _adaptive_long_gap_threshold(samples: list[int]) -> float:
    median = float(statistics.median(samples))
    deviations = [abs(value - median) for value in samples]
    mad = float(statistics.median(deviations))
    return median + 3.0 * max(mad, 1.0)


def _starts_new_island(boundary: DubbingBoundaryEvidence) -> bool:
    return (
        boundary.hard_boundary
        or not boundary.same_speaker
        or boundary.same_scene is False
        or boundary.speech_between is True
        or boundary.pause_classification == "long_silence"
        or boundary.semantic_relation == "break"
        or _boundary_is_ambiguous(boundary)
    )


def _boundary_is_ambiguous(boundary: DubbingBoundaryEvidence) -> bool:
    return (
        boundary.same_scene is None
        or boundary.speech_between is None
        or boundary.pause_classification == "unknown"
        or boundary.semantic_relation == "unknown"
    )


def _partition_island(
    units: list[DubbingSemanticUnit],
    boundary_by_left: dict[str, DubbingBoundaryEvidence],
    *,
    maximum_effective_speech_ms: int,
    maximum_text_pressure: float,
) -> dict[str, object]:
    """Partition by editable speech load, never by a fixed subtitle count."""

    def safe_boundary_after(index: int) -> bool:
        if index == len(units) - 1:
            return True
        boundary = boundary_by_left[units[index].unit_id]
        if boundary.no_break_with_next:
            return False
        return bool(
            boundary.pause_classification == "natural_pause"
            or boundary.low_energy_confidence in {"medium", "high"}
            or boundary.gap_ms >= 80
            or _STRONG_SENTENCE_END.search(units[index].spoken_text)
        )

    groups: list[list[DubbingSemanticUnit]] = []
    needs_review_unit_ids: list[str] = []
    cursor = 0
    while cursor < len(units):
        speech_ms = 0
        safe_end: int | None = None
        scan = cursor
        while scan < len(units):
            unit = units[scan]
            unit_speech_ms = _estimated_speech_ms(unit.spoken_text)
            target_ms = max(
                1,
                (unit.scene_end_ms or unit.end_ms) - unit.start_ms,
            )
            pressure = unit_speech_ms / target_ms
            if (
                speech_ms + unit_speech_ms > maximum_effective_speech_ms
                or pressure > maximum_text_pressure
            ):
                break
            speech_ms += unit_speech_ms
            if safe_boundary_after(scan):
                safe_end = scan + 1
            scan += 1
        if scan == len(units):
            end = len(units)
        elif safe_end is not None:
            end = safe_end
        else:
            end = max(cursor + 1, scan + 1)
            needs_review_unit_ids.extend(
                unit.unit_id for unit in units[cursor:end]
            )
        groups.append(units[cursor:end])
        cursor = end
    return {
        "groups": groups,
        "needs_review_unit_ids": list(dict.fromkeys(needs_review_unit_ids)),
    }


def _estimated_speech_ms(text: str) -> int:
    """Estimate mixed Chinese/product-name speech without counting Latin letters as syllables."""

    latin_tokens = re.findall(
        r"[A-Za-z0-9]+(?:[.+-][A-Za-z0-9]+)*",
        text,
    )
    without_latin = re.sub(
        r"[A-Za-z0-9]+(?:[.+-][A-Za-z0-9]+)*",
        "",
        text,
    )
    spoken_characters = re.sub(
        r"[\s，。！？、；：,.!?;:\-—…]",
        "",
        without_latin,
    )
    return max(
        400,
        len(spoken_characters) * 180 + len(latin_tokens) * 320,
    )


def _append_pause_findings(
    payload: DubbingCandidateCqcInput,
    findings: list[DubbingQualityFinding],
) -> None:
    assert payload.audio is not None
    baseline = payload.audio.expected_pause_baseline_ms
    for pause in payload.audio.internal_pauses:
        if pause.expected_semantic_boundary:
            continue
        if baseline is None:
            findings.append(
                _finding(
                    "CANDIDATE_INTERNAL_PAUSE_REVIEW_REQUIRED",
                    "warning",
                    "候选内部存在非语义边界停顿，但当前没有项目停顿基准。",
                    [payload.candidate_id],
                    "实际听审；若破坏连贯性，优先重生成或缩组。",
                )
            )
            continue
        if pause.duration_ms > (
            baseline * payload.policy.unexpected_pause_multiplier
        ):
            action = (
                "仅在确认安全音素边界时剪辑，否则缩组重新生成。"
                if pause.safe_edit_boundary
                else "缩小台词分组并重新生成，不要硬切连续音素。"
            )
            findings.append(
                _finding(
                    "CANDIDATE_UNEXPECTED_LONG_INTERNAL_PAUSE",
                    "blocking",
                    "候选在不应断开的语义内部出现异常长停顿。",
                    [payload.candidate_id],
                    action,
                )
            )


def _append_acoustic_evidence_findings(
    payload: DubbingCandidateCqcInput,
    findings: list[DubbingQualityFinding],
) -> None:
    assert payload.audio is not None
    audio = payload.audio
    missing_fields = [
        name
        for name, value in (
            ("speech_start_ms", audio.speech_start_ms),
            ("speech_end_ms", audio.speech_end_ms),
            ("speech_span_ms", audio.speech_span_ms),
            ("voiced_duration_ms", audio.voiced_duration_ms),
            ("speaking_rate_ratio", audio.speaking_rate_ratio),
            (
                "project_speaking_rate_ratio_min",
                audio.project_speaking_rate_ratio_min,
            ),
            (
                "project_speaking_rate_ratio_max",
                audio.project_speaking_rate_ratio_max,
            ),
        )
        if value is None
    ]
    if (
        missing_fields
        or not audio.voiced_spans
        or not audio.aligned_words
        or not audio.gap_evidence
    ):
        missing_evidence = [
            name
            for name, present in (
                ("voiced_spans", bool(audio.voiced_spans)),
                ("aligned_words", bool(audio.aligned_words)),
                ("gap_evidence", bool(audio.gap_evidence)),
            )
            if not present
        ]
        findings.append(
            _finding(
                "CANDIDATE_ACOUSTIC_EVIDENCE_INCOMPLETE",
                "info",
                "候选还缺少部分实际发声、倍速或音频间隙证据。",
                [payload.candidate_id, *missing_fields, *missing_evidence],
                "保留可听结果并补充当前组证据；缺少主观证据本身不阻断放轨。",
            )
        )

    preferred_sources = {"waveform", "energy", "vad", "word_alignment"}
    for gap in audio.gap_evidence:
        sources = set(gap.evidence_sources)
        if not sources.intersection(preferred_sources) or not gap.evidence_ids:
            findings.append(
                _finding(
                    "CANDIDATE_GAP_AUDIO_EVIDENCE_MISSING",
                    "warning",
                    "音频间隙缺少可复核的真实波形、能量、VAD 或逐词对齐证据。",
                    [payload.candidate_id, gap.gap_id],
                    "不要凭时间盒剪辑；保留该间隙或补充真实音频证据后再处理。",
                )
            )
        if (
            gap.edit_decision in {"remove", "shorten"}
            and gap.boundary_confidence == "ambiguous"
            and not (
            {"waveform", "listening"}.issubset(sources)
            and gap.review_evidence_ids
            )
        ):
            findings.append(
                _finding(
                    "CANDIDATE_AMBIGUOUS_GAP_REVIEW_REQUIRED",
                    "warning",
                    "模糊间隙边界还没有同时完成波形与试听复核。",
                    [payload.candidate_id, gap.gap_id],
                    "保存波形和试听复核证据；无证据不得通过时间线剪辑门。",
                )
            )
        if gap.edit_decision in {"remove", "shorten"} and gap.safe_edit_boundary is not True:
            findings.append(
                _finding(
                    "CANDIDATE_GAP_EDIT_BOUNDARY_UNSAFE",
                    "blocking",
                    "间隙计划删除或缩短，但切点安全性尚未被证据确认。",
                    [payload.candidate_id, gap.gap_id],
                    "用 VAD、逐词对齐和波形确认两侧发音完整，否则保留或重生成。",
                )
            )

    continuous_phrase_edits = [
        gap
        for gap in audio.gap_evidence
        if gap.kind == "internal"
        and gap.semantic_role == "continuous_phrase"
        and gap.edit_decision in {"remove", "shorten"}
        and gap.retained_duration_ms < gap.duration_ms
    ]
    if len(continuous_phrase_edits) >= 2:
        findings.append(
            _finding(
                "CANDIDATE_OVER_FRAGMENTED_PHRASE",
                "warning",
                "同一段连续话术需要多次句内拼接，成品容易出现卡顿或音色、气息断裂。",
                [
                    payload.candidate_id,
                    *(gap.gap_id for gap in continuous_phrase_edits),
                ],
                "停止继续剪碎；保持完整语义重新生成，必要时缩小生成组后再尝试。",
            )
        )

    has_speed_exception = bool(
        audio.content_speed_exception_reason
        and audio.content_speed_exception_evidence_ids
    )
    rate = audio.speaking_rate_ratio
    if rate is not None and not 1.0 <= rate <= 1.3 and not has_speed_exception:
        findings.append(
            _finding(
                "CANDIDATE_SPEAKING_RATE_OUT_OF_RANGE",
                "warning",
                "候选使用的归一化朗读倍速不在 1.0–1.3 的常规范围内。",
                [payload.candidate_id],
                "回到常规倍速重生成；内容确需快慢变化时提交明确的内容倍速例外证据。",
            )
        )
    if has_speed_exception:
        findings.append(
            _finding(
                "CANDIDATE_CONTENT_SPEED_EXCEPTION_APPLIED",
                "info",
                "该候选使用了有证据的内容倍速例外。",
                [payload.candidate_id, *audio.content_speed_exception_evidence_ids],
                "保留例外原因和来源证据供最终复核。",
            )
        )
def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return _TOKEN_PATTERN.findall(normalized)


def _join_spelled_latin_letters(text: str) -> str:
    return re.sub(
        r"(?<![a-z0-9])(?:[a-z]\s+)+[a-z](?![a-z0-9])",
        lambda match: re.sub(r"\s+", "", match.group(0)),
        text,
    )


def _comparison_tokens(text: str) -> list[str]:
    """Return ASR-comparison units insensitive to common written forms.

    ASR engines may join spelled Latin letters (``C E O`` -> ``CEO``) and
    render digit-by-digit Chinese years as Arabic digits
    (``一九七一`` -> ``1971``).  Compare those forms at the character level
    while leaving ordinary Chinese content unchanged.
    """

    chinese_digits = {
        "零": "0",
        "〇": "0",
        "一": "1",
        "二": "2",
        "三": "3",
        "四": "4",
        "五": "5",
        "六": "6",
        "七": "7",
        "八": "8",
        "九": "9",
    }
    normalized = unicodedata.normalize(
        "NFKC",
        _normalize_asr_written_numbers(text),
    ).casefold()
    normalized = _join_spelled_latin_letters(normalized)
    # A TTS engine may spell a URL while ASR joins the same letters into one
    # token and omits the written separators.  Keep the comparison about what
    # was pronounced: dots, slashes and URL punctuation between ASCII units do
    # not create artificial missing/extra tokens.  Chinese punctuation and
    # ordinary word boundaries remain unchanged.
    normalized = re.sub(
        r"(?<=[a-z0-9])[./:_-]+(?=[a-z0-9])",
        "",
        normalized,
    )
    compared: list[str] = []
    for token in _TOKEN_PATTERN.findall(normalized):
        if token in {"地", "得"}:
            compared.append("的")
        elif token in {"她", "它"}:
            compared.append("他")
        elif token in chinese_digits:
            compared.append(chinese_digits[token])
        elif token.isascii() and token.isdigit():
            compared.extend(token)
        else:
            compared.append(token)
    return compared


def transcript_pronunciation_tokens(text: str) -> list[str]:
    """Return tone-aware Mandarin pronunciation units for ASR comparison.

    Candidate ASR is evidence about the sound, not authoritative Chinese
    orthography.  This projection lets genuinely homophonic spellings compare
    equal without changing the plan text.  Mandarin tones remain part of each
    unit, so a different tone still requires review.
    """

    normalized = unicodedata.normalize(
        "NFKC",
        _normalize_asr_written_numbers(text),
    ).casefold()
    normalized = _join_spelled_latin_letters(normalized)
    normalized = re.sub(
        r"(?<=[a-z0-9])[./:_-]+(?=[a-z0-9])",
        "",
        normalized,
    )
    normalized = normalized.translate(
        str.maketrans(
            {
                "地": "的",
                "得": "的",
                "她": "他",
                "它": "他",
                "零": "0",
                "〇": "0",
                "一": "1",
                "二": "2",
                "三": "3",
                "四": "4",
                "五": "5",
                "六": "6",
                "七": "7",
                "八": "8",
                "九": "9",
            }
        )
    )
    raw_units = lazy_pinyin(
        normalized,
        style=Style.TONE3,
        neutral_tone_with_five=True,
        errors=lambda value: list(value),
    )
    return [unit.casefold() for raw in raw_units for unit in re.findall(r"[a-z0-9]+", raw.casefold())]


def _normalize_asr_written_numbers(text: str) -> str:
    """Expand only ASR forms whose spoken reading is context-deterministic."""

    def spoken(match: re.Match[str]) -> str:
        return text_normalizer.normalize_spoken_numbers(match.group(0)).rstrip("。")

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = re.sub(r"\d+(?:\.\d+)?\s*%", spoken, normalized)
    normalized = re.sub(r"(?<!\d)\d{4}\s*年", spoken, normalized)
    normalized = re.sub(r"(?<=\d)\.(?=\d)", "点", normalized)
    return re.sub(
        r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)",
        " ",
        normalized,
    )


def _integer_or_none(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _status_from_findings(
    findings: list[DubbingQualityFinding],
) -> ReviewStatus:
    if any(finding.severity == "blocking" for finding in findings):
        return "failed"
    if any(finding.severity == "warning" for finding in findings):
        return "warning"
    return "passed"


def _finding(
    code: str,
    severity: str,
    message: str,
    entity_ids: list[str],
    recommended_action: str,
) -> DubbingQualityFinding:
    return DubbingQualityFinding(
        code=code,
        severity=severity,
        message=message,
        entity_ids=entity_ids,
        recommended_action=recommended_action,
    )


__all__ = [
    "audit_timeline",
    "build_generation_plan",
    "build_current_timeline_audit_input",
    "build_project_snapshot",
    "compare_transcripts",
    "dubbing_source_revision",
    "evaluate_candidate",
    "rebalance_planned_timeline_clips",
    "rebalance_selected_group_timeline_clips",
    "rebalance_selected_group_with_adjacent_window_timeline_clips",
    "rebase_compatible_generation_plan",
    "split_planned_timeline_clips",
]
