from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    draft_store,
    media_assets,
    operation_detail_projection,
    project_manifest,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.voice_studio import Project  # noqa: E402
from app.services import database, project_store  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_detail_core_store as detail_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_store as operation_store,
)
from app.services.video_localization_execution_fence import (  # noqa: E402
    ExecutionFenceLost,
    execution_fence_scope,
)


PROJECT_ID = "project-detail-shadow"
OPERATION_ID = "semantic-shadow"
WORKFLOW_VERSION = "semantic-tts-grouping-workflow-v2"
T0 = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def isolated_store(tmp_path: Path, monkeypatch):
    original_path = database.DB_PATH
    database.set_db_path(tmp_path / "voice_studio.db")
    monkeypatch.setattr(
        project_manifest,
        "write_project_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        media_assets,
        "cache_project_timeline_audio_paths",
        lambda *_args, **_kwargs: None,
    )
    try:
        yield tmp_path
    finally:
        database.set_db_path(original_path)


def _operation(
    *,
    operation_id: str = OPERATION_ID,
    status: str = "queued",
    target_chars: int = 120,
    completed_at: str | None = None,
) -> VideoLocalizationOperation:
    return VideoLocalizationOperation(
        project_id=PROJECT_ID,
        operation_id=operation_id,
        kind="semantic_tts_grouping",
        status=status,
        progress=1.0 if status == "success" else 0.0,
        parameters={
            "profile_id": "profile-local",
            "profile_configuration_fingerprint": "a" * 64,
            "workflow_id": "semantic-tts-grouping",
            "target_chars": target_chars,
            "max_chars": 180,
            "scope": {
                "area": "localized_subtitles",
                "exclusive": False,
            },
        },
        result_summary={
            "stage": "准备语义分组",
            "workflow_schema_version": WORKFLOW_VERSION,
        },
        created_at=T0.isoformat(),
        completed_at=completed_at,
    )


def _create_empty_project() -> None:
    project_store.save_project(
        Project(
            project_id=PROJECT_ID,
            name="Detail shadow",
            parameters={
                "video_localization": (
                    VideoLocalizationDraft().model_dump(mode="json")
                )
            },
        ),
        touch_updated_at=False,
    )


def _submit(
    operation: VideoLocalizationOperation | None = None,
) -> VideoLocalizationDraft:
    current = operation or _operation()
    draft = VideoLocalizationDraft(operations=[current])
    command = ledger_store.command_from_operation(
        "submit",
        current.model_dump(mode="json"),
        expected_project_revision=operation_store.project_revision(
            PROJECT_ID
        ),
        created_at=T0.isoformat(),
    )
    saved = draft_store.save(
        PROJECT_ID,
        draft,
        intent="content",
        operation_command=command,
    )
    assert saved is not None
    return saved


def _revision() -> int:
    return operation_store.project_revision(PROJECT_ID)


def test_submit_writes_ledger_project_and_detail_core_together(
    isolated_store,
):
    _create_empty_project()

    saved = _submit()

    record = detail_store.get_detail_core(
        PROJECT_ID,
        OPERATION_ID,
    )
    ledger = ledger_store.get_operation(PROJECT_ID, OPERATION_ID)
    assert record is not None
    assert ledger is not None
    assert record.core == (
        operation_detail_projection.detail_core_from_operation(
            saved.operations[0],
            workflow_version=ledger.workflow_version,
        )
    )
    assert ledger.workflow_version == WORKFLOW_VERSION


def test_detail_failure_rolls_back_command_project_and_outbox(
    isolated_store,
    monkeypatch,
):
    _create_empty_project()
    before_revision = _revision()

    def reject_detail(*_args, **_kwargs):
        raise RuntimeError("injected detail failure")

    monkeypatch.setattr(
        detail_store,
        "put_detail_core_from_connection",
        reject_detail,
    )

    with pytest.raises(RuntimeError, match="injected detail failure"):
        _submit()

    assert _revision() == before_revision
    assert ledger_store.get_operation(
        PROJECT_ID,
        OPERATION_ID,
    ) is None
    assert ledger_store.pending_outbox_count() == 0
    project = project_store.get_project(PROJECT_ID)
    assert project is not None
    mirror = VideoLocalizationDraft.model_validate(
        project.parameters["video_localization"]
    )
    assert mirror.operations == []


