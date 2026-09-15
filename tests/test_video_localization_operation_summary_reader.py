from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
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
from app.errors import AppException  # noqa: E402
from app.schemas.voice_studio import Project  # noqa: E402
from app.services import database  # noqa: E402
from app.services import video_localization_operations  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_summary_store,
)


@pytest.fixture
def isolated_database(tmp_path: Path):
    original_path = database.DB_PATH
    database.set_db_path(tmp_path / "voice_studio.db")
    video_localization_operations._operation_feed_reader.clear()
    with database.conn() as connection:
        connection.execute(
            """
            DELETE FROM
                video_localization_operation_summary_authority
            """
        )
    try:
        yield tmp_path
    finally:
        video_localization_operations._operation_feed_reader.clear()
        database.set_db_path(original_path)


def _operation(
    project_id: str,
    operation_id: str = "operation-1",
    *,
    created_at: str = "2026-07-30T01:00:00+00:00",
    parameters: dict | None = None,
    result_summary: dict | None = None,
) -> VideoLocalizationOperation:
    return VideoLocalizationOperation(
        operation_id=operation_id,
        project_id=project_id,
        kind="english_asr",
        status="success",
        label="固定任务",
        progress=1,
        parameters=parameters or {},
        result_summary=result_summary or {"cue_count": 3},
        created_at=created_at,
        completed_at="2026-07-30T01:01:00+00:00",
    )


def _insert_project(
    project_id: str,
    operations: list[VideoLocalizationOperation],
) -> None:
    project = Project(
        project_id=project_id,
        name=project_id,
        parameters={
            "video_localization": VideoLocalizationDraft(
                operations=operations,
                updated_at="2026-07-30T02:00:00+00:00",
            ).model_dump(mode="json")
        },
        updated_at="2026-07-30T02:00:00+00:00",
    )
    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO projects (project_id, data, updated_at)
            VALUES (?, ?, ?)
            """,
            (
                project_id,
                json.dumps(
                    project.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
                project.updated_at,
            ),
        )


def _backfill(project_id: str) -> None:
    report = (
        video_localization_operations.backfill_operation_summaries(
            limit=100
        )
    )
    assert any(
        project.project_id == project_id
        and project.status in {"backfilled", "unchanged"}
        for project in report.projects
    )


def _promote(project_id: str) -> None:
    report = (
        video_localization_operations.promote_operation_summaries(
            limit=100
        )
    )
    project = next(
        item
        for item in report.projects
        if item.project_id == project_id
    )
    assert project.status in {"verified", "unchanged"}
    state = (
        video_localization_operation_summary_store
        .read_projection_state(project_id)
    )
    assert state is not None
    assert state.summary_status == "verified"
    assert state.last_verified_at is not None


def test_verified_reader_does_not_read_projects_data(
    isolated_database: Path,
    monkeypatch,
):
    project_id = "no-project-blob"
    _insert_project(project_id, [_operation(project_id)])
    _backfill(project_id)
    _promote(project_id)
    video_localization_operations._operation_feed_reader.clear()

    original_conn = database.conn

    @contextmanager
    def guarded_connection():
        with original_conn() as connection:
            def authorize(
                action: int,
                table: str | None,
                column: str | None,
                _database_name: str | None,
                _trigger: str | None,
            ) -> int:
                if (
                    action == sqlite3.SQLITE_READ
                    and table == "projects"
                    and column == "data"
                ):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorize)
            yield connection

    monkeypatch.setattr(database, "conn", guarded_connection)

    feed = video_localization_operations.read_operation_feed_v2(
        project_id
    )

    assert feed is not None
    assert [item.operation_id for item in feed.history] == [
        "operation-1"
    ]


def test_repair_required_blocks_even_unchanged_short_circuit(
    isolated_database: Path,
):
    project_id = "repair-required"
    _insert_project(project_id, [_operation(project_id)])
    _backfill(project_id)
    descriptor = (
        video_localization_operation_summary_store
        .read_feed_descriptor(project_id)
    )
    assert descriptor is not None
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_projection_state
            SET summary_status = 'repair_required'
            WHERE project_id = ?
            """,
            (project_id,),
        )

    with pytest.raises(AppException) as captured:
        video_localization_operations.read_operation_feed_v2(
            project_id,
            after_revision=descriptor.projection_revision,
        )

    assert captured.value.status_code == 409
    assert captured.value.code == (
        "VIDEO_LOCALIZATION_OPERATION_SUMMARY_REPAIR_REQUIRED"
    )


def test_corrupt_verified_core_fails_without_legacy_fallback(
    isolated_database: Path,
):
    project_id = "corrupt-verified"
    _insert_project(project_id, [_operation(project_id)])
    _backfill(project_id)
    _promote(project_id)
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_summaries
            SET core_json = '{"broken":true}'
            WHERE project_id = ?
            """,
            (project_id,),
        )
    video_localization_operations._operation_feed_reader.clear()
    with pytest.raises(AppException) as captured:
        video_localization_operations.read_operation_feed_v2(
            project_id
        )

    assert captured.value.code == (
        "VIDEO_LOCALIZATION_OPERATION_SUMMARY_REPAIR_REQUIRED"
    )
def test_promotion_rejects_corruption_and_keeps_shadow(
    isolated_database: Path,
):
    project_id = "promotion-rejects"
    _insert_project(project_id, [_operation(project_id)])
    _backfill(project_id)
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_summaries
            SET content_fingerprint = 'corrupt'
            WHERE project_id = ?
            """,
            (project_id,),
        )

    report = (
        video_localization_operations.promote_operation_summaries(
            limit=100
        )
    )
    project = next(
        item
        for item in report.projects
        if item.project_id == project_id
    )
    state = (
        video_localization_operation_summary_store
        .read_projection_state(project_id)
    )

    assert project.status == "rejected"
    assert "summary_core_fingerprint_mismatch" in (
        project.issue_categories
    )
    assert state is not None
    assert state.summary_status == "shadow"
    assert state.last_verified_at is None


def test_repository_reader_preserves_stable_tie_order(
    isolated_database: Path,
):
    project_id = "stable-order"
    created_at = "2026-07-30T01:00:00+00:00"
    _insert_project(
        project_id,
        [
            _operation(
                project_id,
                "operation-a",
                created_at=created_at,
            ),
            _operation(
                project_id,
                "operation-b",
                created_at=created_at,
            ),
        ],
    )
    _backfill(project_id)
    _promote(project_id)

    feed = video_localization_operations.read_operation_feed_v2(
        project_id
    )

    assert feed is not None
    assert [
        operation.operation_id for operation in feed.history
    ] == ["operation-b", "operation-a"]
