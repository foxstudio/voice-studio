from __future__ import annotations

import json
import sys
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import operation_queue  # noqa: E402
from app.domains.video_localization import project_manifest  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.voice_studio import Project  # noqa: E402
from app.services import database  # noqa: E402
from app.services import project_store  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_store as operation_store,
)
from app.services.video_localization_execution_fence import (  # noqa: E402
    ExecutionFence,
    ExecutionFenceLost,
    ExecutionOperationCancelled,
    execution_fence_scope,
)


UTC = timezone.utc
T0 = datetime(2026, 7, 30, 1, 0, tzinfo=UTC)


def _epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1_000)


def _project(
    project_id: str,
    operation_id: str,
    *,
    status: str,
) -> Project:
    operation = VideoLocalizationOperation(
        project_id=project_id,
        operation_id=operation_id,
        kind="english_asr",
        status=status,
        created_at=T0.isoformat(),
    )
    return Project(
        project_id=project_id,
        name=project_id,
        parameters={
            "video_localization": VideoLocalizationDraft(
                operations=[operation]
            ).model_dump(mode="json")
        },
    )


def _claim(
    project_id: str,
    operation_id: str,
    *,
    runner_id: str,
    observed_at: datetime,
    duration_seconds: int = 5,
):
    decision = attempt_store.claim_attempt(
        project_id,
        operation_id,
        runner_id=runner_id,
        observed_at=observed_at,
        lease_duration=timedelta(seconds=duration_seconds),
    )
    assert decision.acquired is True
    fence = decision.attempt.execution_fence
    assert fence is not None
    return decision.attempt, fence


