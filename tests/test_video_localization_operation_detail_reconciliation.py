from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
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
    operation_detail_projection,
    operation_detail_reconciliation,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.video_localization_llm_observability import (  # noqa: E402
    VideoLocalizationLlmCallRecord,
)
from app.schemas.video_localization_semantic_tts_grouping_step import (  # noqa: E402
    SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION,
    SemanticTtsGroupingRoundArtifactV1,
    semantic_tts_grouping_round_artifact_bytes,
)
from app.schemas.voice_studio import AppSettings, Project  # noqa: E402
from app.services import database, project_store, settings_store  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_detail_core_store as detail_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_store as step_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_store as operation_store,
)


PROJECT_ID = "project-detail-audit"
OPERATION_ID = "semantic-audit"
WORKFLOW_VERSION = "semantic-tts-grouping-workflow-v2"
T0 = datetime(2026, 7, 31, 7, 0, tzinfo=timezone.utc)


@pytest.fixture
def isolated_store(tmp_path: Path):
    original_path = database.DB_PATH
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
    try:
        yield tmp_path
    finally:
        database.set_db_path(original_path)


def _operation(
    *,
    status: str = "queued",
    completed_at: str | None = None,
) -> VideoLocalizationOperation:
    return VideoLocalizationOperation(
        project_id=PROJECT_ID,
        operation_id=OPERATION_ID,
        kind="semantic_tts_grouping",
        status=status,
        progress=1.0 if status == "success" else 0.0,
        parameters={
            "profile_id": "profile-audit",
            "profile_configuration_fingerprint": "d" * 64,
            "workflow_id": "semantic-tts-grouping",
            "target_chars": 120,
            "max_chars": 180,
            "scope": {
                "area": "localized_subtitles",
                "exclusive": False,
            },
        },
        result_summary={
            "stage": "语义分组",
            "workflow_schema_version": WORKFLOW_VERSION,
        },
        created_at=T0.isoformat(),
        completed_at=completed_at,
    )


def _project(operation: VideoLocalizationOperation) -> Project:
    return Project(
        project_id=PROJECT_ID,
        name="Detail audit",
        parameters={
            "video_localization": VideoLocalizationDraft(
                operations=[operation]
            ).model_dump(mode="json")
        },
        created_at=T0.isoformat(),
        updated_at=T0.isoformat(),
    )


def _save_queued() -> VideoLocalizationOperation:
    operation = _operation()
    command = ledger_store.command_from_operation(
        "submit",
        operation.model_dump(mode="json"),
        expected_project_revision=0,
        created_at=T0.isoformat(),
    )
    core = operation_detail_projection.detail_core_from_operation(
        operation,
        workflow_version=WORKFLOW_VERSION,
    )
    assert core is not None
    operation_store.save_project_with_projection(
        PROJECT_ID,
        _project(operation).model_dump(mode="json"),
        updated_at=T0.isoformat(),
        operation_updated_at=T0.isoformat(),
        operation_command=command,
        operation_detail_core=core,
    )
    return operation


def _call() -> VideoLocalizationLlmCallRecord:
    return VideoLocalizationLlmCallRecord(
        call_id="semantic-tts-grouping-1",
        purpose="semantic_tts_grouping",
        round_index=1,
        profile_id="profile-audit",
        model_id="model-a",
        provider_host="loopback",
        request_chars=120,
        request_body_bytes=180,
        max_tokens=1200,
        timeout_seconds=180,
        reasoning_effort_requested=None,
        reasoning_control_applied=True,
        duration_ms=10,
        finish_reason="stop",
        prompt_tokens=80,
        completion_tokens=20,
        total_tokens=100,
        content_chars=40,
        reasoning_chars=0,
        response_id="response-audit",
    )


