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
    video_localization_operation_step_adjudication_store
    as adjudication_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_store as step_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_store as operation_store,
)


OBSERVED_AT = datetime(2026, 7, 31, 3, 0, tzinfo=timezone.utc)
LEASE_DURATION = timedelta(seconds=60)
OUTPUT_FINGERPRINT = "a" * 64


def _insert_operation(
    project_id: str,
    operation_id: str,
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
            ) VALUES (
                ?, ?, 'test', 'running', ?,
                'workflow-v1', 'command', ?, ?
            )
            """,
            (
                project_id,
                operation_id,
                f"parameters-{project_id}-{operation_id}",
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
            ),
        )


def _claim(
    tmp_path: Path,
    *,
    project_id: str = "project-1",
    operation_id: str = "operation-1",
):
    database.set_db_path(tmp_path / "voice_studio.db")
    _insert_operation(project_id, operation_id)
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


def _paid_unknown(
    tmp_path: Path,
    *,
    provider_request_id: str | None = None,
):
    execution_fence = _claim(tmp_path)
    step = step_store.prepare_step(
        execution_fence,
        step_id="paid-completion",
        workflow_version="workflow-v1",
        input_fingerprint="input-v1",
        cost_class="external_paid",
        provider_name="provider-a",
        provider_idempotency_key="provider-key-1",
        observed_at=OBSERVED_AT + timedelta(seconds=1),
    ).step
    step_store.mark_step_submitted(
        step.step_attempt_id,
        execution_fence=execution_fence,
        observed_at=OBSERVED_AT + timedelta(seconds=2),
    )
    if provider_request_id is not None:
        step_store.record_provider_request_id(
            step.step_attempt_id,
            execution_fence=execution_fence,
            provider_request_id=provider_request_id,
            observed_at=OBSERVED_AT + timedelta(seconds=3),
        )
    unknown = step_store.mark_step_result_unknown(
        step.step_attempt_id,
        execution_fence=execution_fence,
        observed_at=OBSERVED_AT + timedelta(seconds=4),
        error_code="PROVIDER_RESULT_UNKNOWN",
    )
    return unknown


def _adjudicate_success(
    step,
    *,
    source: str = "human_review",
    reason_code: str = "HUMAN_CONFIRMED_SUCCESS",
):
    return adjudication_store.adjudicate_result_unknown(
        step.project_id,
        step.operation_id,
        step.step_attempt_id,
        expected_status_revision=step.status_revision,
        decision="success",
        source=source,
        reason_code=reason_code,
        observed_at=OBSERVED_AT + timedelta(seconds=5),
        output_fingerprint=OUTPUT_FINGERPRINT,
    )


def test_adjudication_schema_is_additive_and_bootstrap_is_clean(
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
                    video_localization_operation_step_adjudications
                )
                """
            ).fetchall()
        }

    assert {
        "adjudication_id",
        "adjudication_schema_version",
        "step_attempt_id",
        "project_id",
        "operation_id",
        "decision",
        "source",
        "reason_code",
        "expected_status_revision",
        "resulting_status_revision",
        "provider_request_id",
        "output_fingerprint",
        "error_code",
        "decided_at",
    } <= columns


def test_human_success_adjudication_is_atomic_and_auditable(
    tmp_path: Path,
):
    unknown = _paid_unknown(tmp_path)

    decision = _adjudicate_success(unknown)

    assert decision.outcome == "created"
    assert decision.step.status == "success"
    assert decision.step.status_revision == unknown.status_revision + 1
    assert decision.step.output_fingerprint == OUTPUT_FINGERPRINT
    assert decision.step.error_code is None
    assert decision.adjudication.decision == "success"
    assert decision.adjudication.source == "human_review"
    assert (
        decision.adjudication.expected_status_revision
        == unknown.status_revision
    )
    assert (
        decision.adjudication.resulting_status_revision
        == decision.step.status_revision
    )
    assert decision.adjudication.provider_request_id is None


def test_provider_query_failure_requires_request_id_and_records_it(
    tmp_path: Path,
):
    unknown = _paid_unknown(
        tmp_path,
        provider_request_id="provider-request-1",
    )

    decision = adjudication_store.adjudicate_result_unknown(
        unknown.project_id,
        unknown.operation_id,
        unknown.step_attempt_id,
        expected_status_revision=unknown.status_revision,
        decision="failed",
        source="provider_query",
        reason_code="PROVIDER_CONFIRMED_FAILURE",
        observed_at=OBSERVED_AT + timedelta(seconds=5),
        error_code="PROVIDER_REJECTED_REQUEST",
    )

    assert decision.step.status == "failed"
    assert decision.step.output_fingerprint is None
    assert decision.step.error_code == "PROVIDER_REJECTED_REQUEST"
    assert (
        decision.adjudication.provider_request_id
        == "provider-request-1"
    )


