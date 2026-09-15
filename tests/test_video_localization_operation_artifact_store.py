from __future__ import annotations

import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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
    video_localization_operation_step_store as step_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_store as operation_store,
)
from app.services.video_localization_execution_fence import (  # noqa: E402
    ExecutionFenceLost,
)


OBSERVED_AT = datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
LEASE_DURATION = timedelta(seconds=60)


def _setup(tmp_path: Path):
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
                'test',
                'running',
                'parameters-v1',
                'workflow-v1',
                'command',
                ?,
                ?
            )
            """,
            (OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat()),
        )
    claim = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        observed_at=OBSERVED_AT,
        lease_duration=LEASE_DURATION,
    ).attempt.execution_fence
    assert claim is not None
    step = step_store.prepare_step(
        claim,
        step_id="normalize",
        workflow_version="workflow-v1",
        input_fingerprint="input-v1",
        cost_class="local_free",
        observed_at=OBSERVED_AT + timedelta(seconds=1),
    ).step
    return claim, step


def _json_payload(
    value: str = "normalized",
    *,
    schema_version: str = "normalized-result-v1",
) -> bytes:
    return json.dumps(
        {
            "schema_version": schema_version,
            "value": value,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _stage(
    claim,
    step,
    *,
    content: bytes | None = None,
):
    return artifact_store.stage_artifact(
        claim,
        file_backend=managed_artifact_files,
        step_attempt_id=step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
        payload_schema_version="normalized-result-v1",
        media_type="application/json",
        content=content if content is not None else _json_payload(),
        observed_at=OBSERVED_AT + timedelta(seconds=2),
    )


def test_artifact_schema_is_additive_and_never_stores_payload(
    tmp_path: Path,
):
    existing_db = tmp_path / "existing.db"
    with sqlite3.connect(existing_db) as connection:
        connection.execute(
            "CREATE TABLE existing_data (value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO existing_data (value) VALUES ('preserved')"
        )
    database.set_db_path(existing_db)

    with database.conn() as connection:
        assert connection.in_transaction is False
        columns = {
            row["name"]
            for row in connection.execute(
                """
                PRAGMA table_info(
                    video_localization_operation_artifacts
                )
                """
            ).fetchall()
        }
        preserved = connection.execute(
            "SELECT value FROM existing_data"
        ).fetchone()["value"]

    assert preserved == "preserved"
    assert {
        "artifact_id",
        "artifact_schema_version",
        "project_id",
        "operation_id",
        "step_attempt_id",
        "artifact_kind",
        "artifact_key",
        "payload_schema_version",
        "media_type",
        "storage_backend",
        "storage_key",
        "staging_key",
        "content_fingerprint",
        "size_bytes",
        "status",
        "status_revision",
        "created_at",
        "committed_at",
    } <= columns
    assert {"payload", "content", "data"}.isdisjoint(columns)


def test_stage_commit_and_verified_read_use_project_relative_key(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)
    payload = _json_payload("payload-only-marker")
    staged = _stage(claim, step, content=payload).artifact

    assert staged.status == "staged"
    assert not Path(staged.storage_key).is_absolute()
    assert ".." not in Path(staged.storage_key).parts
    assert staged.staging_key is not None
    committed = artifact_store.commit_artifact(
        staged.artifact_id,
        file_backend=managed_artifact_files,
        execution_fence=claim,
        observed_at=OBSERVED_AT + timedelta(seconds=3),
    )
    read = artifact_store.read_artifact(
        staged.artifact_id,
        file_backend=managed_artifact_files,
    )

    assert committed.status == "committed"
    assert committed.staging_key is None
    assert read.artifact == committed
    assert read.content == payload
    project_root = media_assets.project_video_localization_dir(
        "project-1"
    )
    assert (project_root / committed.storage_key).is_file()
    with database.conn() as connection:
        stored = connection.execute(
            """
            SELECT *
            FROM video_localization_operation_artifacts
            WHERE artifact_id = ?
            """,
            (committed.artifact_id,),
        ).fetchone()
    assert "payload-only-marker" not in " ".join(
        str(value) for value in tuple(stored)
    )


def test_jpeg_artifact_is_committed_and_verified(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)
    payload = b"\xff\xd8\xff\xe0visual-frame\xff\xd9"

    staged = artifact_store.stage_artifact(
        claim,
        file_backend=managed_artifact_files,
        step_attempt_id=step.step_attempt_id,
        artifact_kind="visual-frame",
        artifact_key="visual_01_r1_f1",
        payload_schema_version="asr-visual-frame-v1",
        media_type="image/jpeg",
        content=payload,
        observed_at=OBSERVED_AT + timedelta(seconds=2),
    ).artifact
    committed = artifact_store.commit_artifact(
        staged.artifact_id,
        file_backend=managed_artifact_files,
        execution_fence=claim,
        observed_at=OBSERVED_AT + timedelta(seconds=3),
    )
    read = artifact_store.read_artifact(
        committed.artifact_id,
        file_backend=managed_artifact_files,
    )

    assert committed.storage_key.endswith(".jpg")
    assert read.content == payload


def test_same_semantic_slot_and_content_reuses_but_change_conflicts(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)

    first = _stage(claim, step)
    repeated = _stage(claim, step)
    with pytest.raises(
        artifact_store.ArtifactIdentityConflict,
        match="different content",
    ):
        _stage(
            claim,
            step,
            content=_json_payload("changed"),
        )

    assert first.outcome == "created"
    assert repeated.outcome == "reused"
    assert repeated.artifact == first.artifact


def test_concurrent_stage_creates_one_row_and_one_staging_file(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)
    release = threading.Event()
    decisions: list[artifact_store.ArtifactStageDecision] = []
    errors: list[BaseException] = []

    def stage() -> None:
        release.wait(timeout=1)
        try:
            decisions.append(_stage(claim, step))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=stage) for _ in range(2)]
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
    with database.conn() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM video_localization_operation_artifacts
            """
        ).fetchone()["count"]
    staging_root = (
        media_assets.project_video_localization_dir("project-1")
        / ".artifact-staging"
    )
    assert count == 1
    assert len(list(staging_root.glob("*.part"))) == 1


