"""Plan replacement retains proof only for unchanged, rendered audio."""

from copy import deepcopy

import pytest
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import dubbing_production as domain
from app.domains.video_localization.dubbing_plan_continuation import retain_unchanged_completion_evidence
from app.domains.video_localization.dubbing_production_run import build_production_run_snapshot
from app.domains.video_localization.dubbing_timeline_edit_gate import (
    candidate_clip_projection_fingerprint,
    rendered_gap_duration_ms,
)
from app.schemas.video_localization_dubbing_production import (
    DubbingAudioGapEvidence,
    DubbingCandidateAlignedWord,
    DubbingCandidateCqcInput,
    DubbingGenerationPlan,
    DubbingProductionState,
)


@pytest.fixture
def completed():
    unit = dict(unit_id="u", subtitle_ids=["s"], source_cue_ids=["source"],
                speaker_id="speaker", start_ms=1000, end_ms=2000,
                source_anchor_start_ms=1000, source_anchor_end_ms=2000,
                display_text="你好", spoken_text="你好")
    group = dict(group_id="g", island_id="i", unit_ids=["u"], subtitle_ids=["s"],
                 speaker_id="speaker", spoken_text="你好", target_start_ms=1000,
                 target_end_ms=2000, source_reference_start_ms=1000,
                 source_reference_end_ms=2000)
    plan = DubbingGenerationPlan(source_revision="a" * 64, plan_revision=1,
                                 status="passed", semantic_units=[unit],
                                 speech_islands=[], groups=[group])
    frozen = DubbingCandidateCqcInput(
        source_revision=plan.source_revision, plan_revision=1, group_id="g",
        candidate_id="c", task_status="success", artifact_id="artifact",
        audio_sha256="b" * 64, expected_spoken_text="你好", reference_transcript="Hello",
        candidate_transcript="你好", target_start_ms=1000, target_end_ms=2000,
        content_evidence=dict(audio_sha256="b" * 64, engine_id="qwen3-asr-mlx",
                              status="complete", transcript="你好"),
        placement_start_ms=1050, placement_end_ms=1900,
        audio=dict(duration_ms=1000, peak_dbfs=-1, clipping_ratio=0,
                   leading_silence_ms=50, trailing_silence_ms=100,
                   speech_start_ms=50, speech_end_ms=900, speech_span_ms=850,
                   aligned_words=[dict(word_id="w", text="你好", start_ms=50, end_ms=900)],
                   speaking_rate_ratio=1,
                   gap_evidence=[dict(gap_id="head", kind="leading", start_ms=0,
                                      end_ms=50, duration_ms=50,
                                      evidence_sources=["waveform", "energy"],
                                      evidence_ids=["wave", "energy"], boundary_confidence="clear",
                                      edit_decision="retain", retained_duration_ms=50,
                                      decision_reason="起音", safe_edit_boundary=True)]),
    )
    report = domain.build_candidate_gap_processing_report(frozen)
    state = DubbingProductionState(active_plan=plan, plan_revision_counter=1,
                                   candidate_inputs=[frozen], candidate_reports=[report])
    clips = [dict(clip_id="clip", track_id="dub", dub_lane=0, candidate_id="c",
                  dubbing_group_id="g", target_subtitle_ids=["s"], start_ms=1000,
                  end_ms=2000, source_start_ms=0, source_end_ms=1000)]
    return dict(state=state, new_plan=plan.model_copy(update={"plan_revision": 2}),
                timeline_clips=clips, audio_sha256_by_clip_id={"clip": "b" * 64})


def test_identical_plan_rebinds_evidence_and_remains_complete(completed):
    original = deepcopy(completed)
    inputs, reports = retain_unchanged_completion_evidence(**completed)
    assert len(inputs) == len(reports) == 1
    assert inputs[0].plan_revision == reports[0].plan_revision == 2
    assert reports[0].evidence_fingerprint == domain.candidate_evidence_fingerprint(inputs[0])
    run = build_production_run_snapshot(
        current_source_revision=completed["new_plan"].source_revision,
        active_plan=completed["new_plan"], workflows=[], candidate_inputs=inputs,
        candidate_reports=reports, group_failures=[], timeline_clips=completed["timeline_clips"],
    )
    assert run.groups[0].stage == "accepted"
    assert completed == original