def test_provider_query_without_request_id_is_rejected_without_write(
    tmp_path: Path,
):
    unknown = _paid_unknown(tmp_path)

    with pytest.raises(
        adjudication_store.AdjudicationConflict,
        match="request ID",
    ):
        adjudication_store.adjudicate_result_unknown(
            unknown.project_id,
            unknown.operation_id,
            unknown.step_attempt_id,
            expected_status_revision=unknown.status_revision,
            decision="failed",
            source="provider_query",
            reason_code="PROVIDER_CONFIRMED_FAILURE",
            observed_at=OBSERVED_AT + timedelta(seconds=5),
            error_code="PROVIDER_REJECTED_REQUEST",
        )

    assert (
        adjudication_store.get_adjudication(
            unknown.step_attempt_id
        )
        is None
    )
    assert (
        step_store.get_step_attempt(unknown.step_attempt_id)
        == unknown
    )


def test_adjudication_is_idempotent_and_conflicting_retry_fails(
    tmp_path: Path,
):
    unknown = _paid_unknown(tmp_path)

    created = _adjudicate_success(unknown)
    reused = _adjudicate_success(unknown)

    assert reused.outcome == "reused"
    assert reused.adjudication == created.adjudication
    assert reused.step == created.step
    with pytest.raises(
        adjudication_store.AdjudicationConflict,
        match="different",
    ):
        adjudication_store.adjudicate_result_unknown(
            unknown.project_id,
            unknown.operation_id,
            unknown.step_attempt_id,
            expected_status_revision=unknown.status_revision,
            decision="failed",
            source="human_review",
            reason_code="HUMAN_CONFIRMED_FAILURE",
            observed_at=OBSERVED_AT + timedelta(seconds=5),
            error_code="HUMAN_REJECTED_RESULT",
        )


def test_adjudication_rejects_stale_revision_and_invalid_shape(
    tmp_path: Path,
):
    unknown = _paid_unknown(tmp_path)

    with pytest.raises(
        adjudication_store.AdjudicationConflict,
        match="revision",
    ):
        adjudication_store.adjudicate_result_unknown(
            unknown.project_id,
            unknown.operation_id,
            unknown.step_attempt_id,
            expected_status_revision=unknown.status_revision - 1,
            decision="success",
            source="human_review",
            reason_code="HUMAN_CONFIRMED_SUCCESS",
            observed_at=OBSERVED_AT + timedelta(seconds=5),
            output_fingerprint=OUTPUT_FINGERPRINT,
        )
    with pytest.raises(ValueError, match="fingerprint"):
        adjudication_store.adjudicate_result_unknown(
            unknown.project_id,
            unknown.operation_id,
            unknown.step_attempt_id,
            expected_status_revision=unknown.status_revision,
            decision="success",
            source="human_review",
            reason_code="HUMAN_CONFIRMED_SUCCESS",
            observed_at=OBSERVED_AT + timedelta(seconds=5),
            output_fingerprint="not-a-sha",
        )
    with pytest.raises(ValueError, match="reason code"):
        adjudication_store.adjudicate_result_unknown(
            unknown.project_id,
            unknown.operation_id,
            unknown.step_attempt_id,
            expected_status_revision=unknown.status_revision,
            decision="failed",
            source="human_review",
            reason_code="free form reason",
            observed_at=OBSERVED_AT + timedelta(seconds=5),
            error_code="HUMAN_REJECTED_RESULT",
        )

    assert (
        adjudication_store.get_adjudication(
            unknown.step_attempt_id
        )
        is None
    )


def test_only_result_unknown_step_with_matching_identity_can_be_adjudicated(
    tmp_path: Path,
):
    execution_fence = _claim(tmp_path)
    prepared = step_store.prepare_step(
        execution_fence,
        step_id="paid-completion",
        workflow_version="workflow-v1",
        input_fingerprint="input-v1",
        cost_class="external_paid",
        provider_name="provider-a",
        provider_idempotency_key="provider-key-1",
        observed_at=OBSERVED_AT + timedelta(seconds=1),
    ).step

    with pytest.raises(
        adjudication_store.AdjudicationConflict,
        match="result_unknown",
    ):
        _adjudicate_success(prepared)
    with pytest.raises(
        adjudication_store.AdjudicationConflict,
        match="identity",
    ):
        adjudication_store.adjudicate_result_unknown(
            "other-project",
            prepared.operation_id,
            prepared.step_attempt_id,
            expected_status_revision=prepared.status_revision,
            decision="success",
            source="human_review",
            reason_code="HUMAN_CONFIRMED_SUCCESS",
            observed_at=OBSERVED_AT + timedelta(seconds=5),
            output_fingerprint=OUTPUT_FINGERPRINT,
        )


