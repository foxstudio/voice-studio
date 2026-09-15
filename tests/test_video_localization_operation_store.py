from __future__ import annotations

import sys
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import operation_queue  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.services import database  # noqa: E402
from app.services import project_store  # noqa: E402
from app.services import video_localization_operation_attempt_store  # noqa: E402
from app.services import video_localization_operation_ledger_store  # noqa: E402
from app.services import video_localization_operation_store  # noqa: E402
from app.schemas.voice_studio import Project  # noqa: E402


def test_database_connection_has_explicit_busy_timeout(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")

    with database.conn() as connection:
        busy_timeout = connection.execute(
            "PRAGMA busy_timeout"
        ).fetchone()[0]

    assert busy_timeout == database.SQLITE_BUSY_TIMEOUT_MS


def test_generic_database_upsert_cannot_bypass_project_repository(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")

    with pytest.raises(
        ValueError,
        match="project_store repository CAS",
    ):
        database.upsert(
            "projects",
            "bypass-project",
            Project(
                project_id="bypass-project",
                name="Bypass",
            ).model_dump(mode="json"),
        )


def test_legacy_projects_table_adds_repository_revision_compatibly(
    tmp_path: Path,
):
    db_path = tmp_path / "voice_studio.db"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        """
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    payload = Project(
        project_id="legacy-project",
        name="Legacy",
    )
    legacy.execute(
        """
        INSERT INTO projects (project_id, data, updated_at)
        VALUES (?, ?, ?)
        """,
        (
            payload.project_id,
            payload.model_dump_json(),
            payload.updated_at,
        ),
    )
    legacy.commit()
    legacy.close()

    database.set_db_path(db_path)
    loaded = project_store.get_project(payload.project_id)

    assert loaded is not None
    assert loaded._repository_revision == 0
    loaded.description = "migrated"
    saved = project_store.save_project(
        loaded,
        touch_updated_at=False,
    )
    assert saved._repository_revision == 1
    with database.conn() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(projects)"
            ).fetchall()
        }
    assert "repository_revision" in columns


def test_stale_project_snapshot_cannot_replace_newer_project_payload(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_store.save_project(
        Project(project_id="project-cas", name="Initial"),
        touch_updated_at=False,
    )
    first = project_store.get_project("project-cas")
    stale = project_store.get_project("project-cas")
    assert first is not None
    assert stale is not None
    stale_updated_at = stale.updated_at

    first.name = "First commit"
    project_store.save_project(first)
    stale.name = "Stale commit"

    with pytest.raises(
        video_localization_operation_store.ProjectRevisionConflict
    ):
        project_store.save_project(stale)

    persisted = project_store.get_project("project-cas")
    assert persisted is not None
    assert persisted.name == "First commit"
    assert stale.updated_at == stale_updated_at


def test_project_reads_bind_repository_revision_without_serializing_it(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    created = project_store.save_project(
        Project(project_id="revision-project", name="Revision"),
        touch_updated_at=False,
    )

    loaded = project_store.get_project("revision-project")
    listed = project_store.list_projects()

    assert created._repository_revision == 1
    assert loaded is not None
    assert loaded._repository_revision == 1
    assert listed[0]._repository_revision == 1
    assert "_repository_revision" not in loaded.model_dump(mode="json")

    loaded.description = "second revision"
    project_store.save_project(loaded, touch_updated_at=False)

    assert loaded._repository_revision == 2
    assert (
        project_store.get_project("revision-project")
        ._repository_revision
        == 2
    )


def test_concurrent_project_snapshots_allow_exactly_one_commit(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_store.save_project(
        Project(project_id="project-race", name="Initial"),
        touch_updated_at=False,
    )
    contenders = [
        project_store.get_project("project-race"),
        project_store.get_project("project-race"),
    ]
    assert all(project is not None for project in contenders)
    barrier = threading.Barrier(3)
    outcomes: list[str] = []

    def commit(project: Project, name: str) -> None:
        project.name = name
        barrier.wait()
        try:
            project_store.save_project(
                project,
                touch_updated_at=False,
            )
        except (
            video_localization_operation_store
            .ProjectRevisionConflict
        ):
            outcomes.append("conflict")
        else:
            outcomes.append("saved")

    workers = [
        threading.Thread(
            target=commit,
            args=(contenders[0], "First contender"),
        ),
        threading.Thread(
            target=commit,
            args=(contenders[1], "Second contender"),
        ),
    ]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=2)

    assert all(not worker.is_alive() for worker in workers)
    assert sorted(outcomes) == ["conflict", "saved"]
    persisted = project_store.get_project("project-race")
    assert persisted is not None
    assert persisted.name in {
        "First contender",
        "Second contender",
    }
    assert persisted._repository_revision == 2


def test_unchanged_operation_feed_revision_does_not_parse_project_json(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO projects (project_id, data, updated_at)
            VALUES (?, ?, ?)
            """,
            ("project-unchanged", "malformed-json", "2026-07-30T00:00:00"),
        )
        connection.execute(
            """
            INSERT INTO video_localization_operation_projection_state (
                project_id,
                projection_revision
            )
            VALUES (?, ?)
            """,
            ("project-unchanged", 7),
        )

    result = video_localization_operation_store.read_project_operations(
        "project-unchanged",
        include_media=True,
        after_revision=7,
    )

    assert result.project_exists is True
    assert result.projection_revision == 7
    assert result.changed is False
    assert result.operations == ()
    assert result.source_media is None
    assert result.stems is None


def test_database_connection_keeps_one_path_during_global_path_switch(
    tmp_path: Path,
    monkeypatch,
):
    first_path = tmp_path / "first.db"
    next_path = tmp_path / "next.db"
    next_path.touch()
    database.set_db_path(first_path)
    original_connect = sqlite3.connect

    def connect_then_switch(path, *args, **kwargs):
        connection = original_connect(path, *args, **kwargs)
        database.set_db_path(next_path)
        return connection

    monkeypatch.setattr(database.sqlite3, "connect", connect_then_switch)

    with database.conn() as connection:
        first_tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    with database.conn() as connection:
        next_tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "settings" in first_tables
    assert "settings" in next_tables


def test_database_writer_waits_for_short_competing_transaction(
    tmp_path: Path,
):
    db_path = tmp_path / "voice_studio.db"
    database.set_db_path(db_path)
    with database.conn():
        pass

    blocker = sqlite3.connect(db_path)
    blocker.execute("BEGIN IMMEDIATE")
    attempted = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def write_project() -> None:
        attempted.set()
        try:
            with database.conn() as connection:
                connection.execute(
                    """
                    INSERT INTO projects (project_id, data, updated_at)
                    VALUES ('waited-project', '{}', '2026-07-30T00:00:00')
                    """
                )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            finished.set()

    writer = threading.Thread(target=write_project)
    writer.start()
    assert attempted.wait(timeout=1)
    assert not finished.wait(timeout=0.05)

    blocker.commit()
    blocker.close()
    writer.join(timeout=1)

    assert not writer.is_alive()
    assert errors == []
    with database.conn() as connection:
        assert connection.execute(
            """
            SELECT project_id
            FROM projects
            WHERE project_id = 'waited-project'
            """
        ).fetchone()["project_id"] == "waited-project"


def _operation(
    project_id: str,
    operation_id: str,
    *,
    kind: str = "english_asr",
    status: str = "success",
    cancel_requested: bool = False,
    created_at: str = "2026-07-30T00:00:00",
) -> dict:
    return VideoLocalizationOperation(
        project_id=project_id,
        operation_id=operation_id,
        kind=kind,
        status=status,
        cancel_requested=cancel_requested,
        created_at=created_at,
        result_summary={"stage": f"stage-{operation_id}"},
    ).model_dump(mode="json")


def _save_project(
    project_id: str,
    operations: list[dict],
    *,
    source_media: dict | None = None,
    stems: dict | None = None,
    extra_draft_fields: dict | None = None,
) -> None:
    localization = {
        "operations": operations,
        "source_media": source_media or {},
        "stems": stems or {},
        **(extra_draft_fields or {}),
    }
    payload = Project(
        project_id=project_id,
        name=project_id,
        default_engine_id=None,
        parameters={"video_localization": localization},
        created_at="2026-07-30T00:00:00",
        updated_at="2026-07-30T00:00:00",
    )
    with database.conn() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO projects (
                project_id,
                data,
                updated_at
            )
            VALUES (?, ?, ?)
            """,
            (
                project_id,
                payload.model_dump_json(),
                payload.updated_at,
            ),
        )


def test_operation_reads_project_only_the_authoritative_json_slice(
    tmp_path: Path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "project_slice"
    source_path = tmp_path / "source.wav"
    source_path.write_bytes(b"audio")
    older = _operation(
        project_id,
        "older",
        created_at="2026-07-30T00:00:00",
    )
    source = _operation(
        project_id,
        "source",
        kind="source_audio",
        created_at="2026-07-30T00:00:01",
    )
    _save_project(
        project_id,
        [older, source],
        source_media={
            "audio_path": str(source_path),
            "duration_ms": 1234,
            "metadata": {
                "audio_extract_status": "completed",
                "audio_sample_rate": 48000,
                "audio_channels": 2,
            },
        },
        extra_draft_fields={
            # The operation projection must not parse unrelated Draft fields.
            "cues": "intentionally-invalid",
        },
    )

    def unexpected_full_draft_read(_project_id: str):
        raise AssertionError("operation reads must not load the full Draft")

    monkeypatch.setattr(
        operation_queue.service,
        "get_video_localization",
        unexpected_full_draft_read,
    )

    operations = operation_queue.list_operations(project_id)
    selected = operation_queue.get_operation(project_id, "older")

    assert operations is not None
    assert [item.operation_id for item in operations] == ["source", "older"]
    assert selected is not None
    assert selected.operation_id == "older"


def test_project_scoped_operation_lookup_does_not_scan_projects(
    tmp_path: Path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    for index in range(1_000):
        project_id = f"project_{index:04d}"
        operations = (
            [_operation(project_id, "oldest-operation")]
            if index == 0
            else []
        )
        _save_project(project_id, operations)

    def unexpected_project_scan():
        raise AssertionError("operation lookup must not list every project")

    monkeypatch.setattr(
        operation_queue.project_store,
        "list_projects",
        unexpected_project_scan,
    )

    operation = operation_queue.get_operation(
        "project_0000",
        "oldest-operation"
    )

    assert operation is not None
    assert operation.operation_id == "oldest-operation"


def test_explicit_legacy_backfill_is_idempotent(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    _save_project(
        "legacy",
        [_operation("legacy", "legacy-operation")],
    )

    video_localization_operation_store.backfill_legacy_projects()
    video_localization_operation_store.backfill_legacy_projects()

    with database.conn() as connection:
        state_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM video_localization_operation_projection_state
            WHERE project_id = 'legacy'
            """
        ).fetchone()[0]
        operation_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM video_localization_operations
            WHERE project_id = 'legacy'
            """
        ).fetchone()[0]

    assert state_count == 1
    assert operation_count == 1


def test_database_migrates_legacy_projection_state_schema(
    tmp_path: Path,
):
    db_path = tmp_path / "voice_studio.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE video_localization_operation_index_state (
                project_id TEXT PRIMARY KEY
            )
            """
        )
        connection.execute(
            """
            INSERT INTO video_localization_operation_index_state (project_id)
            VALUES ('legacy')
            """
        )
    database.set_db_path(db_path)

    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT projection_revision
            FROM video_localization_operation_projection_state
            WHERE project_id = 'legacy'
            """
        ).fetchone()
        legacy_tables = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name IN (
                  'video_localization_operation_index',
                  'video_localization_operation_index_state'
              )
            """
        ).fetchall()

    assert row["projection_revision"] == 0
    assert legacy_tables == []