def test_unchanged_plan_keeps_exact_semantic_review(completed):
    from app.domains.video_localization import dubbing_gap_adjudication

    state = completed["state"]
    frozen = state.candidate_inputs[0]
    report = state.candidate_reports[0]
    audit = dubbing_gap_adjudication.build_semantic_boundary_audit(
        source_revision=frozen.source_revision, plan_revision=frozen.plan_revision,
        candidate_id=frozen.candidate_id, audio_sha256=frozen.audio_sha256,
        candidate_evidence_fingerprint=report.evidence_fingerprint,
        candidate_clip_projection_fingerprint=candidate_clip_projection_fingerprint(
            completed["timeline_clips"]
        ),
        expected_spoken_text=frozen.expected_spoken_text,
        aligned_words=list(frozen.audio.aligned_words),
        gaps=list(frozen.audio.gap_evidence), clips=completed["timeline_clips"],
    ).model_copy(update={"status": "accepted"})
    completed["state"] = state.model_copy(update={
        "candidate_reports": [report.model_copy(update={"semantic_boundary_audit": audit})],
    })

    inputs, reports = retain_unchanged_completion_evidence(**completed)

    kept = reports[0].semantic_boundary_audit
    assert kept is not None
    assert kept.status == "accepted"
    assert kept.plan_revision == 2
    assert kept.candidate_evidence_fingerprint == domain.candidate_evidence_fingerprint(inputs[0])
    assert kept.boundaries == audit.boundaries
    assert kept.agent_reviews == audit.agent_reviews
    assert audit.plan_revision == 1


def test_rebind_reconciles_a_previously_split_formal_gap(completed):
    state = completed["state"]
    frozen = state.candidate_inputs[0].model_copy(update={
        "placement_start_ms": 1_100,
        "placement_end_ms": 1_660,
        "audio": state.candidate_inputs[0].audio.model_copy(update={
            "speech_start_ms": 100,
            "speech_span_ms": 800,
            "aligned_words": [
                DubbingCandidateAlignedWord(
                    word_id="left", text="甲", start_ms=100, end_ms=300,
                ),
                DubbingCandidateAlignedWord(
                    word_id="right", text="乙", start_ms=700, end_ms=900,
                ),
            ],
            "gap_evidence": [DubbingAudioGapEvidence(
                gap_id="internal", kind="internal", start_ms=300,
                end_ms=700, duration_ms=400,
                evidence_sources=["vad", "word_alignment"],
                evidence_ids=["vad:internal"], boundary_confidence="clear",
                edit_decision="retain", retained_duration_ms=400,
                decision_reason="旧报告仍记录原始静音。",
                safe_edit_boundary=True,
            )],
        }),
    })
    report = domain.build_candidate_gap_processing_report(frozen)
    completed["state"] = state.model_copy(update={
        "candidate_inputs": [frozen], "candidate_reports": [report],
    })
    completed["timeline_clips"] = [
        {
            "clip_id": "left", "track_id": "dub", "dub_lane": 0,
            "candidate_id": "c", "dubbing_group_id": "g",
            "target_subtitle_ids": ["s"], "start_ms": 1_000,
            "end_ms": 1_380, "source_start_ms": 0, "source_end_ms": 380,
        },
        {
            "clip_id": "right", "track_id": "dub", "dub_lane": 0,
            "candidate_id": "c", "dubbing_group_id": "g",
            "target_subtitle_ids": ["s"], "start_ms": 1_380,
            "end_ms": 1_760, "source_start_ms": 620, "source_end_ms": 1_000,
        },
    ]
    from app.domains.video_localization import dubbing_gap_adjudication
    audit = dubbing_gap_adjudication.build_semantic_boundary_audit(
        source_revision=frozen.source_revision, plan_revision=frozen.plan_revision,
        candidate_id=frozen.candidate_id, audio_sha256=frozen.audio_sha256,
        candidate_evidence_fingerprint=report.evidence_fingerprint,
        candidate_clip_projection_fingerprint=candidate_clip_projection_fingerprint(
            completed["timeline_clips"]
        ),
        expected_spoken_text=frozen.expected_spoken_text,
        aligned_words=list(frozen.audio.aligned_words),
        gaps=list(frozen.audio.gap_evidence),
        clips=completed["timeline_clips"],
    ).model_copy(update={"status": "accepted"})
    report = report.model_copy(update={"semantic_boundary_audit": audit})
    completed["state"] = state.model_copy(update={
        "candidate_inputs": [frozen], "candidate_reports": [report],
    })
    completed["audio_sha256_by_clip_id"] = {"left": "b" * 64, "right": "b" * 64}
    assert rendered_gap_duration_ms(
        frozen.audio.gap_evidence[0].model_dump(mode="json"), completed["timeline_clips"],
    ) == 160
    old_run = build_production_run_snapshot(
        current_source_revision=completed["state"].active_plan.source_revision,
        active_plan=completed["state"].active_plan, workflows=[],
        candidate_inputs=completed["state"].candidate_inputs,
        candidate_reports=completed["state"].candidate_reports, group_failures=[],
        timeline_clips=completed["timeline_clips"],
    )
    assert old_run.groups[0].stage != "accepted"
    inputs, reports = retain_unchanged_completion_evidence(**completed)

    gap = reports[0].audio_evidence.gap_evidence[0]
    assert (gap.edit_decision, gap.retained_duration_ms) == ("shorten", 160)
    assert inputs[0].audio.gap_evidence[0].retained_duration_ms == 160
    assert reports[0].evidence_fingerprint == domain.candidate_evidence_fingerprint(inputs[0])
    run = build_production_run_snapshot(
        current_source_revision=completed["new_plan"].source_revision,
        active_plan=completed["new_plan"], workflows=[], candidate_inputs=inputs,
        candidate_reports=reports, group_failures=[], timeline_clips=completed["timeline_clips"],
    )
    assert run.groups[0].stage == "accepted"


