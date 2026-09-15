from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import draft_store  # noqa: E402
from app.domains.video_localization import (  # noqa: E402
    operation_summary_projection,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.voice_studio import AppSettings, Project  # noqa: E402
from app.services import (  # noqa: E402
    database,
    project_store,
    settings_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_ledger_store,
)
from app.services import video_localization_operation_store  # noqa: E402
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
    with database.conn() as connection:
        connection.execute(
            """
            DELETE FROM
                video_localization_operation_summary_authority
            """
        )
    try:
        yield
    finally:
        database.set_db_path(original_path)


def _create_project(project_id: str = "project-1") -> None:
    project_store.save_project(
        Project(
            project_id=project_id,
            name="Summary shadow project",
        )
    )


def _operation(
    *,
    project_id: str = "project-1",
    status: str = "running",
    progress: float = 0.25,
    completed_at: str | None = None,
) -> VideoLocalizationOperation:
    return VideoLocalizationOperation.model_validate(
        {
            "operation_id": "shared-operation",
            "project_id": project_id,
            "kind": "english_asr",
            "status": status,
            "progress": progress,
            "parameters": {
                "execution_mode": "formal",
                "artifact_path": "/private/input.wav",
            },
            "result_summary": {
                "stage": "固定步骤",
                "cue_count": 12,
                "artifact_path": "/private/result.json",
            },
            "created_at": "2026-07-30T10:00:00+00:00",
            "completed_at": completed_at,
        }
    )


def _save_draft(
    operation: VideoLocalizationOperation,
) -> VideoLocalizationDraft:
    draft = VideoLocalizationDraft(
        operations=[operation],
    )
    saved = draft_store.save(
        operation.project_id,
        draft,
        intent="runtime",
    )
    assert saved is not None
    return saved


def _summary_row(
    project_id: str = "project-1",
):
    with database.conn() as connection:
        return connection.execute(
            """
            SELECT *
            FROM video_localization_operation_summaries
            WHERE project_id = ?
              AND operation_id = 'shared-operation'
            """,
            (project_id,),
        ).fetchone()


def test_draft_save_writes_path_free_shadow_core(
    isolated_database,
):
    _create_project()
    _save_draft(_operation())

    row = _summary_row()
    state = (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
    )
    assert row is not None
    assert state is not None
    assert row["summary_schema_version"] == (
        "operation-summary-core-v1"
    )
    assert row["ledger_state_revision"] == 1
    assert row["core_revision"] == 1
    assert row["content_fingerprint"]
    core_payload = json.loads(row["core_json"])
    assert core_payload["operation_id"] == "shared-operation"
    assert core_payload["progress"] == 0.25
    assert "artifact_path" not in row["core_json"]
    assert state.summary_status == "shadow"
    assert state.summary_row_count == 1
    assert state.summary_schema_version == (
        "operation-summary-core-v1"
    )
    assert state.history_revision == 0
    assert state.summary_fingerprint
    assert state.history_fingerprint


def test_submit_command_preflights_previous_shadow_then_commits_all(
    isolated_database,
):
    _create_project()
    operation = _operation(status="queued", progress=0.0)
    command = (
        video_localization_operation_ledger_store
        .command_from_operation(
            "submit",
            operation.model_dump(mode="json"),
            expected_project_revision=(
                video_localization_operation_store
                .project_revision("project-1")
            ),
            command_id="summary-success-command",
            created_at="2026-07-30T10:00:00+00:00",
        )
    )

    saved = draft_store.save(
        "project-1",
        VideoLocalizationDraft(operations=[operation]),
        intent="runtime",
        operation_command=command,
    )

    assert saved is not None
    ledger = (
        video_localization_operation_ledger_store
        .get_operation("project-1", "shared-operation")
    )
    events = (
        video_localization_operation_ledger_store.list_outbox(
            "project-1",
            "shared-operation",
        )
    )
    assert ledger is not None
    assert ledger.origin == "command"
    assert ledger.command_revision == 1
    assert [(event.command_type, event.status) for event in events] == [
        ("submit", "applied")
    ]
    assert _summary_row() is not None


def test_core_and_history_revisions_only_track_visible_scope(
    isolated_database,
):
    _create_project()
    first = _save_draft(_operation())
    first_row = _summary_row()
    first_state = (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
    )
    assert first_row is not None
    assert first_state is not None

    project = project_store.get_project("project-1")
    assert project is not None
    project.name = "Renamed without operation change"
    project_store.save_project(project)
    renamed_row = _summary_row()
    renamed_state = (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
    )
    assert renamed_row is not None
    assert renamed_state is not None
    assert renamed_row["core_revision"] == 1
    assert renamed_row["projected_at"] == first_row["projected_at"]
    assert (
        renamed_state.summary_fingerprint
        == first_state.summary_fingerprint
    )
    assert renamed_state.history_revision == 0

    current = draft_store.get("project-1")
    assert current is not None
    running = current.model_copy(
        update={
            "operations": [
                _operation(progress=0.75),
            ]
        }
    )
    saved_running = draft_store.save(
        "project-1",
        running,
        intent="runtime",
    )
    assert saved_running is not None
    running_row = _summary_row()
    running_state = (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
    )
    assert running_row is not None
    assert running_state is not None
    assert running_row["ledger_state_revision"] == 1
    assert running_row["core_revision"] == 2
    assert running_state.history_revision == 0
    assert (
        running_state.summary_fingerprint
        != first_state.summary_fingerprint
    )

    terminal = saved_running.model_copy(
        update={
            "operations": [
                _operation(
                    status="success",
                    progress=1.0,
                    completed_at=(
                        "2026-07-30T10:01:00+00:00"
                    ),
                ),
            ]
        }
    )
    saved_terminal = draft_store.save(
        "project-1",
        terminal,
        intent="runtime",
    )
    assert saved_terminal is not None
    terminal_row = _summary_row()
    terminal_state = (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
    )
    assert terminal_row is not None
    assert terminal_state is not None
    assert terminal_row["ledger_state_revision"] == 2
    assert terminal_row["core_revision"] == 3
    assert terminal_state.history_revision == 1

    repeated = draft_store.save(
        "project-1",
        saved_terminal,
        intent="runtime",
        updated_at=saved_terminal.updated_at,
    )
    assert repeated is not None
    repeated_row = _summary_row()
    repeated_state = (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
    )
    assert repeated_row is not None
    assert repeated_state is not None
    assert repeated_row["core_revision"] == 3
    assert (
        repeated_row["projected_at"]
        == terminal_row["projected_at"]
    )
    assert repeated_state.history_revision == 1
    assert (
        repeated_state.summary_fingerprint
        == terminal_state.summary_fingerprint
    )


def test_projection_conflict_rolls_back_project_and_ledger(
    isolated_database,
):
    _create_project()
    project_before = project_store.get_project("project-1")
    revision_before = (
        video_localization_operation_store.project_revision(
            "project-1"
        )
    )
    assert project_before is not None
    operation = _operation()
    payload = project_before.model_dump(mode="json")
    payload["parameters"]["video_localization"] = {
        "operations": [operation.model_dump(mode="json")],
    }
    command = (
        video_localization_operation_ledger_store
        .command_from_operation(
            "submit",
            operation.model_dump(mode="json"),
            expected_project_revision=revision_before,
            command_id="summary-rollback-command",
            created_at="2026-07-30T10:00:00+00:00",
        )
    )

    with pytest.raises(
        video_localization_operation_summary_store
        .OperationSummaryProjectionConflict
    ):
        (
            video_localization_operation_store
            .save_project_with_projection(
                "project-1",
                payload,
                updated_at=project_before.updated_at,
                operation_command=command,
                operation_summary_cores=(),
            )
        )

    assert (
        project_store.get_project("project-1")
        == project_before
    )
    assert (
        video_localization_operation_store.project_revision(
            "project-1"
        )
        == revision_before
    )
    assert (
        video_localization_operation_ledger_store
        .list_project_operations("project-1")
        == []
    )
    assert (
        video_localization_operation_ledger_store.list_outbox(
            "project-1",
            "shared-operation",
        )
        == []
    )
    assert _summary_row() is None


def test_same_legacy_operation_id_is_isolated_by_project(
    isolated_database,
):
    for project_id in ("project-a", "project-b"):
        _create_project(project_id)
        _save_draft(_operation(project_id=project_id))

    row_a = _summary_row("project-a")
    row_b = _summary_row("project-b")
    assert row_a is not None
    assert row_b is not None
    assert json.loads(row_a["core_json"])["project_id"] == (
        "project-a"
    )
    assert json.loads(row_b["core_json"])["project_id"] == (
        "project-b"
    )


def test_project_delete_removes_summary_rows(
    isolated_database,
):
    _create_project()
    _save_draft(_operation())
    assert _summary_row() is not None

    project_store.delete_project("project-1")

    assert _summary_row() is None
    assert (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
        is None
    )


def test_removing_legacy_operation_deletes_its_shadow_core(
    isolated_database,
):
    _create_project()
    _save_draft(
        _operation(
            status="success",
            progress=1.0,
            completed_at="2026-07-30T10:01:00+00:00",
        )
    )
    before = (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
    )
    assert before is not None
    assert before.summary_row_count == 1

    saved = draft_store.save(
        "project-1",
        VideoLocalizationDraft(),
        intent="runtime",
    )

    assert saved is not None
    assert _summary_row() is None
    state = (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
    )
    assert state is not None
    assert state.summary_status == "shadow"
    assert state.summary_row_count == 0
    assert state.history_revision == (
        before.history_revision + 1
    )


@pytest.mark.parametrize(
    "summary_status",
    ["verified", "authoritative", "repair_required"],
)
def test_shadow_sync_preserves_explicit_projection_status(
    isolated_database,
    summary_status: str,
):
    _create_project()
    saved = _save_draft(_operation())
    verified_at = "2026-07-30T10:02:00+00:00"
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_projection_state
            SET summary_status = ?, last_verified_at = ?
            WHERE project_id = 'project-1'
            """,
            (summary_status, verified_at),
        )
    updated = saved.model_copy(
        update={
            "operations": [
                _operation(progress=0.5),
            ]
        }
    )

    assert draft_store.save(
        "project-1",
        updated,
        intent="runtime",
    ) is not None

    state = (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
    )
    assert state is not None
    assert state.summary_status == summary_status
    assert state.last_verified_at == verified_at


def test_corrupt_shadow_core_blocks_implicit_repair_and_rolls_back(
    isolated_database,
):
    _create_project()
    saved = _save_draft(_operation())
    state_before = (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
    )
    revision_before = (
        video_localization_operation_store.project_revision(
            "project-1"
        )
    )
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_summaries
            SET core_json = '{"broken": true}'
            WHERE project_id = 'project-1'
              AND operation_id = 'shared-operation'
            """
        )

    with pytest.raises(
        video_localization_operation_summary_store
        .OperationSummaryProjectionConflict
    ):
        draft_store.save(
            "project-1",
            saved,
            intent="runtime",
            updated_at=saved.updated_at,
        )

    state_after = (
        video_localization_operation_summary_store
        .read_projection_state("project-1")
    )
    assert state_after == state_before
    assert (
        video_localization_operation_store.project_revision(
            "project-1"
        )
        == revision_before
    )
    row = _summary_row()
    assert row is not None
    assert row["core_json"] == '{"broken": true}'


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        (
            "summary_row_count",
            0,
            "row count is inconsistent",
        ),
        (
            "summary_schema_version",
            "operation-summary-core-v0",
            "state is incomplete",
        ),
        (
            "summary_fingerprint",
            "broken-fingerprint",
            "fingerprint is inconsistent",
        ),
    ],
)
def test_corrupt_projection_state_blocks_shadow_write(
    isolated_database,
    column: str,
    value: object,
    message: str,
):
    _create_project()
    saved = _save_draft(_operation())
    revision_before = (
        video_localization_operation_store.project_revision(
            "project-1"
        )
    )
    with database.conn() as connection:
        connection.execute(
            f"""
            UPDATE video_localization_operation_projection_state
            SET {column} = ?
            WHERE project_id = 'project-1'
            """,
            (value,),
        )

    with pytest.raises(
        video_localization_operation_summary_store
        .OperationSummaryProjectionConflict,
        match=message,
    ):
        draft_store.save(
            "project-1",
            saved,
            intent="runtime",
            updated_at=saved.updated_at,
        )

    assert (
        video_localization_operation_store.project_revision(
            "project-1"
        )
        == revision_before
    )


