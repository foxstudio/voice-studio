from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from app.domains.video_localization import (
    dubbing_media,
    dubbing_production as domain,
    dubbing_production_service as service_module,
)
from app.domains.video_localization.dubbing_production_service import (
    DubbingProductionApplicationService,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
)
from app.errors import AppException
from app.schemas.tts_content import TtsContentEvidence
from app.schemas.voice_studio import (
    VideoLocalizationTtsTask,
    VideoLocalizationTtsTaskStage,
)
from app.schemas.video_localization_dubbing_production import (
    DubbingAutomaticAudioEvidence,
    DubbingAudioGapEvidence,
    DubbingCandidateAlignedWord,
    DubbingCandidateContentEvidenceRequest,
    DubbingCurrentProjectionRequest,
    DubbingCandidateCqcInput,
    DubbingRetainedContentEvidence,
    DubbingStagedCandidateSplitRequest,
    DubbingTimelineAlignedSlice,
    DubbingTimelineClipSplitCommand,
    DubbingCapacityRecoveryEvidence,
    DubbingGenerationGroup,
    DubbingGenerationPlan,
    DubbingManualTimingDeferralRequest,
    DubbingProductionState,
    DubbingSemanticUnit,
)


def _recovery_evidence() -> list[DubbingCapacityRecoveryEvidence]:
    return [
        DubbingCapacityRecoveryEvidence(
            strategy=strategy,
            outcome="applied" if strategy == "verify_window_and_group" else "exhausted",
            evidence_ids=[f"evidence:{strategy}"],
            reason=f"已核对 {strategy}",
            attempt_count=(2 if strategy in {"whole_regeneration", "equivalent_text_compression"} else 1),
        )
        for strategy in (
            "verify_window_and_group",
            "safe_gap_edit",
            "allowed_speed",
            "whole_regeneration",
            "semantic_split",
            "equivalent_text_compression",
        )
    ]


