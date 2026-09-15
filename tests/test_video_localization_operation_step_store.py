from __future__ import annotations

import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import database  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_store as step_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_store as operation_store,
)
from app.services.video_localization_execution_fence import (  # noqa: E402
    ExecutionFenceLost,
    ExecutionOperationCancelled,
)


OBSERVED_AT = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)
LEASE_DURATION = timedelta(seconds=60)


def _insert_operation(
    project_id: str,
    operation_id: str,
    *,
    workflow_version: str = "workflow-v1",
) -> None:
    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO video_localization_operations (
                project_id,
                operation_id,
                kind,
                status,
                parameters_fingerprint,
                workflow_version,
                origin,
                created_at,
                updated_at
            ) VALUES (?, ?, 'test', 'running', ?, ?, 'command', ?, ?)
            """,
            (
                project_id,
                operation_id,
                f"parameters-{project_id}-{operation_id}",
                workflow_version,
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
            ),
        )


def _claim(
    tmp_path: Path,
    *,
    project_id: str = "project-1",
    operation_id: str = "operation-1",
    workflow_version: str = "workflow-v1",
):
    database.set_db_path(tmp_path / "voice_studio.db")
    _insert_operation(
        project_id,
        operation_id,
        workflow_version=workflow_version,
    )
    decision = attempt_store.claim_attempt(
        project_id,
        operation_id,
        runner_id=f"runner-{project_id}",
        observed_at=OBSERVED_AT,
        lease_duration=LEASE_DURATION,
    )
    assert decision.acquired is True
    assert decision.attempt.execution_fence is not None
    return decision.attempt.execution_fence


def _prepare_local(
    execution_fence,
    *,
    input_fingerprint: str = "input-v1",
    step_id: str = "normalize",
):
    return step_store.prepare_step(
        execution_fence,
        step_id=step_id,
        workflow_version="workflow-v1",
        input_fingerprint=input_fingerprint,
        cost_class="local_free",
        observed_at=OBSERVED_AT + timedelta(seconds=1),
    )


def test_step_schema_is_additive_and_bootstrap_leaves_no_transaction(
    tmp_path: Path,
):
    db_path = tmp_path / "existing.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE existing_data (value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO existing_data (value) VALUES ('preserved')"
        )
    database.set_db_path(db_path)

    with database.conn() as connection:
        assert connection.in_transaction is False
        assert connection.execute(
            "SELECT value FROM existing_data"
        ).fetchone()["value"] == "preserved"
        columns = {
            row["name"]
            for row in connection.execute(
                """
                PRAGMA table_info(
                    video_localization_operation_step_attempts
                )
                """
            ).fetchall()
        }

    assert {
        "step_attempt_id",
        "project_id",
        "operation_id",
        "operation_attempt_id",
        "step_id",
        "step_schema_version",
        "step_attempt_number",
        "fencing_token",
        "workflow_version",
        "input_fingerprint",
        "cost_class",
        "provider_name",
        "provider_idempotency_key",
        "provider_request_id",
        "status",
        "status_revision",
        "prepared_at",
        "submitted_at",
        "result_unknown_at",
        "completed_at",
        "output_fingerprint",
        "error_code",
    } <= columns


def test_prepare_step_reuses_same_input_and_numbers_changed_input(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)

    created = _prepare_local(execution_fence)
    reused = _prepare_local(execution_fence)
    changed = _prepare_local(
        execution_fence,
        input_fingerprint="input-v2",
    )

    assert created.outcome == "created"
    assert reused.outcome == "reused"
    assert reused.step == created.step
    assert created.step.step_attempt_number == 1
    assert changed.step.step_attempt_number == 2
    assert (
        created.step.step_schema_version
        == step_store.STEP_ATTEMPT_SCHEMA_VERSION
    )
    assert [
        item.step_attempt_id
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
    ] == [
        created.step.step_attempt_id,
        changed.step.step_attempt_id,
    ]


def test_project_scoped_clone_can_reuse_operation_and_step_ids(
    tmp_path: Path,
):
    first_fence = _claim(
        tmp_path,
        project_id="project-a",
        operation_id="shared-operation",
    )
    _insert_operation("project-b", "shared-operation")
    second_decision = attempt_store.claim_attempt(
        "project-b",
        "shared-operation",
        runner_id="runner-project-b",
        observed_at=OBSERVED_AT,
        lease_duration=LEASE_DURATION,
    )
    assert second_decision.attempt.execution_fence is not None

    first = _prepare_local(first_fence)
    second = _prepare_local(second_decision.attempt.execution_fence)

    assert first.step.step_attempt_number == 1
    assert second.step.step_attempt_number == 1
    assert first.step.step_attempt_id != second.step.step_attempt_id


def test_paid_step_requires_provider_identity_and_global_idempotency_key(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)

    with pytest.raises(ValueError, match="provider"):
        step_store.prepare_step(
            execution_fence,
            step_id="translate",
            workflow_version="workflow-v1",
            input_fingerprint="input-v1",
            cost_class="external_paid",
            observed_at=OBSERVED_AT + timedelta(seconds=1),
        )

    first = step_store.prepare_step(
        execution_fence,
        step_id="translate",
        workflow_version="workflow-v1",
        input_fingerprint="input-v1",
        cost_class="external_paid",
        provider_name="provider-a",
        provider_idempotency_key="provider-key-1",
        observed_at=OBSERVED_AT + timedelta(seconds=1),
    )
    _insert_operation("project-2", "operation-2")
    other_decision = attempt_store.claim_attempt(
        "project-2",
        "operation-2",
        runner_id="runner-2",
        observed_at=OBSERVED_AT,
        lease_duration=LEASE_DURATION,
    )
    assert other_decision.attempt.execution_fence is not None

    with pytest.raises(
        step_store.ProviderIdempotencyConflict,
        match="idempotency",
    ):
        step_store.prepare_step(
            other_decision.attempt.execution_fence,
            step_id="translate",
            workflow_version="workflow-v1",
            input_fingerprint="different-input",
            cost_class="external_paid",
            provider_name="provider-a",
            provider_idempotency_key="provider-key-1",
            observed_at=OBSERVED_AT + timedelta(seconds=1),
        )

    assert first.step.provider_idempotency_key == "provider-key-1"


def test_provider_request_id_is_write_once_and_unknown_cannot_finish(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)
    prepared = step_store.prepare_step(
        execution_fence,
        step_id="translate",
        workflow_version="workflow-v1",
        input_fingerprint="input-v1",
        cost_class="external_paid",
        provider_name="provider-a",
        provider_idempotency_key="provider-key-1",
        observed_at=OBSERVED_AT + timedelta(seconds=1),
    ).step

    submitted = step_store.mark_step_submitted(
        prepared.step_attempt_id,
        execution_fence=execution_fence,
        observed_at=OBSERVED_AT + timedelta(seconds=2),
    )
    with_request_id = step_store.record_provider_request_id(
        prepared.step_attempt_id,
        execution_fence=execution_fence,
        provider_request_id="request-1",
        observed_at=OBSERVED_AT + timedelta(seconds=3),
    )
    same_request_id = step_store.record_provider_request_id(
        prepared.step_attempt_id,
        execution_fence=execution_fence,
        provider_request_id="request-1",
        observed_at=OBSERVED_AT + timedelta(seconds=4),
    )
    with pytest.raises(
        step_store.StepTransitionConflict,
        match="request ID",
    ):
        step_store.record_provider_request_id(
            prepared.step_attempt_id,
            execution_fence=execution_fence,
            provider_request_id="request-2",
            observed_at=OBSERVED_AT + timedelta(seconds=4),
        )

    unknown = step_store.mark_step_result_unknown(
        prepared.step_attempt_id,
        execution_fence=execution_fence,
        observed_at=OBSERVED_AT + timedelta(seconds=5),
        error_code="PROVIDER_RESPONSE_INCOMPLETE",
    )
    with pytest.raises(
        step_store.StepTransitionConflict,
        match="result_unknown",
    ):
        step_store.finish_step(
            prepared.step_attempt_id,
            execution_fence=execution_fence,
            status="success",
            observed_at=OBSERVED_AT + timedelta(seconds=6),
            output_fingerprint="output-v1",
        )

    assert submitted.status == "submitted"
    assert with_request_id.provider_request_id == "request-1"
    assert same_request_id == with_request_id
    assert unknown.status == "result_unknown"
    assert unknown.error_code == "PROVIDER_RESPONSE_INCOMPLETE"


def test_non_local_step_must_be_submitted_before_success(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)
    prepared = step_store.prepare_step(
        execution_fence,
        step_id="lookup",
        workflow_version="workflow-v1",
        input_fingerprint="input-v1",
        cost_class="external_free",
        provider_name="provider-a",
        provider_idempotency_key="lookup-key-1",
        observed_at=OBSERVED_AT + timedelta(seconds=1),
    ).step

    with pytest.raises(
        step_store.StepTransitionConflict,
        match="submitted",
    ):
        step_store.finish_step(
            prepared.step_attempt_id,
            execution_fence=execution_fence,
            status="success",
            observed_at=OBSERVED_AT + timedelta(seconds=2),
            output_fingerprint="output-v1",
        )

    step_store.mark_step_submitted(
        prepared.step_attempt_id,
        execution_fence=execution_fence,
        observed_at=OBSERVED_AT + timedelta(seconds=2),
    )
    completed = step_store.finish_step(
        prepared.step_attempt_id,
        execution_fence=execution_fence,
        status="success",
        observed_at=OBSERVED_AT + timedelta(seconds=3),
        output_fingerprint="output-v1",
    )

    assert completed.status == "success"
    assert completed.status_revision == 3
    assert completed.output_fingerprint == "output-v1"


def test_expired_or_cancelled_fence_cannot_write_step(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)

    with pytest.raises(ExecutionFenceLost):
        step_store.prepare_step(
            execution_fence,
            step_id="expired",
            workflow_version="workflow-v1",
            input_fingerprint="input-v1",
            cost_class="local_free",
            observed_at=OBSERVED_AT + LEASE_DURATION,
        )

    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operations
            SET cancel_requested = 1
            WHERE project_id = ?
              AND operation_id = ?
            """,
            (
                execution_fence.project_id,
                execution_fence.operation_id,
            ),
        )

    with pytest.raises(ExecutionOperationCancelled):
        _prepare_local(execution_fence)


