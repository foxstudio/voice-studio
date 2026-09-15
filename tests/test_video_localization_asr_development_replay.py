from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.video_localization import (
    VideoLocalizationAsrOperationRequest,
)
from app.domains.video_localization import (
    asr_development_replay,
    development_checkpoints,
    operation_queue,
    review_decisions,
    workflow_contracts,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationTranscriptSegment,
)
from app.services import llm_runtime, settings_store


def _review_request() -> review_decisions.AsrReviewDecisionsInput:
    return review_decisions.AsrReviewDecisionsInput(
        upstream_operation_id="section-review-r1",
        source_track_id="vocals",
        source_audio_sha256="audio-sha",
        language="en",
        profile_id="review-profile",
        round_index=1,
        document_summary="One interview segment.",
        content_logic=["check one phrase"],
        speaker_style="interview",
        segments=[
            VideoLocalizationTranscriptSegment(
                segment_id="segment-1",
                start_ms=0,
                end_ms=1_000,
                raw_text="AI less demand.",
            )
        ],
        issues=[
            {
                "issue_id": "issue-1",
                "section_id": "section-1",
                "segment_id": "segment-1",
                "current_excerpt": "AI less demand",
                "proposed_replacement": "AI means less demand",
                "reason": "A word may be missing.",
                "confidence": 0.9,
            }
        ],
        upstream_status="completed",
    )


def test_every_asr_atomic_task_declares_one_replay_contract() -> None:
    tasks = [*workflow_contracts.asr_development_replay_tasks()]

    assert tuple(task.id for task in tasks) == (asr_development_replay.ASR_DEVELOPMENT_TARGET_STEP_IDS)
    assert all(task.output_contract_version for task in tasks)
    assert {task.id: task.output_contract_version for task in tasks} == {
        task.id: (asr_development_replay.expected_output_contract(task.id)) for task in tasks
    }


def test_asr_replay_accepts_formal_raw_snapshot_contract() -> None:
    assert asr_development_replay.output_contract_matches(
        "asr",
        "asr-raw-v2",
    )
    assert not asr_development_replay.output_contract_matches(
        "asr",
        "asr-raw-v1",
    )


def test_review_decisions_replay_uses_post_model_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {
            "decisions": [
                {
                    "issue_id": "issue-1",
                    "accept": True,
                    "replacement": "AI means less demand",
                    "reason": "Context confirms the missing word.",
                    "confidence": 0.95,
                    "needs_confirmation": False,
                    "evidence_source_ids": [],
                }
            ]
        },
    )
    prepared: list[review_decisions.AsrReviewDecisionsPreparedSnapshot] = []
    expected = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
        _review_request(),
        on_prepared_decisions=prepared.append,
    )
    writer = development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="formal-operation",
    )
    writer("review_decisions_r1_prepared", prepared[0])

    def unexpected_model_call(*_args, **_kwargs):
        raise AssertionError("prepared replay must not call the model")

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        unexpected_model_call,
    )
    execution = asr_development_replay.replay_asr_target(
        root=tmp_path,
        project_id="project-1",
        source_operation_id="formal-operation",
        replay_operation_id="replay-operation",
        target_step_id="review_decisions_r1",
    )

    assert execution.skipped_paid_preparation is True
    assert execution.source_snapshot_step_id == ("review_decisions_r1_prepared")
    assert execution.result.updated_segments == (expected.updated_segments)
    assert execution.result.changes == expected.changes
    assert execution.result.warnings == expected.warnings
    assert execution.result.quality_summary == (expected.quality_summary)


def test_asr_development_target_request_requires_source_and_target() -> None:
    request = VideoLocalizationAsrOperationRequest(
        execution_mode="development_target",
        development_source_operation_id="formal-operation",
        development_target_step_id="boundary_review",
    )

    assert request.development_target_step_id == "boundary_review"
    assert "stop_after_step" not in request.model_fields_set
    with pytest.raises(ValidationError):
        VideoLocalizationAsrOperationRequest(
            execution_mode="development_target",
            development_source_operation_id="formal-operation",
        )
    with pytest.raises(ValidationError):
        VideoLocalizationAsrOperationRequest(
            execution_mode="full",
            development_source_operation_id="formal-operation",
            development_target_step_id="boundary_review",
        )


