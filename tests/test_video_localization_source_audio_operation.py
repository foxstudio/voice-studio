from __future__ import annotations

import json
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    managed_artifact_files,
    managed_local_step,
    media_assets,
    operation_detail_reconciliation,
    operation_detail_reader,
    operation_queue,
    operation_state,
    operation_step_shadow,
    project_lifecycle_cleanup,
    service,
    source_audio_execution,
    source_pipeline,
)
from app.errors import AppException  # noqa: E402
from app.main import app  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationOperation,
)
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import database, settings_store  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_execution as operation_execution,
)
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_store as step_store,
)


def _client(tmp_path: Path) -> TestClient:
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    return TestClient(app)


def _create_source_project(
    client: TestClient,
    *,
    name: str,
    content: bytes,
) -> str:
    project_id = client.post(
        "/api/projects",
        json={"name": name, "description": ""},
    ).json()["project_id"]
    imported = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/source-media"
        ),
        files={"file": ("demo.mp4", content, "video/mp4")},
    )
    assert imported.status_code == 200
    return project_id


def _run_source_audio_operation(
    tmp_path: Path,
    monkeypatch,
    *,
    extract_error: AppException | None = None,
) -> tuple[TestClient, str, dict]:
    client = _client(tmp_path)
    project_id = _create_source_project(
        client,
        name="managed source audio",
        content=b"source-video-for-managed-workflow",
    )

    def fake_extract(video_path: Path, audio_path: Path) -> dict:
        assert video_path.is_file()
        assert video_path.parent.name == "source"
        if extract_error is not None:
            raise extract_error
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"fake-shadow-wav")
        return {
            "duration_ms": 2345,
            "sample_rate": 44100,
            "channels": 1,
            "size_bytes": 15,
        }

    monkeypatch.setattr(
        media_assets,
        "extract_audio_file",
        fake_extract,
    )
    submitted = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={"kind": "source_audio"},
    ).json()
    completed = None
    for _ in range(100):
        completed = client.get(
            (
                f"/api/projects/{project_id}"
                "/video-localization/operations/"
                f"{submitted['operation_id']}"
            )
        ).json()
        if completed["status"] in {
            "success",
            "failed",
            "cancelled",
        }:
            break
        time.sleep(0.02)
    assert completed is not None
    if operation_queue._scheduler is not None:
        operation_queue._scheduler.join()
    return client, project_id, completed


