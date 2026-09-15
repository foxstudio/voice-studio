from __future__ import annotations

import sys
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.dubbing_production import (  # noqa: E402
    audit_timeline,
    build_candidate_gap_processing_report,
    candidate_evidence_fingerprint,
    build_generation_plan,
    build_current_timeline_audit_input,
    build_project_snapshot,
    compare_transcripts,
    continuous_speech_underfill_assessments,
    DubbingContinuousBoundaryUnderfill,
    dubbing_source_revision,
    evaluate_candidate,
    rebase_compatible_generation_plan,
    refresh_generation_plan_timings,
    rebalance_planned_timeline_clips,
    rebalance_selected_group_timeline_clips,
    rebalance_selected_group_with_adjacent_window_timeline_clips,
    restore_rebalance_groups,
    split_planned_timeline_clips,
    transcript_pronunciation_tokens,
)
from app.domains.video_localization.dubbing_production_run import (  # noqa: E402
    build_production_run_snapshot,
)
from app.domains.video_localization.dubbing_timeline_edit_gate import (  # noqa: E402
    candidate_audible_timeline_bounds,
    candidate_clip_projection_fingerprint,
    reconcile_gap_evidence_with_projection,
    rendered_gap_duration_ms,
)
from app.domains.video_localization.audio_boundaries import (  # noqa: E402
    analyze_dubbing_candidate_audio,
)
from app.domains.video_localization import (  # noqa: E402
    draft_store,
    dubbing_candidate_alignment,
    dubbing_gap_adjudication,
    dubbing_production_run,
    dubbing_production_service,
    dub_subtitles,
    media_assets,
    quality_gate,
    tts_pipeline,
)
from app.errors import AppException  # noqa: E402
from app.domains.video_localization.dubbing_production_service import (  # noqa: E402
    DubbingProductionApplicationService,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
    VideoLocalizationAudioBoundaryEvidence,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationDubSubtitleCue,
    VideoLocalizationSpokenSegment,
    VideoLocalizationSubtitleCue,
    VideoLocalizationTranscriptionState,
)
from app.schemas.video_localization_dubbing_production import (  # noqa: E402
    DubbingAudioGapEvidence,
    DubbingAutomaticAudioEvidence,
    DubbingBoundaryEvidence,
    DubbingCandidateAlignedWord,
    DubbingCandidateCqcInput,
    DubbingCandidateReviewCommand,
    DubbingGenerationGroup,
    DubbingGenerationPlan,
    DubbingGenerationPlanInput,
    DubbingPauseEvidence,
    DubbingProductionSnapshot,
    DubbingProductionState,
    DubbingProductionGroupReview,
    DubbingProductionGroupFailure,
    DubbingProductionManualReviewRequest,
    DubbingSemanticUnit,
    DubbingStagedCandidateClip,
    DubbingStagedCandidateProjection,
    DubbingSubjectiveReview,
    DubbingTimelineAuditInput,
    DubbingTimelineAuditReport,
    DubbingTimelineClipSplitCommand,
    DubbingTimelineClip,
    DubbingTimelineExpectedUnit,
    DubbingTimelineEditGate,
    DubbingVoicedSpan,
)


def test_reconcile_existing_formal_group_preserves_clips_and_records_provenance(
    monkeypatch,
    tmp_path: Path,
):
    source_revision = "f" * 64
    group = DubbingGenerationGroup(
        group_id="group-1",
        island_id="island-1",
        unit_ids=["unit-1"],
        subtitle_ids=["localized-1"],
        speaker_id="speaker-1",
        spoken_text="这是一句测试。",
        target_start_ms=1_000,
        target_end_ms=2_000,
        source_reference_start_ms=1_000,
        source_reference_end_ms=2_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=source_revision,
        plan_revision=7,
        status="passed",
        semantic_units=[],
        speech_islands=[],
        groups=[group],
    )
    clip = {
        "clip_id": "clip-existing",
        "track_id": "dub",
        "dub_lane": 0,
        "candidate_id": "candidate-existing",
        "result_id": "result-existing",
        "dubbing_group_id": "group-1",
        "target_subtitle_ids": ["localized-1"],
        "start_ms": 1_000,
        "end_ms": 2_000,
        "source_start_ms": 0,
        "source_end_ms": 1_000,
    }
    projection_fingerprint = candidate_clip_projection_fingerprint([clip])
    clip["timeline_edit_gate"] = DubbingTimelineEditGate(
        source_revision="e" * 64,
        plan_revision=6,
        candidate_id="candidate-existing",
        cqc_report_fingerprint="c" * 64,
        candidate_clip_projection_fingerprint=projection_fingerprint,
        status="passed",
        actual_speech_start_delta_ms=0,
        actual_speech_end_delta_ms=0,
        speaking_rate_ratio=1.0,
        alignment_word_ids=["word-1"],
        gap_decisions=[
            DubbingAudioGapEvidence(
                gap_id="gap-1",
                kind="leading",
                start_ms=0,
                end_ms=50,
                duration_ms=50,
                evidence_sources=["waveform"],
                evidence_ids=["waveform:gap-1"],
                boundary_confidence="clear",
                edit_decision="retain",
                retained_duration_ms=50,
                decision_reason="保留",
                safe_edit_boundary=True,
            )
        ],
    ).model_dump(mode="json")
    original_clip = json.loads(json.dumps(clip))
    draft = VideoLocalizationDraft(
        timeline_clips=[clip],
        dubbing_production=DubbingProductionState(
            plan_revision_counter=7,
            active_plan=plan,
        ),
    )
    current = {"draft": draft}
    service = DubbingProductionApplicationService()
    output_path = tmp_path / "existing.wav"
    output_path.write_bytes(b"existing-audio")
    monkeypatch.setattr(
        service,
        "_require_current_project",
        lambda _project_id: current["draft"],
    )
    monkeypatch.setattr(
        "app.domains.video_localization.dubbing_production_service.history_store.get",
        lambda _result_id: SimpleNamespace(
            project_id="project-1",
            task_id="task-1",
            output_path=str(output_path),
        ),
    )
    monkeypatch.setattr(
        "app.domains.video_localization.dubbing_production_service.task_queue.get_task",
        lambda _task_id: SimpleNamespace(
            project_id="project-1",
            status=SimpleNamespace(value="success"),
            parameters={
                "text": "这是一句测试。",
                "video_localization_target_subtitle_ids": ["localized-1"],
            },
            input_text="这是一句测试。",
            localized_subtitle_id=None,
        ),
    )

    def update(_project_id, apply, *, intent):
        assert intent == "runtime"
        current["draft"] = apply(current["draft"])
        return current["draft"]

    monkeypatch.setattr(
        "app.domains.video_localization.dubbing_production_service.project_service.update_video_localization_atomic",
        update,
    )

    result = service.reconcile_existing_formal_groups("project-1")

    assert result.accepted_group_count == 1
    assert result.pending_review_group_count == 0
    assert current["draft"].timeline_clips == [original_clip]
    acceptance = current[
        "draft"
    ].dubbing_production.existing_formal_acceptances[0]
    assert acceptance.group_id == "group-1"
    assert acceptance.candidate_clip_projection_fingerprint == projection_fingerprint
    run = service._build_production_run_snapshot(current["draft"])
    assert run.groups[0].stage == "accepted"
    assert run.groups[0].passed_candidate_id == "candidate-existing"



def test_generated_group_recovery_prefers_new_history_over_stale_report(
    monkeypatch,
):
    service = DubbingProductionApplicationService()
    group = SimpleNamespace(
        group_id="group-1",
        subtitle_ids=["localized-1"],
    )
    plan = SimpleNamespace(
        source_revision="source-1",
        plan_revision=1,
        groups=[group],
    )
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=plan,
            candidate_reports=[
                SimpleNamespace(
                    group_id="group-1",
                    source_revision="source-1",
                    plan_revision=1,
                    candidate_id="candidate-deleted",
                )
            ],
            candidate_inputs=[],
        ),
        timeline_clips=[],
    )
    calls: list[str] = []
    monkeypatch.setattr(
        service,
        "_require_current_project",
        lambda _project_id: draft,
    )
    monkeypatch.setattr(
        service,
        "resync_candidate_automatic_cqc",
        lambda _project_id, _identity: (_ for _ in ()).throw(
            AppException(404, "VIDEO_LOCALIZATION_CANDIDATE_NOT_FOUND", "missing")
        ),
    )

    def history_resync(_project_id, identity):
        calls.append(identity)
        if identity == "stale-task-id":
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_HISTORY_NOT_FOUND",
                "missing",
            )
        return SimpleNamespace(group_id="group-1", candidate_id="candidate-valid")

    monkeypatch.setattr(
        service,
        "resync_history_candidate_automatic_cqc",
        history_resync,
    )
    def finalize(project_id, candidate_id, group_id):
        calls.append(f"finalize:{project_id}:{candidate_id}:{group_id}")
        if candidate_id == "candidate-deleted":
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_CANDIDATE_NOT_FOUND",
                "missing",
            )
        return "accepted"

    monkeypatch.setattr(service, "finalize_generated_candidate", finalize)

    result = service.recover_and_finalize_generated_group(
        "project-1",
        "group-1",
        ["stale-task-id", "valid-history-id"],
    )

    assert result == "accepted"
    assert calls == [
        "stale-task-id",
        "valid-history-id",
        "finalize:project-1:candidate-valid:group-1",
    ]


def test_generated_group_recovery_maps_timeline_conflict_to_regeneration(
    monkeypatch,
):
    service = DubbingProductionApplicationService()
    group = SimpleNamespace(group_id="group-1", subtitle_ids=["localized-1"])
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(
                source_revision="source-1",
                plan_revision=1,
                groups=[group],
            ),
            candidate_inputs=[],
            candidate_reports=[],
        ),
        timeline_clips=[],
    )
    monkeypatch.setattr(service, "_require_current_project", lambda _project_id: draft)
    monkeypatch.setattr(
        service,
        "resync_candidate_automatic_cqc",
        lambda _project_id, _identity: SimpleNamespace(
            group_id="group-1",
            candidate_id="candidate-new",
        ),
    )
    monkeypatch.setattr(
        service,
        "finalize_generated_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AppException(
                409,
                "VIDEO_LOCALIZATION_DUBBING_COMMIT_TIMELINE_CONFLICT",
                "conflict",
            )
        ),
    )

    assert (
        service.recover_and_finalize_generated_group(
            "project-1",
            "group-1",
            ["candidate-new"],
        )
        == "regeneration_required"
    )


def test_generated_group_recovery_tries_older_viable_candidate_before_failing(
    monkeypatch,
):
    service = DubbingProductionApplicationService()
    group = SimpleNamespace(group_id="group-1", subtitle_ids=["localized-1"])
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(
                source_revision="source-1",
                plan_revision=1,
                groups=[group],
            ),
            candidate_inputs=[],
            candidate_reports=[],
        ),
        timeline_clips=[],
    )
    monkeypatch.setattr(service, "_require_current_project", lambda _project_id: draft)
    monkeypatch.setattr(
        service,
        "resync_candidate_automatic_cqc",
        lambda _project_id, identity: SimpleNamespace(
            group_id="group-1",
            candidate_id=identity,
        ),
    )
    monkeypatch.setattr(
        service,
        "finalize_generated_candidate",
        lambda _project_id, candidate_id, _group_id, **_kwargs: (
            "regeneration_required"
            if candidate_id == "candidate-newer"
            else "accepted"
        ),
    )

    assert (
        service.recover_and_finalize_generated_group(
            "project-1",
            "group-1",
            ["candidate-newer", "candidate-older"],
        )
        == "accepted"
    )


@pytest.mark.parametrize("current_result,older_result,expected", [
    ("retryable_failure", "regeneration_required", "retryable_failure"),
    ("regeneration_required", "retryable_failure", "regeneration_required"),
    ("retryable_failure", "accepted", "accepted"),
    ("read_error", "regeneration_required", "retryable_failure"),
    ("regeneration_required", "read_error", "regeneration_required"),
])
def test_recovery_uses_current_candidate_need_not_an_older_failure(
    monkeypatch, current_result, older_result, expected,
):
    service = DubbingProductionApplicationService()
    group = SimpleNamespace(group_id="group-1", subtitle_ids=["localized-1"])
    old_report = SimpleNamespace(group_id="group-1", source_revision="source-1",
                                 plan_revision=1, candidate_id="older")
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(source_revision="source-1", plan_revision=1, groups=[group]),
            candidate_inputs=[], candidate_reports=[old_report],
        ), timeline_clips=[],
    )
    monkeypatch.setattr(service, "_require_current_project", lambda _: draft)
    def resync(_project, identity):
        disposition = current_result if identity == "current" else older_result
        if disposition == "read_error":
            raise AppException(409, "TTS_CONTENT_AUDIO_UNAVAILABLE", "unavailable")
        return SimpleNamespace(group_id="group-1", candidate_id=identity)
    monkeypatch.setattr(service, "resync_candidate_automatic_cqc", resync)
    monkeypatch.setattr(service, "finalize_generated_candidate",
                        lambda _project, identity, _group: (
                            "retryable_failure" if (current_result if identity == "current" else older_result) == "read_error"
                            else current_result if identity == "current" else older_result))
    assert service.recover_and_finalize_generated_group("project", "group-1", ["current", "older"]) == expected


def test_automatic_outer_gap_trim_repairs_preliminary_projection_without_expanding_edits(
    monkeypatch,
):
    service = DubbingProductionApplicationService()
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip-1",
                "track_id": "dub",
                "candidate_id": "candidate-1",
                "start_ms": 10_000,
                "end_ms": 17_360,
                "source_start_ms": 0,
                "source_end_ms": 7_360,
                "alignment_lead_ms": 0,
                "alignment_trail_ms": 0,
                "timeline_edit_gate": {"status": "passed"},
            },
            {
                "clip_id": "completed-neighbor",
                "track_id": "dub",
                "candidate_id": "completed-candidate",
                "start_ms": 2_000,
                "end_ms": 3_000,
                "source_start_ms": 200,
                "source_end_ms": 1_200,
            },
        ]
    )
    frozen = SimpleNamespace(
        target_start_ms=10_000,
        audio=SimpleNamespace(
            speech_start_ms=430,
            speech_end_ms=7_190,
            aligned_words=[
                DubbingCandidateAlignedWord(
                    word_id="word-first",
                    text="开",
                    start_ms=390,
                    end_ms=620,
                ),
                DubbingCandidateAlignedWord(
                    word_id="word-last",
                    text="始",
                    start_ms=7_080,
                    end_ms=7_250,
                ),
            ],
        ),
    )
    gaps = [
        DubbingAudioGapEvidence(
            gap_id="leading",
            kind="leading",
            start_ms=0,
            end_ms=430,
            duration_ms=430,
            evidence_sources=["word_alignment"],
            evidence_ids=["leading-evidence"],
            boundary_confidence="clear",
            edit_decision="remove",
            retained_duration_ms=0,
            decision_reason="trim",
            safe_edit_boundary=True,
        ),
        DubbingAudioGapEvidence(
            gap_id="trailing",
            kind="trailing",
            start_ms=7_190,
            end_ms=7_360,
            duration_ms=170,
            evidence_sources=["word_alignment"],
            evidence_ids=["trailing-evidence"],
            boundary_confidence="clear",
            edit_decision="remove",
            retained_duration_ms=0,
            decision_reason="trim",
            safe_edit_boundary=True,
        ),
    ]

    def update(_project_id, apply, **_kwargs):
        return apply(draft)

    monkeypatch.setattr(
        "app.domains.video_localization.dubbing_production_service.project_service.update_video_localization_atomic",
        update,
    )

    updated = service._trim_automatic_outer_gaps(
        "project-1",
        candidate_id="candidate-1",
        frozen=frozen,
        reviewed_gaps=gaps,
    )

    target = next(item for item in updated.timeline_clips if item["clip_id"] == "clip-1")
    assert target["source_start_ms"] == 310
    assert target["source_end_ms"] == 7_330
    assert target["start_ms"] == 9_920
    assert target["end_ms"] == 16_940
    assert target["alignment_lead_ms"] == 80
    assert target["alignment_trail_ms"] == 80
    assert "timeline_edit_gate" not in target
    assert updated.timeline_clips[1] == draft.timeline_clips[1]


def test_gap_projection_rejects_unexecuted_requested_edit():
    gap = DubbingAudioGapEvidence(
        gap_id="gap_internal",
        kind="internal",
        start_ms=500,
        end_ms=900,
        duration_ms=400,
        evidence_sources=["waveform", "word_alignment"],
        evidence_ids=["waveform:gap_internal", "alignment:gap_internal"],
        boundary_confidence="clear",
        edit_decision="remove",
        retained_duration_ms=0,
        decision_reason="逐词证据确认可以删除。",
        safe_edit_boundary=True,
        semantic_role="continuous_phrase",
    )

    with pytest.raises(ValueError, match="不能改写为保留"):
        reconcile_gap_evidence_with_projection(
            [gap],
            [
                {
                    "clip_id": "clip-1",
                    "candidate_id": "candidate-1",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "source_start_ms": 0,
                    "source_end_ms": 1_000,
                }
            ],
        )


def test_split_blocker_comparison_uses_stable_group_identity():
    groups = [
        SimpleNamespace(group_id="group-left", subtitle_ids=["subtitle-left"]),
        SimpleNamespace(group_id="group-right", subtitle_ids=["subtitle-right"]),
    ]
    before_draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(groups=groups)
        ),
        timeline_clips=[
            {
                "clip_id": "left-before",
                "target_subtitle_ids": ["subtitle-left"],
            },
            {
                "clip_id": "right-before",
                "target_subtitle_ids": ["subtitle-right"],
            },
        ],
    )
    after_draft = SimpleNamespace(
        dubbing_production=before_draft.dubbing_production,
        timeline_clips=[
            {
                "clip_id": "left-after-part-2",
                "dubbing_group_id": "group-left",
                "target_subtitle_ids": ["subtitle-left"],
            },
            {
                "clip_id": "right-before",
                "target_subtitle_ids": ["subtitle-right"],
            },
        ],
    )
    before = SimpleNamespace(
        findings=[
            SimpleNamespace(
                code="TIMELINE_UNEXPLAINED_DUB_OVERLAP",
                severity="blocking",
                entity_ids=["left-before", "right-before"],
            )
        ]
    )
    after = SimpleNamespace(
        findings=[
            SimpleNamespace(
                code="TIMELINE_UNEXPLAINED_DUB_OVERLAP",
                severity="blocking",
                entity_ids=["right-before", "left-after-part-2"],
            )
        ]
    )

    assert (
        dubbing_production_service._new_semantic_blocking_timeline_finding(
            before_draft,
            before,
            after_draft,
            after,
        )
        is None
    )


SOURCE_REVISION = "source-revision-1"
AUDIO_SHA = "a" * 64


@pytest.mark.parametrize('generation_status, action', [('success', 'process_gaps'), ('failed', 'regenerate_candidate')])
def test_failed_placement_does_not_request_new_generation(generation_status, action):
    plan = SimpleNamespace(source_revision='a'*64, plan_revision=3,
        groups=[SimpleNamespace(group_id='group_1', subtitle_ids=['localized_1'])])
    workflow = SimpleNamespace(workflow_id='workflow', status='failed', result_id='saved',
        generation_task_id='task', stages=[SimpleNamespace(kind='generation', status=generation_status,
        parameters={'video_localization_dubbing_group_id':'group_1',
                    'video_localization_dubbing_plan_revision':3})])
    run = build_production_run_snapshot(current_source_revision=plan.source_revision, active_plan=plan,
        workflows=[workflow], candidate_inputs=[], candidate_reports=[], group_failures=[], timeline_clips=[])
    assert run.groups[0].stage == 'failed'
    assert run.groups[0].recommended_action == action


def test_run_keeps_generated_history_separate_from_current_quality_acceptance():
    plan = SimpleNamespace(source_revision="a" * 64, plan_revision=3,
        groups=[SimpleNamespace(group_id="group_1", subtitle_ids=["localized_1"])])
    run = build_production_run_snapshot(
        current_source_revision=plan.source_revision, active_plan=plan,
        workflows=[], candidate_inputs=[], candidate_reports=[], group_failures=[],
        timeline_clips=[],
        recoverable_candidate_ids_by_group={"group_1": ["saved_result", "saved_task", "candidate_saved_task"]},
    )
    group = run.groups[0]
    assert group.stage == "needs_gap_processing"
    assert group.recommended_action == "process_gaps"
    assert group.candidate_ids == ["saved_result", "saved_task", "candidate_saved_task"]
    assert group.passed_candidate_id is None
    assert group.formal_clip_ids == []
    assert run.accepted_group_count == 0


@pytest.mark.parametrize('compatible', [True, False])
def test_run_exposes_only_verified_old_receipt_for_capacity_recovery(compatible):
    plan = SimpleNamespace(source_revision='a'*64, plan_revision=3,
        groups=[SimpleNamespace(group_id='group_1', subtitle_ids=['localized_1'])])
    workflow = SimpleNamespace(workflow_id='old', status='running', result_id='saved',
        generation_task_id='task', stages=[SimpleNamespace(kind='generation', status='success',
        parameters={'video_localization_dubbing_group_id':'group_1',
                    'video_localization_dubbing_plan_revision':2})])
    run = build_production_run_snapshot(current_source_revision=plan.source_revision,
        active_plan=plan, workflows=[workflow], candidate_inputs=[], candidate_reports=[],
        group_failures=[], timeline_clips=[],
        recoverable_candidate_ids_by_group={'group_1': ['candidate_task']} if compatible else {})
    assert run.groups[0].workflow_ids == (['old'] if compatible else [])
    assert run.groups[0].stage != 'generating'


def test_production_run_projection_exposes_recoverable_group_transitions():
    source_revision = "a" * 64
    plan = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=3,
        groups=[SimpleNamespace(group_id="group_1", subtitle_ids=["localized_1"])],
    )

    def project(
        *,
        workflows=None,
        inputs=None,
        reports=None,
        failures=None,
        clips=None,
    ):
        return build_production_run_snapshot(
            current_source_revision=source_revision,
            active_plan=plan,
            workflows=workflows or [],
            candidate_inputs=inputs or [],
            candidate_reports=reports or [],
            group_failures=failures or [],
            timeline_clips=clips or [],
        )

    ready = project()
    assert ready.groups[0].stage == "ready_to_generate"
    assert ready.next_action == "generate_candidate"

    pending_boundary_report = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=3,
        group_id="group_1",
        candidate_id="candidate_pending",
        overall_status="needs_review",
        semantic_boundary_audit=SimpleNamespace(status="pending_agent"),
    )
    semantic_pending = project(reports=[pending_boundary_report])
    assert semantic_pending.status == "needs_attention"
    assert semantic_pending.groups[0].stage == "needs_semantic_review"
    assert semantic_pending.next_action == "review_semantic_boundaries"

    pending_boundary_report.staged_candidate_projection = SimpleNamespace(clips=[{
        "clip_id": "pending", "track_id": "dub", "status": "ready",
        "start_ms": 900, "end_ms": 1900, "target_subtitle_ids": ["localized_1"],
    }])
    neighbour = {"clip_id": "previous", "track_id": "dub", "status": "ready",
                 "audio_path": "previous.wav", "start_ms": 0, "end_ms": 1000,
                 "target_subtitle_ids": ["another_target"]}
    stale_placement = project(reports=[pending_boundary_report], clips=[neighbour])
    assert stale_placement.groups[0].stage == "needs_gap_processing"
    assert stale_placement.next_action == "process_gaps"
    for safe_neighbour in [{**neighbour, "end_ms": 900}, {**neighbour, "dub_lane": 1}]:
        assert project(reports=[pending_boundary_report], clips=[safe_neighbour]).next_action == "review_semantic_boundaries"
    del pending_boundary_report.staged_candidate_projection

    existing_clip = {
        "clip_id": "formal_1",
        "track_id": "dub",
        "dub_lane": 0,
        "candidate_id": "candidate_existing",
        "result_id": "result_existing",
        "dubbing_group_id": "group_1",
        "target_subtitle_ids": ["localized_1"],
        "start_ms": 1_000,
        "end_ms": 2_000,
        "source_start_ms": 0,
        "source_end_ms": 1_000,
    }
    unreconciled = project(clips=[existing_clip])
    assert unreconciled.groups[0].stage == "needs_gap_processing"
    assert unreconciled.next_action == "process_gaps"
    assert unreconciled.groups[0].candidate_ids == ["candidate_existing"]

    exhausted_replacement = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=3,
        group_id="group_1",
        candidate_id="candidate_replacement",
        note="新候选重试耗尽，旧片段仍保留在时间线。",
    )
    failed_replacement = project(
        clips=[existing_clip],
        failures=[exhausted_replacement],
    )
    assert failed_replacement.groups[0].stage == "failed"
    assert failed_replacement.groups[0].last_error == exhausted_replacement.note
    assert failed_replacement.groups[0].formal_clip_ids == ["formal_1"]

    moved_clip = {**existing_clip, "start_ms": 1_100, "end_ms": 2_100}
    stale = project(clips=[moved_clip])
    assert stale.groups[0].stage == "needs_gap_processing"

    generation_stage = SimpleNamespace(
        kind="generation",
        parameters={
            "video_localization_dubbing_plan_revision": 3,
            "video_localization_dubbing_group_id": "group_1",
        },
        error_message=None,
    )
    active_workflow = SimpleNamespace(
        workflow_id="workflow_1",
        status="running",
        result_id=None,
        stages=[generation_stage],
    )
    assert project(workflows=[active_workflow]).groups[0].stage == "generating"

    completed_workflow = SimpleNamespace(
        workflow_id="workflow_1",
        status="success",
        result_id="candidate_1",
        stages=[generation_stage],
    )
    completed_pending_closeout = project(workflows=[completed_workflow])
    assert completed_pending_closeout.groups[0].stage == "needs_gap_processing"
    assert completed_pending_closeout.next_action == "process_gaps"
    assert completed_pending_closeout.groups[0].candidate_ids == ["candidate_1"]

    frozen = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=3,
        group_id="group_1",
        candidate_id="candidate_1",
    )
    assert project(inputs=[frozen]).groups[0].stage == "needs_gap_processing"

    failed = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=3,
        group_id="group_1",
        candidate_id="candidate_1",
        overall_status="failed",
    )
    failed_report = project(inputs=[frozen], reports=[failed])
    assert failed_report.groups[0].stage == "needs_timeline_work"
    assert failed_report.groups[0].recommended_action == "place_candidate"

    audio_evidence = SimpleNamespace(
        gap_evidence=[
            DubbingAudioGapEvidence(
                gap_id="gap_head",
                kind="leading",
                start_ms=0,
                end_ms=50,
                duration_ms=50,
                evidence_sources=["waveform", "energy"],
                evidence_ids=["waveform:gap_head", "energy:gap_head"],
                boundary_confidence="clear",
                edit_decision="retain",
                retained_duration_ms=50,
                decision_reason="保留自然起音前气口",
                safe_edit_boundary=True,
            )
        ],
        aligned_words=[
            DubbingCandidateAlignedWord(
                word_id="candidate_word_0001",
                text="好",
                start_ms=50,
                end_ms=900,
            )
        ],
        speaking_rate_ratio=1.1,
        content_speed_exception_reason=None,
        content_speed_exception_evidence_ids=[],
    )
    passed = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=3,
        group_id="group_1",
        candidate_id="candidate_1",
        overall_status="passed",
        evidence_fingerprint="c" * 64,
        audio_evidence=audio_evidence,
    )
    waiting_for_timeline = project(inputs=[frozen], reports=[passed])
    assert waiting_for_timeline.groups[0].stage == "needs_timeline_work"

    clip = {
        "clip_id": "clip_1",
        "track_id": "dub",
        "dub_lane": 0,
        "dubbing_group_id": "group_1",
        "candidate_id": "candidate_1",
        "target_subtitle_ids": ["localized_1"],
        "start_ms": 0,
        "end_ms": 1_000,
        "source_start_ms": 0,
        "source_end_ms": 1_000,
    }
    accepted_without_extra_label = project(
        inputs=[frozen], reports=[passed], clips=[clip]
    )
    assert accepted_without_extra_label.groups[0].stage == "accepted"
    assert accepted_without_extra_label.groups[0].recommended_action == "complete"

    old_failed_workflow = SimpleNamespace(workflow_id='old-failure', status='failed',
        result_id='old-result', stages=[generation_stage])
    # A later ordinary handoff has no managed-plan fields, but the adopted
    # candidate and its result identity already have current quality evidence.
    later_manual_workflow = SimpleNamespace(workflow_id='manual-success', status='success',
        result_id='candidate_1', stages=[])
    for ordered, expected_stage in [
        ([old_failed_workflow, later_manual_workflow], 'accepted'),
        ([later_manual_workflow, old_failed_workflow], 'failed'),
    ]:
        restored = project(workflows=ordered, inputs=[frozen], reports=[passed], clips=[clip])
        assert restored.groups[0].stage == expected_stage

    cropped_final_word = {
        **clip,
        "end_ms": 800,
        "source_end_ms": 800,
    }
    stale_crop = project(
        inputs=[frozen], reports=[passed], clips=[cropped_final_word]
    )
    assert stale_crop.groups[0].stage == "needs_timeline_work"

    unapplied_gap_edit = {
        **clip,
        "start_ms": 50,
        "source_start_ms": 50,
    }
    stale_gap_projection = project(
        inputs=[frozen], reports=[passed], clips=[unapplied_gap_edit]
    )
    assert stale_gap_projection.groups[0].stage == "needs_timeline_work"

    legacy_clip = {**clip, "dubbing_group_id": None}
    accepted_legacy_clip = project(
        inputs=[frozen], reports=[passed], clips=[legacy_clip]
    )
    assert accepted_legacy_clip.groups[0].stage == "accepted"
    assert accepted_legacy_clip.groups[0].formal_clip_ids == ["clip_1"]

    legacy_review = project(
        inputs=[frozen],
        reports=[failed],
        clips=[clip],
    )
    assert legacy_review.groups[0].stage == "needs_timeline_work"
    assert legacy_review.deferred_group_count == 0
    assert legacy_review.next_action == "place_candidate"
    assert accepted_without_extra_label.next_action == "complete"

    advisory_failure = project(inputs=[frozen], reports=[failed], clips=[clip])
    assert advisory_failure.groups[0].stage == "needs_timeline_work"
    assert advisory_failure.groups[0].recommended_action == "place_candidate"

    edited_clip = {
        **clip,
        "timeline_edit_gate": {
            "schema_version": "dubbing-timeline-edit-gate-v1",
            "source_revision": source_revision,
            "plan_revision": 3,
            "candidate_id": "candidate_1",
            "cqc_report_fingerprint": "c" * 64,
            "candidate_clip_projection_fingerprint": (
                candidate_clip_projection_fingerprint([clip])
            ),
            "status": "passed",
            "actual_speech_start_delta_ms": 0,
            "actual_speech_end_delta_ms": 0,
            "speaking_rate_ratio": 1.1,
            "content_speed_exception_applied": False,
            "alignment_word_ids": ["candidate_word_0001"],
            "gap_decisions": [
                {
                    key: value
                    for key, value in item.model_dump(mode="json").items()
                    if key not in {"semantic_role", "semantic_pause_scale"}
                }
                for item in audio_evidence.gap_evidence
            ],
        },
    }
    accepted = project(inputs=[frozen], reports=[passed], clips=[edited_clip])
    assert accepted.groups[0].stage == "accepted"
    assert accepted.status == "completed"
    assert accepted.next_action == "complete"

    # Candidate history is retained for evidence.  A rejected predecessor
    # cannot reopen a group once a later candidate is fully adopted.
    current_clip = {**edited_clip, "candidate_id": "candidate_task-current"}
    current_projection = candidate_clip_projection_fingerprint([current_clip])
    current_clip["timeline_edit_gate"] = {
        **current_clip["timeline_edit_gate"],
        "candidate_clip_projection_fingerprint": current_projection,
    }
    current_report = SimpleNamespace(
        **{
            **vars(passed),
            "candidate_id": "candidate_task-current",
            "semantic_boundary_audit": SimpleNamespace(
                status="accepted",
                candidate_clip_projection_fingerprint=current_projection,
            ),
        }
    )
    rejected_predecessor = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=3,
        group_id="group_1",
        candidate_id="candidate_task-rejected",
        overall_status="failed",
        semantic_boundary_audit=SimpleNamespace(status="recovery_required"),
    )
    historical_recovery = project(
        reports=[rejected_predecessor, current_report],
        clips=[current_clip],
        workflows=[
            SimpleNamespace(
                workflow_id="workflow-rejected",
                status="cancelled",
                generation_task_id="task-rejected",
                result_id="result-rejected",
                stages=[generation_stage],
            )
        ],
    )
    assert historical_recovery.groups[0].stage == "accepted"
    assert historical_recovery.groups[0].passed_candidate_id == "candidate_task-current"

    # A fresh non-cancelled replacement remains actionable. This is how an
    # explicit regenerate_existing run can hand off its newer candidate.
    replacement_pending = SimpleNamespace(
        **{
            **vars(current_report),
            "candidate_id": "candidate_task-replacement",
            "semantic_boundary_audit": SimpleNamespace(status="pending_agent"),
        }
    )
    explicit_replacement = project(
        reports=[current_report, replacement_pending],
        clips=[current_clip],
        workflows=[
            SimpleNamespace(
                workflow_id="workflow-replacement",
                status="success",
                generation_task_id="task-replacement",
                result_id="result-replacement",
                stages=[generation_stage],
            )
        ],
    )
    assert explicit_replacement.groups[0].stage == "needs_semantic_review"
    assert explicit_replacement.next_action == "review_semantic_boundaries"

    active_semantic_handoff = project(
        reports=[current_report, replacement_pending],
        clips=[current_clip],
        workflows=[
            SimpleNamespace(
                workflow_id="workflow-replacement",
                status="running",
                generation_task_id="task-replacement",
                result_id="result-replacement",
                stages=[generation_stage],
            )
        ],
    )
    assert active_semantic_handoff.groups[0].stage == "needs_semantic_review"
    assert active_semantic_handoff.next_action == "review_semantic_boundaries"

    # Before the first audit is produced, a completed generation must still
    # hand its saved result to the finisher. A running or unknown result waits.
    for generation_status, result_id, expected_stage in [
        ("success", "result-replacement", "needs_gap_processing"),
        ("running", "result-replacement", "generating"),
        ("success", None, "generating"),
    ]:
        pending_closeout = project(
            reports=[current_report], clips=[current_clip],
            workflows=[SimpleNamespace(
                workflow_id="workflow-replacement", status="running",
                generation_task_id="task-replacement", result_id=result_id,
                stages=[SimpleNamespace(**{**vars(generation_stage), "status": generation_status})],
            )],
        )
        assert pending_closeout.groups[0].stage == expected_stage
        assert pending_closeout.next_action == (
            "process_gaps" if expected_stage == "needs_gap_processing" else "wait_for_generation"
        )

    replacement_failed = project(
        reports=[current_report],
        clips=[current_clip],
        workflows=[
            SimpleNamespace(
                workflow_id="workflow-replacement",
                status="failed",
                generation_task_id="task-replacement",
                result_id="result-replacement",
                stages=[generation_stage],
            )
        ],
    )
    assert replacement_failed.groups[0].stage == "failed"

    blocking_report = SimpleNamespace(
        **{
            **vars(passed),
            "overall_status": "failed",
        }
    )
    blocked_even_with_gate = project(
        inputs=[frozen],
        reports=[blocking_report],
        clips=[edited_clip],
    )
    assert blocked_even_with_gate.groups[0].stage == "needs_timeline_work"
    assert blocked_even_with_gate.groups[0].recommended_action == "place_candidate"

    deferred_after_edit = project(
        inputs=[frozen],
        reports=[passed],
        clips=[edited_clip],
    )
    assert deferred_after_edit.groups[0].stage == "accepted"
    assert deferred_after_edit.deferred_group_count == 0
    assert deferred_after_edit.next_action == "complete"

    resolved_review = project(
        inputs=[frozen],
        reports=[passed],
        clips=[edited_clip],
    )
    assert resolved_review.groups[0].stage == "accepted"

    split_raw_clips = [
        {
            **clip,
            "end_ms": 500,
            "source_end_ms": 500,
        },
        {
            **clip,
            "clip_id": "clip_2",
            "start_ms": 500,
            "end_ms": 1_000,
            "source_start_ms": 500,
            "source_end_ms": 1_000,
        },
    ]
    split_projection = candidate_clip_projection_fingerprint(split_raw_clips)
    split_clips = [
        {
            **item,
            "timeline_edit_gate": {
                **edited_clip["timeline_edit_gate"],
                "candidate_clip_projection_fingerprint": split_projection,
            },
        }
        for item in split_raw_clips
    ]
    split_through_word = project(inputs=[frozen], reports=[passed], clips=split_clips)
    assert split_through_word.groups[0].stage == "needs_timeline_work"
    assert split_through_word.groups[0].formal_clip_ids == []

    completed = project(inputs=[frozen], reports=[passed], clips=[edited_clip])
    assert completed.status == "completed"
    assert completed.next_action == "complete"


