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
    operation_summary_legacy,
    operation_summary_projection,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.video_localization_operation_summary import (  # noqa: E402
    OperationSummaryCoreV1,
    operation_summary_core_fingerprint,
    operation_summary_core_json,
)
from app.schemas.voice_studio import AppSettings, Project  # noqa: E402
from app.services import database, settings_store  # noqa: E402
from app.services import video_localization_operations  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_summary_audit,
)
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
        database.set_db_path(original_path)


def _operation(
    operation_id: str,
    project_id: str,
    *,
    kind: str = "source_audio",
    status: str = "success",
    created_at: str = "2026-07-30T01:00:00+00:00",
    progress: float = 1.0,
) -> VideoLocalizationOperation:
    return VideoLocalizationOperation.model_validate(
        {
            "operation_id": operation_id,
            "project_id": project_id,
            "kind": kind,
            "status": status,
            "progress": progress,
            "result_summary": {"cue_count": 3},
            "created_at": created_at,
            "completed_at": (
                "2026-07-30T01:01:00+00:00"
                if status in {"success", "failed", "cancelled"}
                else None
            ),
        }
    )


def _project(
    project_id: str,
    operations: list[VideoLocalizationOperation] | None,
    *,
    updated_at: str = "2026-07-30T02:00:00+00:00",
) -> Project:
    parameters = (
        {}
        if operations is None
        else {
            "video_localization": VideoLocalizationDraft(
                operations=operations,
                updated_at="2026-07-30T01:59:00+00:00",
            ).model_dump(mode="json")
        }
    )
    return Project(
        project_id=project_id,
        name=project_id,
        parameters=parameters,
        updated_at=updated_at,
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


def _summary_core(
    project_id: str,
    operation_id: str,
) -> OperationSummaryCoreV1:
    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT core_json
            FROM video_localization_operation_summaries
            WHERE project_id = ? AND operation_id = ?
            """,
            (project_id, operation_id),
        ).fetchone()
    assert row is not None
    return OperationSummaryCoreV1.model_validate_json(
        str(row["core_json"])
    )


def test_backfill_is_bounded_resumable_idempotent_and_preserves_business_time(
    isolated_database: Path,
):
    _insert_project(_project("project-a", None))
    for suffix in ("b", "c"):
        project_id = f"project-{suffix}"
        _insert_project(
            _project(
                project_id,
                [
                    _operation(
                        f"operation-{suffix}",
                        project_id,
                        created_at=(
                            f"2026-07-30T01:0"
                            f"{1 if suffix == 'b' else 2}:00+00:00"
                        ),
                    )
                ],
            )
        )

    first = video_localization_operations.backfill_operation_summaries(
        limit=2
    )
    second = video_localization_operations.backfill_operation_summaries(
        after_project_id=first.next_cursor,
        limit=2,
    )
    repeated = (
        video_localization_operations.backfill_operation_summaries(
            limit=10
        )
    )

    assert first.truncated is True
    assert first.next_cursor == "project-b"
    assert [item.status for item in first.projects] == [
        "skipped",
        "backfilled",
    ]
    assert second.truncated is False
    assert [
        (item.project_id, item.status)
        for item in second.projects
    ] == [("project-c", "backfilled")]
    assert repeated.backfilled_project_count == 0
    assert repeated.unchanged_project_count == 2
    with database.conn() as connection:
        updated_times = {
            str(row["project_id"]): str(row["updated_at"])
            for row in connection.execute(
                """
                SELECT project_id, updated_at
                FROM projects
                ORDER BY project_id
                """
            ).fetchall()
        }
    assert set(updated_times.values()) == {
        "2026-07-30T02:00:00+00:00"
    }
    assert (
        video_localization_operations
        .reconcile_operation_summaries()
        .healthy
        is True
    )


def test_backfill_snapshots_legacy_media_facts_without_touching_business_time(
    isolated_database: Path,
):
    project_id = "project-media"
    operation = _operation(
        "operation-media",
        project_id,
        kind="source_audio",
    ).model_copy(
        update={"result_summary": {"stage": "源音轨已抽取"}}
    )
    draft = VideoLocalizationDraft.model_validate(
        {
            "source_media": {
                "audio_path": "/managed/source.wav",
                "duration_ms": 12_345,
                "metadata": {
                    "audio_sample_rate": 48_000,
                    "audio_channels": 2,
                    "audio_extract_status": "completed",
                },
            },
            "operations": [operation],
            "updated_at": "2026-07-30T01:59:00+00:00",
        }
    )
    project = Project(
        project_id=project_id,
        name=project_id,
        parameters={
            "video_localization": draft.model_dump(mode="json")
        },
        updated_at="2026-07-30T02:00:00+00:00",
    )
    _insert_project(project)

    report = (
        video_localization_operations.backfill_operation_summaries(
            limit=10
        )
    )
    core = _summary_core(project_id, "operation-media")
    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT data, updated_at
            FROM projects
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
    assert row is not None
    persisted = json.loads(str(row["data"]))
    persisted_summary = persisted["parameters"][
        "video_localization"
    ]["operations"][0]["result_summary"]

    assert report.backfilled_project_count == 1
    assert str(row["updated_at"]) == (
        "2026-07-30T02:00:00+00:00"
    )
    assert persisted_summary["duration_ms"] == 12_345
    assert persisted_summary["sample_rate"] == 48_000
    assert persisted_summary["channels"] == 2
    assert persisted_summary["audio_extract_status"] == "completed"
    assert persisted_summary["track_count"] == 1
    assert core.result_summary["duration_ms"] == 12_345
    assert core.result_summary["track_count"] == 1
    assert "audio_path" not in persisted_summary
    assert "audio_path" not in core.result_summary


def test_backfill_scopes_cloned_operation_ids_to_each_project(
    isolated_database: Path,
):
    original = _operation("shared-operation", "project-a")
    _insert_project(_project("project-a", [original]))
    _insert_project(_project("project-b", [original]))

    report = (
        video_localization_operations.backfill_operation_summaries(
            limit=10
        )
    )

    assert report.backfilled_project_count == 2
    assert _summary_core(
        "project-a",
        "shared-operation",
    ).project_id == "project-a"
    assert _summary_core(
        "project-b",
        "shared-operation",
    ).project_id == "project-b"
    with database.conn() as connection:
        project_b = json.loads(
            connection.execute(
                """
                SELECT data FROM projects
                WHERE project_id = 'project-b'
                """
            ).fetchone()["data"]
        )
    assert (
        project_b["parameters"]["video_localization"]
        ["operations"][0]["project_id"]
        == "project-b"
    )
    assert (
        video_localization_operations
        .reconcile_operation_summaries()
        .healthy
        is True
    )


def test_bad_projects_are_isolated_and_explicit_rerun_repairs_them(
    isolated_database: Path,
):
    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO projects (project_id, data, updated_at)
            VALUES ('project-a', '{broken', 'fixed-business-time')
            """
        )
    duplicate_active = [
        _operation(
            f"active-{index}",
            "project-b",
            status="running",
            progress=0.2,
        )
        for index in range(2)
    ]
    _insert_project(_project("project-b", duplicate_active))
    _insert_project(
        _project(
            "project-c",
            [_operation("valid-operation", "project-c")],
        )
    )

    report = (
        video_localization_operations.backfill_operation_summaries(
            limit=10
        )
    )

    assert [
        (item.project_id, item.status, item.category)
        for item in report.projects
    ] == [
        (
            "project-a",
            "repair_required",
            "legacy_project_invalid",
        ),
        (
            "project-b",
            "repair_required",
            "active_kind_conflict",
        ),
        ("project-c", "backfilled", "none"),
    ]
    assert (
        video_localization_operation_summary_store
        .read_projection_state("project-a")
        .summary_status
        == "repair_required"
    )
    assert (
        video_localization_operation_summary_store
        .read_projection_state("project-b")
        .summary_status
        == "repair_required"
    )
    audit = (
        video_localization_operations
        .reconcile_operation_summaries()
    )
    assert {
        issue.category for issue in audit.issues
    } >= {
        "legacy_project_invalid",
        "active_kind_conflict",
        "projection_repair_required",
    }
    with database.conn() as connection:
        fixed = _project(
            "project-a",
            [_operation("recovered", "project-a")],
            updated_at="fixed-business-time",
        )
        connection.execute(
            """
            UPDATE projects
            SET data = ?
            WHERE project_id = 'project-a'
            """,
            (
                json.dumps(
                    fixed.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
            ),
        )

    repaired = (
        video_localization_operations.backfill_operation_summaries(
            limit=1
        )
    )

    assert repaired.projects[0].status == "backfilled"
    assert (
        video_localization_operation_summary_store
        .read_projection_state("project-a")
        .summary_status
        == "shadow"
    )
    with database.conn() as connection:
        assert (
            connection.execute(
                """
                SELECT updated_at FROM projects
                WHERE project_id = 'project-a'
                """
            ).fetchone()["updated_at"]
            == "fixed-business-time"
        )


def test_corrupt_shadow_requires_a_marked_then_explicit_repair_rerun(
    isolated_database: Path,
):
    _insert_project(
        _project(
            "project-a",
            [_operation("operation-a", "project-a")],
        )
    )
    video_localization_operations.backfill_operation_summaries()
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_projection_state
            SET history_revision = 7
            WHERE project_id = 'project-a'
            """
        )
        connection.execute(
            """
            UPDATE video_localization_operation_summaries
            SET core_json = '{"broken":true}'
            WHERE project_id = 'project-a'
            """
        )

    marked = (
        video_localization_operations.backfill_operation_summaries()
    )

    assert marked.projects[0].status == "repair_required"
    assert marked.projects[0].category == "projection_conflict"
    with database.conn() as connection:
        assert (
            connection.execute(
                """
                SELECT core_json
                FROM video_localization_operation_summaries
                WHERE project_id = 'project-a'
                """
            ).fetchone()["core_json"]
            == '{"broken":true}'
        )

    repaired = (
        video_localization_operations.backfill_operation_summaries()
    )

    assert repaired.projects[0].status == "backfilled"
    assert _summary_core(
        "project-a",
        "operation-a",
    ).operation_id == "operation-a"
    assert (
        video_localization_operation_summary_store
        .read_projection_state("project-a")
        .history_revision
        == 7
    )


def test_reconciliation_classifies_bidirectional_core_state_order_and_locator_drift(
    isolated_database: Path,
):
    tied_time = "2026-07-30T01:00:00+00:00"
    _insert_project(
        _project(
            "project-order",
            [
                _operation(
                    "operation-a",
                    "project-order",
                    created_at=tied_time,
                ),
                _operation(
                    "operation-z",
                    "project-order",
                    created_at=tied_time,
                ),
            ],
        )
    )
    _insert_project(
        _project(
            "project-drift",
            [_operation("operation-drift", "project-drift")],
        )
    )
    video_localization_operations.backfill_operation_summaries()
    core = _summary_core("project-drift", "operation-drift")
    changed = core.model_copy(
        update={
            "progress": 0.5,
            "result_summary": {
                **core.result_summary,
                "artifact_path": "/private/result.json",
            },
        }
    )
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_summaries
            SET core_json = ?, content_fingerprint = ?
            WHERE project_id = 'project-drift'
              AND operation_id = 'operation-drift'
            """,
            (
                operation_summary_core_json(changed),
                operation_summary_core_fingerprint(changed),
            ),
        )
        connection.execute(
            """
            UPDATE video_localization_operations
            SET status = 'failed', state_revision = state_revision + 1
            WHERE project_id = 'project-drift'
              AND operation_id = 'operation-drift'
            """
        )
        connection.execute(
            """
            UPDATE video_localization_operations
            SET created_at = '2026-07-30T01:01:00+00:00'
            WHERE project_id = 'project-order'
              AND operation_id = 'operation-a'
            """
        )
        connection.execute(
            """
            DELETE FROM video_localization_operation_summaries
            WHERE project_id = 'project-order'
              AND operation_id = 'operation-a'
            """
        )

    report = (
        video_localization_operations
        .reconcile_operation_summaries()
    )
    categories = {issue.category for issue in report.issues}

    assert report.healthy is False
    assert {
        "ordering_mismatch",
        "summary_operation_missing",
        "projection_state_mismatch",
        "state_mismatch",
        "ledger_revision_mismatch",
        "summary_core_mismatch",
        "public_locator_exposed",
    } <= categories
    serialized = json.dumps(
        [issue.__dict__ for issue in report.issues]
    )
    assert "/private/result.json" not in serialized
    assert "core_json" not in serialized


