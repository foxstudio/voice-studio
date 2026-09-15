from __future__ import annotations

import json
import os
import subprocess
import sys
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import operation_queue  # noqa: E402
from app.domains.video_localization import service  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.voice_studio import Project  # noqa: E402
from app.services import database  # noqa: E402
from app.services import project_store  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_ledger_audit as ledger_audit,
)
from app.services import (  # noqa: E402
    video_localization_operation_store as operation_store,
)


def _project(
    project_id: str,
    operations: list[VideoLocalizationOperation],
    *,
    source_video: str | None = None,
) -> Project:
    return Project(
        project_id=project_id,
        name=project_id,
        parameters={
            "video_localization": VideoLocalizationDraft(
                source_media=(
                    {
                        "filename": Path(source_video).name,
                        "video_path": source_video,
                        "duration_ms": 1_000,
                    }
                    if source_video
                    else {}
                ),
                operations=operations,
            ).model_dump(mode="json"),
        },
    )


def _save(
    project_id: str,
    operations: list[VideoLocalizationOperation],
    *,
    source_video: str | None = None,
) -> Project:
    project = _project(
        project_id,
        operations,
        source_video=source_video,
    )
    current = project_store.get_project(project_id)
    if current is not None:
        project._repository_revision = current._repository_revision
    return project_store.save_project(
        project,
        touch_updated_at=False,
    )


