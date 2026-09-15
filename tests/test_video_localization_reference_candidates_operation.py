from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    managed_artifact_files,
    media_assets,
    operation_detail_reconciliation,
    operation_detail_reader,
    operation_queue,
    operation_state,
    reference_candidates_execution,
    reference_candidates_operation_projection,
    reference_clips,
    service,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import database, settings_store  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_execution as operation_execution,
)
from app.services import (  # noqa: E402
    video_localization_operation_ledger_store as ledger_store,
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


def _create_reference_project(
    client: TestClient,
    *,
    name: str,
) -> tuple[str, Path]:
    project_id = client.post(
        "/api/projects",
        json={"name": name, "description": ""},
    ).json()["project_id"]
    vocals_path = (
        media_assets.ensure_project_video_localization_dir(
            project_id
        )
        / "stems"
        / "vocals.wav"
    )
    vocals_path.parent.mkdir(parents=True, exist_ok=True)
    vocals_path.write_bytes(b"managed-clean-vocals")
    vocals_sha256 = media_assets.file_sha256(vocals_path)
    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            source_media={"duration_ms": 8_000},
            stems={
                "separation_status": "completed",
                "vocals_clean_path": str(vocals_path),
                "vocals_clean_sha256": vocals_sha256,
            },
            speakers=[
                {
                    "speaker_id": "speaker_01",
                    "display_name": "A",
                }
            ],
            cues=[
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1_000,
                    "end_ms": 3_200,
                    "en_subtitle_text": (
                        "This is a reference line."
                    ),
                }
            ],
        ),
    )
    assert saved is not None
    return project_id, vocals_path


def _fake_media(monkeypatch, cut_count: list[int]) -> None:
    def fake_cut(
        source_path: Path,
        destination: Path,
        start_ms: int,
        end_ms: int,
    ) -> Path:
        assert source_path.is_file()
        cut_count[0] += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            f"{start_ms}-{end_ms}".encode("utf-8")
        )
        return destination

    monkeypatch.setattr(
        media_assets,
        "cut_audio_clip",
        fake_cut,
    )
    monkeypatch.setattr(
        reference_clips.audio_tools,
        "probe_audio",
        lambda _path: {
            "duration_ms": 2_200,
            "sample_rate": 24_000,
            "channels": 1,
        },
    )


def _submit_and_process(
    client: TestClient,
    project_id: str,
    monkeypatch,
) -> dict:
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _key: None,
    )
    submitted = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={"kind": "reference_clips"},
    )
    assert submitted.status_code == 200
    operation_id = submitted.json()["operation_id"]
    operation_queue._process(project_id, operation_id)
    completed = operation_queue.get_operation(
        project_id,
        operation_id,
    )
    assert completed is not None
    return completed.model_dump(mode="json")


