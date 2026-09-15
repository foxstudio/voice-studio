from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import database  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_execution as operation_execution,
)


UTC = timezone.utc
T0 = datetime(2026, 7, 30, 1, 0, tzinfo=UTC)


def test_acquire_execution_claim_reports_foreign_active_lease(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    first = operation_execution.acquire_execution_claim(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        lease_duration=timedelta(seconds=10),
        heartbeat_interval_seconds=1,
        busy_timeout_ms=0,
        clock=lambda: T0,
    )
    blocked = operation_execution.acquire_execution_claim(
        "project-1",
        "operation-1",
        runner_id="runner-2",
        lease_duration=timedelta(seconds=10),
        heartbeat_interval_seconds=1,
        busy_timeout_ms=0,
        clock=lambda: T0 + timedelta(seconds=1),
    )

    assert first.acquired is True
    assert first.claim is not None
    assert blocked.outcome == "active_lease"
    assert blocked.claim is None
    assert (
        blocked.retry_at_ms
        == first.claim.attempt.lease_expires_at_ms
    )


def test_heartbeat_renews_and_finish_closes_durable_attempt(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    decision = operation_execution.acquire_execution_claim(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        lease_duration=timedelta(milliseconds=300),
        heartbeat_interval_seconds=0.02,
        busy_timeout_ms=0,
    )
    claim = decision.claim
    assert claim is not None
    initial_heartbeat = claim.attempt.heartbeat_at_ms

    claim.start_heartbeat()
    deadline = time.monotonic() + 1
    renewed = None
    while time.monotonic() < deadline:
        renewed = attempt_store.active_claim(
            "project-1",
            "operation-1",
            observed_at=datetime.now(UTC),
        )
        if (
            renewed is not None
            and renewed.heartbeat_at_ms != initial_heartbeat
        ):
            break
        time.sleep(0.01)

    assert renewed is not None
    assert renewed.heartbeat_at_ms != initial_heartbeat
    assert claim.finish(status="success") is True
    stored = attempt_store.list_attempts("project-1", "operation-1")
    assert stored[-1].status == "success"
    assert stored[-1].completed_at is not None


def test_failed_heartbeat_marks_claim_lost_and_prevents_finish(
    tmp_path: Path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    decision = operation_execution.acquire_execution_claim(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        lease_duration=timedelta(milliseconds=300),
        heartbeat_interval_seconds=0.02,
        busy_timeout_ms=0,
    )
    claim = decision.claim
    assert claim is not None
    monkeypatch.setattr(
        attempt_store,
        "heartbeat_claim",
        lambda *_args, **_kwargs: False,
    )

    claim.start_heartbeat()
    deadline = time.monotonic() + 1
    while not claim.lost and time.monotonic() < deadline:
        time.sleep(0.01)

    assert claim.lost is True
    assert claim.finish(status="success") is False
    assert attempt_store.list_attempts(
        "project-1",
        "operation-1",
    )[-1].status == (
        "running"
    )


@pytest.mark.parametrize(
    ("lease_duration", "heartbeat_interval", "busy_timeout_ms"),
    [
        (timedelta(seconds=3), 1, 0),
        (timedelta(seconds=4), 1, 1_000),
        (timedelta(seconds=10), 0, 0),
    ],
)
def test_execution_timing_rejects_missing_safety_margin(
    tmp_path: Path,
    lease_duration: timedelta,
    heartbeat_interval: float,
    busy_timeout_ms: int,
):
    database.set_db_path(tmp_path / "voice_studio.db")

    with pytest.raises(ValueError):
        operation_execution.acquire_execution_claim(
            "project-1",
            "operation-1",
            runner_id="runner-1",
            lease_duration=lease_duration,
            heartbeat_interval_seconds=heartbeat_interval,
            busy_timeout_ms=busy_timeout_ms,
            clock=lambda: T0,
        )

    assert attempt_store.list_attempts(
        "project-1",
        "operation-1",
    ) == []
