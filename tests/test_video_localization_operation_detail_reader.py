from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    managed_artifact_files,
    media_assets,
    operation_detail_projection,
    operation_detail_reader,
    operation_state,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.main import app  # noqa: E402
from app.schemas.video_localization_llm_observability import (  # noqa: E402
    VideoLocalizationLlmCallRecord,
)
from app.schemas.video_localization_semantic_tts_grouping_step import (  # noqa: E402
    SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION,
    SemanticTtsGroupingRoundArtifactV1,
    semantic_tts_grouping_round_artifact_bytes,
)
from app.schemas.voice_studio import AppSettings, Project  # noqa: E402
from app.services import database, settings_store  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
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


PROJECT_ID = "project-detail-reader"
OPERATION_ID = "semantic-reader"
WORKFLOW_VERSION = "semantic-tts-grouping-workflow-v2"
T0 = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)


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
    workflow_version: str = WORKFLOW_VERSION,
) -> VideoLocalizationOperation:
    parameters = {
        "profile_id": "profile-reader",
        "profile_configuration_fingerprint": "d" * 64,
        "workflow_id": "semantic-tts-grouping",
        "target_chars": 120,
        "max_chars": 180,
    }
    parameters["scope"] = operation_state.operation_scope(
        "semantic_tts_grouping",
        parameters,
    )
    return VideoLocalizationOperation(
        project_id=PROJECT_ID,
        operation_id=OPERATION_ID,
        kind="semantic_tts_grouping",
        status=status,
        progress=1.0 if status in {"success", "failed"} else 0.0,
        parameters=parameters,
        result_summary={
            "stage": "语义分组",
            "workflow_schema_version": workflow_version,
        },
        created_at=T0.isoformat(),
        completed_at=completed_at,
    )


def _project(operation: VideoLocalizationOperation) -> Project:
    return Project(
        project_id=PROJECT_ID,
        name="Detail reader",
        parameters={
            "video_localization": VideoLocalizationDraft(
                operations=[operation]
            ).model_dump(mode="json")
        },
        created_at=T0.isoformat(),
        updated_at=T0.isoformat(),
    )


def _save_queued(
    *,
    with_core: bool = True,
    workflow_version: str = WORKFLOW_VERSION,
) -> VideoLocalizationOperation:
    operation = _operation(workflow_version=workflow_version)
    command = ledger_store.command_from_operation(
        "submit",
        operation.model_dump(mode="json"),
        expected_project_revision=0,
        created_at=T0.isoformat(),
    )
    core = (
        operation_detail_projection.detail_core_from_operation(
            operation,
            workflow_version=workflow_version,
        )
        if with_core
        else None
    )
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
        call_id="semantic-reader-1",
        purpose="semantic_tts_grouping",
        round_index=1,
        profile_id="profile-reader",
        model_id="model-reader",
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
        response_id="response-reader",
    )