def test_existing_projection_state_receives_revision_and_ledger_tables(
    tmp_path: Path,
):
    db_path = tmp_path / "voice_studio.db"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        """
        CREATE TABLE video_localization_operation_index_state (
            project_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL DEFAULT 'locator-v1'
        )
        """
    )
    legacy.execute(
        """
        INSERT INTO video_localization_operation_index_state (
            project_id,
            schema_version
        ) VALUES ('project-1', 'locator-v1')
        """
    )
    legacy.commit()
    legacy.close()
    database.set_db_path(db_path)

    with database.conn() as connection:
        state_columns = {
            str(row["name"])
            for row in connection.execute(
                """
                PRAGMA table_info(
                    video_localization_operation_projection_state
                )
                """
            ).fetchall()
        }
        tables = {
            str(row["name"])
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            ).fetchall()
        }
        active_index_sql = str(
            connection.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'index'
                  AND name = ?
                """,
                (
                    "idx_video_localization_ledger_active_kind",
                ),
            ).fetchone()["sql"]
        )

    assert "projection_revision" in state_columns
    assert "video_localization_operation_index_state" not in tables
    assert "video_localization_operation_index" not in tables
    assert "video_localization_operations" in tables
    assert "video_localization_operation_outbox" in tables
    assert "origin = 'command'" in active_index_sql


def test_global_operation_id_schema_migrates_to_project_scope(
    tmp_path: Path,
):
    db_path = tmp_path / "voice_studio.db"
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE video_localization_operations (
            operation_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            command_revision INTEGER NOT NULL DEFAULT 0,
            state_revision INTEGER NOT NULL DEFAULT 0,
            parameters_fingerprint TEXT NOT NULL,
            workflow_version TEXT NOT NULL,
            origin TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            last_command_id TEXT
        );
        CREATE TABLE video_localization_operation_outbox (
            event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL UNIQUE,
            project_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            source_operation_id TEXT,
            command_type TEXT NOT NULL,
            command_revision INTEGER NOT NULL,
            payload_version TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            applied_at TEXT,
            UNIQUE (operation_id, command_revision)
        );
        INSERT INTO video_localization_operations VALUES (
            'shared-operation',
            'project-1',
            'source_audio',
            'success',
            0,
            1,
            2,
            'fingerprint',
            'operation-v1',
            'command',
            '2026-07-30T01:00:00Z',
            '2026-07-30T01:01:00Z',
            '2026-07-30T01:01:00Z',
            'command-1'
        );
        INSERT INTO video_localization_operation_outbox VALUES (
            'event-1',
            'command-1',
            'project-1',
            'shared-operation',
            NULL,
            'submit',
            1,
            'operation-command-v1',
            'applied',
            '2026-07-30T01:00:00Z',
            '2026-07-30T01:00:00Z'
        );
        """
    )
    legacy.commit()
    legacy.close()
    database.set_db_path(db_path)

    with database.conn() as connection:
        primary_key = {
            str(row["name"]): int(row["pk"])
            for row in connection.execute(
                """
                PRAGMA table_info(
                    video_localization_operations
                )
                """
            ).fetchall()
            if int(row["pk"])
        }
        outbox_sql = "".join(
            str(
                connection.execute(
                    """
                    SELECT sql
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name = ?
                    """,
                    (
                        "video_localization_operation_outbox",
                    ),
                ).fetchone()["sql"]
            ).lower().split()
        )
        connection.execute(
            """
            INSERT INTO video_localization_operations (
                operation_id,
                project_id,
                kind,
                status,
                parameters_fingerprint,
                workflow_version,
                origin,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "shared-operation",
                "project-2",
                "source_audio",
                "success",
                "fingerprint",
                "operation-v1",
                "legacy_project",
                "2026-07-30T01:00:00Z",
                "2026-07-30T01:01:00Z",
            ),
        )

    assert primary_key == {
        "project_id": 1,
        "operation_id": 2,
    }
    assert (
        "unique(project_id,operation_id,command_revision)"
        in outbox_sql
    )
    migrated = ledger_store.get_operation(
        "project-1",
        "shared-operation",
    )
    assert migrated is not None
    assert migrated.last_command_id == "command-1"
    assert ledger_store.list_outbox(
        "project-1",
        "shared-operation",
    )[0].status == "applied"
    assert ledger_store.get_operation(
        "project-2",
        "shared-operation",
    ) is not None


def test_startup_backfill_scopes_cloned_legacy_operations_to_project(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    shared_operation = VideoLocalizationOperation(
        operation_id="shared-operation",
        project_id="project-1",
        kind="source_audio",
        status="success",
    )
    projects = [
        _project("project-1", [shared_operation]),
        _project("project-2", [shared_operation]),
    ]
    with database.conn() as connection:
        connection.executemany(
            """
            INSERT INTO projects (project_id, data, updated_at)
            VALUES (?, ?, ?)
            """,
            [
                (
                    project.project_id,
                    json.dumps(
                        project.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                    project.updated_at,
                )
                for project in projects
            ],
        )

    operation_store.backfill_legacy_projects()

    assert ledger_store.get_operation(
        "project-1",
        "shared-operation",
    ) is not None
    assert ledger_store.get_operation(
        "project-2",
        "shared-operation",
    ) is not None
    cloned = operation_store.read_project_mirror_operation(
        "project-2",
        "shared-operation",
    )[1]
    assert cloned is not None
    assert cloned["project_id"] == "project-2"
    report = ledger_audit.reconcile_operation_ledger()
    assert report.total_ledger_count == 2
    assert report.total_mirror_count == 2
    assert report.healthy is True


def test_cloned_operation_state_writes_are_isolated_by_project(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    shared_id = "shared-operation"
    first = VideoLocalizationOperation(
        operation_id=shared_id,
        project_id="project-1",
        kind="source_audio",
        status="queued",
    )
    second = first.model_copy(
        update={"project_id": "project-2"}
    )
    _save("project-1", [first])
    _save("project-2", [second])

    _save(
        "project-1",
        [first.model_copy(update={"status": "running"})],
    )

    first_running = ledger_store.get_operation(
        "project-1",
        shared_id,
    )
    second_queued = ledger_store.get_operation(
        "project-2",
        shared_id,
    )
    assert first_running is not None
    assert first_running.status == "running"
    assert second_queued is not None
    assert second_queued.status == "queued"

    project = project_store.get_project("project-1")
    assert project is not None
    cancelled = first.model_copy(
        update={
            "status": "cancelled",
            "cancel_requested": True,
            "completed_at": "2026-07-30T01:00:00Z",
        }
    )
    project.parameters["video_localization"] = (
        VideoLocalizationDraft(
            operations=[cancelled]
        ).model_dump(mode="json")
    )
    command = ledger_store.command_from_operation(
        "cancel",
        cancelled.model_dump(mode="json"),
        expected_project_revision=(
            operation_store.project_revision("project-1")
        ),
    )

    project_store.save_project(
        project,
        touch_updated_at=False,
        operation_command=command,
    )

    first_cancelled = ledger_store.get_operation(
        "project-1",
        shared_id,
    )
    second_still_queued = ledger_store.get_operation(
        "project-2",
        shared_id,
    )
    assert first_cancelled is not None
    assert first_cancelled.status == "cancelled"
    assert first_cancelled.command_revision == 1
    assert second_still_queued is not None
    assert second_still_queued.status == "queued"
    assert second_still_queued.command_revision == 0


def test_project_save_backfills_legacy_ledger_and_revisions(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="english_asr",
        status="running",
        parameters={"profile_id": "profile-1"},
    )

    _save("project-1", [operation])

    entry = ledger_store.get_operation(
        "project-1",
        operation.operation_id,
    )
    assert entry is not None
    assert entry.origin == "legacy_project"
    assert entry.command_revision == 0
    assert entry.state_revision == 1
    assert entry.status == "running"
    assert operation_store.project_revision("project-1") == 1

    _save(
        "project-1",
        [
            operation.model_copy(
                update={
                    "status": "success",
                    "completed_at": "2026-07-30T01:00:00Z",
                }
            )
        ],
    )

    updated = ledger_store.get_operation(
        "project-1",
        operation.operation_id,
    )
    assert updated is not None
    assert updated.status == "success"
    assert updated.state_revision == 2
    assert operation_store.project_revision("project-1") == 2


def test_runtime_state_timestamp_does_not_touch_project_history_time(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="source_audio",
        status="queued",
    )
    project = _save("project-1", [operation])
    project_updated_at = project.updated_at

    saved = service.update_video_localization_atomic(
        "project-1",
        lambda draft: draft.model_copy(
            update={
                "operations": [
                    operation.model_copy(update={"status": "running"})
                ]
            }
        ),
        intent="runtime",
    )

    assert saved is not None
    persisted_project = project_store.get_project("project-1")
    assert persisted_project is not None
    assert persisted_project.updated_at == project_updated_at
    entry = ledger_store.get_operation(
        "project-1",
        operation.operation_id,
    )
    assert entry is not None
    assert entry.status == "running"
    assert entry.state_revision == 2
    assert entry.updated_at == saved.updated_at
    assert entry.updated_at != project_updated_at


def test_product_readers_overlay_ledger_owned_state(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="source_audio",
        status="queued",
    )
    _save("project-1", [operation])
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE projects
            SET data = json_set(
                data,
                '$.parameters.video_localization.operations[0].status',
                'failed'
            )
            WHERE project_id = ?
            """,
            ("project-1",),
        )
    raw_mirror = operation_store.read_project_mirror_operation(
        "project-1",
        operation.operation_id,
    )[1]
    detail = operation_queue.get_operation(
        "project-1",
        operation.operation_id,
    )
    listed = operation_queue.list_operations("project-1")

    assert raw_mirror is not None
    assert raw_mirror["status"] == "failed"
    assert detail is not None
    assert detail.status == "queued"
    assert listed is not None
    assert listed[0].status == "queued"
    assert ledger_store.list_recoverable_project_ids(
        {"queued", "running"}
    ) == ["project-1"]
    report = ledger_audit.reconcile_operation_ledger()
    assert [issue.category for issue in report.issues] == [
        "state_mismatch"
    ]


