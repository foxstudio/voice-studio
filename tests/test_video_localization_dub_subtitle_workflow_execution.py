from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domains.video_localization import (
    dub_subtitle_workflow,
    dub_subtitles,
    operation_queue,
)
from app.domains.video_localization.schemas import VideoLocalizationOperation
from app.errors import AppException
from app.schemas import video_localization_dub_subtitle_step as contracts


def _chain():
    clip = contracts.DubSubtitleSourceClip(
        clip_id="clip-1",
        dub_lane=0,
        audio_sha256="a" * 64,
        timeline_start_ms=1_000,
        timeline_end_ms=2_000,
        source_start_ms=0,
        source_end_ms=1_000,
        reference_ids=["localized-1"],
        speaker_id="speaker-1",
    )
    reference = contracts.DubSubtitleTextReference(
        subtitle_id="localized-1",
        text="分三级",
    )
    prepare_input = contracts.DubSubtitlePrepareTrackInput(
        timeline_duration_ms=3_000,
        clips=[clip],
    )
    workflow_input = contracts.DubSubtitleWorkflowInput(
        source_revision="b" * 64,
        video_frame_rate=24.0,
        prepare_track=prepare_input,
        references=[reference],
        regeneration_scope=contracts.DubSubtitleRegenerationScope(
            mode="full",
            reason="no_prior_subtitles",
            affected_clip_ids=[clip.clip_id],
        ),
    )
    prepared = contracts.DubSubtitlePrepareTrackOutput(
        audio=contracts.DubSubtitlePreparedAudio(
            artifact_id="operation:full-dub",
            audio_sha256="c" * 64,
            duration_ms=3_000,
        ),
        clip_count=1,
    )
    transcribed_input = contracts.DubSubtitleTranscribeTrackInput(
        audio=prepared.audio,
        engine_id="qwen3-asr-mlx",
    )
    transcribed = contracts.DubSubtitleTranscribeTrackOutput(
        audio_sha256=prepared.audio.audio_sha256,
        engine_id=transcribed_input.engine_id,
        language="zh",
        chunks=[
            contracts.DubSubtitleTranscriptChunk(
                segment_id="segment-1",
                audio_window_start_ms=1_000,
                audio_window_end_ms=2_000,
                text="分三集",
            )
        ],
        quality_status="passed",
    )
    proofread_input = contracts.DubSubtitleProofreadTextInput(
        chunks=transcribed.chunks,
        references=[reference],
    )
    proofread = contracts.DubSubtitleProofreadTextOutput(
        chunks=[
            contracts.DubSubtitleProofreadChunk(
                cue_id="cue-1",
                audio_window_start_ms=1_000,
                audio_window_end_ms=2_000,
                raw_text="分三集",
                text="分三级",
            )
        ],
    )
    aligned = contracts.DubSubtitleAlignWordsOutput(
        audio_sha256=prepared.audio.audio_sha256,
        words=[
            contracts.DubSubtitleAlignedWord(
                word_id="word-1",
                cue_id="cue-1",
                text="分三级",
                start_ms=1_100,
                end_ms=1_900,
            )
        ],
        alignment_call_count=1,
    )
    segmented = contracts.DubSubtitleSegmentSubtitlesOutput(
        subtitles=[
            contracts.DubSubtitleCandidateCue(
                subtitle_id="dub-1",
                start_ms=1_100,
                end_ms=1_900,
                text="分三级",
                speaker_id="speaker-1",
                source_clip_ids=["clip-1"],
                dub_lanes=[0],
                source_audio_sha256="c" * 64,
            )
        ],
    )
    return (
        workflow_input,
        prepared,
        transcribed,
        proofread,
        aligned,
        segmented,
    )


