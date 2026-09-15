from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domains.video_localization import (
    localization_dual_tracks,
    localization_tracks,
    localization_workflow_execution,
    operation_queue,
    operation_state,
    service,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationSpokenSegment,
    VideoLocalizationSubtitleCue,
)
from app.schemas.voice_studio import ProjectCreate
from app.services import project_store, video_localization_operation_ledger_store
from app.services.localization_ai_policy import LocalizationAiPhaseRoute
from tests.test_video_localization_localization_dual_tracks import (
    _fixture as _dual_track_fixture,
)
from tests.test_video_localization_localization_source import (
    _configure,
    _draft as _source_draft,
)


def _operation_fixture(tmp_path: Path) -> tuple[str, str]:
    _configure(tmp_path, enabled=True)
    project = project_store.create_project(
        ProjectCreate(name="localization terminal commit")
    )
    operation = VideoLocalizationOperation(
        operation_id="localization-terminal-commit",
        project_id=project.project_id,
        kind="localization_draft",
        status="queued",
        label="生成本土化字幕初稿",
        parameters={
            "execution_mode": "full",
            "target_language": "zh-Hans",
        },
    )
    saved = service.save_video_localization(
        project.project_id,
        VideoLocalizationDraft(operations=[operation]),
    )
    assert saved is not None
    return project.project_id, operation.operation_id