def test_submit_command_atomically_applies_ledger_mirror_and_outbox(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project = _save("project-1", [])
    expected_revision = operation_store.project_revision("project-1")
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="source_audio",
        status="queued",
        parameters={"scope_id": "safe-id"},
    )
    command = ledger_store.command_from_operation(
        "submit",
        operation.model_dump(mode="json"),
        expected_project_revision=expected_revision,
        command_id="command-1",
        created_at="2026-07-30T01:00:00Z",
    )
    project.parameters["video_localization"] = (
        VideoLocalizationDraft(
            operations=[operation]
        ).model_dump(mode="json")
    )

    project_store.save_project(
        project,
        touch_updated_at=False,
        operation_command=command,
    )

    entry = ledger_store.get_operation(
        "project-1",
        operation.operation_id,
    )
    assert entry is not None
    assert entry.origin == "command"
    assert entry.command_revision == 1
    assert entry.state_revision == 1
    assert entry.last_command_id == "command-1"
    assert entry.status == "queued"
    events = ledger_store.list_outbox(
        "project-1",
        operation.operation_id,
    )
    assert [(event.command_type, event.status) for event in events] == [
        ("submit", "applied")
    ]
    stored = operation_store.read_project_mirror_operation(
        "project-1",
        operation.operation_id,
    )[1]
    assert stored is not None
    assert stored["status"] == "queued"
    assert operation_store.project_revision("project-1") == (
        expected_revision + 1
    )