def test_contextual_terminal_commit_recreates_missing_detail_core(
    isolated_store,
):
    _create_empty_project()
    saved = _submit()
    claim = attempt_store.claim_attempt(
        PROJECT_ID,
        OPERATION_ID,
        runner_id="runner-detail",
        observed_at=T0 + timedelta(seconds=1),
        lease_duration=timedelta(seconds=30),
    )
    fence = claim.attempt.execution_fence
    assert fence is not None
    with database.conn() as connection:
        connection.execute(
            """
            DELETE FROM video_localization_operation_detail_cores
            WHERE project_id = ? AND operation_id = ?
            """,
            (PROJECT_ID, OPERATION_ID),
        )
    completed_at = (T0 + timedelta(seconds=2)).isoformat()
    final_operation = saved.operations[0].model_copy(
        update={
            "status": "success",
            "progress": 1.0,
            "completed_at": completed_at,
        }
    )
    final_draft = saved.model_copy(
        update={"operations": [final_operation]}
    )

    with execution_fence_scope(fence):
        committed = draft_store.save(
            PROJECT_ID,
            final_draft,
            intent="runtime",
            observed_at_ms=int(
                (T0 + timedelta(seconds=2)).timestamp() * 1_000
            ),
        )

    assert committed is not None
    assert detail_store.get_detail_core(
        PROJECT_ID,
        OPERATION_ID,
    ) is not None
    assert ledger_store.get_operation(
        PROJECT_ID,
        OPERATION_ID,
    ).status == "success"


def test_retry_writes_a_distinct_detail_core_in_the_same_command(
    isolated_store,
):
    _create_empty_project()
    source = _operation(status="failed")
    saved = _submit(source)
    retry_operation = _operation(
        operation_id="semantic-retry",
    )
    retry_draft = saved.model_copy(
        update={
            "operations": [
                saved.operations[0],
                retry_operation,
            ]
        }
    )
    command = ledger_store.command_from_operation(
        "retry",
        retry_operation.model_dump(mode="json"),
        expected_project_revision=_revision(),
        source_operation_id=OPERATION_ID,
        created_at=(T0 + timedelta(seconds=1)).isoformat(),
    )

    committed = draft_store.save(
        PROJECT_ID,
        retry_draft,
        intent="content",
        operation_command=command,
    )

    assert committed is not None
    assert detail_store.get_detail_core(
        PROJECT_ID,
        OPERATION_ID,
    ) is not None
    retry_core = detail_store.get_detail_core(
        PROJECT_ID,
        "semantic-retry",
    )
    assert retry_core is not None
    assert retry_core.core.operation_id == "semantic-retry"
    assert ledger_store.list_outbox(
        PROJECT_ID,
        "semantic-retry",
    )[0].command_type == "retry"


def test_terminal_detail_conflict_rolls_back_project_and_ledger(
    isolated_store,
):
    _create_empty_project()
    saved = _submit()
    claim = attempt_store.claim_attempt(
        PROJECT_ID,
        OPERATION_ID,
        runner_id="runner-conflict",
        observed_at=T0 + timedelta(seconds=1),
        lease_duration=timedelta(seconds=30),
    )
    fence = claim.attempt.execution_fence
    assert fence is not None
    before_revision = _revision()
    changed = _operation(
        status="success",
        target_chars=140,
        completed_at=(T0 + timedelta(seconds=2)).isoformat(),
    )

    with pytest.raises(detail_store.OperationDetailCoreConflict):
        draft_store.save(
            PROJECT_ID,
            saved.model_copy(update={"operations": [changed]}),
            intent="runtime",
            execution_fence=fence,
            observed_at_ms=int(
                (T0 + timedelta(seconds=2)).timestamp() * 1_000
            ),
        )

    assert _revision() == before_revision
    assert ledger_store.get_operation(
        PROJECT_ID,
        OPERATION_ID,
    ).status == "queued"
    assert detail_store.get_detail_core(
        PROJECT_ID,
        OPERATION_ID,
    ).core.parameters.target_chars == 120