def test_database_retires_locator_and_preserves_projection_revision(
    tmp_path: Path,
):
    db_path = tmp_path / "voice_studio.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE video_localization_operation_projection_state (
                project_id TEXT PRIMARY KEY,
                projection_revision INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE video_localization_operation_index (
                project_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (project_id, operation_id)
            );
            CREATE TABLE video_localization_operation_index_state (
                project_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL DEFAULT 'locator-v1',
                projection_revision INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO video_localization_operation_index VALUES (
                'legacy',
                'operation-1',
                '{}',
                '2026-07-30T00:00:00',
                'success',
                0
            );
            INSERT INTO video_localization_operation_index_state VALUES (
                'legacy',
                'locator-v1',
                7
            );
            INSERT INTO video_localization_operation_projection_state VALUES (
                'legacy',
                3
            );
            """
        )
    database.set_db_path(db_path)

    with database.conn() as connection:
        revision = connection.execute(
            """
            SELECT projection_revision
            FROM video_localization_operation_projection_state
            WHERE project_id = 'legacy'
            """
        ).fetchone()
        legacy_tables = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name LIKE 'video_localization_operation_index%'
            """
        ).fetchall()

    assert revision["projection_revision"] == 7
    assert legacy_tables == []


def test_recovery_query_returns_only_projects_with_recoverable_operations(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    _save_project(
        "completed",
        [_operation("completed", "done", status="success")],
    )
    _save_project(
        "queued",
        [_operation("queued", "queued-op", status="queued")],
    )
    _save_project(
        "running",
        [_operation("running", "running-op", status="running")],
    )
    _save_project(
        "cancel-requested",
        [
            _operation(
                "cancel-requested",
                "cancel-op",
                status="running",
                cancel_requested=True,
            )
        ],
    )
    _save_project(
        "terminal-cancelled",
        [
            _operation(
                "terminal-cancelled",
                "terminal-cancelled-op",
                status="cancelled",
                cancel_requested=True,
            )
        ],
    )
    video_localization_operation_store.backfill_legacy_projects()

    project_ids = (
        video_localization_operation_ledger_store
        .list_recoverable_project_ids(
            {"queued", "running"}
        )
    )

    assert project_ids == ["cancel-requested", "queued", "running"]


def test_recovery_loads_only_recoverable_projects_without_global_scan(
    tmp_path: Path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    completed = _operation("completed", "done", status="success")
    queued = _operation("queued", "queued-op", status="queued")
    terminal_cancelled = _operation(
        "queued",
        "terminal-cancelled-op",
        status="cancelled",
        cancel_requested=True,
    )
    running = _operation("running", "running-op", status="running")
    cancelled = _operation(
        "cancel-requested",
        "cancel-op",
        status="running",
        cancel_requested=True,
    )
    _save_project("completed", [completed])
    _save_project("queued", [queued, terminal_cancelled])
    _save_project("running", [running])
    _save_project("cancel-requested", [cancelled])
    by_project = {
        "completed": VideoLocalizationDraft.model_validate(
            {"operations": [completed]}
        ),
        "queued": VideoLocalizationDraft.model_validate(
            {"operations": [queued, terminal_cancelled]}
        ),
        "running": VideoLocalizationDraft.model_validate(
            {"operations": [running]}
        ),
        "cancel-requested": VideoLocalizationDraft.model_validate(
            {"operations": [cancelled]}
        ),
    }
    loaded: list[str] = []
    saved: dict[str, VideoLocalizationDraft] = {}
    enqueued: list[str] = []

    def load(project_id: str):
        loaded.append(project_id)
        return by_project[project_id].model_copy(deep=True)

    def save(project_id: str, update, **_kwargs):
        saved[project_id] = update(
            by_project[project_id].model_copy(deep=True)
        )
        return saved[project_id]

    monkeypatch.setattr(
        operation_queue.project_store,
        "list_projects",
        lambda: (_ for _ in ()).throw(
            AssertionError("recovery must not scan every project")
        ),
    )
    monkeypatch.setattr(
        operation_queue,
        "list_operations",
        lambda project_id: list(load(project_id).operations),
    )
    monkeypatch.setattr(
        operation_queue.service,
        "update_video_localization_atomic",
        save,
    )
    monkeypatch.setattr(operation_queue, "_enqueue", enqueued.append)
    video_localization_operation_store.backfill_legacy_projects()

    operation_queue._recover_active_operations()

    assert loaded == ["cancel-requested", "queued", "running"]
    assert "completed" not in saved
    assert saved["cancel-requested"].operations[0].status == "cancelled"
    interrupted = saved["running"].operations[0]
    assert interrupted.status == "failed"
    assert (
        interrupted.error_code
        == "VIDEO_LOCALIZATION_OPERATION_INTERRUPTED"
    )
    assert interrupted.result_summary["interrupted"] is True
    assert (
        video_localization_operation_attempt_store.list_attempts(
            "running",
            "running-op"
        )[-1].status
        == "failed"
    )
    assert "queued" not in saved
    assert enqueued == [("queued", "queued-op")]


def test_recovery_preserves_foreign_live_lease_then_interrupts(
    tmp_path: Path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "running"
    operation_id = "running-op"
    running = _operation(
        project_id,
        operation_id,
        status="running",
    )
    _save_project(project_id, [running])
    video_localization_operation_store.backfill_legacy_projects()
    observed_at = datetime.now(timezone.utc)
    claim = video_localization_operation_attempt_store.claim_attempt(
        project_id,
        operation_id,
        runner_id="foreign-runner",
        observed_at=observed_at,
        lease_duration=timedelta(seconds=60),
    ).attempt
    scheduled: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        operation_queue,
        "_schedule_operation_recovery",
        lambda scheduled_project_id, scheduled_operation_id, *, deadline_ms: (
            scheduled.append(
                (
                    scheduled_project_id,
                    scheduled_operation_id,
                    deadline_ms,
                )
            )
        ),
    )
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _operation_id: None,
    )

    operation_queue._recover_project_operations(project_id)

    preserved = operation_queue.get_operation(project_id, operation_id)
    assert preserved is not None
    assert preserved.status == "running"
    assert scheduled == [
        (project_id, operation_id, claim.lease_expires_at_ms)
    ]

    assert video_localization_operation_attempt_store.finish_claim(
        claim.attempt_id,
        project_id,
        operation_id,
        runner_id=claim.runner_id,
        fencing_token=claim.fencing_token,
        status="interrupted",
        observed_at=observed_at + timedelta(seconds=1),
    )
    operation_queue._recover_project_operations(project_id)

    interrupted = operation_queue.get_operation(
        project_id,
        operation_id,
    )
    assert interrupted is not None
    assert interrupted.status == "failed"
    assert interrupted.error_code == (
        "VIDEO_LOCALIZATION_OPERATION_INTERRUPTED"
    )
    latest_attempt = (
        video_localization_operation_attempt_store.list_attempts(
            project_id,
            operation_id
        )[-1]
    )
    assert latest_attempt.status == "failed"
    assert latest_attempt.fencing_token > claim.fencing_token


def test_recovery_does_not_mutate_when_lease_read_is_uncertain(
    tmp_path: Path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "running"
    operation_id = "running-op"
    _save_project(
        project_id,
        [_operation(project_id, operation_id, status="running")],
    )
    scheduled: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        operation_queue.video_localization_operation_execution,
        "acquire_execution_claim",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("database unavailable")
        ),
    )
    monkeypatch.setattr(
        operation_queue,
        "_schedule_operation_recovery",
        lambda scheduled_project_id, scheduled_operation_id, *, deadline_ms: (
            scheduled.append(
                (
                    scheduled_project_id,
                    scheduled_operation_id,
                    deadline_ms,
                )
            )
        ),
    )

    operation_queue._recover_project_operations(project_id)

    preserved = operation_queue.get_operation(project_id, operation_id)
    assert preserved is not None
    assert preserved.status == "running"
    assert len(scheduled) == 1
    assert scheduled[0][:2] == (project_id, operation_id)