def test_command_owned_operation_rejects_created_at_rewrite(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project = _save("project-1", [])
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="source_audio",
        status="queued",
    )
    project.parameters["video_localization"] = (
        VideoLocalizationDraft(
            operations=[operation]
        ).model_dump(mode="json")
    )
    command = ledger_store.command_from_operation(
        "submit",
        operation.model_dump(mode="json"),
        expected_project_revision=(
            operation_store.project_revision("project-1")
        ),
    )
    project_store.save_project(
        project,
        touch_updated_at=False,
        operation_command=command,
    )
    rewritten = project_store.get_project("project-1")
    assert rewritten is not None
    rewritten_operation = operation.model_copy(
        update={"created_at": "2026-07-30T09:00:00Z"}
    )
    rewritten.parameters["video_localization"] = (
        VideoLocalizationDraft(
            operations=[rewritten_operation]
        ).model_dump(mode="json")
    )

    with pytest.raises(ledger_store.OperationProjectionConflict):
        project_store.save_project(
            rewritten,
            touch_updated_at=False,
        )

    entry = ledger_store.get_operation(
        "project-1",
        operation.operation_id,
    )
    assert entry is not None
    assert entry.created_at == operation.created_at
    stored = operation_store.read_project_mirror_operation(
        "project-1",
        operation.operation_id,
    )[1]
    assert stored is not None
    assert stored["created_at"] == operation.created_at


def test_command_owned_operation_history_cannot_be_removed_by_project_save(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project = _save("project-1", [])
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="source_audio",
        status="queued",
    )
    project.parameters["video_localization"] = (
        VideoLocalizationDraft(
            operations=[operation]
        ).model_dump(mode="json")
    )
    command = ledger_store.command_from_operation(
        "submit",
        operation.model_dump(mode="json"),
        expected_project_revision=(
            operation_store.project_revision("project-1")
        ),
    )
    project_store.save_project(
        project,
        touch_updated_at=False,
        operation_command=command,
    )
    replacement = project_store.get_project("project-1")
    assert replacement is not None
    replacement.parameters["video_localization"] = (
        VideoLocalizationDraft().model_dump(mode="json")
    )

    with pytest.raises(ledger_store.OperationProjectionConflict):
        project_store.save_project(
            replacement,
            touch_updated_at=False,
        )

    entry = ledger_store.get_operation(
        "project-1",
        operation.operation_id,
    )
    assert entry is not None
    stored = operation_store.read_project_mirror_operation(
        "project-1",
        operation.operation_id,
    )[1]
    assert stored is not None