def test_production_run_continues_after_one_terminal_group_failure():
    source_revision = "a" * 64
    plan = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=4,
        groups=[
            SimpleNamespace(group_id="group_1", subtitle_ids=["localized_1"]),
            SimpleNamespace(group_id="group_2", subtitle_ids=["localized_2"]),
        ],
    )
    failure = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=4,
        group_id="group_1",
        candidate_id=None,
        note="有界恢复策略已耗尽，当前组没有可安全落位的完整候选。",
    )

    running = build_production_run_snapshot(
        current_source_revision=source_revision,
        active_plan=plan,
        workflows=[],
        candidate_inputs=[],
        candidate_reports=[],
        group_failures=[failure],
        timeline_clips=[],
    )

    assert running.groups[0].stage == "failed"
    assert running.groups[0].last_error == failure.note
    assert running.failed_group_count == 1
    assert running.next_group_id == "group_2"
    assert running.next_action == "generate_candidate"

    second_failure = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=4,
        group_id="group_2",
        candidate_id=None,
        note="第二组也已耗尽。",
    )
    terminal = build_production_run_snapshot(
        current_source_revision=source_revision,
        active_plan=plan,
        workflows=[],
        candidate_inputs=[],
        candidate_reports=[],
        group_failures=[failure, second_failure],
        timeline_clips=[],
    )

    assert terminal.status == "completed_with_failures"
    assert terminal.failed_group_count == 2
    assert terminal.next_action == "complete"


def test_rendered_gap_duration_uses_source_margins_and_timeline_spacing():
    gap = {"start_ms": 1_000, "end_ms": 2_000}
    clips = [
        {
            "source_start_ms": 0,
            "source_end_ms": 1_200,
            "start_ms": 10_000,
            "end_ms": 11_200,
        },
        {
            "source_start_ms": 1_800,
            "source_end_ms": 3_000,
            "start_ms": 11_350,
            "end_ms": 12_550,
        },
    ]

    assert rendered_gap_duration_ms(gap, clips) == 550


def test_gap_evidence_reconciles_safe_outer_padding_with_final_projection():
    gaps = [
        DubbingAudioGapEvidence(
            gap_id="gap_leading_0_130",
            kind="leading",
            start_ms=0,
            end_ms=130,
            duration_ms=130,
            evidence_sources=["waveform", "energy"],
            evidence_ids=["waveform:head", "energy:head"],
            boundary_confidence="clear",
            edit_decision="remove",
            retained_duration_ms=0,
            decision_reason="删除候选首部空白。",
            safe_edit_boundary=True,
            review_evidence_ids=["dubbing-gap-adjudication-v1"],
            semantic_role="continuous_phrase",
        ),
        DubbingAudioGapEvidence(
            gap_id="gap_trailing_1480_1605",
            kind="trailing",
            start_ms=1_480,
            end_ms=1_605,
            duration_ms=125,
            evidence_sources=["waveform", "energy"],
            evidence_ids=["waveform:tail", "energy:tail"],
            boundary_confidence="clear",
            edit_decision="remove",
            retained_duration_ms=0,
            decision_reason="删除候选尾部空白。",
            safe_edit_boundary=True,
            review_evidence_ids=["dubbing-gap-adjudication-v1"],
            semantic_role="continuous_phrase",
        ),
    ]
    clips = [
        {
            "clip_id": "clip_1",
            "candidate_id": "candidate_1",
            "start_ms": 26_959,
            "end_ms": 28_343,
            "source_start_ms": 220,
            "source_end_ms": 1_604,
        }
    ]

    reconciled = reconcile_gap_evidence_with_projection(gaps, clips)

    assert reconciled[0].edit_decision == "remove"
    assert reconciled[0].retained_duration_ms == 0
    assert reconciled[1].edit_decision == "shorten"
    assert reconciled[1].retained_duration_ms == 124
    assert reconciled[1].review_evidence_ids[-1].startswith(
        "dubbing-timeline-projection-v1:"
    )


def test_production_run_keeps_cqc_findings_advisory_for_timeline_placement():
    source_revision = "a" * 64
    plan = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=3,
        groups=[SimpleNamespace(group_id="group_1", subtitle_ids=["localized_1"])],
    )
    frozen = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=3,
        group_id="group_1",
        candidate_id="candidate_1",
    )
    report = SimpleNamespace(
        source_revision=source_revision,
        plan_revision=3,
        group_id="group_1",
        candidate_id="candidate_1",
        overall_status="needs_review",
    )

    result = build_production_run_snapshot(
        current_source_revision=source_revision,
        active_plan=plan,
        workflows=[],
        candidate_inputs=[frozen],
        candidate_reports=[report],
        group_failures=[],
        timeline_clips=[],
    )

    assert result.groups[0].stage == "needs_timeline_work"
    assert result.next_action == "place_candidate"


def test_timeline_edit_gate_fingerprint_covers_split_alignment_identity():
    clip = {
        "clip_id": "clip_1",
        "candidate_id": "candidate_1",
        "start_ms": 1_000,
        "end_ms": 2_000,
        "source_start_ms": 0,
        "source_end_ms": 1_000,
        "dubbing_slice_index": 0,
        "dubbing_slice_count": 2,
        "dubbing_alignment_word_ids": ["word_1"],
    }
    changed = {
        **clip,
        "dubbing_alignment_word_ids": ["word_2"],
    }

    assert candidate_clip_projection_fingerprint([clip]) != (
        candidate_clip_projection_fingerprint([changed])
    )


def _unit(
    index: int,
    *,
    speaker: str = "speaker_a",
    scene: str | None = "scene_a",
    speech_policy: str = "translate",
) -> DubbingSemanticUnit:
    start = (index - 1) * 1_000
    return DubbingSemanticUnit(
        unit_id=f"unit_{index}",
        subtitle_ids=[f"localized_{index}"],
        source_cue_ids=[f"cue_{index}"],
        speaker_id=speaker,
        scene_id=scene,
        start_ms=start,
        end_ms=start + 800,
        source_anchor_start_ms=start + 100,
        source_anchor_end_ms=start + 700,
        display_text=f"第{index}句",
        spoken_text=f"第{index}句",
        speech_policy=speech_policy,
    )


def _boundary(
    left: int,
    *,
    same_speaker: bool = True,
    same_scene: bool | None = True,
    speech_between: bool | None = False,
    pause: str = "natural_pause",
    semantic: str = "continuous",
    hard: bool = False,
    no_break: bool = False,
) -> DubbingBoundaryEvidence:
    return DubbingBoundaryEvidence(
        boundary_id=f"unit_{left}:unit_{left + 1}",
        left_unit_id=f"unit_{left}",
        right_unit_id=f"unit_{left + 1}",
        gap_ms=200,
        same_speaker=same_speaker,
        same_scene=same_scene,
        speech_between=speech_between,
        pause_classification=pause,
        low_energy_confidence="high",
        semantic_relation=semantic,
        hard_boundary=hard,
        no_break_with_next=no_break,
    )


def test_plan_splits_long_silent_demo_instead_of_bridging_adjacent_speech():
    payload = DubbingGenerationPlanInput(
        source_revision=SOURCE_REVISION,
        semantic_units=[_unit(1), _unit(2), _unit(3)],
        boundaries=[
            _boundary(1),
            _boundary(2, pause="long_silence", semantic="break"),
        ],
    )

    plan = build_generation_plan(payload)

    assert [island.unit_ids for island in plan.speech_islands] == [
        ["unit_1", "unit_2"],
        ["unit_3"],
    ]
    assert [group.unit_ids for group in plan.groups] == [
        ["unit_1", "unit_2"],
        ["unit_3"],
    ]


def test_plan_rebase_keeps_unchanged_units_when_another_unit_identity_changes():
    previous = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision="a" * 64,
            semantic_units=[_unit(1), _unit(2)],
            boundaries=[_boundary(1)],
        )
    )
    changed_second = _unit(2, speech_policy="needs_review").model_copy(
        update={"source_cue_ids": ["cue_2_changed"]}
    )
    snapshot = DubbingProductionSnapshot(
        source_revision="b" * 64,
        semantic_units=[
            _unit(1, speech_policy="needs_review"),
            changed_second,
        ],
        boundaries=[_boundary(1)],
    )

    rebased = rebase_compatible_generation_plan(snapshot, previous)

    assert rebased is not None
    assert rebased.semantic_units[0].speech_policy == "translate"
    assert rebased.semantic_units[1].speech_policy == "needs_review"
    assert [group.unit_ids for group in rebased.groups] == [["unit_1"]]


def test_plan_rebase_keeps_reviewed_policy_when_one_unit_is_repartitioned():
    previous_unit = _unit(1).model_copy(
        update={
            "unit_id": "unit_before_edit",
            "subtitle_ids": ["localized_1", "localized_2"],
            "source_cue_ids": ["cue_1", "cue_2"],
            "source_word_ids": ["word_1", "word_2"],
        }
    )
    previous = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision="a" * 64,
            semantic_units=[previous_unit],
            boundaries=[],
        )
    )
    current_units = [
        _unit(1, speech_policy="needs_review").model_copy(
            update={
                "unit_id": "unit_after_edit_1",
                "source_word_ids": ["word_1"],
            }
        ),
        _unit(2, speech_policy="needs_review").model_copy(
            update={
                "unit_id": "unit_after_edit_2",
                "source_word_ids": ["word_2"],
            }
        ),
    ]
    boundary = _boundary(1).model_copy(
        update={
            "boundary_id": "unit_after_edit_1:unit_after_edit_2",
            "left_unit_id": "unit_after_edit_1",
            "right_unit_id": "unit_after_edit_2",
        }
    )
    snapshot = DubbingProductionSnapshot(
        source_revision="b" * 64,
        semantic_units=current_units,
        boundaries=[boundary],
    )

    rebased = rebase_compatible_generation_plan(snapshot, previous)

    assert rebased is not None
    assert [unit.speech_policy for unit in rebased.semantic_units] == [
        "translate",
        "translate",
    ]
    assert {
        subtitle_id
        for group in rebased.groups
        for subtitle_id in group.subtitle_ids
    } == {"localized_1", "localized_2"}


def test_plan_rebase_extends_preserved_scene_end_past_moved_source_anchor():
    previous_unit = _unit(1).model_copy(update={"scene_end_ms": 700})
    previous = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision="a" * 64,
            semantic_units=[previous_unit],
            boundaries=[],
        )
    )
    moved_unit = _unit(1, speech_policy="needs_review").model_copy(
        update={
            "unit_id": "unit_after_timing_edit",
            "source_anchor_start_ms": 200,
            "source_anchor_end_ms": 850,
        }
    )
    snapshot = DubbingProductionSnapshot(
        source_revision="b" * 64,
        semantic_units=[moved_unit],
        boundaries=[],
    )

    rebased = rebase_compatible_generation_plan(snapshot, previous)

    assert rebased is not None
    assert rebased.semantic_units[0].speech_policy == "translate"
    assert rebased.semantic_units[0].scene_end_ms == 850


def test_refresh_plan_timings_preserves_reviewed_grouping_and_decisions():
    source_revision = "a" * 64
    previous = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=source_revision,
            semantic_units=[_unit(1), _unit(2), _unit(3)],
            boundaries=[_boundary(1), _boundary(2)],
        )
    )
    previous = previous.model_copy(
        update={
            "plan_revision": 7,
            "semantic_units": [
                unit.model_copy(
                    update={"speech_policy": "needs_review"}
                )
                if unit.unit_id == "unit_2"
                else unit
                for unit in previous.semantic_units
            ],
        }
    )
    current_units = [
        unit.model_copy(
            update={
                "start_ms": unit.start_ms + 125,
                "end_ms": unit.end_ms + 175,
                "source_anchor_start_ms": unit.source_anchor_start_ms + 125,
                "source_anchor_end_ms": unit.source_anchor_end_ms + 175,
            }
        )
        for unit in previous.semantic_units
    ]
    snapshot = DubbingProductionSnapshot(
        source_revision=source_revision,
        semantic_units=current_units,
        boundaries=[_boundary(1), _boundary(2)],
    )

    refreshed = refresh_generation_plan_timings(snapshot, previous)

    assert refreshed.plan_revision == 7
    assert [group.group_id for group in refreshed.groups] == [
        group.group_id for group in previous.groups
    ]
    assert [group.unit_ids for group in refreshed.groups] == [
        group.unit_ids for group in previous.groups
    ]
    assert refreshed.semantic_units[1].speech_policy == "needs_review"
    assert refreshed.semantic_units[0].start_ms == 125
    assert refreshed.groups[0].target_start_ms == 125


def test_refresh_plan_timings_rejects_changed_localized_text():
    source_revision = "a" * 64
    previous = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=source_revision,
            semantic_units=[_unit(1)],
            boundaries=[],
        )
    )
    snapshot = DubbingProductionSnapshot(
        source_revision=source_revision,
        semantic_units=[_unit(1).model_copy(update={"spoken_text": "改过的内容"})],
        boundaries=[],
    )

    with pytest.raises(ValueError, match="changed beyond timing"):
        refresh_generation_plan_timings(snapshot, previous)


def test_plan_conservatively_splits_unknown_boundary_and_requires_review():
    payload = DubbingGenerationPlanInput(
        source_revision=SOURCE_REVISION,
        semantic_units=[_unit(1), _unit(2)],
        boundaries=[
            _boundary(
                1,
                same_scene=None,
                speech_between=None,
                pause="unknown",
                semantic="unknown",
            )
        ],
    )

    plan = build_generation_plan(payload)

    assert [group.unit_ids for group in plan.groups] == [
        ["unit_1"],
        ["unit_2"],
    ]
    assert plan.status == "warning"
    assert {finding.code for finding in plan.findings} == {"AMBIGUOUS_SPEECH_BOUNDARY"}


def test_plan_uses_editable_speech_load_instead_of_fixed_unit_count():
    ordinary = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=SOURCE_REVISION,
            semantic_units=[_unit(1), _unit(2), _unit(3)],
            boundaries=[_boundary(1), _boundary(2)],
        )
    )
    no_break = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=SOURCE_REVISION,
            semantic_units=[_unit(1), _unit(2), _unit(3)],
            boundaries=[
                _boundary(1, no_break=True),
                _boundary(2, no_break=True),
            ],
        )
    )

    assert [group.unit_ids for group in ordinary.groups] == [["unit_1", "unit_2", "unit_3"]]
    assert [group.unit_ids for group in no_break.groups] == [["unit_1", "unit_2", "unit_3"]]


def test_plan_treats_estimated_no_break_capacity_as_warning_until_real_candidate_exists():
    """Estimated text pressure may guide grouping, but cannot require deleting copy.

    Actual candidate C1-C6 capacity evidence still owns a main-lane block; at
    planning time this fixture has only an estimate and no safe split point.
    """
    dense = _unit(1).model_copy(
        update={
            "end_ms": 200,
            "source_anchor_end_ms": 180,
            "spoken_text": "甲" * 30,
        }
    )

    plan = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=SOURCE_REVISION,
            semantic_units=[dense],
            boundaries=[],
            policy={"maximum_text_pressure": 1.0},
        )
    )

    findings = {finding.code: finding for finding in plan.findings}
    assert plan.status == "warning"
    assert findings["GENERATION_GROUP_CAPACITY_ESTIMATE"].severity == "warning"
    assert "GENERATION_GROUP_NATURAL_BREAK_REQUIRED" not in findings


def test_plan_splits_excessive_speech_load_but_not_by_subtitle_count():
    long_left = _unit(1).model_copy(update={"spoken_text": "甲" * 100})
    long_right = _unit(2).model_copy(update={"spoken_text": "乙" * 100})
    multi_left = _unit(3).model_copy(update={"subtitle_ids": ["localized_3a", "localized_3b"]})
    multi_right = _unit(4).model_copy(update={"subtitle_ids": ["localized_4a", "localized_4b"]})

    text_limited = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=SOURCE_REVISION,
            semantic_units=[long_left, long_right],
            boundaries=[_boundary(1)],
        )
    )
    subtitle_limited = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=SOURCE_REVISION,
            semantic_units=[multi_left, multi_right],
            boundaries=[
                DubbingBoundaryEvidence(
                    boundary_id="unit_3:unit_4",
                    left_unit_id="unit_3",
                    right_unit_id="unit_4",
                    gap_ms=200,
                    same_speaker=True,
                    same_scene=True,
                    speech_between=False,
                    pause_classification="natural_pause",
                    low_energy_confidence="high",
                    semantic_relation="continuous",
                )
            ],
        )
    )

    assert [group.unit_ids for group in text_limited.groups] == [
        ["unit_1"],
        ["unit_2"],
    ]
    assert [group.unit_ids for group in subtitle_limited.groups] == [["unit_3", "unit_4"]]


def test_plan_treats_complete_sentence_end_as_safe_generation_boundary():
    left = _unit(1).model_copy(
        update={
            "start_ms": 0,
            "end_ms": 8_000,
            "source_anchor_start_ms": 100,
            "source_anchor_end_ms": 7_900,
            "spoken_text": "甲" * 40 + "。",
        }
    )
    right = _unit(2).model_copy(
        update={
            "start_ms": 8_000,
            "end_ms": 16_000,
            "source_anchor_start_ms": 8_100,
            "source_anchor_end_ms": 15_900,
            "spoken_text": "乙" * 40 + "。",
        }
    )
    boundary = DubbingBoundaryEvidence(
        boundary_id="unit_1:unit_2",
        left_unit_id="unit_1",
        right_unit_id="unit_2",
        gap_ms=0,
        same_speaker=True,
        same_scene=True,
        speech_between=False,
        pause_classification="continuous",
        low_energy_confidence="none",
        semantic_relation="continuous",
    )

    plan = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=SOURCE_REVISION,
            semantic_units=[left, right],
            boundaries=[boundary],
        )
    )

    assert plan.status == "passed"
    assert [group.unit_ids for group in plan.groups] == [["unit_1"], ["unit_2"]]


def test_plan_uses_reviewed_scene_end_as_available_speech_window():
    dense = _unit(1).model_copy(
        update={
            "end_ms": 500,
            "source_anchor_end_ms": 400,
            "scene_end_ms": 2_000,
            "spoken_text": "这是一句需要利用后续安全画面空间的配音。",
        }
    )

    plan = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=SOURCE_REVISION,
            semantic_units=[dense],
            boundaries=[],
            policy={"maximum_text_pressure": 2.0},
        )
    )

    assert plan.status == "passed"
    assert plan.groups[0].target_end_ms == 2_000


def test_plan_estimates_latin_product_name_by_words_not_letters():
    product_name = _unit(1).model_copy(
        update={
            "end_ms": 600,
            "source_anchor_end_ms": 500,
            "spoken_text": "Seedance",
        }
    )

    plan = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=SOURCE_REVISION,
            semantic_units=[product_name],
            boundaries=[],
            policy={"maximum_text_pressure": 2.0},
        )
    )

    assert plan.status == "passed"


def test_plan_blocks_a_single_unit_that_still_contains_mixed_speakers():
    mixed = _unit(1).model_copy(update={"speaker_id": "mixed"})

    plan = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=SOURCE_REVISION,
            semantic_units=[mixed],
            boundaries=[],
        )
    )

    assert plan.status == "failed"
    assert "GENERATION_GROUP_CROSSES_SPEAKERS" in {finding.code for finding in plan.findings}


def test_plan_blocks_unnatural_leading_one_ten_in_spoken_copy():
    malformed = _unit(1).model_copy(update={"spoken_text": "估值一十亿美元，增长百分之一十。"})

    plan = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=SOURCE_REVISION,
            semantic_units=[malformed],
            boundaries=[],
        )
    )

    assert plan.status == "failed"
    assert "GENERATION_GROUP_UNNATURAL_CARDINAL" in {finding.code for finding in plan.findings}


def test_planned_candidate_apply_keeps_one_formal_clip_for_the_group(
    tmp_path: Path,
):
    first_audio = tmp_path / "first.wav"
    second_audio = tmp_path / "second.wav"
    sf.write(first_audio, np.full(16_000, 0.1, dtype=np.float32), 16_000)
    sf.write(
        second_audio,
        np.concatenate(
            [
                np.zeros(3_200, dtype=np.float32),
                np.full(9_600, 0.2, dtype=np.float32),
                np.zeros(3_200, dtype=np.float32),
            ]
        ),
        16_000,
    )
    base = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_1",
                speaker_id="speaker_1",
                start_ms=1_000,
                end_ms=2_500,
                audio_route="clone_from_source",
                review_status="ready",
            )
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=1_000,
                end_ms=2_500,
                text="这句话可以使用",
                tts_text="这句话可以使用",
                source_cue_ids=["cue_1"],
            )
        ],
    )
    snapshot = build_project_snapshot(base)
    plan = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=snapshot.source_revision,
            semantic_units=snapshot.semantic_units,
            boundaries=snapshot.boundaries,
        )
    )
    group = plan.groups[0]

    def reviewed_candidate(candidate_id: str, audio_path: Path):
        audio_sha = media_assets.file_sha256(audio_path)
        frozen = _candidate(
            source_revision=snapshot.source_revision,
            plan_revision=plan.plan_revision,
            group_id=group.group_id,
            candidate_id=candidate_id,
            artifact_id=candidate_id,
            audio_sha256=audio_sha,
            expected_spoken_text=group.spoken_text,
            candidate_transcript=group.spoken_text,
            target_start_ms=group.target_start_ms,
            target_end_ms=group.target_end_ms,
            placement_start_ms=group.target_start_ms,
            placement_end_ms=group.target_start_ms + 1_000,
            audio=DubbingAutomaticAudioEvidence(
                duration_ms=1_000,
                peak_dbfs=-1.0,
                clipping_ratio=0,
                leading_silence_ms=200,
                trailing_silence_ms=200,
                expected_pause_baseline_ms=200,
                max_leading_silence_ms=250,
                max_trailing_silence_ms=250,
            ),
        )
        return frozen, evaluate_candidate(frozen)

    first_input, first_report = reviewed_candidate("candidate_first", first_audio)
    second_input, second_report = reviewed_candidate("candidate_second", second_audio)
    second_report = second_report.model_copy(
        update={"overall_status": "needs_review", "subjective_status": "not_reviewed"}
    )
    draft = base.model_copy(
        update={
            "dubbing_production": base.dubbing_production.model_copy(
                update={
                    "enforcement_mode": "planned",
                    "active_plan": plan,
                    "candidate_inputs": [first_input, second_input],
                    "candidate_reports": [first_report, second_report],
                }
            ),
            "generated_candidates": [
                {
                    "candidate_id": "candidate_first",
                    "cue_id": "cue_1",
                    "subtitle_id": "group_alias",
                    "audio_path": str(first_audio),
                    "duration_ms": 1_000,
                    "status": "success",
                    "cqc_status": "passed",
                },
                {
                    "candidate_id": "candidate_second",
                    "cue_id": "cue_1",
                    "subtitle_id": "group_alias",
                    "audio_path": str(second_audio),
                    # The task callback may round duration below the CQC
                    # measurement. Placement must preserve the measured file.
                    "duration_ms": 900,
                    "status": "success",
                    "cqc_status": "needs_review",
                },
            ],
            "timeline_clips": [
                {
                    "clip_id": "first_take",
                    "candidate_id": "candidate_first",
                    "track_id": "dub",
                    "subtitle_id": "group_alias",
                    "target_subtitle_ids": ["localized_1"],
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "dub_lane": 0,
                },
                {
                    "clip_id": "second_take",
                    "candidate_id": "candidate_second",
                    "track_id": "dub",
                    "subtitle_id": "group_alias",
                    "target_subtitle_ids": ["localized_1"],
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "dub_lane": 1,
                    "manual_history_copy": True,
                },
                {
                    "clip_id": "stale_split_part",
                    "candidate_id": "candidate_first",
                    "track_id": "dub",
                    "subtitle_id": "localized_legacy_part",
                    "target_subtitle_ids": [],
                    "dubbing_group_id": group.group_id,
                    "dubbing_slice_index": 2,
                    "dubbing_slice_count": 2,
                    "dubbing_alignment_word_ids": ["legacy_word"],
                    "start_ms": 1_500,
                    "end_ms": 2_000,
                    "dub_lane": 0,
                },
                {
                    "clip_id": "accepted_neighbor",
                    "candidate_id": "candidate_neighbor",
                    "track_id": "dub",
                    "subtitle_id": "localized_neighbor",
                    "target_subtitle_ids": ["localized_neighbor"],
                    "dubbing_group_id": "dubbing_group_neighbor",
                    "target_start_ms": 2_000,
                    "target_end_ms": 2_900,
                    "start_ms": 1_700,
                    "end_ms": 2_600,
                    "source_start_ms": 0,
                    "source_end_ms": 900,
                    "alignment_lead_ms": 100,
                    "alignment_trail_ms": 80,
                    "dub_lane": 0,
                    "timeline_edit_gate": {"status": "passed"},
                },
                {
                    "clip_id": "renumbered_old_plan_clip",
                    "candidate_id": "candidate_old_plan",
                    "track_id": "dub",
                    "subtitle_id": "localized_old",
                    "target_subtitle_ids": ["localized_old"],
                    # A refreshed plan may reuse a positional group id for a
                    # different subtitle range.  Explicit target ownership
                    # must win so adopting the new group cannot delete this.
                    "dubbing_group_id": group.group_id,
                    "start_ms": 3_000,
                    "end_ms": 3_800,
                    "source_start_ms": 0,
                    "source_end_ms": 800,
                    "dub_lane": 0,
                },
            ],
        }
    )

    accepted_neighbor_before = next(
        dict(item)
        for item in draft.timeline_clips
        if item["clip_id"] == "accepted_neighbor"
    )

    applied = tts_pipeline.with_applied_generated_candidate(
        draft,
        "candidate_second",
    )

    dub_clips = [item for item in applied.timeline_clips if item["track_id"] == "dub"]
    assert len(dub_clips) == 5
    selected_clip = next(
        item for item in dub_clips if item["candidate_id"] == "candidate_second"
    )
    assert selected_clip["target_subtitle_ids"] == ["localized_1"]
    assert selected_clip["dubbing_group_id"] == group.group_id
    assert "dubbing_slice_count" not in selected_clip
    assert selected_clip["dub_lane"] == 1
    assert selected_clip["source_start_ms"] == 120
    assert selected_clip["source_end_ms"] == 1_000
    assert selected_clip["speech_onset_ms"] == 200
    assert selected_clip["start_ms"] == 920
    assert selected_clip["end_ms"] == 1_800
    assert "cqc_status" not in selected_clip
    assert next(
        item for item in dub_clips if item["clip_id"] == "accepted_neighbor"
    ) == accepted_neighbor_before
    assert any(item["clip_id"] == "first_take" for item in dub_clips)
    assert any(item["clip_id"] == "stale_split_part" for item in dub_clips)
    assert any(
        item["clip_id"] == "renumbered_old_plan_clip"
        for item in dub_clips
    )
    assert applied.generated_candidates == draft.generated_candidates


def test_planned_candidate_apply_allocates_unique_clip_id_when_default_is_taken(
    tmp_path: Path,
):
    audio_path = tmp_path / "candidate.wav"
    sf.write(audio_path, np.full(16_000, 0.1, dtype=np.float32), 16_000)
    base = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_1",
                speaker_id="speaker_1",
                start_ms=1_000,
                end_ms=2_500,
                audio_route="clone_from_source",
                review_status="ready",
            )
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=1_000,
                end_ms=2_500,
                text="这句话可以使用",
                tts_text="这句话可以使用",
                source_cue_ids=["cue_1"],
            )
        ],
    )
    snapshot = build_project_snapshot(base)
    plan = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=snapshot.source_revision,
            semantic_units=snapshot.semantic_units,
            boundaries=snapshot.boundaries,
        )
    )
    group = plan.groups[0]
    frozen = _candidate(
        source_revision=snapshot.source_revision,
        plan_revision=plan.plan_revision,
        group_id=group.group_id,
        candidate_id="candidate_unique",
        artifact_id="candidate_unique",
        audio_sha256=media_assets.file_sha256(audio_path),
        expected_spoken_text=group.spoken_text,
        candidate_transcript=group.spoken_text,
        target_start_ms=group.target_start_ms,
        target_end_ms=group.target_end_ms,
        planned_scene_end_ms=None,
        placement_start_ms=None,
        placement_end_ms=None,
        audio=DubbingAutomaticAudioEvidence(
            duration_ms=1_000,
            peak_dbfs=-1.0,
            clipping_ratio=0,
            leading_silence_ms=50,
            trailing_silence_ms=50,
        ),
    )
    draft = base.model_copy(
        update={
            "dubbing_production": base.dubbing_production.model_copy(
                update={
                    "enforcement_mode": "planned",
                    "active_plan": plan,
                    "candidate_inputs": [frozen],
                    "candidate_reports": [evaluate_candidate(frozen)],
                }
            ),
            "generated_candidates": [
                {
                    "candidate_id": "candidate_unique",
                    "cue_id": "cue_1",
                    "subtitle_id": "localized_1",
                    "audio_path": str(audio_path),
                    "duration_ms": 1_000,
                    "status": "success",
                    "cqc_status": "passed",
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_localized_1",
                    "candidate_id": "candidate_unrelated",
                    "track_id": "dub",
                    "subtitle_id": "unrelated_group",
                    "target_subtitle_ids": ["localized_other"],
                    "start_ms": 5_000,
                    "end_ms": 6_000,
                    "dub_lane": 0,
                }
            ],
        }
    )

    applied = tts_pipeline.with_applied_generated_candidate(
        draft,
        "candidate_unique",
    )

    clip_ids = [item["clip_id"] for item in applied.timeline_clips]
    assert len(clip_ids) == len(set(clip_ids))
    assert "clip_localized_1" in clip_ids
    assert "clip_localized_1_2" in clip_ids


def test_reviewed_replacement_promotes_atomically_and_only_then_removes_old_take(
    monkeypatch,
):
    source_revision = "a" * 64
    group = DubbingGenerationGroup(
        group_id="dubbing_group_0001",
        island_id="island_1",
        unit_ids=["unit_1"],
        subtitle_ids=["localized_1"],
        speaker_id="speaker_1",
        spoken_text="这个结果可以使用",
        target_start_ms=1_000,
        target_end_ms=3_000,
        source_reference_start_ms=1_000,
        source_reference_end_ms=3_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=source_revision,
        plan_revision=1,
        status="passed",
        semantic_units=[],
        speech_islands=[],
        groups=[group],
    )
    base_audio = _candidate().audio
    assert base_audio is not None
    audio = base_audio.model_copy(
        update={
            "aligned_words": [
                DubbingCandidateAlignedWord(
                    word_id="word_1",
                    text="这个结果可以使用",
                    start_ms=50,
                    end_ms=1_920,
                )
            ]
        }
    )
    frozen = _candidate(plan_revision=1, audio=audio).model_copy(
        update={"source_revision": source_revision}
    )
    report = evaluate_candidate(frozen)
    candidate_clip = {
        "clip_id": "clip-new",
        "track_id": "dub",
        "dub_lane": 1,
        "candidate_id": frozen.candidate_id,
        "result_id": frozen.candidate_id,
        "dubbing_group_id": group.group_id,
        "target_subtitle_ids": list(group.subtitle_ids),
        "start_ms": 1_000,
        "end_ms": 3_000,
        "source_start_ms": 0,
        "source_end_ms": 2_000,
    }
    gate = DubbingProductionApplicationService._build_automatic_timeline_gate(
        plan=plan,
        frozen=frozen,
        report=report,
        clips=[candidate_clip],
        gaps=list(audio.gap_evidence),
        status="passed",
    ).model_dump(mode="json")
    candidate_clip["timeline_edit_gate"] = gate
    old_clip = {
        "clip_id": "clip-old",
        "track_id": "dub",
        "dub_lane": 0,
        "candidate_id": "candidate_old",
        "result_id": "candidate_old",
        "dubbing_group_id": group.group_id,
        "target_subtitle_ids": list(group.subtitle_ids),
        "start_ms": 1_000,
        "end_ms": 3_000,
        "source_start_ms": 0,
        "source_end_ms": 2_000,
    }
    draft = VideoLocalizationDraft(
        timeline_clips=[old_clip, candidate_clip],
        generated_candidates=[
            {"candidate_id": "candidate_old", "accepted": True},
            {"candidate_id": frozen.candidate_id, "accepted": False},
        ],
        dubbing_production=DubbingProductionState(
            active_plan=plan,
            candidate_inputs=[frozen],
            candidate_reports=[report],
            group_reviews=[
                DubbingProductionGroupReview(
                    title="待监工确认",
                    note="候选仍在试听轨",
                    reason_code="candidate_supervised_review_pending",
                    group_id=group.group_id,
                    candidate_id=frozen.candidate_id,
                    source_revision=plan.source_revision,
                    plan_revision=plan.plan_revision,
                    created_at="2026-08-31T00:00:00Z",
                )
            ],
        ),
    )
    holder = {"draft": draft}

    def update(_project_id, callback, **_kwargs):
        holder["draft"] = callback(holder["draft"])
        return holder["draft"]

    monkeypatch.setattr(
        dubbing_production_service.project_service,
        "update_video_localization_atomic",
        update,
    )

    DubbingProductionApplicationService._promote_reviewed_candidate(
        "project-1",
        group_id=group.group_id,
        candidate_id=frozen.candidate_id,
    )

    assert [clip["clip_id"] for clip in holder["draft"].timeline_clips] == [
        "clip-new"
    ]
    promoted = holder["draft"].timeline_clips[0]
    assert promoted["dub_lane"] == 0
    assert promoted["timeline_edit_gate"][
        "candidate_clip_projection_fingerprint"
    ] == candidate_clip_projection_fingerprint([dict(promoted)])
    assert holder["draft"].generated_candidates[0]["accepted"] is False
    assert holder["draft"].generated_candidates[1]["accepted"] is True
    assert holder["draft"].dubbing_production.group_reviews == []