def test_managed_source_audio_operation_writes_path_free_artifact(
    tmp_path: Path,
    monkeypatch,
):
    _client_instance, project_id, operation = (
        _run_source_audio_operation(tmp_path, monkeypatch)
    )

    assert operation["status"] == "success"
    assert "audio_path" not in operation["result_summary"]
    steps = step_store.list_step_attempts(
        project_id,
        operation["operation_id"],
    )
    assert len(steps) == 1
    step = steps[0]
    assert step.step_id == "extract_source_audio"
    assert step.cost_class == "local_free"
    assert step.status == "success"
    artifact = artifact_store.get_step_artifact(
        project_id,
        operation["operation_id"],
        step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert artifact is not None
    assert artifact.status == "committed"
    read = artifact_store.read_artifact(
        artifact.artifact_id,
        file_backend=managed_artifact_files,
    )
    payload = json.loads(read.content)

    assert len(read.content) < 1_024
    assert payload == {
        "audio_extract_status": "completed",
        "available_track_count": 1,
        "channels": 1,
        "duration_ms": 2345,
        "media_status": "available",
        "sample_rate": 44100,
        "schema_version": "source-audio-step-output-v1",
        "selected_source": "source_media",
        "track_count": 1,
    }
    assert "audio_path" not in payload
    assert str(tmp_path) not in read.content.decode("utf-8")
    reconciliation = (
        operation_step_shadow.reconcile_source_audio_operation(
            project_id,
            operation["operation_id"],
        )
    )
    assert reconciliation.status == "matched"
    assert reconciliation.issues == ()
    ledger = ledger_store.get_operation(
        project_id,
        operation["operation_id"],
    )
    assert ledger is not None
    assert ledger.workflow_version == "source-audio-workflow-v1"
    assert ledger.parameters_fingerprint == (
        ledger_store.parameters_fingerprint(
            operation["parameters"]
        )
    )
    managed_audit = (
        operation_detail_reconciliation
        .reconcile_operation_detail(
            project_id,
            operation["operation_id"],
        )
    )
    assert managed_audit.status == "matched"
    assert managed_audit.checked_artifact_count == 1


def test_managed_source_audio_detail_never_reads_project_json(
    tmp_path: Path,
    monkeypatch,
):
    _client_instance, project_id, operation = (
        _run_source_audio_operation(tmp_path, monkeypatch)
    )
    original_read_conn = database.read_conn
    statements: list[str] = []

    @contextmanager
    def traced_read_conn():
        with original_read_conn() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(database, "read_conn", traced_read_conn)

    detail = operation_detail_reader.read_operation_detail(
        project_id,
        operation["operation_id"],
    )

    assert detail.authority == "managed"
    assert detail.operation is not None
    assert detail.operation.status == "success"
    assert detail.operation.result_summary[
        "workflow_schema_version"
    ] == "source-audio-workflow-v1"
    assert detail.operation.result_summary["duration_ms"] == 2345
    assert "audio_path" not in detail.operation.result_summary
    assert not any(
        " projects" in statement.lower()
        or "projects." in statement.lower()
        for statement in statements
    )


def test_source_audio_rejects_arbitrary_parameters(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "typed source audio", "description": ""},
    ).json()

    response = client.post(
        (
            f"/api/projects/{project['project_id']}"
            "/video-localization/operations"
        ),
        json={
            "kind": "source_audio",
            "parameters": {"unexpected": True},
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_SOURCE_AUDIO_PARAMETERS_INVALID"
    )


def test_managed_source_audio_detail_covers_queued_and_cancelled(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = _create_source_project(
        client,
        name="queued source audio",
        content=b"queued-source-video",
    )
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)
    submitted = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={"kind": "source_audio"},
    ).json()
    operation_id = submitted["operation_id"]

    queued = operation_detail_reader.read_operation_detail(
        project_id,
        operation_id,
    )

    assert queued.authority == "managed"
    assert queued.operation is not None
    assert queued.operation.status == "queued"
    assert queued.operation.result_summary["task_step_results"][
        "extract_source_audio"
    ]["status"] == "todo"

    cancelled = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations/"
            f"{operation_id}/cancel"
        )
    )
    assert cancelled.status_code == 200
    detail = operation_detail_reader.read_operation_detail(
        project_id,
        operation_id,
    )
    assert detail.operation is not None
    assert detail.operation.status == "cancelled"
    assert detail.operation.result_summary["task_step_results"][
        "extract_source_audio"
    ]["status"] == "cancelled"


def test_managed_source_audio_detail_covers_running_without_step(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = _create_source_project(
        client,
        name="running source audio",
        content=b"running-source-video",
    )
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)
    submitted = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={"kind": "source_audio"},
    ).json()
    operation_queue._mark_operation(
        project_id,
        submitted["operation_id"],
        kind="source_audio",
        status="running",
        progress=0.25,
        started_at="2026-07-31T08:00:00+00:00",
        result_summary={
            "stage": "正在提取原始音轨",
            "stage_id": "extract_source_audio",
        },
    )

    detail = operation_detail_reader.read_operation_detail(
        project_id,
        submitted["operation_id"],
    )

    assert detail.authority == "managed"
    assert detail.operation is not None
    assert detail.operation.status == "running"
    assert detail.operation.result_summary["task_step_results"][
        "extract_source_audio"
    ]["status"] == "running"