def test_stale_project_revision_rolls_back_command_and_mirror(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    stale = _save("project-1", [])
    stale_revision = operation_store.project_revision("project-1")
    current = project_store.get_project("project-1")
    assert current is not None
    current.description = "newer write"
    project_store.save_project(current, touch_updated_at=False)
    operation = VideoLocalizationOperation(
        operation_id="stale-operation",
        project_id="project-1",
        kind="source_audio",
    )
    stale.parameters["video_localization"] = (
        VideoLocalizationDraft(
            operations=[operation]
        ).model_dump(mode="json")
    )
    command = ledger_store.command_from_operation(
        "submit",
        operation.model_dump(mode="json"),
        expected_project_revision=stale_revision,
    )

    with pytest.raises(
        operation_store.OperationProjectRevisionConflict
    ):
        project_store.save_project(
            stale,
            touch_updated_at=False,
            operation_command=command,
        )

    assert ledger_store.get_operation(
        "project-1",
        operation.operation_id,
    ) is None
    assert ledger_store.list_outbox(
        "project-1",
        operation.operation_id,
    ) == []
    persisted = project_store.get_project("project-1")
    assert persisted is not None
    assert persisted.description == "newer write"
    assert operation_store.read_project_mirror_operation(
        "project-1",
        operation.operation_id,
    )[1] is None


def test_outbox_failure_rolls_back_command_project_and_revision(
    tmp_path: Path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project = _save("project-1", [])
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="source_audio",
    )
    project.parameters["video_localization"] = (
        VideoLocalizationDraft(
            operations=[operation]
        ).model_dump(mode="json")
    )
    command = ledger_store.command_from_operation(
        "submit",
        operation.model_dump(mode="json"),
        expected_project_revision=(
            operation_store.project_revision("project-1")
        ),
    )
    monkeypatch.setattr(
        ledger_store,
        "mark_outbox_applied",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected outbox failure")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="injected outbox failure",
    ):
        project_store.save_project(
            project,
            touch_updated_at=False,
            operation_command=command,
        )

    assert ledger_store.get_operation(
        "project-1",
        operation.operation_id,
    ) is None
    assert ledger_store.list_outbox(
        "project-1",
        operation.operation_id,
    ) == []
    assert operation_store.read_project_mirror_operation(
        "project-1",
        operation.operation_id,
    )[1] is None
    persisted = project_store.get_project("project-1")
    assert persisted is not None
    assert (
        persisted.parameters["video_localization"]["operations"]
        == []
    )


def test_active_kind_uniqueness_prevents_project_mirror_overwrite(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project = _save("project-1", [])
    first = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="source_audio",
    )
    project.parameters["video_localization"] = (
        VideoLocalizationDraft(
            operations=[first]
        ).model_dump(mode="json")
    )
    first_command = ledger_store.command_from_operation(
        "submit",
        first.model_dump(mode="json"),
        expected_project_revision=(
            operation_store.project_revision("project-1")
        ),
    )
    project_store.save_project(
        project,
        touch_updated_at=False,
        operation_command=first_command,
    )
    second = first.model_copy(
        update={"operation_id": "operation-2"}
    )
    replacement = _project("project-1", [second])
    current = project_store.get_project("project-1")
    assert current is not None
    replacement._repository_revision = current._repository_revision
    second_command = ledger_store.command_from_operation(
        "submit",
        second.model_dump(mode="json"),
        expected_project_revision=(
            operation_store.project_revision("project-1")
        ),
    )

    with pytest.raises(ledger_store.OperationCommandConflict):
        project_store.save_project(
            replacement,
            touch_updated_at=False,
            operation_command=second_command,
        )

    assert ledger_store.get_operation(
        "project-1",
        second.operation_id,
    ) is None
    assert operation_store.read_project_mirror_operation(
        "project-1",
        second.operation_id,
    )[1] is None
    assert operation_store.read_project_mirror_operation(
        "project-1",
        first.operation_id,
    )[1] is not None


def test_legacy_active_kind_duplicates_backfill_but_block_new_commands(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    legacy_operations = [
        VideoLocalizationOperation(
            operation_id=f"legacy-{index}",
            project_id="project-1",
            kind="source_audio",
            status="running",
        )
        for index in range(2)
    ]
    project = _save("project-1", legacy_operations)

    report = ledger_audit.reconcile_operation_ledger()

    assert report.total_ledger_count == 2
    assert {
        issue.operation_id
        for issue in report.issues
        if issue.category == "active_kind_conflict"
    } == {"legacy-0", "legacy-1"}

    command_operation = VideoLocalizationOperation(
        operation_id="command-operation",
        project_id="project-1",
        kind="source_audio",
        status="queued",
    )
    project.parameters["video_localization"] = (
        VideoLocalizationDraft(
            operations=[
                *legacy_operations,
                command_operation,
            ]
        ).model_dump(mode="json")
    )
    command = ledger_store.command_from_operation(
        "submit",
        command_operation.model_dump(mode="json"),
        expected_project_revision=(
            operation_store.project_revision("project-1")
        ),
    )

    with pytest.raises(ledger_store.OperationCommandConflict):
        project_store.save_project(
            project,
            touch_updated_at=False,
            operation_command=command,
        )

    assert ledger_store.get_operation(
        "project-1",
        command_operation.operation_id
    ) is None


def test_cancel_and_retry_commands_preserve_command_lineage(
    tmp_path: Path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _operation_id: None,
    )
    queued = VideoLocalizationOperation(
        operation_id="queued-operation",
        project_id="project-1",
        kind="source_audio",
        status="queued",
    )
    _save("project-1", [queued])

    cancelled = operation_queue.cancel(
        "project-1",
        queued.operation_id,
    )

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    cancel_entry = ledger_store.get_operation(
        "project-1",
        queued.operation_id,
    )
    assert cancel_entry is not None
    assert cancel_entry.command_revision == 1
    assert cancel_entry.status == "cancelled"
    assert [
        event.command_type
        for event in ledger_store.list_outbox(
            "project-1",
            queued.operation_id,
        )
    ] == ["cancel"]

    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    failed = VideoLocalizationOperation(
        operation_id="failed-operation",
        project_id="project-2",
        kind="source_audio",
        status="failed",
        error_code="FAILED",
    )
    _save(
        "project-2",
        [failed],
        source_video=str(video_path),
    )

    retried = operation_queue.retry(
        "project-2",
        failed.operation_id,
    )

    assert retried is not None
    assert retried.operation_id != failed.operation_id
    assert retried.status == "queued"
    retry_entry = ledger_store.get_operation(
        "project-2",
        retried.operation_id,
    )
    assert retry_entry is not None
    assert retry_entry.origin == "command"
    events = ledger_store.list_outbox(
        "project-2",
        retried.operation_id,
    )
    assert len(events) == 1
    assert events[0].command_type == "retry"
    assert events[0].source_operation_id == failed.operation_id


def test_ledger_is_payload_free_and_project_delete_cleans_rows(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="english_asr",
        status="success",
        parameters={"private_path": "/not/persisted"},
        result_summary={"preview_cues": [{"text": "not persisted"}]},
    )
    _save("project-1", [operation])

    with database.conn() as connection:
        ledger_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(video_localization_operations)"
            ).fetchall()
        }
        outbox_columns = {
            str(row["name"])
            for row in connection.execute(
                """
                PRAGMA table_info(
                    video_localization_operation_outbox
                )
                """
            ).fetchall()
        }
    assert "data" not in ledger_columns
    assert "payload" not in ledger_columns
    assert "parameters" not in ledger_columns
    assert "data" not in outbox_columns
    assert "payload" not in outbox_columns

    project_store.delete_project("project-1")

    assert ledger_store.get_operation(
        "project-1",
        operation.operation_id,
    ) is None
    assert ledger_store.list_outbox(
        "project-1",
        operation.operation_id,
    ) == []


