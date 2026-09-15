"""Deterministic recoverable projection for planned dubbing production."""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.domains.video_localization import dubbing_candidate_alignment
from app.schemas.video_localization_dubbing_production import (
    DubbingProductionGroupProgress,
    DubbingProductionRunSnapshot,
)
from app.domains.video_localization.dubbing_timeline_edit_gate import (
    candidate_clip_projection_fingerprint,
    first_primary_clip_overlap,
    has_verified_retained_content,
    rendered_gap_duration_ms,
)
_ACTIVE_WORKFLOW_STATUSES = {"prepared", "queued", "running"}
_ATTENTION_STAGES = {
    "needs_gap_processing",
    "needs_regeneration",
    "needs_timeline_work",
    "needs_timeline_edit",
    "needs_semantic_review",
}


def _clip_target_subtitle_ids(clip: dict[str, Any]) -> tuple[str, ...]:
    explicit = tuple(
        dict.fromkeys(
            str(value) for value in (clip.get("target_subtitle_ids") or []) if str(value)
        )
    )
    if explicit:
        return explicit
    subtitle_id = str(clip.get("subtitle_id") or "")
    return (subtitle_id,) if subtitle_id else ()


def valid_manual_timing_deferrals(
    *,
    active_plan: object | None,
    dispositions: list[object],
    timeline_clips: list[dict[str, Any]],
    playable_clip_ids: set[str] | None,
    audio_sha256_by_clip_id: dict[str, str | None] | None,
) -> dict[str, object]:
    """Return only dispositions still backed by their exact full-audio clip."""

    if (
        active_plan is None
        or playable_clip_ids is None
        or audio_sha256_by_clip_id is None
    ):
        return {}
    plan_revision = int(_value(active_plan, "plan_revision", 0))
    source_revision = str(_value(active_plan, "source_revision", ""))
    groups = {
        str(_value(group, "group_id", "")): group
        for group in (_value(active_plan, "groups", []) or [])
    }
    clips_by_id = {
        str(clip.get("clip_id") or ""): clip
        for clip in timeline_clips
        if str(clip.get("clip_id") or "")
    }
    valid: dict[str, object] = {}
    for disposition in dispositions:
        group_id = str(_value(disposition, "group_id", ""))
        group = groups.get(group_id)
        clip_id = str(_value(disposition, "parked_clip_id", ""))
        clip = clips_by_id.get(clip_id)
        if (
            group is None
            or clip is None
            or clip_id not in playable_clip_ids
            or audio_sha256_by_clip_id.get(clip_id)
            != str(_value(disposition, "audio_sha256", ""))
            or _value(disposition, "source_revision") != source_revision
            or int(_value(disposition, "plan_revision", 0)) != plan_revision
            or clip.get("track_id") != "dub"
            or int(clip.get("dub_lane") or 0) != 1
            or clip.get("status") != "ready"
            or not clip.get("audio_path")
            or str(clip.get("dubbing_group_id") or "") != group_id
            or str(clip.get("candidate_id") or "")
            != str(_value(disposition, "candidate_id", ""))
            or str(clip.get("result_id") or "")
            != str(_value(disposition, "result_id", ""))
            or set(_clip_target_subtitle_ids(clip))
            != {str(value) for value in (_value(group, "subtitle_ids", []) or [])}
            or set(str(value) for value in (clip.get("source_cue_ids") or []) if str(value))
            != set(str(value) for value in (_value(disposition, "source_cue_ids", []) or []) if str(value))
            or int(clip.get("source_start_ms") or 0) != 0
            or int(clip.get("source_end_ms") or 0)
            != int(_value(disposition, "candidate_duration_ms", 0))
            or int(clip.get("end_ms") or 0) - int(clip.get("start_ms") or 0)
            != int(_value(disposition, "candidate_duration_ms", 0))
            or candidate_clip_projection_fingerprint([clip])
            != str(_value(disposition, "clip_projection_fingerprint", ""))
        ):
            continue
        valid[group_id] = disposition
    return valid


