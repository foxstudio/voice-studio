from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import managed_local_detail  # noqa: E402
from app.domains.video_localization import (  # noqa: E402
    asr_initial_analysis_detail_reader,
)
from app.services.video_localization_operation_step_store import (  # noqa: E402
    OperationStepAttempt,
)


def _completed_step() -> OperationStepAttempt:
    return OperationStepAttempt(
        step_attempt_id="step-1",
        project_id="project-1",
        operation_id="operation-1",
        operation_attempt_id="attempt-1",
        step_id="asr",
        step_schema_version="asr-step-v1",
        step_attempt_number=1,
        fencing_token=1,
        workflow_version="asr-raw-development-workflow-v1",
        input_fingerprint="a" * 64,
        cost_class="local_free",
        provider_name=None,
        provider_idempotency_key=None,
        provider_request_id=None,
        status="success",
        status_revision=2,
        prepared_at="2026-08-08T15:11:45.000Z",
        completed_at="2026-08-08T15:15:58.000Z",
        output_fingerprint="b" * 64,
    )


def test_managed_local_detail_uses_utc_step_duration_over_mixed_operation_timestamps():
    step = _completed_step()
    state = managed_local_detail.ManagedLocalDetailState(
        project_id="project-1",
        operation_id="operation-1",
        status="success",
        cancel_requested=False,
        created_at="2026-08-08T23:11:44",
        started_at="2026-08-08T15:11:44.857Z",
        completed_at="2026-08-08T23:15:58",
        parameters={},
        steps=(step,),
        result=managed_local_detail.ManagedLocalResult(
            step=step,
            output=object(),
        ),
        error_code=None,
    )

    assert state.task_duration_ms == 253_000


def test_initial_analysis_join_stays_todo_while_parallel_branches_are_running():
    base = _completed_step()
    prepared_steps = tuple(
        OperationStepAttempt(
            **{
                **base.__dict__,
                "step_attempt_id": f"step-{index}",
                "step_id": step_id,
                "status": "prepared",
                "status_revision": 1,
                "completed_at": None,
                "output_fingerprint": None,
            }
        )
        for index, step_id in enumerate(
            ("asr", "diarization", "initial_analysis_join"),
            start=1,
        )
    )
    state = type(
        "InitialAnalysisState",
        (),
        {
            "status": "running",
            "snapshot": None,
            "operation_id": "operation-1",
            "steps": prepared_steps,
            "duration_ms": 1_000,
            "error_code": None,
        },
    )()

    summary = asr_initial_analysis_detail_reader._result_summary(state)

    assert summary["task_step_results"]["asr"]["status"] == "running"
    assert summary["task_step_results"]["diarization"]["status"] == (
        "running"
    )
    assert summary["task_step_results"]["initial_analysis_join"][
        "status"
    ] == "todo"


def test_initial_analysis_duration_uses_canonical_step_timestamps():
    base = _completed_step()
    steps = tuple(
        OperationStepAttempt(
            **{
                **base.__dict__,
                "step_attempt_id": f"step-{index}",
                "step_id": step_id,
                "prepared_at": prepared_at,
                "completed_at": completed_at,
            }
        )
        for index, (
            step_id,
            prepared_at,
            completed_at,
        ) in enumerate(
            (
                (
                    "asr",
                    "2026-08-08T15:20:36.816Z",
                    "2026-08-08T15:27:11.951Z",
                ),
                (
                    "diarization",
                    "2026-08-08T15:20:36.820Z",
                    "2026-08-08T15:28:31.024Z",
                ),
                (
                    "initial_analysis_join",
                    "2026-08-08T15:28:31.067Z",
                    "2026-08-08T15:28:31.233Z",
                ),
            ),
            start=1,
        )
    )
    state = asr_initial_analysis_detail_reader._InitialAnalysisState(
        project_id="project-1",
        operation_id="operation-1",
        status="success",
        cancel_requested=False,
        created_at="2026-08-08T23:20:36",
        started_at="2026-08-08T15:20:36.248Z",
        completed_at="2026-08-08T23:28:31",
        parameters={},
        steps=steps,
        snapshot=None,
        error_code=None,
    )

    assert state.duration_ms == 474_417
    assert state.step_duration_ms("initial_analysis_join") == 166


def test_multi_step_duration_uses_full_canonical_step_span():
    first = _completed_step()
    second = OperationStepAttempt(
        **{
            **first.__dict__,
            "step_attempt_id": "step-2",
            "step_id": "finalize",
            "prepared_at": "2026-08-08T15:20:00.000Z",
            "completed_at": "2026-08-08T15:21:05.500Z",
        }
    )

    assert managed_local_detail.workflow_duration_ms(
        (first, second),
        started_at="2026-08-08T15:11:44.857Z",
        completed_at="2026-08-08T23:21:06",
    ) == 560_500


def test_apply_workflow_duration_keeps_operation_and_dev_stage_in_sync():
    summary = {
        "stage_id": "section_review_r1",
        "task_duration_ms": 999_999,
        "task_stage_timings": {
            "section_review_r1": {"duration_ms": 999_999}
        },
    }

    duration_ms = managed_local_detail.apply_workflow_duration(
        summary,
        (_completed_step(),),
        started_at="2026-08-08T23:11:44",
        completed_at="2026-08-08T23:15:58",
    )

    assert duration_ms == 253_000
    assert summary["task_duration_ms"] == 253_000
    assert summary["task_stage_timings"]["section_review_r1"][
        "duration_ms"
    ] == 253_000