def test_concurrent_commit_is_idempotent(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)
    staged = _stage(claim, step).artifact
    release = threading.Event()
    committed: list[artifact_store.ManagedOperationArtifact] = []
    errors: list[BaseException] = []

    def commit() -> None:
        release.wait(timeout=1)
        try:
            committed.append(
                artifact_store.commit_artifact(
                    staged.artifact_id,
                    file_backend=managed_artifact_files,
                    execution_fence=claim,
                    observed_at=OBSERVED_AT
                    + timedelta(seconds=3),
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=commit) for _ in range(2)]
    for thread in threads:
        thread.start()
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert len(committed) == 2
    assert {item.status for item in committed} == {"committed"}
    assert {item.status_revision for item in committed} == {2}
    assert artifact_store.read_artifact(
        staged.artifact_id,
        file_backend=managed_artifact_files,
    ).content == _json_payload()


def test_fence_loss_after_file_write_cleans_unregistered_staging(
    tmp_path: Path,
    monkeypatch,
):
    claim, step = _setup(tmp_path)
    original = (
        artifact_store.require_step_execution_fence
    )
    calls = 0

    def fail_second_validation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ExecutionFenceLost("injected fence loss")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        artifact_store,
        "require_step_execution_fence",
        fail_second_validation,
    )

    with pytest.raises(ExecutionFenceLost, match="injected"):
        _stage(claim, step)

    with database.conn() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM video_localization_operation_artifacts
            """
        ).fetchone()["count"]
    staging_root = (
        media_assets.project_video_localization_dir("project-1")
        / ".artifact-staging"
    )
    assert count == 0
    assert list(staging_root.glob("*.part")) == []


def test_database_insert_conflict_cleans_unregistered_staging(
    tmp_path: Path,
    monkeypatch,
):
    claim, step = _setup(tmp_path)
    committed = artifact_store.commit_artifact(
        _stage(claim, step).artifact.artifact_id,
        file_backend=managed_artifact_files,
        execution_fence=claim,
        observed_at=OBSERVED_AT + timedelta(seconds=3),
    )
    monkeypatch.setattr(
        artifact_store.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex=committed.artifact_id),
    )

    with pytest.raises(
        artifact_store.ArtifactIdentityConflict,
        match="already exists",
    ):
        artifact_store.stage_artifact(
            claim,
            file_backend=managed_artifact_files,
            step_attempt_id=step.step_attempt_id,
            artifact_kind="diagnostic",
            artifact_key="secondary",
            payload_schema_version="diagnostic-v1",
            media_type="application/json",
            content=_json_payload(schema_version="diagnostic-v1"),
            observed_at=OBSERVED_AT + timedelta(seconds=4),
        )

    root = media_assets.project_video_localization_dir("project-1")
    assert not (
        root
        / ".artifact-staging"
        / f"{committed.artifact_id}.part"
    ).exists()


def test_staging_identity_collision_preserves_existing_file(
    tmp_path: Path,
    monkeypatch,
):
    claim, step = _setup(tmp_path)
    artifact_id = "a" * 32
    keys = managed_artifact_files.build_storage_keys(
        operation_id=claim.operation_id,
        step_attempt_id=step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
        artifact_id=artifact_id,
        media_type="application/json",
    )
    owner_content = b"existing-owner-content"
    managed_artifact_files.write_staging_file(
        claim.project_id,
        keys.staging_key,
        owner_content,
    )
    monkeypatch.setattr(
        artifact_store.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex=artifact_id),
    )

    with pytest.raises(
        artifact_store.ArtifactIntegrityError,
        match="already exists",
    ):
        _stage(claim, step)

    root = media_assets.project_video_localization_dir(claim.project_id)
    assert root.joinpath(keys.staging_key).read_bytes() == owner_content


def test_replace_then_database_rollback_is_replayable(
    tmp_path: Path,
    monkeypatch,
):
    claim, step = _setup(tmp_path)
    staged = _stage(claim, step).artifact
    original_commit_file = (
        managed_artifact_files.commit_staging_file
    )

    def fail_after_file_replace(*args, **kwargs):
        original_commit_file(*args, **kwargs)
        raise RuntimeError("injected failure after file replace")

    monkeypatch.setattr(
        managed_artifact_files,
        "commit_staging_file",
        fail_after_file_replace,
    )
    with pytest.raises(RuntimeError, match="after file replace"):
        artifact_store.commit_artifact(
            staged.artifact_id,
            file_backend=managed_artifact_files,
            execution_fence=claim,
            observed_at=OBSERVED_AT + timedelta(seconds=3),
        )
    monkeypatch.setattr(
        managed_artifact_files,
        "commit_staging_file",
        original_commit_file,
    )

    persisted = artifact_store.get_artifact(staged.artifact_id)
    assert persisted is not None
    assert persisted.status == "staged"
    root = media_assets.project_video_localization_dir("project-1")
    assert not (root / str(persisted.staging_key)).exists()
    assert (root / persisted.storage_key).is_file()

    database.set_db_path(tmp_path / "voice_studio.db")
    recovered = artifact_store.commit_artifact(
        staged.artifact_id,
        file_backend=managed_artifact_files,
        execution_fence=claim,
        observed_at=OBSERVED_AT + timedelta(seconds=4),
    )
    assert recovered.status == "committed"
    assert artifact_store.read_artifact(
        recovered.artifact_id,
        file_backend=managed_artifact_files,
    ).content == _json_payload()


@pytest.mark.parametrize(
    "unsafe_key",
    (
        "../outside.json",
        "/tmp/outside.json",
        "artifacts//outside.json",
        "artifacts/./outside.json",
        r"artifacts\outside.json",
    ),
)
def test_reader_fails_closed_for_tamper_and_storage_escape(
    tmp_path: Path,
    unsafe_key: str,
):
    claim, step = _setup(tmp_path)
    artifact = artifact_store.commit_artifact(
        _stage(claim, step).artifact.artifact_id,
        file_backend=managed_artifact_files,
        execution_fence=claim,
        observed_at=OBSERVED_AT + timedelta(seconds=3),
    )
    root = media_assets.project_video_localization_dir("project-1")
    artifact_path = root / artifact.storage_key
    artifact_path.write_bytes(b"x" * len(_json_payload()))

    with pytest.raises(
        artifact_store.ArtifactIntegrityError,
        match="fingerprint",
    ):
        artifact_store.read_artifact(
            artifact.artifact_id,
            file_backend=managed_artifact_files,
        )

    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_artifacts
            SET storage_key = ?
            WHERE artifact_id = ?
            """,
            (unsafe_key, artifact.artifact_id),
        )
    with pytest.raises(
        artifact_store.ArtifactPathError,
        match="relative",
    ):
        artifact_store.read_artifact(
            artifact.artifact_id,
            file_backend=managed_artifact_files,
        )