def test_ledger_reconciliation_matches_and_detects_state_drift(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="source_audio",
        status="success",
        completed_at="2026-07-30T01:00:00Z",
        result_summary={"workflow_schema_version": "workflow-v3"},
    )
    _save("project-1", [operation])

    matched = ledger_audit.reconcile_operation_ledger()

    assert matched.total_ledger_count == 1
    assert matched.total_mirror_count == 1
    assert matched.category_counts == {"matched": 1}
    assert matched.pending_outbox_count == 0
    assert matched.issues == ()
    assert matched.healthy is True

    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operations
            SET completed_at = '2026-07-30T02:00:00Z'
            WHERE operation_id = ?
            """,
            (operation.operation_id,),
        )

    drifted = ledger_audit.reconcile_operation_ledger()

    assert [issue.category for issue in drifted.issues] == [
        "state_mismatch"
    ]
    assert drifted.healthy is False


def test_ledger_reconciliation_detects_missing_rows_and_pending_outbox(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="source_audio",
        status="success",
    )
    _save("project-1", [operation])
    with database.conn() as connection:
        connection.execute(
            """
            DELETE FROM video_localization_operations
            WHERE operation_id = ?
            """,
            (operation.operation_id,),
        )

    missing = ledger_audit.reconcile_operation_ledger()

    assert [issue.category for issue in missing.issues] == [
        "ledger_operation_missing"
    ]
    assert missing.healthy is False

    command_operation = VideoLocalizationOperation(
        operation_id="operation-2",
        project_id="project-2",
        kind="source_audio",
    )
    project = _save("project-2", [])
    project.parameters["video_localization"] = (
        VideoLocalizationDraft(
            operations=[command_operation]
        ).model_dump(mode="json")
    )
    command = ledger_store.command_from_operation(
        "submit",
        command_operation.model_dump(mode="json"),
        expected_project_revision=(
            operation_store.project_revision("project-2")
        ),
    )
    project_store.save_project(
        project,
        touch_updated_at=False,
        operation_command=command,
    )
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_outbox
            SET status = 'pending', applied_at = NULL
            WHERE operation_id = ?
            """,
            (command_operation.operation_id,),
        )

    pending = ledger_audit.reconcile_operation_ledger()

    assert pending.pending_outbox_count == 1
    assert pending.healthy is False