def _formal_tracks(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    return draft.model_copy(
        update={
            "localized_spoken_segments": [
                VideoLocalizationSpokenSegment(
                    segment_id="spoken-1",
                    paragraph_id="paragraph-1",
                    text="你好，创作者。",
                    start_ms=100,
                    end_ms=1_000,
                    source_cue_ids=["cue-1"],
                    source_word_ids=["word-1"],
                )
            ],
            "localized_subtitles": [
                VideoLocalizationSubtitleCue(
                    subtitle_id="localized-1",
                    start_ms=100,
                    end_ms=1_000,
                    text="你好，创作者。",
                    tts_text="你好，创作者。",
                    source_cue_ids=["cue-1"],
                    source_word_ids=["word-1"],
                    spoken_segment_id="spoken-1",
                )
            ],
        }
    )


def _formal_summary() -> dict:
    task_step_results = {
        task.id: {
            "label": task.label,
            "order": task.order,
            "status": "success",
            "purpose": task.description,
            "summary": f"{task.label}已完成。",
            "metrics": [],
            "sections": [],
            "notes": [],
        }
        for stage in operation_queue.workflow_contracts.LOCALIZATION_WORKFLOW_V3_DEFINITION.stages
        for task in stage.atomic_tasks
    }
    return {
        "stage": "本土化字幕已生成",
        "stage_id": "commit_localization_tracks",
        "workflow_schema_version": "localization-workflow-v3",
        "workflow_id": "localization-v3",
        "task_step_results": task_step_results,
        "localized_spoken_segment_count": 1,
        "localized_subtitle_count": 1,
        "result_count": 1,
        "result_unit": "条上屏字幕",
        "preview_phase": "localization_v3_committed",
        "preview_cues": [
            {
                "subtitle_id": "localized-1",
                "start_ms": 100,
                "end_ms": 1_000,
                "text": "你好，创作者。",
                "spoken_segment_id": "spoken-1",
            }
        ],
    }


def test_common_formal_finalizer_keeps_asr_final_steps_authoritative():
    operation = VideoLocalizationOperation(
        operation_id="asr-terminal-commit",
        project_id="project-asr-terminal-commit",
        kind="english_asr",
        status="running",
        label="听写英文字幕",
        parameters={"execution_mode": "full"},
        result_summary={
            "task_step_results": {
                "text_review": {
                    "status": "running",
                    "summary": "正在复核。",
                    "debug": {"metrics": [{"label": "轮次", "value": "1"}]},
                }
            }
        },
    )
    finalized = operation_queue._finalize_formal_operation(
        VideoLocalizationDraft(operations=[operation]),
        operation.operation_id,
        {
            "task_step_results": {
                "text_review": {
                    "status": "warning",
                    "summary": "复核完成，仍有一处建议人工确认。",
                }
            }
        },
        "2026-09-11T10:00:00+00:00",
    )

    completed = operation_state.operation_from_draft(
        finalized,
        operation.operation_id,
    )
    assert completed is not None
    assert completed.status == "success"
    text_review = completed.result_summary["task_step_results"][
        "text_review"
    ]
    assert text_review["status"] == "warning"
    assert text_review["debug"] == {
        "metrics": [{"label": "轮次", "value": "1"}]
    }


def test_committed_formal_check_excludes_development_operations():
    formal = VideoLocalizationOperation(
        operation_id="formal-asr",
        project_id="project-formal-check",
        kind="english_asr",
        status="running",
        parameters={"execution_mode": "full"},
    )
    development = formal.model_copy(
        update={
            "operation_id": "development-asr",
            "parameters": {
                "execution_mode": "development_target",
                "development_target_step_id": "text_review",
            },
        }
    )

    assert operation_queue._formal_operation_already_committed(
        formal,
        formal.model_copy(update={"status": "success"}),
    )
    assert not operation_queue._formal_operation_already_committed(
        development,
        development.model_copy(update={"status": "success"}),
    )


@pytest.mark.parametrize("post_commit_failure", ["checkpoint", "progress"])
def test_formal_tracks_and_operation_success_commit_before_observer_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    post_commit_failure: str,
):
    project_id, operation_id = _operation_fixture(tmp_path)

    def fail_checkpoint(*_args, **_kwargs):
        raise RuntimeError("injected post-commit checkpoint failure")

    if post_commit_failure == "checkpoint":
        monkeypatch.setattr(
            operation_queue.development_checkpoints,
            "LocalizationDevelopmentCheckpointWriter",
            lambda *_args, **_kwargs: fail_checkpoint,
        )
    else:
        original_mark = operation_queue._mark_operation

        def fail_late_progress(*args, **kwargs):
            summary = kwargs.get("result_summary") or {}
            if summary.get("stage") == "injected late progress":
                raise RuntimeError("injected post-commit progress failure")
            return original_mark(*args, **kwargs)

        monkeypatch.setattr(
            operation_queue,
            "_mark_operation",
            fail_late_progress,
        )

    def fake_localization(
        project_id_arg: str,
        *,
        finalize_formal_commit,
        on_atomic_result,
        on_progress,
        **_kwargs,
    ):
        def apply(current: VideoLocalizationDraft) -> VideoLocalizationDraft:
            return finalize_formal_commit(
                _formal_tracks(current),
                _formal_summary(),
                "2026-09-11T10:00:00+00:00",
            )

        saved = service.update_video_localization_atomic(
            project_id_arg,
            apply,
            intent="content",
        )
        assert saved is not None
        if post_commit_failure == "checkpoint":
            assert on_atomic_result is not None
            on_atomic_result("commit_localization_tracks", object())
            raise AssertionError("checkpoint failure should have interrupted")
        on_progress(0.99, "injected late progress")
        raise AssertionError("progress failure should have interrupted")

    monkeypatch.setattr(service, "run_localization_v3_draft", fake_localization)

    operation_queue._process(project_id, operation_id)

    refreshed = service.get_video_localization(project_id)
    assert refreshed is not None
    assert [item.segment_id for item in refreshed.localized_spoken_segments] == [
        "spoken-1"
    ]
    assert [item.subtitle_id for item in refreshed.localized_subtitles] == [
        "localized-1"
    ]
    completed = operation_state.operation_from_draft(refreshed, operation_id)
    assert completed is not None
    assert completed.status == "success"
    assert completed.result_summary["task_final_result"]["status"] == "success"
    ledger = video_localization_operation_ledger_store.get_operation(
        project_id,
        operation_id,
    )
    assert ledger is not None
    assert ledger.status == "success"
    revision_before_late_progress = project_store.get_project(
        project_id
    )._repository_revision
    operation_queue._mark_operation(
        project_id,
        operation_id,
        kind="localization_draft",
        status="running",
        progress=0.5,
        result_summary={"stage": "stale late progress"},
    )
    revision_after_late_progress = project_store.get_project(
        project_id
    )._repository_revision
    assert revision_after_late_progress == revision_before_late_progress
    refreshed_history = operation_queue.get_operation(project_id, operation_id)
    assert refreshed_history is not None
    assert refreshed_history.status == "success"
    assert refreshed_history.result_summary["task_final_result"]["status"] == (
        "success"
    )