@pytest.mark.parametrize("audio_state", ["unchanged", "deleted", "replaced"])
def test_processed_candidate_commit_checks_current_bytes_and_preserves_timeline_ownership(
    monkeypatch, tmp_path, audio_state,
):
    from app.schemas.tts_content import TtsContentEvidence

    audio_file = tmp_path / "candidate.wav"
    audio_file.write_bytes(b"original candidate bytes")
    audio_sha = hashlib.sha256(audio_file.read_bytes()).hexdigest()
    monkeypatch.setattr(
        dubbing_production_service.media_assets, "managed_project_file",
        lambda _project_id, path: audio_file if str(path) == str(audio_file) and audio_file.is_file() else None,
    )
    source_revision = "a" * 64
    group = DubbingGenerationGroup(
        group_id="dubbing_group_0001",
        island_id="island_1",
        unit_ids=["unit_1"],
        subtitle_ids=["localized_1", "localized_2"],
        speaker_id="speaker_1",
        spoken_text="这个结果可以使用",
        target_start_ms=1_000,
        target_end_ms=3_000,
        source_reference_start_ms=1_000,
        source_reference_end_ms=3_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=source_revision,
        plan_revision=1,
        status="passed",
        semantic_units=[],
        speech_islands=[],
        groups=[group],
    )
    candidate = {
        "clip_id": "clip-new",
        "track_id": "dub",
        "dub_lane": 2,
        "candidate_id": "candidate-new",
        "result_id": "result-new",
        "dubbing_group_id": group.group_id,
        "target_subtitle_ids": list(group.subtitle_ids),
        "start_ms": 1_000,
        "end_ms": 3_000,
        "source_start_ms": 0,
        "source_end_ms": 2_000,
        "audio_path": str(audio_file),
        "status": "ready",
        "cqc_status": "passed",
        "timeline_edit_gate": {"status": "passed"},
    }
    reviewed_input = _candidate(
        source_revision=source_revision,
        plan_revision=1,
        group_id=group.group_id,
        candidate_id="candidate-new",
        audio_sha256=audio_sha,
        content_evidence=TtsContentEvidence(audio_sha256=audio_sha, engine_id="qwen3-asr-mlx",
                                           status="complete", transcript="这个结果可以使用"),
    )
    assert reviewed_input.audio is not None
    reviewed_input = reviewed_input.model_copy(
        update={
            "audio": reviewed_input.audio.model_copy(
                update={
                    "aligned_words": [
                        DubbingCandidateAlignedWord(
                            word_id="candidate_word_0001",
                            text="声音",
                            start_ms=50,
                            end_ms=1_920,
                        )
                    ]
                }
            )
        }
    )
    report = build_candidate_gap_processing_report(reviewed_input)
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip-old-left",
                "track_id": "dub",
                "target_subtitle_ids": ["localized_1"],
                "result_id": "old-left",
            },
            {
                "clip_id": "clip-old-right",
                "track_id": "dub",
                "target_subtitle_ids": ["localized_2"],
                "result_id": "old-right",
            },
            {
                "clip_id": "clip-unrelated",
                "track_id": "dub",
                "dub_lane": 0,
                "target_subtitle_ids": ["localized_3"],
                "result_id": "unrelated",
                "start_ms": 3_200,
                "end_ms": 4_000,
                "source_start_ms": 0,
                "source_end_ms": 800,
                "audio_path": "/tmp/unrelated.wav",
                "status": "ready",
            },
            {
                "clip_id": "clip-manual-copy",
                "track_id": "dub",
                "target_subtitle_ids": ["localized_1"],
                "manual_history_copy": True,
                "result_id": "manual-copy",
            },
        ],
        generated_candidates=[
            {
                "candidate_id": "candidate-new",
                "result_id": "result-new",
                "task_id": "task-new",
                "audio_path": "/tmp/new.wav",
            }
        ],
        dubbing_production=DubbingProductionState(
            active_plan=plan,
            candidate_inputs=[reviewed_input],
            candidate_reports=[report],
            group_failures=[
                DubbingProductionGroupFailure(
                    source_revision=source_revision,
                    plan_revision=1,
                    group_id=group.group_id,
                    candidate_id="candidate-new",
                    title="旧失败",
                    note="第一次收尾失败。",
                    reason_code="closeout_failed",
                    attempt_count=1,
                    created_at="2026-09-03T00:00:00",
                )
            ],
        ),
    )
    holder = {"draft": draft}
    if audio_state == "deleted":
        audio_file.unlink()
    elif audio_state == "replaced":
        audio_file.write_bytes(b"replacement bytes")

    def update(_project_id, callback, **_kwargs):
        if audio_state != "unchanged":
            with pytest.raises(AppException) as error:
                callback(holder["draft"])
            assert error.value.code == "VIDEO_LOCALIZATION_DUBBING_COMMIT_AUDIO_CHANGED"
            return holder["draft"]
        holder["draft"] = callback(holder["draft"])
        return holder["draft"]

    monkeypatch.setattr(
        dubbing_production_service.project_service,
        "update_video_localization_atomic",
        update,
    )
    monkeypatch.setattr(
        dubbing_production_service,
        "_audit_candidate_timeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("current-group adoption must not audit the full timeline")
        ),
    )

    DubbingProductionApplicationService._commit_processed_candidate(
        "project-1",
        group_id=group.group_id,
        candidate_id="candidate-new",
        processed_clips=[candidate],
        reviewed_input=reviewed_input,
        report=report,
        expected_target_projection_fingerprint=(
            dubbing_production_service._target_owned_projection_fingerprint(
                draft,
                target_subtitle_ids=list(group.subtitle_ids),
            )
        ),
        supporting_clips=[
            {
                **next(
                    clip
                    for clip in draft.timeline_clips
                    if clip.get("clip_id") == "clip-unrelated"
                ),
                "start_ms": 3_000,
                "end_ms": 3_800,
            }
        ],
        expected_supporting_projection_fingerprint=(
            dubbing_production_service._exact_clip_projection_fingerprint(
                [
                    next(
                        clip
                        for clip in draft.timeline_clips
                        if clip.get("clip_id") == "clip-unrelated"
                    )
                ]
            )
        ),
    )

    if audio_state != "unchanged":
        assert holder["draft"] == draft
        return
    by_id = {clip["clip_id"]: clip for clip in holder["draft"].timeline_clips}
    assert set(by_id) == {"clip-new", "clip-unrelated", "clip-manual-copy"}
    assert by_id["clip-new"]["dub_lane"] == 0
    assert (by_id["clip-unrelated"]["start_ms"], by_id["clip-unrelated"]["end_ms"]) == (
        3_000,
        3_800,
    )
    assert "cqc_status" not in by_id["clip-new"]
    assert "timeline_edit_gate" not in by_id["clip-new"]
    assert holder["draft"].dubbing_production.group_failures == []


def test_regeneration_working_projection_excludes_only_current_formal_take():
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "old-current",
                "track_id": "dub",
                "target_subtitle_ids": ["localized_1"],
            },
            {
                "clip_id": "old-split",
                "track_id": "dub",
                "target_subtitle_ids": ["localized_1", "localized_2"],
            },
            {
                "clip_id": "manual-copy",
                "track_id": "dub",
                "target_subtitle_ids": ["localized_1"],
                "manual_history_copy": True,
            },
            {
                "clip_id": "next-group",
                "track_id": "dub",
                "target_subtitle_ids": ["localized_3"],
            },
        ]
    )

    working = dubbing_production_service._without_target_owned_formal_clips(
        draft,
        target_subtitle_ids=["localized_1"],
    )

    assert [clip["clip_id"] for clip in working.timeline_clips] == [
        "manual-copy",
        "next-group",
    ]
    assert [clip["clip_id"] for clip in draft.timeline_clips] == [
        "old-current",
        "old-split",
        "manual-copy",
        "next-group",
    ]


def test_candidate_generation_attempt_comes_from_owning_workflow():
    draft = SimpleNamespace(
        tts_tasks=[
            SimpleNamespace(
                generation_task_id="task-2",
                result_id="result-2",
                stages=[
                    SimpleNamespace(
                        kind="generation",
                        parameters={
                            "video_localization_generation_attempt": 2
                        },
                    )
                ],
            )
        ]
    )

    assert dubbing_production_service._candidate_generation_attempt(
        draft,
        generation_task_id="task-2",
        result_id="result-2",
    ) == 2
    assert dubbing_production_service._candidate_generation_attempt(
        draft,
        generation_task_id="missing",
        result_id="missing",
    ) == 1


def test_retry_cannot_make_continuous_boundary_materially_worse():
    before = DubbingContinuousBoundaryUnderfill(
        left_group_id="group-1",
        left_clip_id="left-old",
        right_clip_id="right",
        source_gap_ms=100,
        timeline_gap_ms=1_000,
        allowed_gap_ms=500,
    )
    worse = DubbingContinuousBoundaryUnderfill(
        left_group_id="group-1",
        left_clip_id="left-new",
        right_clip_id="right",
        source_gap_ms=100,
        timeline_gap_ms=1_100,
        allowed_gap_ms=500,
    )

    assert dubbing_production_service._replacement_worsens_continuous_boundary(
        [before],
        [worse],
        tolerance_ms=42,
    )
    assert dubbing_production_service._replacement_worsens_continuous_boundary(
        [],
        [worse],
        tolerance_ms=42,
    )


def test_processed_candidate_commit_rejects_new_neighbor_overlap(monkeypatch):
    source_revision = "c" * 64
    current_group = DubbingGenerationGroup(
        group_id="group-current",
        island_id="island-current",
        unit_ids=["unit-current"],
        subtitle_ids=["localized-current"],
        speaker_id="speaker-1",
        spoken_text="当前声音。",
        target_start_ms=1_000,
        target_end_ms=3_000,
        source_reference_start_ms=1_000,
        source_reference_end_ms=3_000,
    )
    next_group = DubbingGenerationGroup(
        group_id="group-next",
        island_id="island-next",
        unit_ids=["unit-next"],
        subtitle_ids=["localized-next"],
        speaker_id="speaker-1",
        spoken_text="下一段声音。",
        target_start_ms=3_100,
        target_end_ms=5_000,
        source_reference_start_ms=3_100,
        source_reference_end_ms=5_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=source_revision,
        plan_revision=1,
        status="passed",
        semantic_units=[],
        speech_islands=[],
        groups=[current_group, next_group],
    )
    reviewed_input = _candidate(
        source_revision=source_revision,
        plan_revision=1,
        group_id=current_group.group_id,
        candidate_id="candidate-new",
    )
    assert reviewed_input.audio is not None
    reviewed_input = reviewed_input.model_copy(
        update={
            "audio": reviewed_input.audio.model_copy(
                update={
                    "aligned_words": [
                        DubbingCandidateAlignedWord(
                            word_id="candidate_word_0001",
                            text="声音",
                            start_ms=50,
                            end_ms=1_920,
                        )
                    ]
                }
            )
        }
    )
    report = build_candidate_gap_processing_report(reviewed_input)
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip-old",
                "track_id": "dub",
                "dub_lane": 0,
                "status": "ready",
                "candidate_id": "candidate-old",
                "target_subtitle_ids": ["localized-current"],
                "start_ms": 920,
                "end_ms": 2_920,
                "source_start_ms": 0,
                "source_end_ms": 2_000,
            },
            {
                "clip_id": "clip-next",
                "track_id": "dub",
                "dub_lane": 0,
                "status": "ready",
                "audio_path": "/tmp/next.wav",
                "candidate_id": "candidate-next",
                "dubbing_group_id": next_group.group_id,
                "target_subtitle_ids": ["localized-next"],
                "start_ms": 3_020,
                "end_ms": 4_500,
                "source_start_ms": 0,
                "source_end_ms": 1_480,
            },
        ],
        generated_candidates=[
            {
                "candidate_id": "candidate-new",
                "result_id": "result-new",
                "task_id": "task-new",
                "audio_path": "/tmp/new.wav",
            }
        ],
        dubbing_production=DubbingProductionState(
            active_plan=plan,
            candidate_inputs=[reviewed_input],
            candidate_reports=[report],
        ),
    )
    processed_clip = {
        "clip_id": "clip-new",
        "track_id": "dub",
        "dub_lane": 1,
        "status": "ready",
        "audio_path": "/tmp/new.wav",
        "candidate_id": "candidate-new",
        "result_id": "result-new",
        "dubbing_group_id": current_group.group_id,
        "target_subtitle_ids": ["localized-current"],
        "start_ms": 2_500,
        "end_ms": 4_500,
        "source_start_ms": 0,
        "source_end_ms": 2_000,
    }

    def update(_project_id, callback, **_kwargs):
        return callback(draft)

    monkeypatch.setattr(
        dubbing_production_service.project_service,
        "update_video_localization_atomic",
        update,
    )

    with pytest.raises(
        AppException,
        match="新声音会与相邻配音冲突",
    ) as exc_info:
        DubbingProductionApplicationService._commit_processed_candidate(
            "project-1",
            group_id=current_group.group_id,
            candidate_id="candidate-new",
            processed_clips=[processed_clip],
            reviewed_input=reviewed_input,
            report=report,
            expected_target_projection_fingerprint=(
                dubbing_production_service._target_owned_projection_fingerprint(
                    draft,
                    target_subtitle_ids=list(current_group.subtitle_ids),
                )
            ),
        )

    assert exc_info.value.code == "VIDEO_LOCALIZATION_DUBBING_COMMIT_TIMELINE_CONFLICT"


def test_legacy_supervised_review_facts_do_not_finish_gap_processing():
    source_revision = "b" * 64
    group = DubbingGenerationGroup(
        group_id="group-supervised",
        island_id="island-supervised",
        unit_ids=["unit-supervised"],
        subtitle_ids=["localized-supervised"],
        speaker_id="speaker-1",
        spoken_text="这是一段待监工检查的配音。",
        target_start_ms=1_000,
        target_end_ms=3_000,
        source_reference_start_ms=1_000,
        source_reference_end_ms=3_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=source_revision,
        plan_revision=1,
        status="passed",
        semantic_units=[],
        speech_islands=[],
        groups=[group],
    )
    candidate = _candidate(plan_revision=1).model_copy(
        update={
            "source_revision": source_revision,
            "group_id": group.group_id,
            "candidate_id": "candidate-supervised",
        }
    )
    report = evaluate_candidate(candidate)

    run = build_production_run_snapshot(
        current_source_revision=source_revision,
        active_plan=plan,
        workflows=[],
        candidate_inputs=[candidate],
        candidate_reports=[report],
        group_failures=[],
        timeline_clips=[
            {
                "clip_id": "clip-supervised",
                "track_id": "dub",
                "dub_lane": 2,
                "candidate_id": candidate.candidate_id,
                "dubbing_group_id": group.group_id,
                "target_subtitle_ids": list(group.subtitle_ids),
                "start_ms": 1_000,
                "end_ms": 3_000,
            }
        ],
    )

    assert run.groups[0].stage == "needs_timeline_work"
    assert run.groups[0].recommended_action == "place_candidate"


def _retired_test_automatic_retry_review_can_advance_with_candidate_on_working_lane(
    monkeypatch,
):
    source_revision = "c" * 64
    group = DubbingGenerationGroup(
        group_id="group-retry",
        island_id="island-retry",
        unit_ids=["unit-retry"],
        subtitle_ids=["localized-retry"],
        speaker_id="speaker-1",
        spoken_text="这是一段需要最终复核的配音。",
        target_start_ms=1_000,
        target_end_ms=3_000,
        source_reference_start_ms=1_000,
        source_reference_end_ms=3_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=source_revision,
        plan_revision=1,
        status="passed",
        semantic_units=[],
        speech_islands=[],
        groups=[group],
    )
    candidate = _candidate(plan_revision=1).model_copy(
        update={
            "source_revision": source_revision,
            "group_id": group.group_id,
            "candidate_id": "candidate-retry",
        }
    )
    report = evaluate_candidate(candidate)
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip-retry",
                "track_id": "dub",
                "dub_lane": 2,
                "candidate_id": candidate.candidate_id,
                "dubbing_group_id": group.group_id,
                "target_subtitle_ids": list(group.subtitle_ids),
                "start_ms": 1_000,
                "end_ms": 3_000,
            }
        ],
        dubbing_production=DubbingProductionState(
            active_plan=plan,
            candidate_inputs=[candidate],
            candidate_reports=[report],
        ),
    )
    holder = {"draft": draft}
    service = DubbingProductionApplicationService()
    monkeypatch.setattr(
        service,
        "_require_current_project",
        lambda _project_id: holder["draft"],
    )
    monkeypatch.setattr(
        service,
        "_assert_current_revision",
        lambda *_args, **_kwargs: None,
    )

    def update(_project_id, callback, **_kwargs):
        holder["draft"] = callback(holder["draft"])
        return holder["draft"]

    monkeypatch.setattr(
        dubbing_production_service.project_service,
        "update_video_localization_atomic",
        update,
    )

    service.defer_group_for_manual_review(
        "project-1",
        DubbingProductionManualReviewRequest(
            source_revision=source_revision,
            plan_revision=1,
            group_id=group.group_id,
            candidate_id=candidate.candidate_id,
            title="待最终复核",
            note="候选已保留在工作轨",
            reason_code="candidate_retryable_timeline_failure",
        ),
    )

    reviews = holder["draft"].dubbing_production.group_reviews
    assert reviews[-1].candidate_id == candidate.candidate_id
    assert holder["draft"].timeline_clips[0]["dub_lane"] == 2


def test_final_compaction_keeps_only_terminal_selected_dub_clips():
    groups = [
        {
            "group_id": "group-1",
            "stage": "accepted",
            "passed_candidate_id": "candidate-1",
            "target_subtitle_ids": ["subtitle-1"],
            "formal_clip_ids": ["clip-1"],
        },
        {
            "group_id": "group-2",
            "stage": "accepted",
            "passed_candidate_id": "candidate-2",
            "target_subtitle_ids": ["subtitle-2"],
            "formal_clip_ids": ["clip-2"],
        },
    ]
    clips = [
        {"clip_id": "video", "track_id": "video"},
        {
            "clip_id": "old",
            "track_id": "dub",
            "candidate_id": "candidate-old",
            "target_subtitle_ids": ["subtitle-1", "subtitle-2"],
            "dub_lane": 0,
        },
        {
            "clip_id": "clip-1",
            "track_id": "dub",
            "candidate_id": "candidate-1",
            "target_subtitle_ids": ["subtitle-1"],
            "dub_lane": 2,
            "intentional_overlap": True,
            "timeline_edit_gate": {
                "status": "passed",
                "candidate_clip_projection_fingerprint": "before",
            },
        },
        {
            "clip_id": "clip-2",
            "track_id": "dub",
            "candidate_id": "candidate-2",
            "target_subtitle_ids": ["subtitle-2"],
            "dub_lane": 1,
        },
    ]

    compacted = dubbing_production_run.compact_terminal_run_timeline_clips(
        groups=groups,
        timeline_clips=clips,
    )

    assert [item["clip_id"] for item in compacted] == [
        "video",
        "clip-1",
        "clip-2",
    ]
    assert [item["dub_lane"] for item in compacted[1:]] == [0, 0]
    assert all(item["intentional_overlap"] is False for item in compacted[1:])
    assert compacted[1]["timeline_edit_gate"][
        "candidate_clip_projection_fingerprint"
    ] == candidate_clip_projection_fingerprint([compacted[1]])

    acceptances = dubbing_production_run.rebase_terminal_existing_acceptances(
        acceptances=[
            {
                "group_id": "group-2",
                "candidate_id": "candidate-2",
                "target_subtitle_ids": ["subtitle-2"],
                "candidate_clip_projection_fingerprint": "before",
            }
        ],
        timeline_clips=compacted,
    )
    assert acceptances[0]["candidate_clip_projection_fingerprint"] == (
        candidate_clip_projection_fingerprint([compacted[2]])
    )


def test_uniform_candidate_rebalance_rebinds_gate_and_rejects_crop_change():
    original = [
        {
            "clip_id": "clip-a",
            "track_id": "dub",
            "dubbing_group_id": "group-a",
            "candidate_id": "candidate-a",
            "start_ms": 100,
            "end_ms": 300,
            "source_start_ms": 20,
            "source_end_ms": 220,
            "alignment_lead_ms": 20,
            "alignment_trail_ms": 10,
            "timeline_edit_gate": {
                "candidate_clip_projection_fingerprint": "before",
                "actual_speech_start_delta_ms": 5,
                "actual_speech_end_delta_ms": 8,
            },
        },
        {
            "clip_id": "clip-b",
            "track_id": "dub",
            "dubbing_group_id": "group-b",
            "candidate_id": "candidate-b",
            "start_ms": 400,
            "end_ms": 600,
            "source_start_ms": 30,
            "source_end_ms": 230,
            "alignment_lead_ms": 10,
            "alignment_trail_ms": 10,
        },
    ]
    proposed = [
        {**original[0], "start_ms": 140, "end_ms": 340},
        {
            **original[1],
            "start_ms": 450,
            "end_ms": 640,
            "source_end_ms": 220,
        },
    ]

    kept = dubbing_production_run.keep_only_uniform_candidate_rebalance(
        original_timeline_clips=original,
        proposed_timeline_clips=proposed,
    )

    assert (kept[0]["start_ms"], kept[0]["end_ms"]) == (140, 340)
    assert kept[0]["timeline_edit_gate"]["actual_speech_start_delta_ms"] == 45
    assert kept[0]["timeline_edit_gate"]["actual_speech_end_delta_ms"] == 48
    assert kept[0]["timeline_edit_gate"][
        "candidate_clip_projection_fingerprint"
    ] == candidate_clip_projection_fingerprint([kept[0]])
    assert kept[1] == original[1]


def test_uniform_candidate_rebalance_accepts_legacy_clip_without_group_id():
    original = [
        {
            "clip_id": "clip-legacy",
            "track_id": "dub",
            "candidate_id": "candidate-legacy",
            "start_ms": 100,
            "end_ms": 300,
            "source_start_ms": 20,
            "source_end_ms": 220,
            "alignment_lead_ms": 20,
            "alignment_trail_ms": 10,
        }
    ]
    proposed = [{**original[0], "start_ms": 180, "end_ms": 380}]

    kept = dubbing_production_run.keep_only_uniform_candidate_rebalance(
        original_timeline_clips=original,
        proposed_timeline_clips=proposed,
    )

    assert (kept[0]["start_ms"], kept[0]["end_ms"]) == (180, 380)


def test_uniform_candidate_rebalance_accepts_proven_edge_silence_trim():
    original = [
        {
            "clip_id": "clip-a",
            "track_id": "dub",
            "candidate_id": "candidate-a",
            "start_ms": 100,
            "end_ms": 400,
            "source_start_ms": 20,
            "source_end_ms": 320,
            "alignment_lead_ms": 20,
            "alignment_trail_ms": 80,
            "timeline_edit_gate": {
                "candidate_clip_projection_fingerprint": "before",
                "actual_speech_start_delta_ms": 5,
                "actual_speech_end_delta_ms": 8,
            },
        }
    ]
    proposed = [
        {
            **original[0],
            "start_ms": 140,
            "end_ms": 380,
            "source_end_ms": 260,
            "alignment_trail_ms": 20,
        }
    ]

    kept = dubbing_production_run.keep_only_uniform_candidate_rebalance(
        original_timeline_clips=original,
        proposed_timeline_clips=proposed,
    )

    assert (kept[0]["start_ms"], kept[0]["end_ms"]) == (140, 380)
    assert kept[0]["source_end_ms"] == 260
    assert kept[0]["alignment_trail_ms"] == 20
    assert kept[0]["timeline_edit_gate"]["actual_speech_start_delta_ms"] == 45
    assert kept[0]["timeline_edit_gate"]["actual_speech_end_delta_ms"] == 48
    assert kept[0]["timeline_edit_gate"][
        "candidate_clip_projection_fingerprint"
    ] == candidate_clip_projection_fingerprint([kept[0]])


def test_safe_padding_overlap_trim_never_consumes_speech():
    timeline = [
        {
            "clip_id": "left",
            "track_id": "dub",
            "candidate_id": "candidate_left",
            "start_ms": 1_000,
            "end_ms": 2_100,
            "source_start_ms": 50,
            "source_end_ms": 1_150,
            "alignment_lead_ms": 50,
            "alignment_trail_ms": 80,
            "timeline_edit_gate": {"status": "passed"},
        },
        {
            "clip_id": "right",
            "track_id": "dub",
            "candidate_id": "candidate_right",
            "start_ms": 2_050,
            "end_ms": 3_050,
            "source_start_ms": 70,
            "source_end_ms": 1_070,
            "alignment_lead_ms": 70,
            "alignment_trail_ms": 60,
            "timeline_edit_gate": {"status": "passed"},
        },
        {
            "clip_id": "unsafe_left",
            "track_id": "dub",
            "candidate_id": "candidate_unsafe_left",
            "start_ms": 4_000,
            "end_ms": 5_500,
            "source_start_ms": 0,
            "source_end_ms": 1_500,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 20,
        },
        {
            "clip_id": "unsafe_right",
            "track_id": "dub",
            "candidate_id": "candidate_unsafe_right",
            "start_ms": 5_000,
            "end_ms": 6_000,
            "source_start_ms": 0,
            "source_end_ms": 1_000,
            "alignment_lead_ms": 70,
            "alignment_trail_ms": 20,
        },
    ]

    trimmed = dubbing_production_run.trim_safe_alignment_padding_overlaps(
        timeline
    )
    by_id = {clip["clip_id"]: clip for clip in trimmed}

    assert by_id["left"]["end_ms"] == 2_050
    assert by_id["left"]["source_end_ms"] == 1_100
    assert by_id["left"]["alignment_trail_ms"] == 30
    assert "timeline_edit_gate" not in by_id["left"]
    assert by_id["right"] == timeline[1]
    assert by_id["unsafe_left"] == timeline[2]
    assert by_id["unsafe_right"] == timeline[3]


def test_snapshot_splits_long_spoken_track_at_subtitle_and_speaker_boundaries():
    cues = []
    subtitles = []
    spoken_parts = []
    for index in range(1, 9):
        speaker = "speaker_a" if index <= 4 else "speaker_b"
        cue_id = f"cue_{index}"
        subtitle_id = f"localized_{index}"
        word_id = f"word_{index}"
        text = f"第{index}段" + ("内容" * 18) + ("。" if index % 2 == 0 else "，")
        start = (index - 1) * 1_000
        spoken_parts.append(text)
        cues.append(
            VideoLocalizationCue(
                cue_id=cue_id,
                speaker_id=speaker,
                start_ms=start,
                end_ms=start + 900,
                audio_route="clone_from_source",
                review_status="ready",
                source_word_ids=[word_id],
            )
        )
        subtitles.append(
            VideoLocalizationSubtitleCue(
                subtitle_id=subtitle_id,
                start_ms=start,
                end_ms=start + 900,
                text=text.rstrip("，。"),
                tts_text=text,
                source_cue_ids=[cue_id],
                source_word_ids=[word_id],
                spoken_segment_id="spoken_1",
            )
        )
    snapshot = build_project_snapshot(
        VideoLocalizationDraft(
            cues=cues,
            localized_subtitles=subtitles,
            localized_spoken_segments=[
                VideoLocalizationSpokenSegment(
                    segment_id="spoken_1",
                    paragraph_id="paragraph_1",
                    text="".join(spoken_parts),
                    start_ms=0,
                    end_ms=7_900,
                    source_cue_ids=[cue.cue_id for cue in cues],
                    source_word_ids=[f"word_{index}" for index in range(1, 9)],
                )
            ],
        )
    )

    assert len(snapshot.semantic_units) > 2
    assert all(len(unit.spoken_text) <= 180 for unit in snapshot.semantic_units)
    assert all(len(unit.subtitle_ids) <= 3 for unit in snapshot.semantic_units)
    assert [subtitle_id for unit in snapshot.semantic_units for subtitle_id in unit.subtitle_ids] == [
        f"localized_{index}" for index in range(1, 9)
    ]
    assert all(unit.speaker_id != "mixed" for unit in snapshot.semantic_units)
    assert all(
        left.speaker_id == right.speaker_id or boundary.hard_boundary
        for left, right, boundary in zip(
            snapshot.semantic_units,
            snapshot.semantic_units[1:],
            snapshot.boundaries,
        )
    )


def test_snapshot_still_splits_when_display_tts_only_normalizes_punctuation():
    cues = [
        VideoLocalizationCue(
            cue_id=f"cue_{index}",
            speaker_id="speaker_a",
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            audio_route="clone_from_source",
            review_status="ready",
            source_word_ids=[f"word_{index}"],
        )
        for index in range(1, 5)
    ]
    spoken_parts = ["第一句。", "第二句？！", "第三句。", "第四句。"]
    subtitle_parts = ["第一句。", "第二句？", "第三句。", "第四句。"]
    subtitles = [
        VideoLocalizationSubtitleCue(
            subtitle_id=f"localized_{index}",
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            text=text.rstrip("。？"),
            tts_text=text,
            source_cue_ids=[f"cue_{index}"],
            source_word_ids=[f"word_{index}"],
            spoken_segment_id="spoken_1",
        )
        for index, text in enumerate(subtitle_parts, start=1)
    ]

    snapshot = build_project_snapshot(
        VideoLocalizationDraft(
            cues=cues,
            localized_subtitles=subtitles,
            localized_spoken_segments=[
                VideoLocalizationSpokenSegment(
                    segment_id="spoken_1",
                    paragraph_id="paragraph_1",
                    text="".join(spoken_parts),
                    start_ms=0,
                    end_ms=4_000,
                    source_cue_ids=[cue.cue_id for cue in cues],
                    source_word_ids=[f"word_{index}" for index in range(1, 5)],
                )
            ],
        )
    )

    assert len(snapshot.semantic_units) == 4
    assert [item.subtitle_ids for item in snapshot.semantic_units] == [
        ["localized_1"],
        ["localized_2"],
        ["localized_3"],
        ["localized_4"],
    ]


def test_snapshot_respects_two_explicit_sentence_subtitles_inside_one_spoken_segment():
    cues = [
        VideoLocalizationCue(
            cue_id=f"cue_{index}",
            speaker_id="speaker_a",
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            audio_route="clone_from_source",
            review_status="ready",
            source_word_ids=[f"word_{index}"],
        )
        for index in range(1, 3)
    ]
    subtitles = [
        VideoLocalizationSubtitleCue(
            subtitle_id=f"localized_{index}",
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            text=text.rstrip("。？"),
            tts_text=text,
            source_cue_ids=[f"cue_{index}"],
            source_word_ids=[f"word_{index}"],
            spoken_segment_id="spoken_1",
        )
        for index, text in enumerate(["第一句。", "第二句？"], start=1)
    ]

    snapshot = build_project_snapshot(
        VideoLocalizationDraft(
            cues=cues,
            localized_subtitles=subtitles,
            localized_spoken_segments=[
                VideoLocalizationSpokenSegment(
                    segment_id="spoken_1",
                    paragraph_id="paragraph_1",
                    text="第一句。第二句？",
                    start_ms=0,
                    end_ms=2_000,
                    source_cue_ids=["cue_1", "cue_2"],
                    source_word_ids=["word_1", "word_2"],
                )
            ],
        )
    )

    assert [unit.subtitle_ids for unit in snapshot.semantic_units] == [
        ["localized_1"],
        ["localized_2"],
    ]


def test_snapshot_does_not_treat_unknown_plus_one_known_speaker_as_mixed():
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_1",
                start_ms=0,
                end_ms=500,
                audio_route="clone_from_source",
                review_status="ready",
            ),
            VideoLocalizationCue(
                cue_id="cue_2",
                speaker_id="speaker_1",
                start_ms=500,
                end_ms=1_000,
                audio_route="clone_from_source",
                review_status="ready",
            ),
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=0,
                end_ms=1_000,
                text="同一人的一句话",
                tts_text="同一人的一句话",
                source_cue_ids=["cue_1", "cue_2"],
            )
        ],
    )

    snapshot = build_project_snapshot(draft)

    assert snapshot.semantic_units[0].speaker_id == "speaker_1"


def test_snapshot_uses_current_display_merge_and_drops_orphan_spoken_segment():
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_1",
                speaker_id="speaker_1",
                start_ms=1_000,
                end_ms=1_400,
                audio_route="clone_from_source",
                review_status="ready",
                source_word_ids=["word_1"],
            ),
            VideoLocalizationCue(
                cue_id="cue_2",
                speaker_id="speaker_1",
                start_ms=5_000,
                end_ms=5_600,
                audio_route="clone_from_source",
                review_status="ready",
                source_word_ids=["word_2"],
            ),
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=5_000,
                end_ms=5_600,
                text="合并后的完整台词",
                tts_text="合并后的完整台词",
                source_cue_ids=["cue_1", "cue_2"],
                source_word_ids=["word_1", "word_2"],
                spoken_segment_id="spoken_1",
            )
        ],
        localized_spoken_segments=[
            VideoLocalizationSpokenSegment(
                segment_id="spoken_1",
                paragraph_id="paragraph_1",
                text="合并后的完整台词",
                start_ms=1_000,
                end_ms=1_400,
                source_cue_ids=["cue_1"],
                source_word_ids=["word_1"],
            ),
            VideoLocalizationSpokenSegment(
                segment_id="spoken_2",
                paragraph_id="paragraph_2",
                text="已经并入上一段",
                start_ms=5_000,
                end_ms=5_600,
                source_cue_ids=["cue_2"],
                source_word_ids=["word_2"],
            ),
        ],
    )

    snapshot = build_project_snapshot(draft)

    assert [unit.unit_id for unit in snapshot.semantic_units] == ["spoken_1"]
    unit = snapshot.semantic_units[0]
    assert (unit.start_ms, unit.end_ms) == (5_000, 5_600)
    assert unit.subtitle_ids == ["localized_1"]
    assert unit.source_cue_ids == ["cue_1", "cue_2"]
    assert unit.source_word_ids == ["word_1", "word_2"]
    assert unit.speaker_id == "speaker_1"


def test_snapshot_preserves_word_backed_windows_when_same_speaker_text_is_dense():
    texts = [f"第{index}段说明内容。" for index in range(1, 7)]
    cues = [
        VideoLocalizationCue(
            cue_id=f"cue_{index}",
            speaker_id="speaker_a",
            start_ms=(index - 1) * 1_300,
            end_ms=index * 1_300,
            audio_route="clone_from_source",
            review_status="ready",
            source_word_ids=[f"word_{index}"],
        )
        for index in range(1, 7)
    ]
    subtitles = []
    for index, text in enumerate(texts, start=1):
        if index <= 3:
            start_ms = (index - 1) * 1_300
            end_ms = index * 1_300
            cue_id = f"cue_{index}"
        else:
            start_ms = 7_500 + (index - 4) * 100
            end_ms = start_ms + 80
            cue_id = "cue_6"
        subtitles.append(
            VideoLocalizationSubtitleCue(
                subtitle_id=f"localized_{index}",
                start_ms=start_ms,
                end_ms=end_ms,
                text=text.rstrip("。"),
                tts_text=text,
                source_cue_ids=[cue_id],
                source_word_ids=[f"word_{index}"],
                spoken_segment_id="spoken_1",
            )
        )

    snapshot = build_project_snapshot(
        VideoLocalizationDraft(
            cues=cues,
            localized_subtitles=subtitles,
            localized_spoken_segments=[
                VideoLocalizationSpokenSegment(
                    segment_id="spoken_1",
                    paragraph_id="paragraph_1",
                    text="".join(texts),
                    start_ms=0,
                    end_ms=7_800,
                    source_cue_ids=[cue.cue_id for cue in cues],
                    source_word_ids=[f"word_{index}" for index in range(1, 7)],
                )
            ],
        )
    )

    assert len(snapshot.semantic_units) == 6
    assert [(unit.start_ms, unit.end_ms) for unit in snapshot.semantic_units] == [
        (0, 1_300),
        (1_300, 2_600),
        (2_600, 3_900),
        (7_500, 7_580),
        (7_600, 7_680),
        (7_700, 7_780),
    ]
    assert all(
        unit.source_anchor_start_ms == unit.start_ms and unit.source_anchor_end_ms == unit.end_ms
        for unit in snapshot.semantic_units
    )


