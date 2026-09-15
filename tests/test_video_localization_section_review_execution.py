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
    asr_section_review_execution as execution,
    asr_section_review_managed_contracts as managed_contracts,
    managed_local_step,
    section_review,
)
from app.errors import AppException  # noqa: E402
from app.schemas.video_localization_asr_section_review_step import (  # noqa: E402
    ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.services import database, llm_runtime  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_llm_provider_execution as provider_execution,
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
                ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
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
        base_url=(
            "https://paid.example.com/v1"
            if paid
            else "http://127.0.0.1:9999/v1"
        ),
        model_id="review-model",
        api_key="test-only-secret" if paid else None,
    )


def _request(
    *,
    section_count: int = 2,
) -> section_review.AsrSectionReviewInput:
    segments = [
        {
            "segment_id": "asr_0001",
            "start_ms": 0,
            "end_ms": 1_000,
            "raw_text": "The guest discusses AI chips.",
        },
        {
            "segment_id": "asr_0002",
            "start_ms": 1_000,
            "end_ms": 2_000,
            "raw_text": "She expects demand to grow.",
        },
    ]
    sections = [
        {
            "section_id": "opening",
            "start_ordinal": 1,
            "end_ordinal": 1 if section_count == 2 else 2,
            "start_segment_id": "asr_0001",
            "end_segment_id": (
                "asr_0001" if section_count == 2 else "asr_0002"
            ),
            "role": "opening",
        }
    ]
    if section_count == 2:
        sections.append(
            {
                "section_id": "forecast",
                "start_ordinal": 2,
                "end_ordinal": 2,
                "start_segment_id": "asr_0002",
                "end_segment_id": "asr_0002",
                "role": "forecast",
            }
        )
    return section_review.AsrSectionReviewInput(
        upstream_contract_version="asr-entity-normalization-v1",
        upstream_operation_id="entity-operation",
        understanding_operation_id="document-operation",
        source_track_id="vocals",
        source_audio_sha256="e" * 64,
        language="en",
        profile_id="review-profile",
        document_summary="An interview about AI chips.",
        sections=sections,
        segments=segments,
    )


def _prepared(
    profile: llm_runtime.ResolvedProfile,
    *,
    section_count: int = 2,
) -> managed_contracts.AsrSectionReviewPreparedInputV1:
    return managed_contracts.AsrSectionReviewPreparedInputV1(
        entity_artifact_fingerprint="a" * 64,
        document_artifact_fingerprint="b" * 64,
        profile_configuration_fingerprint=(
            provider_execution.provider_configuration_fingerprint(
                profile
            )
        ),
        behavior_fingerprint=(
            execution.section_review_behavior_fingerprint()
        ),
        request=_request(section_count=section_count),
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
            timeout_seconds=180,
            reasoning_effort_requested=None,
            reasoning_control_applied=True,
            duration_ms=25,
            finish_reason="stop",
            content_chars=20,
            reasoning_chars=0,
            response_id=response_id,
        )
    )


def test_managed_section_review_replays_parallel_calls_and_final(
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
    prepared = _prepared(profile)
    submitted: list[str] = []

    def complete_json(**kwargs):
        section_id = kwargs["user_payload"]["section"]["section_id"]
        submitted.append(section_id)
        _trace(kwargs["trace_sink"], profile, section_id)
        return {"issues": []}

    first = execution.execute_prepared_section_review(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    replay = execution.execute_prepared_section_review(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=2),
    )

    assert replay == first
    assert sorted(submitted) == ["forecast", "opening"]
    assert first.status == "completed"
    assert len(first.llm_calls) == 2
    assert [item.duration_ms for item in first.section_runs] == [
        25,
        25,
    ]
    step_ids = {
        item.step_id
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
    }
    assert "prepare_section_review_input" in step_ids
    assert "finalize_section_review" in step_ids
    assert len(
        [
            step_id
            for step_id in step_ids
            if step_id.startswith("section_call_")
        ]
    ) == 2
    serialized = b"\n".join(backend.files.values())
    assert b"test-only-secret" not in serialized
    assert str(tmp_path).encode() not in serialized


def test_known_section_failure_is_durable_partial_result(
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
    prepared = _prepared(profile)
    submitted: list[str] = []

    def complete_json(**kwargs):
        section_id = kwargs["user_payload"]["section"]["section_id"]
        submitted.append(section_id)
        if section_id == "opening":
            raise llm_runtime.LlmRuntimeError(
                "rejected",
                code="llm_authentication_failed",
                status_code=401,
            )
        _trace(kwargs["trace_sink"], profile, section_id)
        return {"issues": []}

    first = execution.execute_prepared_section_review(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
    )
    replay = execution.execute_prepared_section_review(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
    )

    assert replay == first
    assert first.status == "partial"
    assert [item.status for item in first.section_runs] == [
        "failed",
        "completed",
    ]
    assert sorted(submitted) == ["forecast", "opening"]
    provider_steps = [
        item
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        if item.step_id.startswith("section_call_")
    ]
    assert sorted(item.status for item in provider_steps) == [
        "failed",
        "success",
    ]


def test_retryable_section_output_uses_only_missing_attempt(
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
    prepared = _prepared(profile, section_count=1)
    calls = 0

    def complete_json(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise llm_runtime.LlmRuntimeError(
                "truncated",
                code="llm_output_truncated",
                status_code=502,
            )
        _trace(kwargs["trace_sink"], profile, "retry-success")
        return {"issues": []}

    first = execution.execute_prepared_section_review(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
    )
    replay = execution.execute_prepared_section_review(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        is_cancelled=lambda: False,
        file_backend=backend,
        complete_json=complete_json,
    )

    assert replay == first
    assert first.status == "completed"
    assert calls == 2
    attempts = [
        item
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        if item.step_id.startswith("section_call_")
    ]
    assert [item.status for item in attempts] == [
        "failed",
        "success",
    ]


def test_paid_unknown_section_call_is_not_submitted_twice(
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
    profile = _profile(paid=True)
    prepared = _prepared(profile, section_count=1)
    calls = 0

    def uncertain(**_kwargs):
        nonlocal calls
        calls += 1
        raise llm_runtime.LlmRuntimeError(
            "timeout",
            code="llm_timeout",
            status_code=504,
        )

    for _ in range(2):
        with pytest.raises(AppException) as raised:
            execution.execute_prepared_section_review(
                prepared,
                execution_fence=fence,
                resolved_profile=profile,
                is_cancelled=lambda: False,
                file_backend=backend,
                complete_json=uncertain,
            )
        assert raised.value.code == (
            "VIDEO_LOCALIZATION_SECTION_REVIEW_RESULT_UNKNOWN"
        )
    assert calls == 1
    unknown = [
        item
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        if item.status == "result_unknown"
    ]
    assert len(unknown) == 1
    assert unknown[0].cost_class == "external_paid"
