from __future__ import annotations

import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import database
from app.services import (
    video_localization_operation_attempt_store as attempt_store,
)


UTC = timezone.utc
T0 = datetime(2026, 7, 30, 1, 0, tzinfo=UTC)


def test_active_lease_blocks_second_claim(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")

    first = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        observed_at=T0,
        lease_duration=timedelta(seconds=30),
    )
    second = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-2",
        observed_at=T0 + timedelta(seconds=1),
        lease_duration=timedelta(seconds=30),
    )

    assert first.outcome == "acquired"
    assert first.acquired is True
    assert first.attempt.attempt_number == 1
    assert first.attempt.fencing_token == 1
    assert first.attempt.is_durable_claim is True
    assert second.outcome == "active_lease"
    assert second.acquired is False
    assert second.attempt.attempt_id == first.attempt.attempt_id
    assert len(
        attempt_store.list_attempts("project-1", "operation-1")
    ) == 1


def test_same_operation_id_isolated_by_project(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")

    first = attempt_store.claim_attempt(
        "project-1",
        "shared-operation",
        runner_id="runner-1",
        observed_at=T0,
        lease_duration=timedelta(seconds=30),
    )
    second = attempt_store.claim_attempt(
        "project-2",
        "shared-operation",
        runner_id="runner-2",
        observed_at=T0,
        lease_duration=timedelta(seconds=30),
    )

    assert first.acquired is True
    assert second.acquired is True
    assert first.attempt.attempt_number == 1
    assert second.attempt.attempt_number == 1
    assert first.attempt.fencing_token == 1
    assert second.attempt.fencing_token == 1
    assert attempt_store.active_claim(
        "project-1",
        "shared-operation",
        observed_at=T0 + timedelta(seconds=1),
    ) == first.attempt
    assert attempt_store.active_claim(
        "project-2",
        "shared-operation",
        observed_at=T0 + timedelta(seconds=1),
    ) == second.attempt
    assert attempt_store.heartbeat_claim(
        first.attempt.attempt_id,
        "project-2",
        first.attempt.operation_id,
        runner_id=first.attempt.runner_id,
        fencing_token=first.attempt.fencing_token,
        observed_at=T0 + timedelta(seconds=1),
        lease_duration=timedelta(seconds=30),
    ) is False
    inventory = attempt_store.latest_attempt_inventory()
    assert inventory.total_attempt_count == 2
    assert inventory.operation_count == 2
    assert len(inventory.attempts) == 2


def test_concurrent_claim_has_exactly_one_winner(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")
    with database.conn():
        pass
    release = threading.Event()
    decisions: list[attempt_store.ClaimDecision] = []
    errors: list[BaseException] = []

    def claim(runner_id: str) -> None:
        release.wait(timeout=1)
        try:
            decisions.append(
                attempt_store.claim_attempt(
                    "project-1",
                    "operation-1",
                    runner_id=runner_id,
                    observed_at=T0,
                    lease_duration=timedelta(seconds=30),
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=claim, args=(f"runner-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(item.outcome for item in decisions) == [
        "acquired",
        "active_lease",
    ]
    attempts = attempt_store.list_attempts("project-1", "operation-1")
    assert len(attempts) == 1
    assert attempts[0].fencing_token == 1


def test_newer_non_lease_shadow_row_cannot_mask_active_claim(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    active = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        observed_at=T0,
        lease_duration=timedelta(seconds=30),
    ).attempt
    shadow = attempt_store.begin_attempt(
        "project-1",
        "operation-1",
        runner_id="legacy-shadow-runner",
        started_at="2026-07-30T01:00:01",
    )

    blocked = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-2",
        observed_at=T0 + timedelta(seconds=2),
        lease_duration=timedelta(seconds=30),
    )

    assert shadow.attempt_number == 2
    assert shadow.is_durable_claim is False
    assert blocked.outcome == "active_lease"
    assert blocked.attempt.attempt_id == active.attempt_id
    assert len(
        attempt_store.list_attempts("project-1", "operation-1")
    ) == 2


def test_expired_lease_can_be_reclaimed_and_fences_old_runner(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    first = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        observed_at=T0,
        lease_duration=timedelta(seconds=5),
    ).attempt

    second_decision = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-2",
        observed_at=T0 + timedelta(seconds=5),
        lease_duration=timedelta(seconds=20),
    )
    second = second_decision.attempt

    assert second_decision.acquired is True
    assert second.attempt_number == 2
    assert second.fencing_token == 2
    assert attempt_store.heartbeat_claim(
        first.attempt_id,
        first.project_id,
        first.operation_id,
        runner_id=first.runner_id,
        fencing_token=first.fencing_token,
        observed_at=T0 + timedelta(seconds=6),
        lease_duration=timedelta(seconds=20),
    ) is False
    assert attempt_store.finish_claim(
        first.attempt_id,
        first.project_id,
        first.operation_id,
        runner_id=first.runner_id,
        fencing_token=first.fencing_token,
        status="success",
        observed_at=T0 + timedelta(seconds=6),
    ) is False
    assert attempt_store.owns_active_claim(
        second.attempt_id,
        second.project_id,
        second.operation_id,
        runner_id=second.runner_id,
        fencing_token=second.fencing_token,
        observed_at=T0 + timedelta(seconds=6),
    ) is True


def test_heartbeat_extends_only_a_live_monotonic_claim(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")
    claim = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        observed_at=T0,
        lease_duration=timedelta(seconds=10),
    ).attempt

    assert attempt_store.heartbeat_claim(
        claim.attempt_id,
        claim.project_id,
        claim.operation_id,
        runner_id=claim.runner_id,
        fencing_token=claim.fencing_token,
        observed_at=T0 + timedelta(seconds=5),
        lease_duration=timedelta(seconds=10),
    ) is True
    assert attempt_store.heartbeat_claim(
        claim.attempt_id,
        claim.project_id,
        claim.operation_id,
        runner_id=claim.runner_id,
        fencing_token=claim.fencing_token,
        observed_at=T0 + timedelta(seconds=4),
        lease_duration=timedelta(seconds=10),
    ) is False
    assert attempt_store.owns_active_claim(
        claim.attempt_id,
        claim.project_id,
        claim.operation_id,
        runner_id=claim.runner_id,
        fencing_token=claim.fencing_token,
        observed_at=T0 + timedelta(seconds=14, milliseconds=999),
    ) is True
    assert attempt_store.owns_active_claim(
        claim.attempt_id,
        claim.project_id,
        claim.operation_id,
        runner_id=claim.runner_id,
        fencing_token=claim.fencing_token,
        observed_at=T0 + timedelta(seconds=15),
    ) is False


def test_finish_claim_requires_live_matching_fence(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")
    claim = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        observed_at=T0,
        lease_duration=timedelta(seconds=10),
    ).attempt

    assert attempt_store.finish_claim(
        claim.attempt_id,
        claim.project_id,
        claim.operation_id,
        runner_id="runner-2",
        fencing_token=claim.fencing_token,
        status="success",
        observed_at=T0 + timedelta(seconds=1),
    ) is False
    assert attempt_store.finish_claim(
        claim.attempt_id,
        claim.project_id,
        claim.operation_id,
        runner_id=claim.runner_id,
        fencing_token=claim.fencing_token + 1,
        status="success",
        observed_at=T0 + timedelta(seconds=1),
    ) is False
    assert attempt_store.finish_claim(
        claim.attempt_id,
        claim.project_id,
        claim.operation_id,
        runner_id=claim.runner_id,
        fencing_token=claim.fencing_token,
        status="success",
        observed_at=T0 + timedelta(seconds=9),
    ) is True
    assert attempt_store.finish_claim(
        claim.attempt_id,
        claim.project_id,
        claim.operation_id,
        runner_id=claim.runner_id,
        fencing_token=claim.fencing_token,
        status="failed",
        observed_at=T0 + timedelta(seconds=9),
    ) is False


def test_claim_rejects_ambiguous_time_and_non_positive_lease(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")

    with pytest.raises(
        ValueError,
        match="observed_at must be timezone-aware",
    ):
        attempt_store.claim_attempt(
            "project-1",
            "operation-1",
            runner_id="runner-1",
            observed_at=datetime(2026, 7, 30, 1, 0),
            lease_duration=timedelta(seconds=1),
        )
    with pytest.raises(
        ValueError,
        match="lease_duration must be at least 1 millisecond",
    ):
        attempt_store.claim_attempt(
            "project-1",
            "operation-1",
            runner_id="runner-1",
            observed_at=T0,
            lease_duration=timedelta(0),
        )


def test_compatible_migration_preserves_legacy_shadow_attempt(
    tmp_path: Path,
):
    db_path = tmp_path / "voice_studio.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE video_localization_operation_attempts (
                attempt_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                runner_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                completed_at TEXT,
                error_code TEXT,
                UNIQUE (operation_id, attempt_number)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO video_localization_operation_attempts (
                attempt_id,
                project_id,
                operation_id,
                attempt_number,
                runner_id,
                status,
                started_at,
                heartbeat_at
            ) VALUES (
                'attempt-1',
                'project-1',
                'operation-1',
                1,
                'runner-1',
                'running',
                '2026-07-30T01:00:00',
                '2026-07-30T01:00:00'
            )
            """
        )
    database.set_db_path(db_path)

    with database.conn() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(video_localization_operation_attempts)"
            ).fetchall()
        }
        table_sql = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'video_localization_operation_attempts'
            """
        ).fetchone()["sql"]

    assert {
        "fencing_token",
        "heartbeat_at_ms",
        "lease_expires_at_ms",
    }.issubset(columns)
    assert (
        "UNIQUE (project_id, operation_id, attempt_number)"
        in table_sql
    )
    legacy = attempt_store.list_attempts("project-1", "operation-1")
    assert len(legacy) == 1
    assert legacy[0].fencing_token == 0
    assert legacy[0].heartbeat_at_ms is None
    assert legacy[0].lease_expires_at_ms is None
    assert legacy[0].is_durable_claim is False
    cloned = attempt_store.begin_attempt(
        "project-2",
        "operation-1",
        runner_id="runner-2",
        started_at="2026-07-30T01:00:01",
    )
    assert cloned.attempt_number == 1
