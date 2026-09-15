from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_development_continuation as continuation,
    asr_pipeline,
    asr_timing_contracts,
    development_checkpoints,
    document_understanding_contracts,
    operation_queue,
    review_decisions,
    service,
    transcript_quality_gate,
    transcription,
    whole_recheck,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptionState,
)
from app.schemas.voice_studio import AppSettings, ProjectCreate  # noqa: E402
from app.services import database, project_store, settings_store  # noqa: E402


def _segment() -> VideoLocalizationTranscriptSegment:
    return VideoLocalizationTranscriptSegment(
        segment_id="segment-1",
        start_ms=0,
        end_ms=1000,
        raw_text="old text",
        corrected_text="corrected text",
    )


def _workflow() -> asr_pipeline.AsrPipelineInput:
    return asr_pipeline.AsrPipelineInput(
        operation_id="formal-op",
        audio_path="/managed/source.wav",
        alignment_audio_path="/managed/vocals.wav",
        engine_id="test-asr",
        source_track_id="original",
        alignment_source_track_id="vocals",
        language="en",
        duration_ms=1000,
        source_video_duration_ms=1000,
        source_video_frame_rate=25,
        llm_profile_id="review-profile",
        source_audio_sha256="source-sha",
        alignment_audio_sha256="alignment-sha",
    )


def _state(last_step_id: str) -> continuation.AsrDevelopmentContinuationState:
    return continuation.AsrDevelopmentContinuationState(
        lineage_operation_id="formal-op",
        last_step_id=last_step_id,
        transcription=VideoLocalizationTranscriptionState(
            language="en",
            source_track_id="original",
            source_audio_sha256="source-sha",
            alignment_source_track_id="vocals",
            alignment_audio_sha256="alignment-sha",
            engine_id="test-asr",
            raw_text="old text",
            corrected_text="corrected text",
            segments=[_segment()],
        ),
    )


def test_timing_tail_inputs_do_not_read_old_lineage_tail_snapshots(
    monkeypatch,
) -> None:
    gate = transcript_quality_gate.AsrTranscriptQualityGateResult.model_construct(
        input=SimpleNamespace(segments=[_segment()]),
        decision="ready_for_alignment",
        can_start_alignment=True,
        blockers=[],
    )
    joined = asr_pipeline.AsrJoinedTranscript.model_construct(
        diarization=None
    )
    alignment = asr_timing_contracts.AsrAlignmentResult.model_construct(
        words=[], metadata={}
    )
    boundaries = asr_timing_contracts.AsrAudioBoundariesResult.model_construct(
        input=asr_timing_contracts.AsrAudioBoundariesInput(
            audio_path="/managed/source.wav",
            audio_sha256="source-sha",
            source_track_id="original",
            words=[],
        ),
        boundary_features=[],
        subtitle_entry_by_word_id={},
        metadata={"status": "completed"},
    )
    boundary_review = asr_timing_contracts.AsrBoundaryReviewResult.model_construct(
        reviews=[], metadata={}
    )
    results = {
        "transcript_quality_gate_result": gate,
        "alignment_result": alignment,
        "audio_boundaries_result": boundaries,
        "boundary_review_result": boundary_review,
    }
    forbidden = {
        "alignment_input",
        "audio_boundaries_input",
        "boundary_review_input",
        "subtitle_track_input",
    }

    def fake_load(_root, *, operation_id, step_id, **_kwargs):
        assert step_id not in forbidden
        if step_id == "workflow_input":
            return _workflow()
        if step_id == "initial_analysis_join_result":
            return joined
        if step_id == "continuation_state":
            predecessor_step = next(
                step
                for step in continuation.CONTINUATION_STEP_ORDER
                if operation_id == f"{step}-op"
            )
            return _state(predecessor_step)
        return results[step_id]

    monkeypatch.setattr(continuation, "_load", fake_load)
    targets = (
        ("transcript_quality_gate", "alignment"),
        ("alignment", "audio_boundaries"),
        ("audio_boundaries", "boundary_review"),
        ("boundary_review", "subtitle_track"),
    )
    expected_types = (
        asr_timing_contracts.AsrAlignmentInput,
        asr_timing_contracts.AsrAudioBoundariesInput,
        asr_timing_contracts.AsrBoundaryReviewInput,
        asr_timing_contracts.AsrSubtitleTrackInput,
    )
    for (predecessor_step, target_step), expected_type in zip(
        targets, expected_types
    ):
        request = continuation.build_target_input(
            Path("/unused"),
            project_id="project-1",
            lineage_operation_id="formal-op",
            predecessor_operation_id=f"{predecessor_step}-op",
            predecessor_step_id=predecessor_step,
            target_step_id=target_step,
        )
        assert isinstance(request, expected_type)