def _save_success_with_artifact():
    operation = _save_queued()
    claim = attempt_store.claim_attempt(
        PROJECT_ID,
        OPERATION_ID,
        runner_id="runner-audit",
        observed_at=T0 + timedelta(seconds=1),
        lease_duration=timedelta(seconds=60),
    )
    fence = claim.attempt.execution_fence
    assert fence is not None
    step = step_store.prepare_step(
        fence,
        step_id="semantic_grouping_round_1",
        workflow_version=WORKFLOW_VERSION,
        input_fingerprint="e" * 64,
        cost_class="local_free",
        observed_at=T0 + timedelta(seconds=2),
    ).step
    content = semantic_tts_grouping_round_artifact_bytes(
        SemanticTtsGroupingRoundArtifactV1(
            round_index=1,
            groups=[["localized-1"]],
            llm_call=_call(),
        )
    )
    staged = artifact_store.stage_artifact(
        fence,
        file_backend=managed_artifact_files,
        step_attempt_id=step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
        payload_schema_version=(
            SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION
        ),
        media_type="application/json",
        content=content,
        observed_at=T0 + timedelta(seconds=3),
    ).artifact
    committed = artifact_store.commit_artifact(
        staged.artifact_id,
        file_backend=managed_artifact_files,
        execution_fence=fence,
        observed_at=T0 + timedelta(seconds=4),
    )
    step_store.finish_step(
        step.step_attempt_id,
        execution_fence=fence,
        status="success",
        output_fingerprint=committed.content_fingerprint,
        observed_at=T0 + timedelta(seconds=5),
    )
    completed_at = (T0 + timedelta(seconds=6)).isoformat()
    final = operation.model_copy(
        update={
            "status": "success",
            "progress": 1.0,
            "completed_at": completed_at,
        }
    )
    core = operation_detail_projection.detail_core_from_operation(
        final,
        workflow_version=WORKFLOW_VERSION,
    )
    project = _project(final)
    operation_store.save_project_with_projection(
        PROJECT_ID,
        project.model_dump(mode="json"),
        updated_at=completed_at,
        operation_updated_at=completed_at,
        execution_fence=fence,
        observed_at_ms=int(
            (T0 + timedelta(seconds=6)).timestamp() * 1_000
        ),
        operation_detail_core=core,
    )
    return committed


def test_reconciliation_matches_in_one_query_only_snapshot(
    isolated_store,
    monkeypatch,
):
    _save_queued()

    def reject_write_connection(*_args, **_kwargs):
        raise AssertionError("audit opened a mutating connection")

    monkeypatch.setattr(database, "conn", reject_write_connection)

    result = (
        operation_detail_reconciliation
        .reconcile_operation_detail(PROJECT_ID, OPERATION_ID)
    )

    assert result.status == "matched"
    assert result.issues == ()
    assert result.checked_artifact_count == 0
    assert "profile-audit" not in repr(result)
    assert "scope" not in repr(result)
    assert "/" not in repr(result)


def test_bounded_inventory_uses_the_same_query_only_snapshot(
    isolated_store,
    monkeypatch,
):
    _save_queued()

    def reject_write_connection(*_args, **_kwargs):
        raise AssertionError("inventory opened a mutating connection")

    monkeypatch.setattr(database, "conn", reject_write_connection)

    report = (
        operation_detail_reconciliation
        .reconcile_operation_details(limit=1)
    )

    assert report.total_candidate_count == 1
    assert report.checked_candidate_count == 1
    assert report.truncated is False
    assert report.status_counts == {"matched": 1}
    assert report.healthy is True


def test_reconciliation_verifies_committed_artifact_bytes(
    isolated_store,
):
    _save_success_with_artifact()

    result = (
        operation_detail_reconciliation
        .reconcile_operation_detail(PROJECT_ID, OPERATION_ID)
    )

    assert result.status == "matched"
    assert result.checked_artifact_count == 1


def test_reconciliation_reports_missing_detail_without_backfill(
    isolated_store,
):
    _save_queued()
    with database.conn() as connection:
        connection.execute(
            """
            DELETE FROM video_localization_operation_detail_cores
            WHERE project_id = ? AND operation_id = ?
            """,
            (PROJECT_ID, OPERATION_ID),
        )

    result = (
        operation_detail_reconciliation
        .reconcile_operation_detail(PROJECT_ID, OPERATION_ID)
    )

    assert result.status == "missing"
    assert result.issues == ("detail_core_missing",)
    assert detail_store.get_detail_core(
        PROJECT_ID,
        OPERATION_ID,
    ) is None


def test_reconciliation_reports_mirror_parameter_drift(
    isolated_store,
):
    _save_queued()
    with database.conn() as connection:
        row = connection.execute(
            "SELECT data FROM projects WHERE project_id = ?",
            (PROJECT_ID,),
        ).fetchone()
        payload = json.loads(str(row["data"]))
        payload["parameters"]["video_localization"]["operations"][0][
            "parameters"
        ]["target_chars"] = 140
        connection.execute(
            "UPDATE projects SET data = ? WHERE project_id = ?",
            (
                json.dumps(payload, ensure_ascii=False),
                PROJECT_ID,
            ),
        )

    result = (
        operation_detail_reconciliation
        .reconcile_operation_detail(PROJECT_ID, OPERATION_ID)
    )

    assert result.status == "mismatch"
    assert "mirror_parameters_mismatch" in result.issues
    assert "detail_core_mismatch" in result.issues


