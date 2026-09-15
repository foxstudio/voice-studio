from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    draft_store,
    operation_queue,
    operation_summary_legacy,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.errors import AppException  # noqa: E402
from app.schemas.video_localization_operation_summary import (  # noqa: E402
    SUMMARY_CORE_SCHEMA_VERSION,
)
from app.schemas.voice_studio import AppSettings, Project  # noqa: E402
from app.services import (  # noqa: E402
    database,
    project_store,
    settings_store,
)
from app.services import video_localization_operations  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_summary_migration,
)
from app.services import (  # noqa: E402
    video_localization_operation_summary_store,
)


@pytest.fixture
def isolated_database(tmp_path: Path):
    original_path = database.DB_PATH
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
    video_localization_operations._operation_feed_reader.clear()
    try:
        yield tmp_path
    finally:
        video_localization_operations._operation_feed_reader.clear()
        database.set_db_path(original_path)


def _open_migration_window() -> None:
    with database.conn() as connection:
        connection.execute(
            """
            DELETE FROM
                video_localization_operation_summary_authority
            """
        )


def _operation(
    project_id: str,
    operation_id: str,
) -> VideoLocalizationOperation:
    return VideoLocalizationOperation(
        operation_id=operation_id,
        project_id=project_id,
        kind="source_audio",
        status="success",
        progress=1,
        result_summary={"duration_ms": 1_000},
        created_at="2026-07-30T01:00:00+00:00",
        completed_at="2026-07-30T01:00:01+00:00",
    )


def _project(
    project_id: str,
    *,
    operations: list[VideoLocalizationOperation] | None,
) -> Project:
    parameters = (
        {}
        if operations is None
        else {
            "video_localization": VideoLocalizationDraft(
                operations=operations,
                updated_at="2026-07-30T01:01:00+00:00",
            ).model_dump(mode="json")
        }
    )
    return Project(
        project_id=project_id,
        name=project_id,
        parameters=parameters,
        updated_at="2026-07-30T02:00:00+00:00",
    )


def _insert_project(project: Project) -> None:
    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO projects (project_id, data, updated_at)
            VALUES (?, ?, ?)
            """,
            (
                project.project_id,
                json.dumps(
                    project.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
                project.updated_at,
            ),
        )


def _backfill_and_promote() -> None:
    backfill = (
        video_localization_operation_summary_migration
        .backfill_operation_summaries(
            operation_summary_legacy.decode_legacy_summary_source,
            limit=100,
        )
    )
    assert backfill.repair_required_project_count == 0
    promotion = (
        video_localization_operation_summary_migration
        .promote_operation_summaries(
            operation_summary_legacy.decode_legacy_summary_source,
            limit=100,
        )
    )
    assert promotion.rejected_project_count == 0


def _close_authority():
    return (
        video_localization_operation_summary_migration
        .close_operation_summary_authority(
            operation_summary_legacy.decode_legacy_summary_source,
            limit=100,
        )
    )


def test_empty_database_starts_with_summary_authority_closed(
    isolated_database: Path,
):
    authority = (
        video_localization_operation_summary_store
        .read_summary_authority()
    )

    assert authority is not None
    assert authority.summary_schema_version == SUMMARY_CORE_SCHEMA_VERSION
    assert authority.closed_at


def test_empty_database_bootstrap_leaves_connection_ready_for_transaction(
    tmp_path: Path,
):
    original_path = database.DB_PATH
    database.set_db_path(tmp_path / "bootstrap-transaction.db")
    try:
        with database.conn() as connection:
            assert connection.in_transaction is False
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
    finally:
        database.set_db_path(original_path)


def test_authority_close_is_global_atomic_idempotent_and_preserves_project_time(
    isolated_database: Path,
):
    _open_migration_window()
    project_id = "authority-project"
    _insert_project(
        _project(
            project_id,
            operations=[_operation(project_id, "operation-1")],
        )
    )
    _insert_project(_project("non-localization", operations=None))
    _backfill_and_promote()

    first = _close_authority()
    second = _close_authority()
    state = (
        video_localization_operation_summary_store
        .read_projection_state(project_id)
    )
    authority = (
        video_localization_operation_summary_store
        .read_summary_authority()
    )
    with database.conn() as connection:
        updated_at = str(
            connection.execute(
                """
                SELECT updated_at
                FROM projects
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()["updated_at"]
        )

    assert first.closed is True
    assert first.changed is True
    assert first.authoritative_project_count == 1
    assert first.skipped_project_count == 1
    assert first.rejected_project_count == 0
    assert second.closed is True
    assert second.changed is False
    assert second.unchanged_project_count == 1
    assert state is not None
    assert state.summary_status == "authoritative"
    assert authority is not None
    assert updated_at == "2026-07-30T02:00:00+00:00"