def test_failed_formal_commit_rolls_back_tracks_and_terminal_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project_id, operation_id = _operation_fixture(tmp_path)
    original_sync = (
        video_localization_operation_ledger_store.sync_project_operations
    )

    def fail_success_sync(connection, project_id_arg, operations, *, updated_at):
        original_sync(
            connection,
            project_id_arg,
            operations,
            updated_at=updated_at,
        )
        if any(
            item.get("operation_id") == operation_id
            and item.get("status") == "success"
            for item in operations
        ):
            raise RuntimeError("injected formal transaction failure")

    monkeypatch.setattr(
        video_localization_operation_ledger_store,
        "sync_project_operations",
        fail_success_sync,
    )

    def fake_localization(
        project_id_arg: str,
        *,
        finalize_formal_commit,
        **_kwargs,
    ):
        saved = service.update_video_localization_atomic(
            project_id_arg,
            lambda current: finalize_formal_commit(
                _formal_tracks(current),
                _formal_summary(),
                "2026-09-11T10:00:00+00:00",
            ),
            intent="content",
        )
        return saved, _formal_summary()

    monkeypatch.setattr(service, "run_localization_v3_draft", fake_localization)

    operation_queue._process(project_id, operation_id)

    refreshed = service.get_video_localization(project_id)
    assert refreshed is not None
    assert refreshed.localized_spoken_segments == []
    assert refreshed.localized_subtitles == []
    completed = operation_state.operation_from_draft(refreshed, operation_id)
    assert completed is not None
    assert completed.status == "failed"
    ledger = video_localization_operation_ledger_store.get_operation(
        project_id,
        operation_id,
    )
    assert ledger is not None
    assert ledger.status == "failed"


def install_fixed_real_localization_execution(
    initial: VideoLocalizationDraft,
    patch_attribute: Callable[[object, str, object], None],
    *,
    patch_policy: bool = True,
) -> tuple[int, int]:
    """Fix model-node outputs while retaining the real service commit node."""

    source_lock = (
        service.localization_source.DEFAULT_LOCALIZATION_PIPELINE.lock_source(
            service.localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(
                initial
            )
        )
    )
    final_script, adjudicated_alignment = _dual_track_fixture()
    final_script = final_script.model_copy(
        update={"source_fingerprint": source_lock.source_fingerprint}
    )
    dual_tracks = localization_dual_tracks.build_localization_dual_tracks(
        localization_dual_tracks.LocalizationDualTrackInput(
            spoken_script_operation_id="script-operation",
            alignment_operation_id="alignment-operation",
            spoken_script=final_script,
            alignment=adjudicated_alignment,
        )
    )
    gate = localization_tracks.validate_localization_tracks(
        localization_tracks.LocalizationTracksQualityGateInput(
            dual_tracks_operation_id="dual-tracks-operation",
            source_fingerprint=source_lock.source_fingerprint,
            final_script=final_script,
            dual_tracks=dual_tracks,
        ),
        current_source_fingerprint=source_lock.source_fingerprint,
    )
    context_intent = SimpleNamespace(context_intent_fingerprint="c" * 64)
    brief = SimpleNamespace(result_fingerprint="d" * 64)
    evidence = SimpleNamespace(result_fingerprint="e" * 64)
    creation_context = SimpleNamespace(
        result_fingerprint="f" * 64,
        content=SimpleNamespace(verified_evidence_constraints=[]),
    )
    generated_script = SimpleNamespace(result_fingerprint="1" * 64)
    fixed_results = {
        "lock_localization_source": source_lock,
        "lock_localization_context_intent": context_intent,
        "analyze_localization_document": brief,
        "adjudicate_localization_evidence_v3": evidence,
        "lock_localization_creation_context": creation_context,
        "generate_localization_spoken_script": generated_script,
        "finalize_localization_spoken_script": final_script,
        "align_localization_semantics": adjudicated_alignment,
        "adjudicate_localization_alignment": adjudicated_alignment,
        "build_localization_dual_tracks": dual_tracks,
        "adjudicate_localization_display_boundaries": SimpleNamespace(
            dual_tracks=dual_tracks
        ),
        "validate_localization_tracks": gate,
    }

    def fixed_run(self, step_id, _summary, _action, _projector, **_kwargs):
        return fixed_results[step_id]

    def fixed_parallel(self, jobs, **_kwargs):
        step_ids = [item[0] for item in jobs]
        if step_ids == [
            "collect_localization_research_evidence_v3",
            "collect_localization_visual_evidence_v3",
        ]:
            return [SimpleNamespace(), SimpleNamespace()]
        assert step_ids == [
            "review_localization_fidelity",
            "review_localization_naturalness",
        ]
        return [SimpleNamespace(), SimpleNamespace()]

    patch_attribute(
        localization_workflow_execution.LocalizationWorkflowExecution,
        "run",
        fixed_run,
    )
    patch_attribute(
        localization_workflow_execution.LocalizationWorkflowExecution,
        "run_parallel",
        fixed_parallel,
    )
    if patch_policy:
        route = LocalizationAiPhaseRoute(
            phase="document_understanding",
            profile_id="fixed-profile",
            model_id="fixed-model",
            reasoning_effort="low",
            output_format="json",
            prompt_strategy="adaptive",
        )
        policy = SimpleNamespace(
            routes=[route],
            route=lambda phase: route.model_copy(update={"phase": phase}),
        )
        patch_attribute(
            service.localization_ai_policy,
            "resolve_localization_ai_policy",
            lambda *_args, **_kwargs: policy,
        )
    patch_attribute(
        service.localization_creation_context,
        "project_localization_creation_source_lock",
        lambda *_args, **_kwargs: source_lock,
    )
    patch_attribute(
        service.localization_creation_context,
        "project_localization_creation_sections",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                section_id="section_0001",
                source_cue_ids=[
                    item.cue_id for item in source_lock.input.cues
                ],
            )
        ],
    )
    patch_attribute(
        service.localization_spoken_script,
        "LocalizationSpokenScriptInput",
        lambda **_kwargs: SimpleNamespace(source_lock=source_lock),
    )
    patch_attribute(
        service.localization_spoken_script,
        "LocalizationSpokenScriptFinalizationInput",
        lambda **_kwargs: SimpleNamespace(),
    )
    patch_attribute(
        service,
        "LabseTextEncoder",
        lambda: SimpleNamespace(
            model_id="fixed-encoder",
            model_fingerprint="2" * 64,
        ),
    )
    return len(dual_tracks.spoken_segments), len(dual_tracks.display_cues)


