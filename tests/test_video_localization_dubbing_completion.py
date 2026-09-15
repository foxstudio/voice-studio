from copy import deepcopy
from types import SimpleNamespace
import pytest

from app.domains.video_localization.dubbing_completion import build_completion_snapshot
from app.domains.video_localization.schemas import VideoLocalizationDraft


def draft_with_two_clips():
    return VideoLocalizationDraft(
        source_media={'duration_ms': 5000},
        localized_subtitles=[dict(subtitle_id=key, text=key, start_ms=start, end_ms=start+1000,
                                  source_cue_ids=[key]) for key, start in [('a', 0), ('b', 2000)]],
        timeline_clips=[dict(clip_id=key, subtitle_id=key, target_subtitle_ids=[key],
                            track_id='dub', dub_lane=0, start_ms=start, end_ms=start+1000,
                            source_start_ms=0, source_end_ms=1000, audio_path=f'{key}.wav')
                        for key, start in [('a', 0), ('b', 2000)]],
    )


@pytest.mark.parametrize('generation_status, discarded, expected', [
    ('success', False, ['saved-workflow']),
    ('failed', False, []),
    ('success', True, []),
])
def test_failed_placement_keeps_generated_unplaced_result_visible(generation_status, discarded, expected):
    from app.models.schemas import VideoLocalizationTtsTask
    draft = draft_with_two_clips()
    draft.timeline_clips = draft.timeline_clips[:1]
    draft.tts_tasks = [VideoLocalizationTtsTask(
        workflow_id='saved-workflow', project_id='project', segment_id='b',
        subtitle_summary='b', text='b', start_ms=2000, end_ms=3000,
        status='failed', result_id='saved-result', stages=[
            dict(kind='generation', status=generation_status,
                 parameters={'video_localization_target_subtitle_ids': ['b']}),
            dict(kind='placement', status='failed'),
        ])]
    if discarded:
        draft.ui_state['discarded_tts_task_ids'] = ['saved-result']
    result = build_completion_snapshot(draft, audio_duration_ms_by_clip_id={'a': 1000})
    assert result.status == 'incomplete'
    assert result.unplaced_workflow_ids == expected


def test_actual_manual_timeline_is_covered_without_claiming_automatic_review():
    draft = draft_with_two_clips()
    before = deepcopy(draft)
    result = build_completion_snapshot(draft, audio_duration_ms_by_clip_id={'a': 1000, 'b': 1000})
    assert result.status == 'complete_with_warnings'
    assert result.covered_target_subtitle_ids == ['a', 'b']
    assert result.unplanned_target_subtitle_ids == ['a', 'b']
    assert result.unchecked_clip_ids == ['a', 'b']
    assert draft == before


def test_scope_end_cannot_hide_missing_remainder_of_full_video():
    draft = draft_with_two_clips()
    draft.timeline_clips = draft.timeline_clips[:1]
    full = build_completion_snapshot(draft, audio_duration_ms_by_clip_id={'a': 1000})
    assert full.status == 'incomplete'
    assert full.missing_target_subtitle_ids == ['b']
    bounded = build_completion_snapshot(draft, audio_duration_ms_by_clip_id={'a': 1000}, end_ms=1500)
    assert bounded.status == 'complete_with_warnings'
    assert bounded.missing_target_subtitle_ids == []


def test_file_failure_and_repeated_playback_are_not_completed():
    draft = draft_with_two_clips()
    result = build_completion_snapshot(draft, audio_duration_ms_by_clip_id={'a': None, 'b': 1000})
    assert result.status == 'incomplete'
    assert result.invalid_clip_ids == ['a']
    draft.timeline_clips.append({**draft.timeline_clips[0], 'clip_id': 'again',
                                 'start_ms': 3500, 'end_ms': 4500, 'dub_lane': 1})
    result = build_completion_snapshot(draft, audio_duration_ms_by_clip_id={'a': 1000, 'b': 1000, 'again': 1000})
    assert result.repeated_clip_pairs == [('a', 'again')]
    assert result.status == 'incomplete'


