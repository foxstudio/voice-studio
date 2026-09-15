from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    operation_state,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.voice_studio import Project  # noqa: E402
from app.services import database  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_detail_core_store as detail_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_detail_migration as migration,
)
from app.services import (  # noqa: E402
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_store as operation_store,
)


WORKFLOW_VERSION = "semantic-tts-grouping-workflow-v2"
T0 = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)


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
) -> VideoLocalizationOperation:
    parameters = {
        "profile_id": "profile-migration",
        "profile_configuration_fingerprint": "a" * 64,
        "workflow_id": "semantic-tts-grouping",
        "target_chars": 90,
        "max_chars": 150,
    }
    parameters["scope"] = operation_state.operation_scope(
        "semantic_tts_grouping",
        parameters,
    )
    return VideoLocalizationOperation(
        project_id=project_id,
        operation_id=operation_id,
        kind="semantic_tts_grouping",
        parameters=parameters,
        result_summary={
            "workflow_schema_version": WORKFLOW_VERSION,
        },
        created_at=T0.isoformat(),
    )


def _save_missing_core(
    project_id: str,
    operation_id: str,
) -> None:
    operation = _operation(project_id, operation_id)
    project = Project(
        project_id=project_id,
        name="Detail migration",
        parameters={
            "video_localization": VideoLocalizationDraft(
                operations=[operation]
            ).model_dump(mode="json")
        },
        created_at=T0.isoformat(),
        updated_at=T0.isoformat(),
    )
    command = ledger_store.command_from_operation(
        "submit",
        operation.model_dump(mode="json"),
        expected_project_revision=0,
        created_at=T0.isoformat(),
    )
    operation_store.save_project_with_projection(
        project_id,
        project.model_dump(mode="json"),
        updated_at=T0.isoformat(),
        operation_updated_at=T0.isoformat(),
        operation_command=command,
    )


def test_plan_is_query_only_and_reports_missing_core(
    isolated_database,
):
    _save_missing_core("project-a", "operation-a")
    watcher = sqlite3.connect(database.DB_PATH)
    try:
        before = int(
            watcher.execute("PRAGMA data_version").fetchone()[0]
        )
        report = migration.migrate_operation_details(
            apply=False,
        )
        after = int(
            watcher.execute("PRAGMA data_version").fetchone()[0]
        )
    finally:
        watcher.close()

    assert report.planned_operation_count == 1
    assert report.migrated_operation_count == 0
    assert report.rejected_operation_count == 0
    assert report.results[0].status == "planned"
    assert (
        detail_store.get_detail_core(
            "project-a",
            "operation-a",
        )
        is None
    )
    assert after == before


def test_apply_is_idempotent_and_does_not_touch_project_time(
    isolated_database,
):
    _save_missing_core("project-a", "operation-a")

    first = migration.migrate_operation_details(
        apply=True,
        clock=lambda: T0,
    )
    second = migration.migrate_operation_details(
        apply=True,
        clock=lambda: T0,
    )

    assert first.migrated_operation_count == 1
    assert second.unchanged_operation_count == 1
    record = detail_store.get_detail_core(
        "project-a",
        "operation-a",
    )
    assert record is not None
    assert record.core.parameters.profile_id == "profile-migration"
    with database.conn() as connection:
        project = connection.execute(
            """
            SELECT updated_at
            FROM projects
            WHERE project_id = 'project-a'
            """
        ).fetchone()
    assert project["updated_at"] == T0.isoformat()


def test_apply_rejects_mirror_drift_without_partial_core(
    isolated_database,
):
    _save_missing_core("project-a", "operation-a")
    with database.conn() as connection:
        row = connection.execute(
            "SELECT data FROM projects WHERE project_id = 'project-a'"
        ).fetchone()
        payload = json.loads(row["data"])
        payload["parameters"]["video_localization"][
            "operations"
        ][0]["parameters"]["target_chars"] = 999
        connection.execute(
            "UPDATE projects SET data = ? WHERE project_id = 'project-a'",
            (json.dumps(payload),),
        )

    report = migration.migrate_operation_details(apply=True)

    assert report.rejected_operation_count == 1
    assert report.results[0].issue_codes == (
        "mirror_parameters_mismatch",
    )
    assert (
        detail_store.get_detail_core(
            "project-a",
            "operation-a",
        )
        is None
    )


def test_migration_uses_stable_composite_keyset_cursor(
    isolated_database,
):
    _save_missing_core("project-a", "operation-a")
    _save_missing_core("project-b", "operation-b")

    first = migration.migrate_operation_details(
        apply=False,
        limit=1,
    )
    second = migration.migrate_operation_details(
        apply=False,
        limit=1,
        after_project_id=first.next_project_id,
        after_operation_id=first.next_operation_id,
    )

    assert first.truncated is True
    assert (
        first.next_project_id,
        first.next_operation_id,
    ) == ("project-a", "operation-a")
    assert second.truncated is False
    assert second.results[0].project_id == "project-b"


def test_cli_plan_and_apply_are_explicit(isolated_database):
    _save_missing_core("project-a", "operation-a")
    environment = {
        **os.environ,
        "VOICE_STUDIO_DB_PATH": str(database.DB_PATH),
    }
    command = [
        sys.executable,
        str(
            ROOT
            / "scripts"
            / "migrate_video_localization_operation_details.py"
        ),
        "--format",
        "json",
    ]

    planned = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert planned.returncode == 0, planned.stderr
    assert json.loads(planned.stdout)[
        "planned_operation_count"
    ] == 1
    assert (
        detail_store.get_detail_core(
            "project-a",
            "operation-a",
        )
        is None
    )

    applied = subprocess.run(
        [*command, "--apply"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)[
        "migrated_operation_count"
    ] == 1
    assert (
        detail_store.get_detail_core(
            "project-a",
            "operation-a",
        )
        is not None
    )