def test_missing_direct_alignment_state_is_rebuilt_read_only_for_next_node(
    monkeypatch,
) -> None:
    word = VideoLocalizationAlignedWord(
        word_id="word-1",
        segment_id="segment-1",
        text="corrected",
        start_ms=0,
        end_ms=400,
        timing_confidence="high",
        timing_source="forced_aligner",
    )
    alignment = asr_timing_contracts.AsrAlignmentResult(
        input=asr_timing_contracts.AsrAlignmentInput(
            alignment_audio_path="/managed/vocals.wav",
            alignment_audio_sha256="alignment-sha",
            alignment_source_track_id="vocals",
            segments=[_segment()],
            language="en",
            duration_ms=1000,
        ),
        words=[word],
        metadata={"status": "completed", "timing_confidence": "high"},
    )
    loaded_steps: list[tuple[str, str]] = []

    def fake_load(_root, *, operation_id, step_id, **_kwargs):
        loaded_steps.append((operation_id, step_id))
        if step_id == "workflow_input":
            return _workflow()
        if step_id == "continuation_state":
            raise continuation.AppException(
                409,
                "VIDEO_LOCALIZATION_ASR_CONTINUATION_SNAPSHOT_INVALID",
                "missing continuation state",
            )
        assert operation_id == "alignment-op"
        assert step_id == "alignment_result"
        return alignment

    monkeypatch.setattr(continuation, "_load", fake_load)
    monkeypatch.setattr(
        continuation,
        "_state_before_direct_target",
        lambda *_args, **_kwargs: _state(
            "transcript_quality_gate"
        ).transcription,
    )

    request = continuation.build_target_input(
        Path("/unused"),
        project_id="project-1",
        lineage_operation_id="formal-op",
        predecessor_operation_id="alignment-op",
        predecessor_step_id="alignment",
        target_step_id="audio_boundaries",
    )

    assert isinstance(request, asr_timing_contracts.AsrAudioBoundariesInput)
    assert request.words == [word]
    assert ("alignment-op", "continuation_state") in loaded_steps
    assert all(step_id != "audio_boundaries_result" for _op, step_id in loaded_steps)


def test_clean_continuation_state_preserves_partial_review_status(
    monkeypatch,
) -> None:
    raw_input = transcription.TranscribeRawInput(
        audio_path="/managed/source.wav",
        audio_sha256="source-sha",
        engine_id="test-asr",
        source_track_id="original",
        requested_language="en",
        duration_ms=1000,
    )
    raw = transcription.TranscribeRawOutput.model_construct(
        input=raw_input,
        language="en",
        raw_text="old text",
        incomplete_chunk_ranges=[],
        quality_summary=transcription.TranscribeRawQualitySummary.model_construct(
            warning_codes=[]
        ),
    )
    joined = asr_pipeline.AsrJoinedTranscript.model_construct(
        raw_asr=raw,
        diarization=None,
    )

    def fake_load(_root, *, step_id, **_kwargs):
        return {
            "workflow_input": _workflow(),
            "asr_result": raw,
            "initial_analysis_join_result": joined,
        }[step_id]

    monkeypatch.setattr(continuation, "_load", fake_load)
    state = continuation._base_state(
        Path("/unused"),
        project_id="project-1",
        lineage_operation_id="formal-op",
        segments=[_segment()],
        review_status="partial",
        review_profile_id="review-profile",
        review_model_id=None,
    )

    assert state.review_status == "partial"
    assert state.words == []
    assert state.alignment_status == "not_run"
    assert state.corrected_text == "corrected text"