def test_legacy_source_audio_detail_and_retry_remain_compatible(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "legacy source audio", "description": ""},
    ).json()
    project_id = project["project_id"]
    imported = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/source-media"
        ),
        files={
            "file": (
                "legacy.mp4",
                b"legacy-source-video",
                "video/mp4",
            )
        },
    )
    assert imported.status_code == 200
    legacy = VideoLocalizationOperation(
        project_id=project_id,
        kind="source_audio",
        status="failed",
        progress=1.0,
        completed_at="2026-07-31T08:00:00+00:00",
        error_code="LEGACY_FAILURE",
        parameters={
            "legacy_unused_key": "preserved-history",
            "scope": operation_state.operation_scope(
                "source_audio",
                {},
            ),
        },
        result_summary={
            "stage": "旧版原音轨任务失败",
            "workflow_schema_version": "operation-v1",
        },
    )
    service.update_video_localization_atomic(
        project_id,
        lambda draft: operation_state.with_operation(
            draft,
            legacy,
        ),
        intent="runtime",
    )
    detail = operation_detail_reader.read_operation_detail(
        project_id,
        legacy.operation_id,
    )
    assert detail.authority == "legacy"
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)

    retried = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations/"
            f"{legacy.operation_id}/retry"
        )
    )

    assert retried.status_code == 200
    payload = retried.json()
    assert payload["parameters"] == {
        "scope": operation_state.operation_scope(
            "source_audio",
            {},
        )
    }
    assert payload["result_summary"][
        "workflow_schema_version"
    ] == "source-audio-workflow-v1"


