from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import database  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.domains.video_localization import operation_queue  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.voice_studio import Project  # noqa: E402
from app.services import project_store  # noqa: E402
from app.services import video_localization_operation_attempt_audit  # noqa: E402
from app.services.video_localization_execution_fence import (  # noqa: E402
    ExecutionFence,
)


def _save_authoritative_operation(
    project_id: str,
    operation_id: str,
    *,
    status: str,
) -> VideoLocalizationOperation:
    operation = VideoLocalizationOperation(
        operation_id=operation_id,
        project_id=project_id,
        kind="english_asr",
        status=status,
    )
    project_store.save_project(
        Project(
            project_id=project_id,
            name=project_id,
            parameters={
                "video_localization": VideoLocalizationDraft(
                    operations=[operation]
                ).model_dump(mode="json"),
            },
        )
    )
    return operation


def test_attempt_lifecycle_is_append_only_and_runner_owned(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    attempt = attempt_store.begin_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-a",
        started_at="2026-07-30T01:00:00",
    )

    assert attempt.attempt_number == 1
    assert attempt_store.heartbeat_attempt(
        attempt.attempt_id,
        runner_id="runner-b",
        heartbeat_at="2026-07-30T01:00:01",
    ) is False
    assert attempt_store.heartbeat_attempt(
        attempt.attempt_id,
        runner_id="runner-a",
        heartbeat_at="2026-07-30T01:00:02",
    ) is True
    assert attempt_store.finish_attempt(
        attempt.attempt_id,
        runner_id="runner-b",
        status="failed",
        completed_at="2026-07-30T01:00:03",
    ) is False
    assert attempt_store.finish_attempt(
        attempt.attempt_id,
        runner_id="runner-a",
        status="success",
        completed_at="2026-07-30T01:00:04",
    ) is True
    assert attempt_store.finish_attempt(
        attempt.attempt_id,
        runner_id="runner-a",
        status="failed",
        completed_at="2026-07-30T01:00:05",
    ) is False

    assert attempt_store.list_attempts("project-1", "operation-1") == [
        attempt_store.OperationAttempt(
            attempt_id=attempt.attempt_id,
            project_id="project-1",
            operation_id="operation-1",
            attempt_number=1,
            runner_id="runner-a",
            status="success",
            started_at="2026-07-30T01:00:00",
            heartbeat_at="2026-07-30T01:00:04",
            completed_at="2026-07-30T01:00:04",
        )
    ]


def test_concurrent_attempt_numbers_are_unique_and_monotonic(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    with database.conn():
        pass
    release = threading.Event()
    attempts: list[attempt_store.OperationAttempt] = []
    errors: list[BaseException] = []

    def begin(runner_id: str) -> None:
        release.wait(timeout=1)
        try:
            attempts.append(
                attempt_store.begin_attempt(
                    "project-1",
                    "operation-1",
                    runner_id=runner_id,
                    started_at="2026-07-30T01:00:00",
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=begin, args=(f"runner-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(item.attempt_number for item in attempts) == [1, 2]
    assert [
        item.attempt_number
        for item in attempt_store.list_attempts(
            "project-1",
            "operation-1",
        )
    ] == [1, 2]


def test_process_finishes_acquired_execution_claim(monkeypatch):
    running = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="english_asr",
        status="running",
    )
    completed = running.model_copy(
        update={
            "status": "success",
            "completed_at": "2026-07-30T01:00:04",
        }
    )
    calls: list[tuple] = []

    class FakeClaim:
        execution_fence = ExecutionFence(
            attempt_id="attempt-1",
            project_id="project-1",
            operation_id="operation-1",
            runner_id="runner-a",
            fencing_token=1,
        )
        lost = False

        def start_heartbeat(self) -> None:
            calls.append(("start",))

        def mark_lost(self) -> None:
            self.lost = True
            calls.append(("lost",))

        def finish(self, *, status, error_code=None) -> bool:
            calls.append(("finish", status, error_code))
            return True

    claim = FakeClaim()
    decision = SimpleNamespace(
        acquired=True,
        claim=claim,
        retry_at_ms=None,
    )
    monkeypatch.setattr(
        operation_queue.video_localization_operation_execution,
        "acquire_execution_claim",
        lambda *_args, **_kwargs: decision,
    )
    monkeypatch.setattr(
        operation_queue,
        "_process_operation",
        lambda project_id, operation, execution_claim: calls.append(
            (
                "process",
                project_id,
                operation.operation_id,
                execution_claim.execution_fence.fencing_token,
            )
        ),
    )
    operation_reads = iter([running, completed])
    monkeypatch.setattr(
        operation_queue,
        "get_operation",
        lambda _project_id, _operation_id: next(operation_reads),
    )
    operation_queue._process("project-1", "operation-1")

    assert calls == [
        ("start",),
        ("process", "project-1", "operation-1", 1),
        ("finish", "success", None),
    ]


def test_claim_store_failure_blocks_processing_and_schedules_recovery(
    monkeypatch,
):
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="english_asr",
        status="running",
    )
    processed: list[str] = []
    scheduled: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        operation_queue,
        "get_operation",
        lambda _project_id, _operation_id: operation,
    )
    monkeypatch.setattr(
        operation_queue.video_localization_operation_execution,
        "acquire_execution_claim",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("claim unavailable")
        ),
    )
    monkeypatch.setattr(
        operation_queue,
        "_process_operation",
        lambda _project_id, item, execution_claim: processed.append(
            item.operation_id
        ),
    )
    monkeypatch.setattr(
        operation_queue,
        "_schedule_operation_recovery",
        lambda project_id, operation_id, *, deadline_ms: scheduled.append(
            (project_id, operation_id, deadline_ms)
        ),
    )

    operation_queue._process("project-1", "operation-1")

    assert processed == []
    assert len(scheduled) == 1
    assert scheduled[0][:2] == ("project-1", "operation-1")
    assert scheduled[0][2] > 0