def test_no_subtitles_is_not_successfully_completed_production():
    result = build_completion_snapshot(VideoLocalizationDraft(source_media={'duration_ms': 5000}),
                                       audio_duration_ms_by_clip_id={})
    assert result.status == 'incomplete'
    assert result.missing_prerequisites == ['localized_subtitles']


def test_readiness_uses_manual_timeline_instead_of_old_cue_failure(monkeypatch):
    from app.domains.video_localization import readiness, dubbing_media
    draft = draft_with_two_clips()
    draft.cues = []
    monkeypatch.setattr(dubbing_media, "current_timeline_audio_durations", lambda *_: {"a": 1000, "b": 1000})
    audit = readiness.build_production_readiness_audit(project_id="test", project_name="test", draft=draft)
    assert audit["summary"]["coverage_source"] == "timeline"
    assert audit["summary"]["generated_tts_count"] == 2
    assert audit["dubbing_completion"]["missing_target_subtitle_ids"] == []
    assert not any(check["code"] == "tts_failures" for check in audit["checks"])


def test_readiness_and_completion_collect_identical_media_identity(monkeypatch):
    from app.domains.video_localization import readiness, dubbing_media, dubbing_completion
    draft = draft_with_two_clips()
    hashes = {'a': 'a' * 64, 'b': 'b' * 64}
    monkeypatch.setattr(dubbing_media, 'current_timeline_audio_durations', lambda *_: {'a': 1000, 'b': 1000})
    monkeypatch.setattr(dubbing_media, 'current_timeline_audio_sha256s', lambda *_: hashes)
    original = dubbing_completion.build_completion_snapshot
    observed = []
    def project(draft, **kwargs):
        observed.append(kwargs.get('audio_sha256_by_clip_id'))
        return original(draft, **kwargs)
    monkeypatch.setattr(dubbing_completion, 'build_completion_snapshot', project)
    direct = dubbing_completion.read_current_completion('test', draft)
    audit = readiness.build_production_readiness_audit(project_id='test', project_name='test', draft=draft)
    assert observed == [hashes, hashes]
    assert audit['dubbing_completion'] == direct.model_dump(mode='json')


@pytest.mark.parametrize('historical_unplaced', [False, True])
def test_bounded_deferral_completes_only_its_requested_automated_scope(monkeypatch, historical_unplaced):
    from app.domains.video_localization import dubbing_production as domain, dubbing_production_run as run_module
    from app.schemas.video_localization_dubbing_production import DubbingGenerationPlan, DubbingProductionGroupFailure

    draft = draft_with_two_clips()
    revision = domain.dubbing_source_revision(draft)
    plan = DubbingGenerationPlan(
        source_revision=revision, plan_revision=1, status='passed', semantic_units=[], speech_islands=[],
        groups=[dict(group_id=key, island_id=key, unit_ids=[key], subtitle_ids=[key],
                     speaker_id='speaker', spoken_text=key, target_start_ms=start, target_end_ms=start+1000,
                     source_reference_start_ms=start, source_reference_end_ms=start+1000)
                for key, start in [('a', 0), ('b', 2000)]],
    )
    failures = [DubbingProductionGroupFailure(
        source_revision=revision, plan_revision=1, group_id='a', title='Capacity', note='Old overflow',
        reason_code='group_capacity_recovery_decision_required', created_at='2026-09-07T00:00:00Z')]
    draft.dubbing_production = draft.dubbing_production.model_copy(update={
        'active_plan': plan, 'group_failures': failures})
    if historical_unplaced:
        from app.models.schemas import VideoLocalizationTtsTask
        draft.timeline_clips = [clip for clip in draft.timeline_clips if clip['clip_id'] != 'a']
        draft.tts_tasks = [VideoLocalizationTtsTask(
            workflow_id='older-take', project_id='project', segment_id='a',
            subtitle_summary='old a', text='old a', start_ms=0, end_ms=1000,
            status='failed', result_id='older-result-not-selected', stages=[
                dict(kind='generation', status='success', parameters={'video_localization_target_subtitle_ids': ['a']}),
                dict(kind='placement', status='failed'),
            ],
        )]
    # The deferral validator and read model own validity. This test exercises
    # completion's range and historical-failure projection, not those validators.
    monkeypatch.setattr(run_module, 'valid_manual_timing_deferrals', lambda **_: {
        'a': SimpleNamespace(parked_clip_id='parked-a', result_id='result-a')})
    monkeypatch.setattr(run_module, 'build_production_run_snapshot', lambda **_: SimpleNamespace(groups=[
        SimpleNamespace(group_id='a', stage='deferred_manual_timing', formal_clip_ids=[]),
        SimpleNamespace(group_id='b', stage='ready_to_generate', formal_clip_ids=[]),
    ]))
    durations = {'a': 1000, 'b': 1000}
    bounded = build_completion_snapshot(draft, audio_duration_ms_by_clip_id=durations, end_ms=1500)
    assert bounded.automated_production_status == 'resolved'
    assert bounded.status == 'incomplete'
    assert bounded.unchecked_clip_ids == ([] if historical_unplaced else ['a'])  # Old primary isn't falsely accepted.
    assert bounded.unplaced_workflow_ids == []
    assert bounded.unresolved_group_ids == []
    assert bounded.deferred_manual_timing_clip_ids == ['parked-a']
    full = build_completion_snapshot(draft, audio_duration_ms_by_clip_id=durations)
    assert full.automated_production_status == 'unresolved'
    later = build_completion_snapshot(draft, audio_duration_ms_by_clip_id=durations, start_ms=2000)
    assert later.deferred_manual_timing_group_ids == []
    assert later.deferred_manual_timing_clip_ids == []


