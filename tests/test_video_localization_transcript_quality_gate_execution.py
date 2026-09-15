from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))


from app.domains.video_localization import (  # noqa: E402
    asr_transcript_quality_gate_execution as execution,
    asr_transcript_quality_gate_managed_contracts as managed_contracts,
    asr_whole_recheck_result_reader,
    managed_artifact_files,
    managed_local_step,
    operation_detail_reconciliation,
    operation_detail_reader,
    operation_queue,
    service,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.errors import AppException  # noqa: E402
from app.schemas.video_localization_asr_transcript_quality_gate_step import (  # noqa: E402
    ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_asr_whole_recheck_step import (  # noqa: E402
    ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    ProjectCreate,
)
from app.services import (  # noqa: E402
    database,
    project_store,
    settings_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_execution as operation_execution,
)
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_store as step_store,
)
from test_video_localization_document_understanding_provider_gateway import (  # noqa: E402
    FakeArtifactBackend,
)
from test_video_localization_transcript_quality_gate import (  # noqa: E402
    _input,
    _result,
)


T0 = datetime.now(timezone.utc)


def _claim(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")
    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO video_localization_operations (
                project_id,
                operation_id,
                kind,
                status,
                parameters_fingerprint,
                workflow_version,
                origin,
                created_at,
                updated_at
            ) VALUES (
                'project-1',
                'operation-1',
                'english_asr',
                'running',
                'parameters-v1',
                ?,
                'command',
                ?,
                ?
            )
            """,
            (
                ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
                T0.isoformat(),
                T0.isoformat(),
            ),
        )
    claimed = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        observed_at=T0,
        lease_duration=timedelta(minutes=10),
    )
    assert claimed.attempt.execution_fence is not None
    return claimed.attempt.execution_fence


def _prepared():
    return managed_contracts.AsrTranscriptQualityGatePreparedInputV1(
        whole_recheck_artifact_fingerprint="a" * 64,
        behavior_fingerprint=(
            execution.transcript_quality_gate_behavior_fingerprint()
        ),
        request=_input(_result()),
    )


def test_managed_transcript_quality_gate_replays_two_local_steps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    monkeypatch.setattr(
        managed_local_step,
        "managed_artifact_files",
        backend,
    )

    first = execution.execute_prepared_transcript_quality_gate(
        _prepared(),
        execution_fence=fence,
    )
    replay = execution.execute_prepared_transcript_quality_gate(
        _prepared(),
        execution_fence=fence,
    )

    assert replay == first
    assert first.duration_ms == 0
    assert first.can_start_alignment is True
    steps = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )
    assert {
        (item.step_id, item.status, item.cost_class)
        for item in steps
    } == {
        (
            "prepare_transcript_quality_gate_input",
            "success",
            "local_free",
        ),
        (
            "finalize_transcript_quality_gate",
            "success",
            "local_free",
        ),
    }


def test_managed_transcript_quality_gate_rejects_changed_rules(
    tmp_path: Path,
) -> None:
    fence = _claim(tmp_path)
    prepared = _prepared().model_copy(
        update={"behavior_fingerprint": "b" * 64}
    )

    with pytest.raises(AppException) as raised:
        execution.execute_prepared_transcript_quality_gate(
            prepared,
            execution_fence=fence,
        )

    assert raised.value.code == (
        "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_"
        "BEHAVIOR_CHANGED"
    )
    assert (
        step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        == []
    )