def test_reader_fails_closed_for_missing_file(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)
    artifact = artifact_store.commit_artifact(
        _stage(claim, step).artifact.artifact_id,
        file_backend=managed_artifact_files,
        execution_fence=claim,
        observed_at=OBSERVED_AT + timedelta(seconds=3),
    )
    root = media_assets.project_video_localization_dir("project-1")
    root.joinpath(artifact.storage_key).unlink()

    with pytest.raises(
        artifact_store.ArtifactIntegrityError,
        match="missing",
    ):
        artifact_store.read_artifact(
            artifact.artifact_id,
            file_backend=managed_artifact_files,
        )


def test_reader_fails_closed_for_unknown_metadata_schema(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)
    artifact = _stage(claim, step).artifact
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_artifacts
            SET artifact_schema_version = 'future-v9'
            WHERE artifact_id = ?
            """,
            (artifact.artifact_id,),
        )

    with pytest.raises(
        artifact_store.ArtifactSchemaError,
        match="unsupported",
    ):
        artifact_store.get_artifact(artifact.artifact_id)


def test_reader_fails_closed_for_inconsistent_lifecycle_metadata(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)
    artifact = _stage(claim, step).artifact
    with database.conn() as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE video_localization_operation_artifacts
            SET staging_key = NULL
            WHERE artifact_id = ?
            """,
            (artifact.artifact_id,),
        )

    with pytest.raises(
        artifact_store.ArtifactSchemaError,
        match="lifecycle",
    ):
        artifact_store.get_artifact(artifact.artifact_id)