def test_prepare_validates_workflow_version_and_timezone(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)

    with pytest.raises(
        step_store.StepIdentityConflict,
        match="workflow version",
    ):
        step_store.prepare_step(
            execution_fence,
            step_id="normalize",
            workflow_version="workflow-v2",
            input_fingerprint="input-v1",
            cost_class="local_free",
            observed_at=OBSERVED_AT + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="timezone"):
        step_store.prepare_step(
            execution_fence,
            step_id="normalize",
            workflow_version="workflow-v1",
            input_fingerprint="input-v1",
            cost_class="local_free",
            observed_at=datetime(2026, 7, 31, 1, 0),
        )


def test_terminal_step_is_idempotent_and_cannot_change_result(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)
    prepared = _prepare_local(execution_fence).step

    completed = step_store.finish_step(
        prepared.step_attempt_id,
        execution_fence=execution_fence,
        status="success",
        observed_at=OBSERVED_AT + timedelta(seconds=2),
        output_fingerprint="output-v1",
    )
    repeated = step_store.finish_step(
        prepared.step_attempt_id,
        execution_fence=execution_fence,
        status="success",
        observed_at=OBSERVED_AT + timedelta(seconds=3),
        output_fingerprint="output-v1",
    )
    with pytest.raises(
        step_store.StepTransitionConflict,
        match="terminal",
    ):
        step_store.finish_step(
            prepared.step_attempt_id,
            execution_fence=execution_fence,
            status="failed",
            observed_at=OBSERVED_AT + timedelta(seconds=3),
            error_code="LATE_FAILURE",
        )

    assert repeated == completed
    assert repeated.status_revision == 2