def test_ledger_reconciliation_is_bounded(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")
    for index in range(2):
        _save(
            f"project-{index}",
            [
                VideoLocalizationOperation(
                    operation_id=f"operation-{index}",
                    project_id=f"project-{index}",
                    kind="source_audio",
                    status="success",
                )
            ],
        )

    report = ledger_audit.reconcile_operation_ledger(limit=1)

    assert report.total_ledger_count == 2
    assert report.checked_ledger_count == 1
    assert report.truncated is True
    assert report.healthy is False


def test_ledger_reconciliation_cli_json_check_is_read_only(
    tmp_path: Path,
):
    db_path = tmp_path / "voice_studio.db"
    database.set_db_path(db_path)
    with database.conn():
        pass
    environment = {
        **os.environ,
        "VOICE_STUDIO_DB_PATH": str(db_path),
    }

    result = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / "audit_video_localization_operation_ledger.py"
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
        timeout=10,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["total_ledger_count"] == 0
    assert payload["issues"] == []

    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO video_localization_operation_outbox (
                event_id,
                command_id,
                project_id,
                operation_id,
                source_operation_id,
                command_type,
                command_revision,
                payload_version,
                status,
                created_at,
                applied_at
            ) VALUES (
                'event-1',
                'command-1',
                'project-1',
                'operation-1',
                NULL,
                'submit',
                1,
                'operation-command-v1',
                'pending',
                '2026-07-30T01:00:00Z',
                NULL
            )
            """
        )

    unhealthy = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / "audit_video_localization_operation_ledger.py"
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
        timeout=10,
    )

    assert unhealthy.returncode == 1
    assert json.loads(unhealthy.stdout)["pending_outbox_count"] == 1