def compact_terminal_run_timeline_clips(
    *,
    groups: list[object],
    timeline_clips: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep exactly the terminal run selection and move it onto the main lane."""

    selected: dict[str, tuple[str, str, set[str]]] = {}
    for group in groups:
        stage = str(_value(group, "stage", ""))
        if stage != "accepted":
            raise ValueError("final compaction requires every group to have a selected result")
        group_id = str(_value(group, "group_id", ""))
        candidate_id = str(_value(group, "passed_candidate_id", ""))
        target_ids = {
            str(value)
            for value in (_value(group, "target_subtitle_ids", []) or [])
            if str(value)
        }
        clip_ids = [
            str(value)
            for value in (_value(group, "formal_clip_ids", []) or [])
            if str(value)
        ]
        if not group_id or not candidate_id or not target_ids or not clip_ids:
            raise ValueError("final compaction selection is incomplete")
        for clip_id in clip_ids:
            if clip_id in selected:
                raise ValueError("one timeline clip cannot finalize two dubbing groups")
            selected[clip_id] = (group_id, candidate_id, target_ids)

    clips_by_id = {
        str(clip.get("clip_id") or ""): dict(clip)
        for clip in timeline_clips
        if str(clip.get("clip_id") or "")
    }
    missing = sorted(set(selected) - set(clips_by_id))
    if missing:
        raise ValueError("final compaction selection references missing timeline clips")

    coverage_by_group: dict[str, set[str]] = {}
    expected_by_group: dict[str, set[str]] = {}
    for clip_id, (group_id, candidate_id, target_ids) in selected.items():
        clip = clips_by_id[clip_id]
        identity = str(clip.get("candidate_id") or clip.get("result_id") or "")
        if clip.get("track_id") != "dub" or identity != candidate_id:
            raise ValueError("final compaction selection no longer matches its candidate")
        actual_targets = {
            str(value)
            for value in (
                clip.get("target_subtitle_ids")
                or ([clip.get("subtitle_id")] if clip.get("subtitle_id") else [])
            )
            if str(value)
        }
        coverage_by_group.setdefault(group_id, set()).update(actual_targets)
        expected_by_group[group_id] = target_ids
    if any(
        coverage_by_group.get(group_id, set()) != target_ids
        for group_id, target_ids in expected_by_group.items()
    ):
        raise ValueError("final compaction selection does not exactly cover its group")

    compacted: list[dict[str, Any]] = []
    for raw in timeline_clips:
        clip = dict(raw)
        if clip.get("track_id") != "dub":
            compacted.append(clip)
            continue
        clip_id = str(clip.get("clip_id") or "")
        selection = selected.get(clip_id)
        if selection is None:
            continue
        group_id, _candidate_id, _target_ids = selection
        clip["dubbing_group_id"] = group_id
        clip["dub_lane"] = 0
        clip["intentional_overlap"] = False
        compacted.append(clip)

    # Lane assignment is part of the durable clip projection fingerprint. A
    # final move to lane zero therefore rebinds the existing gas-edit evidence
    # to the new projection; the audio crop and timing are unchanged.
    selected_groups = {
        (group_id, candidate_id)
        for group_id, candidate_id, _target_ids in selected.values()
    }
    for group_id, candidate_id in selected_groups:
        candidate_clips = [
            clip
            for clip in compacted
            if clip.get("track_id") == "dub"
            and str(clip.get("dubbing_group_id") or "") == group_id
            and str(clip.get("candidate_id") or clip.get("result_id") or "")
            == candidate_id
        ]
        gates = [clip.get("timeline_edit_gate") for clip in candidate_clips]
        if not gates or any(not isinstance(gate, dict) for gate in gates):
            continue
        first_gate = dict(gates[0])
        if any(gate != gates[0] for gate in gates[1:]):
            raise ValueError("final compaction found inconsistent timeline gates")
        first_gate["candidate_clip_projection_fingerprint"] = (
            candidate_clip_projection_fingerprint(candidate_clips)
        )
        for clip in candidate_clips:
            clip["timeline_edit_gate"] = dict(first_gate)
    return compacted


def rebase_terminal_existing_acceptances(
    *,
    acceptances: list[object],
    timeline_clips: list[dict[str, Any]],
) -> list[object]:
    """Rebind unchanged legacy acceptance claims after final lane compaction."""

    rebased: list[object] = []
    for acceptance in acceptances:
        group_id = str(_value(acceptance, "group_id", ""))
        candidate_id = str(_value(acceptance, "candidate_id", ""))
        target_ids = {
            str(value)
            for value in (_value(acceptance, "target_subtitle_ids", []) or [])
            if str(value)
        }
        clips = [
            clip
            for clip in timeline_clips
            if clip.get("track_id") == "dub"
            and int(clip.get("dub_lane") or 0) == 0
            and str(clip.get("dubbing_group_id") or "")
            in {"", group_id}
            and str(clip.get("candidate_id") or clip.get("result_id") or "")
            == candidate_id
        ]
        coverage = {
            str(value)
            for clip in clips
            for value in (
                clip.get("target_subtitle_ids")
                or ([clip.get("subtitle_id")] if clip.get("subtitle_id") else [])
            )
            if str(value)
        }
        if clips and coverage == target_ids:
            update = {
                "candidate_clip_projection_fingerprint": (
                    candidate_clip_projection_fingerprint(clips)
                )
            }
            acceptance = (
                acceptance.model_copy(update=update)
                if hasattr(acceptance, "model_copy")
                else {**dict(acceptance), **update}
            )
        rebased.append(acceptance)
    return rebased


def keep_only_uniform_candidate_rebalance(
    *,
    original_timeline_clips: list[dict[str, Any]],
    proposed_timeline_clips: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep candidate-wide shifts and proven edge-silence trims.

    A candidate may move as one block. The scheduler may additionally remove
    only declared leading/trailing alignment padding while keeping the audible
    words at the same candidate-relative positions. Its gate can therefore be
    rebound by the common audible-speech shift and a fresh clip fingerprint.
    Any other crop, duration, or relative-slice change is restored for later
    candidate-level review.
    """

    original_by_id = {
        str(clip.get("clip_id") or ""): dict(clip)
        for clip in original_timeline_clips
        if str(clip.get("clip_id") or "")
    }
    proposed_by_key: dict[str, list[dict[str, Any]]] = {}
    for clip in proposed_timeline_clips:
        if clip.get("track_id") != "dub":
            continue
        key = str(
            clip.get("candidate_id") or clip.get("result_id") or ""
        )
        if key:
            proposed_by_key.setdefault(key, []).append(clip)

    accepted_shifts: dict[str, int] = {}
    for key, proposed in proposed_by_key.items():
        original = [original_by_id.get(str(clip.get("clip_id") or "")) for clip in proposed]
        if any(clip is None for clip in original):
            continue
        audible_shifts: set[int] = set()
        padding_only = True
        for before, after in zip(original, proposed):
            lead_trim_ms = int(after.get("source_start_ms") or 0) - int(
                before.get("source_start_ms") or 0
            )
            trail_trim_ms = int(before.get("source_end_ms") or 0) - int(
                after.get("source_end_ms") or 0
            )
            if (
                lead_trim_ms < 0
                or trail_trim_ms < 0
                or int(after.get("alignment_lead_ms") or 0)
                != int(before.get("alignment_lead_ms") or 0) - lead_trim_ms
                or int(after.get("alignment_trail_ms") or 0)
                != int(before.get("alignment_trail_ms") or 0) - trail_trim_ms
            ):
                padding_only = False
                break
            start_shift_ms = (
                int(after.get("start_ms") or 0)
                - int(before.get("start_ms") or 0)
                - lead_trim_ms
            )
            end_shift_ms = (
                int(after.get("end_ms") or 0)
                - int(before.get("end_ms") or 0)
                + trail_trim_ms
            )
            if start_shift_ms != end_shift_ms:
                padding_only = False
                break
            audible_shifts.add(start_shift_ms)
        if padding_only and len(audible_shifts) == 1:
            accepted_shifts[key] = next(iter(audible_shifts))

    kept: list[dict[str, Any]] = []
    for raw in proposed_timeline_clips:
        clip = dict(raw)
        if clip.get("track_id") != "dub":
            kept.append(clip)
            continue
        key = str(
            clip.get("candidate_id") or clip.get("result_id") or ""
        )
        if key not in accepted_shifts:
            clip = dict(original_by_id.get(str(clip.get("clip_id") or ""), clip))
        kept.append(clip)

    for key, shift_ms in accepted_shifts.items():
        clips = [
            clip
            for clip in kept
            if clip.get("track_id") == "dub"
            and str(
                clip.get("candidate_id") or clip.get("result_id") or ""
            )
            == key
        ]
        gates = [clip.get("timeline_edit_gate") for clip in clips]
        if not gates or any(not isinstance(gate, dict) for gate in gates):
            continue
        if any(gate != gates[0] for gate in gates[1:]):
            continue
        gate = dict(gates[0])
        gate["candidate_clip_projection_fingerprint"] = (
            candidate_clip_projection_fingerprint(clips)
        )
        for field in (
            "actual_speech_start_delta_ms",
            "actual_speech_end_delta_ms",
        ):
            if gate.get(field) is not None:
                gate[field] = int(gate[field]) + shift_ms
        for clip in clips:
            clip["timeline_edit_gate"] = dict(gate)
    return kept


def trim_safe_alignment_padding_overlaps(
    timeline_clips: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove only inter-group overlap proven to be retained edge padding.

    The generated audio file is immutable.  This helper changes the visible
    source crop and timeline span by no more than the declared leading or
    trailing alignment padding, so it cannot consume an aligned spoken word.
    Overlap that reaches beyond those edge buffers is deliberately untouched
    for listening, shifting, or regeneration.
    """

    clips = [dict(item) for item in timeline_clips]
    dub_indexes = [
        index
        for index, clip in enumerate(clips)
        if clip.get("track_id") == "dub"
    ]
    ordered = sorted(
        dub_indexes,
        key=lambda index: (
            int(clips[index].get("start_ms") or 0),
            int(clips[index].get("end_ms") or 0),
            str(clips[index].get("clip_id") or ""),
        ),
    )
    for left_position, left_index in enumerate(ordered):
        left = clips[left_index]
        for right_index in ordered[left_position + 1 :]:
            right = clips[right_index]
            left_end_ms = int(left.get("end_ms") or 0)
            right_start_ms = int(right.get("start_ms") or 0)
            if right_start_ms >= left_end_ms:
                break
            left_identity = str(
                left.get("candidate_id") or left.get("result_id") or ""
            )
            right_identity = str(
                right.get("candidate_id") or right.get("result_id") or ""
            )
            if (
                not left_identity
                or not right_identity
                or left_identity == right_identity
                or left.get("intentional_overlap") is True
                or right.get("intentional_overlap") is True
            ):
                continue
            overlap_ms = left_end_ms - right_start_ms
            left_trail_ms = max(
                0, int(left.get("alignment_trail_ms") or 0)
            )
            right_lead_ms = max(
                0, int(right.get("alignment_lead_ms") or 0)
            )
            if overlap_ms > left_trail_ms + right_lead_ms:
                continue

            trim_left_ms = min(overlap_ms, left_trail_ms)
            if trim_left_ms:
                left["end_ms"] = left_end_ms - trim_left_ms
                left["source_end_ms"] = (
                    int(left.get("source_end_ms") or 0) - trim_left_ms
                )
                left["alignment_trail_ms"] = (
                    left_trail_ms - trim_left_ms
                )
                left.pop("timeline_edit_gate", None)
            remaining_ms = overlap_ms - trim_left_ms
            if remaining_ms:
                right["start_ms"] = right_start_ms + remaining_ms
                right["source_start_ms"] = (
                    int(right.get("source_start_ms") or 0) + remaining_ms
                )
                right["alignment_lead_ms"] = right_lead_ms - remaining_ms
                right.pop("timeline_edit_gate", None)
    return clips


def _value(item: object, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _processed_candidate_projection_is_current(
    *,
    report: object | None,
    candidate_clips: list[dict[str, Any]],
    frozen: object | None = None,
) -> bool:
    """Revalidate the current projection instead of trusting an old pass flag."""

    if report is None or _value(report, "overall_status") == "failed":
        return False
    audit = _value(report, "semantic_boundary_audit")
    # Existing completed takes have no reconstructable boundary-review input.
    # Keep them readable as legacy evidence; every newly staged candidate
    # carries this audit and cannot pass until its Agent disposition binds the
    # current final projection.
    if audit is not None and (
        _value(audit, "status") != "accepted"
        or _value(audit, "candidate_clip_projection_fingerprint")
        != candidate_clip_projection_fingerprint(candidate_clips)
    ):
        return False
    audio = _value(report, "audio_evidence")
    aligned_words = list(_value(audio, "aligned_words", []) or [])
    gap_evidence = list(_value(audio, "gap_evidence", []) or [])
    if audio is None or not aligned_words or not candidate_clips:
        return False
    try:
        dubbing_candidate_alignment.validate_candidate_clip_coverage(
            clips=candidate_clips,
            words=aligned_words,
            speech_start_ms=None if has_verified_retained_content(frozen, candidate_clips) else _value(audio, "speech_start_ms"),
            speech_end_ms=None if has_verified_retained_content(frozen, candidate_clips) else _value(audio, "speech_end_ms"),
        )
    except (TypeError, ValueError):
        return False
    for gap in gap_evidence:
        serialized = (
            gap.model_dump(mode="json")
            if hasattr(gap, "model_dump")
            else dict(gap)
        )
        if int(serialized.get("retained_duration_ms") or 0) != (
            rendered_gap_duration_ms(serialized, candidate_clips)
        ):
            return False
    return True


def _generation_parameters(workflow: object) -> dict[str, Any]:
    for stage in _value(workflow, "stages", []) or []:
        if _value(stage, "kind") == "generation":
            parameters = _value(stage, "parameters", {})
            return parameters if isinstance(parameters, dict) else {}
    return {}


def _workflow_candidate_identities(workflow: object) -> set[str]:
    """Return the task/result aliases used by durable candidate evidence."""

    task_id = str(_value(workflow, "generation_task_id", "") or "")
    workflow_id = str(_value(workflow, "workflow_id", "") or "")
    result_id = str(_value(workflow, "result_id", "") or "")
    return {
        value
        for value in {
            task_id,
            workflow_id,
            result_id,
            f"candidate_{task_id}" if task_id else "",
            f"candidate_{workflow_id}" if workflow_id else "",
        }
        if value
    }


def _last_workflow_error(workflows: list[object]) -> str | None:
    for workflow in reversed(workflows):
        for stage in reversed(_value(workflow, "stages", []) or []):
            error = _value(stage, "error_message")
            if error:
                return str(error)
    return None


def build_production_run_snapshot(
    *,
    current_source_revision: str,
    active_plan: object | None,
    workflows: list[object],
    candidate_inputs: list[object],
    candidate_reports: list[object],
    group_failures: list[object],
    timeline_clips: list[dict[str, Any]],
    manual_timing_deferrals: list[object] | None = None,
    existing_formal_acceptances: list[object] | None = None,
    playable_clip_ids: set[str] | None = None,
    audio_sha256_by_clip_id: dict[str, str | None] | None = None,
    recoverable_candidate_ids_by_group: dict[str, list[str]] | None = None,
) -> DubbingProductionRunSnapshot:
    """Project durable task/gap-edit/timeline facts into the next safe action."""

    if active_plan is None:
        return DubbingProductionRunSnapshot(
            source_revision=current_source_revision,
            status="needs_plan",
            next_action="create_plan",
        )

    plan_revision = int(_value(active_plan, "plan_revision", 0))
    plan_source_revision = str(_value(active_plan, "source_revision", current_source_revision))
    groups = list(_value(active_plan, "groups", []) or [])
    current_workflows: dict[str, list[object]] = {}
    for workflow in workflows:
        parameters = _generation_parameters(workflow)
        if int(parameters.get("video_localization_dubbing_plan_revision") or 0) != plan_revision:
            continue
        group_id = str(parameters.get("video_localization_dubbing_group_id") or "")
        if group_id:
            current_workflows.setdefault(group_id, []).append(workflow)

    current_inputs: dict[str, list[object]] = {}
    for item in candidate_inputs:
        if (
            _value(item, "source_revision") == plan_source_revision
            and int(_value(item, "plan_revision", 0)) == plan_revision
        ):
            current_inputs.setdefault(str(_value(item, "group_id", "")), []).append(item)

    current_reports: dict[str, list[object]] = {}
    for report in candidate_reports:
        if (
            _value(report, "source_revision") == plan_source_revision
            and int(_value(report, "plan_revision", 0)) == plan_revision
        ):
            current_reports.setdefault(str(_value(report, "group_id", "")), []).append(report)

    current_failures = {
        str(_value(item, "group_id", "")): item
        for item in group_failures
        if (
            _value(item, "source_revision") == plan_source_revision
            and int(_value(item, "plan_revision", 0)) == plan_revision
        )
    }
    current_deferrals = valid_manual_timing_deferrals(
        active_plan=active_plan,
        dispositions=list(manual_timing_deferrals or []),
        timeline_clips=timeline_clips,
        playable_clip_ids=playable_clip_ids,
        audio_sha256_by_clip_id=audio_sha256_by_clip_id,
    )
    current_deferral_claims = {
        str(_value(item, "group_id", "")): item
        for item in (manual_timing_deferrals or [])
        if _value(item, "source_revision") == plan_source_revision
        and int(_value(item, "plan_revision", 0)) == plan_revision
    }
    current_existing_acceptances = {
        str(_value(item, "group_id", "")): item
        for item in (existing_formal_acceptances or [])
        if _value(item, "source_revision") == plan_source_revision
        and int(_value(item, "plan_revision", 0)) == plan_revision
    }

    main_lane_clips_by_identity: dict[str, list[dict[str, Any]]] = {}
    for clip in timeline_clips:
        if clip.get("track_id") != "dub" or int(clip.get("dub_lane") or 0) != 0:
            continue
        identity = str(clip.get("candidate_id") or clip.get("result_id") or "")
        if identity:
            main_lane_clips_by_identity.setdefault(identity, []).append(clip)

    progress: list[DubbingProductionGroupProgress] = []
    for group_index, group in enumerate(groups):
        group_id = str(_value(group, "group_id", ""))
        target_ids = [str(item) for item in _value(group, "subtitle_ids", [])]
        group_workflows = current_workflows.get(group_id, [])
        group_inputs = current_inputs.get(group_id, [])
        group_reports = current_reports.get(group_id, [])
        group_failure = current_failures.get(group_id)
        group_deferral = current_deferrals.get(group_id)
        invalid_group_deferral = (
            group_id in current_deferral_claims and group_deferral is None
        )
        reported_ids = [
            str(_value(item, "candidate_id", "")) for item in group_reports
        ]

        accepted_candidate_id = None
        accepted_clips: list[dict[str, Any]] = []
        accepted_rate_ratio: float | None = None
        accepted_speed_exception = False

        for candidate_id in reversed(reported_ids):
            candidate_clips = [
                clip
                for clip in timeline_clips
                if clip.get("track_id") == "dub"
                and int(clip.get("dub_lane") or 0) == 0
                and str(
                    clip.get("candidate_id") or clip.get("result_id") or ""
                ) == candidate_id
                and (
                    str(clip.get("dubbing_group_id") or "") == group_id
                    or (
                        not clip.get("dubbing_group_id")
                        and set(
                            str(subtitle_id)
                            for subtitle_id in (
                                clip.get("target_subtitle_ids")
                                or (
                                    [clip.get("subtitle_id")]
                                    if clip.get("subtitle_id")
                                    else []
                                )
                            )
                        )
                        == set(target_ids)
                    )
                )
            ]
            coverage = Counter(
                str(subtitle_id)
                for clip in candidate_clips
                for subtitle_id in (
                    clip.get("target_subtitle_ids")
                    or ([clip.get("subtitle_id")] if clip.get("subtitle_id") else [])
                )
            )
            if set(coverage) != set(target_ids):
                continue
            report = next(
                (
                    item
                    for item in group_reports
                    if str(_value(item, "candidate_id", "")) == candidate_id
                ),
                None,
            )
            audio = _value(report, "audio_evidence") if report is not None else None
            speaking_rate_ratio = _value(audio, "speaking_rate_ratio")
            speed_exception_applied = bool(
                _value(audio, "content_speed_exception_reason")
                and _value(audio, "content_speed_exception_evidence_ids", [])
            )
            processed_take_is_current = _processed_candidate_projection_is_current(
                report=report,
                candidate_clips=candidate_clips,
                frozen=next((item for item in group_inputs if _value(item, "candidate_id") == candidate_id), None),
            )
            if candidate_clips and processed_take_is_current:
                accepted_candidate_id = candidate_id
                accepted_clips = candidate_clips
                if speaking_rate_ratio is not None:
                    accepted_rate_ratio = float(speaking_rate_ratio)
                accepted_speed_exception = speed_exception_applied
                break

        # ``reconcile_existing_formal_groups`` is the public migration path
        # for an unchanged, already-gated take after a plan rebind.  Join its
        # current-plan claim here so a read does not re-open the same formal
        # clips as gap work.  A current workflow still wins below.
        formal_acceptance = current_existing_acceptances.get(group_id)
        if (
            accepted_candidate_id is None
            and formal_acceptance is not None
            and set(_value(formal_acceptance, "target_subtitle_ids", []) or [])
            == set(target_ids)
        ):
            candidate_id = str(_value(formal_acceptance, "candidate_id", ""))
            candidate_clips = [
                clip
                for clip in main_lane_clips_by_identity.get(candidate_id, [])
                if str(clip.get("dubbing_group_id") or "") == group_id
                and set(
                    clip.get("target_subtitle_ids")
                    or ([clip.get("subtitle_id")] if clip.get("subtitle_id") else [])
                )
                == set(target_ids)
            ]
            if (
                candidate_clips
                and candidate_clip_projection_fingerprint(candidate_clips)
                == _value(
                    formal_acceptance,
                    "candidate_clip_projection_fingerprint",
                )
            ):
                accepted_candidate_id = candidate_id
                accepted_clips = candidate_clips

        exact_existing: list[tuple[str, list[dict[str, Any]]]] = []
        for identity, identity_clips in main_lane_clips_by_identity.items():
            coverage = {
                str(subtitle_id)
                for clip in identity_clips
                for subtitle_id in (
                    clip.get("target_subtitle_ids")
                    or ([clip.get("subtitle_id")] if clip.get("subtitle_id") else [])
                )
            }
            if coverage == set(target_ids):
                exact_existing.append((identity, identity_clips))

        # Timeline membership survives manual splitting, regrouping and loss of
        # old candidate reports. Partial coverage is not an empty generation slot.
        existing_clips = [
            clip for clip in timeline_clips
            if clip.get("track_id") == "dub"
            and int(clip.get("dub_lane") or 0) == 0
            and set(target_ids).intersection(
                clip.get("target_subtitle_ids")
                or ([clip.get("subtitle_id")] if clip.get("subtitle_id") else [])
            )
        ]
        existing_targets = {
            str(value) for clip in existing_clips
            for value in (clip.get("target_subtitle_ids")
                          or ([clip.get("subtitle_id")] if clip.get("subtitle_id") else []))
        }
        requires_reconciliation = bool(existing_clips) and (
            len(exact_existing) != 1
            or {str(clip.get("clip_id") or "") for clip in existing_clips}
            != {str(clip.get("clip_id") or "") for clip in exact_existing[0][1]}
        )

        active = [item for item in group_workflows if _value(item, "status") in _ACTIVE_WORKFLOW_STATUSES]
        completed_with_result = [
            item
            for item in group_workflows
            if _value(item, "status") == "success" and _value(item, "result_id")
        ]
        failed_workflows = [item for item in group_workflows if _value(item, "status") in {"failed", "cancelled"}]
        reviewed_candidate_ids = {str(_value(item, "candidate_id", "")) for item in group_reports}
        unreviewed_inputs = [
            item
            for item in group_inputs
            if str(_value(item, "candidate_id", "")) not in reviewed_candidate_ids
        ]
        # A current formal take is selected from exact clip coverage and its
        # own reconstructable report.  Historical reports are audit history:
        # an earlier rejected or recovery-required candidate must not reopen a
        # group after a later candidate has been atomically adopted.  The one
        # exception is a newer, non-cancelled workflow: it represents an
        # explicit replacement attempt and remains the group owner's current
        # work until it reaches a terminal result.
        latest_live_workflow = next(
            (
                item
                for item in reversed(group_workflows)
                if _value(item, "status") != "cancelled"
            ),
            None,
        )
        latest_live_candidate_ids = (
            _workflow_candidate_identities(latest_live_workflow)
            if latest_live_workflow is not None
            else set()
        )
        latest_live_report = next(
            (
                item
                for item in reversed(group_reports)
                if str(_value(item, "candidate_id", ""))
                in latest_live_candidate_ids
            ),
            None,
        )
        latest_live_audit_status = str(
            _value(
                _value(latest_live_report, "semantic_boundary_audit"),
                "status",
                "",
            )
            or ""
        )
        latest_live_is_active = (
            latest_live_workflow is not None
            and _value(latest_live_workflow, "status") in _ACTIVE_WORKFLOW_STATUSES
        )
        latest_live_failed = (
            latest_live_workflow is not None
            and _value(latest_live_workflow, "status") == "failed"
        )
        # Ordinary handoffs can supersede a managed failure after their exact
        # result has passed the current evidence/clip checks above. They carry
        # no plan fields, so compare their durable workflow order explicitly.
        accepted_workflow_index = next((index for index in range(len(workflows) - 1, -1, -1)
            if accepted_candidate_id is not None
            and _value(workflows[index], "status") == "success"
            and accepted_candidate_id in _workflow_candidate_identities(workflows[index])), -1)
        failed_workflow_index = next((index for index in range(len(workflows) - 1, -1, -1)
            if workflows[index] is latest_live_workflow), -1)
        failure_superseded = latest_live_failed and accepted_workflow_index > failed_workflow_index

        pending_reports = ([latest_live_report] if latest_live_audit_status == "pending_agent" else [
            report for report in group_reports
            if _value(_value(report, "semantic_boundary_audit"), "status") == "pending_agent"
        ] if latest_live_workflow is None and accepted_candidate_id is None else [])
        other_primary_clips = [clip for clip in timeline_clips
            if not set(_clip_target_subtitle_ids(clip)).intersection(group.subtitle_ids)]
        pending_placement_conflict = any(
            first_primary_clip_overlap(
                [clip if isinstance(clip, dict) else clip.model_dump(mode="json")
                 for clip in _value(_value(report, "staged_candidate_projection"), "clips", []) or []],
                other_primary_clips, staged=True,
            ) is not None for report in pending_reports
        )

        if group_deferral is not None:
            stage, action = "deferred_manual_timing", "complete"
        elif invalid_group_deferral:
            # A move, crop, broken file, or explicit deletion invalidates the
            # terminal proof but remains user-owned timeline intent. Reopen
            # manual reconciliation without authorizing another TTS request.
            stage, action = "needs_timeline_edit", "edit_timeline"
        elif requires_reconciliation and not active:
            stage, action = "needs_timeline_edit", "edit_timeline"
        elif pending_placement_conflict:
            stage, action = "needs_gap_processing", "process_gaps"
        elif latest_live_audit_status == "pending_agent":
            stage, action = "needs_semantic_review", "review_semantic_boundaries"
        elif latest_live_audit_status == "recovery_required":
            stage, action = "needs_regeneration", "regenerate_candidate"
        elif (
            latest_live_is_active
            and _value(latest_live_workflow, "result_id")
            and any(
                _value(item, "kind") == "generation"
                and _value(item, "status") == "success"
                for item in _value(latest_live_workflow, "stages", []) or []
            )
        ):
            # A saved voice may still need its first audit or local placement
            # replay. Waiting for generation here would strand that result.
            stage, action = "needs_gap_processing", "process_gaps"
        # Generation is complete before the placement stage closes. The shared
        # finisher deliberately keeps placement active while its exact current
        # candidate waits for the Agent's semantic-boundary disposition, so that
        # candidate-bound audit must take precedence over generic workflow activity.
        elif latest_live_is_active:
            stage, action = "generating", "wait_for_generation"
        elif latest_live_failed and not failure_superseded:
            # A failed placement does not invalidate a successful generation.
            # Keep the failure visible, but route its saved audio to close-out.
            generation_saved = bool(_value(latest_live_workflow, "result_id")) and any(
                _value(item, "kind") == "generation" and _value(item, "status") == "success"
                for item in _value(latest_live_workflow, "stages", []) or []
            )
            stage, action = "failed", "process_gaps" if generation_saved else "regenerate_candidate"
        elif accepted_candidate_id is not None:
            stage, action = "accepted", "complete"
        # A historical group failure remains actionable only while there is no
        # later formally adopted candidate. Once one exists, keep the failure
        # as audit history and do not reopen the group.
        elif group_failure is not None:
            stage, action = "failed", "process_gaps"
        elif any(
            _value(_value(item, "semantic_boundary_audit"), "status")
            == "pending_agent"
            for item in group_reports
        ):
            stage, action = "needs_semantic_review", "review_semantic_boundaries"
        elif any(
            _value(_value(item, "semantic_boundary_audit"), "status")
            == "recovery_required"
            for item in group_reports
        ):
            stage, action = "needs_regeneration", "regenerate_candidate"
        elif group_reports:
            stage, action = "needs_timeline_work", "place_candidate"
        elif unreviewed_inputs:
            stage, action = "needs_gap_processing", "process_gaps"
        elif active:
            stage, action = "generating", "wait_for_generation"
        # Generation success is persisted before the completion callback
        # freezes/reviews/places the result.  Treat that short close-out window
        # as recoverable candidate work, never as permission to submit another
        # generation for the same group.
        elif completed_with_result:
            stage, action = "needs_gap_processing", "process_gaps"
        elif exact_existing:
            # An exact formal result is user-owned timeline work.  Until it is
            # reconciled or reviewed, expose it as recoverable evidence and
            # never authorize a duplicate generation.
            stage, action = "needs_gap_processing", "process_gaps"
        elif (recoverable_candidate_ids_by_group or {}).get(group_id):
            # Immutable generation history survives an editorial replan. Its
            # audio identity is not a current quality or placement approval.
            stage, action = "needs_gap_processing", "process_gaps"
        elif failed_workflows:
            stage, action = "failed", "regenerate_candidate"
        else:
            stage, action = "ready_to_generate", "generate_candidate"

        # Recovery order is newest durable workflow result first. A retry can
        # complete while the previous candidate report is still visible; a
        # set/sort here used to erase recency and revive the stale take.
        candidate_ids = list(
            dict.fromkeys(
                str(value)
                for value in [
                    *(
                        _value(item, "result_id")
                        for item in reversed(group_workflows)
                    ),
                    *(
                        _value(item, "candidate_id", "")
                        for item in reversed(group_reports)
                    ),
                    *(
                        _value(item, "candidate_id", "")
                        for item in reversed(group_inputs)
                    ),
                    *(identity for identity, _clips in reversed(exact_existing)),
                    *(recoverable_candidate_ids_by_group or {}).get(group_id, []),
                    *(
                        [str(_value(group_deferral, "candidate_id", ""))]
                        if group_deferral is not None
                        else []
                    ),
                ]
                if value
            )
        )
        progress.append(
            DubbingProductionGroupProgress(
                group_id=group_id,
                group_index=group_index,
                target_subtitle_ids=target_ids,
                stage=stage,
                recommended_action=action,
                # Verified equivalent old-plan outputs retain their receipt
                # identity for recovery, without becoming current active jobs.
                workflow_ids=list(dict.fromkeys([
                    str(_value(item, "workflow_id", "")) for item in group_workflows
                ] + [
                    str(_value(item, "workflow_id", ""))
                    for item in workflows
                    if _generation_parameters(item).get("video_localization_dubbing_group_id") == group_id
                    and _workflow_candidate_identities(item).intersection(
                        (recoverable_candidate_ids_by_group or {}).get(group_id, [])
                    )
                ])),
                candidate_ids=candidate_ids,
                passed_candidate_id=(
                    accepted_candidate_id if stage == "accepted" else None
                ),
                formal_clip_ids=[
                    str(clip.get("clip_id") or "")
                    for clip in (
                        accepted_clips
                        or (
                            [
                                clip
                                for _identity, clips in exact_existing
                                for clip in clips
                            ]
                            if group_failure is not None
                            else []
                        )
                    )
                ],
                deferred_clip_ids=(
                    [str(_value(group_deferral, "parked_clip_id", ""))]
                    if group_deferral is not None
                    else []
                ),
                existing_timeline_clip_ids=[str(clip.get("clip_id") or "") for clip in existing_clips],
                uncovered_target_subtitle_ids=[item for item in target_ids if item not in existing_targets],
                timeline_requires_reconciliation=requires_reconciliation,
                speaking_rate_ratio=accepted_rate_ratio,
                content_speed_exception_applied=accepted_speed_exception,
                # Only work that reached the generation queue consumes a model
                # attempt. A prepared workflow rejected during handoff is an
                # infrastructure failure, not an OmniVoice retry.
                attempt_count=sum(
                    1
                    for item in group_workflows
                    if _value(item, "generation_task_id")
                ),
                last_error=(
                    str(_value(group_failure, "note", ""))
                    if group_failure is not None
                    else (
                        None
                        if accepted_candidate_id is not None
                        else _last_workflow_error(group_workflows)
                    )
                ),
            )
        )

    accepted_count = sum(item.stage == "accepted" for item in progress)
    deferred_count = sum(item.stage == "deferred_manual_timing" for item in progress)
    failed_count = sum(item.stage == "failed" for item in progress)
    active_count = sum(item.stage == "generating" for item in progress)
    attention_count = sum(item.stage in _ATTENTION_STAGES for item in progress)
    all_terminal = bool(progress) and (
        accepted_count + deferred_count + failed_count == len(progress)
    )
    completed = all_terminal

    if not progress:
        status, next_action, next_group_id = "needs_attention", "create_plan", None
    elif completed:
        status = (
            "completed_with_failures"
            if failed_count
            else "completed_with_deferred"
            if deferred_count
            else "completed"
        )
        next_action, next_group_id = "complete", None
    else:
        next_group = next(
            item
            for item in progress
            if item.stage not in {"accepted", "deferred_manual_timing", "failed"}
        )
        status = "needs_attention" if attention_count else "running"
        next_action = next_group.recommended_action
        next_group_id = next_group.group_id

    return DubbingProductionRunSnapshot(
        source_revision=plan_source_revision,
        plan_revision=plan_revision,
        status=status,
        group_count=len(progress),
        accepted_group_count=accepted_count,
        deferred_group_count=deferred_count,
        failed_group_count=failed_count,
        active_group_count=active_count,
        attention_group_count=attention_count,
        next_group_id=next_group_id,
        next_action=next_action,
        groups=progress,
    )