def test_managed_source_audio_reader_ignores_mirror_mismatch(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, operation = _run_source_audio_operation(
        tmp_path,
        monkeypatch,
    )
    before = client.get(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations/"
            f"{operation['operation_id']}"
        )
    ).json()
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE projects
            SET data = json_set(
                data,
                '$.parameters.video_localization.operations[0].result_summary.duration_ms',
                9999
            )
            WHERE project_id = ?
            """,
            (project_id,),
        )

    with sqlite3.connect(
        tmp_path / "voice_studio.db"
    ) as observer:
        before_version = observer.execute(
            "PRAGMA data_version"
        ).fetchone()[0]
        reconciliation = (
            operation_step_shadow.reconcile_source_audio_operation(
                project_id,
                operation["operation_id"],
            )
        )
        after_version = observer.execute(
            "PRAGMA data_version"
        ).fetchone()[0]
    after = client.get(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations/"
            f"{operation['operation_id']}"
        )
    ).json()

    assert reconciliation.status == "mismatch"
    assert reconciliation.issues == ("output_mismatch",)
    assert after_version == before_version
    assert before["result_summary"]["duration_ms"] == 2345
    assert after["result_summary"]["duration_ms"] == 2345


def test_managed_artifact_failure_prevents_product_success(
    tmp_path: Path,
    monkeypatch,
    caplog,
):
    def fail_stage(*args, **kwargs):
        raise RuntimeError("injected shadow artifact failure")

    monkeypatch.setattr(
        artifact_store,
        "stage_artifact",
        fail_stage,
    )
    _client_instance, project_id, operation = (
        _run_source_audio_operation(tmp_path, monkeypatch)
    )

    assert operation["status"] == "failed"
    steps = step_store.list_step_attempts(
        project_id,
        operation["operation_id"],
    )
    assert len(steps) == 1
    assert steps[0].status == "failed"
    assert (
        steps[0].error_code
        == "VIDEO_LOCALIZATION_SOURCE_AUDIO_RESULT_WRITE_FAILED"
    )
    reconciliation = (
        operation_step_shadow.reconcile_source_audio_operation(
            project_id,
            operation["operation_id"],
        )
    )
    assert reconciliation.status == "incomplete"
    assert reconciliation.issues == ("step_failed",)
    assert (
        operation["error_code"]
        == "VIDEO_LOCALIZATION_SOURCE_AUDIO_RESULT_WRITE_FAILED"
    )
    draft = service.get_video_localization(project_id)
    assert draft is not None
    assert draft.source_media.audio_path is None


def test_project_commit_failure_never_leaves_success_without_media(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "atomic source audio", "description": ""},
    ).json()
    project_id = project["project_id"]
    imported = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/source-media"
        ),
        files={
            "file": (
                "demo.mp4",
                b"source-video-for-atomic-commit",
                "video/mp4",
            )
        },
    )
    assert imported.status_code == 200

    def fake_extract(_video_path: Path, audio_path: Path) -> dict:
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"atomic-source-wav")
        return {
            "duration_ms": 1234,
            "sample_rate": 48000,
            "channels": 2,
            "size_bytes": 17,
        }

    monkeypatch.setattr(media_assets, "extract_audio_file", fake_extract)
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)
    response = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={"kind": "source_audio"},
    )
    assert response.status_code == 200
    operation_id = response.json()["operation_id"]
    original_save = service.draft_store.save

    def fail_success_commit(
        project_id_arg,
        draft,
        *,
        intent,
        **kwargs,
    ):
        operation = operation_state.operation_from_draft(
            draft,
            operation_id,
        )
        if operation is not None and operation.status == "success":
            raise RuntimeError("injected final project commit failure")
        return original_save(
            project_id_arg,
            draft,
            intent=intent,
            **kwargs,
        )

    monkeypatch.setattr(
        service.draft_store,
        "save",
        fail_success_commit,
    )

    operation_queue._process(project_id, operation_id)

    completed = operation_queue.get_operation(
        project_id,
        operation_id,
    )
    assert completed is not None
    assert completed.status == "failed"
    persisted = service.get_video_localization(project_id)
    assert persisted is not None
    assert persisted.source_media.audio_path is None
    steps = step_store.list_step_attempts(
        project_id,
        operation_id,
    )
    assert len(steps) == 1
    assert steps[0].status == "success"
    assert (
        artifact_store.get_step_artifact(
            project_id,
            operation_id,
            steps[0].step_attempt_id,
            artifact_kind="step-result",
            artifact_key="primary",
        )
        is not None
    )


def test_recovery_reuses_local_success_after_crash_before_project_commit(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "recover source audio", "description": ""},
    ).json()
    project_id = project["project_id"]
    imported = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/source-media"
        ),
        files={
            "file": (
                "demo.mp4",
                b"source-video-for-recovery",
                "video/mp4",
            )
        },
    )
    assert imported.status_code == 200
    extraction_count = 0

    def fake_extract(_video_path: Path, audio_path: Path) -> dict:
        nonlocal extraction_count
        extraction_count += 1
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"recover-source-wav")
        return {
            "duration_ms": 3456,
            "sample_rate": 48000,
            "channels": 2,
            "size_bytes": 18,
        }

    monkeypatch.setattr(media_assets, "extract_audio_file", fake_extract)
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)
    submitted = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={"kind": "source_audio"},
    ).json()
    operation_id = submitted["operation_id"]
    operation_queue._mark_operation(
        project_id,
        operation_id,
        kind="source_audio",
        status="running",
        progress=0.5,
        started_at="2026-07-31T08:00:00+00:00",
        result_summary={
            "stage": "正在提取原始音轨",
            "stage_id": "extract_source_audio",
            **operation_queue
            ._source_audio_workflow_summary_fields(),
        },
    )
    decision = operation_execution.acquire_execution_claim(
        project_id,
        operation_id,
        runner_id="crashed-source-worker",
    )
    assert decision.acquired and decision.claim is not None
    claim = decision.claim
    draft = service.get_video_localization(project_id)
    assert draft is not None
    handle = source_audio_execution.prepare_source_audio_step(
        claim.execution_fence,
        draft,
    )
    extracted = source_pipeline.with_extracted_source_audio(
        project_id,
        draft,
    )
    source_audio_execution.complete_source_audio_step(
        handle,
        extracted,
        operation_state.source_audio_summary(extracted),
    )
    before_recovery = service.get_video_localization(project_id)
    assert before_recovery is not None
    assert before_recovery.source_media.audio_path is None
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_attempts
            SET lease_expires_at_ms = 0
            WHERE attempt_id = ?
            """,
            (claim.attempt.attempt_id,),
        )

    operation_queue._recover_project_operations(project_id)

    completed = operation_queue.get_operation(
        project_id,
        operation_id,
    )
    assert completed is not None
    assert completed.status == "success"
    persisted = service.get_video_localization(project_id)
    assert persisted is not None
    assert persisted.source_media.audio_path is not None
    assert Path(persisted.source_media.audio_path).is_file()
    assert extraction_count == 2
    steps = step_store.list_step_attempts(
        project_id,
        operation_id,
    )
    assert len(steps) == 1
    assert steps[0].status == "success"


