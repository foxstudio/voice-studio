"""Deterministic speed continuity policy for localized dubbing.

A short source-speed change must not reset the normal delivery speed for every
following group.  The stable baseline therefore comes from recent ordinary
speech, while the immediately preceding task still supplies the audible
continuity fact.  Separated-vocals timing is retained as review evidence, but
does not itself authorize crossing the ordinary adjacent-speed limit.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Any, Iterable


MIN_DUBBING_SPEED = 1.0
MAX_DUBBING_SPEED = 1.3
DUBBING_CAPACITY_REPAIR_SPEED = 1.35
DUBBING_TIGHT_DIALOGUE_REPAIR_SPEED = 1.8
MAX_ADJACENT_SPEED_DELTA = 0.05
LARGE_SOURCE_PACE_CHANGE_RATIO = 0.20
NEIGHBORHOOD_BASELINE_CLIPS = 3


@dataclass(frozen=True)
class DubbingSpeedDecision:
    speed: float
    proposed_speed: float
    baseline_speed: float | None = None
    baseline_clip_id: str | None = None
    source_pace_ratio: float | None = None
    content_speed_exception_reason: str | None = None
    content_speed_exception_evidence_ids: tuple[str, ...] = ()

    def generation_parameters(self) -> dict[str, Any]:
        parameters: dict[str, Any] = {"speed": self.speed}
        if self.content_speed_exception_reason:
            parameters["content_speed_exception_reason"] = (
                self.content_speed_exception_reason
            )
            parameters["content_speed_exception_evidence_ids"] = list(
                self.content_speed_exception_evidence_ids
            )
        return parameters


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _bounded_speed(value: float) -> float:
    return round(max(MIN_DUBBING_SPEED, min(MAX_DUBBING_SPEED, value)), 2)


def text_pressure_speed(text: str, duration_ms: int) -> float:
    compact = re.sub(r"[^\u3400-\u9fffA-Za-z0-9]", "", text)
    pressure = len(compact) / max(0.5, duration_ms / 1000)
    if pressure >= 7.0:
        return 1.25
    if pressure >= 6.0:
        return 1.18
    if pressure >= 5.2:
        return 1.1
    return 1.0


def _formal_previous_clip(draft: Any, group: Any) -> Any | None:
    current_subtitle_ids = set(_value(group, "subtitle_ids", []) or [])
    current_start_ms = int(_value(group, "target_start_ms", 0) or 0)
    eligible = []
    for clip in _value(draft, "timeline_clips", []) or []:
        if _value(clip, "track_id", "dub") != "dub":
            continue
        if _value(clip, "status", "ready") != "ready":
            continue
        if int(_value(clip, "dub_lane", 0) or 0) != 0:
            continue
        clip_subtitle_ids = set(
            _value(clip, "target_subtitle_ids", [])
            or [_value(clip, "subtitle_id", "")]
        )
        if current_subtitle_ids.intersection(clip_subtitle_ids):
            continue
        clip_end_ms = int(
            _value(clip, "end_ms", _value(clip, "timeline_end_ms", 0)) or 0
        )
        if clip_end_ms <= 0 or clip_end_ms > current_start_ms:
            continue
        eligible.append(clip)
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda clip: int(
            _value(clip, "end_ms", _value(clip, "timeline_end_ms", 0)) or 0
        ),
    )


def _workflow_speed(draft: Any, clip: Any) -> float | None:
    clip_id = str(_value(clip, "clip_id", "") or "")
    task_id = str(_value(clip, "task_id", "") or "")
    result_id = str(_value(clip, "result_id", "") or "")
    for workflow in reversed(list(_value(draft, "tts_tasks", []) or [])):
        identity_match = any(
            (
                expected
                and str(_value(workflow, field, "") or "") == expected
            )
            for field, expected in (
                ("generation_task_id", task_id),
                ("result_id", result_id),
                ("timeline_clip_id", clip_id),
            )
        )
        if not identity_match:
            continue
        for stage in _value(workflow, "stages", []) or []:
            if _value(stage, "kind") != "generation":
                continue
            parameters = _value(stage, "parameters", {}) or {}
            raw_speed = _value(parameters, "speed")
            if raw_speed is not None:
                return _bounded_speed(float(raw_speed))
    return None


def _workflow_speed_exception(draft: Any, clip: Any) -> bool:
    clip_id = str(_value(clip, "clip_id", "") or "")
    task_id = str(_value(clip, "task_id", "") or "")
    result_id = str(_value(clip, "result_id", "") or "")
    for workflow in reversed(list(_value(draft, "tts_tasks", []) or [])):
        identity_match = any(
            expected and str(_value(workflow, field, "") or "") == expected
            for field, expected in (
                ("generation_task_id", task_id),
                ("result_id", result_id),
                ("timeline_clip_id", clip_id),
            )
        )
        if not identity_match:
            continue
        for stage in _value(workflow, "stages", []) or []:
            if _value(stage, "kind") != "generation":
                continue
            parameters = _value(stage, "parameters", {}) or {}
            return bool(
                _value(parameters, "content_speed_exception_reason")
                or _value(parameters, "content_speed_exception_evidence_ids", [])
            )
    return False


def _recent_formal_clips(draft: Any, group: Any) -> list[Any]:
    current_subtitle_ids = set(_value(group, "subtitle_ids", []) or [])
    current_start_ms = int(_value(group, "target_start_ms", 0) or 0)
    eligible = []
    for clip in _value(draft, "timeline_clips", []) or []:
        if _value(clip, "track_id", "dub") != "dub":
            continue
        if _value(clip, "status", "ready") != "ready":
            continue
        if int(_value(clip, "dub_lane", 0) or 0) != 0:
            continue
        clip_subtitle_ids = set(
            _value(clip, "target_subtitle_ids", [])
            or [_value(clip, "subtitle_id", "")]
        )
        if current_subtitle_ids.intersection(clip_subtitle_ids):
            continue
        clip_end_ms = int(
            _value(clip, "end_ms", _value(clip, "timeline_end_ms", 0)) or 0
        )
        if 0 < clip_end_ms <= current_start_ms:
            eligible.append(clip)
    return sorted(
        eligible,
        key=lambda clip: int(
            _value(clip, "end_ms", _value(clip, "timeline_end_ms", 0)) or 0
        ),
        reverse=True,
    )


def _stable_neighborhood_baseline(
    draft: Any,
    group: Any,
) -> tuple[float | None, str | None]:
    facts: list[tuple[float, str]] = []
    for clip in _recent_formal_clips(draft, group):
        review_codes = set(
            _value(clip, "manual_review_reason_codes", []) or []
        )
        if "candidate_supervised_review_pending" in review_codes:
            continue
        speed = _workflow_speed(draft, clip) or _reviewed_speed(draft, clip)
        if speed is None or _workflow_speed_exception(draft, clip):
            continue
        facts.append((speed, str(_value(clip, "clip_id", "") or "")))
        if len(facts) >= NEIGHBORHOOD_BASELINE_CLIPS:
            break
    if not facts:
        return None, None
    return (
        _bounded_speed(float(statistics.median(speed for speed, _ in facts))),
        facts[0][1] or None,
    )


def _previous_group(draft: Any, group: Any) -> Any | None:
    production = _value(draft, "dubbing_production")
    plan = _value(production, "active_plan")
    current_start_ms = int(_value(group, "target_start_ms", 0) or 0)
    eligible = [
        item
        for item in (_value(plan, "groups", []) or [])
        if _value(item, "group_id") != _value(group, "group_id")
        and int(_value(item, "target_end_ms", 0) or 0) <= current_start_ms
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda item: int(_value(item, "target_end_ms", 0) or 0),
    )


def _reserved_workflow_speed_for_group(
    draft: Any,
    group: Any,
) -> tuple[float | None, str | None, bool]:
    target_ids = set(_value(group, "subtitle_ids", []) or [])
    for workflow in reversed(list(_value(draft, "tts_tasks", []) or [])):
        for stage in _value(workflow, "stages", []) or []:
            if _value(stage, "kind") != "generation":
                continue
            parameters = _value(stage, "parameters", {}) or {}
            workflow_targets = set(
                _value(
                    parameters,
                    "video_localization_target_subtitle_ids",
                    _value(parameters, "target_subtitle_ids", []),
                )
                or []
            )
            if workflow_targets != target_ids:
                continue
            raw_speed = _value(parameters, "speed")
            if raw_speed is None:
                continue
            return (
                _bounded_speed(float(raw_speed)),
                str(_value(workflow, "workflow_id", "") or "") or None,
                bool(
                    _value(parameters, "content_speed_exception_reason")
                    or _value(
                        parameters,
                        "content_speed_exception_evidence_ids",
                        [],
                    )
                ),
            )
    return None, None, False


def _reviewed_speed(draft: Any, clip: Any) -> float | None:
    gate = _value(clip, "timeline_edit_gate", {}) or {}
    gate_speed = _value(gate, "speaking_rate_ratio")
    if gate_speed is not None:
        return _bounded_speed(float(gate_speed))
    candidate_id = str(_value(clip, "candidate_id", "") or "")
    production = _value(draft, "dubbing_production")
    for report in reversed(list(_value(production, "candidate_reports", []) or [])):
        if candidate_id and str(_value(report, "candidate_id", "") or "") != candidate_id:
            continue
        evidence = _value(report, "audio_evidence")
        ratio = _value(evidence, "speaking_rate_ratio")
        if ratio is not None:
            return _bounded_speed(float(ratio))
    return None


def _group_for_clip(draft: Any, clip: Any) -> Any | None:
    production = _value(draft, "dubbing_production")
    plan = _value(production, "active_plan")
    clip_subtitle_ids = set(
        _value(clip, "target_subtitle_ids", [])
        or [_value(clip, "subtitle_id", "")]
    )
    for group in _value(plan, "groups", []) or []:
        if clip_subtitle_ids.intersection(_value(group, "subtitle_ids", []) or []):
            return group
    return None


def _source_cue_ids(draft: Any, group: Any) -> list[str]:
    production = _value(draft, "dubbing_production")
    plan = _value(production, "active_plan")
    unit_ids = set(_value(group, "unit_ids", []) or [])
    return list(
        dict.fromkeys(
            str(cue_id)
            for unit in _value(plan, "semantic_units", []) or []
            if _value(unit, "unit_id") in unit_ids
            for cue_id in (_value(unit, "source_cue_ids", []) or [])
        )
    )


def _word_ids_for_group(draft: Any, group: Any) -> list[str]:
    cue_ids = set(_source_cue_ids(draft, group))
    return list(
        dict.fromkeys(
            str(word_id)
            for cue in _value(draft, "cues", []) or []
            if str(_value(cue, "cue_id", "") or "") in cue_ids
            for word_id in (_value(cue, "source_word_ids", []) or [])
        )
    )


def _pace_for_word_ids(words: Iterable[Any], word_ids: list[str]) -> float | None:
    selected_ids = set(word_ids)
    selected = [word for word in words if _value(word, "word_id") in selected_ids]
    if len(selected) < 2:
        return None
    selected.sort(key=lambda word: int(_value(word, "start_ms", 0) or 0))
    start_ms = int(_value(selected[0], "start_ms", 0) or 0)
    end_ms = int(_value(selected[-1], "end_ms", 0) or 0)
    if end_ms <= start_ms:
        return None
    return len(selected) / ((end_ms - start_ms) / 1000)


def _source_pace_evidence(
    draft: Any,
    current_group: Any,
    previous_group: Any | None,
) -> tuple[float | None, tuple[str, ...]]:
    transcription = _value(draft, "transcription")
    stems = _value(draft, "stems")
    if (
        transcription is None
        or previous_group is None
        or _value(stems, "separation_status") != "completed"
        or _value(transcription, "source_track_id") != "vocals"
        or _value(transcription, "alignment_source_track_id") != "vocals"
    ):
        return None, ()
    words = _value(transcription, "words", []) or []
    previous_word_ids = _word_ids_for_group(draft, previous_group)
    current_word_ids = _word_ids_for_group(draft, current_group)
    previous_pace = _pace_for_word_ids(words, previous_word_ids)
    current_pace = _pace_for_word_ids(words, current_word_ids)
    if not previous_pace or not current_pace:
        return None, ()
    revision_id = str(_value(transcription, "revision_id", "unknown") or "unknown")
    evidence_ids = (
        f"separated-vocals:{revision_id}",
        f"source-words:{previous_word_ids[0]}:{previous_word_ids[-1]}",
        f"source-words:{current_word_ids[0]}:{current_word_ids[-1]}",
    )
    return current_pace / previous_pace, evidence_ids


def decide_dubbing_speed(draft: Any, group: Any) -> DubbingSpeedDecision:
    proposed = text_pressure_speed(
        str(_value(group, "spoken_text", "") or ""),
        int(_value(group, "target_end_ms", 0) or 0)
        - int(_value(group, "target_start_ms", 0) or 0),
    )
    previous_clip = _formal_previous_clip(draft, group)
    previous_group = (
        _group_for_clip(draft, previous_clip)
        if previous_clip is not None
        else _previous_group(draft, group)
    )
    baseline_clip_id = (
        str(_value(previous_clip, "clip_id", "") or "") or None
        if previous_clip is not None
        else None
    )
    immediate_baseline = (
        _workflow_speed(draft, previous_clip)
        or _reviewed_speed(draft, previous_clip)
        if previous_clip is not None
        else None
    )
    immediate_exception = (
        _workflow_speed_exception(draft, previous_clip)
        if previous_clip is not None
        else False
    )
    immediate_pending_review = bool(
        previous_clip is not None
        and "candidate_supervised_review_pending"
        in set(_value(previous_clip, "manual_review_reason_codes", []) or [])
    )
    if immediate_baseline is None and previous_group is not None:
        (
            immediate_baseline,
            baseline_clip_id,
            immediate_exception,
        ) = _reserved_workflow_speed_for_group(
            draft,
            previous_group,
        )
    stable_baseline, stable_clip_id = _stable_neighborhood_baseline(
        draft,
        group,
    )
    if (
        immediate_baseline is not None
        and not immediate_exception
        and not immediate_pending_review
    ):
        # Normal adjacent speech is the first continuity reference.  This
        # allows a gradual return toward the project baseline in 0.05 steps.
        baseline = immediate_baseline
    else:
        # An adjacent source-backed exception must not become the new normal.
        baseline = stable_baseline or immediate_baseline
        if stable_baseline is not None:
            baseline_clip_id = stable_clip_id
    if baseline is None:
        return DubbingSpeedDecision(speed=proposed, proposed_speed=proposed)

    source_pace_ratio, _evidence_ids = _source_pace_evidence(
        draft,
        group,
        previous_group,
    )
    # A stable neighborhood keeps one exceptional clip from resetting the
    # delivery baseline, but an immediately preceding *normal* clip is still
    # the audible continuity anchor.  A change that stays within ±0.05 of that
    # clip is ordinary continuity and must not be mislabeled as a source-pace
    # exception merely because it is farther from the older median.
    speed = _bounded_speed(
        max(
            baseline - MAX_ADJACENT_SPEED_DELTA,
            min(baseline + MAX_ADJACENT_SPEED_DELTA, proposed),
        )
    )
    if (
        immediate_baseline is not None
        and not immediate_exception
        and not immediate_pending_review
    ):
        speed = _bounded_speed(
            max(
                immediate_baseline - MAX_ADJACENT_SPEED_DELTA,
                min(immediate_baseline + MAX_ADJACENT_SPEED_DELTA, speed),
            )
        )
    return DubbingSpeedDecision(
        speed=speed,
        proposed_speed=proposed,
        baseline_speed=baseline,
        baseline_clip_id=baseline_clip_id,
        source_pace_ratio=(
            round(source_pace_ratio, 3) if source_pace_ratio is not None else None
        ),
    )


def decide_dubbing_capacity_repair_speed(
    draft: Any,
    group: Any,
    *,
    ordinary_speed_baseline: float | None = None,
) -> DubbingSpeedDecision:
    """Use one evidence-labelled speed exception for a proven tight window."""

    ordinary = decide_dubbing_speed(draft, group)
    group_id = str(_value(group, "group_id", "") or "")
    if ordinary_speed_baseline is not None:
        baseline = float(ordinary_speed_baseline)
        if not 0.5 <= baseline <= 2.0:
            raise ValueError("invalid frozen dubbing speed baseline")
        speed = round(min(2.0, baseline + MAX_ADJACENT_SPEED_DELTA), 2)
        return DubbingSpeedDecision(
            speed=speed,
            proposed_speed=speed,
            baseline_speed=baseline,
            baseline_clip_id=ordinary.baseline_clip_id,
            source_pace_ratio=ordinary.source_pace_ratio,
            content_speed_exception_reason=(
                f"当前组容量恢复，在冻结基线 {baseline:.2f} 的允许差值内使用 {speed:.2f} 倍速度。"
            ),
            content_speed_exception_evidence_ids=(
                f"timeline-capacity:{group_id}:bounded-speed-retry",
                f"frozen-baseline:{baseline:.2f}",
            ),
        )
    target_subtitle_ids = {
        str(item)
        for item in _value(group, "subtitle_ids", []) or []
    }
    target_start_ms = int(_value(group, "target_start_ms", 0) or 0)
    target_end_ms = int(_value(group, "target_end_ms", 0) or 0)
    formal_clip_ids = [
        str(_value(clip, "clip_id", "") or "")
        for clip in _value(draft, "timeline_clips", []) or []
        if (
            str(_value(clip, "dubbing_group_id", "") or "") == group_id
            or {
                str(item)
                for item in _value(clip, "target_subtitle_ids", []) or []
            }
            == target_subtitle_ids
        )
        and _value(clip, "track_id", "dub") == "dub"
        and int(_value(clip, "dub_lane", 0) or 0) == 0
        and _value(clip, "status", "ready") == "ready"
    ]
    if not formal_clip_ids:
        return ordinary
    prior_capacity_repair_speeds: list[float] = []
    for workflow in _value(draft, "tts_tasks", []) or []:
        for stage in _value(workflow, "stages", []) or []:
            if _value(stage, "kind") != "generation":
                continue
            parameters = _value(stage, "parameters", {}) or {}
            if not isinstance(parameters, dict):
                continue
            if str(
                parameters.get("video_localization_dubbing_group_id") or ""
            ) != group_id:
                continue
            evidence_ids = parameters.get(
                "content_speed_exception_evidence_ids"
            ) or []
            if not any(
                str(evidence_id).startswith("timeline-capacity:")
                for evidence_id in evidence_ids
            ):
                continue
            prior_capacity_repair_speeds.append(
                float(parameters.get("speed") or 1.0)
            )
    second_level_repair = (
        prior_capacity_repair_speeds
        and max(prior_capacity_repair_speeds)
        >= DUBBING_CAPACITY_REPAIR_SPEED
    )
    repair_speed = max(
        ordinary.speed,
        (
            DUBBING_TIGHT_DIALOGUE_REPAIR_SPEED
            if second_level_repair
            else DUBBING_CAPACITY_REPAIR_SPEED
        ),
    )
    return DubbingSpeedDecision(
        speed=repair_speed,
        proposed_speed=max(ordinary.proposed_speed, repair_speed),
        baseline_speed=ordinary.baseline_speed,
        baseline_clip_id=ordinary.baseline_clip_id,
        source_pace_ratio=ordinary.source_pace_ratio,
        content_speed_exception_reason=(
            "当前正式候选在严格时间线审计中占用过长，"
            f"本次只对该组使用 {repair_speed:.2f} 倍容量修复速度。"
        ),
        content_speed_exception_evidence_ids=(
            f"timeline-capacity:{group_id}:{target_start_ms}:{target_end_ms}",
            f"formal-clips:{formal_clip_ids[0]}:{formal_clip_ids[-1]}",
            *(
                (
                    "timeline-capacity:bounded-second-level-repair",
                )
                if second_level_repair
                else ()
            ),
        ),
    )


__all__ = [
    "DUBBING_CAPACITY_REPAIR_SPEED",
    "DUBBING_TIGHT_DIALOGUE_REPAIR_SPEED",
    "DubbingSpeedDecision",
    "LARGE_SOURCE_PACE_CHANGE_RATIO",
    "MAX_ADJACENT_SPEED_DELTA",
    "NEIGHBORHOOD_BASELINE_CLIPS",
    "decide_dubbing_capacity_repair_speed",
    "decide_dubbing_speed",
    "text_pressure_speed",
]
