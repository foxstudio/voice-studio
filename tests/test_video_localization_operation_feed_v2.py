from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.video_localization_public_contracts import (  # noqa: E402
    PublicVideoLocalizationOperationFeedV2,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.errors import AppException  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings, Project  # noqa: E402
from app.services import database, settings_store  # noqa: E402
from app.services import video_localization_operations  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_summary_store,
)


@pytest.fixture
def isolated_database(tmp_path: Path):
    original_path = database.DB_PATH
    original_settings = settings_store.get().model_copy(deep=True)
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
        settings_store.update(original_settings)


def _operation(
    project_id: str,
    operation_id: str,
    *,
    status: str = "success",
    kind: str = "english_asr",
    created_at: str,
) -> VideoLocalizationOperation:
    return VideoLocalizationOperation.model_validate(
        {
            "operation_id": operation_id,
            "project_id": project_id,
            "kind": kind,
            "status": status,
            "progress": 0.5 if status in {"queued", "running"} else 1,
            "result_summary": {"cue_count": 3},
            "created_at": created_at,
            "completed_at": (
                None
                if status in {"queued", "running"}
                else created_at
            ),
        }
    )


def _insert_verified_project(
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
    backfill = (
        video_localization_operations.backfill_operation_summaries(
            limit=100
        )
    )
    assert any(
        item.project_id == project_id
        and item.status in {"backfilled", "unchanged"}
        for item in backfill.projects
    )
    promotion = (
        video_localization_operations.promote_operation_summaries(
            limit=100
        )
    )
    assert any(
        item.project_id == project_id
        and item.status in {"verified", "unchanged"}
        for item in promotion.projects
    )


def _fixture_operations(
    project_id: str,
) -> list[VideoLocalizationOperation]:
    history = [
        _operation(
            project_id,
            f"history-{index}",
            created_at=f"2026-07-30T01:00:0{index}+00:00",
        )
        for index in range(5)
    ]
    return [
        *history,
        _operation(
            project_id,
            "active-source",
            status="running",
            kind="source_audio",
            created_at="2026-07-30T01:00:06+00:00",
        ),
        _operation(
            project_id,
            "active-stems",
            status="queued",
            kind="stems",
            created_at="2026-07-30T01:00:05+00:00",
        ),
    ]


def test_v2_separates_all_active_operations_from_bounded_history(
    isolated_database: Path,
):
    project_id = "feed-v2-bounded"
    _insert_verified_project(
        project_id,
        _fixture_operations(project_id),
    )

    head = video_localization_operations.read_operation_feed_v2(
        project_id,
        history_limit=2,
    )
    assert head is not None
    assert head.schema_version == "operation-feed-v2"
    assert [
        item.operation_id for item in head.active_operations
    ] == ["active-source", "active-stems"]
    assert [item.operation_id for item in head.history] == [
        "history-4",
        "history-3",
    ]
    assert head.history_total == 5
    assert head.next_cursor

    second = video_localization_operations.read_operation_feed_v2(
        project_id,
        cursor=head.next_cursor,
        history_limit=2,
    )
    assert second is not None
    assert second.active_operations == []
    assert [item.operation_id for item in second.history] == [
        "history-2",
        "history-1",
    ]
    assert second.history_total == 5
    assert second.next_cursor

    third = video_localization_operations.read_operation_feed_v2(
        project_id,
        cursor=second.next_cursor,
        history_limit=2,
    )
    assert third is not None
    assert [item.operation_id for item in third.history] == [
        "history-0",
    ]
    assert third.next_cursor is None


def test_v2_unchanged_head_is_empty(
    isolated_database: Path,
):
    project_id = "feed-v2-unchanged"
    _insert_verified_project(
        project_id,
        _fixture_operations(project_id),
    )
    head = video_localization_operations.read_operation_feed_v2(
        project_id
    )
    assert head is not None

    unchanged = (
        video_localization_operations.read_operation_feed_v2(
            project_id,
            after_revision=head.revision,
        )
    )

    assert unchanged is not None
    assert unchanged.changed is False
    assert unchanged.active_operations == []
    assert unchanged.history == []
    assert unchanged.history_total == 0
    assert unchanged.next_cursor is None


def test_v2_rejects_stale_and_malformed_history_cursors(
    isolated_database: Path,
):
    project_id = "feed-v2-cursor"
    _insert_verified_project(
        project_id,
        _fixture_operations(project_id),
    )
    head = video_localization_operations.read_operation_feed_v2(
        project_id,
        history_limit=2,
    )
    assert head is not None and head.next_cursor
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_projection_state
            SET history_revision = history_revision + 1
            WHERE project_id = ?
            """,
            (project_id,),
        )

    with pytest.raises(AppException) as stale:
        video_localization_operations.read_operation_feed_v2(
            project_id,
            cursor=head.next_cursor,
            history_limit=2,
        )
    with pytest.raises(AppException) as malformed:
        video_localization_operations.read_operation_feed_v2(
            project_id,
            cursor="not-a-valid-cursor",
        )

    assert stale.value.status_code == 409
    assert stale.value.code == (
        "VIDEO_LOCALIZATION_OPERATION_HISTORY_CURSOR_STALE"
    )
    assert malformed.value.status_code == 400
    assert malformed.value.code == (
        "VIDEO_LOCALIZATION_OPERATION_HISTORY_CURSOR_INVALID"
    )


def test_v2_verified_page_does_not_read_project_blob(
    isolated_database: Path,
    monkeypatch,
):
    project_id = "feed-v2-no-blob"
    _insert_verified_project(
        project_id,
        _fixture_operations(project_id),
    )
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

    head = video_localization_operations.read_operation_feed_v2(
        project_id,
        history_limit=2,
    )
    assert head is not None and head.next_cursor
    page = video_localization_operations.read_operation_feed_v2(
        project_id,
        cursor=head.next_cursor,
        history_limit=2,
    )

    assert page is not None
    assert [item.operation_id for item in page.history] == [
        "history-2",
        "history-1",
    ]


def test_v2_public_contract_removes_recursive_locators(
    isolated_database: Path,
):
    project_id = "feed-v2-public"
    operation = _operation(
        project_id,
        "history-public",
        created_at="2026-07-30T01:00:00+00:00",
    ).model_copy(
        update={
            "result_summary": {
                "artifact_path": "/private/result.json",
                "nested": {"output_path": "/private/audio.wav"},
            }
        }
    )
    _insert_verified_project(project_id, [operation])

    feed = video_localization_operations.read_operation_feed_v2(
        project_id
    )
    public = PublicVideoLocalizationOperationFeedV2.model_validate(
        feed,
        from_attributes=True,
    )
    payload = public.model_dump(mode="json")

    encoded = json.dumps(payload, ensure_ascii=False)
    assert "artifact_path" not in encoded
    assert "output_path" not in encoded
    assert payload["history"][0]["detail_available"] is True


def test_v2_http_contract_supports_head_poll_and_keyset_pages(
    isolated_database: Path,
):
    project_id = "feed-v2-http"
    _insert_verified_project(
        project_id,
        _fixture_operations(project_id)[:5],
    )

    with TestClient(app) as client:
        head_response = client.get(
            f"/api/projects/{project_id}/video-localization/"
            "operations/feed-v2",
            params={"history_limit": 2},
        )
        assert head_response.status_code == 200
        head = head_response.json()
        assert head["schema_version"] == "operation-feed-v2"
        assert head["active_operations"] == []
        assert [
            item["operation_id"] for item in head["history"]
        ] == ["history-4", "history-3"]
        assert head["history_total"] == 5
        assert head["next_cursor"]

        unchanged_response = client.get(
            f"/api/projects/{project_id}/video-localization/"
            "operations/feed-v2",
            params={"after_revision": head["revision"]},
        )
        assert unchanged_response.status_code == 200
        assert unchanged_response.json() == {
            "schema_version": "operation-feed-v2",
            "revision": head["revision"],
            "history_revision": head["history_revision"],
            "changed": False,
            "active_operations": [],
            "history": [],
            "history_total": 0,
            "next_cursor": None,
        }

        page_response = client.get(
            f"/api/projects/{project_id}/video-localization/"
            "operations/feed-v2",
            params={
                "cursor": head["next_cursor"],
                "history_limit": 2,
            },
        )
        assert page_response.status_code == 200
        page = page_response.json()
        assert page["active_operations"] == []
        assert [
            item["operation_id"] for item in page["history"]
        ] == ["history-2", "history-1"]

        incompatible_response = client.get(
            f"/api/projects/{project_id}/video-localization/"
            "operations/feed-v2",
            params={
                "cursor": head["next_cursor"],
                "after_revision": head["revision"],
            },
        )
        assert incompatible_response.status_code == 400
        assert incompatible_response.json()["error"]["code"] == (
            "VIDEO_LOCALIZATION_OPERATION_FEED_QUERY_INVALID"
        )

        malformed_response = client.get(
            f"/api/projects/{project_id}/video-localization/"
            "operations/feed-v2",
            params={"cursor": "not-a-valid-cursor"},
        )
        assert malformed_response.status_code == 400
        assert malformed_response.json()["error"]["code"] == (
            "VIDEO_LOCALIZATION_OPERATION_HISTORY_CURSOR_INVALID"
        )

        oversized_response = client.get(
            f"/api/projects/{project_id}/video-localization/"
            "operations/feed-v2",
            params={"history_limit": 101},
        )
        assert oversized_response.status_code == 400
        assert oversized_response.json()["error"]["code"] == (
            "INVALID_REQUEST"
        )

        missing_response = client.get(
            "/api/projects/missing/video-localization/"
            "operations/feed-v2"
        )
        assert missing_response.status_code == 404
