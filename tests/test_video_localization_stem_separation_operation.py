from __future__ import annotations

import json
import sys
from contextlib import contextmanager
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
    operation_detail_reconciliation,
    operation_detail_reader,
    operation_queue,
    operation_state,
    service,
    source_pipeline,
    stem_separation_operation_projection,
    stem_separation_execution,
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


def _create_audio_project(
    client: TestClient,
    *,
    name: str,
    audio_content: bytes = b"managed-source-audio",
) -> tuple[str, Path]:
    project_id = client.post(
        "/api/projects",
        json={"name": name, "description": ""},
    ).json()["project_id"]
    audio_path = (
        media_assets.ensure_project_video_localization_dir(
            project_id
        )
        / "audio"
        / "source.wav"
    )
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(audio_content)
    audio_sha256 = media_assets.file_sha256(audio_path)
    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            source_media={
                "audio_path": str(audio_path),
                "audio_sha256": audio_sha256,
                "duration_ms": 4_000,
            },
            stems={
                "original_audio_path": str(audio_path),
                "original_audio_sha256": audio_sha256,
            },
        ),
    )
    assert saved is not None
    return project_id, audio_path


def _fake_separator(call_count: list[int]):
    def separate(
        source_audio: Path,
        stems_dir: Path,
        *,
        output_prefix: str | None = None,
    ) -> dict:
        assert source_audio.is_file()
        assert output_prefix
        call_count[0] += 1
        stems_dir.mkdir(parents=True, exist_ok=True)
        vocals = (
            stems_dir
            / f"{output_prefix}-vocals-clean.wav"
        )
        background = (
            stems_dir
            / f"{output_prefix}-background.wav"
        )
        vocals.write_bytes(b"managed-vocals")
        background.write_bytes(b"managed-background")
        return {
            "vocals_clean_path": vocals,
            "background_path": background,
            "engine_id": "bs-roformer-viperx-1297:residual-v1",
            "quality_flags": ["needs_reference_review"],
        }

    return separate


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
        json={"kind": "stems"},
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