def test_reconciliation_uses_one_sqlite_snapshot(
    isolated_database: Path,
):
    project_id = "project-snapshot"
    operation = _operation("operation-a", project_id)
    _insert_project(_project(project_id, [operation]))
    video_localization_operations.backfill_operation_summaries()
    expected = (
        video_localization_operation_summary_audit
        .ExpectedOperationSummary(
            core=(
                operation_summary_projection
                .operation_summary_core_from_operation(operation)
            ),
            kind=operation.kind,
            status=operation.status,
            cancel_requested=operation.cancel_requested,
            created_at=operation.created_at,
            completed_at=operation.completed_at,
        ),
    )
    reader = sqlite3.connect(database.DB_PATH)
    reader.row_factory = sqlite3.Row
    writer = sqlite3.connect(database.DB_PATH)
    try:
        reader.execute("BEGIN")
        reader.execute(
            "SELECT data FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        writer.execute(
            """
            UPDATE video_localization_operations
            SET status = 'failed', state_revision = state_revision + 1
            WHERE project_id = ? AND operation_id = 'operation-a'
            """,
            (project_id,),
        )
        writer.commit()

        same_snapshot = (
            video_localization_operation_summary_audit
            .reconcile_project_snapshot(
                reader,
                project_id,
                project_exists=True,
                expected_operations=expected,
            )
        )
    finally:
        reader.close()
        writer.close()

    assert same_snapshot.issues == ()
    assert same_snapshot.matched_count == 1
    current = (
        video_localization_operations
        .reconcile_operation_summaries()
    )
    assert {
        issue.category for issue in current.issues
    } >= {"state_mismatch", "ledger_revision_mismatch"}