def test_asr_development_target_parameters_are_minimal_and_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_store,
        "get",
        lambda: SimpleNamespace(video_localization_development_step_control_enabled=True),
    )

    normalized = operation_queue._normalized_operation_parameters(
        "english_asr",
        {
            "execution_mode": "development_target",
            "development_source_operation_id": "formal-operation",
            "development_target_step_id": "subtitle_track",
        },
        VideoLocalizationDraft(),
    )

    assert normalized == {
        "execution_mode": "development_target",
        "development_source_operation_id": "formal-operation",
        "development_target_step_id": "subtitle_track",
    }


def _development_operation(
    operation_id: str,
    *,
    lineage_id: str,
    step_id: str,
    status: str = "success",
) -> VideoLocalizationOperation:
    return VideoLocalizationOperation(
        operation_id=operation_id,
        project_id="project-1",
        kind="english_asr",
        status=status,
        parameters={
            "execution_mode": "development_target",
            "development_source_operation_id": lineage_id,
            "development_target_step_id": step_id,
        },
    )


def test_asr_continuation_requires_successful_direct_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_store,
        "get",
        lambda: SimpleNamespace(
            video_localization_development_step_control_enabled=True
        ),
    )
    draft = VideoLocalizationDraft(
        operations=[
            _development_operation(
                "review-op",
                lineage_id="formal-op",
                step_id="review_decisions_r1",
                status="running",
            )
        ]
    )

    with pytest.raises(Exception) as exc_info:
        operation_queue._normalized_operation_parameters(
            "english_asr",
            {
                "execution_mode": "development_target",
                "development_source_operation_id": "formal-op",
                "development_predecessor_operation_id": "review-op",
                "development_target_step_id": "whole_recheck_r1",
            },
            draft,
        )

    assert getattr(exc_info.value, "code", "") == (
        "VIDEO_LOCALIZATION_ASR_CONTINUATION_PREDECESSOR_INVALID"
    )


@pytest.mark.parametrize(
    ("lineage_id", "step_id"),
    [
        ("different-formal-op", "review_decisions_r1"),
        ("formal-op", "whole_recheck_r1"),
    ],
)
def test_asr_continuation_rejects_crossed_or_skipped_lineage(
    monkeypatch: pytest.MonkeyPatch,
    lineage_id: str,
    step_id: str,
) -> None:
    monkeypatch.setattr(
        settings_store,
        "get",
        lambda: SimpleNamespace(
            video_localization_development_step_control_enabled=True
        ),
    )
    draft = VideoLocalizationDraft(
        operations=[
            _development_operation(
                "predecessor-op",
                lineage_id=lineage_id,
                step_id=step_id,
            )
        ]
    )

    with pytest.raises(Exception) as exc_info:
        operation_queue._normalized_operation_parameters(
            "english_asr",
            {
                "execution_mode": "development_target",
                "development_source_operation_id": "formal-op",
                "development_predecessor_operation_id": "predecessor-op",
                "development_target_step_id": "whole_recheck_r1",
            },
            draft,
        )

    assert getattr(exc_info.value, "code", "") == (
        "VIDEO_LOCALIZATION_ASR_CONTINUATION_LINEAGE_INVALID"
    )


def test_asr_continuation_keeps_unrelated_draft_edits_out_of_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_store,
        "get",
        lambda: SimpleNamespace(
            video_localization_development_step_control_enabled=True
        ),
    )
    draft = VideoLocalizationDraft(
        operations=[
            _development_operation(
                "review-op",
                lineage_id="formal-op",
                step_id="review_decisions_r1",
            )
        ],
        notes="UI-only note changed while the development chain was running",
    )
    normalized = operation_queue._normalized_operation_parameters(
        "english_asr",
        {
            "execution_mode": "development_target",
            "development_source_operation_id": "formal-op",
            "development_predecessor_operation_id": "review-op",
            "development_target_step_id": "whole_recheck_r1",
        },
        draft,
    )

    assert normalized["development_predecessor_operation_id"] == "review-op"