def test_prepare_rolls_back_if_post_insert_validation_fails(
    tmp_path: Path,
    monkeypatch,
):
    execution_fence = _claim(tmp_path)
    original_read_step = step_store._read_step

    def fail_after_insert(*_args, **_kwargs):
        raise RuntimeError("injected post-insert failure")

    monkeypatch.setattr(step_store, "_read_step", fail_after_insert)
    with pytest.raises(RuntimeError, match="post-insert"):
        _prepare_local(execution_fence)
    monkeypatch.setattr(step_store, "_read_step", original_read_step)

    assert (
        step_store.list_step_attempts("project-1", "operation-1")
        == []
    )


def test_prepare_waits_for_short_competing_database_writer(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)
    blocker = sqlite3.connect(database.DB_PATH)
    blocker.execute("BEGIN IMMEDIATE")
    started = threading.Event()
    finished = threading.Event()
    decisions: list[step_store.StepPrepareDecision] = []
    errors: list[BaseException] = []

    def prepare() -> None:
        started.set()
        try:
            decisions.append(_prepare_local(execution_fence))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=prepare)
    thread.start()
    assert started.wait(timeout=1)
    assert not finished.wait(timeout=0.05)

    blocker.commit()
    blocker.close()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert errors == []
    assert len(decisions) == 1
    assert decisions[0].outcome == "created"