def test_concurrent_identical_adjudication_creates_one_record(
    tmp_path: Path,
):
    unknown = _paid_unknown(tmp_path)
    release = threading.Event()
    decisions: list[
        adjudication_store.StepAdjudicationDecision
    ] = []
    errors: list[BaseException] = []

    def adjudicate() -> None:
        release.wait(timeout=1)
        try:
            decisions.append(_adjudicate_success(unknown))
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=adjudicate)
        for _ in range(2)
    ]
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
        adjudication_store.list_operation_adjudications(
            unknown.project_id,
            unknown.operation_id,
        )
    ) == 1


def test_adjudication_rolls_back_step_if_record_read_fails(
    tmp_path: Path,
    monkeypatch,
):
    unknown = _paid_unknown(tmp_path)

    def fail_after_insert(*_args, **_kwargs):
        raise RuntimeError("injected adjudication read failure")

    monkeypatch.setattr(
        adjudication_store,
        "_read_adjudication",
        fail_after_insert,
    )
    with pytest.raises(RuntimeError, match="injected"):
        _adjudicate_success(unknown)

    assert (
        step_store.get_step_attempt(unknown.step_attempt_id)
        == unknown
    )
    with database.conn() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM video_localization_operation_step_adjudications
            """
        ).fetchone()["count"] == 0


def test_adjudication_waits_for_short_database_writer(
    tmp_path: Path,
):
    unknown = _paid_unknown(tmp_path)
    blocker = sqlite3.connect(database.DB_PATH)
    blocker.execute("BEGIN IMMEDIATE")
    started = threading.Event()
    finished = threading.Event()
    decisions: list[
        adjudication_store.StepAdjudicationDecision
    ] = []
    errors: list[BaseException] = []

    def adjudicate() -> None:
        started.set()
        try:
            decisions.append(_adjudicate_success(unknown))
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=adjudicate)
    thread.start()
    assert started.wait(timeout=1)
    assert not finished.wait(timeout=0.05)

    blocker.commit()
    blocker.close()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert errors == []
    assert decisions[0].outcome == "created"


def test_adjudication_survives_restart_and_rejects_future_schema(
    tmp_path: Path,
):
    unknown = _paid_unknown(tmp_path)
    created = _adjudicate_success(unknown)
    db_path = database.DB_PATH

    database.set_db_path(db_path)
    assert (
        adjudication_store.get_adjudication(
            unknown.step_attempt_id
        )
        == created.adjudication
    )
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_step_adjudications
            SET adjudication_schema_version = 'future-v9'
            WHERE step_attempt_id = ?
            """,
            (unknown.step_attempt_id,),
        )

    with pytest.raises(
        adjudication_store.AdjudicationSchemaError,
        match="unsupported",
    ):
        adjudication_store.get_adjudication(
            unknown.step_attempt_id
        )


def test_idempotent_retry_rejects_terminal_step_drift(
    tmp_path: Path,
):
    unknown = _paid_unknown(tmp_path)
    _adjudicate_success(unknown)
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_step_attempts
            SET output_fingerprint = ?
            WHERE step_attempt_id = ?
            """,
            ("b" * 64, unknown.step_attempt_id),
        )

    with pytest.raises(
        adjudication_store.AdjudicationSchemaError,
        match="inconsistent",
    ):
        _adjudicate_success(unknown)


def test_project_delete_cleans_adjudication_before_step(
    tmp_path: Path,
):
    unknown = _paid_unknown(tmp_path)
    _adjudicate_success(unknown)

    operation_store.delete_project_with_projection(
        unknown.project_id
    )

    with database.conn() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM video_localization_operation_step_adjudications
            WHERE project_id = ?
            """,
            (unknown.project_id,),
        ).fetchone()["count"] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM video_localization_operation_step_attempts
            WHERE project_id = ?
            """,
            (unknown.project_id,),
        ).fetchone()["count"] == 0


def test_operation_adjudication_list_uses_composite_index(
    tmp_path: Path,
):
    unknown = _paid_unknown(tmp_path)
    _adjudicate_success(unknown)

    with database.conn() as connection:
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT *
            FROM video_localization_operation_step_adjudications
            WHERE project_id = ?
              AND operation_id = ?
            ORDER BY decided_at, adjudication_id
            """,
            (unknown.project_id, unknown.operation_id),
        ).fetchall()

    detail = " ".join(str(row["detail"]) for row in plan)
    assert "USING INDEX" in detail
    assert "project_id=? AND operation_id=?" in detail