def test_active_fence_commits_project_and_revision_together(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "project-1"
    operation_id = "operation-1"
    queued = _project(project_id, operation_id, status="queued")
    operation_store.save_project_with_projection(
        project_id,
        queued.model_dump(mode="json"),
        updated_at=queued.updated_at,
    )
    _attempt, fence = _claim(
        project_id,
        operation_id,
        runner_id="runner-1",
        observed_at=T0,
    )
    project = _project(
        project_id,
        operation_id,
        status="running",
    )

    operation_store.save_project_with_projection(
        project_id,
        project.model_dump(mode="json"),
        updated_at=project.updated_at,
        execution_fence=fence,
        observed_at_ms=_epoch_ms(T0 + timedelta(seconds=1)),
    )

    with database.conn() as connection:
        project_row = connection.execute(
            "SELECT data FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        projection_row = connection.execute(
            """
            SELECT projection_revision
            FROM video_localization_operation_projection_state
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
    assert project_row is not None
    assert (
        json.loads(project_row["data"])["parameters"]
        ["video_localization"]["operations"][0]["status"]
        == "running"
    )
    assert projection_row["projection_revision"] == 2
    ledger_entry = ledger_store.get_operation(
        project_id,
        operation_id,
    )
    assert ledger_entry is not None
    assert ledger_entry.status == "running"
    assert ledger_entry.state_revision == 2


def test_superseded_fence_cannot_change_project_or_revision(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "project-1"
    operation_id = "operation-1"
    queued = _project(project_id, operation_id, status="queued")
    operation_store.save_project_with_projection(
        project_id,
        queued.model_dump(mode="json"),
        updated_at=queued.updated_at,
    )
    _first, stale_fence = _claim(
        project_id,
        operation_id,
        runner_id="runner-1",
        observed_at=T0,
    )
    _second, current_fence = _claim(
        project_id,
        operation_id,
        runner_id="runner-2",
        observed_at=T0 + timedelta(seconds=5),
    )
    completed = _project(project_id, operation_id, status="success")

    with pytest.raises(ExecutionFenceLost):
        operation_store.save_project_with_projection(
            project_id,
            completed.model_dump(mode="json"),
            updated_at=completed.updated_at,
            execution_fence=stale_fence,
            observed_at_ms=_epoch_ms(T0 + timedelta(seconds=6)),
        )

    stored = operation_store.read_project_mirror_operation(project_id, operation_id)[1]
    assert stored is not None
    assert stored["status"] == "queued"
    operation_store.save_project_with_projection(
        project_id,
        completed.model_dump(mode="json"),
        updated_at=completed.updated_at,
        execution_fence=current_fence,
        observed_at_ms=_epoch_ms(T0 + timedelta(seconds=6)),
    )
    stored = operation_store.read_project_mirror_operation(project_id, operation_id)[1]
    assert stored is not None
    assert stored["status"] == "success"


def test_execution_fence_cannot_cross_project_or_token(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    _attempt, fence = _claim(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        observed_at=T0,
    )
    other_project = _project(
        "project-2",
        "operation-2",
        status="running",
    )

    with pytest.raises(
        ExecutionFenceLost,
        match="another project",
    ):
        operation_store.save_project_with_projection(
            other_project.project_id,
            other_project.model_dump(mode="json"),
            updated_at=other_project.updated_at,
            execution_fence=fence,
            observed_at_ms=_epoch_ms(T0 + timedelta(seconds=1)),
        )
    with pytest.raises(ExecutionFenceLost):
        operation_store.save_project_with_projection(
            "project-1",
            _project(
                "project-1",
                "operation-1",
                status="running",
            ).model_dump(mode="json"),
            updated_at=T0.isoformat(),
            execution_fence=replace(
                fence,
                fencing_token=fence.fencing_token + 1,
            ),
            observed_at_ms=_epoch_ms(T0 + timedelta(seconds=1)),
        )


def test_claim_cannot_supersede_fence_during_project_transaction(
    tmp_path: Path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "project-1"
    operation_id = "operation-1"
    queued = _project(project_id, operation_id, status="queued")
    operation_store.save_project_with_projection(
        project_id,
        queued.model_dump(mode="json"),
        updated_at=queued.updated_at,
    )
    _attempt, fence = _claim(
        project_id,
        operation_id,
        runner_id="runner-1",
        observed_at=T0,
    )
    project = _project(project_id, operation_id, status="running")
    transaction_reached = threading.Event()
    release_transaction = threading.Event()
    save_finished = threading.Event()
    claim_started = threading.Event()
    claim_finished = threading.Event()
    errors: list[BaseException] = []
    decisions: list[attempt_store.ClaimDecision] = []
    original_replace = operation_store._write_project_revision

    def blocking_replace(*args, **kwargs):
        transaction_reached.set()
        assert release_transaction.wait(timeout=1)
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(
        operation_store,
        "_write_project_revision",
        blocking_replace,
    )

    def save() -> None:
        try:
            operation_store.save_project_with_projection(
                project_id,
                project.model_dump(mode="json"),
                updated_at=project.updated_at,
                execution_fence=fence,
                observed_at_ms=_epoch_ms(
                    T0 + timedelta(seconds=4)
                ),
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            save_finished.set()

    def reclaim() -> None:
        claim_started.set()
        try:
            decisions.append(
                attempt_store.claim_attempt(
                    project_id,
                    operation_id,
                    runner_id="runner-2",
                    observed_at=T0 + timedelta(seconds=5),
                    lease_duration=timedelta(seconds=5),
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            claim_finished.set()

    save_thread = threading.Thread(target=save)
    save_thread.start()
    assert transaction_reached.wait(timeout=1)
    claim_thread = threading.Thread(target=reclaim)
    claim_thread.start()
    assert claim_started.wait(timeout=1)
    assert not claim_finished.wait(timeout=0.05)

    release_transaction.set()
    save_thread.join(timeout=2)
    claim_thread.join(timeout=2)

    assert errors == []
    assert save_finished.is_set()
    assert claim_finished.is_set()
    assert decisions[0].acquired is True
    assert decisions[0].attempt.fencing_token == 2
    stored = operation_store.read_project_mirror_operation(project_id, operation_id)[1]
    assert stored is not None
    assert stored["status"] == "running"


def test_cancelled_project_operation_rejects_still_live_fence(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "project-1"
    operation_id = "operation-1"
    running = _project(project_id, operation_id, status="running")
    operation_store.save_project_with_projection(
        project_id,
        running.model_dump(mode="json"),
        updated_at=running.updated_at,
    )
    _attempt, fence = _claim(
        project_id,
        operation_id,
        runner_id="runner-1",
        observed_at=T0,
    )
    cancelled = _project(project_id, operation_id, status="cancelled")
    operation_store.save_project_with_projection(
        project_id,
        cancelled.model_dump(mode="json"),
        updated_at=cancelled.updated_at,
    )

    with pytest.raises(ExecutionOperationCancelled):
        operation_store.save_project_with_projection(
            project_id,
            _project(
                project_id,
                operation_id,
                status="success",
            ).model_dump(mode="json"),
            updated_at=running.updated_at,
            execution_fence=fence,
            observed_at_ms=_epoch_ms(T0 + timedelta(seconds=1)),
        )

    stored = operation_store.read_project_mirror_operation(project_id, operation_id)[1]
    assert stored is not None
    assert stored["status"] == "cancelled"


def test_fenced_commit_uses_ledger_when_project_mirror_drifted(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "project-1"
    operation_id = "operation-1"
    running = _project(project_id, operation_id, status="running")
    operation_store.save_project_with_projection(
        project_id,
        running.model_dump(mode="json"),
        updated_at=running.updated_at,
    )
    _attempt, fence = _claim(
        project_id,
        operation_id,
        runner_id="runner-1",
        observed_at=T0,
    )
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE projects
            SET data = json_set(
                data,
                '$.parameters.video_localization.operations[0].status',
                'cancelled'
            )
            WHERE project_id = ?
            """,
            (project_id,),
        )

    operation_store.save_project_with_projection(
        project_id,
        _project(
            project_id,
            operation_id,
            status="success",
        ).model_dump(mode="json"),
        updated_at=running.updated_at,
        execution_fence=fence,
        observed_at_ms=_epoch_ms(T0 + timedelta(seconds=1)),
    )

    ledger = ledger_store.get_operation(project_id, operation_id)
    stored = operation_store.read_project_mirror_operation(
        project_id,
        operation_id,
    )[1]
    assert ledger is not None
    assert ledger.status == "success"
    assert stored is not None
    assert stored["status"] == "success"


def test_fenced_runtime_status_write_defers_snapshot_and_rejects_lease_loss(
    tmp_path: Path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "project-1"
    operation_id = "operation-1"
    project_store.save_project(
        _project(project_id, operation_id, status="queued"),
        touch_updated_at=False,
    )
    _first, stale_fence = _claim(
        project_id,
        operation_id,
        runner_id="runner-1",
        observed_at=T0,
    )
    snapshots: list[str] = []
    monkeypatch.setattr(
        project_manifest,
        "write_project_snapshot",
        lambda project, _draft, **_kwargs: snapshots.append(
            project.project_id
        ),
    )

    operation_queue._mark_operation(
        project_id,
        operation_id,
        status="running",
        execution_fence=stale_fence,
        observed_at_ms=_epoch_ms(T0 + timedelta(seconds=1)),
    )
    assert snapshots == []
    _second, current_fence = _claim(
        project_id,
        operation_id,
        runner_id="runner-2",
        observed_at=T0 + timedelta(seconds=5),
    )

    with pytest.raises(ExecutionFenceLost):
        operation_queue._mark_operation(
            project_id,
            operation_id,
            status="success",
            execution_fence=stale_fence,
            observed_at_ms=_epoch_ms(T0 + timedelta(seconds=6)),
        )

    assert snapshots == []
    stored = operation_queue.get_operation(project_id, operation_id)
    assert stored is not None
    assert stored.status == "running"
    operation_queue._mark_operation(
        project_id,
        operation_id,
        status="success",
        execution_fence=current_fence,
        observed_at_ms=_epoch_ms(T0 + timedelta(seconds=6)),
    )
    stored = operation_queue.get_operation(project_id, operation_id)
    assert stored is not None
    assert stored.status == "success"


def test_execution_fence_rejects_empty_identity_and_non_positive_token():
    with pytest.raises(ValueError, match="identifiers"):
        ExecutionFence(
            attempt_id="",
            project_id="project-1",
            operation_id="operation-1",
            runner_id="runner-1",
            fencing_token=1,
        )
    with pytest.raises(ValueError, match="positive"):
        ExecutionFence(
            attempt_id="attempt-1",
            project_id="project-1",
            operation_id="operation-1",
            runner_id="runner-1",
            fencing_token=0,
        )


def test_execution_fence_scope_reaches_nested_project_write(
    tmp_path: Path,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    project_id = "project-1"
    operation_id = "operation-1"
    observed_at = datetime.now(tz=UTC)
    project_store.save_project(
        _project(project_id, operation_id, status="queued"),
        touch_updated_at=False,
    )
    _attempt, fence = _claim(
        project_id,
        operation_id,
        runner_id="runner-1",
        observed_at=observed_at,
        duration_seconds=60,
    )

    with execution_fence_scope(fence):
        operation_queue._mark_operation(
            project_id,
            operation_id,
            status="running",
            observed_at_ms=_epoch_ms(
                observed_at + timedelta(seconds=1)
            ),
        )

    stored = operation_queue.get_operation(project_id, operation_id)
    assert stored is not None
    assert stored.status == "running"

    with execution_fence_scope(fence):
        with pytest.raises(ValueError, match="conflicts"):
            operation_queue._mark_operation(
                project_id,
                operation_id,
                status="success",
                execution_fence=replace(
                    fence,
                    fencing_token=fence.fencing_token + 1,
                ),
                observed_at_ms=_epoch_ms(
                    observed_at + timedelta(seconds=1)
                ),
            )