def test_public_queue_direct_target_persists_continuation_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
            video_localization_development_step_control_enabled=True,
        )
    )
    project = project_store.create_project(
        ProjectCreate(name="Direct ASR target continuation")
    )
    service.save_video_localization(project.project_id, VideoLocalizationDraft())
    checkpoint_root = tmp_path / "asr-checkpoints"
    monkeypatch.setattr(
        operation_queue,
        "DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT",
        checkpoint_root,
    )
    monkeypatch.setattr(operation_queue, "_enqueue", lambda *_args: None)
    alignment = asr_timing_contracts.AsrAlignmentResult(
        input=asr_timing_contracts.AsrAlignmentInput(
            alignment_audio_path="/managed/vocals.wav",
            alignment_audio_sha256="alignment-sha",
            alignment_source_track_id="vocals",
            segments=[_segment()],
            language="en",
            duration_ms=1000,
        ),
        words=[],
        metadata={"status": "completed", "timing_confidence": "high"},
    )
    monkeypatch.setattr(
        operation_queue.asr_development_replay,
        "replay_asr_target",
        lambda **_kwargs: (
            operation_queue.asr_development_replay.AsrDevelopmentReplayExecution(
                target_step_id="alignment",
                source_snapshot_step_id="alignment_input",
                result=alignment,
            )
        ),
    )
    expected_state = _state("alignment")
    calls: list[str | None] = []

    def build_state(*_args, predecessor_operation_id=None, **_kwargs):
        calls.append(predecessor_operation_id)
        return expected_state

    monkeypatch.setattr(
        operation_queue.asr_development_continuation,
        "build_state_after_result",
        build_state,
    )
    submitted = operation_queue.submit(
        project.project_id,
        "english_asr",
        {
            "execution_mode": "development_target",
            "development_source_operation_id": "formal-op",
            "development_target_step_id": "alignment",
        },
    )
    assert submitted is not None

    operation_queue._process(project.project_id, submitted.operation_id)

    saved_state = development_checkpoints.load_development_checkpoint(
        checkpoint_root,
        project_id=project.project_id,
        workflow_operation_id=submitted.operation_id,
        step_id="continuation_state",
        result_model=continuation.AsrDevelopmentContinuationState,
    )
    assert calls == [None]
    assert saved_state == expected_state