@pytest.mark.parametrize(
    ("target_step_id", "expected_calls"),
    [
        ("prepare_track", ["prepare_track"]),
        (
            "transcribe_track",
            ["prepare_track", "transcribe_track"],
        ),
        (
            "proofread_text",
            ["prepare_track", "transcribe_track", "proofread_text"],
        ),
        (
            "align_words",
            [
                "prepare_track",
                "transcribe_track",
                "proofread_text",
                "align_words",
            ],
        ),
        (
            "segment_subtitles",
            [
                "prepare_track",
                "transcribe_track",
                "proofread_text",
                "align_words",
                "segment_subtitles",
            ],
        ),
    ],
)
def test_development_target_never_executes_downstream_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_step_id: str,
    expected_calls: list[str],
):
    (
        workflow_input,
        prepared,
        transcribed,
        proofread,
        aligned,
        segmented,
    ) = _chain()
    calls: list[str] = []

    def fake(name, result):
        def action(*_args, **_kwargs):
            calls.append(name)
            return result

        return action

    monkeypatch.setattr(
        dub_subtitles,
        "prepare_track",
        fake("prepare_track", prepared),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "transcribe_track",
        fake("transcribe_track", transcribed),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "proofread_text",
        fake("proofread_text", proofread),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "align_words",
        fake("align_words", aligned),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "segment_subtitles",
        fake("segment_subtitles", segmented),
    )
    execution = dub_subtitle_workflow.DubSubtitleWorkflowExecution(
        operation_id="operation-1",
        workflow_input=workflow_input,
        engine_id="qwen3-asr-mlx",
        audio_paths={"clip-1": Path("/not-used.wav")},
        commit_action=lambda _request: pytest.fail(
            "development target must not commit"
        ),
        development=(
            dub_subtitle_workflow
            .DubSubtitleDevelopmentExecutionConfig(
                target_step_id=target_step_id,
                development_session_id="session-1",
                project_id="project-1",
                snapshot_root=tmp_path,
            )
        ),
    )

    result = execution.run()

    assert calls == expected_calls
    assert list(result.step_results) == expected_calls


def test_formal_execution_runs_all_six_atomic_steps(
    monkeypatch: pytest.MonkeyPatch,
):
    (
        workflow_input,
        prepared,
        transcribed,
        proofread,
        aligned,
        segmented,
    ) = _chain()
    calls: list[str] = []

    def fake(name, result):
        def action(*_args, **_kwargs):
            calls.append(name)
            return result

        return action

    monkeypatch.setattr(
        dub_subtitles,
        "prepare_track",
        fake("prepare_track", prepared),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "transcribe_track",
        fake("transcribe_track", transcribed),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "proofread_text",
        fake("proofread_text", proofread),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "align_words",
        fake("align_words", aligned),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "segment_subtitles",
        fake("segment_subtitles", segmented),
    )

    def commit(request):
        calls.append("commit")
        return contracts.DubSubtitleCommitOutput(
            saved_source_revision=request.source_revision,
            saved_subtitle_count=1,
        )

    result = dub_subtitle_workflow.DubSubtitleWorkflowExecution(
        operation_id="operation-1",
        workflow_input=workflow_input,
        engine_id="qwen3-asr-mlx",
        audio_paths={"clip-1": Path("/not-used.wav")},
        commit_action=commit,
    ).run()

    assert calls == [
        "prepare_track",
        "transcribe_track",
        "proofread_text",
        "align_words",
        "segment_subtitles",
        "commit",
    ]
    assert result.commit is not None
    assert list(result.step_results) == calls
    assert (
        result.step_results["transcribe_track"]["sections"][0]["title"]
        == "首次听写结果"
    )
    assert (
        result.step_results["align_words"]["sections"][0]["title"]
        == "真实字词时间"
    )
    assert (
        result.step_results["segment_subtitles"]["sections"][0][
            "title"
        ]
        == "最终字幕候选"
    )
    assert all(
        "result" not in step_result
        for step_result in result.step_results.values()
    )