def test_timeline_rebalance_compacts_continuous_speech_instead_of_spreading_residual():
    groups = [
        {
            "group_id": f"group_{index}",
            "unit_ids": [f"unit_{index}"],
            "subtitle_ids": [f"localized_{index}"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": (index - 1) * 4_000,
            "target_end_ms": index * 4_000,
        }
        for index in range(1, 6)
    ]
    durations = [3_000, 1_000, 3_000, 1_000, 3_000]
    clips = [
        {
            "clip_id": f"clip_{index}",
            "target_subtitle_ids": [f"localized_{index}"],
            "start_ms": (index - 1) * 4_000,
            "end_ms": (index - 1) * 4_000 + duration,
            "source_start_ms": 0,
            "source_end_ms": duration,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        }
        for index, duration in enumerate(durations, start=1)
    ]
    boundaries = [
        DubbingBoundaryEvidence(
            boundary_id=f"boundary_{index}",
            left_unit_id=f"unit_{index}",
            right_unit_id=f"unit_{index + 1}",
            gap_ms=0,
            same_speaker=True,
            same_scene=True,
            speech_between=False,
            pause_classification="continuous",
            semantic_relation="continuous",
            no_break_with_next=True,
        )
        for index in range(1, 5)
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=boundaries,
    )

    gaps = [right["start_ms"] - left["end_ms"] for left, right in zip(updated, updated[1:])]
    assert gaps == [0, 0, 2, 1_000]
    assert sum(gaps) < 9_000
    for clip, group in zip(updated, groups):
        assert clip["start_ms"] < group["target_end_ms"]
        assert clip["end_ms"] > group["target_start_ms"]


def test_timeline_rebalance_returns_planned_clips_to_main_lane():
    group = {
        "group_id": "group_1",
        "unit_ids": ["unit_1"],
        "subtitle_ids": ["localized_1"],
        "speaker_id": "speaker_a",
        "scene_id": "scene_a",
        "target_start_ms": 1_000,
        "target_end_ms": 3_000,
    }
    clip = {
        "clip_id": "clip_1",
        "dubbing_group_id": "group_1",
        "target_subtitle_ids": ["localized_1"],
        "target_start_ms": 1_000,
        "target_end_ms": 3_000,
        "start_ms": 900,
        "end_ms": 2_400,
        "source_start_ms": 100,
        "source_end_ms": 1_600,
        "alignment_lead_ms": 100,
        "alignment_trail_ms": 20,
        "dub_lane": 2,
    }

    updated = rebalance_planned_timeline_clips(
        timeline_clips=[clip],
        groups=[group],
        boundaries=[],
    )

    assert updated[0]["dub_lane"] == 0
    assert updated[0]["start_ms"] + updated[0]["alignment_lead_ms"] == 1_000


def test_selected_group_rebalance_does_not_move_unrelated_groups():
    groups = [
        {
            "group_id": "group_current",
            "unit_ids": ["unit_current"],
            "subtitle_ids": ["localized_current"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 1_000,
            "target_end_ms": 3_000,
        },
        {
            "group_id": "group_unrelated",
            "unit_ids": ["unit_unrelated"],
            "subtitle_ids": ["localized_unrelated"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 20_000,
            "target_end_ms": 22_000,
        },
    ]
    clips = [
        {
            "clip_id": "clip_current",
            "dubbing_group_id": "group_current",
            "target_subtitle_ids": ["localized_current"],
            "target_start_ms": 1_000,
            "target_end_ms": 3_000,
            "start_ms": 900,
            "end_ms": 2_400,
            "source_start_ms": 100,
            "source_end_ms": 1_600,
            "alignment_lead_ms": 100,
            "alignment_trail_ms": 20,
            "dub_lane": 2,
        },
        {
            "clip_id": "clip_unrelated",
            "dubbing_group_id": "group_unrelated",
            "target_subtitle_ids": ["localized_unrelated"],
            "target_start_ms": 20_000,
            "target_end_ms": 22_000,
            "start_ms": 19_500,
            "end_ms": 21_000,
            "source_start_ms": 50,
            "source_end_ms": 1_550,
            "alignment_lead_ms": 50,
            "alignment_trail_ms": 30,
            "dub_lane": 3,
        },
    ]

    updated = rebalance_selected_group_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=[],
        group_id="group_current",
    )

    assert updated[0]["dub_lane"] == 0
    assert updated[1] == clips[1]


def test_selected_group_rebalance_fits_repaired_tail_between_fixed_neighbors():
    groups = [
        {
            "group_id": "group_previous",
            "unit_ids": ["unit_previous"],
            "subtitle_ids": ["localized_previous"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 1_000,
            "target_end_ms": 5_000,
        },
        {
            "group_id": "group_current",
            "unit_ids": ["unit_current"],
            "subtitle_ids": ["localized_current"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 5_400,
            "target_end_ms": 12_000,
        },
        {
            "group_id": "group_next",
            "unit_ids": ["unit_next"],
            "subtitle_ids": ["localized_next"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 12_100,
            "target_end_ms": 15_000,
        },
    ]
    clips = [
        {
            "clip_id": "clip_previous",
            "dubbing_group_id": "group_previous",
            "target_subtitle_ids": ["localized_previous"],
            "target_start_ms": 1_000,
            "target_end_ms": 5_000,
            "start_ms": 1_000,
            "end_ms": 5_200,
            "source_start_ms": 0,
            "source_end_ms": 4_200,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_current_1",
            "dubbing_group_id": "group_current",
            "target_subtitle_ids": ["localized_current"],
            "target_start_ms": 5_400,
            "target_end_ms": 12_000,
            "start_ms": 5_800,
            "end_ms": 8_800,
            "source_start_ms": 80,
            "source_end_ms": 3_080,
            "alignment_lead_ms": 80,
            "alignment_trail_ms": 80,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_current_2",
            "dubbing_group_id": "group_current",
            "target_subtitle_ids": ["localized_current"],
            "target_start_ms": 5_400,
            "target_end_ms": 12_000,
            "start_ms": 8_800,
            "end_ms": 12_300,
            "source_start_ms": 3_400,
            "source_end_ms": 6_900,
            "alignment_lead_ms": 80,
            "alignment_trail_ms": 80,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_next",
            "dubbing_group_id": "group_next",
            "target_subtitle_ids": ["localized_next"],
            "target_start_ms": 12_100,
            "target_end_ms": 15_000,
            "start_ms": 12_150,
            "end_ms": 14_000,
            "source_start_ms": 50,
            "source_end_ms": 1_900,
            "alignment_lead_ms": 50,
            "alignment_trail_ms": 20,
            "dub_lane": 0,
        },
    ]

    updated = rebalance_selected_group_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=[],
        group_id="group_current",
    )

    by_id = {clip["clip_id"]: clip for clip in updated}
    assert by_id["clip_previous"] == clips[0]
    assert by_id["clip_next"] == clips[3]
    assert by_id["clip_current_1"]["start_ms"] == 5_650
    assert by_id["clip_current_2"]["end_ms"] == 12_150
    assert by_id["clip_current_1"]["start_ms"] >= by_id["clip_previous"]["end_ms"]
    assert by_id["clip_current_2"]["end_ms"] <= by_id["clip_next"]["start_ms"]


def test_adjacent_window_rebalance_moves_only_current_and_immediate_neighbors():
    starts = [0, 3_000, 6_000, 9_000, 12_000]
    groups = [
        {
            "group_id": f"group_{index}",
            "unit_ids": [f"unit_{index}"],
            "subtitle_ids": [f"localized_{index}"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": start_ms,
            "target_end_ms": start_ms + 3_000,
        }
        for index, start_ms in enumerate(starts, start=1)
    ]
    durations = [3_000, 3_300, 2_000, 3_100, 2_500]
    positions = [0, 3_200, 6_400, 8_400, 11_500]
    clips = [
        {
            "clip_id": f"clip_{index}",
            "track_id": "dub",
            "dubbing_group_id": f"group_{index}",
            "target_subtitle_ids": [f"localized_{index}"],
            "target_start_ms": start_ms,
            "target_end_ms": start_ms + 3_000,
            "start_ms": position_ms,
            "end_ms": position_ms + duration_ms,
            "source_start_ms": 0,
            "source_end_ms": duration_ms,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
            "audio_path": f"/tmp/{index}.wav",
            "status": "ready",
        }
        for index, (start_ms, position_ms, duration_ms) in enumerate(
            zip(starts, positions, durations),
            start=1,
        )
    ]
    boundaries = [
        DubbingBoundaryEvidence(
            boundary_id=f"boundary_{index}",
            left_unit_id=f"unit_{index}",
            right_unit_id=f"unit_{index + 1}",
            gap_ms=0,
            same_speaker=True,
            same_scene=True,
            speech_between=False,
            pause_classification="continuous",
            semantic_relation="continuous",
            no_break_with_next=True,
        )
        for index in range(1, 5)
    ]

    updated = rebalance_selected_group_with_adjacent_window_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=boundaries,
        group_id="group_3",
    )

    by_id = {clip["clip_id"]: clip for clip in updated}
    assert by_id["clip_1"] == clips[0]
    assert by_id["clip_5"] == clips[4]
    assert [
        (by_id[f"clip_{index}"]["start_ms"], by_id[f"clip_{index}"]["end_ms"])
        for index in range(2, 5)
    ] == [(3_000, 6_300), (6_300, 8_300), (8_300, 11_400)]


def test_selected_group_rebalance_does_not_leave_short_speech_before_its_window():
    group = {
        "group_id": "group_current",
        "unit_ids": ["unit_current"],
        "subtitle_ids": ["localized_current"],
        "speaker_id": "speaker_a",
        "scene_id": "scene_a",
        "target_start_ms": 5_000,
        "target_end_ms": 10_000,
    }
    clip = {
        "clip_id": "clip_current",
        "dubbing_group_id": "group_current",
        "target_subtitle_ids": ["localized_current"],
        "target_start_ms": 5_000,
        "target_end_ms": 10_000,
        "start_ms": 3_000,
        "end_ms": 6_000,
        "source_start_ms": 0,
        "source_end_ms": 3_000,
        "alignment_lead_ms": 80,
        "alignment_trail_ms": 80,
        "dub_lane": 0,
    }

    [updated] = rebalance_selected_group_timeline_clips(
        timeline_clips=[clip],
        groups=[group],
        boundaries=[],
        group_id="group_current",
    )

    assert updated["start_ms"] == 4_920
    assert updated["start_ms"] + updated["alignment_lead_ms"] == 5_000


def test_selected_group_rebalance_keeps_locked_source_onset_when_tail_overflows():
    groups = [
        {
            "group_id": "group_current",
            "unit_ids": ["unit_current"],
            "subtitle_ids": ["localized_current"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 5_000,
            "target_end_ms": 10_000,
        },
        {
            "group_id": "group_next",
            "unit_ids": ["unit_next"],
            "subtitle_ids": ["localized_next"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 10_100,
            "target_end_ms": 12_000,
        },
    ]
    clips = [
        {
            "clip_id": "clip_current",
            "dubbing_group_id": "group_current",
            "target_subtitle_ids": ["localized_current"],
            "target_start_ms": 5_000,
            "target_end_ms": 10_000,
            "start_ms": 4_920,
            "end_ms": 10_920,
            "source_start_ms": 80,
            "source_end_ms": 6_080,
            "alignment_lead_ms": 80,
            "alignment_trail_ms": 80,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_next",
            "dubbing_group_id": "group_next",
            "target_subtitle_ids": ["localized_next"],
            "target_start_ms": 10_100,
            "target_end_ms": 12_000,
            "start_ms": 10_100,
            "end_ms": 11_500,
            "source_start_ms": 0,
            "source_end_ms": 1_400,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
    ]

    updated = rebalance_selected_group_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=[],
        group_id="group_current",
        selected_start_anchor_ms=4_920,
    )

    by_id = {clip["clip_id"]: clip for clip in updated}
    assert by_id["clip_current"]["start_ms"] + by_id["clip_current"]["alignment_lead_ms"] == 5_000
    assert by_id["clip_current"]["end_ms"] > 10_000
    assert by_id["clip_next"] == clips[1]


@pytest.mark.parametrize("scheduler_recrops", [False, True])
def test_selected_group_rebalance_preserves_existing_manual_start_anchor(monkeypatch, scheduler_recrops):
    group = {
        "group_id": "group_current",
        "unit_ids": ["unit_current"],
        "subtitle_ids": ["localized_current"],
        "speaker_id": "speaker_a",
        "scene_id": "scene_a",
        "target_start_ms": 5_000,
        "target_end_ms": 10_000,
    }
    clip = {
        "clip_id": "manual-formal",
        "dubbing_group_id": "group_current",
        "target_subtitle_ids": ["localized_current"],
        "target_start_ms": 5_000,
        "target_end_ms": 10_000,
        "start_ms": 6_200,
        "end_ms": 7_700,
        "source_start_ms": 80,
        "source_end_ms": 1_580,
        "alignment_lead_ms": 80,
        "alignment_trail_ms": 20,
        "dub_lane": 0,
    }

    if scheduler_recrops:
        # Global scheduling can consume padding while fitting other groups.
        # A locked, already checked projection must keep its source mapping,
        # not merely put that newly cropped proposal back at the old start.
        monkeypatch.setattr(
            dubbing_production_service.domain,
            "rebalance_planned_timeline_clips",
            lambda **_kwargs: [{
                **clip,
                "start_ms": clip["start_ms"] + 80,
                "source_start_ms": clip["source_start_ms"] + 80,
                "alignment_lead_ms": 0,
            }],
        )

    [updated] = rebalance_selected_group_timeline_clips(
        timeline_clips=[clip],
        groups=[group],
        boundaries=[],
        group_id="group_current",
        selected_start_anchor_ms=6_200,
    )

    assert updated["start_ms"] == 6_200
    assert updated["end_ms"] == 7_700
    assert updated["source_start_ms"] == clip["source_start_ms"]
    assert updated["source_end_ms"] == clip["source_end_ms"]
    assert updated["alignment_lead_ms"] == clip["alignment_lead_ms"]


def test_timeline_rebalance_preserves_evidence_backed_break():
    groups = [
        {
            "group_id": f"group_{index}",
            "unit_ids": [f"unit_{index}"],
            "subtitle_ids": [f"localized_{index}"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": start_ms,
            "target_end_ms": start_ms + 4_000,
        }
        for index, start_ms in enumerate([0, 4_000, 11_000, 15_000], start=1)
    ]
    clips = [
        {
            "clip_id": f"clip_{index}",
            "target_subtitle_ids": [f"localized_{index}"],
            "start_ms": group["target_start_ms"],
            "end_ms": group["target_start_ms"] + 2_000,
            "source_start_ms": 0,
            "source_end_ms": 2_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        }
        for index, group in enumerate(groups, start=1)
    ]
    boundaries = [
        DubbingBoundaryEvidence(
            boundary_id=f"boundary_{index}",
            left_unit_id=f"unit_{index}",
            right_unit_id=f"unit_{index + 1}",
            gap_ms=3_000 if index == 2 else 0,
            same_speaker=True,
            same_scene=True,
            speech_between=False,
            pause_classification=("long_silence" if index == 2 else "continuous"),
            semantic_relation=("break" if index == 2 else "continuous"),
            hard_boundary=index == 2,
            no_break_with_next=index != 2,
        )
        for index in range(1, 4)
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=boundaries,
    )

    assert updated[2]["start_ms"] - updated[1]["end_ms"] >= 3_000


def test_timeline_rebalance_cascades_safe_forward_shift_across_hard_boundaries():
    groups = [
        {
            "group_id": "group_1",
            "unit_ids": ["unit_1"],
            "subtitle_ids": ["localized_1"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_1",
            "target_start_ms": 0,
            "target_end_ms": 2_000,
        },
        {
            "group_id": "group_2",
            "unit_ids": ["unit_2"],
            "subtitle_ids": ["localized_2"],
            "speaker_id": "speaker_b",
            "scene_id": "scene_2",
            "target_start_ms": 1_500,
            "target_end_ms": 4_000,
        },
        {
            "group_id": "group_3",
            "unit_ids": ["unit_3"],
            "subtitle_ids": ["localized_3"],
            "speaker_id": "speaker_c",
            "scene_id": "scene_3",
            "target_start_ms": 3_900,
            "target_end_ms": 5_000,
        },
    ]
    clips = [
        {
            "clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "target_subtitle_ids": ["localized_1"],
            "target_start_ms": 0,
            "target_end_ms": 2_000,
            "start_ms": 0,
            "end_ms": 2_000,
            "source_start_ms": 0,
            "source_end_ms": 2_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_2",
            "dubbing_group_id": "group_2",
            "target_subtitle_ids": ["localized_2"],
            "target_start_ms": 1_500,
            "target_end_ms": 4_000,
            "start_ms": 1_500,
            "end_ms": 3_500,
            "source_start_ms": 0,
            "source_end_ms": 2_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_3",
            "dubbing_group_id": "group_3",
            "target_subtitle_ids": ["localized_3"],
            "target_start_ms": 3_900,
            "target_end_ms": 5_000,
            "start_ms": 3_900,
            "end_ms": 4_900,
            "source_start_ms": 0,
            "source_end_ms": 1_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
    ]
    boundaries = [
        DubbingBoundaryEvidence(
            boundary_id=f"boundary_{index}",
            left_unit_id=f"unit_{index}",
            right_unit_id=f"unit_{index + 1}",
            gap_ms=0,
            same_speaker=False,
            same_scene=False,
            speech_between=False,
            pause_classification="natural_pause",
            semantic_relation="break",
            hard_boundary=True,
        )
        for index in (1, 2)
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=boundaries,
    )

    assert [clip["start_ms"] for clip in updated] == [0, 2_000, 4_000]
    assert all(
        right["start_ms"] >= left["end_ms"]
        for left, right in zip(updated, updated[1:])
    )


def test_timeline_rebalance_aligns_one_semantic_island_by_its_outer_edges():
    target_starts = [10_000, 12_400, 14_900, 17_400]
    target_ends = [12_200, 14_700, 17_100, 19_800]
    durations = [2_100, 2_100, 2_100, 2_600]
    groups = [
        {
            "group_id": f"group_{index}",
            "unit_ids": [f"unit_{index}"],
            "subtitle_ids": [f"localized_{index}"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": start_ms,
            "target_end_ms": end_ms,
        }
        for index, (start_ms, end_ms) in enumerate(
            zip(target_starts, target_ends),
            start=1,
        )
    ]
    clips = [
        {
            "clip_id": f"clip_{index}",
            "dubbing_group_id": f"group_{index}",
            "target_subtitle_ids": [f"localized_{index}"],
            "start_ms": start_ms,
            "end_ms": start_ms + duration_ms,
            "source_start_ms": 0,
            "source_end_ms": duration_ms,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        }
        for index, (start_ms, duration_ms) in enumerate(
            zip(target_starts, durations),
            start=1,
        )
    ]
    retained_pauses = [200, 200, 300]
    boundaries = [
        DubbingBoundaryEvidence(
            boundary_id=f"boundary_{index}",
            left_unit_id=f"unit_{index}",
            right_unit_id=f"unit_{index + 1}",
            gap_ms=pause_ms,
            same_speaker=True,
            same_scene=True,
            speech_between=False,
            pause_classification="natural_pause",
            low_energy_confidence="high",
            semantic_relation="continuous",
        )
        for index, pause_ms in enumerate(retained_pauses, start=1)
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=boundaries,
    )

    assert updated[0]["start_ms"] == target_starts[0]
    assert abs(updated[-1]["end_ms"] - target_ends[-1]) <= 300
    assert [
        right["start_ms"] - left["end_ms"]
        for left, right in zip(updated, updated[1:])
    ] == retained_pauses
    assert any(
        clip["start_ms"] != target_start_ms
        for clip, target_start_ms in zip(updated[1:], target_starts[1:])
    )


def test_timeline_rebalance_reanchors_single_clip_chain_after_prior_drift():
    groups = [
        {
            "group_id": "group_1",
            "unit_ids": ["unit_1"],
            "subtitle_ids": ["localized_1"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 10_000,
            "target_end_ms": 12_000,
        }
    ]
    clips = [
        {
            "clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "target_subtitle_ids": ["localized_1"],
            "start_ms": 30_000,
            "end_ms": 31_000,
            "source_start_ms": 100,
            "source_end_ms": 1_100,
            "alignment_lead_ms": 100,
            "alignment_trail_ms": 100,
            "dub_lane": 0,
        }
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=[],
    )

    assert updated[0]["start_ms"] == 9_900
    assert updated[0]["end_ms"] == 10_900


def test_timeline_rebalance_uses_strong_acoustic_continuity_without_editorial_labels():
    groups = [
        {
            "group_id": "group_1",
            "unit_ids": ["unit_1"],
            "subtitle_ids": ["localized_1"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 0,
            "target_end_ms": 2_000,
        },
        {
            "group_id": "group_2",
            "unit_ids": ["unit_2"],
            "subtitle_ids": ["localized_2"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 2_000,
            "target_end_ms": 4_000,
        },
    ]
    clips = [
        {
            "clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "target_subtitle_ids": ["localized_1"],
            "start_ms": 0,
            "end_ms": 1_000,
            "source_start_ms": 0,
            "source_end_ms": 1_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_2",
            "dubbing_group_id": "group_2",
            "target_subtitle_ids": ["localized_2"],
            "start_ms": 3_000,
            "end_ms": 4_000,
            "source_start_ms": 0,
            "source_end_ms": 1_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
    ]
    boundaries = [
        DubbingBoundaryEvidence(
            boundary_id="boundary_1",
            left_unit_id="unit_1",
            right_unit_id="unit_2",
            gap_ms=80,
            same_speaker=True,
            same_scene=None,
            speech_between=False,
            pause_classification="natural_pause",
            low_energy_confidence="high",
            semantic_relation="unknown",
        )
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=boundaries,
    )

    assert updated[1]["start_ms"] - updated[0]["end_ms"] == 80


def test_timeline_rebalance_does_not_push_a_hard_boundary_chain_off_anchor():
    groups = [
        {
            "group_id": "group_1",
            "unit_ids": ["unit_1"],
            "subtitle_ids": ["localized_1"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 0,
            "target_end_ms": 4_000,
        },
        {
            "group_id": "group_2",
            "unit_ids": ["unit_2"],
            "subtitle_ids": ["localized_2"],
            "speaker_id": "speaker_b",
            "scene_id": "scene_b",
            "target_start_ms": 4_000,
            "target_end_ms": 8_000,
        },
    ]
    clips = [
        {
            "clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "target_subtitle_ids": ["localized_1"],
            "start_ms": 0,
            "end_ms": 9_000,
            "source_start_ms": 0,
            "source_end_ms": 9_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_2",
            "dubbing_group_id": "group_2",
            "target_subtitle_ids": ["localized_2"],
            "start_ms": 9_000,
            "end_ms": 11_000,
            "source_start_ms": 0,
            "source_end_ms": 2_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
    ]
    boundaries = [
        DubbingBoundaryEvidence(
            boundary_id="boundary_1",
            left_unit_id="unit_1",
            right_unit_id="unit_2",
            gap_ms=0,
            same_speaker=False,
            same_scene=False,
            speech_between=False,
            pause_classification="natural_pause",
            semantic_relation="break",
            hard_boundary=True,
        )
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=boundaries,
    )

    assert updated[1]["start_ms"] == 4_000
    assert updated[1]["end_ms"] == 6_000


def test_timeline_rebalance_coordinates_hard_boundary_chains_when_feasible():
    groups = [
        {
            "group_id": "group_1",
            "unit_ids": ["unit_1"],
            "subtitle_ids": ["localized_1"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 0,
            "target_end_ms": 4_000,
        },
        {
            "group_id": "group_2",
            "unit_ids": ["unit_2"],
            "subtitle_ids": ["localized_2"],
            "speaker_id": "speaker_b",
            "scene_id": "scene_b",
            "target_start_ms": 4_000,
            "target_end_ms": 8_000,
        },
    ]
    clips = [
        {
            "clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "target_subtitle_ids": ["localized_1"],
            "start_ms": 0,
            "end_ms": 4_500,
            "source_start_ms": 0,
            "source_end_ms": 4_500,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_2",
            "dubbing_group_id": "group_2",
            "target_subtitle_ids": ["localized_2"],
            "start_ms": 4_000,
            "end_ms": 7_000,
            "source_start_ms": 0,
            "source_end_ms": 3_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
    ]
    boundaries = [
        DubbingBoundaryEvidence(
            boundary_id="boundary_1",
            left_unit_id="unit_1",
            right_unit_id="unit_2",
            gap_ms=0,
            same_speaker=False,
            same_scene=False,
            speech_between=False,
            pause_classification="natural_pause",
            semantic_relation="break",
            hard_boundary=True,
        )
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=boundaries,
    )

    assert updated[0]["start_ms"] == 0
    assert updated[0]["end_ms"] == 4_500
    assert updated[1]["start_ms"] == 4_500
    assert updated[1]["end_ms"] == 7_500


def test_timeline_rebalance_uses_verified_tail_silence_before_splitting_schedule():
    groups = [
        {
            "group_id": "group_1",
            "unit_ids": ["unit_1"],
            "subtitle_ids": ["localized_1"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 0,
            "target_end_ms": 4_000,
        },
        {
            "group_id": "group_2",
            "unit_ids": ["unit_2"],
            "subtitle_ids": ["localized_2"],
            "speaker_id": "speaker_b",
            "scene_id": "scene_b",
            "target_start_ms": 4_000,
            "target_end_ms": 4_080,
        },
    ]
    clips = [
        {
            "clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "target_subtitle_ids": ["localized_1"],
            "start_ms": 0,
            "end_ms": 4_080,
            "source_start_ms": 0,
            "source_end_ms": 4_080,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 100,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_2",
            "dubbing_group_id": "group_2",
            "target_subtitle_ids": ["localized_2"],
            "start_ms": 4_000,
            "end_ms": 7_000,
            "source_start_ms": 0,
            "source_end_ms": 3_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
    ]
    boundaries = [
        DubbingBoundaryEvidence(
            boundary_id="boundary_1",
            left_unit_id="unit_1",
            right_unit_id="unit_2",
            gap_ms=0,
            same_speaker=False,
            same_scene=False,
            speech_between=False,
            pause_classification="natural_pause",
            semantic_relation="break",
            hard_boundary=True,
        )
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=boundaries,
    )

    assert updated[0]["alignment_trail_ms"] == 20
    assert updated[0]["end_ms"] == updated[1]["start_ms"] == 4_000


def test_conservative_rebalance_restores_every_slice_in_rejected_group():
    original = [
        {
            "clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "dubbing_slice_index": 1,
            "start_ms": 100,
            "end_ms": 500,
        },
        {
            "clip_id": "clip_1__part_002",
            "dubbing_group_id": "group_1",
            "dubbing_slice_index": 2,
            "start_ms": 500,
            "end_ms": 900,
        },
        {
            "clip_id": "clip_2",
            "dubbing_group_id": "group_2",
            "start_ms": 1_000,
            "end_ms": 1_500,
        },
    ]
    proposal = [
        {**original[0], "start_ms": 200, "end_ms": 600},
        {**original[1], "start_ms": 600, "end_ms": 1_000},
        {**original[2], "start_ms": 900, "end_ms": 1_400},
    ]

    restored = restore_rebalance_groups(
        original_timeline_clips=original,
        proposed_timeline_clips=proposal,
        group_ids={"group_1"},
    )

    assert restored[:2] == original[:2]
    assert restored[2] == proposal[2]


def test_timeline_rebalance_preserves_internal_candidate_pause_shape():
    groups = [
        {
            "group_id": "group_1",
            "unit_ids": ["unit_1"],
            "subtitle_ids": ["localized_1", "localized_2"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 0,
            "target_end_ms": 8_000,
        }
    ]
    clips = [
        {
            "clip_id": "clip_1",
            "media_source_clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "dubbing_slice_index": 1,
            "dubbing_slice_count": 2,
            "target_subtitle_ids": ["localized_1"],
            "target_start_ms": 0,
            "target_end_ms": 1_000,
            "start_ms": 0,
            "end_ms": 1_000,
            "source_start_ms": 0,
            "source_end_ms": 1_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_1__part_002",
            "media_source_clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "dubbing_slice_index": 2,
            "dubbing_slice_count": 2,
            "target_subtitle_ids": ["localized_2"],
            "target_start_ms": 6_000,
            "target_end_ms": 8_000,
            "start_ms": 1_000,
            "end_ms": 2_000,
            "source_start_ms": 1_000,
            "source_end_ms": 2_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=[],
    )

    assert updated[1]["start_ms"] == updated[0]["end_ms"]


def test_timeline_rebalance_preserves_explicit_source_rhythm_gap():
    groups = [
        {
            "group_id": "group_1",
            "unit_ids": ["unit_1"],
            "subtitle_ids": ["localized_1"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 0,
            "target_end_ms": 4_000,
        }
    ]
    clips = [
        {
            "clip_id": "clip_1",
            "media_source_clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "dubbing_slice_index": 1,
            "dubbing_slice_count": 2,
            "target_subtitle_ids": ["localized_1"],
            "target_start_ms": 0,
            "target_end_ms": 4_000,
            "start_ms": 0,
            "end_ms": 1_000,
            "source_start_ms": 0,
            "source_end_ms": 1_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
        {
            "clip_id": "clip_1__part_002",
            "media_source_clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "dubbing_slice_index": 2,
            "dubbing_slice_count": 2,
            "dubbing_timeline_gap_before_ms": 600,
            "target_subtitle_ids": ["localized_1"],
            "target_start_ms": 0,
            "target_end_ms": 4_000,
            "start_ms": 1_600,
            "end_ms": 2_600,
            "source_start_ms": 1_000,
            "source_end_ms": 2_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        },
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=[],
    )

    assert updated[1]["start_ms"] - updated[0]["end_ms"] == 600


def test_timeline_rebalance_never_falls_back_to_spacing_candidate_slices(
    monkeypatch: pytest.MonkeyPatch,
):
    groups = [
        {
            "group_id": "group_1",
            "unit_ids": ["unit_1"],
            "subtitle_ids": ["localized_1", "localized_2"],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": 0,
            "target_end_ms": 8_000,
        }
    ]
    clips = [
        {
            "clip_id": f"clip_1_{index}",
            "media_source_clip_id": "clip_1",
            "dubbing_group_id": "group_1",
            "dubbing_slice_index": index,
            "dubbing_slice_count": 2,
            "target_subtitle_ids": [f"localized_{index}"],
            "target_start_ms": (index - 1) * 6_000,
            "target_end_ms": index * 4_000,
            "start_ms": (index - 1) * 1_000,
            "end_ms": index * 1_000,
            "source_start_ms": (index - 1) * 1_000,
            "source_end_ms": index * 1_000,
            "alignment_lead_ms": 0,
            "alignment_trail_ms": 0,
            "dub_lane": 0,
        }
        for index in (1, 2)
    ]
    monkeypatch.setattr(
        "app.domains.video_localization.dubbing_production._schedule_timeline_rebalance_chain",
        lambda _entries: None,
    )

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=[],
    )

    assert updated[1]["start_ms"] == updated[0]["end_ms"]


def test_timeline_rebalance_distributes_time_between_candidate_blocks_only():
    groups = [
        {
            "group_id": f"group_{group_index}",
            "unit_ids": [f"unit_{group_index}"],
            "subtitle_ids": [
                f"localized_{group_index}_1",
                f"localized_{group_index}_2",
            ],
            "speaker_id": "speaker_a",
            "scene_id": "scene_a",
            "target_start_ms": (group_index - 1) * 3_000,
            "target_end_ms": group_index * 3_000,
        }
        for group_index in range(1, 4)
    ]
    clips = []
    for group_index in range(1, 4):
        for slice_index in range(1, 3):
            clips.append(
                {
                    "clip_id": f"clip_{group_index}_{slice_index}",
                    "media_source_clip_id": f"clip_{group_index}",
                    "dubbing_group_id": f"group_{group_index}",
                    "dubbing_slice_index": slice_index,
                    "dubbing_slice_count": 2,
                    "target_subtitle_ids": [f"localized_{group_index}_{slice_index}"],
                    "target_start_ms": ((group_index - 1) * 3_000 + (slice_index - 1) * 1_500),
                    "target_end_ms": ((group_index - 1) * 3_000 + slice_index * 1_500),
                    "start_ms": (group_index - 1) * 3_000 + (slice_index - 1) * 500,
                    "end_ms": (group_index - 1) * 3_000 + slice_index * 500,
                    "source_start_ms": (slice_index - 1) * 500,
                    "source_end_ms": slice_index * 500,
                    "alignment_lead_ms": 0,
                    "alignment_trail_ms": 0,
                    "dub_lane": 0,
                }
            )
    boundaries = [
        DubbingBoundaryEvidence(
            boundary_id=f"boundary_{index}",
            left_unit_id=f"unit_{index}",
            right_unit_id=f"unit_{index + 1}",
            gap_ms=0,
            same_speaker=True,
            same_scene=True,
            speech_between=False,
            pause_classification="continuous",
            semantic_relation="continuous",
            no_break_with_next=True,
        )
        for index in range(1, 3)
    ]

    updated = rebalance_planned_timeline_clips(
        timeline_clips=clips,
        groups=groups,
        boundaries=boundaries,
    )

    assert updated[1]["start_ms"] == updated[0]["end_ms"]
    assert updated[3]["start_ms"] == updated[2]["end_ms"]
    assert updated[5]["start_ms"] == updated[4]["end_ms"]
    candidate_gaps = [
        updated[2]["start_ms"] - updated[1]["end_ms"],
        updated[4]["start_ms"] - updated[3]["end_ms"],
    ]
    assert candidate_gaps == [1_002, 2_000]


def test_plan_excludes_preserved_or_non_speech_units_from_tts_groups():
    payload = DubbingGenerationPlanInput(
        source_revision=SOURCE_REVISION,
        semantic_units=[
            _unit(1),
            _unit(2, speech_policy="preserve_original"),
            _unit(3, speech_policy="omit_non_speech"),
            _unit(4),
        ],
        boundaries=[_boundary(1), _boundary(2), _boundary(3)],
    )

    plan = build_generation_plan(payload)

    assert [group.unit_ids for group in plan.groups] == [
        ["unit_1"],
        ["unit_4"],
    ]


def test_project_snapshot_uses_adaptive_speaker_gaps_for_long_silence():
    starts = [0, 1_000, 2_100, 3_210, 4_340, 10_140]
    cues = []
    subtitles = []
    words = []
    for index, start in enumerate(starts, start=1):
        cue_id = f"cue_{index}"
        word_id = f"word_{index}"
        cues.append(
            VideoLocalizationCue(
                cue_id=cue_id,
                speaker_id="speaker_a",
                start_ms=start,
                end_ms=start + 900,
                audio_route="clone_from_source",
                review_status="ready",
                en_subtitle_text=f"source {index}",
                source_word_ids=[word_id],
            )
        )
        subtitles.append(
            VideoLocalizationSubtitleCue(
                subtitle_id=f"localized_{index}",
                start_ms=start,
                end_ms=start + 900,
                text=f"第{index}句",
                tts_text=f"第{index}句",
                source_cue_ids=[cue_id],
                source_word_ids=[word_id],
            )
        )
        words.append(
            VideoLocalizationAlignedWord(
                word_id=word_id,
                segment_id=f"segment_{index}",
                text=f"word{index}",
                speaker_cluster_id="speaker_a",
                start_ms=start,
                end_ms=start + 900,
                timing_confidence="high",
            )
        )
    long_gap = starts[-1] - (starts[-2] + 900)
    feature = VideoLocalizationAudioBoundaryEvidence(
        boundary_id="word_5:word_6",
        left_word_id="word_5",
        right_word_id="word_6",
        start_ms=starts[-2] + 900,
        end_ms=starts[-1],
        gap_ms=long_gap,
        low_energy_ms=long_gap,
        low_energy_ratio=1,
        gap_rms_dbfs=-60,
        speech_reference_dbfs=-18,
        noise_floor_dbfs=-70,
        energy_drop_db=42,
        confidence="high",
    )
    snapshot = build_project_snapshot(
        VideoLocalizationDraft(
            cues=cues,
            localized_subtitles=subtitles,
            transcription=VideoLocalizationTranscriptionState(
                words=words,
                audio_boundary_features=[feature],
            ),
        )
    )

    assert len(snapshot.source_revision) == 64
    assert snapshot.boundaries[-1].pause_classification == "long_silence"
    assert snapshot.boundaries[-1].hard_boundary is True
    assert snapshot.boundaries[-1].speech_between is False
    assert "speaker_adaptive_gap_baseline" in (snapshot.boundaries[-1].evidence_codes)
    assert all(boundary.pause_classification != "long_silence" for boundary in snapshot.boundaries[:-1])


def test_project_snapshot_does_not_promote_tiny_adaptive_gap_to_hard_silence():
    starts = [0, 1_000, 2_084, 3_168, 4_252, 5_375]
    cues = []
    subtitles = []
    words = []
    for index, start in enumerate(starts, start=1):
        cue_id = f"cue_{index}"
        word_id = f"word_{index}"
        cues.append(
            VideoLocalizationCue(
                cue_id=cue_id,
                speaker_id="speaker_a",
                start_ms=start,
                end_ms=start + 1_000,
                audio_route="clone_from_source",
                review_status="ready",
                source_word_ids=[word_id],
            )
        )
        subtitles.append(
            VideoLocalizationSubtitleCue(
                subtitle_id=f"localized_{index}",
                start_ms=start,
                end_ms=start + 1_000,
                text=f"第{index}句",
                tts_text=f"第{index}句",
                source_cue_ids=[cue_id],
                source_word_ids=[word_id],
            )
        )
        words.append(
            VideoLocalizationAlignedWord(
                word_id=word_id,
                segment_id=f"segment_{index}",
                text=f"word{index}",
                speaker_cluster_id="speaker_a",
                start_ms=start,
                end_ms=start + 1_000,
                timing_confidence="high",
            )
        )
    gap_ms = starts[-1] - (starts[-2] + 1_000)
    snapshot = build_project_snapshot(
        VideoLocalizationDraft(
            cues=cues,
            localized_subtitles=subtitles,
            transcription=VideoLocalizationTranscriptionState(
                words=words,
                audio_boundary_features=[
                    VideoLocalizationAudioBoundaryEvidence(
                        boundary_id="word_5:word_6",
                        left_word_id="word_5",
                        right_word_id="word_6",
                        start_ms=starts[-2] + 1_000,
                        end_ms=starts[-1],
                        gap_ms=gap_ms,
                        low_energy_ms=gap_ms,
                        low_energy_ratio=1,
                        gap_rms_dbfs=-60,
                        speech_reference_dbfs=-18,
                        noise_floor_dbfs=-70,
                        energy_drop_db=42,
                        confidence="high",
                    )
                ],
            ),
        )
    )

    assert snapshot.boundaries[-1].gap_ms == 123
    assert snapshot.boundaries[-1].pause_classification == "natural_pause"
    assert snapshot.boundaries[-1].hard_boundary is False
    assert snapshot.boundaries[-1].speech_between is False


def test_dubbing_revision_changes_when_spoken_wording_changes():
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_1",
                start_ms=0,
                end_ms=1_000,
                audio_route="clone_from_source",
                review_status="ready",
            )
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=0,
                end_ms=1_000,
                text="原台词",
                tts_text="原台词",
                source_cue_ids=["cue_1"],
            )
        ],
    )
    changed_subtitle = draft.localized_subtitles[0].model_copy(update={"tts_text": "修改后的自然台词"})

    assert dubbing_source_revision(draft) != dubbing_source_revision(
        draft.model_copy(update={"localized_subtitles": [changed_subtitle]})
    )


def test_dubbing_revision_covers_word_timing_and_boundary_evidence():
    word = VideoLocalizationAlignedWord(
        word_id="word_1",
        segment_id="segment_1",
        text="source",
        start_ms=100,
        end_ms=500,
        timing_confidence="high",
    )
    boundary = VideoLocalizationAudioBoundaryEvidence(
        boundary_id="word_1:word_2",
        left_word_id="word_1",
        right_word_id="word_2",
        start_ms=500,
        end_ms=700,
        gap_ms=200,
        low_energy_ms=180,
        low_energy_ratio=0.9,
        gap_rms_dbfs=-50,
        speech_reference_dbfs=-20,
        noise_floor_dbfs=-65,
        energy_drop_db=30,
        confidence="high",
    )
    transcription = VideoLocalizationTranscriptionState(
        revision_id="same-transcription-revision",
        words=[word],
        audio_boundary_features=[boundary],
    )
    draft = VideoLocalizationDraft(transcription=transcription)

    moved_word = transcription.model_copy(update={"words": [word.model_copy(update={"start_ms": 120})]})
    changed_boundary = transcription.model_copy(
        update={"audio_boundary_features": [boundary.model_copy(update={"confidence": "medium"})]}
    )

    assert dubbing_source_revision(draft) != dubbing_source_revision(
        draft.model_copy(update={"transcription": moved_word})
    )
    assert dubbing_source_revision(draft) != dubbing_source_revision(
        draft.model_copy(update={"transcription": changed_boundary})
    )


def test_compatible_content_edit_rebases_plan_and_invalidates_reports():
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_1",
                start_ms=0,
                end_ms=1_000,
                speaker_id="speaker_1",
                audio_route="clone_from_source",
                review_status="ready",
            )
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=0,
                end_ms=1_000,
                text="原台词",
                tts_text="原台词",
                source_cue_ids=["cue_1"],
            )
        ],
    )
    snapshot = build_project_snapshot(draft)
    plan = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=snapshot.source_revision,
            semantic_units=snapshot.semantic_units,
            boundaries=snapshot.boundaries,
        )
    ).model_copy(update={"plan_revision": 7})
    cqc_input = _candidate(source_revision=snapshot.source_revision)
    report = evaluate_candidate(cqc_input)
    with_plan = draft.model_copy(
        update={
            "dubbing_production": draft.dubbing_production.model_copy(
                update={
                    "enforcement_mode": "planned",
                    "plan_revision_counter": 7,
                    "active_plan": plan,
                    "candidate_reports": [report],
                    "candidate_inputs": [cqc_input],
                }
            ),
            "generated_candidates": [
                {
                    "candidate_id": "candidate_1",
                    "cue_id": "cue_1",
                    "audio_path": "/nonexistent/stale.wav",
                    "cqc_status": "passed",
                    "cqc_report_version": report.schema_version,
                    "cqc_report": report.model_dump(mode="json"),
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_1",
                    "candidate_id": "candidate_1",
                    "track_id": "dub",
                    "start_ms": 0,
                    "end_ms": 1_000,
                    "cqc_status": "passed",
                    "cqc_report_version": report.schema_version,
                }
            ],
        }
    )
    changed = with_plan.model_copy(
        update={
            "localized_subtitles": [with_plan.localized_subtitles[0].model_copy(update={"tts_text": "修改后的台词"})]
        }
    )

    normalized = draft_store.with_fresh_gate(changed, "later")

    assert normalized.dubbing_production.active_plan is not None
    assert normalized.dubbing_production.active_plan.plan_revision == 8
    assert normalized.dubbing_production.active_plan.source_revision == (
        dubbing_source_revision(normalized)
    )
    assert normalized.dubbing_production.active_plan.groups[0].spoken_text == (
        "修改后的台词"
    )
    assert normalized.dubbing_production.enforcement_mode == "planned"
    assert normalized.dubbing_production.plan_revision_counter == 8
    assert normalized.dubbing_production.candidate_reports == []
    assert normalized.dubbing_production.candidate_inputs == []
    assert normalized.dubbing_production.latest_timeline_audit is None
    assert "cqc_status" not in normalized.generated_candidates[0]
    assert "cqc_report_version" not in normalized.generated_candidates[0]
    assert "cqc_report" not in normalized.generated_candidates[0]
    assert "cqc_status" not in normalized.timeline_clips[0]
    assert "cqc_report_version" not in normalized.timeline_clips[0]
    assert "DUBBING_PLAN_REQUIRED" not in {
        issue.code
        for issue in quality_gate.dubbing_production_export_blockers(
            normalized
        )
    }
    with pytest.raises(AppException) as exc_info:
        tts_pipeline.with_applied_generated_candidate(
            normalized,
            "candidate_1",
        )
    assert exc_info.value.code == "VIDEO_LOCALIZATION_CANDIDATE_CQC_REQUIRED"


def test_empty_project_uses_planned_default_without_blocking_export():
    draft = VideoLocalizationDraft()

    assert draft.dubbing_production.enforcement_mode == "planned"
    assert quality_gate.dubbing_production_export_blockers(draft) == []


def _all_subjective_reviews(status: str = "passed"):
    return [
        DubbingSubjectiveReview(dimension=dimension, status=status)
        for dimension in (
            "meaning",
            "pronunciation",
            "prosody_parse",
            "voice_match",
            "naturalness",
        )
    ]


def _candidate(**updates) -> DubbingCandidateCqcInput:
    values = {
        "source_revision": SOURCE_REVISION,
        "group_id": "dubbing_group_0001",
        "candidate_id": "candidate_1",
        "task_status": "success",
        "artifact_id": "artifact_1",
        "audio_sha256": AUDIO_SHA,
        "expected_spoken_text": "这个结果可以使用",
        "reference_transcript": "hello from the source",
        "candidate_transcript": "这个结果可以使用",
        "target_start_ms": 1_000,
        "target_end_ms": 3_000,
        "planned_scene_end_ms": 3_500,
        "placement_start_ms": 1_000,
        "placement_end_ms": 3_000,
        "audio": DubbingAutomaticAudioEvidence(
            duration_ms=2_000,
            peak_dbfs=-1.0,
            clipping_ratio=0,
            leading_silence_ms=50,
            trailing_silence_ms=80,
            speech_start_ms=50,
            speech_end_ms=1_920,
            speech_span_ms=1_870,
            voiced_spans=[DubbingVoicedSpan(start_ms=50, end_ms=1_920)],
            voiced_duration_ms=1_870,
            speaking_rate_ratio=1.1,
            project_speaking_rate_ratio_min=1.0,
            project_speaking_rate_ratio_max=1.2,
            gap_evidence=[
                DubbingAudioGapEvidence(
                    gap_id="gap_head",
                    kind="leading",
                    start_ms=0,
                    end_ms=50,
                    duration_ms=50,
                    evidence_sources=["waveform", "energy"],
                    evidence_ids=["waveform:gap_head", "energy:gap_head"],
                    boundary_confidence="clear",
                    edit_decision="retain",
                    retained_duration_ms=50,
                    decision_reason="保留自然起音前气口",
                    safe_edit_boundary=True,
                ),
                DubbingAudioGapEvidence(
                    gap_id="gap_tail",
                    kind="trailing",
                    start_ms=1_920,
                    end_ms=2_000,
                    duration_ms=80,
                    evidence_sources=["waveform", "energy"],
                    evidence_ids=["waveform:gap_tail", "energy:gap_tail"],
                    boundary_confidence="clear",
                    edit_decision="retain",
                    retained_duration_ms=80,
                    decision_reason="保留自然尾音后的气口",
                    safe_edit_boundary=True,
                ),
            ],
            expected_pause_baseline_ms=200,
            max_leading_silence_ms=120,
            max_trailing_silence_ms=120,
        ),
        "subjective_reviews": _all_subjective_reviews(),
    }
    values.update(updates)
    return DubbingCandidateCqcInput(**values)


@pytest.mark.parametrize("post_adoption_change", [
    None, "clip", "audio", "plan", "ownership", "concurrent_edit",
])
def test_semantic_boundary_review_persists_agent_handoff_without_replacing_timeline(
    monkeypatch,
    tmp_path: Path,
    post_adoption_change,
):
    source_revision = "f" * 64
    group = DubbingGenerationGroup(
        group_id="dubbing_group_0001", island_id="island", unit_ids=["unit"],
        subtitle_ids=["localized_1"], speaker_id="speaker", spoken_text="甲乙",
        target_start_ms=1_000, target_end_ms=3_000,
        source_reference_start_ms=1_000, source_reference_end_ms=3_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=source_revision, plan_revision=1, status="passed",
        semantic_units=[], speech_islands=[], groups=[group],
    )
    frozen = _candidate(
        plan_revision=1,
        source_revision=source_revision,
        expected_spoken_text="甲乙",
        audio=_candidate().audio.model_copy(update={"aligned_words": [
            DubbingCandidateAlignedWord(word_id="a", text="甲", start_ms=100, end_ms=400),
            DubbingCandidateAlignedWord(word_id="b", text="乙", start_ms=400, end_ms=700),
        ]}),
    )
    stage = DubbingStagedCandidateProjection(
        candidate_id=frozen.candidate_id,
        target_projection_fingerprint=hashlib.sha256(b"[]").hexdigest(),
        candidate_clip_projection_fingerprint="c" * 64,
        clips=[DubbingStagedCandidateClip(
            clip_id="staged-1", candidate_id=frozen.candidate_id,
            start_ms=1_000, end_ms=3_000, source_start_ms=0, source_end_ms=2_000,
            target_subtitle_ids=["localized_1"], dubbing_group_id=group.group_id,
        )],
    )
    stage = stage.model_copy(update={
        "candidate_clip_projection_fingerprint": candidate_clip_projection_fingerprint(
            [clip.model_dump(exclude_none=True) for clip in stage.clips]
        ),
    })
    audit = dubbing_gap_adjudication.build_semantic_boundary_audit(
        source_revision=source_revision, plan_revision=1, candidate_id=frozen.candidate_id,
        audio_sha256=AUDIO_SHA,
        candidate_evidence_fingerprint=candidate_evidence_fingerprint(frozen),
        candidate_clip_projection_fingerprint=stage.candidate_clip_projection_fingerprint,
        expected_spoken_text="甲乙", aligned_words=list(frozen.audio.aligned_words),
        gaps=[], clips=[{
            "source_start_ms": 0, "source_end_ms": 2_000, "start_ms": 1_000,
        }],
    )
    report = build_candidate_gap_processing_report(frozen).model_copy(update={
        "semantic_boundary_audit": audit,
        "staged_candidate_projection": stage,
        "overall_status": "needs_review",
    })
    audio_path = tmp_path / "candidate.wav"
    audio_path.write_bytes(b"candidate")
    draft = VideoLocalizationDraft(
        generated_candidates=[{"candidate_id": frozen.candidate_id, "audio_path": str(audio_path)}],
        dubbing_production=DubbingProductionState(
            active_plan=plan, candidate_inputs=[frozen], candidate_reports=[report],
        ),
    )
    holder = {"draft": draft}
    service = DubbingProductionApplicationService()
    monkeypatch.setattr(service, "_require_current_project", lambda _id: holder["draft"])
    monkeypatch.setattr(
        dubbing_production_service.dubbing_media,
        "current_timeline_audio_sha256s",
        lambda _id, current: {str(clip["clip_id"]): AUDIO_SHA for clip in current.timeline_clips},
    )
    def update(_id, apply, **_kwargs):
        holder["draft"] = apply(holder["draft"])
        return holder["draft"]

    monkeypatch.setattr(
        dubbing_production_service.project_service,
        "update_video_localization_atomic",
        update,
    )
    command = DubbingCandidateReviewCommand(
        source_revision=audit.source_revision, plan_revision=1, candidate_id=frozen.candidate_id,
        audio_sha256=AUDIO_SHA, candidate_evidence_fingerprint=audit.candidate_evidence_fingerprint,
        candidate_clip_projection_fingerprint=audit.candidate_clip_projection_fingerprint,
        semantic_boundary_reviews=[{
            "boundary_id": audit.boundaries[0].boundary_id,
            "semantic_role": "continuous_phrase", "disposition": "uncertain", "reason": "需继续核对听感",
        }],
    )

    with pytest.raises(AppException, match="每个相邻字词边界"):
        service.submit_candidate_semantic_boundary_review(
            "project-1", command.model_copy(update={"semantic_boundary_reviews": []})
        )
    saved = service.submit_candidate_semantic_boundary_review("project-1", command)

    assert saved.semantic_boundary_audit.status == "pending_agent"
    assert saved.semantic_boundary_audit.agent_reviews == command.semantic_boundary_reviews
    assert holder["draft"].timeline_clips == []
    recovery_command = DubbingCandidateReviewCommand.model_validate({
        **command.model_dump(exclude={"semantic_boundary_reviews"}),
        "semantic_boundary_reviews": [{
            "boundary_id": audit.boundaries[0].boundary_id,
            "semantic_role": "continuous_phrase", "disposition": "recover", "reason": "需恢复候选",
        }],
    })
    recovery = service.submit_candidate_semantic_boundary_review(
        "project-1", recovery_command
    )
    assert recovery.semantic_boundary_audit.status == "recovery_required"
    assert holder["draft"].timeline_clips == []
    retained = service.submit_candidate_semantic_boundary_review(
        "project-1", command.model_copy(update={"semantic_boundary_reviews": []})
    )
    assert retained.semantic_boundary_audit.status == "recovery_required"
    assert holder["draft"].timeline_clips == []

    accepted_command = DubbingCandidateReviewCommand.model_validate({
        **command.model_dump(exclude={"semantic_boundary_reviews"}),
        "semantic_boundary_reviews": [{
            "boundary_id": audit.boundaries[0].boundary_id,
            "semantic_role": "continuous_phrase", "disposition": "acceptable", "reason": "已完成断句处置",
        }],
    })
    accepted = service.submit_candidate_semantic_boundary_review(
        "project-1", accepted_command
    )
    assert accepted.semantic_boundary_audit.status == "accepted"
    assert holder["draft"].timeline_clips[0]["candidate_id"] == frozen.candidate_id
    assert service.submit_candidate_semantic_boundary_review(
        "project-1", accepted_command
    ).semantic_boundary_audit.status == "accepted"
    adopted_clips = json.loads(json.dumps(holder["draft"].timeline_clips))
    if post_adoption_change is None:
        accepted_report = holder["draft"].dubbing_production.candidate_reports[0]
        accepted_frozen = holder["draft"].dubbing_production.candidate_inputs[0]
        assert candidate_clip_projection_fingerprint(adopted_clips) == (
            accepted_report.staged_candidate_projection
            .candidate_clip_projection_fingerprint
        )
        assert accepted_report.semantic_boundary_audit.candidate_evidence_fingerprint == (
            candidate_evidence_fingerprint(accepted_frozen)
        )
        assert accepted_report.semantic_boundary_audit.status == "accepted"
        assert accepted_report.semantic_boundary_audit.audio_sha256 == accepted_frozen.audio_sha256
        assert accepted_report.semantic_boundary_audit.source_revision == plan.source_revision
        assert accepted_report.semantic_boundary_audit.plan_revision == plan.plan_revision
        assert accepted_report.semantic_boundary_audit.candidate_clip_projection_fingerprint == (
            accepted_report.staged_candidate_projection
            .candidate_clip_projection_fingerprint
        )
        assert dubbing_production_service._current_group_working_candidate_clips(
            holder["draft"], group_id=group.group_id,
            candidate_id=frozen.candidate_id,
            target_subtitle_ids=list(group.subtitle_ids),
        )
        assert (
            service.finalize_generated_candidate(
                "project-1", frozen.candidate_id, group.group_id,
            )
            == "accepted"
        )
        assert holder["draft"].timeline_clips == adopted_clips
    revised_command = accepted_command.model_copy(update={
        "semantic_boundary_reviews": [
            accepted_command.semantic_boundary_reviews[0].model_copy(update={
                "reason": "甲乙是连续短语；补充完整的逐字时序证据说明。",
                "evidence_id": "retained-audio-asr",
            }),
        ],
    })
    if post_adoption_change == "clip":
        holder["draft"].timeline_clips[0]["source_start_ms"] += 10
    elif post_adoption_change == "ownership":
        holder["draft"].timeline_clips.append({
            **holder["draft"].timeline_clips[0], "clip_id": "another-owned-clip",
        })
    elif post_adoption_change == "audio":
        monkeypatch.setattr(
            dubbing_production_service.dubbing_media,
            "current_timeline_audio_sha256s",
            lambda _id, current: {str(clip["clip_id"]): "e" * 64
                                  for clip in current.timeline_clips},
        )
    elif post_adoption_change == "plan":
        current = holder["draft"]
        holder["draft"] = current.model_copy(update={
            "dubbing_production": current.dubbing_production.model_copy(update={
                "active_plan": plan.model_copy(update={"plan_revision": 2}),
            }),
        })
    elif post_adoption_change == "concurrent_edit":
        def update_after_edit(_id, apply, **_kwargs):
            holder["draft"].timeline_clips[0]["start_ms"] += 10
            return update(_id, apply, **_kwargs)
        monkeypatch.setattr(
            dubbing_production_service.project_service,
            "update_video_localization_atomic", update_after_edit,
        )
    if post_adoption_change:
        with pytest.raises(AppException, match="候选音频、版本或最终裁切已经变化"):
            service.submit_candidate_semantic_boundary_review("project-1", revised_command)
        assert holder["draft"].dubbing_production.candidate_reports[0].semantic_boundary_audit.agent_reviews == accepted_command.semantic_boundary_reviews
        with pytest.raises(AppException, match="候选音频、版本或最终裁切已经变化"):
            service.submit_candidate_semantic_boundary_review("project-1", accepted_command)
        return

    monkeypatch.setattr(service, "_commit_processed_candidate", lambda *a, **kw: pytest.fail(
        "Updating review evidence must not adopt the audio again"
    ))
    revised = service.submit_candidate_semantic_boundary_review("project-1", revised_command)
    assert revised.semantic_boundary_audit.agent_reviews == revised_command.semantic_boundary_reviews
    assert holder["draft"].timeline_clips == adopted_clips


def test_recovery_closeout_prefers_semantic_handoff_over_stale_lookup_failure(
    monkeypatch,
):
    group = SimpleNamespace(group_id="group-1", subtitle_ids=["subtitle-1"])
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(source_revision="a" * 64, plan_revision=1, groups=[group]),
            candidate_inputs=[], candidate_reports=[],
        ),
        timeline_clips=[],
    )
    report = SimpleNamespace(group_id="group-1", candidate_id="candidate-current")
    service = DubbingProductionApplicationService()
    monkeypatch.setattr(service, "_require_current_project", lambda _id: draft)
    calls = iter([AppException(409, "TTS_CONTENT_AUDIO_UNAVAILABLE", "暂不可读"), report])
    def resync(*_args):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(service, "resync_candidate_automatic_cqc", resync)
    monkeypatch.setattr(
        service,
        "resync_history_candidate_automatic_cqc",
        lambda *_args: (_ for _ in ()).throw(
            AppException(409, "TTS_CONTENT_AUDIO_UNAVAILABLE", "暂不可读")
        ),
    )
    monkeypatch.setattr(service, "finalize_generated_candidate", lambda *_args, **_kwargs: "needs_semantic_review")

    assert service.recover_and_finalize_generated_group(
        "project-1", "group-1", ["missing", "candidate-current"]
    ) == "needs_semantic_review"


def test_candidate_finalize_returns_typed_failure_when_alignment_is_empty(
    monkeypatch,
):
    group = DubbingGenerationGroup(
        group_id="dubbing_group_0001",
        island_id="speech_island_0001",
        unit_ids=["spoken_segment_0001"],
        subtitle_ids=["localized_cue_0001"],
        speaker_id="speaker_01",
        spoken_text="停！",
        target_start_ms=1_000,
        target_end_ms=3_000,
        source_reference_start_ms=1_000,
        source_reference_end_ms=3_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=SOURCE_REVISION,
        plan_revision=1,
        status="passed",
        semantic_units=[],
        speech_islands=[],
        groups=[group],
    )
    frozen = _candidate(plan_revision=1)
    assert frozen.audio is not None
    assert frozen.audio.aligned_words == []
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=plan,
            candidate_inputs=[frozen],
        )
    )
    service = DubbingProductionApplicationService()
    monkeypatch.setattr(
        service,
        "_require_current_project",
        lambda _project_id: draft,
    )

    with pytest.raises(ValueError, match="候选缺少可提交的气口门禁证据"):
        service._build_automatic_timeline_gate(
            plan=plan,
            frozen=frozen,
            report=build_candidate_gap_processing_report(frozen),
            clips=[
                {
                    "clip_id": "candidate-clip",
                    "candidate_id": frozen.candidate_id,
                    "start_ms": 1_000,
                    "end_ms": 3_000,
                    "source_start_ms": 0,
                    "source_end_ms": 2_000,
                }
            ],
            gaps=list(frozen.audio.gap_evidence),
            status="passed",
        )

    assert (
        service.finalize_generated_candidate(
            "project-1",
            frozen.candidate_id,
            group.group_id,
        )
        == "retryable_failure"
    )


@pytest.mark.parametrize("padding_only,overflow_ms", [(False, 0), (True, 0), (False, 4), (False, 24)])
@pytest.mark.parametrize("next_clip_present", [False, True])
def test_candidate_finalize_rejects_overlong_new_take_without_advancing_source_onset(
    monkeypatch, padding_only, overflow_ms, next_clip_present,
):
    group = DubbingGenerationGroup(
        group_id="dubbing_group_0001",
        island_id="speech_island_0001",
        unit_ids=["spoken_segment_0001"],
        subtitle_ids=["localized_cue_0001"],
        speaker_id="speaker_01",
        spoken_text="一段完整配音",
        target_start_ms=1_000,
        target_end_ms=3_000,
        source_reference_start_ms=1_000,
        source_reference_end_ms=3_000,
    )
    next_group = DubbingGenerationGroup(
        group_id="dubbing_group_0002",
        island_id="speech_island_0002",
        unit_ids=["spoken_segment_0002"],
        subtitle_ids=["localized_cue_0002"],
        speaker_id="speaker_02",
        spoken_text="相邻配音",
        target_start_ms=3_100,
        target_end_ms=5_000,
        source_reference_start_ms=3_100,
        source_reference_end_ms=5_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=SOURCE_REVISION,
        plan_revision=1,
        status="passed",
        semantic_units=[],
        speech_islands=[],
        groups=[group, next_group],
    )
    duration = 2_200 if padding_only else 4_000
    occupied_end = 3_000 if next_clip_present else next_group.target_start_ms
    if overflow_ms:
        duration = occupied_end - group.target_start_ms + 160 + overflow_ms + 100
    speech_end = duration - 100
    audio = _candidate().audio.model_copy(update={
        "duration_ms": duration,
        "speech_start_ms": 190,
        "speech_end_ms": speech_end,
        "speech_span_ms": speech_end - 190,
        "voiced_spans": [DubbingVoicedSpan(start_ms=190, end_ms=speech_end)],
        "voiced_duration_ms": speech_end - 190,
        "aligned_words": [DubbingCandidateAlignedWord(
            word_id="candidate_word_0001", text="一", start_ms=160, end_ms=speech_end,
        )],
        "gap_evidence": [],
    })
    frozen = _candidate(
        plan_revision=1,
        target_start_ms=group.target_start_ms,
        target_end_ms=group.target_end_ms,
        audio=audio,
    )
    next_clip = {
        "clip_id": "clip-next",
        "audio_path": "next.wav",
        "track_id": "dub",
        "status": "ready",
        "dubbing_group_id": next_group.group_id,
        "target_subtitle_ids": list(next_group.subtitle_ids),
        "target_start_ms": next_group.target_start_ms,
        "target_end_ms": next_group.target_end_ms,
        "start_ms": 3_000,
        "end_ms": 4_500,
        "source_start_ms": 0,
        "source_end_ms": 1_500,
        "alignment_lead_ms": 0,
        "alignment_trail_ms": 0,
        "dub_lane": 0,
    }
    candidate_clip = {
        "clip_id": "clip-candidate",
        "track_id": "dub",
        "status": "ready",
        "candidate_id": frozen.candidate_id,
        "dubbing_group_id": group.group_id,
        "target_subtitle_ids": list(group.subtitle_ids),
        "target_start_ms": group.target_start_ms,
        "target_end_ms": group.target_end_ms,
        "start_ms": 840,
        "end_ms": 840 + duration,
        "source_start_ms": 0,
        "source_end_ms": duration,
        "alignment_lead_ms": 160,
        "alignment_trail_ms": 100,
        "dub_lane": 0,
    }
    draft = VideoLocalizationDraft(
        timeline_clips=[next_clip] if next_clip_present else [],
        dubbing_production=DubbingProductionState(
            active_plan=plan,
            candidate_inputs=[frozen],
        ),
    )
    service = DubbingProductionApplicationService()
    monkeypatch.setattr(service, "_require_current_project", lambda _project_id: draft)
    monkeypatch.setattr(
        dubbing_production_service.tts_pipeline,
        "with_applied_generated_candidate",
        lambda current, _candidate_id: current.model_copy(
            update={"timeline_clips": [*current.timeline_clips, candidate_clip]}
        ),
    )
    monkeypatch.setattr(
        dubbing_production_service.domain,
        "build_candidate_gap_processing_report",
        lambda _frozen: SimpleNamespace(overall_status="passed"),
    )
    monkeypatch.setattr(
        dubbing_production_service.domain,
        "build_project_snapshot",
        lambda _draft: SimpleNamespace(boundaries=[]),
    )
    monkeypatch.setattr(
        dubbing_production_service,
        "_source_pause_rhythm_for_group",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        dubbing_production_service.dubbing_gap_adjudication,
        "process_gap_evidence",
        lambda *_args, **_kwargs: [],
    )
    observed: list[list[dict]] = []
    real_rebalance = dubbing_production_service.domain.rebalance_selected_group_timeline_clips

    def capture_rebalance(**kwargs):
        result = real_rebalance(**kwargs)
        selected = [
            dict(clip)
            for clip in result
            if clip.get("dubbing_group_id") == group.group_id
        ]
        if kwargs.get("selected_start_anchor_ms") is not None:
            observed.append(selected)
        return result

    monkeypatch.setattr(
        dubbing_production_service.domain,
        "rebalance_selected_group_timeline_clips",
        capture_rebalance,
    )

    if padding_only:
        class ReachedStaging(Exception):
            pass

        def capture_staging(**kwargs):
            clips = kwargs["clips"]
            assert clips[-1]["end_ms"] <= occupied_end
            assert clips[-1]["source_end_ms"] >= speech_end
            assert candidate_audible_timeline_bounds(
                clips, speech_start_ms=160, speech_end_ms=speech_end,
            )[0] == group.target_start_ms
            assert draft.timeline_clips == ([next_clip] if next_clip_present else [])
            raise ReachedStaging

        monkeypatch.setattr(dubbing_production_service, "_staged_candidate_projection", capture_staging)
        with pytest.raises(ReachedStaging):
            service.finalize_generated_candidate("project-1", frozen.candidate_id, group.group_id)
        return

    result = service.finalize_generated_candidate(
        "project-1", frozen.candidate_id, group.group_id,
    )

    assert result == "capacity_recovery_required"
    assert observed
    audible_bounds = candidate_audible_timeline_bounds(
        observed[-1], speech_start_ms=160, speech_end_ms=speech_end,
    )
    assert audible_bounds is not None
    assert audible_bounds[0] == group.target_start_ms
    assert max(clip["end_ms"] for clip in observed[-1]) > group.target_end_ms


def test_candidate_finalize_preserves_word_tail_and_classifies_capacity_after_leading_reanchor(
    monkeypatch,
):
    group = DubbingGenerationGroup(
        group_id="dubbing_group_0050", island_id="island_0050", unit_ids=["unit_0050"],
        subtitle_ids=["localized_cue_0063"], speaker_id="speaker_01", spoken_text="完整台词",
        target_start_ms=177_790, target_end_ms=179_138,
        source_reference_start_ms=177_790, source_reference_end_ms=179_138,
    )
    next_group = DubbingGenerationGroup(
        group_id="dubbing_group_0051", island_id="island_0051", unit_ids=["unit_0051"],
        subtitle_ids=["localized_cue_0064"], speaker_id="speaker_01", spoken_text="相邻台词",
        target_start_ms=179_222, target_end_ms=180_000,
        source_reference_start_ms=179_222, source_reference_end_ms=180_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=SOURCE_REVISION, plan_revision=1, status="passed",
        semantic_units=[], speech_islands=[], groups=[group, next_group],
    )
    audio = _candidate().audio.model_copy(update={
        "duration_ms": 1_665, "speech_start_ms": 180, "speech_end_ms": 1_520,
        "speech_span_ms": 1_340, "voiced_spans": [DubbingVoicedSpan(start_ms=180, end_ms=1_520)],
        "voiced_duration_ms": 1_340,
        "aligned_words": [DubbingCandidateAlignedWord(
            word_id="candidate_word_0050", text="完", start_ms=160, end_ms=1_600,
        )],
        "gap_evidence": [],
    })
    frozen = _candidate(
        group_id=group.group_id, candidate_id="candidate_50", plan_revision=1,
        target_start_ms=group.target_start_ms, target_end_ms=group.target_end_ms,
        audio=audio,
    )
    candidate_clip = {
        "clip_id": "clip_localized_cue_0063", "candidate_id": frozen.candidate_id,
        "track_id": "dub", "status": "ready", "dub_lane": 0, "audio_path": "candidate.wav",
        "dubbing_group_id": group.group_id, "target_subtitle_ids": list(group.subtitle_ids),
        "start_ms": 177_710, "end_ms": 179_265,
        "source_start_ms": 110, "source_end_ms": 1_665,
    }
    next_clip = {
        "clip_id": "clip_localized_cue_0064", "track_id": "dub", "status": "ready",
        "dub_lane": 0, "audio_path": "next.wav", "dubbing_group_id": next_group.group_id,
        "target_subtitle_ids": list(next_group.subtitle_ids),
        "start_ms": 179_132, "end_ms": 180_342,
        "source_start_ms": 70, "source_end_ms": 1_280,
    }
    draft = VideoLocalizationDraft(
        source_media={"duration_ms": 200_000, "frame_rate": 30},
        timeline_clips=[next_clip],
        dubbing_production=DubbingProductionState(active_plan=plan, candidate_inputs=[frozen]),
    )
    service = DubbingProductionApplicationService()
    monkeypatch.setattr(service, "_require_current_project", lambda _project_id: draft)
    monkeypatch.setattr(
        dubbing_production_service.tts_pipeline,
        "with_applied_generated_candidate",
        lambda current, _candidate_id: current.model_copy(
            update={"timeline_clips": [*current.timeline_clips, candidate_clip]}
        ),
    )
    monkeypatch.setattr(
        dubbing_production_service.domain,
        "build_project_snapshot",
        lambda _draft: SimpleNamespace(boundaries=[]),
    )
    monkeypatch.setattr(
        dubbing_production_service,
        "_source_pause_rhythm_for_group",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        dubbing_production_service.dubbing_gap_adjudication,
        "process_gap_evidence",
        lambda *_args, **_kwargs: [],
    )
    observed: list[list[dict]] = []

    def rebalance(**kwargs):
        clips = [dict(clip) for clip in kwargs["timeline_clips"]]
        if kwargs.get("selected_start_anchor_ms") is None:
            for clip in clips:
                if clip.get("candidate_id") == frozen.candidate_id:
                    clip.update({"start_ms": 177_577, "end_ms": 179_132})
        else:
            observed.append(clips)
        return clips

    monkeypatch.setattr(
        dubbing_production_service.domain,
        "rebalance_selected_group_timeline_clips",
        rebalance,
    )

    assert (
        service.finalize_generated_candidate("project-1", frozen.candidate_id, group.group_id)
        == "capacity_recovery_required"
    )
    candidate = [
        clip for clip in observed[-1]
        if clip.get("candidate_id") == frozen.candidate_id
    ][0]
    assert candidate["source_end_ms"] == 1_665
    assert candidate["end_ms"] == 179_295


def test_current_candidate_projection_uses_trimmed_descendants_not_resurfaced_source():
    draft = SimpleNamespace(
        timeline_clips=[
            {
                "clip_id": "clip-source",
                "track_id": "dub",
                "dub_lane": 0,
                "candidate_id": "candidate-1",
                "target_subtitle_ids": ["localized-1"],
                "source_start_ms": 0,
                "source_end_ms": 2_000,
            },
            {
                "clip_id": "clip-source-part-2",
                "media_source_clip_id": "clip-source",
                "track_id": "dub",
                "dub_lane": 0,
                "candidate_id": "candidate-1",
                "target_subtitle_ids": ["localized-1"],
                "source_start_ms": 200,
                "source_end_ms": 1_900,
            },
        ]
    )

    clips = dubbing_production_service._current_group_working_candidate_clips(
        draft,
        group_id="group-1",
        candidate_id="candidate-1",
        target_subtitle_ids=["localized-1"],
    )

    assert [clip["clip_id"] for clip in clips] == ["clip-source-part-2"]


def test_current_candidate_projection_preserves_first_slice_reusing_source_id():
    clips = [
        {
            "clip_id": "source" if index == 1 else "source__part_002",
            "media_source_clip_id": "source",
            "track_id": "dub",
            "candidate_id": "candidate-1",
            "target_subtitle_ids": ["localized-1"],
            "dubbing_slice_index": index,
            "dubbing_slice_count": 2,
            "source_start_ms": start,
            "source_end_ms": end,
        }
        for index, start, end in [(1, 80, 1000), (2, 1100, 2000)]
    ]
    result = dubbing_production_service._current_group_working_candidate_clips(
        SimpleNamespace(timeline_clips=clips),
        group_id="group-1",
        candidate_id="candidate-1",
        target_subtitle_ids=["localized-1"],
    )
    assert result == [{**clip, "dubbing_group_id": "group-1"} for clip in clips]


@pytest.mark.parametrize("targets, existing_group, accepted", [
    (["localized-1"], None, True),
    (["localized-1", "localized-2"], None, False),
    (["localized-1"], "other-group", False),
])
def test_history_adopted_crop_can_enter_typed_review_only_for_exact_group(
    targets, existing_group, accepted,
):
    clip = {
        "clip_id": "history-crop", "candidate_id": "candidate-1",
        "track_id": "dub", "status": "ready", "dub_lane": 0,
        "start_ms": 1_000, "end_ms": 2_460,
        "source_start_ms": 220, "source_end_ms": 1_680,
        "target_subtitle_ids": targets, "subtitle_id": "localized-1",
        "result_id": "result-1", "task_id": "task-1",
        "generation_id": "task-1", "cue_id": "cue-1",
        "target_start_ms": 1_020, "target_end_ms": 2_500,
        "tts_target_text": "完整台词", "source_cue_ids": ["cue-1"],
    }
    if existing_group:
        clip["dubbing_group_id"] = existing_group
    original = dict(clip)
    result = dubbing_production_service._current_group_working_candidate_clips(
        SimpleNamespace(timeline_clips=[clip]), group_id="group-1",
        candidate_id="candidate-1", target_subtitle_ids=["localized-1"],
    )
    assert clip == original
    if not accepted:
        assert result == []
        return
    stage = dubbing_production_service._staged_candidate_projection(
        candidate_id="candidate-1", target_projection_fingerprint="a" * 64,
        clips=result,
    )
    assert stage.clips[0].dubbing_group_id == "group-1"
    assert stage.clips[0].source_start_ms == 220
    assert stage.clips[0].source_end_ms == 1_680


def test_candidate_cqc_reports_acoustic_timing_rate_and_gap_evidence():
    candidate = _candidate()

    report = evaluate_candidate(candidate)

    assert report.overall_status == "passed"
    assert report.audio_evidence == candidate.audio
    assert report.audio_evidence.speech_start_ms == 50
    assert report.audio_evidence.speech_end_ms == 1_920
    assert report.audio_evidence.voiced_duration_ms == 1_870
    assert report.audio_evidence.speaking_rate_ratio == 1.1
    assert [gap.gap_id for gap in report.audio_evidence.gap_evidence] == [
        "gap_head",
        "gap_tail",
    ]


def test_managed_gap_processing_keeps_raw_content_mismatch_advisory():
    from app.schemas.tts_content import TtsContentEvidence

    candidate = _candidate(
        candidate_transcript="这个结果可以使用",
        content_evidence=TtsContentEvidence(
            audio_sha256=AUDIO_SHA, engine_id="qwen3-asr-mlx", status="complete",
            transcript="完全不同的识别文本",
        ),
        subjective_reviews=_all_subjective_reviews("failed"),
    )

    report = build_candidate_gap_processing_report(candidate)

    assert report.overall_status == "warning"
    assert report.recommended_action == "listen_and_review"
    assert report.subjective_status == "not_reviewed"
    assert any(finding.code == "CANDIDATE_TRANSCRIPT_MINOR_MISMATCH" for finding in report.findings)
    assert report.audio_evidence == candidate.audio


def test_audio_gap_contract_allows_evidence_backed_pause_extension():
    gap = DubbingAudioGapEvidence(
        gap_id="gap_sentence_boundary",
        kind="internal",
        start_ms=900,
        end_ms=1_100,
        duration_ms=200,
        evidence_sources=["waveform", "energy", "listening"],
        evidence_ids=["waveform:gap", "energy:gap", "listening:gap"],
        boundary_confidence="clear",
        edit_decision="extend",
        retained_duration_ms=350,
        decision_reason="完整语义结束后需要更明确的换句停顿",
        safe_edit_boundary=True,
        review_evidence_ids=["user-listening:gap"],
        semantic_role="semantic_boundary",
        semantic_pause_scale="normal",
    )

    assert gap.retained_duration_ms == 350


def test_candidate_audible_bounds_ignore_trimmed_lead_tail_and_internal_gap():
    bounds = candidate_audible_timeline_bounds(
        [
            {
                "start_ms": 1_000,
                "end_ms": 1_400,
                "source_start_ms": 100,
                "source_end_ms": 500,
            },
            {
                "start_ms": 1_400,
                "end_ms": 1_700,
                "source_start_ms": 700,
                "source_end_ms": 1_000,
            },
        ],
        speech_start_ms=150,
        speech_end_ms=900,
    )

    assert bounds == (1_050, 1_600)


def test_candidate_cqc_keeps_legacy_fingerprint_when_semantic_fields_are_absent():
    candidate = _candidate()
    legacy_payload = candidate.model_dump(mode="json")
    legacy_payload.pop("retained_content_evidence", None)
    # These optional provenance extensions were absent from stored v1 inputs.
    for field in ("source_context_fingerprint", "evidence_origin_source_revision", "evidence_origin_plan_revision"):
        legacy_payload.pop(field, None)
    for gap in legacy_payload["audio"]["gap_evidence"]:
        gap.pop("semantic_role", None)
        gap.pop("semantic_pause_scale", None)
    legacy_fingerprint = hashlib.sha256(
        json.dumps(
            legacy_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    report = evaluate_candidate(candidate)

    assert report.evidence_fingerprint == legacy_fingerprint


def test_candidate_alignment_binds_real_words_to_acoustic_gaps():
    candidate = _candidate()
    assert candidate.audio is not None
    words = dubbing_candidate_alignment.normalize_aligned_words(
        [
            {"text": "这", "start_time": 0.05, "end_time": 0.4},
            {"text": "个", "start_time": 0.4, "end_time": 0.8},
            {"text": "结", "start_time": 1.1, "end_time": 1.4},
            {"text": "果", "start_time": 1.4, "end_time": 1.92},
        ],
        duration_ms=candidate.audio.duration_ms,
    )
    internal = DubbingAudioGapEvidence(
        gap_id="gap_internal",
        kind="internal",
        start_ms=800,
        end_ms=1_100,
        duration_ms=300,
        evidence_sources=["waveform", "energy"],
        evidence_ids=["waveform:gap_internal", "energy:gap_internal"],
        boundary_confidence="ambiguous",
        edit_decision="retain",
        retained_duration_ms=300,
        decision_reason="等待语义判断",
    )
    within_word = DubbingAudioGapEvidence(
        gap_id="gap_inside_word",
        kind="internal",
        start_ms=1_450,
        end_ms=1_520,
        duration_ms=70,
        evidence_sources=["waveform", "energy"],
        evidence_ids=["waveform:gap_inside_word", "energy:gap_inside_word"],
        boundary_confidence="ambiguous",
        edit_decision="retain",
        retained_duration_ms=70,
        decision_reason="词内低能量区",
    )
    overlaps_word_tail = DubbingAudioGapEvidence(
        gap_id="gap_overlaps_word_tail",
        kind="internal",
        start_ms=690,
        end_ms=1_100,
        duration_ms=410,
        evidence_sources=["waveform", "energy"],
        evidence_ids=[
            "waveform:gap_overlaps_word_tail",
            "energy:gap_overlaps_word_tail",
        ],
        boundary_confidence="ambiguous",
        edit_decision="retain",
        retained_duration_ms=410,
        decision_reason="低能量区压住前一个字的尾音",
    )
    audio = candidate.audio.model_copy(
        update={
            "gap_evidence": [
                *candidate.audio.gap_evidence,
                internal,
                within_word,
                overlaps_word_tail,
            ]
        }
    )

    aligned = dubbing_candidate_alignment.with_alignment_evidence(audio, words)

    assert [word.word_id for word in aligned.aligned_words] == [
        "candidate_word_0001",
        "candidate_word_0002",
        "candidate_word_0003",
        "candidate_word_0004",
    ]
    reviewed = next(
        gap for gap in aligned.gap_evidence if gap.gap_id == "gap_internal"
    )
    assert reviewed.safe_edit_boundary is True
    assert "word_alignment" in reviewed.evidence_sources
    unsafe = next(
        gap for gap in aligned.gap_evidence if gap.gap_id == "gap_inside_word"
    )
    assert unsafe.safe_edit_boundary is None
    assert "word_alignment" not in unsafe.evidence_sources
    partial_overlap = next(
        gap
        for gap in aligned.gap_evidence
        if gap.gap_id == "gap_overlaps_word_tail"
    )
    assert partial_overlap.safe_edit_boundary is None
    assert "word_alignment" not in partial_overlap.evidence_sources


@pytest.mark.parametrize("point_seconds", [0.0, 1.28])
def test_candidate_alignment_preserves_one_point_anchor_without_inventing_span(
    point_seconds,
):
    words = dubbing_candidate_alignment.normalize_aligned_words(
        [
            {
                "text": "停",
                "start_time": point_seconds,
                "end_time": point_seconds,
            }
        ],
        duration_ms=1_785,
    )
    point_ms = round(point_seconds * 1_000)

    assert [word.model_dump(mode="json") for word in words] == [
        {
            "word_id": "candidate_word_0001",
            "text": "停",
            "start_ms": point_ms,
            "end_ms": point_ms,
        }
    ]


def test_candidate_alignment_requires_a_padded_internal_cut_core():
    words = [
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0001",
            text="后",
            start_ms=160,
            end_ms=640,
        ),
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0002",
            text="面",
            start_ms=720,
            end_ms=800,
        ),
    ]
    too_tight = DubbingAudioGapEvidence(
        gap_id="gap_internal_610_740",
        kind="internal",
        start_ms=610,
        end_ms=740,
        duration_ms=130,
        evidence_sources=["waveform", "energy"],
        evidence_ids=["waveform:tight", "energy:tight"],
        boundary_confidence="ambiguous",
        edit_decision="retain",
        retained_duration_ms=130,
        decision_reason="等待逐词边界检查",
    )
    safely_wide = too_tight.model_copy(
        update={
            "gap_id": "gap_internal_640_1040",
            "start_ms": 640,
            "end_ms": 1_040,
            "duration_ms": 400,
            "evidence_ids": ["waveform:wide", "energy:wide"],
        }
    )
    wide_words = [
        words[0],
        words[1].model_copy(update={"start_ms": 1_040, "end_ms": 1_120}),
    ]

    assert dubbing_candidate_alignment.safe_internal_gap_cut(
        too_tight,
        words,
    ) is None
    assert dubbing_candidate_alignment.safe_internal_gap_cut(
        safely_wide,
        wide_words,
    ) == (720, 960)


def test_automatic_gap_split_rejects_zero_length_alignment_anchor(monkeypatch):
    service = DubbingProductionApplicationService()
    monkeypatch.setattr(
        dubbing_candidate_alignment,
        "safe_internal_gap_cut",
        lambda _gap, _words: (400, 600),
    )
    zero_anchor = DubbingCandidateAlignedWord(
        word_id="candidate_word_0001",
        text="停",
        start_ms=0,
        end_ms=0,
    )
    valid_word = DubbingCandidateAlignedWord(
        word_id="candidate_word_0002",
        text="后",
        start_ms=700,
        end_ms=900,
    )

    request = service._build_automatic_gap_split_request(
        SimpleNamespace(
            timeline_clips=[
                {
                    "clip_id": "clip-1",
                    "candidate_id": "candidate-1",
                    "source_start_ms": 0,
                    "source_end_ms": 1_000,
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                }
            ]
        ),
        candidate_id="candidate-1",
        group=SimpleNamespace(subtitle_ids=["localized-1"]),
        frozen=SimpleNamespace(
            audio=SimpleNamespace(aligned_words=[zero_anchor, valid_word]),
            audio_sha256="a" * 64,
            source_revision="b" * 64,
            plan_revision=1,
            expected_spoken_text="停后",
        ),
        reviewed_gaps=[
            SimpleNamespace(
                kind="internal",
                safe_edit_boundary=True,
                edit_decision="remove",
                semantic_role="continuous_phrase",
            )
        ],
        source_pauses=[],
    )

    assert request is None


def test_automatic_gap_split_creates_safe_phrase_boundary_for_source_rhythm(
    monkeypatch,
):
    service = DubbingProductionApplicationService()
    monkeypatch.setattr(
        dubbing_production_service.domain,
        "dubbing_timeline_projection_revision",
        lambda _draft: "c" * 64,
    )
    words = [
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0001",
            text="前",
            start_ms=100,
            end_ms=400,
        ),
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0002",
            text="后",
            start_ms=700,
            end_ms=1_000,
        ),
    ]
    retained_gap = DubbingAudioGapEvidence(
        gap_id="gap-semantic",
        kind="internal",
        start_ms=400,
        end_ms=700,
        duration_ms=300,
        evidence_sources=["word_alignment"],
        evidence_ids=["word_alignment:gap"],
        boundary_confidence="clear",
        edit_decision="retain",
        retained_duration_ms=300,
        decision_reason="语义停顿",
        safe_edit_boundary=True,
        semantic_role="semantic_boundary",
        semantic_pause_scale="normal",
    )

    request = service._build_automatic_gap_split_request(
        SimpleNamespace(
            timeline_clips=[
                {
                    "clip_id": "clip-1",
                    "candidate_id": "candidate-1",
                    "source_start_ms": 0,
                    "source_end_ms": 1_200,
                    "start_ms": 1_000,
                    "end_ms": 2_200,
                }
            ]
        ),
        candidate_id="candidate-1",
        group=SimpleNamespace(subtitle_ids=["localized-1"]),
        frozen=SimpleNamespace(
            audio=SimpleNamespace(aligned_words=words),
            audio_sha256="a" * 64,
            source_revision="b" * 64,
            plan_revision=1,
            expected_spoken_text="前，后",
        ),
        reviewed_gaps=[retained_gap],
        source_pauses=[
            dubbing_gap_adjudication.SourcePauseRhythmEvidence(
                duration_ms=900,
                right_word_start_ms=5_000,
            )
        ],
    )

    assert request is not None
    slices = request.commands[0].slices
    assert [
        (item.source_start_ms, item.source_end_ms)
        for item in slices
    ] == [(0, 550), (550, 1_200)]
    assert [item.alignment_word_ids for item in slices] == [
        ["candidate_word_0001"],
        ["candidate_word_0002"],
    ]


def test_automatic_source_rhythm_split_uses_safe_core_when_gap_edge_overlaps_word(
    monkeypatch,
):
    service = DubbingProductionApplicationService()
    monkeypatch.setattr(
        dubbing_production_service.domain,
        "dubbing_timeline_projection_revision",
        lambda _draft: "c" * 64,
    )
    words = [
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0001",
            text="嚯",
            start_ms=160,
            end_ms=400,
        ),
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0002",
            text="快",
            start_ms=640,
            end_ms=800,
        ),
    ]
    retained_gap = DubbingAudioGapEvidence(
        gap_id="gap-semantic-with-slop",
        kind="internal",
        start_ms=470,
        end_ms=670,
        duration_ms=200,
        evidence_sources=["waveform", "word_alignment"],
        evidence_ids=["word_alignment:gap"],
        boundary_confidence="clear",
        edit_decision="retain",
        retained_duration_ms=200,
        decision_reason="语义停顿",
        safe_edit_boundary=True,
        semantic_role="semantic_boundary",
        semantic_pause_scale="normal",
    )

    request = service._build_automatic_gap_split_request(
        SimpleNamespace(
            timeline_clips=[
                {
                    "clip_id": "clip-1",
                    "candidate_id": "candidate-1",
                    "source_start_ms": 80,
                    "source_end_ms": 900,
                    "start_ms": 1_000,
                    "end_ms": 1_820,
                }
            ]
        ),
        candidate_id="candidate-1",
        group=SimpleNamespace(subtitle_ids=["localized-1"]),
        frozen=SimpleNamespace(
            audio=SimpleNamespace(aligned_words=words),
            audio_sha256="a" * 64,
            source_revision="b" * 64,
            plan_revision=1,
            expected_spoken_text="嚯，快",
        ),
        reviewed_gaps=[retained_gap],
        source_pauses=[
            dubbing_gap_adjudication.SourcePauseRhythmEvidence(
                duration_ms=900,
                right_word_start_ms=5_000,
            )
        ],
    )

    assert request is not None
    slices = request.commands[0].slices
    assert [(item.source_start_ms, item.source_end_ms) for item in slices] == [
        (80, 520),
        (520, 900),
    ]
    assert [item.alignment_word_ids for item in slices] == [
        ["candidate_word_0001"],
        ["candidate_word_0002"],
    ]


def test_candidate_alignment_rejects_split_with_invented_word_ids():
    words = [
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0001",
            text="前",
            start_ms=100,
            end_ms=400,
        ),
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0002",
            text="后",
            start_ms=600,
            end_ms=900,
        ),
    ]
    slices = [
        SimpleNamespace(
            alignment_word_ids=["candidate_word_0001"],
            speech_start_ms=100,
            speech_end_ms=400,
        ),
        SimpleNamespace(
            alignment_word_ids=["invented_word"],
            speech_start_ms=600,
            speech_end_ms=900,
        ),
    ]

    with pytest.raises(ValueError, match="完整且唯一覆盖"):
        dubbing_candidate_alignment.validate_slice_alignment(
            slices=slices,
            words=words,
            source_start_ms=0,
            source_end_ms=1_000,
        )


def test_candidate_alignment_rejects_crop_that_drops_final_word():
    words = [
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0001",
            text="运",
            start_ms=6_800,
            end_ms=6_880,
        ),
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0002",
            text="动",
            start_ms=6_880,
            end_ms=7_120,
        ),
    ]
    slices = [
        SimpleNamespace(
            source_start_ms=6_400,
            source_end_ms=6_900,
            alignment_word_ids=["candidate_word_0001"],
            speech_start_ms=6_800,
            speech_end_ms=6_880,
        )
    ]

    with pytest.raises(ValueError, match="完整覆盖候选的全部发音字词"):
        dubbing_candidate_alignment.validate_slice_alignment(
            slices=slices,
            words=words,
            source_start_ms=6_400,
            source_end_ms=6_900,
        )


def test_candidate_projection_rejects_final_clip_that_cuts_through_last_word():
    words = [
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0001",
            text="手",
            start_ms=5_920,
            end_ms=6_160,
        ),
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0002",
            text="持",
            start_ms=6_160,
            end_ms=6_560,
        ),
    ]

    with pytest.raises(ValueError, match="切入了发音字词"):
        dubbing_candidate_alignment.validate_candidate_clip_coverage(
            clips=[
                {
                    "clip_id": "clip-final",
                    "source_start_ms": 5_200,
                    "source_end_ms": 6_180,
                    "start_ms": 10_000,
                    "end_ms": 10_980,
                }
            ],
            words=words,
            speech_start_ms=5_920,
            speech_end_ms=6_560,
        )


