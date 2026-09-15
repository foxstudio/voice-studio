import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domains.video_localization import dubbing_production as domain
from app.domains.video_localization.dubbing_text_rebase import rebase_text_edit
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft, VideoLocalizationSubtitleCue, VideoLocalizationCue,
    VideoLocalizationSpokenSegment,
)
from app.schemas.video_localization_dubbing_production import (
    DubbingGenerationPlanInput, DubbingProductionState, DubbingCandidateCqcInput,
    DubbingAutomaticAudioEvidence, DubbingCandidateAlignedWord, DubbingAudioGapEvidence,
)


def draft_with_plan(*, dual_tracks=False, proven=False):
    draft = VideoLocalizationDraft(localized_subtitles=[
        VideoLocalizationSubtitleCue(subtitle_id=f"s{i}", start_ms=i*3000,
                                     end_ms=i*3000+1000, text=f"Sentence {i}.", tts_text=f"Sentence {i}.")
        for i in range(3)
    ])
    if dual_tracks:
        draft = draft.model_copy(update={
            "localized_subtitles": [item.model_copy(update={"spoken_segment_id": f"sp{i}",
                "source_cue_ids": [f"cue{i}"], "source_word_ids": [f"w{i}"]})
                for i,item in enumerate(draft.localized_subtitles)],
            "cues": [VideoLocalizationCue(cue_id=f"cue{i}", speaker_id=f"speaker{i}",
                start_ms=i*3000, end_ms=i*3000+1000, en_subtitle_text=f"Source {i}.",
                source_word_ids=[f"w{i}"]) for i in range(3)],
            "localized_spoken_segments": [VideoLocalizationSpokenSegment(segment_id=f"sp{i}",
                paragraph_id=f"p{i}", text=f"Sentence {i}.", start_ms=i*3000+20,
                end_ms=i*3000+980,source_cue_ids=[f"cue{i}"],source_word_ids=[f"w{i}"])
                for i in range(3)],
        })
    snapshot = domain.build_project_snapshot(draft)
    units = [unit.model_copy(update={"speech_policy": "translate", "speaker_id": f"speaker{i}"})
             for i, unit in enumerate(snapshot.semantic_units)]
    plan = domain.build_generation_plan(DubbingGenerationPlanInput(
        source_revision=snapshot.source_revision, semantic_units=units, boundaries=snapshot.boundaries,
    )).model_copy(update={"plan_revision": 1})
    inputs = [DubbingCandidateCqcInput(source_revision=plan.source_revision, plan_revision=1,
              group_id=g.group_id, candidate_id=f"c{i}", task_status="success",
              source_context_fingerprint=(domain.group_evidence_context_fingerprint(draft, plan, g) if proven else None),
              placement_start_ms=g.target_start_ms+100, placement_end_ms=g.target_start_ms+900,
              expected_spoken_text=g.spoken_text, candidate_transcript=g.spoken_text,
              target_start_ms=g.target_start_ms, target_end_ms=g.target_end_ms,
              artifact_id=f"artifact{i}", audio_sha256="b"*64,
              audio=DubbingAutomaticAudioEvidence(duration_ms=1000, peak_dbfs=-3,
                  clipping_ratio=0, leading_silence_ms=100, trailing_silence_ms=100,
                  speech_start_ms=100, speech_end_ms=900, speech_span_ms=800,
                  aligned_words=[DubbingCandidateAlignedWord(word_id="w", text=g.spoken_text, start_ms=100, end_ms=900)],
                  gap_evidence=[DubbingAudioGapEvidence(gap_id="head", kind="leading",
                      start_ms=0,end_ms=100,duration_ms=100,evidence_sources=["waveform"],
                      evidence_ids=["head"], boundary_confidence="clear",edit_decision="retain",
                      retained_duration_ms=100,decision_reason="preserve",safe_edit_boundary=True)]))
              for i,g in enumerate(plan.groups)]
    return draft.model_copy(update={"dubbing_production": DubbingProductionState(
        active_plan=plan, plan_revision_counter=1, candidate_inputs=inputs,
        candidate_reports=[domain.evaluate_candidate(item) for item in inputs]),
        "timeline_clips": [{"clip_id": f"clip{i}", "track_id": "dub", "status": "ready",
            "candidate_id": f"c{i}", "dubbing_group_id": g.group_id, "dub_lane": 0,
            "target_subtitle_ids": g.subtitle_ids,"start_ms": g.target_start_ms,
            "end_ms": g.target_start_ms+1000,"source_start_ms":0,"source_end_ms":1000}
            for i,g in enumerate(plan.groups)],
        "ui_state": {"discarded_tts_task_ids": ["user-deleted"]}})


