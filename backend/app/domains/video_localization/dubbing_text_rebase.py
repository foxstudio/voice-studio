"""Preserve reviewed grouping when an editor changes only localized wording."""

from app.domains.video_localization import dubbing_production as domain
from app.schemas.voice_studio import VideoLocalizationDraft


def rebase_text_edit(
    before: VideoLocalizationDraft,
    after: VideoLocalizationDraft,
    *, project_id: str | None = None,
) -> VideoLocalizationDraft:
    """Return a narrowly rebound draft, or unchanged input for ordinary invalidation.

    Frozen workflows and timeline media are never rewritten. Only evidence for
    an identical group contract can acquire the new editorial revision.
    """
    state = before.dubbing_production
    plan = state.active_plan
    if plan is None or after.dubbing_production != state:
        return after
    if any(getattr(before, field) != getattr(after, field) for field in (
        "source_media", "stems", "speakers", "reference_clips", "language_config",
    )):
        return after
    old_subs = {item.subtitle_id: item for item in before.localized_subtitles}
    old_segments = {item.segment_id: item for item in before.localized_spoken_segments}
    if ([item.subtitle_id for item in after.localized_subtitles] != list(old_subs)
        or [item.segment_id for item in after.localized_spoken_segments] != list(old_segments)):
        return after
    current_subs = {item.subtitle_id: item for item in after.localized_subtitles}
    changed = {key for key, item in current_subs.items()
               if (item.text, item.tts_text) != (old_subs[key].text, old_subs[key].tts_text)}
    spoken_changed = {key for key, item in current_subs.items()
                      if (item.tts_text or item.text) != (old_subs[key].tts_text or old_subs[key].text)}
    if not changed and all(item.text == old_segments[item.segment_id].text
                           for item in after.localized_spoken_segments):
        return after
    restored_subs = [item.model_copy(update={
        "text": old_subs[item.subtitle_id].text,
        "tts_text": old_subs[item.subtitle_id].tts_text,
    }) for item in after.localized_subtitles]
    restored_segments = [item.model_copy(update={"text": old_segments[item.segment_id].text})
                         for item in after.localized_spoken_segments]
    restored = after.model_copy(update={"localized_subtitles": restored_subs,
                                       "localized_spoken_segments": restored_segments})
    if domain.build_project_snapshot(restored).source_revision != plan.source_revision:
        return after
    revision = domain.build_project_snapshot(after).source_revision
    if revision == plan.source_revision:
        return after
    segment_text = {}
    for segment in after.localized_spoken_segments:
        if segment.text == old_segments[segment.segment_id].text:
            continue
        targets = [item.subtitle_id for item in after.localized_subtitles
                   if item.spoken_segment_id == segment.segment_id]
        # A standalone spoken edit has no per-subtitle allocation. Do not
        # guess how to distribute it across already reviewed semantic units.
        if not set(targets).intersection(changed):
            units = [unit for unit in plan.semantic_units if set(unit.subtitle_ids) == set(targets)]
            if len(units) != 1:
                return after
            segment_text[units[0].unit_id] = segment.text
            changed.update(targets)
            spoken_changed.update(targets)
    if not changed:
        return after
    units = []
    for unit in plan.semantic_units:
        if not set(unit.subtitle_ids).intersection(changed):
            units.append(unit)
            continue
        if not set(unit.subtitle_ids).issubset(current_subs):
            return after
        items = [current_subs[key] for key in unit.subtitle_ids]
        units.append(unit.model_copy(update={
            "display_text": " ".join(item.text for item in items),
            "spoken_text": (segment_text.get(unit.unit_id, "".join((item.tts_text or item.text).strip() for item in items))
                            if set(unit.subtitle_ids).intersection(spoken_changed) else unit.spoken_text),
        }))
    by_id = {unit.unit_id: unit for unit in units}
    groups = [group.model_copy(update={
        "spoken_text": "\n".join(by_id[key].spoken_text for key in group.unit_ids),
    }) if set(group.subtitle_ids).intersection(spoken_changed) else group for group in plan.groups]
    unchanged = {new.group_id for old, new in zip(plan.groups, groups) if old == new}
    for group in groups:
        def clips(draft):
            return [item for item in draft.timeline_clips
                    if item.get("dubbing_group_id") == group.group_id
                    or set(item.get("target_subtitle_ids") or [item.get("subtitle_id")]).intersection(group.subtitle_ids)]
        if clips(before) != clips(after):
            unchanged.discard(group.group_id)
    next_revision = max(state.plan_revision_counter, plan.plan_revision) + 1
    lineage = {"source_revision": revision, "plan_revision": next_revision}
    rebound_plan = plan.model_copy(update={**lineage, "semantic_units": units, "groups": groups})
    from app.domains.video_localization import dubbing_media
    from app.domains.video_localization.dubbing_plan_continuation import (
        retain_unchanged_completion_evidence,
        retain_unchanged_manual_timing_deferrals,
    )

    # Text edits and explicit replanning use the same dependency, file and
    # projection checks. No separate path can promote an old report.
    audio_hashes = (
        dubbing_media.current_timeline_audio_sha256s(project_id, after)
        if project_id is not None else {}
    )
    inputs, reports = retain_unchanged_completion_evidence(
        state=state, new_plan=rebound_plan, current_draft=after,
        timeline_clips=[dict(clip) for clip in after.timeline_clips],
        audio_sha256_by_clip_id=audio_hashes,
    )
    deferrals = retain_unchanged_manual_timing_deferrals(
        state=state, new_plan=rebound_plan, current_draft=after,
        timeline_clips=[dict(clip) for clip in after.timeline_clips],
        audio_sha256_by_clip_id=audio_hashes,
    )
    # Changed/unsupported evidence remains in task/media history, not as a
    # current pass claim. Media deletion and frozen task lineage remain intact.
    def retained(items):
        return [item.model_copy(update=lineage) for item in items
                if item.group_id in unchanged and item.source_revision == plan.source_revision
                and item.plan_revision == plan.plan_revision]
    rebound = state.model_copy(update={
        "active_plan": rebound_plan, "plan_revision_counter": next_revision,
        "candidate_inputs": inputs, "candidate_reports": reports,
        "manual_timing_deferrals": deferrals,
        "scheduling_policies": retained(state.scheduling_policies),
        "group_failures": retained(state.group_failures),
        "group_reviews": retained(state.group_reviews),
        "existing_formal_acceptances": [], "latest_timeline_audit": None,
    })
    return after.model_copy(update={"dubbing_production": rebound})