def test_development_target_reuses_valid_upstream_snapshots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    (
        workflow_input,
        prepared,
        transcribed,
        proofread,
        aligned,
        segmented,
    ) = _chain()
    calls: list[str] = []

    def fake(name, result):
        def action(*_args, **_kwargs):
            calls.append(name)
            return result

        return action

    monkeypatch.setattr(
        dub_subtitles,
        "prepare_track",
        fake("prepare_track", prepared),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "transcribe_track",
        fake("transcribe_track", transcribed),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "proofread_text",
        fake("proofread_text", proofread),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "align_words",
        fake("align_words", aligned),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "segment_subtitles",
        fake("segment_subtitles", segmented),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "ensure_prepared_audio_available",
        lambda *_args, **_kwargs: None,
    )

    def run_target(operation_id: str, target_step_id: str):
        return dub_subtitle_workflow.DubSubtitleWorkflowExecution(
            operation_id=operation_id,
            workflow_input=workflow_input,
            engine_id="qwen3-asr-mlx",
            audio_paths={"clip-1": Path("/not-used.wav")},
            commit_action=lambda _request: pytest.fail(
                "this test must not commit"
            ),
            development=(
                dub_subtitle_workflow
                .DubSubtitleDevelopmentExecutionConfig(
                    target_step_id=target_step_id,
                    development_session_id="session-reuse",
                    project_id="project-1",
                    snapshot_root=tmp_path,
                )
            ),
        ).run()

    first = run_target("operation-1", "transcribe_track")
    second = run_target("operation-2", "proofread_text")

    assert calls == [
        "prepare_track",
        "transcribe_track",
        "proofread_text",
    ]
    assert first.executed_step_ids == [
        "prepare_track",
        "transcribe_track",
    ]
    assert first.reused_step_ids == []
    assert second.executed_step_ids == ["proofread_text"]
    assert second.reused_step_ids == [
        "prepare_track",
        "transcribe_track",
    ]
    assert second.stage_timings["prepare_track"]["reused"] is True
    assert second.stage_timings["transcribe_track"]["reused"] is True


def test_alignment_failure_never_segments_or_commits(
    monkeypatch: pytest.MonkeyPatch,
):
    (
        workflow_input,
        prepared,
        transcribed,
        proofread,
        _aligned,
        _segmented,
    ) = _chain()
    calls: list[str] = []

    def fixed(name, result):
        def action(*_args, **_kwargs):
            calls.append(name)
            return result

        return action

    monkeypatch.setattr(
        dub_subtitles,
        "prepare_track",
        fixed("prepare_track", prepared),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "transcribe_track",
        fixed("transcribe_track", transcribed),
    )
    monkeypatch.setattr(
        dub_subtitles,
        "proofread_text",
        fixed("proofread_text", proofread),
    )

    def fail_alignment(*_args, **_kwargs):
        calls.append("align_words")
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED",
            "没有真实声学时间",
        )

    monkeypatch.setattr(
        dub_subtitles,
        "align_words",
        fail_alignment,
    )
    monkeypatch.setattr(
        dub_subtitles,
        "segment_subtitles",
        lambda *_args, **_kwargs: pytest.fail(
            "alignment failure must not segment"
        ),
    )

    with pytest.raises(AppException) as exc:
        dub_subtitle_workflow.DubSubtitleWorkflowExecution(
            operation_id="operation-1",
            workflow_input=workflow_input,
            engine_id="qwen3-asr-mlx",
            audio_paths={"clip-1": Path("/not-used.wav")},
            commit_action=lambda _request: pytest.fail(
                "alignment failure must not commit"
            ),
        ).run()

    assert exc.value.code == (
        "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED"
    )
    assert calls == [
        "prepare_track",
        "transcribe_track",
        "proofread_text",
        "align_words",
    ]


def test_delete_only_incremental_execution_skips_audio_steps_and_commits():
    workflow_input = contracts.DubSubtitleWorkflowInput(
        source_revision="b" * 64,
        prepare_track=contracts.DubSubtitlePrepareTrackInput(
            timeline_duration_ms=3_000,
            clips=[],
        ),
        references=[],
        regeneration_scope=contracts.DubSubtitleRegenerationScope(
            mode="incremental",
            reason="dirty_scope",
            affected_clip_ids=["deleted-clip"],
            affected_ranges=[
                contracts.DubSubtitleAffectedRange(
                    start_ms=1_000,
                    end_ms=2_000,
                )
            ],
            replaceable_subtitle_fingerprints={"old-dub": "a" * 64},
        ),
    )
    received: list[contracts.DubSubtitleCommitInput] = []

    result = dub_subtitle_workflow.DubSubtitleWorkflowExecution(
        operation_id="operation-delete",
        workflow_input=workflow_input,
        engine_id="qwen3-asr-mlx",
        audio_paths={},
        commit_action=lambda request: (
            received.append(request)
            or contracts.DubSubtitleCommitOutput(
                saved_source_revision=request.source_revision,
                saved_subtitle_count=0,
            )
        ),
    ).run()

    assert result.prepare_track is None
    assert result.commit is not None
    assert list(result.step_results) == ["commit"]
    assert received[0].subtitles == []
    assert received[0].regeneration_scope == workflow_input.regeneration_scope