def test_managed_reference_candidates_write_path_free_artifact(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _vocals_path = _create_reference_project(
        client,
        name="managed reference candidates",
    )
    cut_count = [0]
    _fake_media(monkeypatch, cut_count)

    operation = _submit_and_process(
        client,
        project_id,
        monkeypatch,
    )

    assert operation["status"] == "success"
    assert cut_count == [1]
    assert operation["result_summary"][
        "workflow_schema_version"
    ] == "reference-candidates-workflow-v1"
    assert operation["result_summary"][
        "reference_clip_count"
    ] == 1
    steps = step_store.list_step_attempts(
        project_id,
        operation["operation_id"],
    )
    assert len(steps) == 1
    assert steps[0].step_id == (
        "generate_reference_candidates"
    )
    assert steps[0].cost_class == "local_free"
    assert steps[0].status == "success"
    artifact = artifact_store.get_step_artifact(
        project_id,
        operation["operation_id"],
        steps[0].step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert artifact is not None
    read = artifact_store.read_artifact(
        artifact.artifact_id,
        file_backend=managed_artifact_files,
    )
    payload = json.loads(read.content)
    assert payload["schema_version"] == (
        "reference-candidates-step-output-v1"
    )
    assert payload["candidate_clip_count"] == 1
    assert payload["generated_clip_count"] == 1
    assert payload["linked_cue_count"] == 1
    assert len(payload["generated_media"]) == 1
    assert "audio_path" not in payload["generated_media"][0]
    assert "text" not in payload["generated_media"][0]
    assert str(tmp_path) not in read.content.decode("utf-8")
    ledger = ledger_store.get_operation(
        project_id,
        operation["operation_id"],
    )
    assert ledger is not None
    assert ledger.workflow_version == (
        "reference-candidates-workflow-v1"
    )
    draft = service.get_video_localization(project_id)
    assert draft is not None
    assert len(draft.reference_clips) == 1
    assert Path(
        str(draft.reference_clips[0].audio_path)
    ).name.startswith(
        f"auto-ref-{operation['operation_id']}-"
    )
    audit = (
        operation_detail_reconciliation
        .reconcile_operation_detail(
            project_id,
            operation["operation_id"],
        )
    )
    assert audit.status == "matched"
    assert audit.checked_artifact_count == 1


def test_managed_reference_detail_never_reads_project_json(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _vocals_path = _create_reference_project(
        client,
        name="query-only reference candidates",
    )
    _fake_media(monkeypatch, [0])
    operation = _submit_and_process(
        client,
        project_id,
        monkeypatch,
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
    ] == "reference-candidates-workflow-v1"
    assert not any(
        " projects" in statement.lower()
        or "projects." in statement.lower()
        for statement in statements
    )


def test_reference_candidates_reject_parameters_and_retry_legacy(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _vocals_path = _create_reference_project(
        client,
        name="typed reference candidates",
    )
    rejected = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={
            "kind": "reference_clips",
            "parameters": {"ignored": True},
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_REFERENCE_CANDIDATES_PARAMETERS_INVALID"
    )
    legacy = VideoLocalizationOperation(
        project_id=project_id,
        kind="reference_clips",
        status="failed",
        progress=1.0,
        completed_at="2026-07-31T08:00:00+00:00",
        error_code="LEGACY_FAILURE",
        parameters={
            "legacy_unused_key": "history-only",
            "scope": operation_state.operation_scope(
                "reference_clips",
                {},
            ),
        },
        result_summary={
            "stage": "旧版参考音任务失败",
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
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _key: None,
    )
    retried = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations/"
            f"{legacy.operation_id}/retry"
        )
    )
    assert retried.status_code == 200
    assert retried.json()["parameters"] == {
        "scope": operation_state.operation_scope(
            "reference_clips",
            {},
        )
    }
    assert retried.json()["result_summary"][
        "workflow_schema_version"
    ] == "reference-candidates-workflow-v1"


def test_reference_artifact_failure_cleans_media_and_fails(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _vocals_path = _create_reference_project(
        client,
        name="reference artifact failure",
    )
    _fake_media(monkeypatch, [0])

    def fail_stage(*args, **kwargs):
        raise RuntimeError("injected reference artifact failure")

    monkeypatch.setattr(
        artifact_store,
        "stage_artifact",
        fail_stage,
    )
    operation = _submit_and_process(
        client,
        project_id,
        monkeypatch,
    )

    assert operation["status"] == "failed"
    assert operation["error_code"] == (
        "VIDEO_LOCALIZATION_REFERENCE_CANDIDATES_RESULT_WRITE_FAILED"
    )
    draft = service.get_video_localization(project_id)
    assert draft is not None
    assert draft.reference_clips == []
    references_dir = (
        media_assets.project_video_localization_dir(project_id)
        / "references"
    )
    assert list(references_dir.glob("auto-ref-*.wav")) == []


def test_reference_project_commit_failure_never_leaves_success(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _vocals_path = _create_reference_project(
        client,
        name="reference project failure",
    )
    _fake_media(monkeypatch, [0])
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _key: None,
    )
    submitted = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={"kind": "reference_clips"},
    ).json()
    operation_id = submitted["operation_id"]
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
            raise RuntimeError(
                "injected reference project failure"
            )
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
    assert persisted.reference_clips == []
    assert not media_assets.automatic_reference_clip_path(
        project_id,
        operation_id,
        "ref_speaker_01_cue_0001",
    ).exists()


def test_reference_recovery_reuses_media_without_recut(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _vocals_path = _create_reference_project(
        client,
        name="recover reference candidates",
    )
    cut_count = [0]
    _fake_media(monkeypatch, cut_count)
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _key: None,
    )
    submitted = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={"kind": "reference_clips"},
    ).json()
    operation_id = submitted["operation_id"]
    operation_queue._mark_operation(
        project_id,
        operation_id,
        kind="reference_clips",
        status="running",
        started_at="2026-07-31T08:00:00+00:00",
        result_summary=(
            reference_candidates_operation_projection
            .initial_summary()
        ),
    )
    decision = operation_execution.acquire_execution_claim(
        project_id,
        operation_id,
        runner_id="crashed-reference-worker",
    )
    assert decision.acquired and decision.claim is not None
    claim = decision.claim
    draft = service.get_video_localization(project_id)
    assert draft is not None
    handle = (
        reference_candidates_execution
        .prepare_reference_candidates_step(
            claim.execution_fence,
            draft,
        )
    )
    result = reference_clips.build_automatic_reference_candidates(
        project_id,
        draft,
        operation_id=operation_id,
    )
    (
        reference_candidates_execution
        .complete_reference_candidates_step(
            handle,
            result.draft,
            result,
        )
    )
    assert cut_count == [1]
    before = service.get_video_localization(project_id)
    assert before is not None
    assert before.reference_clips == []
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
    assert len(persisted.reference_clips) == 1
    assert cut_count == [1]


def test_reference_recovery_regenerates_missing_media(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _vocals_path = _create_reference_project(
        client,
        name="regenerate reference candidates",
    )
    cut_count = [0]
    _fake_media(monkeypatch, cut_count)
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _key: None,
    )
    submitted = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={"kind": "reference_clips"},
    ).json()
    operation_id = submitted["operation_id"]
    operation_queue._mark_operation(
        project_id,
        operation_id,
        kind="reference_clips",
        status="running",
        result_summary=(
            reference_candidates_operation_projection
            .initial_summary()
        ),
    )
    decision = operation_execution.acquire_execution_claim(
        project_id,
        operation_id,
        runner_id="missing-reference-worker",
    )
    assert decision.acquired and decision.claim is not None
    claim = decision.claim
    draft = service.get_video_localization(project_id)
    assert draft is not None
    handle = (
        reference_candidates_execution
        .prepare_reference_candidates_step(
            claim.execution_fence,
            draft,
        )
    )
    result = reference_clips.build_automatic_reference_candidates(
        project_id,
        draft,
        operation_id=operation_id,
    )
    output = (
        reference_candidates_execution
        .complete_reference_candidates_step(
            handle,
            result.draft,
            result,
        )
    )
    media_assets.automatic_reference_clip_path(
        project_id,
        operation_id,
        output.generated_media[0].reference_clip_id,
    ).unlink()
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
    assert cut_count == [2]


def test_relevant_concurrent_edit_fails_and_unrelated_ui_edit_merges(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _vocals_path = _create_reference_project(
        client,
        name="reference candidate concurrency",
    )
    _fake_media(monkeypatch, [0])
    original_builder = (
        reference_clips.build_automatic_reference_candidates
    )
    injected = False

    def edit_relevant(*args, **kwargs):
        nonlocal injected
        result = original_builder(*args, **kwargs)
        if not injected:
            injected = True
            service.update_video_localization_atomic(
                project_id,
                lambda draft: draft.model_copy(
                    update={
                        "cues": [
                            draft.cues[0].model_copy(
                                update={
                                    "en_subtitle_text": "Changed"
                                }
                            )
                        ]
                    }
                ),
                intent="content",
            )
        return result

    monkeypatch.setattr(
        reference_clips,
        "build_automatic_reference_candidates",
        edit_relevant,
    )
    failed = _submit_and_process(
        client,
        project_id,
        monkeypatch,
    )
    assert failed["status"] == "failed"
    assert failed["error_code"] == (
        "VIDEO_LOCALIZATION_REFERENCE_INPUT_CHANGED"
    )
    assert service.get_video_localization(
        project_id
    ).reference_clips == []

    monkeypatch.setattr(
        reference_clips,
        "build_automatic_reference_candidates",
        original_builder,
    )
    service.update_video_localization_atomic(
        project_id,
        lambda draft: draft.model_copy(
            update={
                "cues": [
                    draft.cues[0].model_copy(
                        update={
                            "en_subtitle_text": (
                                "This is a reference line."
                            )
                        }
                    )
                ]
            }
        ),
        intent="content",
    )
    injected = False

    def edit_ui(*args, **kwargs):
        nonlocal injected
        result = original_builder(*args, **kwargs)
        if not injected:
            injected = True
            service.update_video_localization_atomic(
                project_id,
                lambda draft: draft.model_copy(
                    update={
                        "ui_state": {
                            "panel": "references"
                        }
                    }
                ),
                intent="runtime",
            )
        return result

    monkeypatch.setattr(
        reference_clips,
        "build_automatic_reference_candidates",
        edit_ui,
    )
    succeeded = _submit_and_process(
        client,
        project_id,
        monkeypatch,
    )
    assert succeeded["status"] == "success"
    persisted = service.get_video_localization(project_id)
    assert persisted is not None
    assert persisted.ui_state == {"panel": "references"}
    assert len(persisted.reference_clips) == 1


def test_tampered_reference_artifact_requires_repair(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _vocals_path = _create_reference_project(
        client,
        name="tampered reference artifact",
    )
    _fake_media(monkeypatch, [0])
    operation = _submit_and_process(
        client,
        project_id,
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
    artifact_path = (
        media_assets.project_video_localization_dir(project_id)
        / artifact.storage_key
    )
    artifact_path.write_bytes(b"x" * artifact.size_bytes)

    response = client.get(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations/"
            f"{operation['operation_id']}"
        )
    )
    assert response.status_code == 409
    assert response.json()["error"]["detail"][
        "issue_codes"
    ] == ["artifact_invalid"]
    audit = (
        operation_detail_reconciliation
        .reconcile_operation_detail(
            project_id,
            operation["operation_id"],
        )
    )
    assert audit.status == "invalid"
    assert audit.issues == ("artifact_state_invalid",)


def test_reference_candidates_detail_covers_queued_and_cancelled(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _vocals_path = _create_reference_project(
        client,
        name="queued reference candidates",
    )
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _key: None,
    )
    submitted = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={"kind": "reference_clips"},
    ).json()
    operation_id = submitted["operation_id"]
    queued = operation_detail_reader.read_operation_detail(
        project_id,
        operation_id,
    )
    assert queued.authority == "managed"
    assert queued.operation is not None
    assert queued.operation.status == "queued"
    assert queued.operation.result_summary[
        "task_step_results"
    ]["generate_reference_candidates"]["status"] == "todo"
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