def test_authority_close_rolls_back_every_project_when_one_projection_is_corrupt(
    isolated_database: Path,
):
    _open_migration_window()
    for suffix in ("a", "b"):
        project_id = f"authority-{suffix}"
        _insert_project(
            _project(
                project_id,
                operations=[
                    _operation(project_id, f"operation-{suffix}")
                ],
            )
        )
    _backfill_and_promote()
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_summaries
            SET content_fingerprint = 'corrupt'
            WHERE project_id = 'authority-b'
            """
        )

    report = _close_authority()
    states = {
        project_id: (
            video_localization_operation_summary_store
            .read_projection_state(project_id)
        )
        for project_id in ("authority-a", "authority-b")
    }

    assert report.closed is False
    assert report.changed is False
    assert report.rejected_project_count == 1
    assert (
        video_localization_operation_summary_store
        .read_summary_authority()
        is None
    )
    assert {
        state.summary_status for state in states.values()
        if state is not None
    } == {"verified"}


def test_unverified_runtime_feed_fails_without_using_legacy_reader(
    isolated_database: Path,
    monkeypatch,
):
    _open_migration_window()
    project_id = "shadow-project"
    _insert_project(
        _project(
            project_id,
            operations=[_operation(project_id, "operation-1")],
        )
    )
    backfill = (
        video_localization_operation_summary_migration
        .backfill_operation_summaries(
            operation_summary_legacy.decode_legacy_summary_source,
            limit=100,
        )
    )
    assert backfill.backfilled_project_count == 1

    with pytest.raises(AppException) as captured:
        video_localization_operations.read_operation_feed_v2(
            project_id
        )

    assert captured.value.status_code == 409
    assert captured.value.code == (
        "VIDEO_LOCALIZATION_OPERATION_SUMMARY_AUTHORITY_NOT_CLOSED"
    )


def test_closed_authority_keeps_new_project_projection_authoritative(
    isolated_database: Path,
):
    project_id = "new-authoritative-project"
    project_store.save_project(
        Project(
            project_id=project_id,
            name=project_id,
        )
    )
    saved = draft_store.save(
        project_id,
        VideoLocalizationDraft(
            operations=[_operation(project_id, "operation-1")]
        ),
        intent="runtime",
    )
    state = (
        video_localization_operation_summary_store
        .read_projection_state(project_id)
    )
    feed = video_localization_operations.read_operation_feed_v2(
        project_id
    )

    assert saved is not None
    assert state is not None
    assert state.summary_status == "authoritative"
    assert feed is not None
    assert [item.operation_id for item in feed.history] == [
        "operation-1"
    ]


def test_authority_close_refuses_a_truncated_global_scan(
    isolated_database: Path,
):
    _open_migration_window()
    for suffix in ("a", "b"):
        project_id = f"truncated-{suffix}"
        _insert_project(
            _project(
                project_id,
                operations=[
                    _operation(project_id, f"operation-{suffix}")
                ],
            )
        )
    _backfill_and_promote()

    report = (
        video_localization_operation_summary_migration
        .close_operation_summary_authority(
            operation_summary_legacy.decode_legacy_summary_source,
            limit=1,
        )
    )

    assert report.closed is False
    assert report.changed is False
    assert report.truncated is True
    assert report.scanned_project_count == 1
    assert (
        video_localization_operation_summary_store
        .read_summary_authority()
        is None
    )
    assert {
        video_localization_operation_summary_store
        .read_projection_state(f"truncated-{suffix}")
        .summary_status
        for suffix in ("a", "b")
    } == {"verified"}


def test_authority_close_rolls_back_if_marker_commit_fails(
    isolated_database: Path,
    monkeypatch,
):
    _open_migration_window()
    project_id = "marker-failure"
    _insert_project(
        _project(
            project_id,
            operations=[_operation(project_id, "operation-1")],
        )
    )
    _backfill_and_promote()

    monkeypatch.setattr(
        video_localization_operation_summary_store,
        "mark_summary_authority_closed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected marker failure")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="injected marker failure",
    ):
        _close_authority()

    state = (
        video_localization_operation_summary_store
        .read_projection_state(project_id)
    )
    assert state is not None
    assert state.summary_status == "verified"
    assert (
        video_localization_operation_summary_store
        .read_summary_authority()
        is None
    )


def test_authority_close_fails_cleanly_while_database_is_busy(
    isolated_database: Path,
    monkeypatch,
):
    _open_migration_window()
    project_id = "busy-close"
    _insert_project(
        _project(
            project_id,
            operations=[_operation(project_id, "operation-1")],
        )
    )
    _backfill_and_promote()
    monkeypatch.setattr(database, "SQLITE_BUSY_TIMEOUT_MS", 50)
    blocker = sqlite3.connect(database.DB_PATH, timeout=0)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError):
            _close_authority()
    finally:
        blocker.rollback()
        blocker.close()

    state = (
        video_localization_operation_summary_store
        .read_projection_state(project_id)
    )
    assert state is not None
    assert state.summary_status == "verified"
    assert (
        video_localization_operation_summary_store
        .read_summary_authority()
        is None
    )


def test_authority_close_serializes_a_concurrent_runtime_writer(
    isolated_database: Path,
    monkeypatch,
):
    _open_migration_window()
    project_id = "authority-writer"
    operation = _operation(project_id, "operation-1")
    _insert_project(_project(project_id, operations=[operation]))
    _backfill_and_promote()
    close_entered = threading.Event()
    release_close = threading.Event()
    writer_completed = threading.Event()
    close_results = []
    writer_results = []
    failures: list[BaseException] = []
    original_close = (
        video_localization_operation_summary_store
        .close_project_projection_authority
    )

    def blocking_close(*args, **kwargs):
        close_entered.set()
        assert release_close.wait(timeout=3)
        return original_close(*args, **kwargs)

    monkeypatch.setattr(
        video_localization_operation_summary_store,
        "close_project_projection_authority",
        blocking_close,
    )

    def close() -> None:
        try:
            close_results.append(_close_authority())
        except BaseException as exc:
            failures.append(exc)

    def write() -> None:
        try:
            writer_results.append(
                draft_store.save(
                    project_id,
                    VideoLocalizationDraft(
                        operations=[
                            operation.model_copy(
                                update={
                                    "result_summary": {
                                        "duration_ms": 2_000
                                    }
                                }
                            )
                        ]
                    ),
                    intent="runtime",
                )
            )
        except BaseException as exc:
            failures.append(exc)
        finally:
            writer_completed.set()

    close_thread = threading.Thread(target=close)
    writer_thread = threading.Thread(target=write)
    close_thread.start()
    assert close_entered.wait(timeout=3)
    writer_thread.start()
    time.sleep(0.05)
    assert writer_completed.is_set() is False
    release_close.set()
    close_thread.join(timeout=3)
    writer_thread.join(timeout=3)

    assert failures == []
    assert close_results[0].closed is True
    assert writer_results[0] is not None
    state = (
        video_localization_operation_summary_store
        .read_projection_state(project_id)
    )
    assert state is not None
    assert state.summary_status == "authoritative"


def test_closed_reader_rejects_rows_without_projection_state_before_304(
    isolated_database: Path,
):
    project_id = "orphaned-authority"
    project_store.save_project(
        Project(project_id=project_id, name=project_id)
    )
    draft_store.save(
        project_id,
        VideoLocalizationDraft(
            operations=[_operation(project_id, "operation-1")]
        ),
        intent="runtime",
    )
    with database.conn() as connection:
        connection.execute(
            """
            DELETE FROM
                video_localization_operation_projection_state
            WHERE project_id = ?
            """,
            (project_id,),
        )

    with pytest.raises(AppException) as captured:
        video_localization_operations.read_operation_feed_v2(
            project_id,
            after_revision=0,
        )

    assert captured.value.code == (
        "VIDEO_LOCALIZATION_OPERATION_SUMMARY_REPAIR_REQUIRED"
    )


def test_preexisting_empty_database_is_closed_on_schema_initialization(
    tmp_path: Path,
):
    original_path = database.DB_PATH
    db_path = tmp_path / "preexisting-empty.db"
    db_path.touch()
    database.set_db_path(db_path)
    try:
        authority = (
            video_localization_operation_summary_store
            .read_summary_authority()
        )
    finally:
        database.set_db_path(original_path)

    assert authority is not None
    assert authority.summary_schema_version == SUMMARY_CORE_SCHEMA_VERSION


def test_authority_close_cli_is_machine_readable_and_idempotent(
    isolated_database: Path,
):
    _open_migration_window()
    project_id = "authority-cli"
    _insert_project(
        _project(
            project_id,
            operations=[_operation(project_id, "operation-1")],
        )
    )
    _backfill_and_promote()
    command = [
        sys.executable,
        str(
            ROOT
            / "scripts"
            / "close_video_localization_operation_summary_authority.py"
        ),
        "--limit",
        "100",
        "--format",
        "json",
    ]
    environment = {
        **os.environ,
        "VOICE_STUDIO_DB_PATH": str(database.DB_PATH),
    }

    first = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    second = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)

    assert first.returncode == 0
    assert first_payload["closed"] is True
    assert first_payload["changed"] is True
    assert second.returncode == 0
    assert second_payload["closed"] is True
    assert second_payload["changed"] is False
