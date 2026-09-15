"""Retain proven, unchanged completed work when replacing a generation plan."""

from __future__ import annotations

import hashlib
import json

from app.domains.video_localization import dubbing_production as domain
from app.domains.video_localization.dubbing_production_run import (
    build_production_run_snapshot,
    valid_manual_timing_deferrals,
)
from app.domains.video_localization.dubbing_timeline_edit_gate import (
    candidate_audible_timeline_bounds,
    candidate_clip_projection_fingerprint,
    candidate_source_speech_bounds,
    reconcile_gap_evidence_with_projection,
)
from app.schemas.video_localization_dubbing_production import (
    DubbingCandidateCqcInput,
    DubbingCandidateCqcReport,
    DubbingGenerationPlan,
    DubbingManualTimingDeferral,
    DubbingProductionState,
)


def retain_unchanged_manual_timing_deferrals(
    *,
    state: DubbingProductionState,
    new_plan: DubbingGenerationPlan,
    timeline_clips: list[dict],
    audio_sha256_by_clip_id: dict[str, str | None],
    current_draft,
) -> list[DubbingManualTimingDeferral]:
    """Rebind an unchanged parked take while preserving its original assertions."""

    old_plan = state.active_plan
    if old_plan is None:
        return []
    old_units = {unit.unit_id: unit for unit in old_plan.semantic_units}
    new_units = {unit.unit_id: unit for unit in new_plan.semantic_units}
    new_groups = {group.group_id: group for group in new_plan.groups}
    unchanged = {
        group.group_id: new_groups[group.group_id]
        for group in old_plan.groups
        if new_groups.get(group.group_id) == group
        and all(
            unit_id in old_units
            and unit_id in new_units
            and old_units[unit_id].model_dump(exclude={"display_text"})
            == new_units[unit_id].model_dump(exclude={"display_text"})
            for unit_id in group.unit_ids
        )
    }
    retained: list[DubbingManualTimingDeferral] = []
    for disposition in state.manual_timing_deferrals:
        group = unchanged.get(disposition.group_id)
        if group is None:
            continue
        context = domain.group_evidence_context_fingerprint(
            current_draft, new_plan, group
        )
        text_fingerprint = hashlib.sha256(
            json.dumps(
                domain.transcript_pronunciation_tokens(group.spoken_text),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if (
            context != disposition.source_context_fingerprint
            or text_fingerprint != disposition.candidate_spoken_text_fingerprint
        ):
            continue
        # Older text-edit paths left receipts at an earlier editorial revision.
        # Exact saved dependency/text proof permits rebinding, never a new
        # quality assertion. Validate the real full-audio projection below and
        # retain the original evidence revision and recovery decisions.
        retained.append(
            disposition.model_copy(
                update={
                    "source_revision": new_plan.source_revision,
                    "plan_revision": new_plan.plan_revision,
                }
            )
        )
    valid = valid_manual_timing_deferrals(
        active_plan=new_plan,
        dispositions=retained,
        timeline_clips=timeline_clips,
        playable_clip_ids={
            clip_id for clip_id, value in audio_sha256_by_clip_id.items()
            if value is not None
        },
        audio_sha256_by_clip_id=audio_sha256_by_clip_id,
    )
    return list(valid.values())


def retain_unchanged_completion_evidence(
    *,
    state: DubbingProductionState,
    new_plan: DubbingGenerationPlan,
    timeline_clips: list[dict],
    audio_sha256_by_clip_id: dict[str, str | None],
    current_draft=None,
) -> tuple[list[DubbingCandidateCqcInput], list[DubbingCandidateCqcReport]]:
    """Rebind existing proof, never infer acceptance from a playable clip alone.

    Cross-source reuse additionally requires a trusted local dependency hash.
    Legacy inputs without it remain conservatively invalidated. Audio bytes,
    complete retained projection and adjacent source context must stay equal;
    provenance records the original evidence revision, never a new listening pass.
    """
    old_plan = state.active_plan
    if old_plan is None:
        return [], []
    old_units = {unit.unit_id: unit for unit in old_plan.semantic_units}
    new_units = {unit.unit_id: unit for unit in new_plan.semantic_units}
    new_groups = {group.group_id: group for group in new_plan.groups}
    unchanged = {
        group.group_id: group
        for group in old_plan.groups
        if new_groups.get(group.group_id) == group
        and all(
            unit_id in old_units
            and unit_id in new_units
            and old_units[unit_id].model_dump(exclude={"display_text"})
            == new_units[unit_id].model_dump(exclude={"display_text"})
            for unit_id in group.unit_ids
        )
    }
    run = build_production_run_snapshot(
        current_source_revision=old_plan.source_revision,
        active_plan=old_plan,
        workflows=[],
        candidate_inputs=state.candidate_inputs,
        candidate_reports=state.candidate_reports,
        group_failures=state.group_failures,
        timeline_clips=timeline_clips,
    )
    inputs_by_id = {item.candidate_id: item for item in state.candidate_inputs}
    reports_by_id = {item.candidate_id: item for item in state.candidate_reports}
    retained_inputs = []
    retained_reports = []
    for progress in run.groups:
        group = unchanged.get(progress.group_id)
        if group is None:
            continue
        candidate_id = progress.passed_candidate_id
        formal_clips = None
        if progress.stage != "accepted":
            # A final staged review can be accepted while a stale gap-duration
            # field keeps the old run projection from recognizing it. Its
            # exact formal clips are still trustworthy input for rebind; the
            # projection reconciliation below supplies the missing duration.
            for candidate in reversed(state.candidate_reports):
                audit = candidate.semantic_boundary_audit
                if (
                    audit is None
                    or audit.status != "accepted"
                    or candidate.group_id != group.group_id
                ):
                    continue
                candidate_clips = [
                    clip
                    for clip in timeline_clips
                    if str(clip.get("candidate_id") or clip.get("result_id") or "")
                    == candidate.candidate_id
                    and set(clip.get("target_subtitle_ids") or [])
                    == set(group.subtitle_ids)
                ]
                if (
                    candidate_clips
                    and candidate_clip_projection_fingerprint(candidate_clips)
                    == audit.candidate_clip_projection_fingerprint
                ):
                    candidate_id = candidate.candidate_id
                    formal_clips = candidate_clips
                    break
            if candidate_id is None or formal_clips is None:
                continue
        frozen = inputs_by_id.get(candidate_id)
        report = reports_by_id.get(candidate_id)
        if frozen is None or report is None or report.audio_evidence is None:
            continue
        if (
            frozen.source_revision != old_plan.source_revision
            or frozen.plan_revision != old_plan.plan_revision
            or frozen.group_id != group.group_id
            or frozen.task_status != "success"
            or frozen.expected_spoken_text != group.spoken_text
            or frozen.target_start_ms != group.target_start_ms
            or frozen.target_end_ms != group.target_end_ms
            or not frozen.audio_sha256
        ):
            continue
        # Verify the historical reviewed combination before deriving a new
        # current-plan report. The raw waveform remains the same, but a
        # prior split may have made its recorded retained duration stale.
        historical_reviewed = frozen.model_copy(
            update={"audio": report.audio_evidence}
        )
        if report.evidence_fingerprint != domain.candidate_evidence_fingerprint(
            historical_reviewed
        ):
            continue
        clips = formal_clips
        if clips is None:
            clip_ids = set(progress.formal_clip_ids)
            clips = [
                clip for clip in timeline_clips if clip.get("clip_id") in clip_ids
            ]
        if not clips or any(
            audio_sha256_by_clip_id.get(str(clip.get("clip_id"))) != frozen.audio_sha256
            for clip in clips
        ):
            continue
        audio = report.audio_evidence.model_copy(update={
            "gap_evidence": reconcile_gap_evidence_with_projection(
                list(report.audio_evidence.gap_evidence), clips,
            )
        })
        source_onset_ms, protected_speech_end_ms = candidate_source_speech_bounds(audio)
        if source_onset_ms is None or protected_speech_end_ms is None:
            continue
        bounds = candidate_audible_timeline_bounds(
            clips,
            speech_start_ms=source_onset_ms,
            speech_end_ms=protected_speech_end_ms,
        )
        if bounds != (frozen.placement_start_ms, frozen.placement_end_ms):
            continue
        if old_plan.source_revision != new_plan.source_revision:
            if current_draft is None or not frozen.source_context_fingerprint:
                continue
            current_context = domain.group_evidence_context_fingerprint(current_draft, new_plan, group)
            if current_context != frozen.source_context_fingerprint:
                continue
        rebound = frozen.model_copy(update={
            "audio": audio,
            "source_revision": new_plan.source_revision,
            "plan_revision": new_plan.plan_revision,
            "evidence_origin_source_revision": frozen.evidence_origin_source_revision or frozen.source_revision,
            "evidence_origin_plan_revision": (
                frozen.evidence_origin_plan_revision
                if frozen.evidence_origin_plan_revision is not None else frozen.plan_revision
            ),
        })
        retained_inputs.append(rebound)
        evidence_fingerprint = domain.candidate_evidence_fingerprint(rebound)
        audit = report.semantic_boundary_audit
        retained_audit = None
        if (
            audit is not None
            and audit.status == "accepted"
            and audit.source_revision == old_plan.source_revision
            and audit.plan_revision == old_plan.plan_revision
            and audit.audio_sha256 == frozen.audio_sha256
            and audit.candidate_evidence_fingerprint == report.evidence_fingerprint
            and audit.candidate_clip_projection_fingerprint
            == candidate_clip_projection_fingerprint(clips)
        ):
            # Local source context, words, bytes and the complete audible
            # projection were all checked above. A new plan number alone
            # does not change an existing semantic decision or its provenance.
            retained_audit = audit.model_copy(update={
                "source_revision": new_plan.source_revision,
                "plan_revision": new_plan.plan_revision,
                "candidate_evidence_fingerprint": evidence_fingerprint,
                "audio_gap_evidence": list(audio.gap_evidence),
            })
        retained_reports.append(report.model_copy(update={
            "source_revision": new_plan.source_revision,
            "plan_revision": new_plan.plan_revision,
            "audio_evidence": audio,
            "evidence_fingerprint": evidence_fingerprint,
            "semantic_boundary_audit": retained_audit,
        }))
    return retained_inputs, retained_reports