@pytest.mark.parametrize("change", ["source", "text", "source_binding", "target_binding", "time",
                                   "missing_input", "missing_report", "hash", "missing_file",
                                   "moved", "cropped", "gap_changed", "fingerprint", "transcript",
                                   "content_missing"])
def test_changed_or_absent_proof_is_not_promoted(completed, change):
    state = completed["state"]
    plan = completed["new_plan"]
    if change == "source":
        completed["new_plan"] = plan.model_copy(update={"source_revision": "d" * 64})
    elif change in {"text", "target_binding", "time"}:
        update = {"text": {"spoken_text": "再见"}, "target_binding": {"subtitle_ids": ["new"]},
                  "time": {"target_end_ms": 2100}}[change]
        completed["new_plan"] = plan.model_copy(update={"groups": [plan.groups[0].model_copy(update=update)]})
    elif change == "source_binding":
        completed["new_plan"] = plan.model_copy(update={"semantic_units": [
            plan.semantic_units[0].model_copy(update={"source_cue_ids": ["other"]})]})
    elif change in {"missing_input", "missing_report"}:
        field = "candidate_inputs" if change == "missing_input" else "candidate_reports"
        completed["state"] = state.model_copy(update={field: []})
    elif change in {"hash", "missing_file"}:
        completed["audio_sha256_by_clip_id"] = {"clip": "e" * 64 if change == "hash" else None}
    elif change == "moved":
        completed["timeline_clips"][0].update(start_ms=1100, end_ms=2100)
    elif change == "cropped":
        completed["timeline_clips"][0].update(source_end_ms=850, end_ms=1850)
    elif change == "gap_changed":
        completed["timeline_clips"][0].update(source_start_ms=40, start_ms=1040)
    elif change == "fingerprint":
        completed["state"] = state.model_copy(update={"candidate_reports": [
            state.candidate_reports[0].model_copy(update={"evidence_fingerprint": "f" * 64})]})
    elif change == "transcript":
        completed["state"] = state.model_copy(update={"candidate_inputs": [
            state.candidate_inputs[0].model_copy(update={"candidate_transcript": "other"})]})
    elif change == "content_missing":
        completed["state"] = state.model_copy(update={"candidate_inputs": [
            state.candidate_inputs[0].model_copy(update={"content_evidence": None})]})
    assert retain_unchanged_completion_evidence(**completed) == ([], [])


def test_unrelated_plan_expansion_keeps_completed_group(completed):
    plan = completed["new_plan"]
    completed["new_plan"] = plan.model_copy(update={"groups": [*plan.groups,
        plan.groups[0].model_copy(update={"group_id": "another", "subtitle_ids": ["other"]})]})
    inputs, reports = retain_unchanged_completion_evidence(**completed)
    assert [item.group_id for item in inputs] == ["g"]
    assert [item.group_id for item in reports] == ["g"]


def test_completed_projection_does_not_require_raw_transcript(completed):
    state = completed["state"]
    frozen = state.candidate_inputs[0].model_copy(update={"content_evidence": None})
    completed["state"] = state.model_copy(update={
        "candidate_inputs": [frozen],
        "candidate_reports": [domain.build_candidate_gap_processing_report(frozen)],
    })
    inputs, reports = retain_unchanged_completion_evidence(**completed)
    assert len(inputs) == len(reports) == 1
    assert inputs[0].content_evidence is None
    assert reports[0].transcript.coverage_ratio == 0