def test_reconciliation_bounds_reported_compound_ids(
    isolated_database: Path,
):
    project_id = "project-bounded-issues"
    _insert_project(
        _project(
            project_id,
            [_operation("operation-a", project_id)],
        )
    )
    video_localization_operations.backfill_operation_summaries()
    with database.conn() as connection:
        connection.execute(
            """
            DELETE FROM video_localization_operation_summaries
            WHERE project_id = ?
            """,
            (project_id,),
        )

    report = (
        video_localization_operations
        .reconcile_operation_summaries(limit=1)
    )

    assert report.total_project_count == 1
    assert report.checked_project_count == 1
    assert report.total_issue_count >= 2
    assert len(report.issues) == 1
    assert report.truncated is True
    assert report.healthy is False


def test_audit_cli_is_read_only_and_check_rejects_truncation(
    isolated_database: Path,
):
    for index in range(2):
        project_id = f"project-{index}"
        _insert_project(
            _project(
                project_id,
                [_operation(f"operation-{index}", project_id)],
            )
        )
    video_localization_operations.backfill_operation_summaries()
    environment = {
        **os.environ,
        "VOICE_STUDIO_DB_PATH": str(database.DB_PATH),
    }
    watcher = sqlite3.connect(database.DB_PATH)
    try:
        before = int(
            watcher.execute("PRAGMA data_version").fetchone()[0]
        )
        truncated = subprocess.run(
            [
                sys.executable,
                str(
                    ROOT
                    / "scripts"
                    / "audit_video_localization_operation_summaries.py"
                ),
                "--limit",
                "1",
                "--format",
                "json",
                "--check",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        after = int(
            watcher.execute("PRAGMA data_version").fetchone()[0]
        )
    finally:
        watcher.close()

    assert truncated.returncode == 1
    payload = json.loads(truncated.stdout)
    assert payload["truncated"] is True
    assert payload["issues"] == []
    assert after == before

    complete = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / "audit_video_localization_operation_summaries.py"
            ),
            "--limit",
            "10",
            "--format",
            "json",
            "--check",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert complete.returncode == 0
    assert json.loads(complete.stdout)["issues"] == []


def test_backfill_cli_resumes_cleanly_in_a_new_process(
    isolated_database: Path,
):
    for index in range(2):
        project_id = f"project-{index}"
        _insert_project(
            _project(
                project_id,
                [_operation(f"operation-{index}", project_id)],
            )
        )
    environment = {
        **os.environ,
        "VOICE_STUDIO_DB_PATH": str(database.DB_PATH),
    }
    first = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / "backfill_video_localization_operation_summaries.py"
            ),
            "--limit",
            "1",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    first_payload = json.loads(first.stdout)
    resumed = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / "backfill_video_localization_operation_summaries.py"
            ),
            "--limit",
            "1",
            "--after-project-id",
            first_payload["next_cursor"],
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert first.returncode == 0
    assert first_payload["truncated"] is True
    assert resumed.returncode == 0
    assert json.loads(resumed.stdout)["projects"][0]["project_id"] == (
        "project-1"
    )
    assert (
        video_localization_operations
        .reconcile_operation_summaries()
        .healthy
        is True
    )


