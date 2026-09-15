from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.voice_studio import Project  # noqa: E402
from app.services import database  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_ledger_audit as ledger_audit,
)
from app.services import (  # noqa: E402
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_store as operation_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_workflow_version_migration as migration,
)


T0 = "2026-07-31T10:00:00+00:00"


@pytest.fixture
def isolated_database(tmp_path: Path):
    original_path = database.DB_PATH
    database.set_db_path(tmp_path / "voice_studio.db")
    try:
        yield tmp_path
    finally:
        database.set_db_path(original_path)


def _operation(
    project_id: str,
    operation_id: str,
    *,
    result_summary: dict | None = None,
) -> VideoLocalizationOperation:
    return VideoLocalizationOperation(
        project_id=project_id,
        operation_id=operation_id,
        kind="source_audio",
        parameters={"scope": {"area": "timeline"}},
        result_summary=result_summary or {},
        created_at=T0,
    )


def _save_operation(
    project_id: str,
    operation_id: str,
) -> None:
    operation = _operation(project_id, operation_id)
    project = Project(
        project_id=project_id,
        name="Workflow migration",
        parameters={
            "video_localization": VideoLocalizationDraft(
                operations=[operation]
            ).model_dump(mode="json")
        },
        created_at=T0,
        updated_at=T0,
    )
    command = ledger_store.command_from_operation(
        "submit",
        operation.model_dump(mode="json"),
        expected_project_revision=0,
        created_at=T0,
    )
    operation_store.save_project_with_projection(
        project_id,
        project.model_dump(mode="json"),
        updated_at=T0,
        operation_updated_at=T0,
        operation_command=command,
    )


def _set_legacy_null_sentinel(
    project_id: str,
    operation_id: str,
) -> None:
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operations
            SET workflow_version = 'None'
            WHERE project_id = ?
              AND operation_id = ?
            """,
            (project_id, operation_id),
        )


def test_writer_normalizes_missing_null_and_blank_workflow_versions():
    base = _operation(
        "project-a",
        "operation-a",
    ).model_dump(mode="json")
    assert (
        ledger_store.command_from_operation(
            "submit",
            base,
            expected_project_revision=0,
        ).workflow_version
        == "operation-v1"
    )
    for value in (None, ""):
        payload = {
            **base,
            "result_summary": {
                "workflow_schema_version": value,
            },
        }
        assert (
            ledger_store.command_from_operation(
                "submit",
                payload,
                expected_project_revision=0,
            ).workflow_version
            == "operation-v1"
        )


@pytest.mark.parametrize("value", ["None", 1, False])
def test_writer_rejects_invalid_workflow_version_values(value):
    payload = _operation(
        "project-a",
        "operation-a",
    ).model_dump(mode="json")
    payload["result_summary"] = {
        "workflow_schema_version": value,
    }

    with pytest.raises(ValueError):
        ledger_store.command_from_operation(
            "submit",
            payload,
            expected_project_revision=0,
        )


def test_ledger_audit_no_longer_reports_shared_null_bug_as_matched(
    isolated_database,
):
    _save_operation("project-a", "operation-a")
    _set_legacy_null_sentinel("project-a", "operation-a")

    report = ledger_audit.reconcile_operation_ledger()

    assert report.category_counts == {"state_mismatch": 1}
    assert [issue.category for issue in report.issues] == [
        "state_mismatch"
    ]
    assert report.healthy is False


def test_normal_projection_does_not_partially_migrate_legacy_sentinel(
    isolated_database,
):
    _save_operation("project-a", "operation-a")
    _set_legacy_null_sentinel("project-a", "operation-a")
    project_exists, operation = operation_store.read_project_mirror_operation(
        "project-a",
        "operation-a",
    )
    assert project_exists is True
    assert operation is not None

    with database.conn() as connection:
        ledger_store.sync_project_operations(
            connection,
            "project-a",
            [operation],
            updated_at="2026-07-31T11:00:00+00:00",
        )

    entry = ledger_store.get_operation(
        "project-a",
        "operation-a",
    )
    assert entry is not None
    assert entry.workflow_version == "None"


def test_query_only_plan_is_bounded_and_does_not_write(
    isolated_database,
):
    _save_operation("project-a", "operation-a")
    _save_operation("project-b", "operation-b")
    _set_legacy_null_sentinel("project-a", "operation-a")
    _set_legacy_null_sentinel("project-b", "operation-b")
    watcher = sqlite3.connect(database.DB_PATH)
    try:
        before = int(
            watcher.execute("PRAGMA data_version").fetchone()[0]
        )
        first = migration.migrate_legacy_null_workflow_versions(
            limit=1,
        )
        after = int(
            watcher.execute("PRAGMA data_version").fetchone()[0]
        )
    finally:
        watcher.close()

    assert first.planned_operation_count == 1
    assert first.truncated is True
    assert first.next_project_id == "project-a"
    assert first.next_operation_id == "operation-a"
    assert after == before
    second = migration.migrate_legacy_null_workflow_versions(
        limit=1,
        after_project_id=first.next_project_id,
        after_operation_id=first.next_operation_id,
    )
    assert second.planned_operation_count == 1
    assert second.truncated is False


def test_apply_updates_ledger_and_steps_without_touching_project_time(
    isolated_database,
):
    _save_operation("project-a", "operation-a")
    _set_legacy_null_sentinel("project-a", "operation-a")
    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO video_localization_operation_step_attempts (
                step_attempt_id,
                project_id,
                operation_id,
                operation_attempt_id,
                step_id,
                step_schema_version,
                step_attempt_number,
                fencing_token,
                workflow_version,
                input_fingerprint,
                cost_class,
                status,
                prepared_at
            ) VALUES (
                'step-a',
                'project-a',
                'operation-a',
                'attempt-a',
                'extract_source_audio',
                'operation-step-attempt-v1',
                1,
                1,
                'None',
                'input-a',
                'local_free',
                'prepared',
                ?
            )
            """,
            (T0,),
        )

    report = migration.migrate_legacy_null_workflow_versions(
        apply=True,
    )
    rerun = migration.migrate_legacy_null_workflow_versions(
        apply=True,
    )

    assert report.migrated_operation_count == 1
    assert report.rejected_operation_count == 0
    assert rerun.scanned_operation_count == 0
    with database.conn() as connection:
        ledger = connection.execute(
            """
            SELECT workflow_version
            FROM video_localization_operations
            WHERE project_id = 'project-a'
              AND operation_id = 'operation-a'
            """
        ).fetchone()
        step = connection.execute(
            """
            SELECT workflow_version
            FROM video_localization_operation_step_attempts
            WHERE step_attempt_id = 'step-a'
            """
        ).fetchone()
        project = connection.execute(
            """
            SELECT updated_at
            FROM projects
            WHERE project_id = 'project-a'
            """
        ).fetchone()
    assert ledger["workflow_version"] == "operation-v1"
    assert step["workflow_version"] == "operation-v1"
    assert project["updated_at"] == T0
    assert ledger_audit.reconcile_operation_ledger().healthy is True