def test_plan_commit_preserves_proof_in_atomic_write(completed, monkeypatch):
    from app.domains.video_localization import dubbing_production_service as service
    from app.domains.video_localization.schemas import VideoLocalizationDraft

    draft = VideoLocalizationDraft(
        dubbing_production=completed["state"], timeline_clips=completed["timeline_clips"],
    )
    saved = []

    def atomic_update(project_id, apply, *, intent):
        assert project_id == "isolated-project"
        saved.append(apply(draft))
        return saved[-1]

    monkeypatch.setattr(service.project_service, "update_video_localization_atomic", atomic_update)
    monkeypatch.setattr(domain, "dubbing_source_revision", lambda current: completed["new_plan"].source_revision)
    monkeypatch.setattr(service.dubbing_media, "current_timeline_audio_sha256s",
                        lambda project_id, current: completed["audio_sha256_by_clip_id"])
    result = service.DubbingProductionApplicationService._persist_plan(
        "isolated-project", completed["new_plan"],
    )
    assert result.plan_revision == 2
    assert saved[0].dubbing_production.candidate_reports[0].plan_revision == 2
    assert saved[0].dubbing_production.candidate_inputs[0].candidate_id == "c"
    assert saved[0].timeline_clips == draft.timeline_clips


def test_distant_source_edit_reuses_verified_local_evidence(completed):
    from app.domains.video_localization.schemas import VideoLocalizationDraft
    draft = VideoLocalizationDraft()
    old = completed['state']
    plan = old.active_plan
    context = domain.group_evidence_context_fingerprint(draft, plan, plan.groups[0])
    frozen = old.candidate_inputs[0].model_copy(update={'source_context_fingerprint': context})
    completed['state'] = old.model_copy(update={'candidate_inputs': [frozen]})
    completed['new_plan'] = completed['new_plan'].model_copy(update={'source_revision': 'd' * 64})
    completed['current_draft'] = draft
    inputs, reports = retain_unchanged_completion_evidence(**completed)
    assert len(inputs) == len(reports) == 1
    assert inputs[0].source_revision == 'd' * 64
    assert inputs[0].evidence_origin_source_revision == 'a' * 64
    assert reports[0].automatic_status == old.candidate_reports[0].automatic_status
    assert reports[0].evidence_fingerprint == domain.candidate_evidence_fingerprint(inputs[0])
    completed['current_draft'] = draft.model_copy(update={'scene_context': {'changed': True}})
    assert retain_unchanged_completion_evidence(**completed) == ([], [])


def test_source_context_tracks_adjacent_units_but_not_distant_units(completed):
    from app.domains.video_localization.schemas import VideoLocalizationDraft
    plan = completed["state"].active_plan
    unit = plan.semantic_units[0]
    adjacent = unit.model_copy(update={"unit_id": "adjacent", "subtitle_ids": ["adjacent"],
                                       "source_cue_ids": ["adjacent"], "start_ms": 2100, "end_ms": 3000})
    distant = unit.model_copy(update={"unit_id": "distant", "subtitle_ids": ["distant"],
                                      "source_cue_ids": ["distant"], "start_ms": 8000, "end_ms": 9000})
    plan = plan.model_copy(update={"semantic_units": [unit, adjacent, distant]})
    draft = VideoLocalizationDraft()
    original = domain.group_evidence_context_fingerprint(draft, plan, plan.groups[0])
    far = plan.model_copy(update={"semantic_units": [unit, adjacent, distant.model_copy(update={"spoken_text": "远处修改"})]})
    assert domain.group_evidence_context_fingerprint(draft, far, far.groups[0]) == original
    near = plan.model_copy(update={"semantic_units": [unit, adjacent.model_copy(update={"spoken_text": "接缝修改"}), distant]})
    assert domain.group_evidence_context_fingerprint(draft, near, near.groups[0]) != original


def test_continuation_uses_word_anchor_and_vad_protected_tail(completed):
    """The proof uses the same source bounds as final placement."""
    state = completed["state"]
    frozen = state.candidate_inputs[0]
    audio = frozen.audio.model_copy(update={
        "speech_start_ms": 150,
        "speech_end_ms": 850,
        "speech_span_ms": 700,
        "aligned_words": [
            frozen.audio.aligned_words[0].model_copy(update={"start_ms": 100, "end_ms": 900}),
        ],
    })
    frozen = frozen.model_copy(update={
        "audio": audio,
        "placement_start_ms": 1100,
        "placement_end_ms": 1900,
    })
    report = domain.build_candidate_gap_processing_report(frozen)
    completed["state"] = state.model_copy(update={
        "candidate_inputs": [frozen], "candidate_reports": [report],
    })
    completed["timeline_clips"][0].update(
        start_ms=1000, end_ms=2000, source_start_ms=0, source_end_ms=1000,
    )

    inputs, reports = retain_unchanged_completion_evidence(**completed)

    assert [item.candidate_id for item in inputs] == ["c"]
    assert [item.candidate_id for item in reports] == ["c"]
