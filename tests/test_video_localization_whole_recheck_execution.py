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
    asr_document_understanding_result_reader,
    document_understanding_contracts,
    asr_review_decisions_operation_projection,
    asr_review_decisions_result_reader,
    asr_section_review_result_reader,
    asr_whole_recheck_execution as execution,
    asr_whole_recheck_managed_contracts as managed_contracts,
    managed_artifact_files,
    managed_local_step,
    operation_detail_reader,
    operation_queue,
    review_decisions,
    service,
    whole_recheck,
)
from app.errors import AppException  # noqa: E402
from app.schemas.video_localization_asr_whole_recheck_step import (  # noqa: E402
    ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_asr_document_understanding_step import (  # noqa: E402
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    ProjectCreate,
)
from app.services import (  # noqa: E402
    database,
    llm_runtime,
    project_store,
    settings_store,
)
from app.services import (  # noqa: E402
    video_localization_llm_provider_execution as provider_execution,
)
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_store as step_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
)
from test_video_localization_document_understanding_provider_gateway import (  # noqa: E402
    FakeArtifactBackend,
)
from test_video_localization_review_decisions_execution import (  # noqa: E402
    _section_result,
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
                ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
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


def _profile(*, paid: bool = False) -> llm_runtime.ResolvedProfile:
    return llm_runtime.ResolvedProfile(
        profile_id="review-profile",
        protocol="openai_compatible",
        base_url=("https://paid.example.com/v1" if paid else "http://127.0.0.1:9999/v1"),
        model_id="review-model",
        api_key="test-only-secret" if paid else None,
    )


def _request() -> whole_recheck.AsrWholeRecheckInput:
    return whole_recheck.AsrWholeRecheckInput(
        upstream_operation_id="decisions-operation",
        understanding_operation_id="document-operation",
        source_track_id="vocals",
        source_audio_sha256="e" * 64,
        language="en",
        profile_id="review-profile",
        document_summary="A short interview.",
        segments=[
            {
                "segment_id": "asr_0001",
                "start_ms": 0,
                "end_ms": 1_000,
                "raw_text": "The transcript is complete.",
            }
        ],
        upstream_status="completed",
    )


def _prepared(
    profile: llm_runtime.ResolvedProfile,
) -> managed_contracts.AsrWholeRecheckPreparedInputV1:
    return managed_contracts.AsrWholeRecheckPreparedInputV1(
        review_decisions_artifact_fingerprint="a" * 64,
        document_understanding_artifact_fingerprint="b" * 64,
        profile_configuration_fingerprint=(provider_execution.provider_configuration_fingerprint(profile)),
        behavior_fingerprint=(execution.whole_recheck_behavior_fingerprint()),
        request=_request(),
    )


def _trace(sink, profile, response_id: str) -> None:
    sink(
        llm_runtime.LlmCompletionTrace(
            profile_id=profile.profile_id,
            model_id=profile.model_id,
            provider_host="127.0.0.1",
            request_chars=100,
            request_body_bytes=200,
            max_tokens=6_000,
            timeout_seconds=240,
            reasoning_effort_requested=None,
            reasoning_control_applied=True,
            duration_ms=25,
            finish_reason="stop",
            content_chars=20,
            reasoning_chars=0,
            response_id=response_id,
        )
    )


def _passed() -> dict:
    return {
        "passed": True,
        "summary": "全文连贯，可以结束自动复查。",
        "warnings": [],
        "next_sections": [],
        "unresolved_items": [],
    }


def test_managed_whole_recheck_replays_local_closure_without_provider_call(
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
    profile = _profile()
    first = execution.execute_prepared_whole_recheck(
        _prepared(profile),
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
    )
    replay = execution.execute_prepared_whole_recheck(
        _prepared(profile),
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
    )

    assert replay == first
    assert first.llm_calls == []
    assert first.passed is True
    assert {
        item.step_id
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
    } == {
        "prepare_whole_recheck_input",
        "finalize_whole_recheck",
    }