def test_managed_stems_write_path_free_typed_artifact(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _audio_path = _create_audio_project(
        client,
        name="managed stems",
    )
    call_count = [0]
    monkeypatch.setattr(
        media_assets,
        "separate_audio_file",
        _fake_separator(call_count),
    )

    operation = _submit_and_process(
        client,
        project_id,
        monkeypatch,
    )

    assert operation["status"] == "success"
    assert call_count == [1]
    assert operation["result_summary"][
        "workflow_schema_version"
    ] == "stem-separation-workflow-v1"
    assert "vocals_clean_path" not in operation["result_summary"]
    assert "background_path" not in operation["result_summary"]
    steps = step_store.list_step_attempts(
        project_id,
        operation["operation_id"],
    )
    assert len(steps) == 1
    assert steps[0].step_id == "separate_stems"
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
        "stem-separation-step-output-v1"
    )
    assert payload["separation_status"] == "completed"
    assert payload["track_count"] == 2
    assert payload["available_track_count"] == 2
    assert payload["media_status"] == "complete"
    assert len(payload["source_audio_sha256"]) == 64
    assert len(payload["vocals_clean_sha256"]) == 64
    assert len(payload["background_sha256"]) == 64
    assert "path" not in read.content.decode("utf-8")
    assert str(tmp_path) not in read.content.decode("utf-8")
    ledger = ledger_store.get_operation(
        project_id,
        operation["operation_id"],
    )
    assert ledger is not None
    assert ledger.workflow_version == (
        "stem-separation-workflow-v1"
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


def test_managed_stems_detail_never_reads_project_json(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _audio_path = _create_audio_project(
        client,
        name="query-only stems",
    )
    monkeypatch.setattr(
        media_assets,
        "separate_audio_file",
        _fake_separator([0]),
    )
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
    ] == "stem-separation-workflow-v1"
    assert "vocals_clean_path" not in (
        detail.operation.result_summary
    )
    assert not any(
        " projects" in statement.lower()
        or "projects." in statement.lower()
        for statement in statements
    )


def test_managed_stems_detail_covers_queued_and_cancelled(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _audio_path = _create_audio_project(
        client,
        name="queued managed stems",
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
        json={"kind": "stems"},
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
    ]["separate_stems"]["status"] == "todo"

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
    assert detail.operation.result_summary[
        "task_step_results"
    ]["separate_stems"]["status"] == "cancelled"


def test_stems_reject_arbitrary_parameters_and_retry_cleans_legacy(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _audio_path = _create_audio_project(
        client,
        name="typed stems",
    )
    rejected = client.post(
        (
            f"/api/projects/{project_id}"
            "/video-localization/operations"
        ),
        json={
            "kind": "stems",
            "parameters": {"ignored": True},
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_STEM_SEPARATION_PARAMETERS_INVALID"
    )

    legacy = VideoLocalizationOperation(
        project_id=project_id,
        kind="stems",
        status="failed",
        progress=1.0,
        completed_at="2026-07-31T08:00:00+00:00",
        error_code="LEGACY_FAILURE",
        parameters={
            "legacy_unused_key": "history-only",
            "scope": operation_state.operation_scope(
                "stems",
                {},
            ),
        },
        result_summary={
            "stage": "旧版分轨失败",
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
    payload = retried.json()
    assert payload["parameters"] == {
        "scope": operation_state.operation_scope("stems", {})
    }
    assert payload["result_summary"][
        "workflow_schema_version"
    ] == "stem-separation-workflow-v1"


def test_stem_artifact_failure_cleans_media_and_prevents_success(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _audio_path = _create_audio_project(
        client,
        name="stem artifact failure",
    )
    monkeypatch.setattr(
        media_assets,
        "separate_audio_file",
        _fake_separator([0]),
    )

    def fail_stage(*args, **kwargs):
        raise RuntimeError("injected stem artifact failure")

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
        "VIDEO_LOCALIZATION_STEM_SEPARATION_RESULT_WRITE_FAILED"
    )
    draft = service.get_video_localization(project_id)
    assert draft is not None
    assert draft.stems.vocals_clean_path is None
    assert draft.stems.background_path is None
    paths = media_assets.stem_separation_output_paths(
        project_id,
        operation["operation_id"],
    )
    assert not any(path.exists() for path in paths)
    steps = step_store.list_step_attempts(
        project_id,
        operation["operation_id"],
    )
    assert len(steps) == 1
    assert steps[0].status == "failed"


def test_managed_stem_writer_cleans_partial_files_on_quality_failure(
    tmp_path: Path,
    monkeypatch,
):
    audio_path = tmp_path / "source.wav"
    stems_dir = tmp_path / "stems"
    audio_path.write_bytes(b"source")
    stems_dir.mkdir()
    def write_partial(
        _source,
        vocals_path,
        background_path,
        **_kwargs,
    ):
        Path(vocals_path).write_bytes(b"partial")
        Path(background_path).write_bytes(b"partial")
        return {"engine_id": "bs-roformer-viperx-1297:residual-v1"}

    monkeypatch.setattr(
        media_assets.stem_separation_engine,
        "separate",
        write_partial,
    )
    monkeypatch.setattr(
        media_assets.audio_tools,
        "quality_metrics",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("quality failure")
        ),
    )

    with pytest.raises(RuntimeError, match="quality failure"):
        media_assets.separate_audio_file(
            audio_path,
            stems_dir,
            output_prefix="operation-1",
        )

    assert list(stems_dir.iterdir()) == []


def test_stem_project_commit_failure_never_leaves_product_success(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _audio_path = _create_audio_project(
        client,
        name="stem project failure",
    )
    monkeypatch.setattr(
        media_assets,
        "separate_audio_file",
        _fake_separator([0]),
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
        json={"kind": "stems"},
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
                "injected stem project commit failure"
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
    assert persisted.stems.vocals_clean_path is None
    assert persisted.stems.background_path is None
    steps = step_store.list_step_attempts(
        project_id,
        operation_id,
    )
    assert len(steps) == 1
    assert steps[0].status == "success"
    assert not any(
        path.exists()
        for path in media_assets.stem_separation_output_paths(
            project_id,
            operation_id,
        )
    )


def test_recovery_reuses_verified_stems_without_rerunning_separator(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _audio_path = _create_audio_project(
        client,
        name="recover managed stems",
    )
    call_count = [0]
    monkeypatch.setattr(
        media_assets,
        "separate_audio_file",
        _fake_separator(call_count),
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
        json={"kind": "stems"},
    ).json()
    operation_id = submitted["operation_id"]
    operation_queue._mark_operation(
        project_id,
        operation_id,
        kind="stems",
        status="running",
        progress=0.5,
        started_at="2026-07-31T08:00:00+00:00",
        result_summary={
            "stage": "正在分离人声与背景声",
            "stage_id": "separate_stems",
            **operation_queue
            ._stem_separation_workflow_summary_fields(),
        },
    )
    decision = operation_execution.acquire_execution_claim(
        project_id,
        operation_id,
        runner_id="crashed-stem-worker",
    )
    assert decision.acquired and decision.claim is not None
    claim = decision.claim
    draft = service.get_video_localization(project_id)
    assert draft is not None
    handle = (
        stem_separation_execution
        .prepare_stem_separation_step(
            claim.execution_fence,
            draft,
        )
    )
    separated = source_pipeline.with_separated_source_audio(
        project_id,
        draft,
        output_prefix=operation_id,
    )
    stem_separation_execution.complete_stem_separation_step(
        handle,
        separated,
    )
    before = service.get_video_localization(project_id)
    assert before is not None
    assert before.stems.vocals_clean_path is None
    assert call_count == [1]
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
    assert Path(
        str(persisted.stems.vocals_clean_path)
    ).is_file()
    assert Path(str(persisted.stems.background_path)).is_file()
    assert call_count == [1]


def test_recovery_regenerates_missing_owned_media_and_verifies_replay(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _audio_path = _create_audio_project(
        client,
        name="regenerate managed stems",
    )
    call_count = [0]
    monkeypatch.setattr(
        media_assets,
        "separate_audio_file",
        _fake_separator(call_count),
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
        json={"kind": "stems"},
    ).json()
    operation_id = submitted["operation_id"]
    operation_queue._mark_operation(
        project_id,
        operation_id,
        kind="stems",
        status="running",
        started_at="2026-07-31T08:00:00+00:00",
        result_summary=(
            stem_separation_operation_projection.initial_summary()
        ),
    )
    decision = operation_execution.acquire_execution_claim(
        project_id,
        operation_id,
        runner_id="missing-media-worker",
    )
    assert decision.acquired and decision.claim is not None
    claim = decision.claim
    draft = service.get_video_localization(project_id)
    assert draft is not None
    handle = (
        stem_separation_execution
        .prepare_stem_separation_step(
            claim.execution_fence,
            draft,
        )
    )
    separated = source_pipeline.with_separated_source_audio(
        project_id,
        draft,
        output_prefix=operation_id,
    )
    stem_separation_execution.complete_stem_separation_step(
        handle,
        separated,
    )
    vocals_path, _background_path = (
        media_assets.stem_separation_output_paths(
            project_id,
            operation_id,
        )
    )
    vocals_path.unlink()
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
    assert call_count == [2]


def test_tampered_stem_artifact_requires_explicit_repair(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _audio_path = _create_audio_project(
        client,
        name="tampered stem artifact",
    )
    monkeypatch.setattr(
        media_assets,
        "separate_audio_file",
        _fake_separator([0]),
    )
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


def test_input_change_after_separation_fails_closed_and_cleans_outputs(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, audio_path = _create_audio_project(
        client,
        name="stale managed stems",
    )
    call_count = [0]

    def mutate_source(
        source_audio: Path,
        stems_dir: Path,
        *,
        output_prefix: str | None = None,
    ) -> dict:
        result = _fake_separator(call_count)(
            source_audio,
            stems_dir,
            output_prefix=output_prefix,
        )
        audio_path.write_bytes(b"changed-source-audio")
        return result

    monkeypatch.setattr(
        media_assets,
        "separate_audio_file",
        mutate_source,
    )
    operation = _submit_and_process(
        client,
        project_id,
        monkeypatch,
    )

    assert operation["status"] == "failed"
    assert operation["error_code"] == (
        "VIDEO_LOCALIZATION_SOURCE_AUDIO_CHANGED"
    )
    assert not any(
        path.exists()
        for path in media_assets.stem_separation_output_paths(
            project_id,
            operation["operation_id"],
        )
    )