def test_outer_gap_projection_repairs_a_previously_cropped_final_word():
    word = DubbingCandidateAlignedWord(
        word_id="candidate_word_0001",
        text="持",
        start_ms=6_160,
        end_ms=6_560,
    )
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip-final",
                "track_id": "dub",
                "candidate_id": "candidate-1",
                "start_ms": 10_000,
                "end_ms": 10_980,
                "source_start_ms": 5_200,
                "source_end_ms": 6_180,
            }
        ]
    )
    frozen = SimpleNamespace(
        audio=SimpleNamespace(
            duration_ms=6_700,
            speech_start_ms=5_920,
            speech_end_ms=6_560,
            aligned_words=[word],
        ),
        target_start_ms=10_720,
    )

    repaired = dubbing_production_service._with_trimmed_automatic_outer_gaps(
        draft,
        candidate_id="candidate-1",
        frozen=frozen,
        reviewed_gaps=[],
    )

    [clip] = repaired.timeline_clips
    assert clip["source_end_ms"] == 6_640
    assert clip["end_ms"] == 11_440
    assert clip["alignment_trail_ms"] == 80


def test_outer_gap_projection_keeps_existing_speech_anchor_when_repairing_edges():
    words = [
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0001",
            text="镜",
            start_ms=160,
            end_ms=360,
        ),
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0002",
            text="动",
            start_ms=6_880,
            end_ms=7_120,
        ),
    ]
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip-first",
                "track_id": "dub",
                "candidate_id": "candidate-1",
                "start_ms": 10_000,
                "end_ms": 11_180,
                "source_start_ms": 100,
                "source_end_ms": 1_280,
            },
            {
                "clip_id": "clip-last",
                "track_id": "dub",
                "candidate_id": "candidate-1",
                "start_ms": 15_420,
                "end_ms": 15_920,
                "source_start_ms": 6_400,
                "source_end_ms": 6_900,
            },
        ]
    )
    frozen = SimpleNamespace(
        audio=SimpleNamespace(
            duration_ms=7_300,
            speech_start_ms=160,
            speech_end_ms=7_120,
            aligned_words=words,
        ),
        target_start_ms=9_000,
    )

    repaired = dubbing_production_service._with_trimmed_automatic_outer_gaps(
        draft,
        candidate_id="candidate-1",
        frozen=frozen,
        reviewed_gaps=[],
        preserve_existing_placement=True,
    )

    first, last = repaired.timeline_clips
    assert first["source_start_ms"] == 80
    assert first["start_ms"] == 9_980
    assert first["start_ms"] + 80 == 10_060
    assert last["source_end_ms"] == 7_200
    assert last["end_ms"] == 16_220