def test_current_accepted_group_supersedes_old_failure_without_hiding_new_work(monkeypatch):
    from app.domains.video_localization import dubbing_production as domain, dubbing_production_run as run_module
    from app.schemas.video_localization_dubbing_production import DubbingGenerationPlan, DubbingProductionGroupFailure
    from app.models.schemas import VideoLocalizationTtsTask

    draft = draft_with_two_clips()
    revision = domain.dubbing_source_revision(draft)
    plan = DubbingGenerationPlan(
        source_revision=revision, plan_revision=1, status='passed', semantic_units=[], speech_islands=[],
        groups=[dict(group_id=key, island_id=key, unit_ids=[key], subtitle_ids=[key],
                     speaker_id='speaker', spoken_text=key, target_start_ms=start, target_end_ms=start+1000,
                     source_reference_start_ms=start, source_reference_end_ms=start+1000)
                for key, start in [('a', 0), ('b', 2000)]],
    )
    failures = [DubbingProductionGroupFailure(
        source_revision=revision, plan_revision=1, group_id='a', title='Old failure', note='Resolved by adoption',
        reason_code='group_capacity_recovery_decision_required', created_at='2026-09-07T00:00:00Z')]
    draft.dubbing_production = draft.dubbing_production.model_copy(update={
        'active_plan': plan, 'group_failures': failures})
    monkeypatch.setattr(run_module, 'build_production_run_snapshot', lambda **_: SimpleNamespace(groups=[
        SimpleNamespace(group_id=key, stage='accepted', formal_clip_ids=[key]) for key in ['a', 'b']
    ]))
    before = deepcopy(draft)
    result = build_completion_snapshot(draft, audio_duration_ms_by_clip_id={'a': 1000, 'b': 1000})
    assert result.unresolved_group_ids == []
    assert result.status == 'complete'
    assert draft == before  # Historical evidence is retained; only the current view changes.
    draft.tts_tasks = [VideoLocalizationTtsTask(
        workflow_id='new-take', project_id='project', segment_id='a', subtitle_summary='a',
        text='a', start_ms=0, end_ms=1000, status='running', stages=[
            dict(kind='generation', status='running', parameters={'video_localization_target_subtitle_ids': ['a']}),
            dict(kind='placement', status='pending'),
        ])]
    result = build_completion_snapshot(draft, audio_duration_ms_by_clip_id={'a': 1000, 'b': 1000})
    assert result.pending_workflow_ids == ['new-take']
    assert result.automated_production_status == 'unresolved'