def test_real_service_commit_segment_finalizes_before_checkpoint_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _configure(tmp_path, enabled=True)
    project = project_store.create_project(
        ProjectCreate(name="real localization commit segment")
    )
    operation = VideoLocalizationOperation(
        operation_id="real-service-commit",
        project_id=project.project_id,
        kind="localization_draft",
        status="running",
        label="生成本土化字幕初稿",
        parameters={
            "execution_mode": "full",
            "target_language": "zh-Hans",
        },
    )
    initial = _source_draft().model_copy(
        update={"operations": [operation]}
    )
    assert service.save_video_localization(project.project_id, initial) is not None
    install_fixed_real_localization_execution(
        initial,
        monkeypatch.setattr,
    )

    def fail_commit_checkpoint(step_id, _result):
        if step_id == "commit_localization_tracks":
            raise RuntimeError("injected real service checkpoint failure")

    with pytest.raises(
        RuntimeError,
        match="injected real service checkpoint failure",
    ):
        service.run_localization_v3_draft(
            project.project_id,
            operation_id=operation.operation_id,
            profile_id="fixed-profile",
            on_atomic_result=fail_commit_checkpoint,
            finalize_formal_commit=(
                lambda draft, summary, completed_at: (
                    operation_queue._finalize_formal_operation(
                        draft,
                        operation.operation_id,
                        summary,
                        completed_at,
                    )
                )
            ),
        )

    refreshed = service.get_video_localization(project.project_id)
    assert refreshed is not None
    assert refreshed.localized_spoken_segments
    assert refreshed.localized_subtitles
    completed = operation_state.operation_from_draft(
        refreshed,
        operation.operation_id,
    )
    assert completed is not None
    assert completed.status == "success"
    ledger = video_localization_operation_ledger_store.get_operation(
        project.project_id,
        operation.operation_id,
    )
    assert ledger is not None
    assert ledger.status == "success"
