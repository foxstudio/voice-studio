"""One read-only physical completion projection for Agent continuation and delivery."""
from __future__ import annotations

from app.domains.video_localization import dubbing_production as domain
from app.schemas.video_localization_dubbing_production import DubbingCompletionSnapshot


def read_current_completion(project_id: str, draft, *, start_ms: int = 0,
                            end_ms: int | None = None) -> DubbingCompletionSnapshot:
    """Collect the same physical evidence for readiness and production callers."""
    from app.domains.video_localization import dubbing_media

    return build_completion_snapshot(
        draft, start_ms=start_ms, end_ms=end_ms,
        audio_duration_ms_by_clip_id=dubbing_media.current_timeline_audio_durations(project_id, draft),
        audio_sha256_by_clip_id=dubbing_media.current_timeline_audio_sha256s(project_id, draft),
    )


def build_completion_snapshot(draft, *, audio_duration_ms_by_clip_id: dict[str, int | None],
                              audio_sha256_by_clip_id: dict[str, str | None] | None = None,
                              start_ms: int = 0, end_ms: int | None = None) -> DubbingCompletionSnapshot:
    duration = int(draft.source_media.duration_ms or 0)
    end_ms = duration if end_ms is None else end_ms
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("Completion range must have positive duration")
    snapshot = domain.build_project_snapshot(draft)
    plan = draft.dubbing_production.active_plan
    disposition_clip_ids = {
        str(item.parked_clip_id)
        for item in draft.dubbing_production.manual_timing_deferrals
        if plan
        and item.source_revision == plan.source_revision
        and item.plan_revision == plan.plan_revision
    }
    from app.domains.video_localization.dubbing_production_run import (
        build_production_run_snapshot,
        valid_manual_timing_deferrals,
    )
    playable_clip_ids = {
        clip_id for clip_id, media_duration in audio_duration_ms_by_clip_id.items()
        if media_duration is not None
    }
    valid_deferrals = valid_manual_timing_deferrals(
        active_plan=plan,
        dispositions=list(draft.dubbing_production.manual_timing_deferrals),
        timeline_clips=[dict(item) for item in draft.timeline_clips],
        playable_clip_ids=playable_clip_ids,
        audio_sha256_by_clip_id=audio_sha256_by_clip_id,
    )
    deferred_clip_ids = {
        str(item.parked_clip_id) for item in valid_deferrals.values()
    }
    deferred_target_ids = {
        target for group in (plan.groups if plan else [])
        if group.group_id in valid_deferrals
        for target in group.subtitle_ids
    }
    dispositions = {target: unit.speech_policy for unit in (plan.semantic_units if plan and plan.source_revision == snapshot.source_revision else [])
                    for target in unit.subtitle_ids}
    expected = [item.subtitle_id for item in draft.localized_subtitles
                if item.start_ms < end_ms and item.end_ms > start_ms
                and dispositions.get(item.subtitle_id) != "omit_non_speech"]
    expected_set = set(expected)
    clips, invalid, covered = [], [], set()
    for raw in draft.timeline_clips:
        if raw.get('track_id') != 'dub':
            continue
        targets = set(raw.get('target_subtitle_ids') or ([raw['subtitle_id']] if raw.get('subtitle_id') else []))
        start, end = int(raw.get('start_ms') or 0), int(raw.get('end_ms') or 0)
        if not (targets & expected_set or start < end_ms and end > start_ms):
            continue
        clip_id = str(raw.get('clip_id') or '')
        source_start, source_end = int(raw.get('source_start_ms') or 0), int(raw.get('source_end_ms') or 0)
        media_duration = audio_duration_ms_by_clip_id.get(clip_id)
        if (raw.get('status', 'ready') != 'ready' or media_duration is None
            or start < 0 or end <= start or source_start < 0 or source_end <= source_start
            or source_end > media_duration or end - start > source_end - source_start
            or (duration > 0 and end > duration)):
            invalid.append(clip_id)
            continue
        clips.append(raw)
        if clip_id not in disposition_clip_ids:
            covered.update(targets & expected_set)
    overlaps = []
    ordered = sorted(clips, key=lambda c: (int(c.get('dub_lane') or 0), int(c.get('start_ms') or 0)))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if int(right.get('dub_lane') or 0) != int(left.get('dub_lane') or 0):
                break
            if int(right['start_ms']) >= int(left['end_ms']):
                break
            overlaps.append((str(left['clip_id']), str(right['clip_id'])))
    repeated = []
    for index, left in enumerate(clips):
        for right in clips[index + 1:]:
            left_targets = set(left.get('target_subtitle_ids') or [left.get('subtitle_id')])
            right_targets = set(right.get('target_subtitle_ids') or [right.get('subtitle_id')])
            identity = left.get('audio_path') or left.get('result_id') or left.get('candidate_id')
            if (identity and identity == (right.get('audio_path') or right.get('result_id') or right.get('candidate_id'))
                and left_targets.intersection(right_targets) - {None}
                and int(left['source_start_ms']) < int(right['source_end_ms'])
                and int(right['source_start_ms']) < int(left['source_end_ms'])):
                repeated.append((str(left['clip_id']), str(right['clip_id'])))
    missing = expected_set - covered
    pending, unplaced = [], []
    discarded = set(draft.ui_state.get('discarded_tts_task_ids') or [])
    for task in draft.tts_tasks:
        if discarded.intersection({task.workflow_id, task.generation_task_id, task.result_id}):
            continue
        generation = next((step for step in task.stages if step.kind == 'generation'), None)
        parameters = generation.parameters if generation else {}
        targets = set(parameters.get('video_localization_target_subtitle_ids') or [])
        if not targets.intersection(expected_set):
            continue
        if task.status in {'prepared', 'queued', 'running', 'generated'}:
            pending.append(task.workflow_id)
        elif (task.status != 'cancelled' and generation is not None
              and generation.status == 'success' and task.result_id
              # A verified selected take resolves the target, including older
              # failed takes of that same target, not only its own result ID.
              and targets.intersection(missing) - deferred_target_ids):
            unplaced.append(task.workflow_id)
    planned = {target for group in (plan.groups if plan else []) for target in group.subtitle_ids}
    failures = [item.group_id for item in draft.dubbing_production.group_failures
                if plan and item.source_revision == plan.source_revision and item.plan_revision == plan.plan_revision
                and any(g.group_id == item.group_id and set(g.subtitle_ids) & expected_set for g in plan.groups)]
    # Evidence missing for manually edited material is a warning, not a request
    # for human approval and not proof that a new generation is required.
    run = build_production_run_snapshot(current_source_revision=snapshot.source_revision, active_plan=plan,
        workflows=[], candidate_inputs=draft.dubbing_production.candidate_inputs,
        candidate_reports=draft.dubbing_production.candidate_reports, group_failures=[],
        timeline_clips=draft.timeline_clips,
        manual_timing_deferrals=draft.dubbing_production.manual_timing_deferrals,
        existing_formal_acceptances=draft.dubbing_production.existing_formal_acceptances,
        playable_clip_ids=playable_clip_ids,
        audio_sha256_by_clip_id=audio_sha256_by_clip_id)
    checked = {clip for group in run.groups if group.stage == 'accepted' for clip in group.formal_clip_ids}
    if plan and plan.source_revision != snapshot.source_revision:
        checked = set()
    unchecked = [str(clip['clip_id']) for clip in clips
                 if clip['clip_id'] not in checked and clip['clip_id'] not in deferred_clip_ids]
    prerequisites = ['localized_subtitles'] if not draft.localized_subtitles else []
    scoped_group_ids = {group.group_id for group in (plan.groups if plan else [])
                        if set(group.subtitle_ids) & expected_set}
    scoped_groups = [group for group in run.groups if group.group_id in scoped_group_ids]
    deferred_group_ids = [group.group_id for group in scoped_groups
                          if group.stage == 'deferred_manual_timing']
    # The canonical group projection owns whether an earlier failure has been
    # resolved. Keep historical failures in storage, not in today's problem list.
    # Pending explicit new takes remain independently visible above.
    resolved_group_ids = {
        group.group_id for group in scoped_groups
        if group.stage in {'accepted', 'deferred_manual_timing'}
    }
    failures = [group_id for group_id in failures if group_id not in resolved_group_ids]
    automated_resolved = bool(scoped_groups) and all(
        group.stage in {'accepted', 'deferred_manual_timing'} for group in scoped_groups
    ) and not (expected_set - planned or pending or unplaced or prerequisites)
    status = 'incomplete' if (missing or deferred_group_ids or invalid or overlaps or repeated
                              or pending or unplaced or prerequisites) else (
        'complete_with_warnings' if unchecked or failures else 'complete')
    return DubbingCompletionSnapshot(
        source_revision=snapshot.source_revision, start_ms=start_ms, end_ms=end_ms, status=status,
        expected_target_subtitle_ids=expected, covered_target_subtitle_ids=[t for t in expected if t in covered],
        missing_target_subtitle_ids=[t for t in expected if t in missing],
        unplanned_target_subtitle_ids=[t for t in expected if t not in planned],
        invalid_clip_ids=invalid, overlapping_clip_pairs=overlaps, repeated_clip_pairs=repeated,
        missing_prerequisites=prerequisites, pending_workflow_ids=pending,
        unplaced_workflow_ids=unplaced, unresolved_group_ids=failures, unchecked_clip_ids=unchecked,
        deferred_manual_timing_group_ids=deferred_group_ids,
        deferred_manual_timing_clip_ids=[str(item.parked_clip_id)
                                         for group_id, item in valid_deferrals.items()
                                         if group_id in scoped_group_ids],
        automated_production_status=('resolved' if automated_resolved else 'unresolved'),
    )