def test_existing_crop_does_not_restore_non_target_edge_vocals():
    clip = {
        "clip_id": "edited", "track_id": "dub", "candidate_id": "candidate-1",
        "start_ms": 1_000, "end_ms": 2_460,
        "source_start_ms": 220, "source_end_ms": 1_680,
    }
    frozen = SimpleNamespace(audio=SimpleNamespace(
        duration_ms=1_900, speech_start_ms=90, speech_end_ms=1_800,
        aligned_words=[DubbingCandidateAlignedWord(
            word_id="word-1", text="完整台词", start_ms=240, end_ms=1_600,
        )],
    ), target_start_ms=1_020)
    result = dubbing_production_service._with_trimmed_automatic_outer_gaps(
        VideoLocalizationDraft(timeline_clips=[clip]),
        candidate_id="candidate-1", frozen=frozen, reviewed_gaps=[],
        preserve_existing_placement=True,
        preserve_verified_edge_edit=True,
    )
    assert result.timeline_clips == [clip]


def test_new_candidate_source_onset_lock_moves_the_whole_projection_to_target():
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip-first",
                "track_id": "dub",
                "candidate_id": "candidate-1",
                "start_ms": 734,
                "end_ms": 1_734,
                "source_start_ms": 80,
                "source_end_ms": 1_080,
            },
            {
                "clip_id": "clip-second",
                "track_id": "dub",
                "candidate_id": "candidate-1",
                "start_ms": 1_894,
                "end_ms": 2_894,
                "source_start_ms": 1_240,
                "source_end_ms": 2_240,
            },
        ]
    )

    locked = dubbing_production_service._with_locked_candidate_source_onset(
        draft,
        candidate_id="candidate-1",
        speech_start_ms=160,
        speech_end_ms=2_160,
        target_start_ms=1_080,
    )

    bounds = candidate_audible_timeline_bounds(
        locked.timeline_clips,
        speech_start_ms=160,
        speech_end_ms=2_160,
    )
    assert bounds is not None
    assert bounds[0] == 1_080
    assert [clip["start_ms"] for clip in locked.timeline_clips] == [1_000, 2_160]
    assert [clip["end_ms"] for clip in locked.timeline_clips] == [2_000, 3_160]


def test_word_onset_wins_over_later_vad_while_outer_trim_protects_both():
    word = DubbingCandidateAlignedWord(
        word_id="candidate_word_0001", text="字", start_ms=160, end_ms=940,
    )
    audio = SimpleNamespace(
        duration_ms=1_100,
        speech_start_ms=190,
        speech_end_ms=900,
        aligned_words=[word],
    )
    onset_ms, protected_end_ms = (
        dubbing_production_service._candidate_word_anchor_and_protected_end(audio)
    )
    assert (onset_ms, protected_end_ms) == (160, 940)

    repaired = dubbing_production_service._with_trimmed_automatic_outer_gaps(
        VideoLocalizationDraft(timeline_clips=[{
            "clip_id": "candidate-clip",
            "track_id": "dub",
            "candidate_id": "candidate-1",
            "start_ms": 734,
            "end_ms": 1_534,
            "source_start_ms": 0,
            "source_end_ms": 800,
        }]),
        candidate_id="candidate-1",
        frozen=SimpleNamespace(audio=audio, target_start_ms=1_080),
        reviewed_gaps=[DubbingAudioGapEvidence(
            gap_id="gap-head", kind="leading", start_ms=0, end_ms=190,
            duration_ms=190, evidence_sources=["energy"], evidence_ids=["energy:head"],
            boundary_confidence="clear", edit_decision="remove",
            retained_duration_ms=0, decision_reason="保留词首保护余量",
            safe_edit_boundary=True,
        )],
    )

    [clip] = repaired.timeline_clips
    assert clip["start_ms"] == 1_000
    assert clip["source_start_ms"] == 80
    assert clip["source_end_ms"] == 1_020
    assert clip["end_ms"] == 1_940


def test_retained_internal_gap_is_restored_without_moving_earlier_audio():
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip-left",
                "track_id": "dub",
                "candidate_id": "candidate-1",
                "start_ms": 10_000,
                "end_ms": 11_000,
                "source_start_ms": 0,
                "source_end_ms": 1_000,
                "alignment_trail_ms": 80,
            },
            {
                "clip_id": "clip-right",
                "track_id": "dub",
                "candidate_id": "candidate-1",
                "start_ms": 11_000,
                "end_ms": 12_000,
                "source_start_ms": 1_400,
                "source_end_ms": 2_400,
                "dubbing_timeline_gap_before_ms": 0,
            },
        ]
    )
    retained = DubbingAudioGapEvidence(
        gap_id="gap-retained",
        kind="internal",
        start_ms=900,
        end_ms=1_500,
        duration_ms=600,
        evidence_sources=["word_alignment"],
        evidence_ids=["word_alignment:gap"],
        boundary_confidence="clear",
        edit_decision="retain",
        retained_duration_ms=600,
        decision_reason="语义停顿",
        safe_edit_boundary=True,
    )

    restored = dubbing_production_service._with_restored_retained_internal_gaps(
        draft,
        candidate_id="candidate-1",
        reviewed_gaps=[retained],
    )

    left, right = restored.timeline_clips
    assert left["start_ms"] == 10_000
    assert left["source_end_ms"] == 1_400
    assert left["end_ms"] == 11_400
    assert right["start_ms"] == 11_400
    assert right["end_ms"] == 12_400
    assert right["dubbing_timeline_gap_before_ms"] == 0


def test_gap_processing_reads_punctuation_from_expected_text_and_keeps_breath():
    words = [
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0001",
            text="了",
            start_ms=100,
            end_ms=300,
        ),
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0002",
            text="没",
            start_ms=1_130,
            end_ms=1_300,
        ),
    ]
    gap = DubbingAudioGapEvidence(
        gap_id="gap_internal",
        kind="internal",
        start_ms=300,
        end_ms=1_130,
        duration_ms=830,
        evidence_sources=["waveform", "energy", "word_alignment"],
        evidence_ids=["waveform:gap", "energy:gap", "word_alignment:gap"],
        boundary_confidence="clear",
        edit_decision="retain",
        retained_duration_ms=830,
        decision_reason="等待语义判断",
        safe_edit_boundary=True,
    )

    [reviewed] = dubbing_gap_adjudication.process_gap_evidence(
        [gap],
        aligned_words=words,
        expected_spoken_text="了，没",
        source_pauses=[
            dubbing_gap_adjudication.SourcePauseRhythmEvidence(
                duration_ms=960,
                right_word_start_ms=5_000,
            )
        ],
    )

    assert reviewed.edit_decision == "retain"
    assert reviewed.retained_duration_ms == 830
    assert reviewed.semantic_role == "semantic_boundary"
    assert reviewed.semantic_pause_scale == "deliberate"
    assert "source_pause:960ms" in reviewed.review_evidence_ids


@pytest.mark.parametrize("latest_end_ms, shift", [(None, 600), (12_700, 300), (12_400, 0)])
def test_source_rhythm_spacing_places_later_phrase_on_source_anchor(latest_end_ms, shift):
    words = [
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0001",
            text="前",
            start_ms=100,
            end_ms=900,
        ),
        DubbingCandidateAlignedWord(
            word_id="candidate_word_0002",
            text="后",
            start_ms=1_400,
            end_ms=2_300,
        ),
    ]
    gap = DubbingAudioGapEvidence(
        gap_id="gap-retained",
        kind="internal",
        start_ms=900,
        end_ms=1_400,
        duration_ms=500,
        evidence_sources=["word_alignment"],
        evidence_ids=["word_alignment:gap"],
        boundary_confidence="clear",
        edit_decision="retain",
        retained_duration_ms=500,
        decision_reason="语义停顿",
        safe_edit_boundary=True,
    )
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip-left",
                "track_id": "dub",
                "candidate_id": "candidate-1",
                "start_ms": 10_000,
                "end_ms": 11_400,
                "source_start_ms": 0,
                "source_end_ms": 1_400,
                "dubbing_timeline_gap_before_ms": 0,
            },
            {
                "clip_id": "clip-right",
                "track_id": "dub",
                "candidate_id": "candidate-1",
                "start_ms": 11_400,
                "end_ms": 12_400,
                "source_start_ms": 1_400,
                "source_end_ms": 2_400,
                "dubbing_timeline_gap_before_ms": 0,
            },
        ]
    )

    spaced = dubbing_production_service._with_source_rhythm_spacing(
        draft,
        candidate_id="candidate-1",
        reviewed_gaps=[gap],
        aligned_words=words,
        source_pauses=[
            dubbing_gap_adjudication.SourcePauseRhythmEvidence(
                duration_ms=1_000,
                right_word_start_ms=12_000,
            )
        ],
        expected_spoken_text="前，后",
        latest_end_ms=latest_end_ms,
    )

    left, right = spaced.timeline_clips
    assert left["start_ms"] == 10_000
    assert left["end_ms"] == 11_400
    assert right["start_ms"] == 11_400 + shift
    assert right["end_ms"] == 12_400 + shift
    assert right["dubbing_timeline_gap_before_ms"] == shift


def test_candidate_cqc_warns_for_ambiguous_gap_without_listening_and_waveform_review():
    candidate = _candidate()
    assert candidate.audio is not None
    ambiguous = DubbingAudioGapEvidence(
        gap_id="gap_internal",
        kind="internal",
        start_ms=700,
        end_ms=1_050,
        duration_ms=350,
        evidence_sources=["energy"],
        evidence_ids=["energy:gap_internal"],
        boundary_confidence="ambiguous",
        edit_decision="shorten",
        retained_duration_ms=120,
        decision_reason="疑似过长停顿",
        safe_edit_boundary=None,
    )
    audio = candidate.audio.model_copy(
        update={"gap_evidence": [*candidate.audio.gap_evidence, ambiguous]}
    )

    report = evaluate_candidate(candidate.model_copy(update={"audio": audio}))

    finding = next(
        item
        for item in report.findings
        if item.code == "CANDIDATE_AMBIGUOUS_GAP_REVIEW_REQUIRED"
    )
    assert finding.severity == "warning"


def test_candidate_cqc_does_not_require_edit_evidence_for_retained_ambiguous_gap():
    candidate = _candidate()
    assert candidate.audio is not None
    retained = DubbingAudioGapEvidence(
        gap_id="gap_internal",
        kind="internal",
        start_ms=700,
        end_ms=1_050,
        duration_ms=350,
        evidence_sources=["waveform", "energy"],
        evidence_ids=["waveform:gap_internal", "energy:gap_internal"],
        boundary_confidence="ambiguous",
        edit_decision="retain",
        retained_duration_ms=350,
        decision_reason="自动分析无法确认边界，保持原音频不做剪辑",
        safe_edit_boundary=None,
    )
    audio = candidate.audio.model_copy(
        update={"gap_evidence": [*candidate.audio.gap_evidence, retained]}
    )

    report = evaluate_candidate(candidate.model_copy(update={"audio": audio}))

    assert report.overall_status == "passed"
    assert "CANDIDATE_AMBIGUOUS_GAP_REVIEW_REQUIRED" not in {
        finding.code for finding in report.findings
    }


def test_candidate_cqc_accepts_reviewed_ambiguous_gap_with_reproducible_evidence():
    candidate = _candidate()
    assert candidate.audio is not None
    reviewed = DubbingAudioGapEvidence(
        gap_id="gap_internal",
        kind="internal",
        start_ms=700,
        end_ms=1_050,
        duration_ms=350,
        evidence_sources=["waveform", "energy", "listening"],
        evidence_ids=[
            "waveform:gap_internal",
            "energy:gap_internal",
            "listening:gap_internal",
        ],
        boundary_confidence="ambiguous",
        edit_decision="shorten",
        retained_duration_ms=120,
        decision_reason="波形与试听共同确认中间区间为可缩短静音",
        safe_edit_boundary=True,
        review_evidence_ids=["waveform:gap_internal", "listening:gap_internal"],
    )
    audio = candidate.audio.model_copy(
        update={"gap_evidence": [*candidate.audio.gap_evidence, reviewed]}
    )

    report = evaluate_candidate(candidate.model_copy(update={"audio": audio}))

    assert report.overall_status == "passed"


def test_candidate_cqc_warns_for_multiple_edits_inside_continuous_phrase():
    candidate = _candidate()
    assert candidate.audio is not None
    continuous_gaps = [
        DubbingAudioGapEvidence(
            gap_id=f"gap_continuous_{index}",
            kind="internal",
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=end_ms - start_ms,
            evidence_sources=["waveform", "energy", "vad", "word_alignment"],
            evidence_ids=[
                f"waveform:gap_continuous_{index}",
                f"energy:gap_continuous_{index}",
                f"vad:gap_continuous_{index}",
                f"alignment:gap_continuous_{index}",
            ],
            boundary_confidence="clear",
            edit_decision="shorten",
            retained_duration_ms=60,
            decision_reason="语言模型确认这里仍属于同一段连续话术",
            safe_edit_boundary=True,
            review_evidence_ids=[f"llm:gap_continuous_{index}"],
            semantic_role="continuous_phrase",
        )
        for index, (start_ms, end_ms) in enumerate(
            [(500, 700), (1_200, 1_400)],
            start=1,
        )
    ]
    audio = candidate.audio.model_copy(
        update={
            "gap_evidence": [
                *candidate.audio.gap_evidence,
                *continuous_gaps,
            ]
        }
    )

    report = evaluate_candidate(candidate.model_copy(update={"audio": audio}))

    finding = next(
        item
        for item in report.findings
        if item.code == "CANDIDATE_OVER_FRAGMENTED_PHRASE"
    )
    assert finding.severity == "warning"


def test_candidate_cqc_allows_user_approved_rendered_timeline_fragmentation():
    candidate = _candidate()
    assert candidate.audio is not None
    continuous_gaps = [
        DubbingAudioGapEvidence(
            gap_id=f"gap_continuous_{index}",
            kind="internal",
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=end_ms - start_ms,
            evidence_sources=[
                "waveform",
                "energy",
                "word_alignment",
                "listening",
            ],
            evidence_ids=[
                f"waveform:gap_continuous_{index}",
                f"energy:gap_continuous_{index}",
                f"alignment:gap_continuous_{index}",
                f"user-listening:timeline:gap_continuous_{index}",
            ],
            boundary_confidence="clear",
            edit_decision="shorten",
            retained_duration_ms=60,
            decision_reason="用户已复听当前时间线，确认剪辑后连续自然",
            safe_edit_boundary=True,
            review_evidence_ids=[
                f"user-listening:timeline:gap_continuous_{index}"
            ],
            semantic_role="continuous_phrase",
        )
        for index, (start_ms, end_ms) in enumerate(
            [(500, 700), (1_200, 1_400)],
            start=1,
        )
    ]
    audio = candidate.audio.model_copy(
        update={
            "gap_evidence": [
                *candidate.audio.gap_evidence,
                *continuous_gaps,
            ]
        }
    )
    reviews = [
        DubbingSubjectiveReview(
            dimension=dimension,
            status="passed",
            evidence_id=f"user-listening:timeline:{dimension}",
        )
        for dimension in (
            "meaning",
            "pronunciation",
            "prosody_parse",
            "voice_match",
            "naturalness",
        )
    ]

    report = evaluate_candidate(
        candidate.model_copy(
            update={"audio": audio, "subjective_reviews": reviews}
        )
    )

    assert report.overall_status == "passed"
    finding = next(
        item
        for item in report.findings
        if item.code == "CANDIDATE_OVER_FRAGMENTED_PHRASE"
    )
    assert finding.severity == "warning"


