from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.video_localization_operation_artifact_storage import (  # noqa: E402
    ArtifactStorageKeys,
    ManagedArtifactStorageIntegrityError,
)
from app.services import database  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_store as step_store,
)
from app.services import (  # noqa: E402
    video_localization_provider_step_lifecycle as lifecycle,
)


UTC = timezone.utc
T0 = datetime(2026, 7, 31, 3, 0, tzinfo=UTC)
LEASE = timedelta(seconds=30)


class FakeArtifactBackend:
    ARTIFACT_STORAGE_BACKEND = "project_package"

    def __init__(self) -> None:
        self.files: dict[tuple[str, str], bytes] = {}
        self.fail_write = False

    def build_storage_keys(
        self,
        *,
        operation_id: str,
        step_attempt_id: str,
        artifact_kind: str,
        artifact_key: str,
        artifact_id: str,
        media_type: str,
    ) -> ArtifactStorageKeys:
        del media_type
        prefix = (
            f"artifacts/{operation_id}/{step_attempt_id}/"
            f"{artifact_kind}/{artifact_key}-{artifact_id}"
        )
        return ArtifactStorageKeys(
            storage_key=f"{prefix}.json",
            staging_key=f".staging/{artifact_id}.part",
        )

    def write_staging_file(
        self,
        project_id: str,
        staging_key: str,
        content: bytes,
    ) -> None:
        if self.fail_write:
            raise ManagedArtifactStorageIntegrityError(
                "injected staging failure"
            )
        identity = (project_id, staging_key)
        if identity in self.files:
            raise ManagedArtifactStorageIntegrityError(
                "staging file already exists"
            )
        self.files[identity] = content

    def commit_staging_file(
        self,
        project_id: str,
        *,
        staging_key: str,
        storage_key: str,
        expected_size: int,
        expected_fingerprint: str,
    ) -> None:
        staging = (project_id, staging_key)
        final = (project_id, storage_key)
        if final in self.files:
            self._verify(
                self.files[final],
                expected_size,
                expected_fingerprint,
            )
            self.files.pop(staging, None)
            return
        content = self.files.pop(staging)
        self._verify(
            content,
            expected_size,
            expected_fingerprint,
        )
        self.files[final] = content

    def read_verified_file(
        self,
        project_id: str,
        storage_key: str,
        *,
        expected_size: int,
        expected_fingerprint: str,
    ) -> bytes:
        content = self.files[(project_id, storage_key)]
        self._verify(
            content,
            expected_size,
            expected_fingerprint,
        )
        return content

    def remove_staging_file(
        self,
        project_id: str,
        staging_key: str,
    ) -> None:
        self.files.pop((project_id, staging_key), None)

    @staticmethod
    def content_fingerprint(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _verify(
        content: bytes,
        expected_size: int,
        expected_fingerprint: str,
    ) -> None:
        if (
            len(content) != expected_size
            or hashlib.sha256(content).hexdigest()
            != expected_fingerprint
        ):
            raise ManagedArtifactStorageIntegrityError(
                "artifact content mismatch"
            )


def _insert_operation() -> None:
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
                'project-1',
                'operation-1',
                'semantic_tts_grouping',
                'running',
                'parameters-v1',
                'workflow-v1',
                'command',
                ?,
                ?
            )
            """,
            (T0.isoformat(), T0.isoformat()),
        )


def _claim(
    tmp_path: Path,
    *,
    runner_id: str = "runner-1",
    observed_at: datetime = T0,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    if not attempt_store.list_attempts("project-1", "operation-1"):
        _insert_operation()
    decision = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id=runner_id,
        observed_at=observed_at,
        lease_duration=LEASE,
    )
    assert decision.acquired is True
    assert decision.attempt.execution_fence is not None
    return decision.attempt.execution_fence


def _plan(
    *,
    provider_key: str = "provider-key-1",
    input_fingerprint: str = "a" * 64,
) -> lifecycle.ProviderStepPlan:
    return lifecycle.ProviderStepPlan(
        step_id="semantic_grouping_round_1",
        workflow_version="workflow-v1",
        input_fingerprint=input_fingerprint,
        cost_class="external_paid",
        provider_name="openai-compatible",
        provider_idempotency_key=provider_key,
        artifact_kind="step-result",
        artifact_key="primary",
        payload_schema_version="semantic-grouping-round-v1",
        media_type="application/json",
    )


def _content() -> bytes:
    return (
        b'{"groups":[["subtitle-1"]],'
        b'"schema_version":"semantic-grouping-round-v1"}'
    )


def test_submission_is_durable_before_provider_and_success_is_reusable(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    provider_calls: list[str] = []

    def submit(idempotency_key: str) -> lifecycle.ProviderResponse:
        current = step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        assert current[-1].status == "submitted"
        assert current[-1].provider_idempotency_key == idempotency_key
        provider_calls.append(idempotency_key)
        return lifecycle.ProviderResponse(
            content=_content(),
            provider_request_id="request-1",
        )

    first = lifecycle.run_provider_step(
        fence,
        file_backend=backend,
        plan=_plan(),
        submit=submit,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    repeated = lifecycle.run_provider_step(
        fence,
        file_backend=backend,
        plan=_plan(),
        submit=submit,
        clock=lambda: T0 + timedelta(seconds=2),
    )

    assert first.outcome == "executed"
    assert repeated.outcome == "reused"
    assert first.step.status == "success"
    assert first.step.provider_request_id == "request-1"
    assert first.step.output_fingerprint == hashlib.sha256(
        _content()
    ).hexdigest()
    assert first.artifact.status == "committed"
    assert repeated.content == _content()
    assert provider_calls == ["provider-key-1"]


def test_new_worker_reuses_successful_prior_attempt_without_provider_call(
    tmp_path: Path,
):
    first_fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    first = lifecycle.run_provider_step(
        first_fence,
        file_backend=backend,
        plan=_plan(provider_key="provider-key-1"),
        submit=lambda _key: lifecycle.ProviderResponse(
            content=_content(),
            provider_request_id="request-1",
        ),
        clock=lambda: T0 + timedelta(seconds=1),
    )
    second_fence = _claim(
        tmp_path,
        runner_id="runner-2",
        observed_at=T0 + LEASE + timedelta(seconds=1),
    )
    provider_calls: list[str] = []

    repeated = lifecycle.run_provider_step(
        second_fence,
        file_backend=backend,
        plan=_plan(provider_key="provider-key-2"),
        submit=lambda key: (
            provider_calls.append(key)
            or lifecycle.ProviderResponse(content=b"unexpected")
        ),
        clock=lambda: T0 + LEASE + timedelta(seconds=2),
    )

    assert first.outcome == "executed"
    assert repeated.outcome == "reused"
    assert repeated.step.step_attempt_id == first.step.step_attempt_id
    assert repeated.content == _content()
    assert provider_calls == []
    assert len(
        step_store.list_step_attempts("project-1", "operation-1")
    ) == 1


def test_explicit_provider_rejection_is_terminal_without_retry(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()

    def reject(_idempotency_key: str) -> lifecycle.ProviderResponse:
        raise lifecycle.ProviderRequestRejected(
            "PROVIDER_REQUEST_REJECTED"
        )

    with pytest.raises(
        lifecycle.ProviderStepExecutionFailed,
        match="PROVIDER_REQUEST_REJECTED",
    ):
        lifecycle.run_provider_step(
            fence,
            file_backend=backend,
            plan=_plan(),
            submit=reject,
            clock=lambda: T0 + timedelta(seconds=1),
        )

    step = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )[0]
    assert step.status == "failed"
    assert step.error_code == "PROVIDER_REQUEST_REJECTED"
    assert step.provider_request_id is None


def test_uncertain_provider_error_records_request_id_and_unknown(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()

    def disconnect(_idempotency_key: str) -> lifecycle.ProviderResponse:
        raise lifecycle.ProviderResultUncertain(
            "PROVIDER_RESPONSE_INTERRUPTED",
            provider_request_id="request-uncertain",
        )

    with pytest.raises(
        lifecycle.ProviderStepResultUnknown,
        match="PROVIDER_RESPONSE_INTERRUPTED",
    ):
        lifecycle.run_provider_step(
            fence,
            file_backend=backend,
            plan=_plan(),
            submit=disconnect,
            clock=lambda: T0 + timedelta(seconds=1),
        )

    step = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )[0]
    assert step.status == "result_unknown"
    assert step.provider_request_id == "request-uncertain"
    assert step.error_code == "PROVIDER_RESPONSE_INTERRUPTED"


def test_unclassified_exception_after_submission_fails_closed(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()

    def crash(_idempotency_key: str) -> lifecycle.ProviderResponse:
        raise RuntimeError("secret provider detail")

    with pytest.raises(
        lifecycle.ProviderStepResultUnknown,
        match="PROVIDER_RESULT_UNKNOWN",
    ) as exc_info:
        lifecycle.run_provider_step(
            fence,
            file_backend=backend,
            plan=_plan(),
            submit=crash,
            clock=lambda: T0 + timedelta(seconds=1),
        )

    assert "secret provider detail" not in str(exc_info.value)
    step = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )[0]
    assert step.status == "result_unknown"
    assert step.error_code == "PROVIDER_RESULT_UNKNOWN"


def test_artifact_storage_failure_after_response_becomes_unknown(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    backend.fail_write = True

    with pytest.raises(
        lifecycle.ProviderStepResultUnknown,
        match="PROVIDER_RESULT_PERSISTENCE_FAILED",
    ):
        lifecycle.run_provider_step(
            fence,
            file_backend=backend,
            plan=_plan(),
            submit=lambda _key: lifecycle.ProviderResponse(
                content=_content(),
                provider_request_id="request-before-write-failure",
            ),
            clock=lambda: T0 + timedelta(seconds=1),
        )

    step = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )[0]
    assert step.status == "result_unknown"
    assert (
        step.provider_request_id
        == "request-before-write-failure"
    )
    assert step.error_code == "PROVIDER_RESULT_PERSISTENCE_FAILED"


def test_invalid_provider_payload_is_known_terminal_failure(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()

    with pytest.raises(
        lifecycle.ProviderStepExecutionFailed,
        match="PROVIDER_RESPONSE_INVALID",
    ):
        lifecycle.run_provider_step(
            fence,
            file_backend=backend,
            plan=_plan(),
            submit=lambda _key: lifecycle.ProviderResponse(
                content=b"not-json",
                provider_request_id="request-invalid",
            ),
            clock=lambda: T0 + timedelta(seconds=1),
        )

    step = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )[0]
    assert step.status == "failed"
    assert step.provider_request_id == "request-invalid"
    assert step.error_code == "PROVIDER_RESPONSE_INVALID"


def test_provider_is_not_called_if_submitted_write_fails(
    tmp_path: Path,
    monkeypatch,
):
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    called = False

    def fail_submit(*_args, **_kwargs):
        raise RuntimeError("injected durable submit failure")

    def provider(_key: str) -> lifecycle.ProviderResponse:
        nonlocal called
        called = True
        return lifecycle.ProviderResponse(content=_content())

    monkeypatch.setattr(
        step_store,
        "mark_step_submitted",
        fail_submit,
    )
    with pytest.raises(RuntimeError, match="durable submit"):
        lifecycle.run_provider_step(
            fence,
            file_backend=backend,
            plan=_plan(),
            submit=provider,
            clock=lambda: T0 + timedelta(seconds=1),
        )

    assert called is False
    assert step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )[0].status == "prepared"


def test_new_worker_recovers_submitted_step_and_blocks_paid_replay(
    tmp_path: Path,
):
    old_fence = _claim(tmp_path)
    old = step_store.prepare_step(
        old_fence,
        step_id=_plan().step_id,
        workflow_version="workflow-v1",
        input_fingerprint="a" * 64,
        cost_class="external_paid",
        provider_name="openai-compatible",
        provider_idempotency_key="old-provider-key",
        observed_at=T0 + timedelta(seconds=1),
    ).step
    step_store.mark_step_submitted(
        old.step_attempt_id,
        execution_fence=old_fence,
        observed_at=T0 + timedelta(seconds=2),
    )
    new_fence = _claim(
        tmp_path,
        runner_id="runner-2",
        observed_at=T0 + timedelta(seconds=31),
    )
    called = False

    def provider(_key: str) -> lifecycle.ProviderResponse:
        nonlocal called
        called = True
        return lifecycle.ProviderResponse(content=_content())

    with pytest.raises(
        lifecycle.ProviderReplayBlocked,
        match="result is unresolved",
    ):
        lifecycle.run_provider_step(
            new_fence,
            file_backend=FakeArtifactBackend(),
            plan=_plan(provider_key="new-provider-key"),
            submit=provider,
            clock=lambda: T0 + timedelta(seconds=32),
        )

    recovered = step_store.get_step_attempt(old.step_attempt_id)
    assert recovered is not None
    assert recovered.status == "result_unknown"
    assert recovered.error_code == "PROVIDER_EXECUTION_INTERRUPTED"
    assert called is False
    assert len(
        step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
    ) == 1


def test_direct_prepare_blocks_cross_attempt_unknown_paid_input(
    tmp_path: Path,
):
    old_fence = _claim(tmp_path)
    old = step_store.prepare_step(
        old_fence,
        step_id=_plan().step_id,
        workflow_version="workflow-v1",
        input_fingerprint="a" * 64,
        cost_class="external_paid",
        provider_name="openai-compatible",
        provider_idempotency_key="old-provider-key",
        observed_at=T0 + timedelta(seconds=1),
    ).step
    step_store.mark_step_submitted(
        old.step_attempt_id,
        execution_fence=old_fence,
        observed_at=T0 + timedelta(seconds=2),
    )
    step_store.mark_step_result_unknown(
        old.step_attempt_id,
        execution_fence=old_fence,
        observed_at=T0 + timedelta(seconds=3),
        error_code="PROVIDER_RESULT_UNKNOWN",
    )
    new_fence = _claim(
        tmp_path,
        runner_id="runner-2",
        observed_at=T0 + timedelta(seconds=31),
    )

    with pytest.raises(
        step_store.UnresolvedProviderResult,
        match="unresolved",
    ):
        step_store.prepare_step(
            new_fence,
            step_id=_plan().step_id,
            workflow_version="workflow-v1",
            input_fingerprint="a" * 64,
            cost_class="external_paid",
            provider_name="openai-compatible",
            provider_idempotency_key="new-provider-key",
            observed_at=T0 + timedelta(seconds=32),
        )


def test_same_worker_submitted_reentry_is_blocked_without_provider_call(
    tmp_path: Path,
):
    fence = _claim(tmp_path)
    prepared = step_store.prepare_step(
        fence,
        step_id=_plan().step_id,
        workflow_version="workflow-v1",
        input_fingerprint="a" * 64,
        cost_class="external_paid",
        provider_name="openai-compatible",
        provider_idempotency_key="provider-key-1",
        observed_at=T0 + timedelta(seconds=1),
    ).step
    step_store.mark_step_submitted(
        prepared.step_attempt_id,
        execution_fence=fence,
        observed_at=T0 + timedelta(seconds=2),
    )
    called = False

    def provider(_key: str) -> lifecycle.ProviderResponse:
        nonlocal called
        called = True
        return lifecycle.ProviderResponse(content=_content())

    with pytest.raises(lifecycle.ProviderReplayBlocked):
        lifecycle.run_provider_step(
            fence,
            file_backend=FakeArtifactBackend(),
            plan=_plan(),
            submit=provider,
            clock=lambda: T0 + timedelta(seconds=3),
        )

    assert called is False
    stored = step_store.get_step_attempt(prepared.step_attempt_id)
    assert stored is not None
    assert stored.status == "submitted"


def test_changed_paid_input_is_not_blocked_by_old_unknown_result(
    tmp_path: Path,
):
    old_fence = _claim(tmp_path)
    old = step_store.prepare_step(
        old_fence,
        step_id=_plan().step_id,
        workflow_version="workflow-v1",
        input_fingerprint="a" * 64,
        cost_class="external_paid",
        provider_name="openai-compatible",
        provider_idempotency_key="old-provider-key",
        observed_at=T0 + timedelta(seconds=1),
    ).step
    step_store.mark_step_submitted(
        old.step_attempt_id,
        execution_fence=old_fence,
        observed_at=T0 + timedelta(seconds=2),
    )
    new_fence = _claim(
        tmp_path,
        runner_id="runner-2",
        observed_at=T0 + timedelta(seconds=31),
    )

    result = lifecycle.run_provider_step(
        new_fence,
        file_backend=FakeArtifactBackend(),
        plan=_plan(
            provider_key="new-provider-key",
            input_fingerprint="b" * 64,
        ),
        submit=lambda _key: lifecycle.ProviderResponse(
            content=_content()
        ),
        clock=lambda: T0 + timedelta(seconds=32),
    )

    recovered = step_store.get_step_attempt(old.step_attempt_id)
    assert recovered is not None
    assert recovered.status == "result_unknown"
    assert result.step.status == "success"
    assert result.step.input_fingerprint == "b" * 64


def test_interrupted_recovery_rolls_back_if_validation_fails(
    tmp_path: Path,
    monkeypatch,
):
    old_fence = _claim(tmp_path)
    old = step_store.prepare_step(
        old_fence,
        step_id=_plan().step_id,
        workflow_version="workflow-v1",
        input_fingerprint="a" * 64,
        cost_class="external_paid",
        provider_name="openai-compatible",
        provider_idempotency_key="old-provider-key",
        observed_at=T0 + timedelta(seconds=1),
    ).step
    step_store.mark_step_submitted(
        old.step_attempt_id,
        execution_fence=old_fence,
        observed_at=T0 + timedelta(seconds=2),
    )
    new_fence = _claim(
        tmp_path,
        runner_id="runner-2",
        observed_at=T0 + timedelta(seconds=31),
    )
    original_read = step_store._read_step

    def fail_read(*_args, **_kwargs):
        raise RuntimeError("injected recovery validation failure")

    monkeypatch.setattr(step_store, "_read_step", fail_read)
    with pytest.raises(RuntimeError, match="recovery validation"):
        step_store.recover_interrupted_provider_steps(
            new_fence,
            observed_at=T0 + timedelta(seconds=32),
        )
    monkeypatch.setattr(step_store, "_read_step", original_read)

    stored = step_store.get_step_attempt(old.step_attempt_id)
    assert stored is not None
    assert stored.status == "submitted"
    assert stored.status_revision == 2


def test_old_prepared_step_does_not_block_safe_new_attempt(
    tmp_path: Path,
):
    old_fence = _claim(tmp_path)
    step_store.prepare_step(
        old_fence,
        step_id=_plan().step_id,
        workflow_version="workflow-v1",
        input_fingerprint="a" * 64,
        cost_class="external_paid",
        provider_name="openai-compatible",
        provider_idempotency_key="never-submitted-key",
        observed_at=T0 + timedelta(seconds=1),
    )
    new_fence = _claim(
        tmp_path,
        runner_id="runner-2",
        observed_at=T0 + timedelta(seconds=31),
    )
    calls: list[str] = []

    result = lifecycle.run_provider_step(
        new_fence,
        file_backend=FakeArtifactBackend(),
        plan=_plan(provider_key="safe-new-key"),
        submit=lambda key: (
            calls.append(key)
            or lifecycle.ProviderResponse(content=_content())
        ),
        clock=lambda: T0 + timedelta(seconds=32),
    )

    assert result.outcome == "executed"
    assert calls == ["safe-new-key"]
    assert [
        item.status
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
    ] == ["prepared", "success"]


def test_unresolved_lookup_uses_existing_project_step_index(
    tmp_path: Path,
):
    _claim(tmp_path)

    with database.conn() as connection:
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT *
            FROM video_localization_operation_step_attempts
            WHERE project_id = ?
              AND operation_id = ?
              AND step_id = ?
              AND workflow_version = ?
              AND input_fingerprint = ?
              AND cost_class = 'external_paid'
              AND operation_attempt_id != ?
              AND status IN ('submitted', 'result_unknown')
            ORDER BY step_attempt_number DESC
            LIMIT 1
            """,
            (
                "project-1",
                "operation-1",
                _plan().step_id,
                "workflow-v1",
                "a" * 64,
                "attempt-current",
            ),
        ).fetchall()

    assert all("SCAN " not in str(row["detail"]) for row in plan)
    assert any("USING INDEX" in str(row["detail"]) for row in plan)
