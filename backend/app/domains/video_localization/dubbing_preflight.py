"""Pure, conservative capacity evidence before localized-dubbing generation.

This module deliberately does not infer a universal speaking rate.  It can
estimate only from up to three frozen, current-plan takes made by the same TTS
engine, speaker and exact frozen speed.  The result is advisory except for an
objectively empty timeline window.
"""

from __future__ import annotations

from statistics import median
from typing import Any
import unicodedata

from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.schemas.video_localization_dubbing_production import DubbingGenerationGroup
from app.schemas.video_localization_dubbing_preflight import (
    DubbingGroupPreflightResult,
    DubbingPreflightEstimateEvidence,
    DubbingPreflightReferenceObservation,
    DubbingPreflightTextUnits,
)


_CURRENT_CANDIDATE_STATUSES = {"success"}
_FORMAL_CLIP_STATUSES = {"ready"}
_MAX_REFERENCE_OBSERVATIONS = 3


def _value(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _as_positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _as_nonnegative_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _text_units(text: str) -> DubbingPreflightTextUnits:
    """Count Unicode letter/number code points with script uncertainty explicit."""

    families: set[str] = set()
    count = 0
    for character in unicodedata.normalize("NFKC", text):
        category = unicodedata.category(character)
        if not category.startswith(("L", "N")):
            continue
        count += 1
        name = unicodedata.name(character, "")
        if "LATIN" in name:
            families.add("latin")
        elif any(label in name for label in ("CJK", "HIRAGANA", "KATAKANA", "HANGUL")):
            families.add("east_asian")
        elif category.startswith("L"):
            families.add("other_letter")

    uncertainty_codes: list[str] = []
    if not count:
        uncertainty_codes.append("no_unicode_letter_number_units")
    if len(families) != 1:
        uncertainty_codes.append("mixed_or_indeterminate_script_units")
    return DubbingPreflightTextUnits(
        unit_count=count,
        script_profile=("single_family" if count and len(families) == 1 else "mixed_or_indeterminate"),
        uncertainty_codes=uncertainty_codes,
    )


def _generation_parameters(workflow: Any) -> dict[str, Any] | None:
    for stage in _value(workflow, "stages", []) or []:
        if _value(stage, "kind") == "generation":
            parameters = _value(stage, "parameters", {}) or {}
            return parameters if isinstance(parameters, dict) else None
    return None


def _workflow_matches_candidate(workflow: Any, candidate_id: str) -> bool:
    identifiers = (
        _value(workflow, "generation_task_id", ""),
        _value(workflow, "result_id", ""),
        _value(workflow, "timeline_clip_id", ""),
    )
    return any(
        identifier
        and candidate_id in {str(identifier), f"candidate_{identifier}"}
        for identifier in identifiers
    )


def _settings_for_candidate(draft: Any, candidate_id: str) -> tuple[str | None, float | None]:
    for workflow in reversed(list(_value(draft, "tts_tasks", []) or [])):
        if not _workflow_matches_candidate(workflow, candidate_id):
            continue
        parameters = _generation_parameters(workflow)
        if parameters is None:
            continue
        engine_id = str(parameters.get("engine_id") or "").strip() or None
        try:
            speed = float(parameters["speed"])
        except (KeyError, TypeError, ValueError):
            speed = None
        return engine_id, speed if speed and speed > 0 else None
    return None, None


def _settings_for_group(draft: Any, group: Any, speed: float) -> str | None:
    group_id = str(_value(group, "group_id", "") or "")
    production = _value(draft, "dubbing_production")
    plan = _value(production, "active_plan")
    plan_revision = _value(plan, "plan_revision")
    for workflow in reversed(list(_value(draft, "tts_tasks", []) or [])):
        parameters = _generation_parameters(workflow)
        if parameters is None:
            continue
        if str(parameters.get("video_localization_dubbing_group_id") or "") != group_id:
            continue
        if plan_revision is not None and parameters.get("video_localization_dubbing_plan_revision") not in {None, plan_revision}:
            continue
        try:
            workflow_speed = float(parameters["speed"])
        except (KeyError, TypeError, ValueError):
            continue
        if workflow_speed != speed:
            continue
        engine_id = str(parameters.get("engine_id") or "").strip()
        if engine_id:
            return engine_id
    return None


def _discarded_candidate_ids(draft: Any) -> set[str]:
    ui_state = _value(draft, "ui_state", {}) or {}
    discarded = {
        str(value)
        for value in _value(ui_state, "discarded_tts_task_ids", []) or []
        if str(value)
    }
    for raw in _value(draft, "generated_candidates", []) or []:
        if not bool(_value(raw, "discarded", False)):
            continue
        for field in ("candidate_id", "task_id", "result_id", "artifact_id"):
            value = str(_value(raw, field, "") or "")
            if value:
                discarded.add(value)
    return discarded


def _candidate_is_discarded(candidate: Any, discarded_ids: set[str]) -> bool:
    identifiers = {
        str(_value(candidate, field, "") or "")
        for field in ("candidate_id", "artifact_id")
    }
    return any(
        identifier in {discarded_id, f"candidate_{discarded_id}"}
        for identifier in identifiers
        for discarded_id in discarded_ids
        if identifier and discarded_id
    )


def _usable_window(draft: Any, group: Any) -> tuple[int, int]:
    target_start = int(_value(group, "target_start_ms", 0) or 0)
    target_end = int(_value(group, "target_end_ms", 0) or 0)
    group_id = str(_value(group, "group_id", "") or "")
    occupied: list[tuple[int, int]] = []
    for raw in _value(draft, "timeline_clips", []) or []:
        if _value(raw, "track_id", "dub") != "dub":
            continue
        if _value(raw, "status", "ready") not in _FORMAL_CLIP_STATUSES:
            continue
        if int(_value(raw, "dub_lane", 0) or 0) != 0:
            continue
        if str(_value(raw, "dubbing_group_id", "") or "") == group_id:
            continue
        start = _as_nonnegative_int(_value(raw, "start_ms", _value(raw, "timeline_start_ms")))
        end = _as_positive_int(_value(raw, "end_ms", _value(raw, "timeline_end_ms")))
        if start is None or end is None or end <= start:
            continue
        clipped_start = max(target_start, start)
        clipped_end = min(target_end, end)
        if clipped_end > clipped_start:
            occupied.append((clipped_start, clipped_end))

    merged: list[tuple[int, int]] = []
    for start, end in sorted(occupied):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    cursor = target_start
    available: list[tuple[int, int]] = []
    for start, end in merged:
        if start > cursor:
            available.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < target_end:
        available.append((cursor, target_end))
    if not available:
        return target_start, target_start
    center = (target_start + target_end) // 2
    return max(
        available,
        key=lambda interval: (interval[1] - interval[0], -abs((interval[0] + interval[1]) // 2 - center)),
    )


def _current_groups_by_id(draft: Any) -> dict[str, Any]:
    plan = _value(_value(draft, "dubbing_production"), "active_plan")
    return {str(_value(group, "group_id", "") or ""): group for group in _value(plan, "groups", []) or []}


def _reference_observations(
    draft: Any,
    group: Any,
    *,
    speed: float,
    engine_id: str,
) -> tuple[list[DubbingPreflightReferenceObservation], list[str]]:
    production = _value(draft, "dubbing_production")
    plan = _value(production, "active_plan")
    groups_by_id = _current_groups_by_id(draft)
    target_group_id = str(_value(group, "group_id", "") or "")
    target_speaker = str(_value(group, "speaker_id", "") or "")
    target_center = (int(_value(group, "target_start_ms", 0) or 0) + int(_value(group, "target_end_ms", 0) or 0)) // 2
    discarded_ids = _discarded_candidate_ids(draft)
    observations: list[DubbingPreflightReferenceObservation] = []
    uncertainty_codes: set[str] = set()

    for candidate in _value(production, "candidate_inputs", []) or []:
        candidate_id = str(_value(candidate, "candidate_id", "") or "")
        if not candidate_id or _candidate_is_discarded(candidate, discarded_ids):
            continue
        if _value(candidate, "task_status") not in _CURRENT_CANDIDATE_STATUSES:
            continue
        if _value(candidate, "source_revision") != _value(plan, "source_revision") or _value(candidate, "plan_revision") != _value(plan, "plan_revision"):
            continue
        candidate_group_id = str(_value(candidate, "group_id", "") or "")
        candidate_group = groups_by_id.get(candidate_group_id)
        if candidate_group is None or candidate_group_id == target_group_id:
            continue
        if str(_value(candidate_group, "speaker_id", "") or "") != target_speaker:
            continue
        candidate_engine, candidate_speed = _settings_for_candidate(draft, candidate_id)
        if candidate_engine != engine_id or candidate_speed != speed:
            continue
        audio = _value(candidate, "audio")
        speech_span_ms = _as_positive_int(_value(audio, "speech_span_ms"))
        units = _text_units(str(_value(candidate, "expected_spoken_text", "") or ""))
        if speech_span_ms is None or not units.unit_count:
            uncertainty_codes.add("reference_missing_speech_or_text_units")
            continue
        if units.script_profile != "single_family":
            uncertainty_codes.add("reference_mixed_script_text_units")
            continue
        candidate_center = (int(_value(candidate_group, "target_start_ms", 0) or 0) + int(_value(candidate_group, "target_end_ms", 0) or 0)) // 2
        observations.append(DubbingPreflightReferenceObservation(
            candidate_id=candidate_id,
            group_id=candidate_group_id,
            engine_id=engine_id,
            speaker_id=target_speaker,
            speed=speed,
            speech_span_ms=speech_span_ms,
            text_units=units,
            timeline_distance_ms=abs(candidate_center - target_center),
        ))

    observations.sort(key=lambda item: (item.timeline_distance_ms, item.candidate_id))
    return observations[:_MAX_REFERENCE_OBSERVATIONS], sorted(uncertainty_codes)


def assess_group_preflight(
    draft: VideoLocalizationDraft,
    group: DubbingGenerationGroup,
    *,
    speed: float,
    engine_id: str | None = None,
) -> DubbingGroupPreflightResult:
    """Assess one group without mutating the plan, clips, speed or workflows.

    ``engine_id`` is the frozen engine selected for an imminent first
    generation.  Omitting it retains the read-only workflow lookup used for
    existing groups.
    """

    if speed <= 0:
        raise ValueError("frozen speed must be greater than zero")
    target_start = int(_value(group, "target_start_ms", 0) or 0)
    target_end = int(_value(group, "target_end_ms", 0) or 0)
    usable_start, usable_end = _usable_window(draft, group)
    usable_duration = max(0, usable_end - usable_start)
    units = _text_units(str(_value(group, "spoken_text", "") or ""))
    group_id = str(_value(group, "group_id", "") or "")
    resolved_engine_id = (engine_id or "").strip() or _settings_for_group(
        draft, group, speed,
    )

    if usable_end <= usable_start:
        return DubbingGroupPreflightResult(
            group_id=group_id,
            frozen_speed=speed,
            target_start_ms=target_start,
            target_end_ms=target_end,
            usable_start_ms=usable_start,
            usable_end_ms=usable_end,
            usable_duration_ms=0,
            text_units=units,
            estimate_evidence=DubbingPreflightEstimateEvidence(
                method="unavailable", confidence="none",
                uncertainty_codes=["usable_window_empty"],
            ),
            status="blocked",
            reason_codes=["no_usable_window"],
            message="当前主配音轨没有可用时间窗口；请先处理相邻片段的实际占用。",
        )

    uncertainty_codes = list(units.uncertainty_codes)
    if resolved_engine_id is None:
        uncertainty_codes.append("current_engine_or_frozen_speed_not_found")
        observations: list[DubbingPreflightReferenceObservation] = []
        observation_uncertainty: list[str] = []
    elif units.script_profile != "single_family" or not units.unit_count:
        observations = []
        observation_uncertainty = []
    else:
        observations, observation_uncertainty = _reference_observations(
            draft, group, speed=speed, engine_id=resolved_engine_id,
        )
    uncertainty_codes.extend(observation_uncertainty)

    if not observations:
        uncertainty_codes.append("no_comparable_frozen_reference")
        return DubbingGroupPreflightResult(
            group_id=group_id,
            frozen_speed=speed,
            target_start_ms=target_start,
            target_end_ms=target_end,
            usable_start_ms=usable_start,
            usable_end_ms=usable_end,
            usable_duration_ms=usable_duration,
            text_units=units,
            estimate_evidence=DubbingPreflightEstimateEvidence(
                method="unavailable", confidence="none",
                uncertainty_codes=list(dict.fromkeys(uncertainty_codes)),
            ),
            status="warning",
            reason_codes=["speech_duration_estimate_unavailable"],
            message="没有可比的冻结语音参考，无法在生成前可靠估计这段台词时长。",
        )

    estimated_duration = round(units.unit_count * median(
        item.speech_span_ms / item.text_units.unit_count for item in observations
    ))
    confidence = {1: "low", 2: "medium"}.get(len(observations), "high")
    evidence = DubbingPreflightEstimateEvidence(
        method="frozen_same_engine_speaker_speed_text_units-v1",
        confidence=confidence,
        reference_observations=observations,
        uncertainty_codes=list(dict.fromkeys(uncertainty_codes)),
    )
    if estimated_duration > usable_duration:
        return DubbingGroupPreflightResult(
            group_id=group_id, frozen_speed=speed,
            target_start_ms=target_start, target_end_ms=target_end,
            usable_start_ms=usable_start, usable_end_ms=usable_end,
            usable_duration_ms=usable_duration, text_units=units,
            estimated_speech_duration_ms=estimated_duration,
            estimate_evidence=evidence, status="warning",
            reason_codes=["estimated_speech_exceeds_usable_window"],
            message="冻结参考推算的发声时长可能超过当前可用窗口；这是预警，不会阻断生成。",
        )
    if confidence == "low":
        return DubbingGroupPreflightResult(
            group_id=group_id, frozen_speed=speed,
            target_start_ms=target_start, target_end_ms=target_end,
            usable_start_ms=usable_start, usable_end_ms=usable_end,
            usable_duration_ms=usable_duration, text_units=units,
            estimated_speech_duration_ms=estimated_duration,
            estimate_evidence=evidence, status="warning",
            reason_codes=["limited_comparable_reference"],
            message="当前估计只来自一条可比冻结语音，建议生成后复核实际发声边界。",
        )
    return DubbingGroupPreflightResult(
        group_id=group_id, frozen_speed=speed,
        target_start_ms=target_start, target_end_ms=target_end,
        usable_start_ms=usable_start, usable_end_ms=usable_end,
        usable_duration_ms=usable_duration, text_units=units,
        estimated_speech_duration_ms=estimated_duration,
        estimate_evidence=evidence, status="ready", reason_codes=[],
        message="冻结参考估计的发声时长落在当前可用窗口内。",
    )