def test_promotion_cli_is_bounded_resumable_and_idempotent(
    isolated_database: Path,
):
    for index in range(2):
        project_id = f"project-{index}"
        _insert_project(
            _project(
                project_id,
                [_operation(f"operation-{index}", project_id)],
            )
        )
    _insert_project(_project("project-nonlocal", None))
    video_localization_operations.backfill_operation_summaries()
    environment = {
        **os.environ,
        "VOICE_STUDIO_DB_PATH": str(database.DB_PATH),
    }
    command = [
        sys.executable,
        str(
            ROOT
            / "scripts"
            / "promote_video_localization_operation_summaries.py"
        ),
        "--limit",
        "1",
        "--format",
        "json",
    ]
    first = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    first_payload = json.loads(first.stdout)
    resumed = subprocess.run(
        [
            *command,
            "--after-project-id",
            first_payload["next_cursor"],
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    repeated = subprocess.run(
        [
            *command,
            "--limit",
            "10",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert first.returncode == 0
    assert first_payload["truncated"] is True
    assert first_payload["verified_project_count"] == 1
    assert resumed.returncode == 0
    assert json.loads(resumed.stdout)["verified_project_count"] == 1
    assert repeated.returncode == 0
    repeated_payload = json.loads(repeated.stdout)
    assert repeated_payload["verified_project_count"] == 0
    assert repeated_payload["unchanged_project_count"] == 2
    assert repeated_payload["skipped_project_count"] == 1
    with database.conn() as connection:
        states = connection.execute(
            """
            SELECT summary_status, last_verified_at
            FROM video_localization_operation_projection_state
            ORDER BY project_id
            """
        ).fetchall()
    assert [str(row["summary_status"]) for row in states] == [
        "verified",
        "verified",
    ]
    assert all(row["last_verified_at"] for row in states)


def test_promotion_serializes_with_a_concurrent_runtime_writer(
    isolated_database: Path,
):
    project_id = "project-promotion-writer"
    operation = _operation(
        "operation-writer",
        project_id,
        status="running",
        progress=0.25,
    )
    _insert_project(_project(project_id, [operation]))
    video_localization_operations.backfill_operation_summaries()
    promotion_entered = threading.Event()
    release_promotion = threading.Event()
    writer_completed = threading.Event()
    decode_count = 0
    decode_lock = threading.Lock()
    promotion_result = []
    writer_result = []

    def blocking_decoder(*args):
        nonlocal decode_count
        with decode_lock:
            decode_count += 1
            current_count = decode_count
        if current_count == 1:
            promotion_entered.set()
            assert release_promotion.wait(timeout=3)
        return (
            operation_summary_legacy
            .decode_legacy_summary_source(*args)
        )

    def promote() -> None:
        promotion_result.append(
            video_localization_operation_summary_migration
            .promote_operation_summaries(
                blocking_decoder,
                limit=10,
            )
        )

    def write_runtime_update() -> None:
        writer_result.append(
            draft_store.save(
                project_id,
                VideoLocalizationDraft(
                    operations=[
                        operation.model_copy(
                            update={"progress": 0.75}
                        )
                    ]
                ),
                intent="runtime",
            )
        )
        writer_completed.set()

    promotion_thread = threading.Thread(target=promote)
    writer_thread = threading.Thread(
        target=write_runtime_update
    )
    promotion_thread.start()
    assert promotion_entered.wait(timeout=2)
    writer_thread.start()
    time.sleep(0.05)
    assert writer_completed.is_set() is False
    release_promotion.set()
    promotion_thread.join(timeout=3)
    writer_thread.join(timeout=3)

    assert promotion_thread.is_alive() is False
    assert writer_thread.is_alive() is False
    assert promotion_result[0].verified_project_count == 1
    assert writer_result[0] is not None
    state = (
        video_localization_operation_summary_store
        .read_projection_state(project_id)
    )
    core = _summary_core(project_id, "operation-writer")
    assert state is not None
    assert state.summary_status == "verified"
    assert core.progress == 0.75
    assert (
        video_localization_operations
        .reconcile_operation_summaries()
        .healthy
        is True
    )


def test_backfill_has_no_process_path_cache_after_database_replacement(
    isolated_database: Path,
):
    target_path = database.DB_PATH
    _insert_project(
        _project(
            "old-project",
            [_operation("old-operation", "old-project")],
        )
    )
    video_localization_operations.backfill_operation_summaries()

    replacement_path = (
        isolated_database / "replacement.db"
    )
    database.set_db_path(replacement_path)
    _insert_project(
        _project(
            "new-project",
            [_operation("new-operation", "new-project")],
        )
    )
    for suffix in ("-wal", "-shm"):
        assert not Path(f"{replacement_path}{suffix}").exists()
    os.replace(replacement_path, target_path)
    database.set_db_path(target_path)

    report = (
        video_localization_operations.backfill_operation_summaries()
    )

    assert [
        (item.project_id, item.status)
        for item in report.projects
    ] == [("new-project", "backfilled")]
    assert _summary_core(
        "new-project",
        "new-operation",
    ).project_id == "new-project"
