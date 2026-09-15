from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    managed_artifact_files,
    media_assets,
)
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import database, settings_store  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_adjudication_store as adjudication_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_store as step_store,
)
from app.services import (  # noqa: E402
    video_localization_provider_result_recovery as recovery,
)


UTC = timezone.utc
T0 = datetime(2026, 7, 31, 5, 0, tzinfo=UTC)
LEASE = timedelta(seconds=60)
PAYLOAD_SCHEMA = "recovered-provider-result-v1"
REASON_CODE = "PROVIDER_QUERY_CONFIRMED_SUCCESS"


def _setup_unknown(
    tmp_path: Path,
    *,
    provider_request_id: str | None = "provider-request-1",
):
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            voice_dir=str(tmp_path / "voices"),
            log_dir=str(tmp_path / "logs"),
        )
    )
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
    claim = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        observed_at=T0,
        lease_duration=LEASE,
    ).attempt.execution_fence
    assert claim is not None
    step = step_store.prepare_step(
        claim,
        step_id="semantic_grouping_round_1",
        workflow_version="workflow-v1",
        input_fingerprint="a" * 64,
        cost_class="external_paid",
        provider_name="openai-compatible",
        provider_idempotency_key="provider-key-1",
        observed_at=T0 + timedelta(seconds=1),
    ).step
    step_store.mark_step_submitted(
        step.step_attempt_id,
        execution_fence=claim,
        observed_at=T0 + timedelta(seconds=2),
    )
    if provider_request_id is not None:
        step_store.record_provider_request_id(
            step.step_attempt_id,
            execution_fence=claim,
            provider_request_id=provider_request_id,
            observed_at=T0 + timedelta(seconds=3),
        )
    return step_store.mark_step_result_unknown(
        step.step_attempt_id,
        execution_fence=claim,
        observed_at=T0 + timedelta(seconds=4),
        error_code="PROVIDER_RESPONSE_INTERRUPTED",
    )