def test_reconciliation_fails_closed_on_corrupt_core(
    isolated_store,
):
    _save_queued()
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_detail_cores
            SET core_json = '{"broken":'
            WHERE project_id = ? AND operation_id = ?
            """,
            (PROJECT_ID, OPERATION_ID),
        )

    result = (
        operation_detail_reconciliation
        .reconcile_operation_detail(PROJECT_ID, OPERATION_ID)
    )

    assert result.status == "invalid"
    assert "detail_core_invalid" in result.issues


def test_reconciliation_fails_closed_on_corrupt_artifact_bytes(
    isolated_store,
):
    artifact = _save_success_with_artifact()
    path = (
        media_assets.project_video_localization_dir(PROJECT_ID)
        / artifact.storage_key
    )
    path.write_bytes(b"tampered")

    result = (
        operation_detail_reconciliation
        .reconcile_operation_detail(PROJECT_ID, OPERATION_ID)
    )

    assert result.status == "invalid"
    assert "artifact_state_invalid" in result.issues
    assert result.checked_artifact_count == 0


def test_reconciliation_reports_missing_success_artifact_without_repair(
    isolated_store,
):
    artifact = _save_success_with_artifact()
    with database.conn() as connection:
        connection.execute(
            """
            DELETE FROM video_localization_operation_artifacts
            WHERE artifact_id = ?
            """,
            (artifact.artifact_id,),
        )

    result = (
        operation_detail_reconciliation
        .reconcile_operation_detail(PROJECT_ID, OPERATION_ID)
    )

    assert result.status == "missing"
    assert "successful_step_artifact_missing" in result.issues
    with database.conn() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM video_localization_operation_artifacts
            WHERE project_id = ? AND operation_id = ?
            """,
            (PROJECT_ID, OPERATION_ID),
        ).fetchone()[0] == 0


def test_success_without_durable_round_is_incomplete(
    isolated_store,
):
    operation = _operation(
        status="success",
        completed_at=(T0 + timedelta(seconds=1)).isoformat(),
    )
    command = ledger_store.command_from_operation(
        "submit",
        operation.model_dump(mode="json"),
        expected_project_revision=0,
        created_at=T0.isoformat(),
    )
    core = operation_detail_projection.detail_core_from_operation(
        operation,
        workflow_version=WORKFLOW_VERSION,
    )
    operation_store.save_project_with_projection(
        PROJECT_ID,
        _project(operation).model_dump(mode="json"),
        updated_at=T0.isoformat(),
        operation_command=command,
        operation_detail_core=core,
    )

    result = (
        operation_detail_reconciliation
        .reconcile_operation_detail(PROJECT_ID, OPERATION_ID)
    )

    assert result.status == "incomplete"
    assert result.issues == ("successful_workflow_step_missing",)


def test_detail_audit_cli_is_read_only_and_checkable(
    isolated_store,
):
    _save_queued()
    environment = {
        **os.environ,
        "VOICE_STUDIO_DB_PATH": str(database.DB_PATH),
    }
    watcher = sqlite3.connect(database.DB_PATH)
    try:
        before = int(
            watcher.execute("PRAGMA data_version").fetchone()[0]
        )
        healthy = subprocess.run(
            [
                sys.executable,
                str(
                    ROOT
                    / "scripts"
                    / (
                        "audit_video_localization_"
                        "operation_details.py"
                    )
                ),
                "--format",
                "json",
                "--check",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        after = int(
            watcher.execute("PRAGMA data_version").fetchone()[0]
        )
    finally:
        watcher.close()

    assert healthy.returncode == 0, healthy.stderr
    payload = json.loads(healthy.stdout)
    assert payload["status_counts"] == {"matched": 1}
    assert payload["results"][0]["issues"] == []
    assert after == before

    with database.conn() as connection:
        connection.execute(
            """
            DELETE FROM video_localization_operation_detail_cores
            WHERE project_id = ? AND operation_id = ?
            """,
            (PROJECT_ID, OPERATION_ID),
        )
    unhealthy = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / "audit_video_localization_operation_details.py"
            ),
            "--format",
            "json",
            "--check",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert unhealthy.returncode == 1
    assert json.loads(unhealthy.stdout)["status_counts"] == {
        "missing": 1
    }