def test_terminal_cancelled_operation_is_not_claimed_again(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="english_asr",
        status="cancelled",
        cancel_requested=True,
    )
    draft = VideoLocalizationDraft(operations=[operation])
    project_store.save_project(
        Project(
            project_id="project-1",
            name="attempt integration",
            parameters={
                "video_localization": draft.model_dump(mode="json"),
            },
        )
    )

    operation_queue._process("project-1", "operation-1")

    attempts = attempt_store.list_attempts(
        "project-1",
        "operation-1",
    )
    assert attempts == []
    persisted = operation_queue.get_operation(
        "project-1",
        "operation-1",
    )
    assert persisted is not None
    assert persisted.status == "cancelled"


def test_reconciliation_uses_only_latest_attempt_per_operation(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    _save_authoritative_operation(
        "project-1",
        "operation-1",
        status="success",
    )
    first = attempt_store.begin_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        started_at="2026-07-30T01:00:00",
    )
    assert attempt_store.finish_attempt(
        first.attempt_id,
        runner_id="runner-1",
        status="failed",
        completed_at="2026-07-30T01:00:01",
    )
    second = attempt_store.begin_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-2",
        started_at="2026-07-30T01:00:02",
    )
    assert attempt_store.finish_attempt(
        second.attempt_id,
        runner_id="runner-2",
        status="success",
        completed_at="2026-07-30T01:00:03",
    )

    report = (
        video_localization_operation_attempt_audit
        .reconcile_latest_attempts()
    )

    assert report.total_attempt_count == 2
    assert report.operation_count == 1
    assert report.checked_operation_count == 1
    assert report.category_counts == {"matched_terminal": 1}
    assert report.issues == ()
    assert report.healthy is True


def test_reconciliation_classifies_shadow_authority_differences(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    cases = [
        (
            "running-after-terminal",
            "failed",
            "running",
            "attempt_running_after_terminal",
        ),
        (
            "terminal-while-active",
            "running",
            "success",
            "attempt_terminal_while_authority_active",
        ),
        (
            "terminal-mismatch",
            "success",
            "failed",
            "terminal_status_mismatch",
        ),
        (
            "incomplete",
            "failed",
            "incomplete",
            "attempt_incomplete",
        ),
    ]
    expected_categories = set()
    for operation_id, authority_status, attempt_status, category in cases:
        project_id = f"project-{operation_id}"
        _save_authoritative_operation(
            project_id,
            operation_id,
            status=authority_status,
        )
        attempt = attempt_store.begin_attempt(
            project_id,
            operation_id,
            runner_id="runner-1",
            started_at="2026-07-30T01:00:00",
        )
        if attempt_status != "running":
            assert attempt_store.finish_attempt(
                attempt.attempt_id,
                runner_id="runner-1",
                status=attempt_status,
                completed_at="2026-07-30T01:00:01",
            )
        expected_categories.add(category)

    attempt_store.begin_attempt(
        "missing-project",
        "missing-project-operation",
        runner_id="runner-1",
        started_at="2026-07-30T01:00:00",
    )
    project_store.save_project(
        Project(
            project_id="empty-project",
            name="empty",
            parameters={
                "video_localization": VideoLocalizationDraft().model_dump(
                    mode="json"
                )
            },
        )
    )
    attempt_store.begin_attempt(
        "empty-project",
        "missing-operation",
        runner_id="runner-1",
        started_at="2026-07-30T01:00:00",
    )
    expected_categories.update({"project_missing", "operation_missing"})

    report = (
        video_localization_operation_attempt_audit
        .reconcile_latest_attempts()
    )

    assert {issue.category for issue in report.issues} == (
        expected_categories
    )
    assert report.healthy is False


def test_reconciliation_reports_bounded_truncation(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")
    for index in range(2):
        project_id = f"project-{index}"
        operation_id = f"operation-{index}"
        _save_authoritative_operation(
            project_id,
            operation_id,
            status="running",
        )
        attempt_store.begin_attempt(
            project_id,
            operation_id,
            runner_id="runner-1",
            started_at=f"2026-07-30T01:00:0{index}",
        )

    report = (
        video_localization_operation_attempt_audit
        .reconcile_latest_attempts(limit=1)
    )

    assert report.operation_count == 2
    assert report.checked_operation_count == 1
    assert report.truncated is True
    assert report.healthy is False


def test_reconciliation_cli_json_check_is_read_only(tmp_path: Path):
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
                / "audit_video_localization_operation_attempts.py"
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
    assert payload["total_attempt_count"] == 0
    assert payload["issues"] == []

    attempt_store.begin_attempt(
        "missing-project",
        "operation-1",
        runner_id="runner-1",
        started_at="2026-07-30T01:00:00",
    )
    inconsistent = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / "audit_video_localization_operation_attempts.py"
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

    assert inconsistent.returncode == 1
    issue_payload = json.loads(inconsistent.stdout)
    assert issue_payload["issues"][0]["category"] == "project_missing"