def test_step_row_survives_connection_restart_and_rejects_bad_schema(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)
    prepared = _prepare_local(execution_fence).step
    db_path = database.DB_PATH

    database.set_db_path(db_path)
    assert (
        step_store.get_step_attempt(prepared.step_attempt_id)
        == prepared
    )
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_step_attempts
            SET step_schema_version = 'future-v9'
            WHERE step_attempt_id = ?
            """,
            (prepared.step_attempt_id,),
        )

    with pytest.raises(step_store.StepSchemaError, match="unsupported"):
        step_store.get_step_attempt(prepared.step_attempt_id)


def test_operation_step_list_uses_composite_unique_index(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)
    _prepare_local(execution_fence)

    with database.conn() as connection:
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT *
            FROM video_localization_operation_step_attempts
            WHERE project_id = ?
              AND operation_id = ?
            ORDER BY
                step_id,
                step_attempt_number,
                step_attempt_id
            """,
            ("project-1", "operation-1"),
        ).fetchall()

    detail = " ".join(str(row["detail"]) for row in plan)
    assert "USING INDEX" in detail
    assert "project_id=? AND operation_id=?" in detail


def test_concurrent_prepare_of_same_input_creates_one_row(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)
    release = threading.Event()
    decisions: list[step_store.StepPrepareDecision] = []
    errors: list[BaseException] = []

    def prepare() -> None:
        release.wait(timeout=1)
        try:
            decisions.append(_prepare_local(execution_fence))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=prepare) for _ in range(2)]
    for thread in threads:
        thread.start()
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(item.outcome for item in decisions) == [
        "created",
        "reused",
    ]
    assert len(
        step_store.list_step_attempts("project-1", "operation-1")
    ) == 1


def test_project_delete_cleans_step_attempts_before_operation_ledger(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)
    prepared = _prepare_local(execution_fence).step

    operation_store.delete_project_with_projection("project-1")

    assert step_store.get_step_attempt(prepared.step_attempt_id) is None
    with database.conn() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM video_localization_operation_attempts
            WHERE project_id = 'project-1'
            """
        ).fetchone()["count"] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM video_localization_operations
            WHERE project_id = 'project-1'
            """
        ).fetchone()["count"] == 0