def edit(draft, **fields):
    return draft.model_copy(update={"localized_subtitles": [
        item.model_copy(update=fields) if index == 0 else item
        for index,item in enumerate(draft.localized_subtitles)]})


@pytest.mark.parametrize("invalid", [None, "text", "cancelled", "discarded"])
def test_text_edit_preserves_unplaced_generation_as_pending_work_not_a_new_take(invalid):
    from app.schemas.voice_studio import GenerateRequest, VideoLocalizationTtsTask
    from app.domains.video_localization.dubbing_production_service import DubbingProductionApplicationService
    before = draft_with_plan(proven=True)
    group = before.dubbing_production.active_plan.groups[1]
    request = GenerateRequest(
        text="different" if invalid == "text" else group.spoken_text,
        engine_id="omnivoice", project_id="isolated", source="video_localization",
        video_localization_dubbing_group_id=group.group_id,
        video_localization_dubbing_plan_revision=1,
        video_localization_target_subtitle_ids=group.subtitle_ids,
        video_localization_source_cue_ids=[],
        ref_text="A fixed source phrase.", reference_audio_path="/fixture/reference.wav",
        custom_reference_source_audio_path="/fixture/vocals.wav",
        custom_reference_trim_start_ms=3000, custom_reference_trim_end_ms=4000,
    )
    workflow = VideoLocalizationTtsTask(
        workflow_id="frozen-workflow", project_id="isolated", segment_id="s1",
        subtitle_summary=group.spoken_text, text=group.spoken_text,
        start_ms=3000, end_ms=4000,
        status="cancelled" if invalid == "cancelled" else "needs_attention",
        generation_task_id="frozen-task", result_id="frozen-result",
        stages=[{"kind":"generation", "status":"success", "parameters":request.model_dump(mode="json")},
                {"kind":"placement", "status":"pending"}],
    )
    before = before.model_copy(update={
        "tts_tasks":[workflow],
        "timeline_clips":[clip for clip in before.timeline_clips if clip["clip_id"] != "clip1"],
        "ui_state":{"discarded_tts_task_ids":["frozen-workflow"] if invalid == "discarded" else []},
    })
    after = rebase_text_edit(before, edit(before, tts_text="Changed neighboring words."))
    assert after.tts_tasks == before.tts_tasks
    assert after.timeline_clips == before.timeline_clips
    assert not any(item.group_id == group.group_id for item in after.dubbing_production.candidate_inputs)
    run = DubbingProductionApplicationService._build_production_run_snapshot(after)
    progress = run.groups[1]
    assert progress.stage == ("needs_gap_processing" if invalid is None else "ready_to_generate")
    assert ("frozen-result" in progress.candidate_ids) is (invalid is None)
    assert progress.passed_candidate_id is None
    assert progress.formal_clip_ids == []


def test_text_edit_preserves_grouping_and_only_rebinds_unchanged_evidence(monkeypatch):
    from app.domains.video_localization import dubbing_media
    monkeypatch.setattr(dubbing_media, "current_timeline_audio_sha256s", lambda *_: {f"clip{i}": "b"*64 for i in range(3)})
    before = draft_with_plan(proven=True)
    after = edit(before, tts_text="A revised sentence.")
    result = rebase_text_edit(before, after, project_id="isolated")
    old = before.dubbing_production.active_plan
    new = result.dubbing_production.active_plan
    assert new.plan_revision == 2 and new.source_revision != old.source_revision
    assert [(g.group_id,g.subtitle_ids,g.unit_ids) for g in new.groups] == [
        (g.group_id,g.subtitle_ids,g.unit_ids) for g in old.groups]
    assert new.groups[0].spoken_text == "A revised sentence."
    assert new.groups[1:] == old.groups[1:]
    assert {item.candidate_id for item in result.dubbing_production.candidate_inputs} == {"c2"}
    assert all(item.plan_revision == 2 for item in result.dubbing_production.candidate_reports)
    assert result.timeline_clips == after.timeline_clips
    assert result.tts_tasks == before.tts_tasks
    assert result.ui_state == before.ui_state


def test_time_edit_and_stale_plan_do_not_take_text_path():
    before = draft_with_plan()
    after = edit(before, tts_text="Changed", end_ms=1200)
    assert rebase_text_edit(before, after) is after
    altered = before.model_copy(update={"scene_context": "new context"})
    after = edit(altered, tts_text="Changed")
    assert rebase_text_edit(altered, after) is after