def test_public_queue_continues_after_legacy_alignment_without_saved_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
            video_localization_development_step_control_enabled=True,
        )
    )
    project = project_store.create_project(
        ProjectCreate(name="Legacy alignment continuation")
    )
    predecessor = VideoLocalizationOperation(
        operation_id="alignment-op",
        project_id=project.project_id,
        kind="english_asr",
        status="success",
        parameters={
            "execution_mode": "development_target",
            "development_source_operation_id": "formal-op",
            "development_target_step_id": "alignment",
        },
    )
    service.save_video_localization(
        project.project_id,
        VideoLocalizationDraft(operations=[predecessor]),
    )
    checkpoint_root = tmp_path / "asr-checkpoints"
    monkeypatch.setattr(
        operation_queue,
        "DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT",
        checkpoint_root,
    )
    monkeypatch.setattr(operation_queue, "_enqueue", lambda *_args: None)
    word = VideoLocalizationAlignedWord(
        word_id="word-1",
        segment_id="segment-1",
        text="corrected",
        start_ms=0,
        end_ms=400,
        timing_confidence="high",
        timing_source="forced_aligner",
    )
    alignment = asr_timing_contracts.AsrAlignmentResult(
        input=asr_timing_contracts.AsrAlignmentInput(
            alignment_audio_path="/managed/vocals.wav",
            alignment_audio_sha256="alignment-sha",
            alignment_source_track_id="vocals",
            segments=[_segment()],
            language="en",
            duration_ms=1000,
        ),
        words=[word],
        metadata={"status": "completed", "timing_confidence": "high"},
    )
    development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        checkpoint_root,
        project_id=project.project_id,
        workflow_operation_id="formal-op",
    )("workflow_input", _workflow())
    development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        checkpoint_root,
        project_id=project.project_id,
        workflow_operation_id="alignment-op",
    )("alignment_result", alignment)
    assert development_checkpoints.load_development_checkpoint(
        checkpoint_root,
        project_id=project.project_id,
        workflow_operation_id="alignment-op",
        step_id="continuation_state",
        result_model=continuation.AsrDevelopmentContinuationState,
    ) is None
    monkeypatch.setattr(
        continuation,
        "_state_before_direct_target",
        lambda *_args, **_kwargs: _state(
            "transcript_quality_gate"
        ).transcription,
    )
    boundary_input = asr_timing_contracts.AsrAudioBoundariesInput(
        audio_path="/managed/source.wav",
        audio_sha256="source-sha",
        source_track_id="original",
        words=[word],
        video_frame_rate=25,
    )
    boundaries = asr_timing_contracts.AsrAudioBoundariesResult(
        input=boundary_input,
        boundary_features=[],
        subtitle_entry_by_word_id={},
        metadata={
            "status": "completed",
            "analysis_version": "boundary-v1",
            "quality_flags": [],
        },
    )
    monkeypatch.setattr(
        operation_queue.asr_development_replay,
        "replay_asr_target",
        lambda **_kwargs: (
            operation_queue.asr_development_replay.AsrDevelopmentReplayExecution(
                target_step_id="audio_boundaries",
                source_snapshot_step_id="audio_boundaries_input",
                result=boundaries,
            )
        ),
    )
    submitted = operation_queue.submit(
        project.project_id,
        "english_asr",
        {
            "execution_mode": "development_target",
            "development_source_operation_id": "formal-op",
            "development_predecessor_operation_id": "alignment-op",
            "development_target_step_id": "audio_boundaries",
        },
    )
    assert submitted is not None

    operation_queue._process(project.project_id, submitted.operation_id)

    completed = operation_queue.get_operation(
        project.project_id, submitted.operation_id
    )
    saved_state = development_checkpoints.load_development_checkpoint(
        checkpoint_root,
        project_id=project.project_id,
        workflow_operation_id=submitted.operation_id,
        step_id="continuation_state",
        result_model=continuation.AsrDevelopmentContinuationState,
    )
    assert completed is not None and completed.status == "success"
    assert saved_state is not None
    assert saved_state.last_step_id == "audio_boundaries"
    assert saved_state.transcription.words == [word]
    assert saved_state.transcription.audio_boundary_status == "completed"