def test_managed_prepare_failure_prevents_product_success(
    tmp_path: Path,
    monkeypatch,
    caplog,
):
    def fail_prepare(*args, **kwargs):
        raise RuntimeError("injected shadow prepare failure")

    monkeypatch.setattr(
        managed_local_step.step_store,
        "prepare_step",
        fail_prepare,
    )
    _client_instance, project_id, operation = (
        _run_source_audio_operation(tmp_path, monkeypatch)
    )

    assert operation["status"] == "failed"
    assert (
        step_store.list_step_attempts(
            project_id,
            operation["operation_id"],
        )
        == []
    )
    assert operation["error_code"] == (
        "VIDEO_LOCALIZATION_OPERATION_FAILED"
    )


def test_shadow_reconciliation_fails_closed_for_tampered_artifact(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, operation = (
        _run_source_audio_operation(tmp_path, monkeypatch)
    )
    step = step_store.list_step_attempts(
        project_id,
        operation["operation_id"],
    )[0]
    artifact = artifact_store.get_step_artifact(
        project_id,
        operation["operation_id"],
        step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert artifact is not None
    root = media_assets.project_video_localization_dir(project_id)
    path = root / artifact.storage_key
    path.write_bytes(b"x" * artifact.size_bytes)

    reconciliation = (
        operation_step_shadow.reconcile_source_audio_operation(
            project_id,
            operation["operation_id"],
        )
    )

    assert reconciliation.status == "invalid"
    assert reconciliation.issues == ("artifact_invalid",)
    response = client.get(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations/"
            f"{operation['operation_id']}"
        )
    )
    assert response.status_code == 409
    assert response.json()["error"]["detail"]["issue_codes"] == [
        "artifact_invalid"
    ]
    managed_audit = (
        operation_detail_reconciliation
        .reconcile_operation_detail(
            project_id,
            operation["operation_id"],
        )
    )
    assert managed_audit.status == "invalid"
    assert managed_audit.issues == ("artifact_state_invalid",)


def test_source_audio_failure_is_shadowed_with_original_error_code(
    tmp_path: Path,
    monkeypatch,
):
    _client_instance, project_id, operation = (
        _run_source_audio_operation(
            tmp_path,
            monkeypatch,
            extract_error=AppException(
                502,
                "VIDEO_LOCALIZATION_TEST_EXTRACTION_FAILED",
                "injected extraction failure",
            ),
        )
    )

    assert operation["status"] == "failed"
    assert (
        operation["error_code"]
        == "VIDEO_LOCALIZATION_TEST_EXTRACTION_FAILED"
    )
    steps = step_store.list_step_attempts(
        project_id,
        operation["operation_id"],
    )
    assert len(steps) == 1
    assert steps[0].status == "failed"
    assert (
        steps[0].error_code
        == "VIDEO_LOCALIZATION_TEST_EXTRACTION_FAILED"
    )
    assert (
        artifact_store.get_step_artifact(
            project_id,
            operation["operation_id"],
            steps[0].step_attempt_id,
            artifact_kind="step-result",
            artifact_key="primary",
        )
        is None
    )


def test_shadow_reconciliation_fails_closed_for_metadata_schema(
    tmp_path: Path,
    monkeypatch,
):
    _client_instance, project_id, operation = (
        _run_source_audio_operation(tmp_path, monkeypatch)
    )
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_artifacts
            SET artifact_schema_version = 'future-v9'
            WHERE project_id = ? AND operation_id = ?
            """,
            (project_id, operation["operation_id"]),
        )

    reconciliation = (
        operation_step_shadow.reconcile_source_audio_operation(
            project_id,
            operation["operation_id"],
        )
    )

    assert reconciliation.status == "invalid"
    assert reconciliation.issues == ("metadata_invalid",)


def test_managed_source_change_fails_closed(
    tmp_path: Path,
    monkeypatch,
):
    original_prepare = (
        source_audio_execution.prepare_source_audio_step
    )
    changed = False

    def prepare_then_change(execution_fence, draft):
        nonlocal changed
        handle = original_prepare(execution_fence, draft)
        if changed:
            return handle
        changed = True
        replacement = (
            media_assets.ensure_project_video_localization_dir(
                execution_fence.project_id
            )
            / "source"
            / "replacement.mp4"
        )
        replacement.parent.mkdir(parents=True, exist_ok=True)
        replacement.write_bytes(b"replacement-video")
        latest = service.get_video_localization(
            execution_fence.project_id
        )
        assert latest is not None
        updated = latest.model_copy(
            update={
                "source_media": latest.source_media.model_copy(
                    update={
                        "filename": replacement.name,
                        "video_path": str(replacement),
                        "content_sha256": (
                            media_assets.file_sha256(replacement)
                        ),
                        "audio_path": None,
                        "audio_sha256": None,
                    }
                )
            }
        )
        service.save_video_localization(
            execution_fence.project_id,
            updated,
        )
        return handle

    monkeypatch.setattr(
        source_audio_execution,
        "prepare_source_audio_step",
        prepare_then_change,
    )
    _client_instance, project_id, operation = (
        _run_source_audio_operation(tmp_path, monkeypatch)
    )

    assert operation["status"] == "failed"
    steps = step_store.list_step_attempts(
        project_id,
        operation["operation_id"],
    )
    assert [step.status for step in steps] == ["failed"]
    assert (
        steps[0].error_code
        == "VIDEO_LOCALIZATION_SOURCE_CHANGED"
    )
    assert operation["error_code"] == (
        "VIDEO_LOCALIZATION_SOURCE_CHANGED"
    )


def test_source_audio_shadow_follows_public_project_delete(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, operation = _run_source_audio_operation(
        tmp_path,
        monkeypatch,
    )
    step = step_store.list_step_attempts(
        project_id,
        operation["operation_id"],
    )[0]
    artifact = artifact_store.get_step_artifact(
        project_id,
        operation["operation_id"],
        step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert artifact is not None
    project_root = media_assets.project_video_localization_dir(
        project_id
    )
    scheduled_job_ids: list[str] = []
    monkeypatch.setattr(
        project_lifecycle_cleanup,
        "schedule",
        scheduled_job_ids.append,
    )

    deleted = client.delete(f"/api/projects/{project_id}")
    assert len(scheduled_job_ids) == 1
    assert project_lifecycle_cleanup.flush(scheduled_job_ids[0])

    assert deleted.status_code == 200
    assert not project_root.exists()
    assert (
        step_store.list_step_attempts(
            project_id,
            operation["operation_id"],
        )
        == []
    )
    assert artifact_store.get_artifact(artifact.artifact_id) is None