def test_ledger_shadow_revision_mismatch_blocks_write(
    isolated_database,
):
    _create_project()
    saved = _save_draft(_operation())
    revision_before = (
        video_localization_operation_store.project_revision(
            "project-1"
        )
    )
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operations
            SET state_revision = state_revision + 1
            WHERE project_id = 'project-1'
              AND operation_id = 'shared-operation'
            """
        )

    with pytest.raises(
        video_localization_operation_summary_store
        .OperationSummaryProjectionConflict,
        match="ledger revision is inconsistent",
    ):
        draft_store.save(
            "project-1",
            saved,
            intent="runtime",
            updated_at=saved.updated_at,
        )

    assert (
        video_localization_operation_store.project_revision(
            "project-1"
        )
        == revision_before
    )


def test_compatible_migration_preserves_legacy_projection_revision(
    tmp_path: Path,
):
    original_path = database.DB_PATH
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE
                video_localization_operation_projection_state (
                    project_id TEXT PRIMARY KEY,
                    projection_revision INTEGER NOT NULL DEFAULT 0
                )
            """
        )
        connection.execute(
            """
            INSERT INTO
                video_localization_operation_projection_state
            VALUES ('legacy-project', 17)
            """
        )
    database.set_db_path(db_path)
    try:
        with database.conn() as connection:
            columns = {
                str(row["name"])
                for row in connection.execute(
                    """
                    PRAGMA table_info(
                        video_localization_operation_projection_state
                    )
                    """
                ).fetchall()
            }
            summary_table = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table'
                  AND name =
                    'video_localization_operation_summaries'
                """
            ).fetchone()
        state = (
            video_localization_operation_summary_store
            .read_projection_state("legacy-project")
        )
        assert state is not None
        assert state.projection_revision == 17
        assert state.summary_status == "missing"
        assert state.summary_row_count == 0
        assert state.history_revision == 0
        assert summary_table is not None
        assert {
            "summary_schema_version",
            "summary_status",
            "summary_row_count",
            "summary_fingerprint",
            "history_revision",
            "history_fingerprint",
            "last_verified_at",
        } <= columns
    finally:
        database.set_db_path(original_path)


def test_core_from_operation_excludes_read_time_artifact_state():
    operation = _operation(status="success", progress=1.0)
    summary = (
        operation_summary_projection.project_operation_summary(
            operation,
            artifact_available=True,
        )
    )
    core = operation_summary_projection.operation_summary_core(
        summary
    )
    direct_core = (
        operation_summary_projection
        .operation_summary_core_from_operation(operation)
    )

    assert "artifact_available" in summary.result_summary
    assert "artifact_available" not in core.result_summary
    assert direct_core == core