def test_terminal_commit_cannot_downgrade_command_workflow_identity(
    isolated_store,
):
    _create_empty_project()
    saved = _submit()
    claim = attempt_store.claim_attempt(
        PROJECT_ID,
        OPERATION_ID,
        runner_id="runner-workflow",
        observed_at=T0 + timedelta(seconds=1),
        lease_duration=timedelta(seconds=30),
    )
    fence = claim.attempt.execution_fence
    assert fence is not None
    before_revision = _revision()
    downgraded = saved.operations[0].model_copy(
        update={
            "status": "success",
            "progress": 1.0,
            "completed_at": (
                T0 + timedelta(seconds=2)
            ).isoformat(),
            "result_summary": {"stage": "完成"},
        }
    )

    with pytest.raises(ledger_store.OperationProjectionConflict):
        draft_store.save(
            PROJECT_ID,
            saved.model_copy(
                update={"operations": [downgraded]}
            ),
            intent="runtime",
            execution_fence=fence,
            observed_at_ms=int(
                (T0 + timedelta(seconds=2)).timestamp() * 1_000
            ),
        )

    assert _revision() == before_revision
    assert ledger_store.get_operation(
        PROJECT_ID,
        OPERATION_ID,
    ).workflow_version == WORKFLOW_VERSION


def test_lost_terminal_fence_cannot_write_detail_or_project(
    isolated_store,
):
    _create_empty_project()
    saved = _submit()
    claim = attempt_store.claim_attempt(
        PROJECT_ID,
        OPERATION_ID,
        runner_id="runner-expired",
        observed_at=T0 + timedelta(seconds=1),
        lease_duration=timedelta(seconds=1),
    )
    fence = claim.attempt.execution_fence
    assert fence is not None
    with database.conn() as connection:
        connection.execute(
            """
            DELETE FROM video_localization_operation_detail_cores
            WHERE project_id = ? AND operation_id = ?
            """,
            (PROJECT_ID, OPERATION_ID),
        )
    before_revision = _revision()
    final_operation = saved.operations[0].model_copy(
        update={
            "status": "success",
            "progress": 1.0,
            "completed_at": (
                T0 + timedelta(seconds=5)
            ).isoformat(),
        }
    )

    with pytest.raises(ExecutionFenceLost):
        draft_store.save(
            PROJECT_ID,
            saved.model_copy(
                update={"operations": [final_operation]}
            ),
            intent="runtime",
            execution_fence=fence,
            observed_at_ms=int(
                (T0 + timedelta(seconds=5)).timestamp() * 1_000
            ),
        )

    assert _revision() == before_revision
    assert detail_store.get_detail_core(
        PROJECT_ID,
        OPERATION_ID,
    ) is None
    assert ledger_store.get_operation(
        PROJECT_ID,
        OPERATION_ID,
    ).status == "queued"


def test_unrelated_autosave_does_not_backfill_historical_operation(
    isolated_store,
):
    historical = _operation(status="failed")
    draft = VideoLocalizationDraft(operations=[historical])
    project_store.save_project(
        Project(
            project_id=PROJECT_ID,
            name="Historical",
            parameters={
                "video_localization": draft.model_dump(mode="json")
            },
        ),
        touch_updated_at=False,
    )

    saved = draft_store.save(
        PROJECT_ID,
        draft,
        intent="workspace",
        updated_at=draft.updated_at,
    )

    assert saved is not None
    assert detail_store.get_detail_core(
        PROJECT_ID,
        OPERATION_ID,
    ) is None
    assert ledger_store.get_operation(
        PROJECT_ID,
        OPERATION_ID,
    ).origin == "legacy_project"


def test_detail_shadow_payload_remains_bounded_and_path_free(
    isolated_store,
):
    _create_empty_project()
    _submit()
    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT core_json
            FROM video_localization_operation_detail_cores
            WHERE project_id = ? AND operation_id = ?
            """,
            (PROJECT_ID, OPERATION_ID),
        ).fetchone()

    payload = str(row["core_json"])
    assert len(payload.encode("utf-8")) < 512
    assert json.loads(payload)["parameters"]["profile_id"] == (
        "profile-local"
    )
    assert "scope" not in payload
    assert "artifact" not in payload
    assert "/" not in payload