def test_candidate_cqc_warns_for_rate_outside_normal_band_and_project_spread():
    candidate = _candidate()
    assert candidate.audio is not None
    audio = candidate.audio.model_copy(
        update={
            "speaking_rate_ratio": 1.45,
            "project_speaking_rate_ratio_min": 1.0,
            "project_speaking_rate_ratio_max": 1.45,
        }
    )

    report = evaluate_candidate(candidate.model_copy(update={"audio": audio}))

    codes = {finding.code for finding in report.findings}
    finding = next(
        item
        for item in report.findings
        if item.code == "CANDIDATE_SPEAKING_RATE_OUT_OF_RANGE"
    )
    assert finding.severity == "warning"
    assert "CANDIDATE_SPEAKING_RATE_OUT_OF_RANGE" in codes


def test_candidate_cqc_allows_explicit_content_speed_exception():
    candidate = _candidate()
    assert candidate.audio is not None
    audio = candidate.audio.model_copy(
        update={
            "speaking_rate_ratio": 1.4,
            "project_speaking_rate_ratio_min": 1.0,
            "project_speaking_rate_ratio_max": 1.4,
            "content_speed_exception_reason": "来源角色在该句明确快速连读",
            "content_speed_exception_evidence_ids": [
                "source-word-alignment:unit_1",
                "listening:candidate_1",
            ],
        }
    )

    report = evaluate_candidate(candidate.model_copy(update={"audio": audio}))

    assert report.overall_status == "passed"
    assert "CANDIDATE_CONTENT_SPEED_EXCEPTION_APPLIED" in {
        finding.code for finding in report.findings
    }


def test_candidate_cqc_never_treats_asr_match_as_subjective_listening():
    report = evaluate_candidate(_candidate(subjective_reviews=[]))

    assert report.automatic_status == "passed"
    assert report.subjective_status == "not_reviewed"
    assert report.overall_status == "needs_review"
    assert report.recommended_action == "listen_and_review"
    assert "SUBJECTIVE_LISTENING_REVIEW_REQUIRED" in {finding.code for finding in report.findings}


def test_candidate_cqc_complete_listening_adjudicates_automatic_warnings():
    report = evaluate_candidate(
        _candidate(
            audio=DubbingAutomaticAudioEvidence(
                duration_ms=2_000,
                peak_dbfs=-1.0,
                clipping_ratio=0,
                leading_silence_ms=50,
                trailing_silence_ms=80,
                internal_pauses=[
                    DubbingPauseEvidence(
                        start_ms=700,
                        end_ms=900,
                        duration_ms=200,
                        expected_semantic_boundary=False,
                    )
                ],
                expected_pause_baseline_ms=None,
            ),
            subjective_reviews=_all_subjective_reviews(),
        )
    )

    assert report.automatic_status == "warning"
    assert report.subjective_status == "passed"
    assert report.overall_status == "passed"
    assert report.recommended_action == "accept"


def test_candidate_cqc_evidence_backed_listening_can_adjudicate_asr_name_spelling():
    base = _candidate()
    assert base.audio is not None
    expected = "大家好，我是 Adil。用 Seedance 制作。"
    audio = base.audio.model_copy(
        update={
            "aligned_words": [
                DubbingCandidateAlignedWord(
                    word_id=f"word_{index:02d}",
                    text=text,
                    start_ms=index * 80,
                    end_ms=(index + 1) * 80,
                )
                for index, text in enumerate(
                    ["大", "家", "好", "我", "是", "Adil", "用", "Seedance", "制", "作"]
                )
            ]
        }
    )
    reviews = [
        DubbingSubjectiveReview(
            dimension=dimension,
            status="passed",
            note="已按实际发音复听",
            evidence_id=f"user-listening:{dimension}",
        )
        for dimension in (
            "meaning",
            "pronunciation",
            "prosody_parse",
            "voice_match",
            "naturalness",
        )
    ]

    report = evaluate_candidate(
        _candidate(
            expected_spoken_text=expected,
            candidate_transcript="大家好我是 Adele，用 C 丹斯制作。",
            audio=audio,
            subjective_reviews=reviews,
        )
    )

    assert report.automatic_status == "warning"
    assert report.subjective_status == "passed"
    assert report.overall_status == "passed"
    assert {
        finding.code
        for finding in report.findings
        if finding.severity == "blocking"
    } == set()


def test_candidate_cqc_raw_reference_prefix_is_advisory():
    report = evaluate_candidate(
        _candidate(
            reference_transcript="hello 这个是原始参考音",
            candidate_transcript="hello 这个结果可以使用",
        )
    )

    assert report.automatic_status == "warning"
    assert "REFERENCE_AUDIO_CONTAMINATION_SUSPECTED" in {finding.code for finding in report.findings}


def test_transcript_comparison_does_not_flag_shared_latin_letters_as_leakage():
    comparison = compare_transcripts(
        "CEO 正在发言",
        "CEO正在发言",
        reference_text="another unrelated english sentence",
    )

    assert comparison.reference_only_extra_tokens == []


def test_transcript_comparison_does_not_flag_equivalent_number_forms_as_leakage():
    comparison = compare_transcripts(
        "用 Seedance 二点五 写提示词",
        "用 Sea Dance 2.5 写提示词",
        reference_text="prompt for Seedance 2.5",
    )

    assert comparison.reference_only_extra_tokens == []
    assert comparison.coverage_ratio == 1.0
    assert comparison.extra_ratio == 0.0


def test_transcript_comparison_accepts_generic_split_english_brand_asr_spelling():
    comparison = compare_transcripts(
        "请打开 NovaForge Studio 和 SeeDream Pro。",
        "请打开 Nova Forge Studio 和 C Dream Pro。",
        reference_text="Open NovaForge Studio and SeeDream Pro",
    )

    assert comparison.coverage_ratio == 1.0
    assert comparison.extra_ratio == 0.0
    assert comparison.missing_tokens == []
    assert comparison.extra_tokens == []
    assert comparison.reference_only_extra_tokens == []


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ("一九二九和二零零零", "1929和2000"),
        ("C E O 与 C E O 对话", "CEO与CEO对话"),
        ("他说得对", "他说的对"),
        ("它会成功", "他会成功"),
        (
            "二零零八年，标普五百指数暴跌近百分之四十，仍取得百分之九点五的正收益。",
            "2008年，标普五百指数暴跌近40%，仍取得9.5%的正收益。",
        ),
        (
            "版本二点零支持四 K，并能输出一零八零 P。",
            "版本2.0支持4K，并能输出1080P。",
        ),
        (
            "Model 二点零支持四 K。",
            "Model 2.0支持4K。",
        ),
    ],
)
def test_transcript_comparison_accepts_equivalent_asr_written_forms(
    expected: str,
    actual: str,
):
    comparison = compare_transcripts(expected, actual)

    assert comparison.coverage_ratio == 1.0
    assert comparison.extra_ratio == 0.0
    assert comparison.missing_tokens == []
    assert comparison.extra_tokens == []


def test_transcript_pronunciation_tokens_accept_true_homophone_but_keep_tones():
    assert transcript_pronunciation_tokens("企业持续盈利") == transcript_pronunciation_tokens("企业持续营利")
    assert transcript_pronunciation_tokens("身家越高") != transcript_pronunciation_tokens("身价越高")

    homophone = compare_transcripts("企业持续盈利", "企业持续营利")
    assert homophone.coverage_ratio == 1.0
    assert homophone.extra_ratio == 0.0

    different_tone = compare_transcripts("身家越高", "身价越高")
    assert different_tone.coverage_ratio < 1.0
    assert different_tone.extra_ratio > 0.0


def test_transcript_comparison_isolates_name_substitutions_from_number_formatting():
    comparison = compare_transcripts(
        "ModelCore 二点零支持四 K，ModelCore 二点五支持一零八零 P。",
        "ModelCorp 2.0支持4K，ModelCorp 2.5支持1080P。",
    )

    assert comparison.missing_tokens == ["modelcore", "modelcore"]
    assert comparison.extra_tokens == ["modelcorp", "modelcorp"]


def test_transcript_comparison_accepts_spelled_url_when_asr_joins_separators():
    comparison = compare_transcripts(
        "访问 E X A M P L E.C O M/D E M O。",
        "访问 examplecomdemo。",
    )

    assert comparison.coverage_ratio == 1.0
    assert comparison.extra_ratio == 0.0
    assert comparison.missing_tokens == []
    assert comparison.extra_tokens == []


def test_candidate_cqc_keeps_one_noncritical_short_asr_substitution_reviewable():
    report = evaluate_candidate(
        _candidate(
            expected_spoken_text="然后你还得为这笔利息缴税。",
            candidate_transcript="然后你还得为这笔利息交税。",
            subjective_reviews=_all_subjective_reviews(),
        )
    )

    assert report.automatic_status == "warning"
    assert report.overall_status == "passed"
    assert report.recommended_action == "accept"


def test_candidate_cqc_uses_aligned_words_when_asr_transcript_is_unavailable():
    aligned_text = "这个结果可以使用"
    report = evaluate_candidate(
        _candidate(
            candidate_transcript="",
            audio=DubbingAutomaticAudioEvidence(
                duration_ms=2_000,
                peak_dbfs=-1.0,
                clipping_ratio=0,
                leading_silence_ms=50,
                trailing_silence_ms=80,
                speech_start_ms=50,
                speech_end_ms=1_920,
                speech_span_ms=1_870,
                voiced_spans=[DubbingVoicedSpan(start_ms=50, end_ms=1_920)],
                aligned_words=[
                    DubbingCandidateAlignedWord(
                        word_id=f"word_{index}",
                        text=text,
                        start_ms=100 + index * 100,
                        end_ms=180 + index * 100,
                    )
                    for index, text in enumerate(aligned_text)
                ],
                voiced_duration_ms=1_870,
            ),
            subjective_reviews=_all_subjective_reviews(),
        )
    )

    codes = {finding.code for finding in report.findings}
    assert report.transcript.coverage_ratio == 1.0
    assert "CANDIDATE_TRANSCRIPT_MISSING_CONTENT" not in codes
    assert "CANDIDATE_TRANSCRIPT_ALIGNMENT_FALLBACK_USED" in codes
    assert report.overall_status == "passed"
    assert report.recommended_action == "accept"


def test_candidate_cqc_keeps_one_two_token_long_asr_span_reviewable():
    report = evaluate_candidate(
        _candidate(
            expected_spoken_text="就又能逐渐积累直到偿债压力开始挤压其他支出。",
            candidate_transcript="就又能逐渐积累直到强在压力开始挤压其他支出。",
            subjective_reviews=_all_subjective_reviews(),
        )
    )

    assert report.automatic_status == "warning"
    assert report.overall_status == "passed"
    assert report.recommended_action == "accept"


def test_candidate_cqc_raw_numeric_difference_is_advisory():
    report = evaluate_candidate(
        _candidate(
            expected_spoken_text="利率大约百分之四。",
            candidate_transcript="利率大约百分之五。",
            subjective_reviews=_all_subjective_reviews(),
        )
    )

    assert report.automatic_status == "warning"
    assert report.overall_status == "passed"
    assert report.recommended_action != "regenerate"


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ("财政赤字这么大，那钱从哪儿来？", "财政赤字这么大，那钱从哪来？"),
        ("政府要做这些事，内部也需要人才。", "政府要做这些事儿，内部也需要人才。"),
    ],
)
def test_candidate_cqc_keeps_optional_erhua_variant_reviewable(
    expected: str,
    actual: str,
):
    report = evaluate_candidate(
        _candidate(
            expected_spoken_text=expected,
            candidate_transcript=actual,
            subjective_reviews=_all_subjective_reviews(),
        )
    )

    assert report.automatic_status == "warning"
    assert report.overall_status == "passed"
    assert report.recommended_action == "accept"


def test_candidate_cqc_raw_semantic_character_difference_is_advisory():
    report = evaluate_candidate(
        _candidate(
            expected_spoken_text="他一直很爱自己的儿子。",
            candidate_transcript="他一直很爱自己的子。",
            subjective_reviews=_all_subjective_reviews(),
        )
    )

    assert report.automatic_status == "warning"
    assert report.overall_status == "passed"


def test_candidate_cqc_blocks_wrong_internal_pause_without_safe_hard_cut():
    report = evaluate_candidate(
        _candidate(
            audio=DubbingAutomaticAudioEvidence(
                duration_ms=2_000,
                peak_dbfs=-1.0,
                clipping_ratio=0,
                leading_silence_ms=20,
                trailing_silence_ms=20,
                expected_pause_baseline_ms=120,
                internal_pauses=[
                    DubbingPauseEvidence(
                        start_ms=500,
                        end_ms=900,
                        duration_ms=400,
                        expected_semantic_boundary=False,
                        safe_edit_boundary=False,
                    )
                ],
            )
        )
    )

    finding = next(finding for finding in report.findings if finding.code == "CANDIDATE_UNEXPECTED_LONG_INTERNAL_PAUSE")
    assert report.overall_status == "failed"
    assert "重新生成" in (finding.recommended_action or "")


def test_candidate_cqc_blocks_audio_that_runs_into_demo_scene():
    report = evaluate_candidate(
        _candidate(
            planned_scene_end_ms=2_900,
            placement_end_ms=3_200,
        )
    )

    assert report.overall_status == "failed"
    assert report.recommended_action == "repair_text_or_grouping"
    assert "CANDIDATE_OVERRUNS_SCENE" in {finding.code for finding in report.findings}


def test_candidate_timeline_repair_does_not_use_raw_transcript_as_admission():
    report = evaluate_candidate(
        _candidate(
            expected_spoken_text="完整内容不能丢字",
            candidate_transcript="完整内容",
            subjective_reviews=_all_subjective_reviews(),
        )
    )

    assert report.automatic_status == "warning"


def test_candidate_audio_measurement_reports_only_deterministic_pause_evidence(
    tmp_path: Path,
):
    sample_rate = 16_000
    tone = 0.25 * np.sin(2 * np.pi * 220 * np.arange(round(sample_rate * 0.3)) / sample_rate)
    silence = np.zeros(round(sample_rate * 0.2), dtype=np.float32)
    audio = np.concatenate([silence, tone, silence, tone, silence])
    audio_path = tmp_path / "candidate.wav"
    sf.write(audio_path, audio, sample_rate)

    evidence = analyze_dubbing_candidate_audio(
        audio_path,
        expected_pause_baseline_ms=100,
        max_leading_silence_ms=120,
        max_trailing_silence_ms=120,
    )

    assert evidence.duration_ms == 1_200
    assert evidence.leading_silence_ms >= 180
    assert evidence.trailing_silence_ms >= 180
    assert len(evidence.internal_pauses) == 1
    pause = evidence.internal_pauses[0]
    assert pause.duration_ms >= 180
    assert pause.expected_semantic_boundary is False
    assert pause.safe_edit_boundary is None


def _expected_unit(
    index: int,
    *,
    policy: str = "translate",
    scene_end_ms: int | None = None,
    speaker_id: str = "speaker_a",
    subtitle_ids: list[str] | None = None,
) -> DubbingTimelineExpectedUnit:
    start = (index - 1) * 1_000
    return DubbingTimelineExpectedUnit(
        unit_id=f"unit_{index}",
        subtitle_ids=subtitle_ids or [],
        speaker_id=speaker_id,
        scene_id="scene_a",
        speech_policy=policy,
        source_anchor_start_ms=start + 100,
        source_anchor_end_ms=start + 700,
        scene_end_ms=scene_end_ms,
    )


def _clip(
    index: int,
    *,
    unit_ids: list[str] | None = None,
    lane: int = 0,
    start_ms: int | None = None,
    end_ms: int | None = None,
    cqc: str = "passed",
    overlap: bool = False,
    speaker_id: str = "speaker_a",
    candidate_id: str | None = None,
    group_id: str | None = None,
    target_subtitle_ids: list[str] | None = None,
    source_start_ms: int | None = None,
    source_end_ms: int | None = None,
    timeline_gap_before_ms: int = 0,
    manual_review_reason_codes: list[str] | None = None,
) -> DubbingTimelineClip:
    start = (index - 1) * 1_000 + 100 if start_ms is None else start_ms
    return DubbingTimelineClip(
        clip_id=f"clip_{index}",
        candidate_id=candidate_id or f"candidate_{index}",
        group_id=group_id or f"group_{index}",
        unit_ids=unit_ids or [f"unit_{index}"],
        target_subtitle_ids=target_subtitle_ids or [],
        speaker_id=speaker_id,
        scene_id="scene_a",
        dub_lane=lane,
        timeline_start_ms=start,
        timeline_end_ms=start + 600 if end_ms is None else end_ms,
        source_start_ms=source_start_ms,
        source_end_ms=source_end_ms,
        timeline_gap_before_ms=timeline_gap_before_ms,
        source_revision=SOURCE_REVISION,
        cqc_status=cqc,
        manual_review_reason_codes=manual_review_reason_codes or [],
        intentional_overlap=overlap,
    )


def test_timeline_clip_accepts_observed_source_pause_over_two_seconds():
    clip = _clip(1, timeline_gap_before_ms=2_113)

    assert clip.timeline_gap_before_ms == 2_113


def test_timeline_audit_accepts_partitioned_slices_from_one_candidate():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[
                _expected_unit(
                    1,
                    subtitle_ids=["localized_1", "localized_2"],
                )
            ],
            clips=[
                _clip(
                    1,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1"],
                    source_start_ms=0,
                    source_end_ms=300,
                    end_ms=400,
                ),
                _clip(
                    2,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_2"],
                    source_start_ms=300,
                    source_end_ms=600,
                    start_ms=400,
                    end_ms=700,
                ),
            ],
        )
    )

    assert report.status == "passed"
    assert report.covered_unit_ids == ["unit_1"]
    assert report.duplicate_unit_ids == []


def test_timeline_audit_checks_repeated_subtitle_slices_as_one_semantic_block():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[
                _expected_unit(1, subtitle_ids=["localized_1"])
            ],
            clips=[
                _clip(
                    1,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1"],
                    source_start_ms=0,
                    source_end_ms=300,
                    start_ms=100,
                    end_ms=400,
                ),
                _clip(
                    2,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1"],
                    source_start_ms=700,
                    source_end_ms=900,
                    start_ms=800,
                    end_ms=1_000,
                ),
            ],
        )
    )

    assert "TIMELINE_CLIP_MISSES_SEMANTIC_ANCHOR" not in {
        finding.code for finding in report.findings
    }
    assert report.covered_unit_ids == ["unit_1"]
    assert report.duplicate_unit_ids == []


def test_timeline_audit_checks_repeated_group_slices_against_group_scene_end():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[
                _expected_unit(1, scene_end_ms=1_000, subtitle_ids=["localized_1"]),
                _expected_unit(2, scene_end_ms=2_000, subtitle_ids=["localized_2"]),
                _expected_unit(3, scene_end_ms=3_000, subtitle_ids=["localized_3"]),
            ],
            clips=[
                _clip(
                    1,
                    unit_ids=["unit_1", "unit_2", "unit_3"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1", "localized_2", "localized_3"],
                    source_start_ms=0,
                    source_end_ms=800,
                    start_ms=100,
                    end_ms=900,
                ),
                _clip(
                    2,
                    unit_ids=["unit_1", "unit_2", "unit_3"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1", "localized_2", "localized_3"],
                    source_start_ms=900,
                    source_end_ms=1_900,
                    start_ms=900,
                    end_ms=1_900,
                ),
                _clip(
                    3,
                    unit_ids=["unit_1", "unit_2", "unit_3"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1", "localized_2", "localized_3"],
                    source_start_ms=2_000,
                    source_end_ms=2_800,
                    start_ms=1_900,
                    end_ms=2_700,
                ),
            ],
        )
    )

    codes = {finding.code for finding in report.findings}
    assert "TIMELINE_CLIP_MISSES_SEMANTIC_ANCHOR" not in codes
    assert "TIMELINE_CLIP_OVERRUNS_SCENE" not in codes


def test_timeline_audit_allows_shorter_target_language_group_inside_group_anchor():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[
                _expected_unit(1, scene_end_ms=1_000, subtitle_ids=["localized_1"]),
                _expected_unit(2, scene_end_ms=2_000, subtitle_ids=["localized_2"]),
                _expected_unit(3, scene_end_ms=3_000, subtitle_ids=["localized_3"]),
            ],
            clips=[
                _clip(
                    1,
                    unit_ids=["unit_1", "unit_2", "unit_3"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1", "localized_2", "localized_3"],
                    source_start_ms=0,
                    source_end_ms=800,
                    start_ms=100,
                    end_ms=900,
                ),
                _clip(
                    2,
                    unit_ids=["unit_1", "unit_2", "unit_3"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1", "localized_2", "localized_3"],
                    source_start_ms=900,
                    source_end_ms=1_700,
                    start_ms=900,
                    end_ms=1_700,
                ),
            ],
        )
    )

    codes = {finding.code for finding in report.findings}
    assert "TIMELINE_CLIP_MISSES_SEMANTIC_ANCHOR" not in codes
    assert "TIMELINE_CLIP_OVERRUNS_SCENE" not in codes


def test_timeline_audit_still_blocks_repeated_group_slices_past_group_scene_end():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[
                _expected_unit(1, scene_end_ms=1_000, subtitle_ids=["localized_1"]),
                _expected_unit(2, scene_end_ms=2_000, subtitle_ids=["localized_2"]),
                _expected_unit(3, scene_end_ms=3_000, subtitle_ids=["localized_3"]),
            ],
            clips=[
                _clip(
                    1,
                    unit_ids=["unit_1", "unit_2", "unit_3"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1", "localized_2", "localized_3"],
                    source_start_ms=0,
                    source_end_ms=1_000,
                    start_ms=100,
                    end_ms=1_100,
                ),
                _clip(
                    2,
                    unit_ids=["unit_1", "unit_2", "unit_3"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1", "localized_2", "localized_3"],
                    source_start_ms=1_100,
                    source_end_ms=3_200,
                    start_ms=1_100,
                    end_ms=3_200,
                ),
            ],
        )
    )

    assert "TIMELINE_CLIP_OVERRUNS_SCENE" in {
        finding.code for finding in report.findings
    }


def test_timeline_audit_rejects_extra_gap_inside_one_candidate():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[
                _expected_unit(
                    1,
                    subtitle_ids=["localized_1", "localized_2"],
                )
            ],
            clips=[
                _clip(
                    1,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1"],
                    source_start_ms=0,
                    source_end_ms=300,
                    end_ms=400,
                ),
                _clip(
                    2,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_2"],
                    source_start_ms=300,
                    source_end_ms=600,
                    start_ms=700,
                    end_ms=1_000,
                ),
            ],
        )
    )

    assert report.status == "failed"
    assert "TIMELINE_DUB_INTERNAL_PAUSE_DISTORTED" in {finding.code for finding in report.findings}


def test_timeline_audit_accepts_explicit_safe_pause_extension_inside_one_candidate():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[
                _expected_unit(
                    1,
                    subtitle_ids=["localized_1", "localized_2"],
                )
            ],
            clips=[
                _clip(
                    1,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1"],
                    source_start_ms=0,
                    source_end_ms=300,
                    end_ms=400,
                ),
                _clip(
                    2,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_2"],
                    source_start_ms=300,
                    source_end_ms=600,
                    start_ms=540,
                    end_ms=840,
                    timeline_gap_before_ms=140,
                ),
            ],
        )
    )

    assert "TIMELINE_DUB_INTERNAL_PAUSE_DISTORTED" not in {
        finding.code for finding in report.findings
    }


def test_split_planned_timeline_clip_partitions_source_and_targets():
    result = split_planned_timeline_clips(
        timeline_clips=[
            {
                "clip_id": "clip_1",
                "candidate_id": "candidate_1",
                "track_id": "dub",
                "target_subtitle_ids": ["localized_1", "localized_2"],
                "start_ms": 100,
                "end_ms": 1_100,
                "source_start_ms": 0,
                "source_end_ms": 1_000,
                "dub_lane": 0,
            }
        ],
        commands=[
            DubbingTimelineClipSplitCommand(
                clip_id="clip_1",
                candidate_id="candidate_1",
                audio_sha256=AUDIO_SHA,
                slices=[
                    {
                        "target_subtitle_ids": ["localized_1"],
                        "source_start_ms": 0,
                        "source_end_ms": 500,
                        "speech_start_ms": 100,
                        "speech_end_ms": 420,
                        "alignment_word_ids": ["word_1", "word_2"],
                    },
                    {
                        "target_subtitle_ids": ["localized_2"],
                        "source_start_ms": 500,
                        "source_end_ms": 1_000,
                        "speech_start_ms": 580,
                        "speech_end_ms": 950,
                        "alignment_word_ids": ["word_3", "word_4"],
                    },
                ],
            )
        ],
        groups=[
            {
                "group_id": "group_1",
                "unit_ids": ["unit_1"],
                "subtitle_ids": ["localized_1", "localized_2"],
                "target_start_ms": 100,
                "target_end_ms": 1_100,
            }
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=100,
                end_ms=500,
                text="第一句",
                tts_text="第一句。",
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_2",
                start_ms=600,
                end_ms=1_100,
                text="第二句",
                tts_text="第二句。",
            ),
        ],
        boundaries=[],
    )

    assert [item["target_subtitle_ids"] for item in result] == [
        ["localized_1"],
        ["localized_2"],
    ]
    assert [(item["source_start_ms"], item["source_end_ms"]) for item in result] == [(0, 500), (500, 1_000)]
    assert result[0]["media_source_clip_id"] == "clip_1"
    assert result[1]["media_source_clip_id"] == "clip_1"
    assert result[1]["clip_id"].startswith("clip_1__part_002")
    assert result[0]["end_ms"] <= result[1]["start_ms"]


def test_split_planned_timeline_clip_does_not_move_unrelated_group():
    unrelated = {
        "clip_id": "clip_unrelated",
        "candidate_id": "candidate_2",
        "track_id": "dub",
        "dubbing_group_id": "group_2",
        "target_subtitle_ids": ["localized_3"],
        "target_start_ms": 2_000,
        "target_end_ms": 3_000,
        "start_ms": 2_100,
        "end_ms": 2_900,
        "source_start_ms": 0,
        "source_end_ms": 800,
        "dub_lane": 0,
    }
    result = split_planned_timeline_clips(
        timeline_clips=[
            {
                "clip_id": "clip_1",
                "candidate_id": "candidate_1",
                "track_id": "dub",
                "dubbing_group_id": "group_1",
                "target_subtitle_ids": ["localized_1", "localized_2"],
                "start_ms": 0,
                "end_ms": 1_000,
                "source_start_ms": 0,
                "source_end_ms": 1_000,
                "dub_lane": 0,
            },
            unrelated,
        ],
        commands=[
            DubbingTimelineClipSplitCommand(
                clip_id="clip_1",
                candidate_id="candidate_1",
                audio_sha256=AUDIO_SHA,
                slices=[
                    {
                        "target_subtitle_ids": ["localized_1"],
                        "source_start_ms": 0,
                        "source_end_ms": 500,
                        "speech_start_ms": 100,
                        "speech_end_ms": 420,
                        "alignment_word_ids": ["word_1"],
                    },
                    {
                        "target_subtitle_ids": ["localized_2"],
                        "source_start_ms": 500,
                        "source_end_ms": 1_000,
                        "speech_start_ms": 580,
                        "speech_end_ms": 900,
                        "alignment_word_ids": ["word_2"],
                    },
                ],
            )
        ],
        groups=[
            {
                "group_id": "group_1",
                "unit_ids": ["unit_1"],
                "subtitle_ids": ["localized_1", "localized_2"],
                "target_start_ms": 0,
                "target_end_ms": 1_000,
            },
            {
                "group_id": "group_2",
                "unit_ids": ["unit_2"],
                "subtitle_ids": ["localized_3"],
                "target_start_ms": 2_000,
                "target_end_ms": 3_000,
            },
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(subtitle_id="localized_1", start_ms=0, end_ms=400, text="一"),
            VideoLocalizationSubtitleCue(subtitle_id="localized_2", start_ms=500, end_ms=1_000, text="二"),
            VideoLocalizationSubtitleCue(subtitle_id="localized_3", start_ms=2_000, end_ms=3_000, text="三"),
        ],
        boundaries=[],
    )

    kept = next(item for item in result if item["clip_id"] == "clip_unrelated")
    assert (kept["start_ms"], kept["end_ms"]) == (2_100, 2_900)
    assert (kept["source_start_ms"], kept["source_end_ms"]) == (0, 800)


def test_split_planned_timeline_clip_keeps_semantic_speech_spacing():
    result = split_planned_timeline_clips(
        timeline_clips=[
            {
                "clip_id": "clip_1",
                "candidate_id": "candidate_1",
                "track_id": "dub",
                "target_subtitle_ids": ["localized_1", "localized_2"],
                "start_ms": 0,
                "end_ms": 1_200,
                "source_start_ms": 0,
                "source_end_ms": 1_200,
                "dub_lane": 0,
            }
        ],
        commands=[
            DubbingTimelineClipSplitCommand(
                clip_id="clip_1",
                candidate_id="candidate_1",
                audio_sha256=AUDIO_SHA,
                slices=[
                    {
                        "target_subtitle_ids": ["localized_1"],
                        "source_start_ms": 0,
                        "source_end_ms": 500,
                        "speech_start_ms": 100,
                        "speech_end_ms": 420,
                        "alignment_word_ids": ["word_1"],
                    },
                    {
                        "target_subtitle_ids": ["localized_2"],
                        "source_start_ms": 700,
                        "source_end_ms": 1_200,
                        "speech_start_ms": 780,
                        "speech_end_ms": 1_100,
                        "alignment_word_ids": ["word_2"],
                    },
                ],
            )
        ],
        groups=[
            {
                "group_id": "group_1",
                "unit_ids": ["unit_1"],
                "subtitle_ids": ["localized_1", "localized_2"],
                "target_start_ms": 0,
                "target_end_ms": 1_200,
            }
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=0,
                end_ms=500,
                text="第一句",
                tts_text="第一句。",
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_2",
                start_ms=700,
                end_ms=1_200,
                text="第二句",
                tts_text="第二句。",
            ),
        ],
        boundaries=[],
    )

    assert [(item["source_start_ms"], item["source_end_ms"]) for item in result] == [
        (0, 500),
        (700, 1_200),
    ]
    first_speech_end_ms = result[0]["end_ms"] - result[0]["alignment_trail_ms"]
    second_speech_start_ms = result[1]["start_ms"] + result[1]["alignment_lead_ms"]
    assert second_speech_start_ms > first_speech_end_ms


def test_split_planned_timeline_clip_keeps_first_word_anchor_after_safe_leading_crop():
    result = split_planned_timeline_clips(
        timeline_clips=[
            {
                "clip_id": "clip_1", "candidate_id": "candidate_1",
                "track_id": "dub", "target_subtitle_ids": ["localized_1"],
                "start_ms": 294_731, "end_ms": 299_841,
                "source_start_ms": 90, "source_end_ms": 5_200, "dub_lane": 0,
            },
        ],
        commands=[
            DubbingTimelineClipSplitCommand(
                clip_id="clip_1", candidate_id="candidate_1", audio_sha256=AUDIO_SHA,
                slices=[{
                    "target_subtitle_ids": ["localized_1"],
                    "source_start_ms": 1_280, "source_end_ms": 3_000,
                    "speech_start_ms": 1_360, "speech_end_ms": 2_900,
                    "alignment_word_ids": ["word_1"],
                }, {
                    "target_subtitle_ids": ["localized_1"],
                    "source_start_ms": 3_000, "source_end_ms": 5_200,
                    "speech_start_ms": 3_080, "speech_end_ms": 5_000,
                    "alignment_word_ids": ["word_2"],
                }],
            )
        ],
        groups=[{
            "group_id": "group_1", "unit_ids": ["unit_1"],
            "subtitle_ids": ["localized_1"], "target_start_ms": 294_731,
            "target_end_ms": 299_900,
        }],
        localized_subtitles=[VideoLocalizationSubtitleCue(
            subtitle_id="localized_1", start_ms=294_731, end_ms=299_900,
            text="第一句", tts_text="第一句。",
        )],
        boundaries=[],
    )

    assert [(item["source_start_ms"], item["source_end_ms"]) for item in result] == [
        (1_280, 3_000), (3_000, 5_200),
    ]
    assert (result[0]["start_ms"], result[0]["end_ms"]) == (295_921, 297_641)
    assert (result[1]["start_ms"], result[1]["end_ms"]) == (297_641, 299_841)
    assert result[0]["start_ms"] + (1_360 - 1_280) == 296_001


def test_split_planned_timeline_clip_allows_multiple_acoustic_slices_for_one_subtitle():
    result = split_planned_timeline_clips(
        timeline_clips=[
            {
                "clip_id": "clip_1",
                "candidate_id": "candidate_1",
                "track_id": "dub",
                "target_subtitle_ids": ["localized_1"],
                "start_ms": 100,
                "end_ms": 1_300,
                "source_start_ms": 0,
                "source_end_ms": 1_200,
                "dub_lane": 1,
            }
        ],
        commands=[
            DubbingTimelineClipSplitCommand(
                clip_id="clip_1",
                candidate_id="candidate_1",
                audio_sha256=AUDIO_SHA,
                slices=[
                    {
                        "target_subtitle_ids": ["localized_1"],
                        "source_start_ms": 0,
                        "source_end_ms": 500,
                        "speech_start_ms": 100,
                        "speech_end_ms": 400,
                        "alignment_word_ids": ["word_1", "word_2"],
                    },
                    {
                        "target_subtitle_ids": ["localized_1"],
                        "source_start_ms": 700,
                        "source_end_ms": 1_200,
                        "speech_start_ms": 800,
                        "speech_end_ms": 1_100,
                        "alignment_word_ids": ["word_3", "word_4"],
                        "timeline_gap_before_ms": 140,
                    },
                ],
            )
        ],
        groups=[
            {
                "group_id": "group_1",
                "unit_ids": ["unit_1"],
                "subtitle_ids": ["localized_1"],
                "target_start_ms": 100,
                "target_end_ms": 1_300,
            }
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=100,
                end_ms=1_300,
                text="同一字幕中的连续完整话术",
                tts_text="同一字幕中的连续完整话术。",
            )
        ],
        boundaries=[],
    )

    assert len(result) == 2
    assert all(
        item["target_subtitle_ids"] == ["localized_1"]
        for item in result
    )
    assert result[1]["start_ms"] == result[0]["end_ms"] + 140
    assert result[1]["end_ms"] - result[0]["start_ms"] == 1_140
    assert all(item["dub_lane"] == 1 for item in result)