def _fixture(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "complete.wav"
    sf.write(audio_path, np.zeros(64_000, dtype=np.float32), 16_000)
    audio_sha256 = service_module.media_assets.file_sha256(audio_path)
    subtitle = VideoLocalizationSubtitleCue(
        subtitle_id="subtitle-1",
        text="压缩后的完整文本",
        start_ms=1_000,
        end_ms=3_000,
        source_cue_ids=["cue-1", "cue-2"],
    )
    subtitle_2 = VideoLocalizationSubtitleCue(
        subtitle_id="subtitle-2",
        text="文本",
        start_ms=2_000,
        end_ms=3_000,
        source_cue_ids=["cue-2"],
    )
    base = VideoLocalizationDraft(
        source_media={"duration_ms": 8_000},
        localized_subtitles=[subtitle, subtitle_2],
    )
    source_revision = domain.dubbing_source_revision(base)
    unit = DubbingSemanticUnit(
        unit_id="unit-1",
        subtitle_ids=[subtitle.subtitle_id, subtitle_2.subtitle_id],
        source_cue_ids=["cue-1", "cue-2"],
        speaker_id="speaker-1",
        start_ms=1_000,
        end_ms=3_000,
        source_anchor_start_ms=1_000,
        source_anchor_end_ms=3_000,
        display_text=subtitle.text,
        spoken_text=subtitle.text,
    )
    group = DubbingGenerationGroup(
        group_id="group-1",
        island_id="island-1",
        unit_ids=[unit.unit_id],
        subtitle_ids=[subtitle.subtitle_id, subtitle_2.subtitle_id],
        speaker_id="speaker-1",
        spoken_text=subtitle.text,
        target_start_ms=1_000,
        target_end_ms=3_000,
        source_reference_start_ms=1_000,
        source_reference_end_ms=3_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=source_revision,
        plan_revision=1,
        status="passed",
        semantic_units=[unit],
        speech_islands=[],
        groups=[group],
    )
    content = TtsContentEvidence(
        audio_sha256=audio_sha256,
        engine_id="qwen3-asr-mlx",
        status="complete",
        transcript=subtitle.text,
    )
    frozen = DubbingCandidateCqcInput(
        source_revision=source_revision,
        plan_revision=1,
        group_id=group.group_id,
        candidate_id="candidate-1",
        task_status="success",
        artifact_id="artifact-1",
        audio_sha256=audio_sha256,
        source_context_fingerprint=domain.group_evidence_context_fingerprint(base, plan, group),
        expected_spoken_text=subtitle.text,
        reference_transcript="source",
        candidate_transcript=subtitle.text,
        content_evidence=content,
        target_start_ms=1_000,
        target_end_ms=3_000,
        audio=DubbingAutomaticAudioEvidence(
            duration_ms=4_000,
            peak_dbfs=-10,
            clipping_ratio=0,
            leading_silence_ms=50,
            trailing_silence_ms=50,
            speech_start_ms=50,
            speech_end_ms=3_950,
            speech_span_ms=3_900,
            aligned_words=[
                DubbingCandidateAlignedWord(
                    word_id="word-1", text=subtitle.text, start_ms=50, end_ms=3_950
                )
            ],
            gap_evidence=[],
        ),
    )
    primary = {
        "clip_id": "primary-user-edit",
        "track_id": "dub",
        "dub_lane": 0,
        "subtitle_id": subtitle.subtitle_id,
        "target_subtitle_ids": [subtitle.subtitle_id],
        "source_cue_ids": ["cue-1", "cue-2"],
        "start_ms": 1_100,
        "end_ms": 2_900,
        "source_start_ms": 100,
        "source_end_ms": 1_900,
        "audio_path": str(tmp_path / "primary.wav"),
        "status": "ready",
    }
    draft = base.model_copy(
        update={
            "dubbing_production": DubbingProductionState(
                active_plan=plan,
                plan_revision_counter=1,
                candidate_inputs=[frozen],
            ),
            "generated_candidates": [
                {"candidate_id": "candidate-1", "result_id": "result-1", "task_id": "task-1"}
            ],
            "timeline_clips": [primary],
        }
    )
    draft._repository_revision = 7
    state = {"draft": draft, "writes": 0}

    def atomic_update(_project_id, updater, *, intent):
        assert intent == "content"
        current = state["draft"]
        updated = updater(current)
        if updated != current:
            updated._repository_revision = int(current._repository_revision or 0) + 1
            state["draft"] = updated
            state["writes"] += 1
        return state["draft"]

    history = SimpleNamespace(
        result_id="result-1",
        task_id="task-1",
        generation_id="generation-1",
        project_id="project-1",
        input_text=subtitle.text,
        duration_ms=4_000,
        parameter_snapshot={"source": "video_localization"},
    )
    task = SimpleNamespace(
        status=SimpleNamespace(value="success"),
        localized_subtitle_id=subtitle.subtitle_id,
        input_text=subtitle.text,
        parameters={
            "source": "video_localization",
            "text": subtitle.text,
            "video_localization_dubbing_group_id": group.group_id,
            "video_localization_dubbing_plan_revision": 1,
            "video_localization_target_subtitle_ids": [
                subtitle.subtitle_id,
                subtitle_2.subtitle_id,
            ],
            "video_localization_source_cue_ids": ["cue-1", "cue-2"],
        },
    )
    monkeypatch.setattr(service_module.project_service, "update_video_localization_atomic", atomic_update)
    monkeypatch.setattr(service_module.draft_store, "get", lambda _project_id: state["draft"])
    monkeypatch.setattr(service_module.history_store, "get", lambda result_id: history if result_id == "result-1" else None)
    monkeypatch.setattr(service_module.history_store, "audio_path", lambda result_id: audio_path if result_id == "result-1" else None)
    monkeypatch.setattr(service_module.task_queue, "get_task", lambda task_id: task if task_id == "task-1" else None)
    monkeypatch.setattr(service_module.media_assets, "adopt_tts_audio", lambda *_args: audio_path)
    monkeypatch.setattr(
        dubbing_media,
        "current_timeline_audio_durations",
        lambda _project_id, current: {
            str(clip["clip_id"]): (4_000 if clip["clip_id"] == "parked-1" else None)
            for clip in current.timeline_clips
        },
    )
    monkeypatch.setattr(
        dubbing_media,
        "current_timeline_audio_sha256s",
        lambda _project_id, current: {
            str(clip["clip_id"]): (audio_sha256 if clip["clip_id"] == "parked-1" else None)
            for clip in current.timeline_clips
        },
    )
    payload = DubbingManualTimingDeferralRequest(
        request_id="manual-timing-1",
        expected_repository_revision=7,
        source_revision=source_revision,
        plan_revision=1,
        group_id=group.group_id,
        candidate_id="candidate-1",
        result_id="result-1",
        parked_clip_id="parked-1",
        available_duration_ms=2_050,
        candidate_duration_ms=4_000,
        audio_sha256=audio_sha256,
        content_verification_status="verified_complete",
        content_verification_evidence_ids=[
            f"tts-content-evidence-v1:{audio_sha256}:qwen3-asr-mlx:open-asr-auto-no-hints-v1"
        ],
        semantic_boundary_review="agent_asserted_complete",
        semantic_boundary_evidence_ids=["artifact-1:boundaries"],
        naturalness_review="agent_listened_acceptable",
        recovery_evidence=_recovery_evidence(),
    )
    return state, payload, primary, audio_sha256


@pytest.mark.parametrize("invalid", [None, "revision", "projection", "cut_word", "audio", "capacity", "overlap", "order", "group"])
@pytest.mark.parametrize("has_group_metadata", [True, False])
def test_refresh_actual_projection_never_rearranges_or_generates(tmp_path, monkeypatch, invalid, has_group_metadata):
    state, payload, _primary, audio_hash = _fixture(tmp_path, monkeypatch)
    draft = state["draft"]
    frozen = draft.dubbing_production.candidate_inputs[0]
    frozen = frozen.model_copy(update={"audio": frozen.audio.model_copy(update={
        "speech_end_ms": 1750, "speech_span_ms": 1700,
        "aligned_words": [frozen.audio.aligned_words[0].model_copy(update={"end_ms": 1750})],
    })})
    draft = draft.model_copy(update={"dubbing_production": draft.dubbing_production.model_copy(
        update={"candidate_inputs": [frozen]})})
    clip = {
        "clip_id": "actual", "candidate_id": payload.candidate_id, "track_id": "dub",
        "status": "ready", "dub_lane": 0, "start_ms": 1000, "end_ms": 2800,
        "source_start_ms": 0, "source_end_ms": 1800,
        "target_subtitle_ids": ["subtitle-1", "subtitle-2"],
        "dubbing_group_id": payload.group_id, "result_id": payload.result_id,
        "audio_path": str(tmp_path / "complete.wav"),
    }
    if not has_group_metadata:
        clip.pop("dubbing_group_id")
    if invalid == "group":
        clip['dubbing_group_id'] = 'another-group'
    if invalid == "cut_word":
        clip.update(source_start_ms=500, end_ms=2300)
    if invalid == "capacity":
        clip.update(start_ms=2000, end_ms=3800)
    state["draft"] = draft.model_copy(update={"timeline_clips": [clip]})
    if invalid == "overlap":
        state["draft"].timeline_clips.append({**clip, "clip_id": "other-group",
                                            "target_subtitle_ids": ["other-subtitle"]})
    if invalid == "order":
        state["draft"].timeline_clips.append({**clip, "clip_id": "out-of-order",
                                            "start_ms": 2900, "end_ms": 3000,
                                            "source_start_ms": 0, "source_end_ms": 100})
    state["draft"]._repository_revision = 7
    before = state["draft"].model_copy(deep=True)
    monkeypatch.setattr(dubbing_media, "current_timeline_audio_sha256s",
                        lambda *_: {item["clip_id"]: "f" * 64 if invalid == "audio" else audio_hash
                                    for item in state["draft"].timeline_clips})
    monkeypatch.setattr(service_module.tts_content_verification, "acquire_content_evidence",
                        lambda *_: pytest.fail("Refresh must not run ASR"))
    command = DubbingCurrentProjectionRequest(
        expected_repository_revision=6 if invalid == "revision" else 7,
        source_revision=payload.source_revision, plan_revision=payload.plan_revision,
        group_id=payload.group_id, candidate_id=payload.candidate_id, result_id=payload.result_id,
        candidate_clip_projection_fingerprint=(
            "f" * 64 if invalid == "projection"
            else service_module.candidate_clip_projection_fingerprint([
                item for item in state["draft"].timeline_clips
                if item.get("target_subtitle_ids") == clip["target_subtitle_ids"]
            ])
        ),
    )
    service = DubbingProductionApplicationService()
    if invalid:
        with pytest.raises(AppException):
            service.refresh_current_candidate_projection("project-1", command)
        assert state["draft"] == before
    else:
        report = service.refresh_current_candidate_projection("project-1", command)
        assert state["draft"].timeline_clips == [clip]
        assert report.staged_candidate_projection.clips[0].start_ms == 1000
        assert report.semantic_boundary_audit is not None
        assert state["draft"].dubbing_production.candidate_inputs[0].placement_start_ms == 1050
        assert report.semantic_boundary_audit.status == "pending_agent"


def _stage_candidate_content_projection(state, audio_path: Path):
    draft = state["draft"]
    frozen = draft.dubbing_production.candidate_inputs[0]
    plan = draft.dubbing_production.active_plan
    assert plan is not None
    group = plan.groups[0]
    generated = [
        {
            **dict(candidate),
            "audio_path": str(audio_path),
        }
        for candidate in draft.generated_candidates
    ]
    staged_draft = draft.model_copy(update={"generated_candidates": generated})
    clips = [{
        "clip_id": "staged-content-1",
        "candidate_id": frozen.candidate_id,
        "track_id": "dub",
        "status": "ready",
        "start_ms": 1_000,
        "end_ms": 2_460,
        "source_start_ms": 220,
        "source_end_ms": 1_680,
        "target_subtitle_ids": list(group.subtitle_ids),
        "subtitle_id": group.subtitle_ids[0],
        "dubbing_group_id": group.group_id,
        "result_id": "result-1",
        "task_id": "task-1",
    }]
    stage = service_module._staged_candidate_projection(
        candidate_id=frozen.candidate_id,
        target_projection_fingerprint=service_module._target_owned_projection_fingerprint(
            staged_draft,
            target_subtitle_ids=list(group.subtitle_ids),
        ),
        clips=clips,
    )
    audit = service_module.dubbing_gap_adjudication.build_semantic_boundary_audit(
        source_revision=frozen.source_revision,
        plan_revision=frozen.plan_revision,
        candidate_id=frozen.candidate_id,
        audio_sha256=frozen.audio_sha256,
        candidate_evidence_fingerprint=domain.candidate_evidence_fingerprint(frozen),
        candidate_clip_projection_fingerprint=stage.candidate_clip_projection_fingerprint,
        expected_spoken_text=frozen.expected_spoken_text,
        aligned_words=list(frozen.audio.aligned_words),
        gaps=list(frozen.audio.gap_evidence),
        clips=clips,
    )
    report = domain.build_candidate_gap_processing_report(frozen).model_copy(update={
        "semantic_boundary_audit": audit,
        "staged_candidate_projection": stage,
    })
    state["draft"] = staged_draft.model_copy(update={
        "dubbing_production": staged_draft.dubbing_production.model_copy(update={
            "candidate_reports": [report],
        }),
    })
    return stage, audit


def test_staged_split_preserves_words_clears_projection_content_evidence(
    tmp_path, monkeypatch,
):
    state, _payload, _primary, audio_sha256 = _fixture(tmp_path, monkeypatch)
    draft = state["draft"]
    frozen = draft.dubbing_production.candidate_inputs[0]
    words = [
        DubbingCandidateAlignedWord(word_id="word-1", text="也", start_ms=200, end_ms=1_000),
        DubbingCandidateAlignedWord(word_id="word-2", text="是", start_ms=1_100, end_ms=1_600),
        DubbingCandidateAlignedWord(word_id="word-3", text="我", start_ms=2_320, end_ms=3_000),
        DubbingCandidateAlignedWord(word_id="word-4", text="知", start_ms=3_600, end_ms=3_900),
    ]
    content = TtsContentEvidence(
        audio_sha256=audio_sha256, engine_id="asr", status="complete", transcript="也是我知",
    )
    frozen = frozen.model_copy(update={
        "audio": frozen.audio.model_copy(update={
            "aligned_words": words,
            "gap_evidence": [DubbingAudioGapEvidence(
                gap_id="gap-is-wo", kind="internal", start_ms=1_480,
                end_ms=2_380, duration_ms=900,
                evidence_sources=["vad", "word_alignment"],
                evidence_ids=["vad:gap-is-wo"], boundary_confidence="clear",
                edit_decision="retain", retained_duration_ms=900,
                decision_reason="原始候选的内部静音。",
                safe_edit_boundary=True,
            )],
        }),
        "retained_content_evidence": DubbingRetainedContentEvidence(
            audio_sha256=audio_sha256,
            candidate_clip_projection_fingerprint="a" * 64,
            observation=content,
        ),
    })
    plan = draft.dubbing_production.active_plan
    group = plan.groups[0]
    generated = [{**dict(item), "audio_path": str(tmp_path / "complete.wav")} for item in draft.generated_candidates]
    staged_draft = draft.model_copy(update={"generated_candidates": generated})
    clips = [{
        "clip_id": "staged-split-1", "candidate_id": frozen.candidate_id,
        "track_id": "dub", "status": "ready", "start_ms": 1_000, "end_ms": 5_000,
        "source_start_ms": 0, "source_end_ms": 3_500,
        "target_subtitle_ids": list(group.subtitle_ids), "subtitle_id": group.subtitle_ids[0],
        "dubbing_group_id": group.group_id, "result_id": "result-1", "task_id": "task-1",
    }]
    clips.append({
        "clip_id": "staged-unaffected-2", "candidate_id": frozen.candidate_id,
        "track_id": "dub", "status": "ready", "start_ms": 4_770, "end_ms": 5_270,
        "source_start_ms": 3_500, "source_end_ms": 4_000,
        "target_subtitle_ids": list(group.subtitle_ids), "subtitle_id": group.subtitle_ids[0],
        "dubbing_group_id": group.group_id, "result_id": "result-1", "task_id": "task-1",
    })
    stage = service_module._staged_candidate_projection(
        candidate_id=frozen.candidate_id,
        target_projection_fingerprint=service_module._target_owned_projection_fingerprint(
            staged_draft, target_subtitle_ids=list(group.subtitle_ids),
        ),
        clips=clips,
    )
    report = domain.build_candidate_gap_processing_report(frozen).model_copy(update={
        "staged_candidate_projection": stage,
        "semantic_boundary_audit": service_module.dubbing_gap_adjudication.build_semantic_boundary_audit(
            source_revision=frozen.source_revision, plan_revision=frozen.plan_revision,
            candidate_id=frozen.candidate_id, audio_sha256=audio_sha256,
            candidate_evidence_fingerprint=domain.candidate_evidence_fingerprint(frozen),
            candidate_clip_projection_fingerprint=stage.candidate_clip_projection_fingerprint,
            expected_spoken_text=frozen.expected_spoken_text, aligned_words=words,
            gaps=[], clips=clips,
        ),
    })
    state["draft"] = staged_draft.model_copy(update={"dubbing_production": staged_draft.dubbing_production.model_copy(update={
        "candidate_inputs": [frozen], "candidate_reports": [report],
    })})
    monkeypatch.setattr(
        dubbing_media, "current_timeline_audio_sha256s",
        lambda _project, current: {
            str(clip.get("clip_id")): audio_sha256
            for clip in current.timeline_clips
            if clip.get("candidate_id") == frozen.candidate_id
        },
    )
    request = DubbingStagedCandidateSplitRequest(
        expected_repository_revision=7, source_revision=frozen.source_revision,
        plan_revision=1, candidate_id=frozen.candidate_id,
        candidate_clip_projection_fingerprint=stage.candidate_clip_projection_fingerprint,
        commands=[DubbingTimelineClipSplitCommand(
            clip_id="staged-split-1", candidate_id=frozen.candidate_id,
            audio_sha256=audio_sha256,
            slices=[
                DubbingTimelineAlignedSlice(
                    target_subtitle_ids=list(group.subtitle_ids), source_start_ms=0,
                    source_end_ms=1_680, speech_start_ms=200, speech_end_ms=1_600,
                    alignment_word_ids=["word-1", "word-2"],
                ),
                DubbingTimelineAlignedSlice(
                    target_subtitle_ids=list(group.subtitle_ids), source_start_ms=2_240,
                    source_end_ms=3_500, speech_start_ms=2_320, speech_end_ms=3_000,
                    alignment_word_ids=["word-3"],
                ),
            ],
        )],
    )

    saved = DubbingProductionApplicationService().split_staged_candidate_projection(
        "project-1", request,
    )

    assert [
        (clip.source_start_ms, clip.source_end_ms)
        for clip in saved.staged_candidate_projection.clips
    ] == [(0, 1_680), (2_240, 3_500), (3_500, 4_000)]
    assert [
        (clip.start_ms, clip.end_ms)
        for clip in saved.staged_candidate_projection.clips
    ] == [(1_000, 2_680), (2_680, 3_940), (4_210, 4_710)]
    assert (
        saved.staged_candidate_projection.clips[2].start_ms
        - saved.staged_candidate_projection.clips[1].end_ms
    ) == 270
    assert state["draft"].dubbing_production.candidate_inputs[0].retained_content_evidence is None
    cut_gap = state["draft"].dubbing_production.candidate_inputs[0].audio.gap_evidence[0]
    assert (cut_gap.edit_decision, cut_gap.retained_duration_ms) == ("shorten", 340)
    assert saved.semantic_boundary_audit.audio_gap_evidence[0].retained_duration_ms == 340
    assert saved.semantic_boundary_audit.status == "pending_agent"


def test_saved_candidate_reuses_alignment_until_audio_changes(tmp_path, monkeypatch):
    state, _payload, _primary, _hash = _fixture(tmp_path, monkeypatch)
    service_module.history_store.get("result-1").verification = None
    service_module.task_queue.get_task("task-1").verification = None
    draft = state["draft"]
    frozen = draft.dubbing_production.candidate_inputs[0]
    report = domain.build_candidate_gap_processing_report(frozen)
    state["draft"] = draft.model_copy(update={"dubbing_production":
        draft.dubbing_production.model_copy(update={"candidate_reports": [report]})})
    service = DubbingProductionApplicationService()
    calls = []
    monkeypatch.setattr(service, "sync_generated_candidate_automatic_cqc",
                        lambda *_args, **_kwargs: calls.append(1) or None)
    assert service.resync_history_candidate_automatic_cqc("project-1", "result-1") == report
    assert not calls
    sf.write(tmp_path / "complete.wav", np.ones(64_000, dtype=np.float32) * .1, 16_000)
    assert service.resync_history_candidate_automatic_cqc("project-1", "result-1") is None
    assert calls == [1]


def test_candidate_content_evidence_runs_outside_cas_and_persists_exact_audio(
    tmp_path, monkeypatch
):
    state, payload, _primary, audio_sha256 = _fixture(tmp_path, monkeypatch)
    frozen = state["draft"].dubbing_production.candidate_inputs[0]
    state["draft"] = state["draft"].model_copy(
        update={
            "dubbing_production": state["draft"].dubbing_production.model_copy(
                update={
                    "candidate_inputs": [
                        frozen.model_copy(
                            update={"candidate_transcript": "", "content_evidence": None}
                        )
                    ]
                }
            )
        }
    )
    state["draft"]._repository_revision = 7
    in_atomic = {"value": False}
    original_atomic = service_module.project_service.update_video_localization_atomic

    def observed_atomic(*args, **kwargs):
        in_atomic["value"] = True
        try:
            return original_atomic(*args, **kwargs)
        finally:
            in_atomic["value"] = False

    monkeypatch.setattr(
        service_module.project_service,
        "update_video_localization_atomic",
        observed_atomic,
    )
    calls = []

    def acquire(_path, cached):
        assert in_atomic["value"] is False
        assert cached is None
        calls.append(1)
        return TtsContentEvidence(
            audio_sha256=audio_sha256,
            engine_id="qwen3-asr-mlx",
            status="complete",
            transcript="压缩后的完整文本",
        )

    monkeypatch.setattr(
        service_module.tts_content_verification,
        "acquire_content_evidence",
        acquire,
    )
    request = DubbingCandidateContentEvidenceRequest(
        expected_repository_revision=7,
        source_revision=payload.source_revision,
        plan_revision=payload.plan_revision,
        group_id=payload.group_id,
        candidate_id=payload.candidate_id,
        result_id=payload.result_id,
    )

    response = DubbingProductionApplicationService().acquire_candidate_content_evidence(
        "project-1", request
    )

    assert calls == [1]
    assert response.repository_revision == 8
    assert response.evidence.matches_audio(audio_sha256)
    assert response.evidence_id in payload.content_verification_evidence_ids
    saved = state["draft"].dubbing_production.candidate_inputs[0]
    assert saved.content_evidence == response.evidence
    assert saved.candidate_transcript == "压缩后的完整文本"


def test_content_evidence_accepts_recovered_manual_result_with_exact_formal_binding(
    tmp_path, monkeypatch,
):
    state, payload, _primary, audio_sha256 = _fixture(tmp_path, monkeypatch)
    task = service_module.task_queue.get_task("task-1")
    task.parameters.update({
        "video_localization_dubbing_group_id": None,
        "video_localization_dubbing_plan_revision": None,
    })
    formal = {
        "clip_id": "formal-recovered-manual",
        "candidate_id": payload.candidate_id,
        "result_id": payload.result_id,
        "track_id": "dub",
        "dub_lane": 0,
        "status": "ready",
        "start_ms": 1_000,
        "end_ms": 2_460,
        "source_start_ms": 220,
        "source_end_ms": 1_680,
        "subtitle_id": "subtitle-1",
        "target_subtitle_ids": ["subtitle-1", "subtitle-2"],
        "source_cue_ids": ["cue-1", "cue-2"],
    }
    state["draft"] = state["draft"].model_copy(
        update={"timeline_clips": [formal]}
    )
    state["draft"]._repository_revision = 7
    observed = []

    def acquire(path, cached):
        observed.append((path, cached))
        return TtsContentEvidence(
            audio_sha256=service_module.media_assets.file_sha256(path),
            engine_id="qwen3-asr-mlx",
            status="complete",
            transcript="压缩后的完整文本",
        )

    monkeypatch.setattr(
        service_module.tts_content_verification, "acquire_content_evidence", acquire,
    )

    response = DubbingProductionApplicationService().acquire_candidate_content_evidence(
        "project-1",
        DubbingCandidateContentEvidenceRequest(
            expected_repository_revision=7,
            source_revision=payload.source_revision,
            plan_revision=payload.plan_revision,
            group_id=payload.group_id,
            candidate_id=payload.candidate_id,
            result_id=payload.result_id,
            use_current_timeline_projection=True,
        ),
    )

    assert response.repository_revision == 8
    assert len(observed) == 1
    saved = state["draft"].dubbing_production.candidate_inputs[0]
    assert saved.retained_content_evidence is not None
    assert saved.retained_content_evidence.audio_sha256 == audio_sha256


@pytest.mark.parametrize("mismatch", ["candidate", "source", "text"])
def test_content_evidence_rejects_manual_result_without_exact_formal_binding(
    tmp_path, monkeypatch, mismatch,
):
    state, payload, _primary, _audio_sha256 = _fixture(tmp_path, monkeypatch)
    task = service_module.task_queue.get_task("task-1")
    task.parameters.update({
        "video_localization_dubbing_group_id": None,
        "video_localization_dubbing_plan_revision": None,
    })
    formal = {
        "clip_id": "wrong-manual-candidate",
        "candidate_id": payload.candidate_id,
        "result_id": payload.result_id,
        "track_id": "dub",
        "dub_lane": 0,
        "status": "ready",
        "target_subtitle_ids": ["subtitle-1", "subtitle-2"],
        "source_cue_ids": ["cue-1", "cue-2"],
    }
    if mismatch == "candidate":
        formal["candidate_id"] = "candidate-other"
    elif mismatch == "source":
        formal["source_cue_ids"] = ["cue-1"]
    else:
        task.parameters["text"] = "已经改变的朗读文本"
    state["draft"] = state["draft"].model_copy(
        update={"timeline_clips": [formal]}
    )
    state["draft"]._repository_revision = 7
    monkeypatch.setattr(
        service_module.tts_content_verification,
        "acquire_content_evidence",
        lambda *_args: pytest.fail("wrong formal binding must not reach ASR"),
    )

    with pytest.raises(AppException) as error:
        DubbingProductionApplicationService().acquire_candidate_content_evidence(
            "project-1",
            DubbingCandidateContentEvidenceRequest(
                expected_repository_revision=7,
                source_revision=payload.source_revision,
                plan_revision=payload.plan_revision,
                group_id=payload.group_id,
                candidate_id=payload.candidate_id,
                result_id=payload.result_id,
                use_current_timeline_projection=True,
            ),
        )

    assert error.value.code == "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_BINDING_CHANGED"


def test_retained_content_binds_exact_crop_and_never_replaces_whole_file_evidence(
    tmp_path, monkeypatch,
):
    state, payload, _primary, original_hash = _fixture(tmp_path, monkeypatch)
    draft = state["draft"]
    clip = {
        "clip_id": "edited", "candidate_id": payload.candidate_id,
        "track_id": "dub", "status": "ready", "dub_lane": 0,
        "start_ms": 1_000, "end_ms": 2_460,
        "source_start_ms": 220, "source_end_ms": 1_680,
        "target_subtitle_ids": ["subtitle-1", "subtitle-2"],
        "subtitle_id": "subtitle-1", "result_id": "result-1",
        "task_id": "task-1", "generation_id": "generation-1",
        "cue_id": "cue-1", "source_cue_ids": ["cue-1", "cue-2"],
        "target_start_ms": 1_000, "target_end_ms": 3_000,
        "tts_target_text": "压缩后的完整文本",
    }
    state["draft"] = draft.model_copy(update={"timeline_clips": [clip]})
    state["draft"]._repository_revision = 7
    paths = []
    def acquire(path, cached):
        paths.append(path)
        assert sf.info(path).duration == pytest.approx(1.46)
        return TtsContentEvidence(
            audio_sha256=service_module.media_assets.file_sha256(path),
            engine_id="qwen3-asr-mlx", status="complete", transcript="压缩后的完整文本",
        )
    monkeypatch.setattr(service_module.tts_content_verification, "acquire_content_evidence", acquire)
    request = DubbingCandidateContentEvidenceRequest(
        expected_repository_revision=7, source_revision=payload.source_revision,
        plan_revision=payload.plan_revision, group_id=payload.group_id,
        candidate_id=payload.candidate_id, result_id=payload.result_id,
        use_current_timeline_projection=True,
    )
    DubbingProductionApplicationService().acquire_candidate_content_evidence("project-1", request)
    saved = state["draft"].dubbing_production.candidate_inputs[0]
    assert saved.content_evidence == draft.dubbing_production.candidate_inputs[0].content_evidence
    assert saved.retained_content_evidence.audio_sha256 == original_hash
    assert all(not path.exists() for path in paths)
    clips = service_module._current_group_working_candidate_clips(
        state["draft"], group_id=payload.group_id, candidate_id=payload.candidate_id,
        target_subtitle_ids=["subtitle-1", "subtitle-2"],
    )
    assert service_module._verified_retained_projection(saved, clips)
    assert service_module._verified_retained_projection(saved, [
        {**clip, "dubbing_alignment_word_ids": ["refreshed-word"],
         "dubbing_slice_index": index + 1, "dubbing_slice_count": len(clips)}
        for index, clip in enumerate(clips)
    ])
    assert not service_module._verified_retained_projection(saved, [{**clips[0], "source_start_ms": 0}])
    assert not service_module._verified_retained_projection(saved.model_copy(update={"audio_sha256": "f" * 64}), clips)
    bad = saved.retained_content_evidence.model_copy(update={
        "observation": saved.retained_content_evidence.observation.model_copy(update={"transcript": "错误台词"}),
    })
    assert not service_module._verified_retained_projection(saved.model_copy(update={"retained_content_evidence": bad}), clips)


@pytest.mark.parametrize("adopted", [False, True])
def test_content_evidence_preserves_unchanged_projection_before_and_after_adoption(
    tmp_path, monkeypatch, adopted,
):
    state, payload, primary, _audio_sha256 = _fixture(tmp_path, monkeypatch)
    stage, original_audit = _stage_candidate_content_projection(state, tmp_path / "complete.wav")
    if adopted:
        state["draft"] = state["draft"].model_copy(update={"timeline_clips": [
            {**stage.clips[0].model_dump(exclude_none=True),
             "audio_path": str(tmp_path / "complete.wav")}
        ]})
        state["draft"]._repository_revision = 7
    original_clips = list(state["draft"].timeline_clips)
    paths = []

    def acquire(path, cached):
        paths.append(path)
        assert cached is None
        assert sf.info(path).duration == pytest.approx(1.46)
        return TtsContentEvidence(
            audio_sha256=service_module.media_assets.file_sha256(path),
            engine_id="qwen3-asr-mlx",
            status="complete",
            transcript="压缩后的完整文本",
        )

    monkeypatch.setattr(
        service_module.tts_content_verification,
        "acquire_content_evidence",
        acquire,
    )
    response = DubbingProductionApplicationService().acquire_candidate_content_evidence(
        "project-1",
        DubbingCandidateContentEvidenceRequest(
            expected_repository_revision=7,
            source_revision=payload.source_revision,
            plan_revision=payload.plan_revision,
            group_id=payload.group_id,
            candidate_id=payload.candidate_id,
            result_id=payload.result_id,
            use_current_timeline_projection=True,
        ),
    )

    saved = state["draft"].dubbing_production.candidate_inputs[0]
    group = state["draft"].dubbing_production.active_plan.groups[0]
    assert response.repository_revision == 8
    assert saved.retained_content_evidence is not None
    assert state["draft"].timeline_clips == original_clips
    report = state["draft"].dubbing_production.candidate_reports[0]
    assert report.semantic_boundary_audit is not None
    assert report.semantic_boundary_audit.boundaries == original_audit.boundaries
    assert report.staged_candidate_projection == stage
    assert all(not path.exists() for path in paths)
    assert service_module._verified_retained_projection(
        saved,
        service_module._current_group_content_evidence_clips(
            state["draft"], group=group, frozen=saved, candidate_id=payload.candidate_id,
        ),
    )


def test_content_evidence_rechecks_staged_projection_before_persisting(
    tmp_path, monkeypatch,
):
    state, payload, _primary, _audio_sha256 = _fixture(tmp_path, monkeypatch)
    _stage_candidate_content_projection(state, tmp_path / "complete.wav")

    def acquire(path, _cached):
        draft = state["draft"]
        report = draft.dubbing_production.candidate_reports[0]
        stage = report.staged_candidate_projection
        audit = report.semantic_boundary_audit
        assert stage is not None and audit is not None
        changed = service_module._staged_candidate_projection(
            candidate_id=stage.candidate_id,
            target_projection_fingerprint=stage.target_projection_fingerprint,
            clips=[{
                **stage.clips[0].model_dump(exclude_none=True),
                "source_end_ms": 1_600,
                "end_ms": 2_380,
            }],
        )
        changed_report = report.model_copy(update={
            "staged_candidate_projection": changed,
            "semantic_boundary_audit": audit.model_copy(update={
                "candidate_clip_projection_fingerprint": (
                    changed.candidate_clip_projection_fingerprint
                ),
            }),
        })
        state["draft"] = draft.model_copy(update={
            "dubbing_production": draft.dubbing_production.model_copy(update={
                "candidate_reports": [changed_report],
            }),
        })
        state["draft"]._repository_revision = 7
        return TtsContentEvidence(
            audio_sha256=service_module.media_assets.file_sha256(path),
            engine_id="qwen3-asr-mlx",
            status="complete",
            transcript="压缩后的完整文本",
        )

    monkeypatch.setattr(
        service_module.tts_content_verification,
        "acquire_content_evidence",
        acquire,
    )
    with pytest.raises(AppException) as error:
        DubbingProductionApplicationService().acquire_candidate_content_evidence(
            "project-1",
            DubbingCandidateContentEvidenceRequest(
                expected_repository_revision=7,
                source_revision=payload.source_revision,
                plan_revision=payload.plan_revision,
                group_id=payload.group_id,
                candidate_id=payload.candidate_id,
                result_id=payload.result_id,
                use_current_timeline_projection=True,
            ),
        )
    assert error.value.code == "DUBBING_RETAINED_PROJECTION_CHANGED"


def test_content_evidence_accepts_strictly_retained_candidate_after_unrelated_replan(
    tmp_path, monkeypatch
):
    state, payload, _primary, _audio_sha256 = _fixture(tmp_path, monkeypatch)
    draft = state["draft"]
    old_plan = draft.dubbing_production.active_plan
    old_frozen = draft.dubbing_production.candidate_inputs[0]
    assert old_plan is not None
    rebound_plan = old_plan.model_copy(update={"plan_revision": 2})
    rebound_frozen = old_frozen.model_copy(
        update={
            "plan_revision": 2,
            "evidence_origin_source_revision": old_frozen.source_revision,
            "evidence_origin_plan_revision": old_frozen.plan_revision,
        }
    )
    state["draft"] = draft.model_copy(
        update={
            "dubbing_production": draft.dubbing_production.model_copy(
                update={
                    "active_plan": rebound_plan,
                    "plan_revision_counter": 2,
                    "candidate_inputs": [rebound_frozen],
                }
            )
        }
    )
    state["draft"]._repository_revision = 7

    response = DubbingProductionApplicationService().acquire_candidate_content_evidence(
        "project-1",
        DubbingCandidateContentEvidenceRequest(
            expected_repository_revision=7,
            source_revision=payload.source_revision,
            plan_revision=2,
            group_id=payload.group_id,
            candidate_id=payload.candidate_id,
            result_id=payload.result_id,
        ),
    )

    assert response.plan_revision == 2
    assert response.repository_revision == 8
    saved = state["draft"].dubbing_production.candidate_inputs[0]
    assert saved.evidence_origin_plan_revision == 1
    assert saved.plan_revision == 2


def test_content_evidence_accepts_exact_historical_task_after_legacy_equivalent_rebind(
    tmp_path, monkeypatch
):
    state, payload, _primary, _audio_sha256 = _fixture(tmp_path, monkeypatch)
    draft = state["draft"]
    old_plan = draft.dubbing_production.active_plan
    old_frozen = draft.dubbing_production.candidate_inputs[0]
    assert old_plan is not None
    rebound_plan = old_plan.model_copy(update={"plan_revision": 2})
    # Older equivalent-plan CQC rebinds wrote current evidence but did not
    # retain their origin metadata. The task itself must still match every
    # current group binding before it can supply content evidence.
    rebound_frozen = old_frozen.model_copy(update={"plan_revision": 2})
    state["draft"] = draft.model_copy(
        update={
            "dubbing_production": draft.dubbing_production.model_copy(
                update={
                    "active_plan": rebound_plan,
                    "plan_revision_counter": 2,
                    "candidate_inputs": [rebound_frozen],
                }
            )
        }
    )
    state["draft"]._repository_revision = 7

    response = DubbingProductionApplicationService().acquire_candidate_content_evidence(
        "project-1",
        DubbingCandidateContentEvidenceRequest(
            expected_repository_revision=7,
            source_revision=payload.source_revision,
            plan_revision=2,
            group_id=payload.group_id,
            candidate_id=payload.candidate_id,
            result_id=payload.result_id,
        ),
    )

    assert response.plan_revision == 2
    assert response.repository_revision == 8


@pytest.mark.parametrize("changed_dependency", ["text", "source"])
def test_content_evidence_rejects_retained_candidate_when_exact_dependency_changed(
    tmp_path, monkeypatch, changed_dependency
):
    state, payload, _primary, _audio_sha256 = _fixture(tmp_path, monkeypatch)
    draft = state["draft"]
    old_plan = draft.dubbing_production.active_plan
    old_frozen = draft.dubbing_production.candidate_inputs[0]
    assert old_plan is not None
    group = old_plan.groups[0]
    unit = old_plan.semantic_units[0]
    localized_subtitles = list(draft.localized_subtitles)
    if changed_dependency == "text":
        localized_subtitles[0] = localized_subtitles[0].model_copy(
            update={"text": "已经改变的朗读文本"}
        )
        group = group.model_copy(update={"spoken_text": "已经改变的朗读文本"})
        unit = unit.model_copy(
            update={
                "display_text": "已经改变的朗读文本",
                "spoken_text": "已经改变的朗读文本",
            }
        )
    else:
        localized_subtitles[0] = localized_subtitles[0].model_copy(
            update={"source_cue_ids": ["cue-3"]}
        )
        unit = unit.model_copy(update={"source_cue_ids": ["cue-3", "cue-2"]})
    changed_draft = draft.model_copy(update={"localized_subtitles": localized_subtitles})
    changed_source_revision = domain.dubbing_source_revision(changed_draft)
    rebound_plan = old_plan.model_copy(
        update={
            "source_revision": changed_source_revision,
            "plan_revision": 2,
            "semantic_units": [unit],
            "groups": [group],
        }
    )
    rebound_frozen = old_frozen.model_copy(
        update={
            "source_revision": changed_source_revision,
            "plan_revision": 2,
            "evidence_origin_source_revision": old_frozen.source_revision,
            "evidence_origin_plan_revision": old_frozen.plan_revision,
            "source_context_fingerprint": (
                domain.group_evidence_context_fingerprint(
                    changed_draft, rebound_plan, group
                )
                if changed_dependency == "text"
                else old_frozen.source_context_fingerprint
            ),
        }
    )
    state["draft"] = changed_draft.model_copy(
        update={
            "dubbing_production": draft.dubbing_production.model_copy(
                update={
                    "active_plan": rebound_plan,
                    "plan_revision_counter": 2,
                    "candidate_inputs": [rebound_frozen],
                }
            )
        }
    )
    state["draft"]._repository_revision = 7

    with pytest.raises(AppException) as error:
        DubbingProductionApplicationService().acquire_candidate_content_evidence(
            "project-1",
            DubbingCandidateContentEvidenceRequest(
                expected_repository_revision=7,
                source_revision=changed_source_revision,
                plan_revision=2,
                group_id=payload.group_id,
                candidate_id=payload.candidate_id,
                result_id=payload.result_id,
            ),
        )

    assert error.value.code == "VIDEO_LOCALIZATION_DUBBING_CANDIDATE_BINDING_CHANGED"


@pytest.mark.parametrize("conflict", ["protected_prefix", "padding_only", "other_lane", "same_target"])
def test_manual_timing_deferral_checks_real_placement_not_only_total_duration(tmp_path, monkeypatch, conflict):
    state, payload, primary, _hash = _fixture(tmp_path, monkeypatch)
    draft = state["draft"]
    plan = draft.dubbing_production.active_plan
    group = plan.groups[0].model_copy(update={"target_end_ms": 6_000})
    plan = plan.model_copy(update={"groups": [group]})
    frozen = draft.dubbing_production.candidate_inputs[0]
    audio = frozen.audio.model_copy(update={"aligned_words": [
        frozen.audio.aligned_words[0].model_copy(update={"start_ms": 200})
    ]})
    frozen = frozen.model_copy(update={
        "target_end_ms": 6_000, "audio": audio,
        "source_context_fingerprint": domain.group_evidence_context_fingerprint(draft, plan, group),
    })
    neighbour = {**primary, "clip_id": "previous", "target_subtitle_ids": ["previous-subtitle"],
                 "subtitle_id": "previous-subtitle", "start_ms": 500, "end_ms": 900}
    if conflict == "padding_only":
        neighbour["end_ms"] = 820
    if conflict == "other_lane":
        neighbour["dub_lane"] = 1
    if conflict == "same_target":
        neighbour["target_subtitle_ids"] = ["subtitle-1"]
    state["draft"] = draft.model_copy(update={
        "timeline_clips": [neighbour],
        "dubbing_production": draft.dubbing_production.model_copy(update={
            "active_plan": plan, "candidate_inputs": [frozen],
        }),
    })
    state["draft"]._repository_revision = 7
    # File is only 4s, while the nominal available range is 5.2s. The first
    # aligned word starts at 1s, but real protected activity begins at 850ms.
    payload = DubbingManualTimingDeferralRequest.model_validate({
        **payload.model_dump(), "available_duration_ms": 5_200,
    })
    application = DubbingProductionApplicationService()
    if conflict != "protected_prefix":
        with pytest.raises(AppException) as error:
            application.defer_group_for_manual_timing("project-1", payload)
        assert error.value.code == "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_WINDOW_CHANGED"
        assert state["writes"] == 0
        return
    response = application.defer_group_for_manual_timing("project-1", payload)
    assert response.disposition.placement_failure_reason == "protected_audio_conflict"
    assert response.disposition.conflicting_clip_ids == ["previous"]
    assert state["draft"].timeline_clips[0] == neighbour
    assert state["draft"].timeline_clips[1]["source_end_ms"] == 4_000
    assert response.production_run.groups[0].stage == "deferred_manual_timing"
    application.defer_group_for_manual_timing("project-1", payload)
    assert state["writes"] == 1


def test_manual_timing_deferral_is_atomic_idempotent_and_preserves_primary(tmp_path, monkeypatch):
    state, payload, primary, _audio_sha256 = _fixture(tmp_path, monkeypatch)
    application = DubbingProductionApplicationService()

    response = application.defer_group_for_manual_timing("project-1", payload)

    assert response.repository_revision == 8
    assert response.production_run.groups[0].stage == "deferred_manual_timing"
    assert response.production_run.deferred_group_count == 1
    assert state["draft"].timeline_clips[0] == primary
    parked = state["draft"].timeline_clips[1]
    assert parked["dub_lane"] == 1
    assert parked["target_subtitle_ids"] == ["subtitle-1", "subtitle-2"]
    assert parked["source_cue_ids"] == ["cue-1", "cue-2"]
    assert response.disposition.target_subtitle_ids == ["subtitle-1", "subtitle-2"]
    assert response.disposition.source_cue_ids == ["cue-1", "cue-2"]
    assert parked["source_start_ms"] == 0
    assert parked["source_end_ms"] == 4_000
    assert state["writes"] == 1

    replay = application.defer_group_for_manual_timing("project-1", payload)
    assert replay.repository_revision == 8
    assert state["writes"] == 1
    assert len(state["draft"].timeline_clips) == 2


@pytest.mark.parametrize("changed", [None, "position", "crop", "candidate", "lane", "audio"])
def test_manual_timing_deferral_revalidates_existing_take_without_replacing_it(tmp_path, monkeypatch, changed):
    state, payload, _primary, _hash = _fixture(tmp_path, monkeypatch)
    application = DubbingProductionApplicationService()
    application.defer_group_for_manual_timing("project-1", payload)
    draft = state["draft"]
    draft.dubbing_production = draft.dubbing_production.model_copy(update={"manual_timing_deferrals": []})
    clip = draft.timeline_clips[1]
    if changed == "position":
        clip["start_ms"] += 10
        clip["end_ms"] += 10
    elif changed == "crop":
        clip["source_start_ms"] = 10
    elif changed == "candidate":
        clip["candidate_id"] = "another-candidate"
    elif changed == "lane":
        clip["dub_lane"] = 0
    elif changed == "audio":
        monkeypatch.setattr(dubbing_media, "current_timeline_audio_sha256s", lambda *_: {"parked-1": "changed"})
    clips_before = [dict(item) for item in draft.timeline_clips]
    monkeypatch.setattr(service_module.media_assets, "adopt_tts_audio", lambda *_: pytest.fail("must not copy existing media"))
    fresh = payload.model_copy(update={"request_id": "revalidate-2", "expected_repository_revision": 8})
    if changed:
        with pytest.raises(AppException) as error:
            application.defer_group_for_manual_timing("project-1", fresh)
        assert error.value.code == "VIDEO_LOCALIZATION_DUBBING_DEFERRAL_CLIP_CONFLICT"
        assert state["writes"] == 1
    else:
        response = application.defer_group_for_manual_timing("project-1", fresh)
        assert response.production_run.groups[0].stage == "deferred_manual_timing"
        assert state["writes"] == 2
        application.defer_group_for_manual_timing("project-1", fresh)
        assert state["writes"] == 2
    assert state["draft"].timeline_clips == clips_before


def test_manual_timing_deferral_atomically_closes_selected_pending_placement(
    tmp_path, monkeypatch
):
    state, payload, _primary, _audio_sha256 = _fixture(tmp_path, monkeypatch)
    workflow = VideoLocalizationTtsTask(
        workflow_id="workflow-1",
        project_id="project-1",
        segment_id="group-1",
        subtitle_summary="压缩后的完整文本",
        text="压缩后的完整文本",
        source_cue_ids=["cue-1", "cue-2"],
        start_ms=1_000,
        end_ms=3_000,
        status="running",
        generation_task_id="task-1",
        result_id="result-1",
        stages=[
            VideoLocalizationTtsTaskStage(
                kind="generation", status="success", progress=1.0
            ),
            VideoLocalizationTtsTaskStage(
                kind="placement", status="running", progress=0.5
            ),
        ],
    )
    state["draft"] = state["draft"].model_copy(update={"tts_tasks": [workflow]})
    state["draft"]._repository_revision = 7

    response = DubbingProductionApplicationService().defer_group_for_manual_timing(
        "project-1", payload
    )

    saved = state["draft"].tts_tasks[0]
    assert response.repository_revision == 8
    assert saved.status == "success"
    assert saved.result_id == "result-1"
    assert saved.generation_task_id == "task-1"
    assert saved.timeline_clip_id == "parked-1"
    assert saved.stages[0].status == "success"
    assert saved.stages[1].status == "success"
    assert saved.stages[1].parameters["dub_lane"] == 1


def test_manual_timing_deferral_rejects_stale_repository_revision(tmp_path, monkeypatch):
    state, payload, _primary, _audio_sha256 = _fixture(tmp_path, monkeypatch)
    state["draft"]._repository_revision = 8
    application = DubbingProductionApplicationService()

    with pytest.raises(AppException) as error:
        application.defer_group_for_manual_timing("project-1", payload)

    assert error.value.status_code == 409
    assert error.value.code == "VIDEO_LOCALIZATION_DUBBING_REPOSITORY_CHANGED"
    assert state["writes"] == 0


def test_deleted_parked_clip_is_not_reported_as_terminal(tmp_path, monkeypatch):
    state, payload, _primary, _audio_sha256 = _fixture(tmp_path, monkeypatch)
    application = DubbingProductionApplicationService()
    application.defer_group_for_manual_timing("project-1", payload)
    state["draft"] = state["draft"].model_copy(
        update={"timeline_clips": state["draft"].timeline_clips[:1]}
    )
    state["draft"]._repository_revision = 9

    run = application.read_production_run("project-1")

    assert run.groups[0].stage != "deferred_manual_timing"
    assert run.deferred_group_count == 0


def test_completion_separates_deferred_work_from_primary_acceptance(tmp_path, monkeypatch):
    state, payload, _primary, audio_sha256 = _fixture(tmp_path, monkeypatch)
    application = DubbingProductionApplicationService()
    application.defer_group_for_manual_timing("project-1", payload)

    completion = application.read_completion("project-1")

    assert completion.status == "incomplete"
    assert completion.automated_production_status == "resolved"
    assert completion.deferred_manual_timing_group_ids == ["group-1"]
    assert completion.deferred_manual_timing_clip_ids == ["parked-1"]
    assert "parked-1" not in completion.unchecked_clip_ids
    assert state["draft"].dubbing_production.manual_timing_deferrals[0].audio_sha256 == audio_sha256


def test_retained_projection_keeps_first_razor_slice():
    from types import SimpleNamespace
    from app.domains.video_localization.dubbing_production_service import _current_group_working_candidate_clips
    base = dict(track_id='dub', candidate_id='candidate', target_subtitle_ids=['subtitle'], status='ready', dub_lane=0)
    clips = [dict(base, clip_id='first', start_ms=0, end_ms=1000, source_start_ms=0, source_end_ms=1000),
             dict(base, clip_id='second', media_source_clip_id='first', start_ms=1000, end_ms=2000, source_start_ms=1200, source_end_ms=2200)]
    result = _current_group_working_candidate_clips(SimpleNamespace(timeline_clips=clips), group_id='group', candidate_id='candidate', target_subtitle_ids=['subtitle'])
    assert [c['clip_id'] for c in result] == ['first', 'second']