def test_apply_rejects_detail_core_or_step_identity_conflicts(
    isolated_database,
):
    _save_operation("project-a", "operation-a")
    _save_operation("project-b", "operation-b")
    _set_legacy_null_sentinel("project-a", "operation-a")
    _set_legacy_null_sentinel("project-b", "operation-b")
    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO video_localization_operation_detail_cores (
                project_id,
                operation_id,
                detail_schema_version,
                workflow_version,
                content_fingerprint,
                core_json,
                written_at
            ) VALUES (
                'project-a',
                'operation-a',
                'operation-detail-core-v1',
                'None',
                ?,
                '{}',
                ?
            )
            """,
            ("a" * 64, T0),
        )
        connection.execute(
            """
            INSERT INTO video_localization_operation_step_attempts (
                step_attempt_id,
                project_id,
                operation_id,
                operation_attempt_id,
                step_id,
                step_schema_version,
                step_attempt_number,
                fencing_token,
                workflow_version,
                input_fingerprint,
                cost_class,
                status,
                prepared_at
            ) VALUES (
                'step-b',
                'project-b',
                'operation-b',
                'attempt-b',
                'extract_source_audio',
                'operation-step-attempt-v1',
                1,
                1,
                'unexpected-workflow',
                'input-b',
                'local_free',
                'prepared',
                ?
            )
            """,
            (T0,),
        )

    report = migration.migrate_legacy_null_workflow_versions(
        apply=True,
    )

    assert report.migrated_operation_count == 0
    assert report.rejected_operation_count == 2
    assert {
        issue
        for result in report.results
        for issue in result.issue_codes
    } == {"detail_core_conflict", "step_workflow_mismatch"}
    with database.conn() as connection:
        versions = {
            str(row["workflow_version"])
            for row in connection.execute(
                """
                SELECT workflow_version
                FROM video_localization_operations
                """
            ).fetchall()
        }
    assert versions == {"None"}


def test_cli_defaults_to_query_only(
    isolated_database,
):
    _save_operation("project-a", "operation-a")
    _set_legacy_null_sentinel("project-a", "operation-a")
    environment = {
        **os.environ,
        "VOICE_STUDIO_DB_PATH": str(database.DB_PATH),
    }

    completed = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / (
                    "migrate_video_localization_operation_"
                    "workflow_versions.py"
                )
            ),
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["apply"] is False
    assert payload["planned_operation_count"] == 1
    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT workflow_version
            FROM video_localization_operations
            WHERE project_id = 'project-a'
              AND operation_id = 'operation-a'
            """
        ).fetchone()
    assert row["workflow_version"] == "None"