def test_commit_rejects_lost_execution_fence_without_moving_file(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)
    artifact = _stage(claim, step).artifact
    assert attempt_store.finish_claim(
        claim.attempt_id,
        claim.project_id,
        claim.operation_id,
        runner_id=claim.runner_id,
        fencing_token=claim.fencing_token,
        status="interrupted",
        observed_at=OBSERVED_AT + timedelta(seconds=3),
    )

    with pytest.raises(ExecutionFenceLost):
        artifact_store.commit_artifact(
            artifact.artifact_id,
            file_backend=managed_artifact_files,
            execution_fence=claim,
            observed_at=OBSERVED_AT + timedelta(seconds=4),
        )

    persisted = artifact_store.get_artifact(artifact.artifact_id)
    assert persisted is not None
    assert persisted.status == "staged"
    root = media_assets.project_video_localization_dir(claim.project_id)
    assert persisted.staging_key is not None
    assert root.joinpath(persisted.staging_key).is_file()
    assert not root.joinpath(persisted.storage_key).exists()


def test_invalid_json_media_type_size_and_symlink_root_are_rejected(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)

    with pytest.raises(ValueError, match="schema_version"):
        _stage(claim, step, content=b"{}")
    with pytest.raises(ValueError, match="media type"):
        artifact_store.stage_artifact(
            claim,
            file_backend=managed_artifact_files,
            step_attempt_id=step.step_attempt_id,
            artifact_kind="step-result",
            artifact_key="image",
            payload_schema_version="normalized-result-v1",
            media_type="image/png",
            content=b"jpeg",
            observed_at=OBSERVED_AT + timedelta(seconds=2),
        )
    with pytest.raises(ValueError, match="size"):
        _stage(claim, step, content=b"")
    with pytest.raises(ValueError, match="size"):
        _stage(
            claim,
            step,
            content=b"x"
            * (artifact_store.MAX_MANAGED_ARTIFACT_BYTES + 1),
        )
    with pytest.raises(ValueError, match="UTF-8"):
        artifact_store.stage_artifact(
            claim,
            file_backend=managed_artifact_files,
            step_attempt_id=step.step_attempt_id,
            artifact_kind="diagnostic",
            artifact_key="text",
            payload_schema_version="diagnostic-text-v1",
            media_type="text/plain",
            content=b"\xff",
            observed_at=OBSERVED_AT + timedelta(seconds=2),
        )

    project_root = media_assets.project_video_localization_dir(
        "project-1"
    )
    if project_root.exists():
        project_root.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    project_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(
        artifact_store.ArtifactPathError,
        match="symlink",
    ):
        _stage(claim, step)


def test_project_move_keeps_relative_artifact_readable(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)
    artifact = artifact_store.commit_artifact(
        _stage(claim, step).artifact.artifact_id,
        file_backend=managed_artifact_files,
        execution_fence=claim,
        observed_at=OBSERVED_AT + timedelta(seconds=3),
    )
    old_root = media_assets.project_video_localization_dir("project-1")
    new_root = old_root.with_name("renamed-project--project-1")
    old_root.rename(new_root)
    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO projects (project_id, data, updated_at)
            VALUES (
                'project-1',
                json_object(
                    'project_id', 'project-1',
                    'name', 'renamed project',
                    'parameters', json_object(?, ?)
                ),
                ?
            )
            """,
            (
                media_assets.PROJECT_DIR_NAME_KEY,
                new_root.name,
                OBSERVED_AT.isoformat(),
            ),
        )

    read = artifact_store.read_artifact(
        artifact.artifact_id,
        file_backend=managed_artifact_files,
    )

    assert read.content == _json_payload()
    assert new_root.joinpath(artifact.storage_key).is_file()


def test_project_delete_removes_artifact_metadata_before_step_rows(
    tmp_path: Path,
):
    claim, step = _setup(tmp_path)
    artifact = _stage(claim, step).artifact
    with database.conn() as connection:
        connection.execute(
            """
            CREATE TRIGGER require_artifact_delete_before_step
            BEFORE DELETE ON video_localization_operation_step_attempts
            WHEN EXISTS (
                SELECT 1
                FROM video_localization_operation_artifacts
                WHERE step_attempt_id = OLD.step_attempt_id
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'artifact metadata must be deleted first'
                );
            END
            """
        )

    operation_store.delete_project_with_projection("project-1")

    assert artifact_store.get_artifact(artifact.artifact_id) is None
    assert step_store.get_step_attempt(step.step_attempt_id) is None