def test_public_queue_continues_one_node_and_persists_fresh_input_and_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database.set_db_path(tmp_path / "voice-studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
            video_localization_development_step_control_enabled=True,
        )
    )
    project = project_store.create_project(ProjectCreate(name="ASR continuation"))
    predecessor = VideoLocalizationOperation(
        operation_id="review-op",
        project_id=project.project_id,
        kind="english_asr",
        status="success",
        parameters={
            "execution_mode": "development_target",
            "development_source_operation_id": "formal-op",
            "development_target_step_id": "review_decisions_r1",
        },
    )
    before = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="user-cue",
                start_ms=0,
                end_ms=1000,
                en_subtitle_text="user edit must survive",
            )
        ],
        operations=[predecessor],
    )
    service.save_video_localization(project.project_id, before)
    saved_before = service.get_video_localization(project.project_id)
    assert saved_before is not None
    checkpoint_root = tmp_path / "asr-checkpoints"
    monkeypatch.setattr(
        operation_queue,
        "DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT",
        checkpoint_root,
    )
    monkeypatch.setattr(operation_queue, "_enqueue", lambda *_args: None)
    lineage_writer = development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        checkpoint_root,
        project_id=project.project_id,
        workflow_operation_id="formal-op",
    )
    workflow = _workflow().model_copy(update={"operation_id": "formal-op"})
    raw_input = transcription.TranscribeRawInput(
        audio_path=workflow.audio_path,
        audio_sha256=workflow.source_audio_sha256,
        engine_id=workflow.engine_id,
        source_track_id=workflow.source_track_id,
        requested_language="en",
        duration_ms=1000,
    )
    raw = transcription.build_transcribe_raw_output(
        input=raw_input,
        raw_text="old text",
        language="en",
        segments=[_segment()],
        stage_timing={},
    )
    joined = asr_pipeline.AsrJoinedTranscript(
        raw_asr=raw,
        segments=[_segment()],
    )
    understanding_input = (
        document_understanding_contracts.AsrDocumentUnderstandingInput
        .from_joined_transcript(
            joined,
            upstream_operation_id="formal-op",
            profile_id="review-profile",
        )
    )
    understanding = document_understanding_contracts.AsrDocumentUnderstandingResult(
        input=understanding_input,
        brief={
            "summary": "One short interview.",
            "content_logic": ["One statement"],
            "speaker_style": "interview",
            "entity_candidates": [],
            "research_candidates": [],
            "review_sections": [
                {
                    "section_id": "section-1",
                    "start_ordinal": 1,
                    "end_ordinal": 1,
                    "start_segment_id": "segment-1",
                    "end_segment_id": "segment-1",
                    "role": "single section",
                    "focus": ["check transcript"],
                }
            ],
        },
        profile_id="review-profile",
        model_id="test-model",
        prompt_version="asr-flow-v5",
        execution_strategy="full_document",
        window_count=0,
        llm_call_count=1,
        retry_count=0,
        stage_timing={"duration_ms": 1},
        quality_summary={
            "status": "passed",
            "segment_count": 1,
            "section_count": 1,
            "sections_cover_all_segments": True,
            "source_text_unchanged": True,
        },
    )
    review_input = review_decisions.AsrReviewDecisionsInput(
        upstream_operation_id="section-op",
        source_track_id="original",
        source_audio_sha256="source-sha",
        language="en",
        profile_id="review-profile",
        round_index=1,
        document_summary="One short interview.",
        segments=[_segment()],
        issues=[],
        upstream_status="partial",
    )
    review_result = review_decisions.AsrReviewDecisionsResult(
        input=review_input,
        status="partial",
        profile_id="review-profile",
        decisions=[],
        updated_segments=[_segment()],
        duration_ms=1,
        quality_summary={
            "status": "warning",
            "issue_count": 0,
            "decided_issue_count": 0,
            "applied_change_count": 0,
            "unresolved_issue_count": 0,
            "upstream_review_complete": False,
            "segment_ids_unchanged": True,
            "source_timing_unchanged": True,
            "only_supplied_issues_considered": True,
            "locked_changes_preserved": True,
        },
    )
    for step_id, value in (
        ("workflow_input", workflow),
        ("asr_result", raw),
        ("initial_analysis_join_result", joined),
        ("understand_document_result", understanding),
    ):
        lineage_writer(step_id, value)
    development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        checkpoint_root,
        project_id=project.project_id,
        workflow_operation_id="review-op",
    )("review_decisions_r1_result", review_result)

    def local_whole_recheck(request, *, context=None):
        return whole_recheck.AsrWholeRecheckResult(
            input=request,
            status="completed",
            profile_id=request.profile_id,
            passed=True,
            next_action="finish",
            summary="Local invariant check passed.",
            duration_ms=1,
            quality_summary={
                "status": "passed",
                "segment_count": 1,
                "source_text_unchanged": True,
                "segment_ids_unchanged": True,
                "source_timing_unchanged": True,
                "next_sections_cover_all_segments": True,
                "unresolved_items_reference_known_segments": True,
            },
        )

    monkeypatch.setattr(
        asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_whole_recheck",
        local_whole_recheck,
    )
    submitted = operation_queue.submit(
        project.project_id,
        "english_asr",
        {
            "execution_mode": "development_target",
            "development_source_operation_id": "formal-op",
            "development_predecessor_operation_id": "review-op",
            "development_target_step_id": "whole_recheck_r1",
        },
    )
    assert submitted is not None
    operation_queue._process(project.project_id, submitted.operation_id)

    completed = operation_queue.get_operation(
        project.project_id, submitted.operation_id
    )
    after = service.get_video_localization(project.project_id)
    assert completed is not None and completed.status == "success"
    assert completed.result_summary["stage"] == (
        "ASR 开发结果已保存，未写入正式字幕"
    )
    assert completed.result_summary["task_step_results"][
        "whole_recheck_r1"
    ]["summary"] == "已使用直接前驱的新结果构建输入并运行。"
    assert [
        task["id"]
        for stage in completed.result_summary["task_stage_groups"]
        for task in stage["atomic_tasks"]
    ] == ["whole_recheck_r1"]
    assert after is not None and after.cues == saved_before.cues
    fresh_input = development_checkpoints.load_development_checkpoint(
        checkpoint_root,
        project_id=project.project_id,
        workflow_operation_id=submitted.operation_id,
        step_id="whole_recheck_r1_input",
        result_model=whole_recheck.AsrWholeRecheckInput,
    )
    state = development_checkpoints.load_development_checkpoint(
        checkpoint_root,
        project_id=project.project_id,
        workflow_operation_id=submitted.operation_id,
        step_id="continuation_state",
        result_model=continuation.AsrDevelopmentContinuationState,
    )
    assert fresh_input is not None
    assert fresh_input.upstream_operation_id == "review-op"
    assert state is not None
    assert state.last_step_id == "whole_recheck_r1"
    assert state.transcription.review_status == "partial"