def _payload(value: str = "recovered") -> bytes:
    return json.dumps(
        {
            "schema_version": PAYLOAD_SCHEMA,
            "value": value,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _recover(
    step,
    *,
    content: bytes | None = None,
    expected_status_revision: int | None = None,
    payload_schema_version: str = PAYLOAD_SCHEMA,
):
    return recovery.recover_provider_success(
        "project-1",
        "operation-1",
        step.step_attempt_id,
        expected_status_revision=(
            step.status_revision
            if expected_status_revision is None
            else expected_status_revision
        ),
        reason_code=REASON_CODE,
        artifact_kind="step-result",
        artifact_key="primary",
        payload_schema_version=payload_schema_version,
        media_type="application/json",
        content=content if content is not None else _payload(),
        file_backend=managed_artifact_files,
        observed_at=T0 + timedelta(seconds=5),
    )


def test_recovered_success_commits_artifact_and_adjudication(
    tmp_path: Path,
):
    unknown = _setup_unknown(tmp_path)

    result = _recover(unknown)
    read = artifact_store.read_artifact(
        result.artifact.artifact_id,
        file_backend=managed_artifact_files,
    )

    assert result.outcome == "created"
    assert result.step.status == "success"
    assert result.step.output_fingerprint == (
        result.artifact.content_fingerprint
    )
    assert result.artifact.status == "committed"
    assert result.adjudication.decision == "success"
    assert result.adjudication.source == "provider_query"
    assert result.adjudication.reason_code == REASON_CODE
    assert result.adjudication.output_fingerprint == (
        result.artifact.content_fingerprint
    )
    assert read.content == _payload()


def test_same_recovery_command_is_idempotent_and_reads_artifact(
    tmp_path: Path,
):
    unknown = _setup_unknown(tmp_path)

    first = _recover(unknown)
    repeated = _recover(unknown)

    assert first.outcome == "created"
    assert repeated.outcome == "reused"
    assert repeated.artifact == first.artifact
    assert repeated.adjudication == first.adjudication
    assert repeated.step == first.step
    assert repeated.content == _payload()
    with database.read_conn() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM video_localization_operation_artifacts
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM video_localization_operation_step_adjudications
            """
        ).fetchone()[0] == 1


def test_changed_recovery_content_conflicts_with_completed_command(
    tmp_path: Path,
):
    unknown = _setup_unknown(tmp_path)
    _recover(unknown)

    with pytest.raises(
        recovery.ProviderRecoveryConflict,
        match="different",
    ):
        _recover(unknown, content=_payload("different"))


def test_completed_recovery_rejects_different_artifact_contract(
    tmp_path: Path,
):
    unknown = _setup_unknown(tmp_path)
    _recover(unknown)

    with pytest.raises(
        recovery.ProviderRecoveryIntegrityError,
        match="artifact",
    ):
        _recover(
            unknown,
            payload_schema_version="different-result-v1",
        )


def test_provider_query_without_request_id_writes_nothing(
    tmp_path: Path,
):
    unknown = _setup_unknown(
        tmp_path,
        provider_request_id=None,
    )

    with pytest.raises(
        recovery.ProviderRecoveryConflict,
        match="request ID",
    ):
        _recover(unknown)

    with database.read_conn() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM video_localization_operation_artifacts
            """
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM video_localization_operation_step_adjudications
            """
        ).fetchone()[0] == 0
    stored = step_store.get_step_attempt(unknown.step_attempt_id)
    assert stored is not None
    assert stored.status == "result_unknown"


def test_stale_revision_writes_no_file_or_metadata(
    tmp_path: Path,
):
    unknown = _setup_unknown(tmp_path)

    with pytest.raises(
        recovery.ProviderRecoveryConflict,
        match="revision",
    ):
        _recover(
            unknown,
            expected_status_revision=unknown.status_revision - 1,
        )

    project_root = media_assets.project_video_localization_dir(
        "project-1"
    )
    assert not (project_root / "artifacts").exists()
    assert not (project_root / ".artifact-staging").exists()
    assert artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        unknown.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    ) is None


def test_adjudication_failure_rolls_back_metadata_and_retry_heals_file(
    tmp_path: Path,
    monkeypatch,
):
    unknown = _setup_unknown(tmp_path)
    original = (
        adjudication_store
        .adjudicate_result_unknown_from_connection
    )

    def fail_adjudication(*_args, **_kwargs):
        raise RuntimeError("injected adjudication failure")

    monkeypatch.setattr(
        adjudication_store,
        "adjudicate_result_unknown_from_connection",
        fail_adjudication,
    )
    with pytest.raises(RuntimeError, match="adjudication failure"):
        _recover(unknown)
    monkeypatch.setattr(
        adjudication_store,
        "adjudicate_result_unknown_from_connection",
        original,
    )

    artifact = artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        unknown.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    stored = step_store.get_step_attempt(unknown.step_attempt_id)
    assert artifact is not None
    assert artifact.status == "staged"
    assert artifact.staging_key is not None
    assert stored is not None
    assert stored.status == "result_unknown"
    assert adjudication_store.get_adjudication(
        unknown.step_attempt_id
    ) is None
    project_root = media_assets.project_video_localization_dir(
        "project-1"
    )
    assert (project_root / artifact.storage_key).is_file()
    assert not (project_root / artifact.staging_key).exists()

    healed = _recover(unknown)

    assert healed.outcome == "created"
    assert healed.artifact.status == "committed"
    assert healed.step.status == "success"


def test_concurrent_same_recovery_creates_one_and_reuses_one(
    tmp_path: Path,
):
    unknown = _setup_unknown(tmp_path)
    release = threading.Event()
    results: list[recovery.ProviderRecoveryResult] = []
    errors: list[BaseException] = []

    def recover() -> None:
        release.wait(timeout=1)
        try:
            results.append(_recover(unknown))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=recover) for _ in range(2)]
    for thread in threads:
        thread.start()
    release.set()
    for thread in threads:
        thread.join(timeout=3)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(item.outcome for item in results) == [
        "created",
        "reused",
    ]
    assert len({item.artifact.artifact_id for item in results}) == 1
    assert len(
        {item.adjudication.adjudication_id for item in results}
    ) == 1


def test_failure_adjudication_wins_before_recovery_commit(
    tmp_path: Path,
):
    unknown = _setup_unknown(tmp_path)
    staged = artifact_store.stage_result_unknown_artifact(
        "project-1",
        "operation-1",
        unknown.step_attempt_id,
        expected_status_revision=unknown.status_revision,
        file_backend=managed_artifact_files,
        artifact_kind="step-result",
        artifact_key="primary",
        payload_schema_version=PAYLOAD_SCHEMA,
        media_type="application/json",
        content=_payload(),
        observed_at=T0 + timedelta(seconds=5),
    ).artifact
    adjudication_store.adjudicate_result_unknown(
        "project-1",
        "operation-1",
        unknown.step_attempt_id,
        expected_status_revision=unknown.status_revision,
        decision="failed",
        source="provider_query",
        reason_code="PROVIDER_QUERY_CONFIRMED_FAILURE",
        observed_at=T0 + timedelta(seconds=6),
        error_code="PROVIDER_REQUEST_FAILED",
    )

    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(
            artifact_store.ArtifactIdentityConflict,
            match="result_unknown",
        ):
            artifact_store.commit_result_unknown_artifact_from_connection(
                connection,
                staged.artifact_id,
                project_id="project-1",
                operation_id="operation-1",
                step_attempt_id=unknown.step_attempt_id,
                expected_status_revision=unknown.status_revision,
                file_backend=managed_artifact_files,
                observed_at=T0 + timedelta(seconds=7),
            )

    persisted = artifact_store.get_artifact(
        staged.artifact_id
    )
    assert persisted is not None
    assert persisted.status == "staged"
    assert step_store.get_step_attempt(
        unknown.step_attempt_id
    ).status == "failed"


def test_transaction_level_commit_requires_caller_transaction(
    tmp_path: Path,
):
    unknown = _setup_unknown(tmp_path)
    staged = artifact_store.stage_result_unknown_artifact(
        "project-1",
        "operation-1",
        unknown.step_attempt_id,
        expected_status_revision=unknown.status_revision,
        file_backend=managed_artifact_files,
        artifact_kind="step-result",
        artifact_key="primary",
        payload_schema_version=PAYLOAD_SCHEMA,
        media_type="application/json",
        content=_payload(),
        observed_at=T0 + timedelta(seconds=5),
    ).artifact

    with database.conn() as connection:
        with pytest.raises(RuntimeError, match="transaction"):
            artifact_store.commit_result_unknown_artifact_from_connection(
                connection,
                staged.artifact_id,
                project_id="project-1",
                operation_id="operation-1",
                step_attempt_id=unknown.step_attempt_id,
                expected_status_revision=unknown.status_revision,
                file_backend=managed_artifact_files,
                observed_at=T0 + timedelta(seconds=6),
            )


def test_transaction_level_adjudication_requires_caller_transaction(
    tmp_path: Path,
):
    unknown = _setup_unknown(tmp_path)

    with database.conn() as connection:
        with pytest.raises(RuntimeError, match="transaction"):
            adjudication_store.adjudicate_result_unknown_from_connection(
                connection,
                "project-1",
                "operation-1",
                unknown.step_attempt_id,
                expected_status_revision=unknown.status_revision,
                decision="success",
                source="provider_query",
                reason_code=REASON_CODE,
                observed_at=T0 + timedelta(seconds=6),
                output_fingerprint="a" * 64,
            )

    stored = step_store.get_step_attempt(unknown.step_attempt_id)
    assert stored is not None
    assert stored.status == "result_unknown"


def test_recovery_rejects_invalid_json_before_staging(
    tmp_path: Path,
):
    unknown = _setup_unknown(tmp_path)

    with pytest.raises(ValueError, match="JSON"):
        _recover(unknown, content=b"not-json")

    assert artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        unknown.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    ) is None