def test_split_planned_timeline_clip_rejects_a_removed_speech_range():
    with pytest.raises(ValueError, match="只能删除已验证的内部静音"):
        split_planned_timeline_clips(
            timeline_clips=[
                {
                    "clip_id": "clip_1",
                    "candidate_id": "candidate_1",
                    "track_id": "dub",
                    "target_subtitle_ids": ["localized_1", "localized_2"],
                    "start_ms": 0,
                    "end_ms": 1_000,
                    "source_start_ms": 0,
                    "source_end_ms": 1_000,
                    "dub_lane": 0,
                }
            ],
            commands=[
                DubbingTimelineClipSplitCommand(
                    clip_id="clip_1",
                    candidate_id="candidate_1",
                    audio_sha256=AUDIO_SHA,
                    slices=[
                        {
                            "target_subtitle_ids": ["localized_1"],
                            "source_start_ms": 0,
                            "source_end_ms": 400,
                            "speech_start_ms": 100,
                            "speech_end_ms": 400,
                            "alignment_word_ids": ["word_1"],
                        },
                        {
                            "target_subtitle_ids": ["localized_2"],
                            "source_start_ms": 450,
                            "source_end_ms": 1_000,
                            "speech_start_ms": 450,
                            "speech_end_ms": 900,
                            "alignment_word_ids": ["word_2"],
                        },
                    ],
                )
            ],
            groups=[
                {
                    "group_id": "group_1",
                    "unit_ids": ["unit_1"],
                    "subtitle_ids": ["localized_1", "localized_2"],
                    "target_start_ms": 0,
                    "target_end_ms": 1_000,
                }
            ],
            localized_subtitles=[
                VideoLocalizationSubtitleCue(
                    subtitle_id="localized_1",
                    start_ms=0,
                    end_ms=400,
                    text="第一句",
                    tts_text="第一句。",
                ),
                VideoLocalizationSubtitleCue(
                    subtitle_id="localized_2",
                    start_ms=450,
                    end_ms=1_000,
                    text="第二句",
                    tts_text="第二句。",
                ),
            ],
            boundaries=[],
        )


def test_split_planned_timeline_clip_accepts_safe_acoustic_gap():
    result = split_planned_timeline_clips(
        timeline_clips=[
            {
                "clip_id": "clip_1",
                "candidate_id": "candidate_1",
                "track_id": "dub",
                "target_subtitle_ids": ["localized_1", "localized_2"],
                "start_ms": 0,
                "end_ms": 1_000,
                "source_start_ms": 0,
                "source_end_ms": 1_000,
                "dub_lane": 0,
            }
        ],
        commands=[
            DubbingTimelineClipSplitCommand(
                clip_id="clip_1",
                candidate_id="candidate_1",
                audio_sha256=AUDIO_SHA,
                slices=[
                    {
                        "target_subtitle_ids": ["localized_1"],
                        "source_start_ms": 0,
                        "source_end_ms": 420,
                        "speech_start_ms": 100,
                        "speech_end_ms": 340,
                        "alignment_word_ids": ["word_1"],
                    },
                    {
                        "target_subtitle_ids": ["localized_2"],
                        "source_start_ms": 420,
                        "source_end_ms": 1_000,
                        "speech_start_ms": 500,
                        "speech_end_ms": 900,
                        "alignment_word_ids": ["word_2"],
                    },
                ],
            )
        ],
        groups=[
            {
                "group_id": "group_1",
                "unit_ids": ["unit_1"],
                "subtitle_ids": ["localized_1", "localized_2"],
                "target_start_ms": 0,
                "target_end_ms": 1_000,
            }
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=0,
                end_ms=400,
                text="第一句",
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_2",
                start_ms=450,
                end_ms=1_000,
                text="第二句",
            ),
        ],
        boundaries=[],
    )

    assert len(result) == 2
    assert result[0]["source_end_ms"] <= result[1]["source_start_ms"]


def test_split_keeps_versioned_plan_target_projection():
    result = split_planned_timeline_clips(
        timeline_clips=[
            {
                "clip_id": "clip_1",
                "candidate_id": "candidate_1",
                "track_id": "dub",
                "dubbing_group_id": "group_1",
                "target_subtitle_ids": ["localized_1", "localized_2"],
                "start_ms": 1_000,
                "end_ms": 2_000,
                "source_start_ms": 0,
                "source_end_ms": 1_000,
                "dub_lane": 0,
            }
        ],
        commands=[
            DubbingTimelineClipSplitCommand(
                clip_id="clip_1",
                candidate_id="candidate_1",
                audio_sha256=AUDIO_SHA,
                slices=[
                    {
                        "target_subtitle_ids": ["localized_1"],
                        "source_start_ms": 0,
                        "source_end_ms": 420,
                        "speech_start_ms": 100,
                        "speech_end_ms": 340,
                        "alignment_word_ids": ["word_1"],
                    },
                    {
                        "target_subtitle_ids": ["localized_2"],
                        "source_start_ms": 420,
                        "source_end_ms": 1_000,
                        "speech_start_ms": 500,
                        "speech_end_ms": 900,
                        "alignment_word_ids": ["word_2"],
                    },
                ],
            )
        ],
        groups=[
            {
                "group_id": "group_1",
                "unit_ids": ["unit_1", "unit_2"],
                "subtitle_ids": ["localized_1", "localized_2"],
                "target_start_ms": 1_000,
                "target_end_ms": 2_000,
            }
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=0,
                end_ms=400,
                text="第一句",
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_2",
                start_ms=450,
                end_ms=1_000,
                text="第二句",
            ),
        ],
        boundaries=[],
    )

    assert [
        (clip["target_start_ms"], clip["target_end_ms"])
        for clip in result
    ] == [(1_000, 1_400), (1_450, 2_000)]


def test_split_right_alignment_stops_before_next_lane_clip():
    result = split_planned_timeline_clips(
        timeline_clips=[
            {
                "clip_id": "clip_1",
                "candidate_id": "candidate_1",
                "track_id": "dub",
                "dubbing_group_id": "group_1",
                "target_subtitle_ids": ["localized_1", "localized_2"],
                "start_ms": 0,
                "end_ms": 3_000,
                "source_start_ms": 0,
                "source_end_ms": 3_000,
                "dub_lane": 0,
            },
            {
                "clip_id": "clip_2",
                "candidate_id": "candidate_2",
                "track_id": "dub",
                "dubbing_group_id": "group_2",
                "target_subtitle_ids": ["localized_3"],
                "target_start_ms": 3_500,
                "target_end_ms": 4_500,
                "start_ms": 3_500,
                "end_ms": 4_500,
                "source_start_ms": 0,
                "source_end_ms": 1_000,
                "dub_lane": 0,
            },
        ],
        commands=[
            DubbingTimelineClipSplitCommand(
                clip_id="clip_1",
                candidate_id="candidate_1",
                audio_sha256=AUDIO_SHA,
                slices=[
                    {
                        "target_subtitle_ids": ["localized_1"],
                        "source_start_ms": 0,
                        "source_end_ms": 1_000,
                        "speech_start_ms": 100,
                        "speech_end_ms": 900,
                        "alignment_word_ids": ["word_1"],
                    },
                    {
                        "target_subtitle_ids": ["localized_2"],
                        "source_start_ms": 1_000,
                        "source_end_ms": 3_000,
                        "speech_start_ms": 1_100,
                        "speech_end_ms": 2_900,
                        "alignment_word_ids": ["word_2"],
                    },
                ],
            )
        ],
        groups=[
            {
                "group_id": "group_1",
                "unit_ids": ["unit_1", "unit_2"],
                "subtitle_ids": ["localized_1", "localized_2"],
                "target_start_ms": 0,
                "target_end_ms": 4_000,
            },
            {
                "group_id": "group_2",
                "unit_ids": ["unit_3"],
                "subtitle_ids": ["localized_3"],
                "target_start_ms": 3_500,
                "target_end_ms": 4_500,
            },
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=0,
                end_ms=1_000,
                text="第一句",
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_2",
                start_ms=1_000,
                end_ms=4_000,
                text="第二句",
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_3",
                start_ms=3_500,
                end_ms=4_500,
                text="下一句",
            ),
        ],
        boundaries=[],
    )

    group_1_clips = [
        clip for clip in result if clip.get("dubbing_group_id") == "group_1"
    ]
    assert group_1_clips[-1]["end_ms"] == 3_000
    assert next(
        clip for clip in result if clip.get("dubbing_group_id") == "group_2"
    )["start_ms"] == 3_500


def test_timeline_audit_counts_one_candidate_split_once_but_rejects_source_crop_overlap():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[
                _expected_unit(
                    1,
                    subtitle_ids=["localized_1", "localized_2"],
                )
            ],
            clips=[
                _clip(
                    1,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1"],
                    source_start_ms=0,
                    source_end_ms=400,
                    end_ms=400,
                ),
                _clip(
                    2,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1"],
                    source_start_ms=300,
                    source_end_ms=600,
                    start_ms=400,
                    end_ms=700,
                ),
            ],
        )
    )

    codes = {finding.code for finding in report.findings}
    assert report.status == "failed"
    assert report.duplicate_unit_ids == []
    assert "TIMELINE_DUB_SOURCE_CROP_OVERLAP" in codes


def test_timeline_audit_rejects_duplicate_subtitle_from_different_candidates():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[
                _expected_unit(
                    1,
                    subtitle_ids=["localized_1"],
                )
            ],
            clips=[
                _clip(
                    1,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_1",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1"],
                ),
                _clip(
                    2,
                    unit_ids=["unit_1"],
                    candidate_id="candidate_2",
                    group_id="group_1",
                    target_subtitle_ids=["localized_1"],
                    start_ms=300,
                    end_ms=900,
                ),
            ],
        )
    )

    assert report.status == "failed"
    assert report.duplicate_unit_ids == ["unit_1"]
    assert "TIMELINE_DUB_COVERAGE_DUPLICATED" in {
        finding.code for finding in report.findings
    }


def test_timeline_audit_requires_exact_coverage_current_subtitles_and_cqc():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            dub_subtitle_source_revision="stale",
            expected_units=[
                _expected_unit(1),
                _expected_unit(2),
                _expected_unit(3, policy="preserve_original"),
            ],
            clips=[
                _clip(1, cqc="needs_review"),
                _clip(3),
            ],
            dub_subtitle_clip_ids=["clip_1"],
        )
    )

    codes = {finding.code for finding in report.findings}
    assert report.status == "failed"
    assert report.missing_unit_ids == ["unit_2"]
    assert report.unexpected_unit_ids == ["unit_3"]
    assert "TIMELINE_CLIP_CQC_REVIEW_RECOMMENDED" in codes
    assert "TIMELINE_CLIP_CQC_NOT_PASSED" not in codes
    assert "DUB_SUBTITLE_SOURCE_REVISION_STALE" in codes
    assert "DUB_SUBTITLE_CLIP_COVERAGE_STALE" in codes


def test_timeline_audit_keeps_hard_integrity_gates_but_marks_deferred_cqc_as_warning():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[_expected_unit(1)],
            clips=[
                _clip(
                    1,
                    cqc="failed",
                    manual_review_reason_codes=["CQC_MANUAL_REVIEW"],
                )
            ],
        )
    )

    assert report.status == "warning"
    codes = {finding.code for finding in report.findings}
    assert "TIMELINE_CLIP_MANUAL_REVIEW_MARKED" in codes
    assert "TIMELINE_CLIP_CQC_NOT_PASSED" not in codes


def test_timeline_audit_downgrades_only_matching_marked_timing_issue():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[
                DubbingTimelineExpectedUnit(
                    unit_id="unit_1",
                    speaker_id="speaker_a",
                    scene_id="scene_a",
                    speech_policy="translate",
                    source_anchor_start_ms=0,
                    source_anchor_end_ms=1_000,
                    scene_end_ms=1_200,
                    source_continuous_with_next=True,
                ),
                DubbingTimelineExpectedUnit(
                    unit_id="unit_2",
                    speaker_id="speaker_a",
                    scene_id="scene_a",
                    speech_policy="translate",
                    source_anchor_start_ms=1_050,
                    source_anchor_end_ms=2_000,
                ),
            ],
            clips=[
                _clip(
                    1,
                    start_ms=0,
                    end_ms=1_500,
                    manual_review_reason_codes=[
                        "TIMELINE_CLIP_OVERRUNS_SCENE",
                    ],
                ),
                _clip(
                    2,
                    start_ms=3_000,
                    end_ms=3_600,
                    manual_review_reason_codes=[
                        "TIMELINE_DUB_CONTINUOUS_SPEECH_UNDERFILLED",
                    ],
                ),
            ],
        )
    )

    severities = {
        finding.code: finding.severity
        for finding in report.findings
        if finding.code
        in {
            "TIMELINE_CLIP_OVERRUNS_SCENE",
            "TIMELINE_DUB_CONTINUOUS_SPEECH_UNDERFILLED",
            "TIMELINE_CLIP_MISSES_SEMANTIC_ANCHOR",
        }
    }
    assert severities["TIMELINE_CLIP_OVERRUNS_SCENE"] == "warning"
    assert severities["TIMELINE_DUB_CONTINUOUS_SPEECH_UNDERFILLED"] == "warning"
    assert severities["TIMELINE_CLIP_MISSES_SEMANTIC_ANCHOR"] == "blocking"


def test_timeline_phase_can_pass_before_derived_subtitles_exist():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[_expected_unit(1)],
            clips=[_clip(1)],
        )
    )

    assert report.phase == "timeline"
    assert report.status == "passed"
    assert not {
        "DUB_SUBTITLE_SOURCE_REVISION_STALE",
        "DUB_SUBTITLE_CLIP_COVERAGE_STALE",
    } & {finding.code for finding in report.findings}


def test_current_timeline_does_not_block_only_for_missing_candidate_report():
    base = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_1",
                speaker_id="speaker_1",
                start_ms=0,
                end_ms=1_000,
                audio_route="clone_from_source",
                review_status="ready",
            )
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=0,
                end_ms=1_000,
                text="这句话需要权威质检",
                tts_text="这句话需要权威质检",
                source_cue_ids=["cue_1"],
            )
        ],
    )
    snapshot = build_project_snapshot(base)
    plan = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=snapshot.source_revision,
            semantic_units=snapshot.semantic_units,
            boundaries=snapshot.boundaries,
        )
    )
    draft = base.model_copy(
        update={
            "dubbing_production": base.dubbing_production.model_copy(
                update={
                    "enforcement_mode": "planned",
                    "active_plan": plan,
                }
            ),
            "timeline_clips": [
                {
                    "clip_id": "clip_1",
                    "candidate_id": "candidate_1",
                    "result_id": "candidate_1",
                    "track_id": "dub",
                    "target_subtitle_ids": ["localized_1"],
                    "start_ms": 0,
                    "end_ms": 1_000,
                    "dub_lane": 0,
                    "cqc_status": "passed",
                }
            ],
        }
    )

    blockers = quality_gate.dubbing_production_timeline_blockers(
        draft,
        phase="timeline",
    )

    assert "TIMELINE_CLIP_CQC_NOT_PASSED" not in {
        issue.code for issue in blockers
    }


def test_timeline_audit_blocks_scene_overrun_and_unexplained_overlap():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            dub_subtitle_source_revision=SOURCE_REVISION,
            expected_units=[
                _expected_unit(1, scene_end_ms=1_000),
                _expected_unit(2),
            ],
            clips=[
                _clip(1, end_ms=1_300),
                _clip(2, start_ms=1_200, end_ms=1_800),
            ],
            dub_subtitle_clip_ids=["clip_1", "clip_2"],
        )
    )

    codes = {finding.code for finding in report.findings}
    assert report.status == "failed"
    assert "TIMELINE_CLIP_OVERRUNS_SCENE" in codes
    assert "TIMELINE_UNEXPLAINED_DUB_OVERLAP" in codes


def test_timeline_overlap_requires_different_speakers_and_source_overlap():
    first = _expected_unit(1, speaker_id="speaker_a")
    second = _expected_unit(2, speaker_id="speaker_b").model_copy(
        update={
            "source_anchor_start_ms": 300,
            "source_anchor_end_ms": 800,
        }
    )
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[first, second],
            clips=[
                _clip(1, end_ms=700, overlap=True),
                _clip(
                    2,
                    start_ms=300,
                    end_ms=800,
                    overlap=True,
                    speaker_id="speaker_b",
                ),
            ],
        )
    )

    assert report.status == "passed"

    same_speaker = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[first, second],
            clips=[
                _clip(1, end_ms=700, overlap=True),
                _clip(2, start_ms=300, end_ms=800, overlap=True),
            ],
        )
    )
    assert same_speaker.status == "failed"
    assert "TIMELINE_UNEXPLAINED_DUB_OVERLAP" in {finding.code for finding in same_speaker.findings}


def test_timeline_audit_warns_when_nonoverlap_dialogue_uses_extra_lanes():
    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            dub_subtitle_source_revision=SOURCE_REVISION,
            expected_units=[_expected_unit(1), _expected_unit(2)],
            clips=[_clip(1), _clip(2, lane=1)],
            dub_subtitle_clip_ids=["clip_1", "clip_2"],
        )
    )

    assert report.status == "warning"
    assert {finding.code for finding in report.findings} == {"TIMELINE_UNNECESSARY_DUB_LANES"}


def test_timeline_audit_warns_for_large_dub_gap_inside_continuous_source_speech():
    first = _expected_unit(1).model_copy(
        update={
            "source_anchor_start_ms": 100,
            "source_anchor_end_ms": 1_000,
            "source_continuous_with_next": True,
        }
    )
    second = _expected_unit(2).model_copy(
        update={"source_anchor_start_ms": 1_040, "source_anchor_end_ms": 1_900}
    )

    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[first, second],
            clips=[
                _clip(1, start_ms=100, end_ms=600),
                _clip(2, start_ms=1_300, end_ms=1_800),
            ],
        )
    )

    assert report.status == "warning"
    assert "TIMELINE_DUB_CONTINUOUS_SPEECH_UNDERFILLED" in {
        finding.code for finding in report.findings
    }
    assert next(
        finding
        for finding in report.findings
        if finding.code == "TIMELINE_DUB_CONTINUOUS_SPEECH_UNDERFILLED"
    ).severity == "warning"


def test_continuous_underfill_check_can_be_scoped_to_current_range():
    first = _expected_unit(1).model_copy(
        update={
            "source_anchor_start_ms": 100,
            "source_anchor_end_ms": 1_000,
            "source_continuous_with_next": True,
        }
    )
    second = _expected_unit(2).model_copy(
        update={"source_anchor_start_ms": 1_040, "source_anchor_end_ms": 1_900}
    )
    payload = DubbingTimelineAuditInput(
        current_source_revision=SOURCE_REVISION,
        current_timeline_revision=SOURCE_REVISION,
        phase="timeline",
        expected_units=[first, second],
        clips=[
            _clip(1, start_ms=100, end_ms=600),
            _clip(2, start_ms=1_300, end_ms=1_800),
        ],
    )

    assert continuous_speech_underfill_assessments(
        payload,
        eligible_left_group_ids={"group_1"},
    )
    assert continuous_speech_underfill_assessments(
        payload,
        eligible_left_group_ids={"group_2"},
    ) == []


def test_continuous_underfill_scope_ignores_reused_group_id_from_old_plan():
    first = _expected_unit(1).model_copy(
        update={
            "source_anchor_start_ms": 100,
            "source_anchor_end_ms": 1_000,
            "source_continuous_with_next": True,
        }
    )
    second = _expected_unit(2).model_copy(
        update={"source_anchor_start_ms": 1_040, "source_anchor_end_ms": 1_900}
    )
    payload = DubbingTimelineAuditInput(
        current_source_revision=SOURCE_REVISION,
        current_timeline_revision=SOURCE_REVISION,
        phase="timeline",
        expected_units=[first, second],
        clips=[
            _clip(
                1,
                start_ms=100,
                end_ms=600,
                group_id="reused_group",
            ),
            _clip(2, start_ms=1_300, end_ms=1_800),
        ],
    )

    assert continuous_speech_underfill_assessments(
        payload,
        eligible_left_group_ids={"reused_group"},
        eligible_left_unit_ids={"unit_1"},
    )
    assert continuous_speech_underfill_assessments(
        payload,
        eligible_left_group_ids={"reused_group"},
        eligible_left_unit_ids={"unit_from_current_plan"},
    ) == []


def test_service_scopes_boundary_check_with_current_plan_unit_ids(monkeypatch):
    service = DubbingProductionApplicationService()
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(
                groups=[
                    SimpleNamespace(
                        group_id="reused_group",
                        unit_ids=["current_unit"],
                    )
                ]
            )
        )
    )
    captured = {}
    monkeypatch.setattr(service, "_require_current_project", lambda _project_id: draft)
    monkeypatch.setattr(
        dubbing_production_service.domain,
        "dubbing_timeline_projection_revision",
        lambda _draft: SOURCE_REVISION,
    )
    monkeypatch.setattr(
        dubbing_production_service.domain,
        "build_current_timeline_audit_input",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )

    def assess(_payload, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        dubbing_production_service.domain,
        "continuous_speech_underfill_assessments",
        assess,
    )

    assert service.find_continuous_boundary_underfill_groups(
        "project-1", ["reused_group"]
    ) == []
    assert captured["eligible_left_group_ids"] == {"reused_group"}
    assert captured["eligible_left_unit_ids"] == {"current_unit"}
    assert captured["main_lane_only"] is True


def test_timeline_audit_does_not_invent_continuity_from_close_anchors():
    first = _expected_unit(1).model_copy(
        update={"source_anchor_start_ms": 100, "source_anchor_end_ms": 1_000}
    )
    second = _expected_unit(2).model_copy(
        update={"source_anchor_start_ms": 1_040, "source_anchor_end_ms": 1_900}
    )

    report = audit_timeline(
        DubbingTimelineAuditInput(
            current_source_revision=SOURCE_REVISION,
            current_timeline_revision=SOURCE_REVISION,
            phase="timeline",
            expected_units=[first, second],
            clips=[
                _clip(1, start_ms=100, end_ms=600),
                _clip(2, start_ms=1_300, end_ms=1_800),
            ],
        )
    )

    assert "TIMELINE_DUB_CONTINUOUS_SPEECH_UNDERFILLED" not in {
        finding.code for finding in report.findings
    }


def test_current_timeline_projection_uses_plan_cqc_and_dub_subtitle_owners(
    tmp_path: Path,
):
    audio_path = tmp_path / "candidate.wav"
    sf.write(
        audio_path,
        np.zeros(16_000, dtype=np.float32),
        16_000,
    )
    audio_sha256 = media_assets.file_sha256(audio_path)
    base = VideoLocalizationDraft(
        source_media={"duration_ms": 1_200, "frame_rate": 24.0},
        cues=[
            VideoLocalizationCue(
                cue_id="cue_1",
                speaker_id="speaker_1",
                start_ms=0,
                end_ms=1_000,
                audio_route="clone_from_source",
                review_status="ready",
            )
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_1",
                start_ms=0,
                end_ms=1_000,
                text="这句话可以使用",
                tts_text="这句话可以使用",
                source_cue_ids=["cue_1"],
            )
        ],
    )
    snapshot = build_project_snapshot(base)
    reviewed_unit = snapshot.semantic_units[0].model_copy(update={"scene_id": "scene_1", "scene_end_ms": 1_100})
    plan = build_generation_plan(
        DubbingGenerationPlanInput(
            source_revision=snapshot.source_revision,
            semantic_units=[reviewed_unit],
            boundaries=[],
        )
    )
    candidate_input = _candidate(
        source_revision=snapshot.source_revision,
        candidate_id="candidate_1",
        audio_sha256=audio_sha256,
    )
    report = evaluate_candidate(candidate_input)
    draft = base.model_copy(
        update={
            "dubbing_production": base.dubbing_production.model_copy(
                update={
                    "active_plan": plan,
                    "candidate_reports": [report],
                    "candidate_inputs": [candidate_input],
                }
            ),
            "timeline_clips": [
                {
                    "clip_id": "clip_1",
                    "candidate_id": "candidate_1",
                    "result_id": "result_1",
                    "subtitle_id": "localized_1",
                    "target_subtitle_ids": ["localized_1"],
                    "track_id": "dub",
                    "start_ms": 100,
                    "end_ms": 900,
                    "dub_lane": 0,
                    "status": "ready",
                    "audio_path": str(audio_path),
                }
            ],
            "dub_subtitles": [
                VideoLocalizationDubSubtitleCue(
                    subtitle_id="dub_1",
                    start_ms=100,
                    end_ms=900,
                    text="这句话可以使用",
                    source_clip_ids=["clip_1"],
                    source_audio_sha256=audio_sha256,
                )
            ],
        }
    )
    timeline_revision = dub_subtitles.freeze_workflow_input(draft, regeneration_mode="full").source_revision
    draft = draft.model_copy(update={"dub_subtitle_source_revision": timeline_revision})

    payload = build_current_timeline_audit_input(
        draft,
        current_timeline_revision=timeline_revision,
        current_audio_sha256_by_clip_id={"clip_1": audio_sha256},
    )

    assert payload.current_source_revision == snapshot.source_revision
    assert payload.clips[0].unit_ids == ["localized_1"]
    assert payload.clips[0].group_id == "dubbing_group_0001"
    assert payload.clips[0].cqc_status == "passed"
    assert payload.clips[0].timeline_start_ms == 83
    assert payload.clips[0].timeline_end_ms == 917
    assert payload.dub_subtitle_clip_ids == ["clip_1"]
    assert audit_timeline(payload).status == "passed"
    assert quality_gate.dubbing_production_export_blockers(draft) == []

    unreviewed = draft.model_copy(
        update={"dubbing_production": draft.dubbing_production.model_copy(update={"candidate_reports": []})}
    )
    blocker_codes = {issue.code for issue in quality_gate.dubbing_production_export_blockers(unreviewed)}
    assert "TIMELINE_CLIP_CQC_NOT_PASSED" not in blocker_codes

    changed_audio = build_current_timeline_audit_input(
        draft,
        current_timeline_revision=timeline_revision,
        current_audio_sha256_by_clip_id={"clip_1": "b" * 64},
    )
    changed_report = audit_timeline(changed_audio)
    assert changed_audio.clips[0].cqc_status == "not_reviewed"
    assert "TIMELINE_CLIP_AUDIO_FINGERPRINT_MISMATCH" in {finding.code for finding in changed_report.findings}


def test_current_timeline_projection_maps_split_clip_to_its_subtitle_units_only():
    first = _expected_unit(1).model_copy(
        update={"subtitle_ids": ["localized_1"]}
    )
    second = _expected_unit(2).model_copy(
        update={"subtitle_ids": ["localized_2"]}
    )
    plan = DubbingGenerationPlan(
        source_revision=SOURCE_REVISION,
        plan_revision=1,
        status="passed",
        semantic_units=[
            DubbingSemanticUnit(
                unit_id=first.unit_id,
                subtitle_ids=first.subtitle_ids,
                source_cue_ids=["cue_1"],
                source_word_ids=["word_1"],
                speaker_id=first.speaker_id,
                start_ms=first.source_anchor_start_ms,
                end_ms=first.source_anchor_end_ms,
                source_anchor_start_ms=first.source_anchor_start_ms,
                source_anchor_end_ms=first.source_anchor_end_ms,
                display_text="一",
                spoken_text="一",
                speech_policy="translate",
            ),
            DubbingSemanticUnit(
                unit_id=second.unit_id,
                subtitle_ids=second.subtitle_ids,
                source_cue_ids=["cue_2"],
                source_word_ids=["word_2"],
                speaker_id=second.speaker_id,
                start_ms=second.source_anchor_start_ms,
                end_ms=second.source_anchor_end_ms,
                source_anchor_start_ms=second.source_anchor_start_ms,
                source_anchor_end_ms=second.source_anchor_end_ms,
                display_text="二",
                spoken_text="二",
                speech_policy="translate",
            ),
        ],
        speech_islands=[],
        groups=[
            DubbingGenerationGroup(
                group_id="group_1",
                island_id="island_1",
                unit_ids=[first.unit_id, second.unit_id],
                subtitle_ids=["localized_1", "localized_2"],
                speaker_id=first.speaker_id,
                spoken_text="一\n二",
                target_start_ms=0,
                target_end_ms=1_800,
                source_reference_start_ms=0,
                source_reference_end_ms=1_800,
            )
        ],
    )
    base = VideoLocalizationDraft(
        source_media={"duration_ms": 2_000, "frame_rate": 24.0},
        localized_subtitles=[
            VideoLocalizationSubtitleCue(subtitle_id="localized_1", start_ms=0, end_ms=800, text="一"),
            VideoLocalizationSubtitleCue(subtitle_id="localized_2", start_ms=1_000, end_ms=1_800, text="二"),
        ],
        timeline_clips=[
            {
                "clip_id": "clip_1",
                "candidate_id": "candidate_1",
                "track_id": "dub",
                "dubbing_group_id": "group_1",
                "target_subtitle_ids": ["localized_1"],
                "start_ms": 0,
                "end_ms": 800,
            },
            {
                "clip_id": "clip_2",
                "candidate_id": "candidate_1",
                "track_id": "dub",
                "dubbing_group_id": "group_1",
                "target_subtitle_ids": ["localized_2"],
                "start_ms": 1_000,
                "end_ms": 1_800,
            },
        ],
    )
    draft = base.model_copy(
        update={
            "dubbing_production": base.dubbing_production.model_copy(
                update={"active_plan": plan}
            )
        }
    )

    payload = build_current_timeline_audit_input(
        draft,
        current_timeline_revision="timeline",
    )

    assert [clip.unit_ids for clip in payload.clips] == [[first.unit_id], [second.unit_id]]


@pytest.mark.parametrize('speech_end, word_end, limit, expected_end', [
    (900, 910, 960, 960),
    (970, 910, 960, 1000),
    (900, 970, 960, 1000),
    (900, 910, 1010, 1000),
    (None, 910, 960, 1000),
])
def test_window_fit_trims_only_available_trailing_margin(speech_end, word_end, limit, expected_end):
    from app.domains.video_localization.dubbing_production_service import _fit_trailing_safety_margin
    clips = [dict(clip_id='tail', start_ms=100, end_ms=1000, source_start_ms=100, source_end_ms=1000)]
    audio = SimpleNamespace(speech_end_ms=speech_end, aligned_words=[SimpleNamespace(start_ms=100, end_ms=word_end)])
    fitted = _fit_trailing_safety_margin(clips, audio, latest_end_ms=limit)
    assert fitted[0]['end_ms'] == fitted[0]['source_end_ms'] == expected_end
    assert fitted[0]['start_ms'] == fitted[0]['source_start_ms'] == 100
    assert clips[0]['end_ms'] == 1000


def test_window_fit_trailing_margin_is_reread_from_working_projection():
    """Close-out must validate the exact tail crop it just applied."""
    clips = [{
        "clip_id": "candidate-tail", "candidate_id": "candidate-1",
        "track_id": "dub", "status": "ready", "dub_lane": 0,
        "dubbing_group_id": "group-1", "target_subtitle_ids": ["localized-1"],
        "audio_path": "candidate.wav", "start_ms": 100, "end_ms": 1_000,
        "source_start_ms": 100, "source_end_ms": 1_000,
    }]
    audio = SimpleNamespace(
        speech_end_ms=900,
        aligned_words=[SimpleNamespace(start_ms=100, end_ms=910)],
    )
    fitted = dubbing_production_service._fit_trailing_safety_margin(
        clips, audio, latest_end_ms=960,
    )
    working = SimpleNamespace(timeline_clips=fitted)
    applied = dubbing_production_service._current_group_working_candidate_clips(
        working, group_id="group-1", candidate_id="candidate-1",
        target_subtitle_ids=["localized-1"],
    )

    assert applied[0]["end_ms"] == applied[0]["source_end_ms"] == 960
    assert dubbing_production_service._first_ready_dub_overlap(
        applied,
        [{
            "clip_id": "next", "track_id": "dub", "status": "ready",
            "dub_lane": 0, "audio_path": "next.wav", "start_ms": 960, "end_ms": 1_100,
        }],
    ) is None


def test_window_fit_leading_margin_removes_only_safe_silence_before_previous_clip():
    clips = [{
        "clip_id": "candidate-head", "track_id": "dub", "status": "ready",
        "dub_lane": 0, "start_ms": 160_776, "end_ms": 163_096,
        "source_start_ms": 80, "source_end_ms": 2_400,
        "audio_path": "candidate.wav",
    }]
    audio = SimpleNamespace(
        speech_start_ms=180,
        speech_end_ms=2_300,
        aligned_words=[SimpleNamespace(start_ms=180, end_ms=2_300)],
    )

    fitted = dubbing_production_service._fit_leading_safety_margin(
        clips, audio, earliest_start_ms=160_813,
    )

    assert fitted == [{
        **clips[0],
        "start_ms": 160_813,
        "source_start_ms": 117,
        "alignment_lead_ms": 63,
    }]
    assert clips[0]["start_ms"] == 160_776
    assert fitted[0]["end_ms"] == 163_096
    assert fitted[0]["source_end_ms"] == 2_400
    assert dubbing_production_service._first_ready_dub_overlap(
        fitted,
        [{
            "clip_id": "previous", "track_id": "dub", "status": "ready",
            "dub_lane": 0, "audio_path": "previous.wav", "start_ms": 159_713, "end_ms": 160_813,
        }],
    ) is None


@pytest.mark.parametrize("word_start", [180, 320])
def test_window_fit_leading_margin_rejects_a_real_speech_overlap(word_start):
    clips = [{
        "clip_id": "candidate-head", "track_id": "dub", "status": "ready",
        "dub_lane": 0, "start_ms": 160_776, "end_ms": 163_096,
        "source_start_ms": 80, "source_end_ms": 2_400,
        "audio_path": "candidate.wav",
    }]
    audio = SimpleNamespace(
        speech_start_ms=180,
        speech_end_ms=2_300,
        aligned_words=[SimpleNamespace(start_ms=word_start, end_ms=2_300)],
    )

    fitted = dubbing_production_service._fit_leading_safety_margin(
        clips, audio, earliest_start_ms=160_957,
    )

    assert fitted == clips
    assert dubbing_production_service._first_ready_dub_overlap(
        fitted,
        [{
            "clip_id": "previous", "track_id": "dub", "status": "ready",
            "dub_lane": 0, "audio_path": "previous.wav", "start_ms": 159_713, "end_ms": 160_957,
        }],
    ) == ("candidate-head", "previous")


def test_window_gap_compression_requires_semantics_and_safe_core(monkeypatch):
    from app.domains.video_localization import dubbing_production_service as service
    gap = DubbingAudioGapEvidence(gap_id='safe', kind='internal', start_ms=100, end_ms=460,
        duration_ms=360, evidence_sources=['word_alignment', 'energy'], evidence_ids=['alignment'],
        boundary_confidence='clear', edit_decision='retain', retained_duration_ms=360,
        safe_edit_boundary=True, semantic_role='semantic_boundary', decision_reason='已确认语义边界')
    monkeypatch.setattr(service.dubbing_candidate_alignment, 'safe_internal_gap_cut', lambda g, w: (200, 280))
    unsafe = gap.model_copy(update={'gap_id': 'unsafe', 'safe_edit_boundary': None})
    unknown = gap.model_copy(update={'gap_id': 'unknown', 'semantic_role': None})
    output = service._shorten_safe_gaps_to_fit([unsafe, unknown, gap], [], required_ms=44)
    assert [g.edit_decision for g in output] == ['retain', 'retain', 'remove']
    assert output[-1].retained_duration_ms == 280
    assert service._shorten_safe_gaps_to_fit([gap], [], required_ms=0)[0].edit_decision == 'retain'


@pytest.mark.parametrize("targets", [["a"], ["a", "b"]])
def test_manual_split_coverage_is_not_a_new_generation_slot(targets):
    clips = [
        {"clip_id": f"manual-{target}", "track_id": "dub", "dub_lane": 0,
         "result_id": f"result-{target}", "target_subtitle_ids": [target]}
        for target in targets
    ]
    run = build_production_run_snapshot(
        current_source_revision="a" * 64,
        active_plan=SimpleNamespace(source_revision="a" * 64, plan_revision=2,
            groups=[SimpleNamespace(group_id="group", subtitle_ids=["a", "b"])]),
        workflows=[], candidate_inputs=[], candidate_reports=[], group_failures=[],
        timeline_clips=clips,
    )
    group = run.groups[0]
    assert group.stage == "needs_timeline_edit"
    assert group.recommended_action == "edit_timeline"
    assert group.existing_timeline_clip_ids == [f"manual-{target}" for target in targets]
    assert group.uncovered_target_subtitle_ids == ([] if targets == ["a", "b"] else ["b"])
    assert group.timeline_requires_reconciliation
    assert group.passed_candidate_id is None
    assert run.accepted_group_count == 0
    assert run.status == "needs_attention"
    assert run.attention_group_count == 1