def test_timing_state_keeps_partial_quality_flags_across_all_tail_steps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    current = _state("transcript_quality_gate").model_copy(
        update={
            "transcription": _state(
                "transcript_quality_gate"
            ).transcription.model_copy(
                update={
                    "quality_flags": [
                        "asr_flow_reviewed",
                        "diarization_partial",
                    ]
                }
            )
        }
    )
    monkeypatch.setattr(
        continuation,
        "_previous_state",
        lambda *_args, **_kwargs: current,
    )
    alignment = asr_timing_contracts.AsrAlignmentResult.model_construct(
        input=asr_timing_contracts.AsrAlignmentInput(
            alignment_audio_path="/managed/vocals.wav",
            alignment_audio_sha256="alignment-sha",
            alignment_source_track_id="vocals",
            segments=[_segment()],
            language="en",
            duration_ms=1000,
        ),
        words=[],
        metadata={
            "status": "partial",
            "engine_id": "test-aligner",
            "timing_confidence": "medium",
            "error": "token mismatch",
            "quality_flags": [
                "alignment_partial",
                "alignment_token_count_mismatch",
                "timing_review_required",
            ],
        },
    )
    current = continuation.build_state_after_result(
        tmp_path,
        project_id="project-1",
        lineage_operation_id="formal-op",
        predecessor_operation_id="gate-op",
        target_step_id="alignment",
        result=alignment,
    )
    assert current is not None
    assert current.transcription.alignment_status == "partial"
    assert current.transcription.alignment_error == "token mismatch"
    assert current.transcription.timing_confidence == "medium"

    boundaries = asr_timing_contracts.AsrAudioBoundariesResult.model_construct(
        boundary_features=[],
        subtitle_entry_by_word_id={},
        metadata={
            "status": "completed",
            "analysis_version": "boundary-v1",
            "quality_flags": ["audio_boundary_sparse"],
        },
    )
    current = continuation.build_state_after_result(
        tmp_path,
        project_id="project-1",
        lineage_operation_id="formal-op",
        predecessor_operation_id="alignment-op",
        target_step_id="audio_boundaries",
        result=boundaries,
    )
    assert current is not None
    assert current.transcription.audio_boundary_analysis_version == (
        "boundary-v1"
    )

    boundary_review = (
        asr_timing_contracts.AsrBoundaryReviewResult.model_construct(
            reviews=[],
            metadata={
                "status": "partial",
                "profile_id": "review-profile",
                "model_id": "local-rules",
                "prompt_version": "boundary-review-v1",
                "error": "one boundary unresolved",
                "quality_flags": ["boundary_review_partial"],
            },
        )
    )
    current = continuation.build_state_after_result(
        tmp_path,
        project_id="project-1",
        lineage_operation_id="formal-op",
        predecessor_operation_id="boundaries-op",
        target_step_id="boundary_review",
        result=boundary_review,
    )
    assert current is not None
    assert current.transcription.boundary_review_status == "partial"
    assert current.transcription.boundary_review_error == (
        "one boundary unresolved"
    )
    assert current.transcription.quality_flags == sorted(
        {
            "asr_flow_reviewed",
            "diarization_partial",
            "alignment_partial",
            "alignment_token_count_mismatch",
            "timing_review_required",
            "audio_boundary_sparse",
            "boundary_review_partial",
        }
    )