def test_operation_store_distinguishes_missing_project_from_empty_history(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    _save_project("empty", [])

    empty = video_localization_operation_store.read_project_operations(
        "empty"
    )
    missing = video_localization_operation_store.read_project_operations(
        "missing"
    )
    empty_exists, empty_operation = (
        video_localization_operation_store.read_operation(
            "empty",
            "unknown",
        )
    )

    assert empty.project_exists is True
    assert empty.operations == ()
    assert missing.project_exists is False
    assert empty_exists is True
    assert empty_operation is None


def test_project_save_and_delete_keep_ledger_and_revision_in_sync(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "project_transaction"
    first = _operation(project_id, "first")
    second = _operation(project_id, "second")
    project = Project(
        project_id=project_id,
        name="projection",
        parameters={
            "video_localization": {
                "operations": [first],
            }
        },
    )

    project_store.save_project(project)
    first_exists, first_payload = (
        video_localization_operation_store.read_operation(
            project_id,
            "first",
        )
    )
    first_revision = (
        video_localization_operation_store.project_revision(project_id)
    )
    project.parameters["video_localization"]["operations"] = [second]
    project_store.save_project(project, touch_updated_at=False)
    _, removed_payload = video_localization_operation_store.read_operation(
        project_id,
        "first",
    )
    _, second_payload = video_localization_operation_store.read_operation(
        project_id,
        "second",
    )
    second_revision = (
        video_localization_operation_store.project_revision(project_id)
    )
    video_localization_operation_attempt_store.begin_attempt(
        project_id,
        "second",
        runner_id="runner-1",
        started_at="2026-07-30T00:00:01",
    )
    project_store.delete_project(project_id)
    deleted = video_localization_operation_store.read_project_operations(
        project_id
    )
    deleted_revision = (
        video_localization_operation_store.project_revision(project_id)
    )

    assert first_exists is True
    assert first_payload is not None
    assert first_revision == 1
    assert removed_payload is None
    assert second_payload is not None
    assert second_revision == 2
    assert deleted.project_exists is False
    assert deleted_revision == 0
    assert (
        video_localization_operation_attempt_store.list_attempts(
            project_id,
            "second",
        )
        == []
    )


def test_project_and_operation_projection_rollback_together(
    tmp_path: Path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "project_rollback"
    first = _operation(project_id, "first")
    second = _operation(project_id, "second")
    project = Project(
        project_id=project_id,
        name="before",
        parameters={
            "video_localization": {
                "operations": [first],
            }
        },
    )
    project_store.save_project(project)
    project.name = "after"
    project.parameters["video_localization"]["operations"] = [second]

    def fail_projection(*_args, **_kwargs):
        raise RuntimeError("projection failed")

    monkeypatch.setattr(
        video_localization_operation_store,
        "_write_project_revision",
        fail_projection,
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        project_store.save_project(project)

    stored = project_store.get_project(project_id)
    first_exists, first_payload = (
        video_localization_operation_store.read_operation(
            project_id,
            "first",
        )
    )
    _, second_payload = video_localization_operation_store.read_operation(
        project_id,
        "second",
    )

    assert stored is not None
    assert stored.name == "before"
    assert first_exists is True
    assert first_payload is not None
    assert second_payload is None