@pytest.mark.parametrize(
    ("execution_mode", "target_step_id"),
    [
        ("development_target", "prepare_track"),
        ("development_target", "transcribe_track"),
        ("full", None),
    ],
)
def test_operation_queue_uses_six_step_execution_facade_and_reports_steps(
    monkeypatch: pytest.MonkeyPatch,
    execution_mode: str,
    target_step_id: str | None,
):
    parameters = {
        "engine_id": "qwen3-asr-mlx",
        "execution_mode": execution_mode,
    }
    if target_step_id is not None:
        parameters.update(
            {
                "development_target_step_id": target_step_id,
                "development_session_id": "dub-debug-1",
            }
        )
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="dub_subtitle_generation",
        label="根据合成配音生成字幕",
        parameters=parameters,
        result_summary=operation_queue._initial_operation_summary(
            "dub_subtitle_generation",
            parameters,
        ),
    )
    marks: list[dict] = []
    received: list[dict] = []

    def fake_generate(project_id: str, **kwargs):
        assert project_id == "project-1"
        received.append(kwargs)
        step_ids = (
            ["prepare_track", "transcribe_track"]
            if target_step_id == "transcribe_track"
            else (
                ["prepare_track"]
                if target_step_id == "prepare_track"
                else [
                    "prepare_track",
                    "transcribe_track",
                    "proofread_text",
                    "align_words",
                    "segment_subtitles",
                    "commit",
                ]
            )
        )
        step_results = {}
        for order, step_id in enumerate(step_ids, start=1):
            step_result = {
                "label": step_id,
                "order": order,
                "status": "success",
                "summary": f"{step_id} completed",
                "_task_timing": {
                    "duration_ms": order,
                    "atomic": True,
                },
            }
            kwargs["on_report"](step_id, step_result)
            step_results[step_id] = {
                key: value
                for key, value in step_result.items()
                if key != "_task_timing"
            }
        return object(), {
            "stage": "done",
            "stage_id": step_ids[-1],
            "task_step_results": step_results,
        }

    monkeypatch.setattr(
        operation_queue.service,
        "generate_dub_subtitles",
        fake_generate,
    )
    monkeypatch.setattr(
        operation_queue,
        "_mark_operation",
        lambda *_args, **kwargs: marks.append(kwargs),
    )
    monkeypatch.setattr(
        operation_queue,
        "get_operation",
        lambda _project_id, _operation_id: operation,
    )
    monkeypatch.setattr(
        operation_queue,
        "_cancel_requested",
        lambda _project_id, _operation_id: False,
    )

    operation_queue._process_operation(
        "project-1",
        operation,
        execution_claim=SimpleNamespace(
            lost=False,
            execution_fence=object(),
        ),
    )

    request = received[0]
    assert request["operation_id"] == "operation-1"
    development = request["development_execution"]
    if target_step_id is None:
        assert development is None
        assert any(
            mark.get("kind") == "dub_subtitle_generation"
            for mark in marks
        )
    else:
        assert development.target_step_id == target_step_id
        assert development.development_session_id == "dub-debug-1"
        assert development.project_id == "project-1"
        assert development.snapshot_root == (
            operation_queue
            .DEVELOPMENT_DUB_SUBTITLE_WORKFLOW_CHECKPOINT_ROOT
        )
        assert all(mark.get("kind") is None for mark in marks)
    reported_steps = [
        next(iter(summary["task_step_results"]))
        for mark in marks
        if isinstance(
            summary := mark.get("result_summary"),
            dict,
        )
        and isinstance(summary.get("task_step_results"), dict)
        and len(summary["task_step_results"]) == 1
    ]
    assert reported_steps[: len(requested := (
        ["prepare_track", "transcribe_track"]
        if target_step_id == "transcribe_track"
        else (
            ["prepare_track"]
            if target_step_id == "prepare_track"
            else [
                "prepare_track",
                "transcribe_track",
                "proofread_text",
                "align_words",
                "segment_subtitles",
                "commit",
            ]
        )
    ))] == requested