@pytest.mark.parametrize("change", ["unrelated", "stale_receipt", "own_text", "neighbor_text", "audio", "crop", "deleted"])
def test_text_rebase_retains_only_dependency_checked_parked_take(monkeypatch, change):
    import hashlib
    import json
    from app.domains.video_localization import dubbing_media
    from app.domains.video_localization.dubbing_timeline_edit_gate import candidate_clip_projection_fingerprint
    from app.schemas.video_localization_dubbing_production import DubbingManualTimingDeferral

    before = draft_with_plan(dual_tracks=True, proven=True)
    plan = before.dubbing_production.active_plan
    group = plan.groups[2]
    parked = {**before.timeline_clips[2], "dub_lane": 1, "result_id": "r2",
              "audio_path": "/fixture/parked.wav", "source_cue_ids": ["cue2"],
              "source_end_ms": 4000, "end_ms": group.target_start_ms + 4000}
    receipt = DubbingManualTimingDeferral(
        request_id="park", request_fingerprint="a"*64,
        source_revision=plan.source_revision, plan_revision=plan.plan_revision,
        evidence_origin_source_revision=plan.source_revision, evidence_origin_plan_revision=plan.plan_revision,
        group_id=group.group_id, candidate_id="c2", result_id="r2", parked_clip_id="clip2",
        target_subtitle_ids=group.subtitle_ids, source_cue_ids=["cue2"],
        available_duration_ms=1000, candidate_duration_ms=4000, audio_sha256="b"*64,
        source_context_fingerprint=domain.group_evidence_context_fingerprint(before, plan, group),
        candidate_spoken_text_fingerprint=hashlib.sha256(json.dumps(
            domain.transcript_pronunciation_tokens(group.spoken_text), ensure_ascii=False,
            separators=(",", ":"), sort_keys=True).encode()).hexdigest(),
        clip_projection_fingerprint=candidate_clip_projection_fingerprint([parked]),
        content_verification_status="verified_complete", content_verification_evidence_ids=["fixed-content"],
        semantic_boundary_review="agent_asserted_complete", semantic_boundary_evidence_ids=["fixed-boundaries"],
        naturalness_review="not_claimed", recovery_evidence=[
            {"strategy": strategy, "outcome": "applied" if strategy == "verify_window_and_group" else "exhausted", "evidence_ids": ["fixed"], "reason": "fixed fixture"}
            for strategy in ["verify_window_and_group", "safe_gap_edit", "allowed_speed", "whole_regeneration", "semantic_split", "equivalent_text_compression"]
        ], created_at="2026-01-01T00:00:00",
    )
    before.timeline_clips[2] = parked
    before.dubbing_production = before.dubbing_production.model_copy(update={"manual_timing_deferrals": [receipt]})
    if change == "stale_receipt":
        before.dubbing_production = before.dubbing_production.model_copy(update={
            "active_plan": plan.model_copy(update={"plan_revision": 2}), "plan_revision_counter": 2,
        })
    edited_index = 2 if change == "own_text" else 1 if change == "neighbor_text" else 0
    after = before.model_copy(update={"localized_subtitles": [
        item.model_copy(update={"tts_text": "Changed wording."}) if i == edited_index else item
        for i, item in enumerate(before.localized_subtitles)
    ]})
    if change == "crop":
        after = after.model_copy(update={"timeline_clips": [*after.timeline_clips[:2], {**parked, "source_start_ms": 80, "start_ms": parked["start_ms"]+80}]})
    elif change == "deleted":
        after = after.model_copy(update={"timeline_clips": after.timeline_clips[:2]})
    monkeypatch.setattr(dubbing_media, "current_timeline_audio_sha256s", lambda *_: {
        f"clip{i}": ("c"*64 if change == "audio" and i == 2 else "b"*64) for i in range(3)
    })
    result = rebase_text_edit(before, after, project_id="isolated")
    retained = result.dubbing_production.manual_timing_deferrals
    if change in {"unrelated", "stale_receipt"}:
        assert len(retained) == 1
        assert retained[0].plan_revision == result.dubbing_production.active_plan.plan_revision
        assert retained[0].source_revision == result.dubbing_production.active_plan.source_revision
        assert retained[0].evidence_origin_plan_revision == 1
        assert retained[0].clip_projection_fingerprint == receipt.clip_projection_fingerprint
    else:
        assert retained == []
    assert result.timeline_clips == after.timeline_clips


def test_display_only_edit_does_not_rewrite_independent_spoken_text(monkeypatch):
    from app.domains.video_localization import dubbing_media
    monkeypatch.setattr(dubbing_media, "current_timeline_audio_sha256s", lambda *_: {f"clip{i}": "b"*64 for i in range(3)})
    before = draft_with_plan(dual_tracks=True, proven=True)
    after = edit(before, text="A display-only wording change.")
    result = rebase_text_edit(before, after, project_id="isolated")
    assert len(result.dubbing_production.candidate_reports) == 3
    assert result.dubbing_production.active_plan.groups == before.dubbing_production.active_plan.groups
    assert result.localized_spoken_segments == before.localized_spoken_segments