def _save_success():
    operation = _save_queued()
    claim = attempt_store.claim_attempt(
        PROJECT_ID,
        OPERATION_ID,
        runner_id="runner-reader",
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
            groups=[["localized-1"], ["localized-2"]],
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
    operation_store.save_project_with_projection(
        PROJECT_ID,
        _project(final).model_dump(mode="json"),
        updated_at=completed_at,
        operation_updated_at=completed_at,
        execution_fence=fence,
        observed_at_ms=int(
            (T0 + timedelta(seconds=6)).timestamp() * 1_000
        ),
        operation_detail_core=(
            operation_detail_projection.detail_core_from_operation(
                final,
                workflow_version=WORKFLOW_VERSION,
            )
        ),
    )
    return committed


def test_managed_reader_assembles_success_without_project_json(
    isolated_store,
    monkeypatch,
):
    _save_success()
    original_read_conn = database.read_conn
    statements: list[str] = []

    @contextmanager
    def traced_read_conn():
        with original_read_conn() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(database, "read_conn", traced_read_conn)

    result = operation_detail_reader.read_operation_detail(
        PROJECT_ID,
        OPERATION_ID,
    )

    assert result.authority == "managed"
    assert result.operation is not None
    assert result.operation.status == "success"
    assert result.operation.parameters["profile_id"] == "profile-reader"
    assert (
        result.operation.result_summary["semantic_group_count"]
        == 2
    )
    assert (
        result.operation.result_summary["llm_model_id"]
        == "model-reader"
    )
    assert result.operation.result_summary["task_step_results"][
        "write"
    ]["status"] == "success"
    assert not any(
        " projects" in statement.lower()
        or "projects." in statement.lower()
        for statement in statements
    )
    select_statements = [
        statement
        for statement in statements
        if statement.lstrip().lower().startswith("select")
    ]
    assert len(select_statements) <= 8
    assert len(result.operation.model_dump_json()) < 16_384


def test_managed_reader_ignores_mirror_drift(isolated_store):
    _save_success()
    with database.conn() as connection:
        row = connection.execute(
            "SELECT data FROM projects WHERE project_id = ?",
            (PROJECT_ID,),
        ).fetchone()
        payload = json.loads(row["data"])
        operation = payload["parameters"]["video_localization"][
            "operations"
        ][0]
        operation["status"] = "failed"
        operation["parameters"]["target_chars"] = 999
        connection.execute(
            "UPDATE projects SET data = ? WHERE project_id = ?",
            (json.dumps(payload), PROJECT_ID),
        )

    result = operation_detail_reader.read_operation_detail(
        PROJECT_ID,
        OPERATION_ID,
    )

    assert result.operation is not None
    assert result.operation.status == "success"
    assert result.operation.parameters["target_chars"] == 120


def test_missing_core_fails_closed(isolated_store):
    _save_queued(with_core=False)

    with pytest.raises(
        operation_detail_reader.OperationDetailRepairRequired
    ) as captured:
        operation_detail_reader.read_operation_detail(
            PROJECT_ID,
            OPERATION_ID,
        )

    assert captured.value.issue_codes == (
        "detail_core_missing",
    )


def test_corrupt_artifact_fails_closed(isolated_store):
    committed = _save_success()
    path = (
        media_assets.project_video_localization_dir(PROJECT_ID)
        / committed.storage_key
    )
    path.write_bytes(b'{"corrupt":true}')

    with pytest.raises(
        operation_detail_reader.OperationDetailRepairRequired
    ) as captured:
        operation_detail_reader.read_operation_detail(
            PROJECT_ID,
            OPERATION_ID,
        )

    assert captured.value.issue_codes == ("artifact_invalid",)


def test_legacy_workflow_uses_named_legacy_authority(
    isolated_store,
):
    _save_queued(
        with_core=False,
        workflow_version="operation-v1",
    )

    result = operation_detail_reader.read_operation_detail(
        PROJECT_ID,
        OPERATION_ID,
    )

    assert result.authority == "legacy"
    assert result.operation is None


def test_public_detail_returns_managed_projection(isolated_store):
    _save_success()
    client = TestClient(app)

    response = client.get(
        f"/api/projects/{PROJECT_ID}/video-localization/"
        f"operations/{OPERATION_ID}"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["result_summary"][
        "semantic_group_count"
    ] == 2


def test_public_detail_returns_repair_required_not_mirror(
    isolated_store,
):
    _save_queued(with_core=False)
    client = TestClient(app)

    response = client.get(
        f"/api/projects/{PROJECT_ID}/video-localization/"
        f"operations/{OPERATION_ID}"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED"
    )
    assert response.json()["error"]["detail"]["issue_codes"] == [
        "detail_core_missing"
    ]


def test_missing_ledger_does_not_reopen_managed_mirror_fallback(
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
        connection.execute(
            """
            DELETE FROM video_localization_operation_outbox
            WHERE project_id = ? AND operation_id = ?
            """,
            (PROJECT_ID, OPERATION_ID),
        )
        connection.execute(
            """
            DELETE FROM video_localization_operations
            WHERE project_id = ? AND operation_id = ?
            """,
            (PROJECT_ID, OPERATION_ID),
        )
    client = TestClient(app)

    response = client.get(
        f"/api/projects/{PROJECT_ID}/video-localization/"
        f"operations/{OPERATION_ID}"
    )

    assert response.status_code == 409
    assert response.json()["error"]["detail"]["issue_codes"] == [
        "ledger_operation_missing"
    ]


def test_wrong_composite_identity_is_not_read_across_projects(
    isolated_store,
):
    _save_queued()

    result = operation_detail_reader.read_operation_detail(
        "another-project",
        OPERATION_ID,
    )

    assert result.authority == "missing"
    assert result.operation is None


def test_reader_rejects_future_core_schema(isolated_store):
    _save_queued()
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_detail_cores
            SET detail_schema_version = 'operation-detail-core-v9'
            WHERE project_id = ? AND operation_id = ?
            """,
            (PROJECT_ID, OPERATION_ID),
        )

    with pytest.raises(
        operation_detail_reader.OperationDetailRepairRequired
    ) as captured:
        operation_detail_reader.read_operation_detail(
            PROJECT_ID,
            OPERATION_ID,
        )

    assert captured.value.issue_codes == ("detail_core_invalid",)