def test_corrupt_report_is_not_recertified_and_deleted_clip_not_restored():
    before = draft_with_plan()
    before.dubbing_production = before.dubbing_production.model_copy(update={
        "candidate_reports": [item.model_copy(update={"evidence_fingerprint": "a"*64})
                              for item in before.dubbing_production.candidate_reports]})
    after = edit(before, tts_text="Changed").model_copy(update={"timeline_clips": []})
    result = rebase_text_edit(before, after)
    assert result.dubbing_production.candidate_reports == []
    assert result.timeline_clips == []


def test_public_subtitle_patch_persists_local_rebase(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.services import database
    from app.main import app
    from app.schemas.voice_studio import AppSettings
    from app.services import settings_store
    from app.domains.video_localization import draft_store, dubbing_media
    monkeypatch.setattr(dubbing_media, "current_timeline_audio_sha256s", lambda *_: {f"clip{i}": "b"*64 for i in range(3)})

    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(AppSettings(data_dir=str(tmp_path), **{
        f"{name}_dir": str(tmp_path / name)
        for name in ["voice", "output", "export", "project", "cache", "log"]}))
    with TestClient(app) as client:
        project_id = client.post("/api/projects", json={"name": "text-rebase"}).json()["project_id"]
        before = draft_store.save(project_id, draft_with_plan(dual_tracks=True, proven=True), intent="content")
        assert before.dubbing_production.active_plan.plan_revision == 1
        initial_run = client.get(f"/api/projects/{project_id}/video-localization/dubbing/production-run").json()
        assert [group["stage"] for group in initial_run["groups"]] == ["accepted"]*3
        response = client.patch(f"/api/projects/{project_id}/video-localization/localized-subtitles/s0",
                                json={"tts_text": "Revised spoken words."})
        assert response.status_code == 200, response.text
        saved = draft_store.get(project_id)
        assert saved.dubbing_production.active_plan.plan_revision == 2
        assert saved.dubbing_production.active_plan.groups[1:] == before.dubbing_production.active_plan.groups[1:]
        assert saved.localized_spoken_segments[1:] == before.localized_spoken_segments[1:]
        assert [(s.start_ms,s.end_ms) for s in saved.localized_spoken_segments] == [
            (s.start_ms,s.end_ms) for s in before.localized_spoken_segments]
        assert saved.timeline_clips[1:] == before.timeline_clips[1:]
        assert saved.timeline_clips[0]["tts_target_binding_status"] == "stale"
        assert all(saved.timeline_clips[0][key] == value for key,value in before.timeline_clips[0].items())
        assert {item.candidate_id for item in saved.dubbing_production.candidate_inputs} == {"c2"}
        refreshed = client.get(f"/api/projects/{project_id}/video-localization").json()
        assert refreshed["localized_subtitles"][0]["tts_text"] == "Revised spoken words."
        assert refreshed["dubbing_production"]["active_plan"]["groups"][0]["spoken_text"] == "Revised spoken words."
        run = client.get(f"/api/projects/{project_id}/video-localization/dubbing/production-run").json()
        assert run["groups"][1]["stage"] != "accepted"  # Adjacent join context changed.
        assert run["groups"][2]["stage"] == "accepted"
        assert run["groups"][0]["stage"] != "accepted"

        # A page can still hold the snapshot from before the typed subtitle
        # command. Giving that stale content a fresh conflict token must not
        # roll back the wording or rebuild the reviewed plan on workspace save.
        stale_workspace = before.model_copy(
            update={
                "updated_at": saved.updated_at,
                "ui_state": {**before.ui_state, "selected_cue_id": "cue1"},
            }
        )
        workspace_response = client.put(
            f"/api/projects/{project_id}/video-localization/workspace",
            json=stale_workspace.model_dump(mode="json"),
        )
        assert workspace_response.status_code == 200, workspace_response.text
        after_workspace_save = draft_store.get(project_id)
        assert after_workspace_save.localized_subtitles[0].tts_text == "Revised spoken words."
        assert after_workspace_save.localized_spoken_segments[0].text == "Revised spoken words."
        assert after_workspace_save.dubbing_production.active_plan.plan_revision == 2
        assert after_workspace_save.dubbing_production.active_plan.groups == saved.dubbing_production.active_plan.groups


def test_legacy_text_rebase_keeps_audio_without_promoting_unverifiable_proof():
    before = draft_with_plan()
    after = edit(before, tts_text="Changed")
    result = rebase_text_edit(before, after)
    assert result.dubbing_production.candidate_reports == []
    assert result.timeline_clips == before.timeline_clips
